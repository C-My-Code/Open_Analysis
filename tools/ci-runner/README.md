<!--
SPDX-FileCopyrightText: 2026 Open_Analysis contributors
SPDX-License-Identifier: GPL-3.0-or-later
-->
# Self-hosted Linux GPU runner (in a container)

This sets up a dedicated Linux machine with an NVIDIA GPU as a GitHub Actions runner for
Open_Analysis. It runs:

- the Linux test suite on every push to `main`;
- the GPU tests;
- the nightly slow, EM validation and benchmark jobs.

Everything runs in one container. The host keeps only the NVIDIA driver, Docker, the NVIDIA
Container Toolkit, and a firewall rule.

## Threat model

The repository is public. Self-hosted runners on public repositories are dangerous, because a
pull request from a fork can change a workflow so that it runs on your machine. This setup uses
several layers of protection:

1. **Untrusted code is never scheduled here.** The `linux-gpu` jobs in `ci.yml` run only on
   `push` to `main`, `schedule` or `workflow_dispatch`, and only in `C-My-Code/Open_Analysis`.
   They never run on `pull_request`. Also set Settings → Actions → General → *Fork pull request
   workflows from outside collaborators* → **Require approval for all outside collaborators**,
   and read the diff of any PR before approving its workflows.
2. **No host control.** The runner is an unprivileged user in a container that drops all
   capabilities and sets `no-new-privileges`. It has no Docker socket, no privileged mode and no
   host network. CPU, memory and PIDs are capped.
3. **No lateral movement.** `firewall.sh` rejects traffic from the runner network (`br-oa-ci`)
   to the tailnet (100.64.0.0/10, fd7a:115c:a1e0::/48) and to the private LAN ranges. Jobs can
   reach the internet (GitHub, PyPI) and nothing else. This matters because this machine also
   runs Tailscale.
4. **No secrets.** The runner jobs use no repository secrets, and the workflow sets
   `permissions: contents: read`.
5. **Clean state.** After every job, `job-completed.sh` kills leftover processes and clears
   scratch space. Restarting the container (`docker compose restart`) resets everything except
   the runner registration and the uv and cargo caches.

Residual risk: a malicious dependency pulled in by `uv sync` would run inside the container,
with internet access only. The same is true on any developer machine.

## Setup (on the Linux machine)

1. **Prepare the laptop.** Keep it on AC power. Disable suspend, including on lid close
   (Settings → Power). Make sure the NVIDIA GPU is active: on Pop!_OS, run
   `system76-power graphics` and use `hybrid` or `nvidia`. `nvidia-smi` should list the GPU.
2. **Install the NVIDIA Container Toolkit.** Follow NVIDIA's install guide for Ubuntu-based
   distributions (<https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/>). Then:

   ```bash
   sudo nvidia-ctk runtime configure --runtime=docker && sudo systemctl restart docker
   docker run --rm --gpus all nvidia/cuda:12.6.3-base-ubuntu24.04 nvidia-smi   # must list the GPU
   ```
3. **Install the firewall.** Run this from this folder, then check that it reports the rules
   for `br-oa-ci`:

   ```bash
   sudo install -m 0755 firewall.sh /usr/local/sbin/oa-ci-firewall
   sudo install -m 0644 oa-ci-firewall.service /etc/systemd/system/
   sudo systemctl daemon-reload && sudo systemctl enable --now oa-ci-firewall
   sudo oa-ci-firewall status
   ```
4. **Configure.** Create `.env` next to `compose.yaml`. It is not committed. Take the newest
   version and its SHA-256 from <https://github.com/actions/runner/releases>:

   ```text
   RUNNER_VERSION=2.xxx.x
   RUNNER_SHA256=<sha256 of actions-runner-linux-x64-<version>.tar.gz>
   RUNNER_TOKEN=<registration token, see the next step>
   ```
5. **Get a registration token.** Go to GitHub → the repository → Settings → Actions → Runners →
   *New self-hosted runner*, and copy the token from the `config.sh` line. It expires after an
   hour and is only used once. After the first start, delete `RUNNER_TOKEN` from `.env`.
6. **Start the runner.**

   ```bash
   docker compose up -d --build
   docker compose logs -f   # wait for "Listening for Jobs"
   ```

   The runner then shows as *Idle* under Settings → Actions → Runners, with the label
   `oa-linux-gpu`.
7. **Verify isolation.** Both checks below must be blocked or time out, and the last command
   must print the GPU:

   ```bash
   docker compose exec runner bash -c 'curl -m 5 -sS http://100.100.100.100 || echo blocked'
   docker compose exec runner bash -c 'curl -m 5 -sS http://192.168.1.1 || echo blocked'
   docker compose exec runner nvidia-smi
   ```
8. **Enable the jobs.** Go to Settings → Secrets and variables → Actions → Variables, and set
   `OA_SELF_HOSTED_LINUX` = `true`. To switch the runner off, set the variable to `false` or
   run `docker compose stop`.

## Operation

- **Updating:** change `RUNNER_VERSION` and `RUNNER_SHA256` in `.env`, then run
  `docker compose up -d --build`. The registration stays in the `state` volume.
- **Removing:** run `docker compose down -v` (this deletes the registration), remove the runner
  under Settings → Actions → Runners, and run `sudo systemctl disable --now oa-ci-firewall`.
- **Resources:** the container uses up to 12 of 16 CPUs and 24 of 32 GB, and the project's
  `OA_MEMCAP_GB` caps each Python process at 16 GB. The RTX 2060 (6 GB, slow FP64) runs every
  GPU test. FP64 timings are not comparable with the RTX 4080 SUPER reference machine.
