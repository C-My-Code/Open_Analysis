// SPDX-FileCopyrightText: 2026 Open_Analysis contributors
// SPDX-License-Identifier: GPL-3.0-or-later
//
// Correction kernels on compact edge lists (ADR-0010 section 3 and 5): Debye ADE and the
// surface-impedance boundary condition. One thread per listed edge; the batch loop is inside so
// that no two threads write the same address. fp[0..2] = E base pointers, fp[3..5] = H.
// Equations follow oa_fdtd.reference exactly.

#ifdef USE_DOUBLE
typedef double real;
#else
typedef float real;
#endif

typedef unsigned long long u64;

// Save E^n of the dispersive edges (the ADE update needs dE = E^{n+1} - E^n).
extern "C" __global__ void ade_pre(
    const u64* __restrict__ fp, const int* __restrict__ comp, const long long* __restrict__ flat,
    real* __restrict__ eold, const int ne, const long long bs, const int batch)
{
    const int e = blockIdx.x * blockDim.x + threadIdx.x;
    if (e >= ne) return;
    const real* f = (const real*)fp[comp[e]];
    for (int b = 0; b < batch; ++b) eold[(long long)b * ne + e] = f[(long long)b * bs + flat[e]];
}

// E^{n+1} -= cb * 1/2 sum_p (1 + k_p) J_p^n ;  J_p^{n+1} = k_p J_p^n + beta_p (E^{n+1} - E^n).
extern "C" __global__ void ade_post(
    const u64* __restrict__ fp, const int* __restrict__ comp, const long long* __restrict__ flat,
    const real* __restrict__ cbe, const real* __restrict__ beta, const real* __restrict__ kp,
    real* __restrict__ J, const real* __restrict__ eold, const int ne, const int np_,
    const long long bs, const int batch)
{
    const int e = blockIdx.x * blockDim.x + threadIdx.x;
    if (e >= ne) return;
    real* f = (real*)fp[comp[e]];
    for (int b = 0; b < batch; ++b) {
        real jt = 0;
        for (int p = 0; p < np_; ++p)
            jt += (real)0.5 * ((real)1 + kp[p]) * J[((long long)p * batch + b) * ne + e];
        real* ep = f + (long long)b * bs + flat[e];
        const real en = *ep - cbe[e] * jt;
        const real de = en - eold[(long long)b * ne + e];
        for (int p = 0; p < np_; ++p) {
            real* jp = J + ((long long)p * batch + b) * ne + e;
            *jp = kp[p] * (*jp) + beta[(long long)p * ne + e] * de;
        }
        *ep = en;
    }
}

// Surface impedance: E_t = Zs(s) * (n x H_t), recursive convolution of the R0 + sum R_p s tau/(1+s tau)
// ladder (bilinear sections): v_p = alpha_p (J - Jprev) + gamma_p v_p.
extern "C" __global__ void sibc_update(
    const u64* __restrict__ fp, const int* __restrict__ comp, const long long* __restrict__ flat,
    const int* __restrict__ hcomp, const long long* __restrict__ hflat,
    const real* __restrict__ hsign, const int* __restrict__ mat, const real* __restrict__ r0,
    const real* __restrict__ rp, const real* __restrict__ cp, const real* __restrict__ gp,
    real* __restrict__ jprev, real* __restrict__ v, const int ne, const int np_,
    const long long bs, const int batch)
{
    const int e = blockIdx.x * blockDim.x + threadIdx.x;
    if (e >= ne) return;
    real* f = (real*)fp[comp[e]];
    const int m = mat[e];
    for (int b = 0; b < batch; ++b) {
        real j = 0;
        for (int s = 0; s < 2; ++s) {
            const real sg = hsign[2 * e + s];
            if (sg != (real)0)
                j += sg * ((const real*)fp[3 + hcomp[2 * e + s]])[(long long)b * bs + hflat[2 * e + s]];
        }
        real out = r0[m] * j;
        const long long be = (long long)b * ne + e;
        const real dj = j - jprev[be];
        for (int p = 0; p < np_; ++p) {
            real* vp = v + (long long)p * batch * ne + be;
            const real nv = rp[m * np_ + p] * cp[p] * dj + gp[p] * (*vp);
            *vp = nv;
            out += nv;
        }
        jprev[be] = j;
        f[(long long)b * bs + flat[e]] = out;
    }
}
