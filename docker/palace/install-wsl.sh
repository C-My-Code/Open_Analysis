#!/usr/bin/env bash
# SPDX-FileCopyrightText: 2026 Open_Analysis contributors
# SPDX-License-Identifier: GPL-3.0-or-later
#
# Build Palace 0.18.1 inside WSL2 (or any Linux) WITHOUT sudo and without Docker.
#
#   wsl -d Ubuntu -- bash /mnt/d/<repo>/docker/palace/install-wsl.sh
#
# Everything lands under $HOME/opt (nothing system-wide):
#   ~/opt/mm/env    micromamba environment (conda-forge): gcc/g++/gfortran 14+, cmake, Open MPI, OpenBLAS
#   ~/opt/src       Palace sources (tag v0.18.1) and the build tree
#   ~/opt/palace    install prefix: bin/palace, bin/oa-palace-run
#
# Resource use: the build runs under `systemd-run --user --scope -p MemoryMax=6G -p MemorySwapMax=0 -p CPUQuota=400%`
# (when available), `nice -n 19`, `make -j2`. About 40 minutes and 5 GB of disk on 2 cores.
#
# Why not a prebuilt binary: Palace publishes none (releases are source tags; the supported
# binaries are Spack and containers). conda-forge has no `palace` package as of 0.18.1.
# Palace's CMake superbuild downloads and builds its dependencies (MFEM, hypre, METIS,
# libCEED, SuperLU_DIST, PETSc + SLEPc), which needs outbound HTTPS to github.com and gitlab.com.

set -euo pipefail

PALACE_TAG="${PALACE_TAG:-v0.18.1}"
PREFIX="${PREFIX:-$HOME/opt/palace}"
MM="$HOME/opt/mm"
E="$MM/env"

# 1. toolchain from conda-forge (micromamba is one static binary)
if [ ! -x "$E/bin/cmake" ]; then
  mkdir -p "$MM" && cd "$MM"
  curl -sL https://micro.mamba.pm/api/micromamba/linux-64/latest | tar -xj bin/micromamba
  MAMBA_ROOT_PREFIX="$MM/root" nice -n 19 ./bin/micromamba create -y -q -p "$E" -c conda-forge \
    cmake ninja make git gxx_linux-64 gcc_linux-64 gfortran_linux-64 openmpi openblas liblapack \
    pkg-config python=3.12 gmsh
fi

# 2. sources
mkdir -p "$HOME/opt/src" && cd "$HOME/opt/src"
[ -d palace ] || "$E/bin/git" clone --depth 1 --branch "$PALACE_TAG" https://github.com/awslabs/palace.git

# 3. build (superbuild): serial-friendly settings, nothing the S-parameter flow does not use
export PATH="$E/bin:$PATH"
export CC="$E/bin/x86_64-conda-linux-gnu-gcc" CXX="$E/bin/x86_64-conda-linux-gnu-g++"
export FC="$E/bin/x86_64-conda-linux-gnu-gfortran"
export OMPI_CC="$CC" OMPI_CXX="$CXX" OMPI_FC="$FC"
export OMP_NUM_THREADS=2 OPENBLAS_NUM_THREADS=2 MKL_NUM_THREADS=2
export CMAKE_BUILD_PARALLEL_LEVEL=2 MAKEFLAGS=-j2 LD_LIBRARY_PATH="$E/lib"
mkdir -p palace-build && cd palace-build
cmake -G "Unix Makefiles" -S ../palace -B . -DCMAKE_BUILD_TYPE=Release \
  -DCMAKE_INSTALL_PREFIX="$PREFIX" \
  -DCMAKE_C_COMPILER="$CC" -DCMAKE_CXX_COMPILER="$CXX" -DCMAKE_Fortran_COMPILER="$FC" \
  -DCMAKE_PREFIX_PATH="$E" -DMPI_C_COMPILER="$E/bin/mpicc" -DMPI_CXX_COMPILER="$E/bin/mpicxx" \
  -DPALACE_WITH_GSLIB=OFF -DPALACE_WITH_SUNDIALS=OFF -DPALACE_WITH_MAGMA=OFF \
  -DPALACE_WITH_LIBXSMM=OFF
if systemd-run --user --scope -q true 2>/dev/null; then
  systemd-run --user --scope -q -p MemoryMax=6G -p MemorySwapMax=0 -p CPUQuota=400% nice -n 19 make -j2
else
  prlimit --as=$((6 * 1024 * 1024 * 1024)) nice -n 19 make -j2
fi

# 4. the resource-box wrapper next to the Palace launcher
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
install -m 0755 "$HERE/../../packages/oa-em-palace/src/oa_em_palace/scripts/oa-palace-run" \
  "$PREFIX/bin/oa-palace-run"
echo "${PALACE_TAG#v}" > "$PREFIX/VERSION"
echo "Palace installed in $PREFIX; set [tools.palace] path = \"$PREFIX/bin/oa-palace-run\""
