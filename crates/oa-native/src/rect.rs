// SPDX-FileCopyrightText: 2026 Open_Analysis contributors
// SPDX-License-Identifier: GPL-3.0-or-later
//! Footprint-rectangle coverage and boundary distance (WP-509b), the kernel behind
//! `oa_geometry.refmap._bulk_cover`.
//!
//! A straight piece has a footprint rectangle `[0, len] x [-r, r]` in its own frame. The
//! rectangle lies in a closed polygon when no ring edge meets its open interior and its centre
//! is inside (the interior is then connected and never meets the boundary). The oracle is
//! `shapely.covers(part, shapely.buffer(line, r, cap_style="flat"))`.

use numpy::{PyReadonlyArray1, PyReadonlyArray2, PyReadonlyArray3, PyUntypedArrayMethods};
use pyo3::prelude::*;

type P = (f64, f64);

/// Frame of a segment: origin `a`, unit direction `d`.
struct Frame {
    a: P,
    d: P,
}

impl Frame {
    #[inline]
    fn to_local(&self, p: P) -> P {
        let (x, y) = (p.0 - self.a.0, p.1 - self.a.1);
        (x * self.d.0 + y * self.d.1, -x * self.d.1 + y * self.d.0)
    }
}

/// Does the segment `p-q` (local frame) meet the open rectangle `(0, len) x (-r, r)`?
fn meets_open_rect(p: P, q: P, len: f64, r: f64) -> bool {
    // Liang-Barsky against the closed rectangle, then require the clipped part to have
    // positive length and an interior midpoint (a part lying along a side is not interior).
    let (dx, dy) = (q.0 - p.0, q.1 - p.1);
    let (mut t0, mut t1) = (0.0f64, 1.0f64);
    for (pp, qq) in [(-dx, p.0), (dx, len - p.0), (-dy, p.1 + r), (dy, r - p.1)] {
        if pp == 0.0 {
            if qq < 0.0 {
                return false;
            }
        } else {
            let t = qq / pp;
            if pp < 0.0 {
                if t > t1 {
                    return false;
                }
                if t > t0 {
                    t0 = t;
                }
            } else {
                if t < t0 {
                    return false;
                }
                if t < t1 {
                    t1 = t;
                }
            }
        }
    }
    if t1 <= t0 {
        return false;
    }
    let tm = 0.5 * (t0 + t1);
    let (mx, my) = (p.0 + tm * dx, p.1 + tm * dy);
    mx > 0.0 && mx < len && my > -r && my < r
}

#[inline]
fn dist_point_rect(p: P, len: f64, r: f64) -> f64 {
    let dx = (0.0 - p.0).max(p.0 - len).max(0.0);
    let dy = (-r - p.1).max(p.1 - r).max(0.0);
    dx.hypot(dy)
}

#[inline]
fn dist_point_seg(p: P, a: P, b: P) -> f64 {
    let (dx, dy) = (b.0 - a.0, b.1 - a.1);
    let l2 = dx * dx + dy * dy;
    let t = if l2 == 0.0 {
        0.0
    } else {
        (((p.0 - a.0) * dx + (p.1 - a.1) * dy) / l2).clamp(0.0, 1.0)
    };
    (p.0 - (a.0 + t * dx)).hypot(p.1 - (a.1 + t * dy))
}

/// Distance of a segment that does not meet the open rectangle to the closed rectangle.
fn dist_seg_rect(p: P, q: P, len: f64, r: f64) -> f64 {
    let mut d = dist_point_rect(p, len, r).min(dist_point_rect(q, len, r));
    for c in [(0.0, -r), (len, -r), (len, r), (0.0, r)] {
        d = d.min(dist_point_seg(c, p, q));
    }
    d
}

/// For each (segment, polygon) pair: is the footprint rectangle of half-width `r_foot` around
/// the segment inside the closed polygon, and how far is the body rectangle (half-width
/// `r_body`) from the polygon boundary (shell and holes), capped at `cap`.
///
/// `lines` (n, 2, 2); `r_foot`, `r_body`, `cap`, `geom_of` (n,); polygons are given by the ring
/// tables `xy`, `ring_start`, `geom_start` (as for `cut_intervals`). The distance is `cap`
/// where the rectangle is not covered.
#[pyfunction]
#[allow(clippy::too_many_arguments, clippy::type_complexity)]
pub fn rect_cover_edge<'py>(
    py: Python<'py>,
    lines: PyReadonlyArray3<'py, f64>,
    r_foot: PyReadonlyArray1<'py, f64>,
    r_body: PyReadonlyArray1<'py, f64>,
    cap: PyReadonlyArray1<'py, f64>,
    geom_of: PyReadonlyArray1<'py, i64>,
    xy: PyReadonlyArray2<'py, f64>,
    ring_start: PyReadonlyArray1<'py, i64>,
    geom_start: PyReadonlyArray1<'py, i64>,
) -> PyResult<(Vec<bool>, Vec<f64>)> {
    let bad = |m: &'static str| pyo3::exceptions::PyValueError::new_err(m);
    if lines.shape().get(1) != Some(&2) || lines.shape().get(2) != Some(&2) {
        return Err(bad("lines must be (n, 2, 2)"));
    }
    let l = lines.as_array();
    let rf = r_foot.as_slice().map_err(|_| bad("r_foot must be contiguous"))?;
    let rb = r_body.as_slice().map_err(|_| bad("r_body must be contiguous"))?;
    let cp = cap.as_slice().map_err(|_| bad("cap must be contiguous"))?;
    let go = geom_of.as_slice().map_err(|_| bad("geom_of must be contiguous"))?;
    let pts = xy.as_slice().map_err(|_| bad("xy must be C-contiguous"))?;
    let rs = ring_start.as_slice().map_err(|_| bad("ring_start must be contiguous"))?;
    let gs = geom_start.as_slice().map_err(|_| bad("geom_start must be contiguous"))?;
    let n = l.shape()[0];
    let res = py.allow_threads(|| {
        let mut covered = vec![false; n];
        let mut dist = vec![0.0f64; n];
        for i in 0..n {
            dist[i] = cp[i];
            let a = (l[[i, 0, 0]], l[[i, 0, 1]]);
            let b = (l[[i, 1, 0]], l[[i, 1, 1]]);
            let len = (b.0 - a.0).hypot(b.1 - a.1);
            if len == 0.0 {
                continue;
            }
            let fr = Frame { a, d: ((b.0 - a.0) / len, (b.1 - a.1) / len) };
            let (r, rbody) = (rf[i], rb[i]);
            let g = go[i] as usize;
            // world box of the footprint; edges farther than `cap` from it are skipped
            let ex = r * fr.d.1.abs();
            let ey = r * fr.d.0.abs();
            let (fx0, fx1) = (a.0.min(b.0) - ex, a.0.max(b.0) + ex);
            let (fy0, fy1) = (a.1.min(b.1) - ey, a.1.max(b.1) + ey);
            let c = cp[i];
            let mut ok = true;
            let mut dmin = c;
            let mut hits = 0usize; // crossings of the +x ray from the centre (even-odd)
            let mid = (0.5 * (a.0 + b.0), 0.5 * (a.1 + b.1));
            'rings: for ri in gs[g] as usize..gs[g + 1] as usize {
                let (s0, s1) = (rs[ri] as usize, rs[ri + 1] as usize);
                let nv = s1 - s0;
                if nv < 2 {
                    continue;
                }
                let mut p0 = (pts[2 * s0], pts[2 * s0 + 1]);
                for k in 1..=nv {
                    let q = s0 + (k % nv);
                    let p1 = (pts[2 * q], pts[2 * q + 1]);
                    if (p0.1 > mid.1) != (p1.1 > mid.1) {
                        let xint = p0.0 + (mid.1 - p0.1) * (p1.0 - p0.0) / (p1.1 - p0.1);
                        if mid.0 < xint {
                            hits += 1;
                        }
                    }
                    let (bx0, bx1) = (p0.0.min(p1.0), p0.0.max(p1.0));
                    let (by0, by1) = (p0.1.min(p1.1), p0.1.max(p1.1));
                    if !(bx1 < fx0 - c || bx0 > fx1 + c || by1 < fy0 - c || by0 > fy1 + c) {
                        let (lp, lq) = (fr.to_local(p0), fr.to_local(p1));
                        if meets_open_rect(lp, lq, len, r) {
                            ok = false;
                            break 'rings;
                        }
                        let d = dist_seg_rect(lp, lq, len, rbody);
                        if d < dmin {
                            dmin = d;
                        }
                    }
                    p0 = p1;
                }
            }
            if ok && hits % 2 == 1 {
                covered[i] = true;
                dist[i] = dmin;
            }
        }
        (covered, dist)
    });
    Ok(res)
}
