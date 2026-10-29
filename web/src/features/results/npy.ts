// SPDX-License-Identifier: GPL-3.0-or-later
/** Minimal .npy (v1-v3) reader for the `/results/{rid}/items/{item}/arrays/{name}` endpoint. */
export interface Npy {
  shape: number[];
  /** Real dtypes: the values. Complex dtypes: interleaved re, im (length 2 * size). */
  data: Float64Array;
  complex: boolean;
}

export function parseNpy(buf: ArrayBuffer): Npy {
  const bytes = new Uint8Array(buf);
  const magic = String.fromCharCode(...bytes.slice(1, 6));
  if (bytes[0] !== 0x93 || magic !== 'NUMPY') throw new Error('not an .npy file');
  const dv = new DataView(buf);
  const major = bytes[6]!;
  const hlen = major === 1 ? dv.getUint16(8, true) : dv.getUint32(8, true);
  const start = major === 1 ? 10 : 12;
  const header = new TextDecoder('latin1').decode(bytes.slice(start, start + hlen));
  const descr = /'descr':\s*'([^']+)'/.exec(header)?.[1];
  const shapeTxt = /'shape':\s*\(([^)]*)\)/.exec(header)?.[1];
  if (!descr || shapeTxt === undefined) throw new Error('bad .npy header');
  if (/'fortran_order':\s*True/.test(header)) throw new Error('fortran-ordered .npy is not supported');
  const shape = shapeTxt.split(',').map((x) => x.trim()).filter(Boolean).map(Number);
  const size = shape.reduce((a, b) => a * b, 1);
  const off = start + hlen;
  const little = descr[0] !== '>';
  const kind = descr.slice(1);
  const out = (n: number) => new Float64Array(n);
  const read = (width: number, get: (o: number) => number | bigint, count: number) => {
    const r = out(count);
    for (let i = 0; i < count; i++) r[i] = Number(get(off + i * width));
    return r;
  };
  let data: Float64Array;
  let complex = false;
  switch (kind) {
    case 'f8': data = read(8, (o) => dv.getFloat64(o, little), size); break;
    case 'f4': data = read(4, (o) => dv.getFloat32(o, little), size); break;
    case 'i8': data = read(8, (o) => dv.getBigInt64(o, little), size); break;
    case 'u8': data = read(8, (o) => dv.getBigUint64(o, little), size); break;
    case 'i4': data = read(4, (o) => dv.getInt32(o, little), size); break;
    case 'u4': data = read(4, (o) => dv.getUint32(o, little), size); break;
    case 'i2': data = read(2, (o) => dv.getInt16(o, little), size); break;
    case 'u1':
    case 'b1': data = read(1, (o) => dv.getUint8(o), size); break;
    case 'c16': data = read(8, (o) => dv.getFloat64(o, little), size * 2); complex = true; break;
    case 'c8': data = read(4, (o) => dv.getFloat32(o, little), size * 2); complex = true; break;
    default: throw new Error(`unsupported .npy dtype ${descr}`);
  }
  return { shape, data, complex };
}
