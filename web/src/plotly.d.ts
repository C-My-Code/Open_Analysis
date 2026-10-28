// SPDX-License-Identifier: GPL-3.0-or-later
declare module 'plotly.js-basic-dist-min' {
  export function react(
    el: HTMLElement,
    data: Record<string, unknown>[],
    layout?: Record<string, unknown>,
    config?: Record<string, unknown>,
  ): Promise<unknown>;
}
