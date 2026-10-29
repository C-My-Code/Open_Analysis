// SPDX-License-Identifier: GPL-3.0-or-later
import type { UiSchema } from '@rjsf/utils';

/** Analyses the setup editor offers (config schemas come from `GET /analyses`). */
export const SETUP_KINDS = ['impedance.xsection', 'impedance.profile', 'erc', 'pi.dc'] as const;

type Json = Record<string, unknown>;

/**
 * Pydantic emits `anyOf: [X, {type: null}]` and `default: null` for optional fields. RJSF would render
 * a type picker for those; collapse them to X so the field is simply optional (empty = omitted).
 */
export function simplifySchema<T>(node: T): T {
  if (Array.isArray(node)) return node.map((n) => simplifySchema(n)) as T;
  if (!node || typeof node !== 'object') return node;
  const o = { ...(node as Json) };
  const any = o.anyOf;
  if (Array.isArray(any) && any.length === 2) {
    const nonNull = any.filter((a) => (a as Json).type !== 'null');
    if (nonNull.length === 1) {
      delete o.anyOf;
      Object.assign(o, simplifySchema(nonNull[0]));
      if (o.default === null) delete o.default;
    }
  }
  if (o.default === null) delete o.default;
  for (const k of Object.keys(o)) {
    if (k !== 'default' && k !== 'examples') o[k] = simplifySchema(o[k]);
  }
  return o as T;
}

/** Removes empty strings / nulls / empty objects so the server sees only what the user set. */
export function pruneConfig(v: unknown): unknown {
  if (Array.isArray(v)) return v.map(pruneConfig);
  if (v && typeof v === 'object') {
    const out: Json = {};
    for (const [k, x] of Object.entries(v as Json)) {
      const p = pruneConfig(x);
      if (p === undefined || p === null || p === '') continue;
      out[k] = p;
    }
    return out;
  }
  return v;
}

export function uiSchemaFor(kind: string): UiSchema {
  switch (kind) {
    case 'erc':
      return { rule_config: { 'ui:widget': 'hidden' }, severity: { 'ui:widget': 'hidden' }, waivers: { items: { 'ui:order': ['fingerprint', 'rule_id', 'nets', 'reason', '*'] } } };
    case 'impedance.profile':
      return { nets: { names: { items: { 'ui:widget': 'netName' } }, exclude: { items: { 'ui:widget': 'netName' } } } };
    case 'pi.dc':
      return { rail: { net: { 'ui:widget': 'netName' }, return: { 'ui:widget': 'netName' } } };
    default:
      return {};
  }
}

const cache = new WeakMap<object, unknown>();
/** `simplifySchema` memoised per schema object, so RJSF sees a stable schema between renders. */
export function simplifyCached(schema: object): unknown {
  let r = cache.get(schema);
  if (!r) { r = simplifySchema(schema); cache.set(schema, r); }
  return r;
}
