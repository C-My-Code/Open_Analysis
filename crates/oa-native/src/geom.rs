// SPDX-FileCopyrightText: 2026 Open_Analysis contributors
// SPDX-License-Identifier: GPL-3.0-or-later
//! Polygon ring predicates used by `oa_model.validate` (WP-509).
//!
//! Both kernels use the same f64 expressions, in the same order, as the numpy oracle in
//! `oa_model.validate` (`_ring_self_intersects`, `_points_inside`), so the booleans are identical.
//! Rust does not contract `a * b - c * d` into a fused multiply-add, so no rounding differs.

use numpy::{PyReadonlyArray2, PyUntypedArrayMethods};
use pyo3::prelude::*;

#[inline]
fn orient(u: (f64, f64), v: (f64, f64), w: (f64, f64)) -> f64 {
    (v.0 - u.0) * (w.1 - u.1) - (v.1 - u.1) * (w.0 - u.0)
}

fn rows(a: &PyReadonlyArray2<'_, f64>) -> Vec<(f64, f64)> {
    let v = a.as_array();
    v.outer_iter().map(|r| (r[0], r[1])).collect()
}

/// True when two non-adjacent edges of the closed ring cross properly (strictly, at interior
/// points of both). Mirrors `oa_model.validate._ring_self_intersects`.
#[pyfunction]
pub fn ring_self_intersects(a: PyReadonlyArray2<'_, f64>) -> bool {
    if a.shape().get(1) != Some(&2) {
        return false;
    }
    let p = rows(&a);
    let n = p.len();
    for i in 0..n {
        let (pi, qi) = (p[i], p[(i + 1) % n]);
        for j in (i + 1)..n {
            let d = j - i;
            if d <= 1 || d == n - 1 {
                continue;
            }
            let (pj, qj) = (p[j], p[(j + 1) % n]);
            let o1 = orient(pi, qi, pj);
            let o2 = orient(pi, qi, qj);
            if !(o1 * o2 < 0.0) {
                continue;
            }
            let o3 = orient(pj, qj, pi);
            let o4 = orient(pj, qj, qi);
            if o3 * o4 < 0.0 {
                return true;
            }
        }
    }
    false
}

/// True when every point of `pts` is inside `ring` by the even-odd rule. Mirrors
/// `oa_model.validate._points_inside(...).all()`.
#[pyfunction]
pub fn all_points_inside(ring: PyReadonlyArray2<'_, f64>, pts: PyReadonlyArray2<'_, f64>) -> bool {
    let r = rows(&ring);
    let n = r.len();
    for (x, y) in rows(&pts) {
        let mut hits = 0usize;
        for k in 0..n {
            let (x1, y1) = r[k];
            let (x2, y2) = r[(k + 1) % n];
            if (y1 > y) != (y2 > y) {
                let xint = x1 + (y - y1) * (x2 - x1) / (y2 - y1);
                if x < xint {
                    hits += 1;
                }
            }
        }
        if hits % 2 != 1 {
            return false;
        }
    }
    true
}
