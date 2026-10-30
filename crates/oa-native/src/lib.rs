// SPDX-FileCopyrightText: 2026 Open_Analysis contributors
// SPDX-License-Identifier: GPL-3.0-or-later
//! Open_Analysis compiled core (ADR-0008). Every kernel has a pure-Python oracle.

use pyo3::prelude::*;

mod clip;
mod geom;
mod sexpr;

/// Version of the native kernel ABI; bumped when a kernel's result contract changes.
#[pyfunction]
fn kernel_version() -> u32 {
    1
}

#[pymodule]
fn _native(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_function(wrap_pyfunction!(kernel_version, m)?)?;
    m.add_function(wrap_pyfunction!(sexpr::parse, m)?)?;
    m.add_function(wrap_pyfunction!(geom::ring_self_intersects, m)?)?;
    m.add_function(wrap_pyfunction!(geom::all_points_inside, m)?)?;
    m.add_function(wrap_pyfunction!(clip::cut_intervals, m)?)?;
    Ok(())
}
