# SPDX-FileCopyrightText: 2026 Open_Analysis contributors
# SPDX-License-Identifier: GPL-3.0-or-later

"""``python -m oa_mcp``."""

from oa_mcp.cli import main

if __name__ == "__main__":  # a spawned child re-importing this module must not serve
    main()
