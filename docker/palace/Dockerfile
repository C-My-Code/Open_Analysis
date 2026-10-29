# SPDX-FileCopyrightText: 2026 Open_Analysis contributors
# SPDX-License-Identifier: GPL-3.0-or-later
#
# Palace 0.18.1 (AWS, Apache-2.0) built with Spack, for the `docker` runner of oa_jobs.
# Not built in CI by default (about 1 h, 6 GB of disk). Build and tag:
#
#   docker build -f docker/palace/Dockerfile -t open-analysis/palace:0.18.1 .
#
# (run from the repository root: the context must contain the oa-palace-run wrapper)
#
# tools.toml (the job directory is mounted at /work, the runner starts the program there):
#
#   [tools.palace]
#   runner = "docker"
#   image  = "open-analysis/palace:0.18.1"
#   path   = "/opt/oa/oa-palace-run"
#
# The container is already a resource box when started with `docker run --memory 8g --cpus 2`
# (oa_jobs DockerRunner passes --memory from the job budget); oa-palace-run falls back to prlimit
# inside it because there is no systemd user manager.
#
# GPU: add `+cuda cuda_arch=<sm>` (or `+rocm`) to the spec below, base the image on the CUDA
# runtime, and run the adapter with PalaceAdapter(gpu=True).

FROM ubuntu:24.04 AS build

ARG PALACE_VERSION=0.18.1
ARG SPACK_VERSION=v1.0.0
ENV DEBIAN_FRONTEND=noninteractive

RUN apt-get update && apt-get install -y --no-install-recommends \
      build-essential gfortran git curl ca-certificates python3 unzip xz-utils \
      bzip2 gnupg file patch cmake pkg-config \
    && rm -rf /var/lib/apt/lists/*

RUN git clone --depth 1 --branch ${SPACK_VERSION} https://github.com/spack/spack.git /opt/spack
ENV PATH=/opt/spack/bin:$PATH

# MPI via the system-independent Spack build; -j2 keeps the build polite on a workstation
RUN spack compiler find \
 && spack env create -d /opt/palace-env \
 && spack -e /opt/palace-env add palace@${PALACE_VERSION}~cuda~rocm+superlu-dist+slepc~strumpack~mumps~arpack ^openmpi \
 && spack -e /opt/palace-env config add "config:build_jobs:2" \
 && spack -e /opt/palace-env install --fail-fast \
 && spack -e /opt/palace-env gc -y

FROM ubuntu:24.04
ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update && apt-get install -y --no-install-recommends \
      libgfortran5 libgomp1 python3 util-linux procps ca-certificates gawk \
    && rm -rf /var/lib/apt/lists/*
COPY --from=build /opt/spack /opt/spack
COPY --from=build /opt/palace-env /opt/palace-env
# expose the view of the environment: bin/palace, mpirun and the libraries
RUN ln -s /opt/palace-env/.spack-env/view /opt/palace
COPY packages/oa-em-palace/src/oa_em_palace/scripts/oa-palace-run /opt/oa/oa-palace-run
RUN chmod +x /opt/oa/oa-palace-run
ENV OA_PALACE_HOME=/opt/palace OA_PALACE_ENV=/opt/palace OA_PALACE_STAGE=0
WORKDIR /work
# no ENTRYPOINT: the docker runner appends the program path from tools.toml
