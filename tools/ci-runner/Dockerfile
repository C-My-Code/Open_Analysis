# SPDX-FileCopyrightText: 2026 Open_Analysis contributors
# SPDX-License-Identifier: GPL-3.0-or-later
#
# Self-hosted GitHub Actions runner for Open_Analysis: Linux + NVIDIA GPU, in a container.
# The runner runs as an unprivileged user inside this image; the host's Docker socket is never
# mounted, so a job cannot control the host. See README.md for the threat model and setup.
#
# The CUDA runtime comes from the base image; the host only needs the NVIDIA driver and the
# NVIDIA Container Toolkit. Check the tag still exists before building:
#   https://hub.docker.com/r/nvidia/cuda/tags  [verify]
ARG CUDA_IMAGE=nvidia/cuda:12.6.3-runtime-ubuntu24.04
FROM ${CUDA_IMAGE}

# Pin the runner release and its checksum from https://github.com/actions/runner/releases
# (the release notes list the SHA-256 of actions-runner-linux-x64-<version>.tar.gz).
ARG RUNNER_VERSION
ARG RUNNER_SHA256

ENV DEBIAN_FRONTEND=noninteractive
# gmsh (oa-mesh) needs libGLU and X11 client libraries, as on the hosted runners (ci.yml);
# build-essential and pkg-config are for the oa-native Rust extension.
RUN apt-get update && apt-get install -y --no-install-recommends \
        ca-certificates curl git jq tar gzip unzip xz-utils \
        build-essential pkg-config \
        libglu1-mesa libxcursor1 libxft2 libxinerama1 \
    && rm -rf /var/lib/apt/lists/*

# Volume mount points must exist and belong to the runner before the first mount: Docker
# creates a missing mount point (and a new named volume on it) as root, which the runner user
# then cannot write (uv cache, cargo registry, registration state).
RUN useradd --create-home --shell /bin/bash runner \
    && install -d -o runner -g runner /home/runner/tmp /home/runner/state /home/runner/.cache \
        /home/runner/.cargo /home/runner/.cargo/registry
WORKDIR /home/runner/actions-runner

RUN test -n "$RUNNER_VERSION" && test -n "$RUNNER_SHA256" \
    && curl -fsSL -o runner.tgz \
        "https://github.com/actions/runner/releases/download/v${RUNNER_VERSION}/actions-runner-linux-x64-${RUNNER_VERSION}.tar.gz" \
    && echo "${RUNNER_SHA256}  runner.tgz" | sha256sum -c - \
    && tar xzf runner.tgz && rm runner.tgz \
    && ./bin/installdependencies.sh \
    && rm -rf /var/lib/apt/lists/* \
    && chown -R runner:runner /home/runner

COPY --chown=runner:runner entrypoint.sh job-completed.sh /home/runner/
RUN chmod 0755 /home/runner/entrypoint.sh /home/runner/job-completed.sh

USER runner
# Rust for oa-native, installed for the runner user only (no system toolchain).
RUN curl -fsSL https://sh.rustup.rs | sh -s -- -y --profile minimal --default-toolchain stable
ENV PATH=/home/runner/.cargo/bin:/home/runner/.local/bin:$PATH \
    ACTIONS_RUNNER_HOOK_JOB_COMPLETED=/home/runner/job-completed.sh

ENTRYPOINT ["/home/runner/entrypoint.sh"]
