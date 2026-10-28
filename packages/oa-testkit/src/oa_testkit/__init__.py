# SPDX-License-Identifier: GPL-3.0-or-later
"""Open_Analysis test kit.

This module is deliberately import-light: the pytest plugin (``oa_testkit.plugin``) is loaded
through an entry point in every test session, so importing ``oa_testkit`` must not pull in
``oa_model`` or ``oa_framework``. Import the submodules you need:

- ``oa_testkit.builders``: synthetic ``Board`` builders.
- ``oa_testkit.xsections``: ``CrossSection`` fixtures with analytic expected values.
- ``oa_testkit.emproblems``: ``EMProblem`` fixtures.
- ``oa_testkit.analytic``: pure-Python reference formulas (no dependencies).
- ``oa_testkit.golden``: ``assert_close_to_golden`` and the ``case.toml`` I/O.
- ``oa_testkit.harness``: ``run_inline``.
"""

__version__ = "0.1.0"
