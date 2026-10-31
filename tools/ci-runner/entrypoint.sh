#!/usr/bin/env bash
# SPDX-FileCopyrightText: 2026 Open_Analysis contributors
# SPDX-License-Identifier: GPL-3.0-or-later
#
# Container entrypoint. The runner registration (.runner, .credentials*) lives in the
# /home/runner/state volume so it survives container restarts; everything else is fresh on
# every start. First start: pass RUNNER_TOKEN (a short-lived registration token from the repo's
# Settings -> Actions -> Runners -> New self-hosted runner). Later starts need no token.
set -euo pipefail
cd /home/runner/actions-runner
STATE=/home/runner/state

for f in .runner .credentials .credentials_rsaparams; do
    [ -f "$STATE/$f" ] && cp "$STATE/$f" "./$f"
done

if [ ! -f .runner ]; then
    : "${RUNNER_URL:?set RUNNER_URL, e.g. https://github.com/C-My-Code/Open_Analysis}"
    : "${RUNNER_TOKEN:?first start needs RUNNER_TOKEN (registration token, valid 1 hour)}"
    ./config.sh --unattended --url "$RUNNER_URL" --token "$RUNNER_TOKEN" \
        --name "${RUNNER_NAME:-oa-linux-gpu}" --labels "oa-linux-gpu" \
        --work /home/runner/work --replace
    for f in .runner .credentials .credentials_rsaparams; do cp "./$f" "$STATE/$f"; done
    chmod 600 "$STATE"/.credentials*
fi

exec ./run.sh
