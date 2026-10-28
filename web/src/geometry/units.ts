// SPDX-License-Identifier: GPL-3.0-or-later
/**
 * Unit handling. The Board JSON is SI (metres, Y up). The 2D view uses millimetres as its world
 * unit (the REST geometry endpoints also ship float32 mm), so `M_TO_MM` is the only conversion
 * between board data and deck.gl. Display units (mm or mil) are applied at formatting time only.
 */
export type Units = 'mm' | 'mil';

export const M_TO_MM = 1e3;
const MIL_M = 25.4e-6;

export function metresToDisplay(m: number, units: Units): number {
  return units === 'mm' ? m * 1e3 : m / MIL_M;
}

export function mmToDisplay(mm: number, units: Units): number {
  return metresToDisplay(mm * 1e-3, units);
}

export function formatLengthMm(mm: number, units: Units): string {
  const v = mmToDisplay(mm, units);
  const digits = units === 'mm' ? 3 : 1;
  return `${v.toFixed(digits)} ${units}`;
}

export function formatLengthM(m: number, units: Units): string {
  return formatLengthMm(m * 1e3, units);
}
