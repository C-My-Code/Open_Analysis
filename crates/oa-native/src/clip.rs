// SPDX-FileCopyrightText: 2026 Open_Analysis contributors
// SPDX-License-Identifier: GPL-3.0-or-later
//! Clipping kernels (WP-509b): segments against polygons (closed-set semantics).
//!
//! `cut_intervals` is the kernel behind `oa_geometry.sampling._u_intervals`: the parts of a
//! straight segment that lie in the closed point set of a (multi)polygon with holes, the same set
//! `shapely.intersection(line, polygon)` returns as LineStrings (boundary overlaps included,
//! single-point touches dropped because they have no length). The oracle is the GEOS path.
//!
//! Method: the segment's supporting line is split at every crossing with a ring edge. Crossings
//! use a half-open sidedness rule (a vertex on the line counts as "not positive"), so the
//! even-odd parity of the crossings left of a parameter says whether the (infinitesimally
//! displaced) line is inside. Edges lying exactly on the line contribute their overlap
//! explicitly, which makes the result the closed set.

use numpy::{PyReadonlyArray1, PyReadonlyArray2, PyReadonlyArray3, PyUntypedArrayMethods};
use pyo3::prelude::*;

type P = (f64, f64);

#[inline]
fn cross(a: P, b: P) -> f64 {
    a.0 * b.1 - a.1 * b.0
}

/// Closed-set intervals `[t0, t1]` (parameters in `[0, 1]`) of the segment `a + t (b - a)`
/// inside the polygon rings given as `(ring start, ring end)` index pairs into `xy`.
fn segment_in_rings(
    a: P,
    b: P,
    xy: &[f64],
    rings: &[(usize, usize, (f64, f64, f64, f64))],
    toggles: &mut Vec<f64>,
    extra: &mut Vec<(f64, f64)>,
    out: &mut Vec<(f64, f64)>,
) {
    out.clear();
    toggles.clear();
    extra.clear();
    let d = (b.0 - a.0, b.1 - a.1);
    let dd = d.0 * d.0 + d.1 * d.1;
    if dd == 0.0 {
        return;
    }
    let (lx0, lx1) = (a.0.min(b.0), a.0.max(b.0));
    let (ly0, ly1) = (a.1.min(b.1), a.1.max(b.1));
    for &(s, e, bb) in rings {
        let n = e - s;
        if n < 2 {
            continue;
        }
        // a segment whose box misses the ring's box lies outside the ring entirely
        if lx1 < bb.0 || lx0 > bb.2 || ly1 < bb.1 || ly0 > bb.3 {
            continue;
        }
        let mut p0 = (xy[2 * s], xy[2 * s + 1]);
        let mut s0 = cross(d, (p0.0 - a.0, p0.1 - a.1));
        for k in 1..=n {
            let q = s + (k % n);
            let p1 = (xy[2 * q], xy[2 * q + 1]);
            if p1 == p0 {
                continue;
            }
            let s1 = cross(d, (p1.0 - a.0, p1.1 - a.1));
            if s0 == 0.0 && s1 == 0.0 {
                // edge on the supporting line: its overlap with the segment is in the set
                let t0 = ((p0.0 - a.0) * d.0 + (p0.1 - a.1) * d.1) / dd;
                let t1 = ((p1.0 - a.0) * d.0 + (p1.1 - a.1) * d.1) / dd;
                let (lo, hi) = (t0.min(t1).max(0.0), t0.max(t1).min(1.0));
                if hi > lo {
                    extra.push((lo, hi));
                }
            } else if (s0 > 0.0) != (s1 > 0.0) {
                let u = s0 / (s0 - s1);
                let q = (p0.0 + u * (p1.0 - p0.0), p0.1 + u * (p1.1 - p0.1));
                let t = ((q.0 - a.0) * d.0 + (q.1 - a.1) * d.1) / dd;
                if t <= 1.0 {
                    toggles.push(t);
                }
            }
            p0 = p1;
            s0 = s1;
        }
    }
    toggles.sort_by(|x, y| x.partial_cmp(y).unwrap_or(std::cmp::Ordering::Equal));
    // state at t = 0: parity of the crossings at t < 0
    let mut inside = false;
    let mut i = 0;
    while i < toggles.len() && toggles[i] < 0.0 {
        inside = !inside;
        i += 1;
    }
    let mut start = 0.0f64;
    while i < toggles.len() {
        let t = toggles[i];
        if inside {
            out.push((start, t));
        } else {
            start = t;
        }
        inside = !inside;
        i += 1;
    }
    if inside {
        out.push((start, 1.0));
    }
    out.extend(extra.iter().copied());
    out.sort_by(|x, y| x.0.partial_cmp(&y.0).unwrap_or(std::cmp::Ordering::Equal));
    // merge touching / overlapping intervals
    let mut w = 0usize;
    for r in 0..out.len() {
        let cur = out[r];
        if w > 0 && cur.0 <= out[w - 1].1 {
            if cur.1 > out[w - 1].1 {
                out[w - 1].1 = cur.1;
            }
        } else {
            out[w] = cur;
            w += 1;
        }
    }
    out.truncate(w);
}

/// Parts of straight segments inside closed polygons.
///
/// * `lines` (n, 2, 2): segment endpoints.
/// * `geom_of` (n,): geometry index of each segment.
/// * `xy` (m, 2): all ring vertices (rings closed or open; the closing edge is implied).
/// * `ring_start` (r + 1,): offsets of the rings in `xy`.
/// * `geom_start` (g + 1,): offsets of the rings of each geometry (holes and shells alike).
///
/// Returns `(line index, x0, y0, x1, y1)` columns of the inside parts, in line order. A part
/// that reaches the segment end uses the exact endpoint.
#[pyfunction]
#[allow(clippy::type_complexity)]
pub fn cut_intervals<'py>(
    py: Python<'py>,
    lines: PyReadonlyArray3<'py, f64>,
    geom_of: PyReadonlyArray1<'py, i64>,
    xy: PyReadonlyArray2<'py, f64>,
    ring_start: PyReadonlyArray1<'py, i64>,
    geom_start: PyReadonlyArray1<'py, i64>,
) -> PyResult<(Vec<i64>, Vec<f64>)> {
    if lines.shape().get(1) != Some(&2) || lines.shape().get(2) != Some(&2) {
        return Err(pyo3::exceptions::PyValueError::new_err("lines must be (n, 2, 2)"));
    }
    let l = lines.as_array();
    let go = geom_of.as_array();
    let pts = xy
        .as_slice()
        .map_err(|_| pyo3::exceptions::PyValueError::new_err("xy must be C-contiguous"))?;
    let rs = ring_start
        .as_slice()
        .map_err(|_| pyo3::exceptions::PyValueError::new_err("ring_start must be contiguous"))?;
    let gs = geom_start
        .as_slice()
        .map_err(|_| pyo3::exceptions::PyValueError::new_err("geom_start must be contiguous"))?;
    let n = l.shape()[0];
    let geoms_ok = !gs.is_empty() && !rs.is_empty();
    if !geoms_ok {
        return Ok((vec![], vec![]));
    }
    let rings_of = |g: usize| -> Vec<(usize, usize, (f64, f64, f64, f64))> {
        (gs[g] as usize..gs[g + 1] as usize)
            .map(|r| {
                let mut bb = (f64::INFINITY, f64::INFINITY, f64::NEG_INFINITY, f64::NEG_INFINITY);
                let (s0, s1) = (rs[r] as usize, rs[r + 1] as usize);
                for p in pts[2 * s0..2 * s1].chunks_exact(2) {
                    bb = (bb.0.min(p[0]), bb.1.min(p[1]), bb.2.max(p[0]), bb.3.max(p[1]));
                }
                (s0, s1, bb)
            })
            .collect()
    };
    let res = py.allow_threads(|| {
        let mut idx: Vec<i64> = Vec::new();
        let mut val: Vec<f64> = Vec::new();
        let mut toggles = Vec::new();
        let mut extra = Vec::new();
        let mut out = Vec::new();
        let mut cur_g = usize::MAX;
        let mut rings: Vec<(usize, usize, (f64, f64, f64, f64))> = Vec::new();
        for i in 0..n {
            let g = go[i] as usize;
            if g != cur_g {
                rings = rings_of(g);
                cur_g = g;
            }
            let a = (l[[i, 0, 0]], l[[i, 0, 1]]);
            let b = (l[[i, 1, 0]], l[[i, 1, 1]]);
            segment_in_rings(a, b, pts, &rings, &mut toggles, &mut extra, &mut out);
            let d = (b.0 - a.0, b.1 - a.1);
            for &(t0, t1) in out.iter() {
                let p0 = if t0 <= 0.0 { a } else { (a.0 + t0 * d.0, a.1 + t0 * d.1) };
                let p1 = if t1 >= 1.0 { b } else { (a.0 + t1 * d.0, a.1 + t1 * d.1) };
                idx.push(i as i64);
                val.extend_from_slice(&[p0.0, p0.1, p1.0, p1.1]);
            }
        }
        (idx, val)
    });
    Ok(res)
}
