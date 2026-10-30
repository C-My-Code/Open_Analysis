// SPDX-FileCopyrightText: 2026 Open_Analysis contributors
// SPDX-License-Identifier: GPL-3.0-or-later
//
// Fused Yee kernels (ADR-0010 section 7). Common CUDA/HIP subset only (NVRTC and hiprtc).
//
// Layout: structure of arrays, one array per component, shape (B, Z, Y, X) with x fastest.
// Storage index I = i + 1 along each axis (see oa_fdtd.grid); ghost layers at 0 and n + 1.
// Compile-time switches: USE_DOUBLE (float64 fields), UNIFORM (one material in the z group:
// no index read, constants CA0/CB0 passed as arguments; PEC edges are zeroed afterwards by
// zero_list).
//
// Equations follow the NumPy reference (oa_fdtd.reference) exactly.

#ifdef USE_DOUBLE
typedef double real;
#else
typedef float real;
#endif

typedef unsigned long long u64;

// One thread per cell (i, j, k, b). blockIdx.z enumerates (b, k): kb = b * (nk) + (k - k0).
#define DECODE_CELL(k0, nk)                                                   \
    const int i = blockIdx.x * blockDim.x + threadIdx.x + 1;                  \
    const int j = blockIdx.y * blockDim.y + threadIdx.y + 1;                  \
    const int kb = blockIdx.z * blockDim.z + threadIdx.z;                     \
    const int bb = kb / (nk);                                                 \
    const int k = kb - bb * (nk) + (k0);

extern "C" __global__ void h_update(
    real* __restrict__ Hx, real* __restrict__ Hy, real* __restrict__ Hz,
    const real* __restrict__ Ex, const real* __restrict__ Ey, const real* __restrict__ Ez,
    const real* __restrict__ ipx, const real* __restrict__ ipy, const real* __restrict__ ipz,
    const real dtmu, const int nx, const int ny, const int nz, const int X, const int Y,
    const long long bs, const int batch)
{
    DECODE_CELL(1, nz + 1)
    if (i > nx + 1 || j > ny + 1 || bb >= batch) return;
    const long long sy = X, sz = (long long)X * Y;
    const long long p = (long long)bb * bs + (long long)k * sz + (long long)j * sy + i;
    const real ex = Ex[p], ey = Ey[p], ez = Ez[p];
    if (j <= ny && k <= nz)
        Hx[p] -= dtmu * ((Ez[p + sy] - ez) * ipy[j] - (Ey[p + sz] - ey) * ipz[k]);
    if (i <= nx && k <= nz)
        Hy[p] -= dtmu * ((Ex[p + sz] - ex) * ipz[k] - (Ez[p + 1] - ez) * ipx[i]);
    if (i <= nx && j <= ny)
        Hz[p] -= dtmu * ((Ey[p + 1] - ey) * ipx[i] - (Ex[p + sy] - ex) * ipy[j]);
}

// E update for the z planes [k0, k0 + nk). idx* are shared by all batch members.
extern "C" __global__ void e_update(
    real* __restrict__ Ex, real* __restrict__ Ey, real* __restrict__ Ez,
    const real* __restrict__ Hx, const real* __restrict__ Hy, const real* __restrict__ Hz,
    const unsigned short* __restrict__ mx, const unsigned short* __restrict__ my,
    const unsigned short* __restrict__ mz,
    const real* __restrict__ ca, const real* __restrict__ cb,
    const real* __restrict__ idx_, const real* __restrict__ idy, const real* __restrict__ idz,
    const real ca0, const real cb0, const int nx, const int ny, const int k0, const int nk,
    const int X, const int Y, const long long bs, const int batch)
{
    DECODE_CELL(k0, nk)
    if (i > nx + 1 || j > ny + 1 || bb >= batch) return;
    const long long sy = X, sz = (long long)X * Y;
    const long long q = (long long)k * sz + (long long)j * sy + i;
    const long long p = (long long)bb * bs + q;
    const real hx = Hx[p], hy = Hy[p], hz = Hz[p];
    const real dx_ = idx_[i], dy_ = idy[j], dz_ = idz[k];
    const real cx = (hz - Hz[p - sy]) * dy_ - (hy - Hy[p - sz]) * dz_;
    const real cy = (hx - Hx[p - sz]) * dz_ - (hz - Hz[p - 1]) * dx_;
    const real cz = (hy - Hy[p - 1]) * dx_ - (hx - Hx[p - sy]) * dy_;
#ifdef UNIFORM
    Ex[p] = ca0 * Ex[p] + cb0 * cx;
    Ey[p] = ca0 * Ey[p] + cb0 * cy;
    Ez[p] = ca0 * Ez[p] + cb0 * cz;
#else
    const unsigned short ax = mx[q], ay = my[q], az = mz[q];
    Ex[p] = ca[ax] * Ex[p] + cb[ax] * cx;
    Ey[p] = ca[ay] * Ey[p] + cb[ay] * cy;
    Ez[p] = ca[az] * Ez[p] + cb[az] * cz;
#endif
}

// Set listed E edges to zero (PEC edges inside uniform z groups). pf[c] = base pointer of E_c.
extern "C" __global__ void zero_list(
    const u64* __restrict__ fp, const int* __restrict__ comp, const long long* __restrict__ flat,
    const int n, const long long bs, const int batch)
{
    const long long e = (long long)blockIdx.x * blockDim.x + threadIdx.x;
    if (e >= n) return;
    real* f = (real*)fp[comp[e]];
    for (int b = 0; b < batch; ++b) f[(long long)b * bs + flat[e]] = (real)0;
}

// Source injection: F_c[b, flat] += amp * drive[b, ch] * wave[ch, step]. One thread per entry; the
// batch loop is inside so that no two threads write the same address.
extern "C" __global__ void inject(
    const u64* __restrict__ fp, const int* __restrict__ comp, const long long* __restrict__ flat,
    const double* __restrict__ amp, const int* __restrict__ chan,
    const double* __restrict__ drive, const double* __restrict__ wave, const int* __restrict__ step,
    const int n, const int nch, const int nsteps, const long long bs, const int batch)
{
    const long long e = (long long)blockIdx.x * blockDim.x + threadIdx.x;
    if (e >= n) return;
    real* f = (real*)fp[comp[e]];
    const double w = wave[(long long)chan[e] * nsteps + step[0]];
    for (int b = 0; b < batch; ++b)
        f[(long long)b * bs + flat[e]] += (real)(amp[e] * drive[b * nch + chan[e]] * w);
}

// Weighted probe sums in float64 with a fixed-order tree reduction (deterministic).
// grid = (P, B); out[(slot * B + b) * P + p].
extern "C" __global__ void probe_sum(
    const u64* __restrict__ fp, const int* __restrict__ pcomp, const long long* __restrict__ off,
    const long long* __restrict__ flat, const double* __restrict__ wgt,
    double* __restrict__ out, const int* __restrict__ step, const int chunk, const int P,
    const int batch, const long long bs)
{
    extern __shared__ double sh[];
    const int p = blockIdx.x, b = blockIdx.y;
    const real* f = (const real*)fp[pcomp[p]];
    double acc = 0.0;
    for (long long e = off[p] + threadIdx.x; e < off[p + 1]; e += blockDim.x)
        acc += (double)f[(long long)b * bs + flat[e]] * wgt[e];
    sh[threadIdx.x] = acc;
    __syncthreads();
    for (int s = blockDim.x / 2; s > 0; s >>= 1) {
        if (threadIdx.x < s) sh[threadIdx.x] += sh[threadIdx.x + s];
        __syncthreads();
    }
    if (threadIdx.x == 0) out[((long long)(step[0] % chunk) * batch + b) * P + p] = sh[0];
}

extern "C" __global__ void bump(int* __restrict__ step) { step[0] += 1; }

// CPML correction (ADR-0010 section 4). One thread per (b, box cell).
//   d   = E: (S[p] - S[p - s]) * ip[Ia]   H: (S[p + s] - S[p]) * ip[Ia]      (s = stride of axis a)
//   psi = eb * psi + ec * d ;  F[p] += sign * k * (ek * d + psi)
//   k   = E: cb[mat[q]]   H: hk (= dt / mu0)
extern "C" __global__ void cpml(
    real* __restrict__ F, const real* __restrict__ S, real* __restrict__ psi,
    const unsigned short* __restrict__ mat, const real* __restrict__ cb,
    const real* __restrict__ ip, const real* __restrict__ eb, const real* __restrict__ ec,
    const real* __restrict__ ek,
    const int is_e, const int axis, const real sign, const real hk,
    const int i0, const int j0, const int k0, const int ni, const int nj, const int nk,
    const int X, const int Y, const long long bs, const int batch)
{
    const long long t = (long long)blockIdx.x * blockDim.x + threadIdx.x;
    const long long per = (long long)ni * nj * nk;
    if (t >= per * batch) return;
    const int bb = (int)(t / per);
    long long r = t - (long long)bb * per;
    const int ii = (int)(r % ni); r /= ni;
    const int jj = (int)(r % nj);
    const int kk = (int)(r / nj);
    const int i = i0 + ii, j = j0 + jj, k = k0 + kk;
    const long long sy = X, sz = (long long)X * Y;
    const long long q = (long long)k * sz + (long long)j * sy + i;
    const long long p = (long long)bb * bs + q;
    const long long s = axis == 0 ? 1 : (axis == 1 ? sy : sz);
    const int ia = axis == 0 ? i : (axis == 1 ? j : k);
    const real d = (is_e ? (S[p] - S[p - s]) : (S[p + s] - S[p])) * ip[ia];
    const long long pi = t;  // psi shape (B, nk, nj, ni) matches the thread enumeration
    const real ps = eb[ia] * psi[pi] + ec[ia] * d;
    psi[pi] = ps;
    const real kk_ = is_e ? cb[mat[q]] : hk;
    F[p] += sign * kk_ * (ek[ia] * d + ps);
}
