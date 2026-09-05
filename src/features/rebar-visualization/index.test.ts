import assert from 'node:assert/strict'
import test from 'node:test'
// @ts-ignore Node's strip-types runner intentionally uses the explicit source extension.
import { instanceColor, v3Color, validateVisualization, legendItems } from './index.ts'

const metadata = { schema: 'rebar-visualization-v1', defaultMode: 'rebar-class', instanceStrategy: 'golden-angle-v1', attributes: { SCENE_CLASS: {} }, values: {}, colors: { clutter: '#334155', table: '#94a3b8', noise: '#d946ef', rebar: '#ef4444', directionA: '#22d3ee', directionB: '#f97316', intersection: '#facc15' } } as const

test('validates v3 metadata and composites class colors', () => {
  assert.ok(validateVisualization(metadata))
  assert.equal(validateVisualization({ ...metadata, colors: {} }), null)
  assert.deepEqual(v3Color('rebar-class', metadata, { sceneClass: 1, flags: 0 }), [148 / 255, 163 / 255, 184 / 255])
  assert.deepEqual(v3Color('rebar-class', metadata, { sceneClass: 2, flags: 1, direction: 1 }), [250 / 255, 204 / 255, 21 / 255])
})

test('direction contrast, deterministic instances, sentinels, and v2 fallback', () => {
  assert.deepEqual(v3Color('rebar-direction', metadata, { sceneClass: 2, flags: 0, direction: 1 }), [34 / 255, 211 / 255, 238 / 255])
  assert.notDeepEqual(instanceColor(1), instanceColor(2))
  assert.deepEqual(instanceColor(7), instanceColor(7))
  assert.deepEqual(v3Color('rebar-instance', metadata, { sceneClass: 2, flags: 0, instance: 0xffffffff }), [250 / 255, 204 / 255, 21 / 255])
  assert.equal(v3Color('rebar-class', null, { sceneClass: 2, flags: 0 }), null)
})

test('v4 fixture class and known crossing instance remain distinct', () => {
  const v4 = { ...metadata, schema: 'rebar-visualization-v2', values: { sceneClass: { clutter: 0, table: 1, rebar: 2, noise: 3, fixture: 4 } }, colors: { ...metadata.colors, fixture: '#10b981' } } as const
  assert.ok(validateVisualization(v4))
  assert.deepEqual(v3Color('rebar-class', v4, { sceneClass: 4, flags: 0 }), [16/255,185/255,129/255])
  assert.deepEqual(v3Color('rebar-instance', v4, { sceneClass: 2, flags: 1, instance: 8 }), instanceColor(8))
  assert.deepEqual(v3Color('rebar-class', v4, { sceneClass: 2, flags: 2, instance: 0xffffffff }), [250/255,204/255,21/255])
  assert.ok(legendItems('rebar-class', v4).some((row) => row.label === '夹具／围挡'))
  assert.notDeepEqual(v3Color('rebar-direction', v4, { sceneClass: 2, flags: 0, direction: 3 }), v3Color('rebar-direction', v4, { sceneClass: 2, flags: 0, direction: 4 }))
})
