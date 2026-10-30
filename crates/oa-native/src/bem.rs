// SPDX-FileCopyrightText: 2026 Open_Analysis contributors
// SPDX-License-Identifier: GPL-3.0-or-later
//! BEM influence matrices of flat constant-density panels (WP-515), the kernel behind
//! `oa_impedance.bem.kernels.influence_blocks`.
//!
//! The expressions are those of the numpy oracle, evaluated left to right in f64 without fused
//! multiply-adds; only `ln` and `atan2` come from the platform libm instead of numpy's loops
//! (differences of one ulp in single entries). Rows are split over scoped threads.

use numpy::{PyReadonlyArray1, PyReadonlyArray2, PyReadwriteArray2, PyUntypedArrayMethods};
use pyo3::prelude::*;

const INV_2PI: f64 = 1.0 / (2.0 * std::f64::consts::PI);
const TINY: f64 = 1e-300;

struct Panels<'a> {
    ax: Vec<f64>,
    ay: Vec<f64>,
    tx: Vec<f64>,
    ty: Vec<f64>,
    nx: Vec<f64>,
    ny: Vec<f64>,
    len: &'a [f64],
}

/// Rows `r0..` of P and D (`p` and `d` hold whole rows of `n_c` entries).
fn fill_rows(
    mid: &[f64],
    nrm_t: &[f64],
    pan: &Panels,
    r0: usize,
    p: &mut [f64],
    d: &mut [f64],
    n_c: usize,
) {
    for (k, (prow, drow)) in p.chunks_mut(n_c).zip(d.chunks_mut(n_c)).enumerate() {
        let i = r0 + k;
        let (mx, my) = (mid[2 * i], mid[2 * i + 1]);
        let (nxi, nyi) = (nrm_t[2 * i], nrm_t[2 * i + 1]);
        for j in 0..n_c {
            let rx = mx - pan.ax[j];
            let ry = my - pan.ay[j];
            let x0 = rx * pan.tx[j] + ry * pan.ty[j];
            let y0 = rx * pan.nx[j] + ry * pan.ny[j];
            let s1 = -x0;
            let s2 = pan.len[j] - x0;
            let y2 = y0 * y0;
            let r1sq = s1 * s1 + y2;
            let r2sq = s2 * s2 + y2;
            let l1 = (if r1sq > TINY { r1sq } else { TINY }).ln();
            let l2 = (if r2sq > TINY { r2sq } else { TINY }).ln();
            let ay = y0.abs();
            let f2 = 0.5 * s2 * l2 - s2 + ay * s2.atan2(ay);
            let f1 = 0.5 * s1 * l1 - s1 + ay * s1.atan2(ay);
            prow[j] = -INV_2PI * (f2 - f1);
            let ex = INV_2PI * 0.5 * (l1 - l2);
            let ey = INV_2PI * (y0 * (s2 - s1)).atan2(y2 + s1 * s2);
            drow[j] = ex * (pan.tx[j] * nxi + pan.ty[j] * nyi)
                + ey * (pan.nx[j] * nxi + pan.ny[j] * nyi);
        }
    }
}

fn column(m: &PyReadonlyArray2<'_, f64>, k: usize) -> Vec<f64> {
    let v = m.as_array();
    (0..v.shape()[0]).map(|j| v[[j, k]]).collect()
}

/// Fill `p_out` / `d_out` ((n_r, n_c), C-contiguous) with the potential and normal-field
/// influence of the panels `(a, tangent, nrm, length)` at the targets `(mid, normal)`.
/// `same`: the targets are the panels themselves, so the diagonal of D is zeroed.
#[pyfunction]
#[pyo3(signature = (mid, normal, a, tangent, nrm, length, p_out, d_out, same, threads))]
#[allow(clippy::too_many_arguments)]
pub fn bem_influence<'py>(
    py: Python<'py>,
    mid: PyReadonlyArray2<'py, f64>,
    normal: PyReadonlyArray2<'py, f64>,
    a: PyReadonlyArray2<'py, f64>,
    tangent: PyReadonlyArray2<'py, f64>,
    nrm: PyReadonlyArray2<'py, f64>,
    length: PyReadonlyArray1<'py, f64>,
    mut p_out: PyReadwriteArray2<'py, f64>,
    mut d_out: PyReadwriteArray2<'py, f64>,
    same: bool,
    threads: usize,
) -> PyResult<()> {
    let bad = |m: &str| pyo3::exceptions::PyValueError::new_err(m.to_string());
    let n_r = mid.shape()[0];
    let n_c = a.shape()[0];
    if mid.shape()[1] != 2 || normal.shape() != [n_r, 2] || a.shape()[1] != 2 {
        return Err(bad("mid / normal / a must be (n, 2)"));
    }
    if tangent.shape() != [n_c, 2] || nrm.shape() != [n_c, 2] || length.shape()[0] != n_c {
        return Err(bad("panel arrays must have n_c rows"));
    }
    if p_out.shape() != [n_r, n_c] || d_out.shape() != [n_r, n_c] {
        return Err(bad("outputs must be (n_r, n_c)"));
    }
    let mid_s = mid.as_slice().map_err(|_| bad("mid must be C-contiguous"))?;
    let nrm_t = normal.as_slice().map_err(|_| bad("normal must be C-contiguous"))?;
    let pan = Panels {
        ax: column(&a, 0),
        ay: column(&a, 1),
        tx: column(&tangent, 0),
        ty: column(&tangent, 1),
        nx: column(&nrm, 0),
        ny: column(&nrm, 1),
        len: length.as_slice().map_err(|_| bad("length must be contiguous"))?,
    };
    let p_s = p_out.as_slice_mut().map_err(|_| bad("p_out must be C-contiguous"))?;
    let d_s = d_out.as_slice_mut().map_err(|_| bad("d_out must be C-contiguous"))?;
    if n_r == 0 || n_c == 0 {
        return Ok(());
    }
    let nt = threads.clamp(1, 64).min(n_r);
    py.allow_threads(|| {
        if nt == 1 {
            fill_rows(mid_s, nrm_t, &pan, 0, p_s, d_s, n_c);
        } else {
            let rows = n_r.div_ceil(nt);
            std::thread::scope(|sc| {
                for (k, (pc, dc)) in
                    p_s.chunks_mut(rows * n_c).zip(d_s.chunks_mut(rows * n_c)).enumerate()
                {
                    let pan = &pan;
                    sc.spawn(move || fill_rows(mid_s, nrm_t, pan, k * rows, pc, dc, n_c));
                }
            });
        }
    });
    if same {
        for i in 0..n_r.min(n_c) {
            d_s[i * n_c + i] = 0.0;
        }
    }
    Ok(())
}
