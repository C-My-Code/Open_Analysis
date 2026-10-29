// SPDX-License-Identifier: GPL-3.0-or-later
import { describe, expect, it } from 'vitest';
import analyses from '../../src/fixtures/analyses.json';
import { pruneConfig, simplifySchema } from '../../src/features/setup/schemaUtils';

describe('schema utils', () => {
  it('collapses pydantic nullable anyOf into an optional field', () => {
    const out = simplifySchema({
      properties: { r: { anyOf: [{ type: 'string', minLength: 1 }, { type: 'null' }], default: null, description: 'return net' } },
    }) as { properties: { r: Record<string, unknown> } };
    expect(out.properties.r).toEqual({ type: 'string', minLength: 1, description: 'return net' });
  });

  it('leaves real unions alone and keeps all four config schemas parseable', () => {
    for (const a of analyses as { config_schema: object }[]) {
      expect(JSON.stringify(simplifySchema(a.config_schema))).not.toContain('"type":"null"');
    }
    const u = simplifySchema({ anyOf: [{ type: 'string' }, { type: 'number' }] }) as { anyOf: unknown[] };
    expect(u.anyOf).toHaveLength(2);
  });

  it('prunes empty values before sending a config', () => {
    expect(pruneConfig({ a: '', b: null, c: { d: '' }, e: [1], f: 0, g: false })).toEqual({ c: {}, e: [1], f: 0, g: false });
  });
});
