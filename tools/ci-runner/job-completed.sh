#!/usr/bin/env bash
# SPDX-FileCopyrightText: 2026 Open_Analysis contributors
# SPDX-License-Identifier: GPL-3.0-or-later
#
# Runs after every job: kill anything the job left behind and clear its scratch space, so the
# next job starts clean even though the container is long-lived. The uv and cargo caches stay.
set -u
pkill -u runner -f 'python|pytest|openEMS|palace|gmsh' 2>/dev/null || true
rm -rf /home/runner/work/_temp/* /tmp/* 2>/dev/null || true
exit 0
