import assert from 'node:assert/strict'
import test from 'node:test'
// @ts-ignore Node's strip-types runner intentionally uses the explicit source extension.
import { isRebarV5Result } from './result.ts'

test('V5 result guard supports producer version 2 while retaining version 1 artifacts', () => {
  const base = { algorithm: { id: 'geometric-v5', version: '1' }, analysisSchema: 'rebar-analysis-v2', visualization: { schema: 'rebar-visualization-v3' } }
  assert.equal(isRebarV5Result(base as any), true)
  assert.equal(isRebarV5Result({ ...base, algorithm: { ...base.algorithm, version: '2' } } as any), true)
  assert.equal(isRebarV5Result({ ...base, algorithm: { ...base.algorithm, version: '3' } } as any), false)
})
