import assert from 'node:assert/strict'
import test from 'node:test'
// @ts-ignore Node's strip-types runner intentionally uses the explicit source extension.
import { instanceColor, v3Color, validateVisualization } from './index.ts'

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
