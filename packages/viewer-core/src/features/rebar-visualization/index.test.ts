import assert from 'node:assert/strict'
import test from 'node:test'
// @ts-ignore Node's strip-types runner intentionally uses the explicit source extension.
import { createRebarColorizer, instanceColor, v3Color, validateVisualization, legendItems, isRebarPointVisible, rebarPointVisibilityCategory, rebarSemanticColor, visibleRebarPointIndices } from './index.ts'

const metadata = { schema: 'rebar-visualization-v1', defaultMode: 'rebar-class', instanceStrategy: 'golden-angle-v1', attributes: { SCENE_CLASS: {} }, values: {}, colors: { clutter: '#334155', table: '#94a3b8', noise: '#d946ef', rebar: '#ef4444', directionA: '#22d3ee', directionB: '#f97316', intersection: '#facc15' } } as const

test('spatial instance palette overrides instance colors while preserving semantic and legacy fallback colors', () => {
  const palette = new Map<number, [number, number, number]>([[35, [0.2, 0.8, 0.9]]])
  const colorize = createRebarColorizer(metadata, palette)
  const legacy = createRebarColorizer(metadata)
  const point = { sceneClass: 2, flags: 0, direction: 1, instance: 35 }
  assert.deepEqual(colorize('rebar-instance', point), palette.get(35))
  assert.deepEqual(colorize('rebar-class', point), legacy('rebar-class', point))
  assert.deepEqual(colorize('rebar-direction', point), legacy('rebar-direction', point))
  assert.deepEqual(colorize('rebar-instance', { ...point, instance: 99 }), instanceColor(99))
  assert.deepEqual(colorize('rebar-instance', { ...point, flags: 1 }), legacy('rebar-instance', { ...point, flags: 1 }))
})

test('validates v3 metadata and composites class colors', () => {
  assert.ok(validateVisualization(metadata))
  assert.equal(validateVisualization({ ...metadata, colors: {} }), null)
  assert.deepEqual(v3Color('rebar-class', metadata, { sceneClass: 1, flags: 0 }), [148 / 255, 163 / 255, 184 / 255])
  assert.deepEqual(v3Color('rebar-class', metadata, { sceneClass: 2, flags: 1, direction: 1 }), [250 / 255, 204 / 255, 21 / 255])
})

test('direction contrast, deterministic instances, sentinels, and v2 fallback', () => {
  assert.deepEqual(v3Color('rebar-direction', metadata, { sceneClass: 2, flags: 0, direction: 1 }), [34 / 255, 211 / 255, 238 / 255])
  const inactive = [82 / 255, 82 / 255, 82 / 255]
  assert.deepEqual(v3Color('rebar-direction', metadata, { sceneClass: 3, flags: 0, direction: 0 }), inactive)
  assert.deepEqual(v3Color('rebar-direction', metadata, { sceneClass: 0, flags: 0, direction: 0 }), inactive)
  assert.deepEqual(v3Color('rebar-instance', metadata, { sceneClass: 1, flags: 0, instance: 0 }), inactive)
  assert.notDeepEqual(instanceColor(1), instanceColor(2))
  assert.deepEqual(instanceColor(7), instanceColor(7))
  assert.deepEqual(v3Color('rebar-instance', metadata, { sceneClass: 2, flags: 0, instance: 0xffffffff }), [250 / 255, 204 / 255, 21 / 255])
  assert.equal(v3Color('rebar-class', null, { sceneClass: 2, flags: 0 }), null)
})

test('mode switching does not change the final direction colors', () => {
  const points = [
    { sceneClass: 2, flags: 0, direction: 1, instance: 3 },
    { sceneClass: 2, flags: 0, direction: 2, instance: 4 },
    { sceneClass: 3, flags: 0, direction: 0, instance: 0 },
  ]
  const colorize = createRebarColorizer(metadata)
  const expected = points.map((point) => colorize('rebar-direction', point))
  points.forEach((point) => colorize('rebar-class', point))
  points.forEach((point) => colorize('rebar-instance', point))
  points.forEach((point) => colorize('rebar-direction', point))
  points.forEach((point) => colorize('rebar-class', point))
  assert.deepEqual(points.map((point) => colorize('rebar-direction', point)), expected)
})

test('v4 fixture class and crossing markers remain distinct', () => {
  const v4 = { ...metadata, schema: 'rebar-visualization-v2', values: { sceneClass: { clutter: 0, table: 1, rebar: 2, noise: 3, fixture: 4 } }, colors: { ...metadata.colors, fixture: '#10b981' } } as const
  assert.ok(validateVisualization(v4))
  assert.deepEqual(v3Color('rebar-class', v4, { sceneClass: 4, flags: 0 }), [16/255,185/255,129/255])
  assert.deepEqual(v3Color('rebar-instance', v4, { sceneClass: 2, flags: 1, instance: 8 }), [250/255,204/255,21/255])
  assert.deepEqual(v3Color('rebar-class', v4, { sceneClass: 2, flags: 2, instance: 0xffffffff }), [239/255,68/255,68/255])
  assert.ok(legendItems('rebar-class', v4).some((row) => row.label === '夹具／围挡'))
  const aliases = { ...v4, values: { sceneClass: { ...v4.values.sceneClass, fixture_formwork: 4 } } }
  assert.equal(legendItems('rebar-class', aliases).filter((row) => row.label === '夹具／围挡').length, 1)
  assert.deepEqual(v3Color('rebar-direction', v4, { sceneClass: 2, flags: 0, direction: 1 }), [34 / 255, 211 / 255, 238 / 255])
  assert.deepEqual(v3Color('rebar-direction', v4, { sceneClass: 2, flags: 0, direction: 2 }), [249 / 255, 115 / 255, 22 / 255])
  assert.deepEqual(v3Color('rebar-direction', v4, { sceneClass: 3, flags: 0, direction: 0 }), [82 / 255, 82 / 255, 82 / 255])
  assert.notDeepEqual(v3Color('rebar-direction', v4, { sceneClass: 2, flags: 0, direction: 3 }), v3Color('rebar-direction', v4, { sceneClass: 2, flags: 0, direction: 4 }))
  const directionLegend = legendItems('rebar-direction', v4, { directionCount: 4 })
  assert.deepEqual(directionLegend.slice(0, 4).map((row) => row.color), [
    [34 / 255, 211 / 255, 238 / 255],
    [249 / 255, 115 / 255, 22 / 255],
    instanceColor(3),
    instanceColor(4),
  ])
  assert.deepEqual(directionLegend.slice(0, 4).map((row) => row.label), ['方向 A', '方向 B', '方向 3', '方向 4'])
  assert.ok(directionLegend.some((row) => row.label === '非钢筋'))
  assert.deepEqual(
    legendItems('rebar-direction', v4, { directionCount: 1 }).filter((row) => row.label.startsWith('方向')).map((row) => row.label),
    ['方向 A'],
  )
  assert.equal(legendItems('rebar-direction', v4, { directionCount: 0 }).some((row) => row.label.startsWith('方向')), false)
})

test('v2 flags keep crossings and ownership ambiguity distinct with crossing priority', () => {
  const v2 = {
    ...metadata,
    schema: 'rebar-visualization-v2',
    values: { sceneClass: { clutter: 0, table: 1, rebar: 2, noise: 3, fixture: 4 } },
    colors: { ...metadata.colors, fixture: '#10b981', ambiguity: '#a855f7' },
  } as const
  const crossing = [250 / 255, 204 / 255, 21 / 255]
  const ambiguity = [168 / 255, 85 / 255, 247 / 255]

  assert.deepEqual(v3Color('rebar-instance', v2, { sceneClass: 2, flags: 1, instance: 7 }), crossing)
  assert.deepEqual(v3Color('rebar-direction', v2, { sceneClass: 2, flags: 0, direction: 65535 }), crossing)
  assert.deepEqual(v3Color('rebar-instance', v2, { sceneClass: 2, flags: 2, instance: 0xffffffff }), ambiguity)
  assert.deepEqual(v3Color('rebar-instance', v2, { sceneClass: 2, flags: 3, instance: 7 }), crossing)
  assert.deepEqual(v3Color('rebar-class', v2, { sceneClass: 2, flags: 2, instance: 0xffffffff }), [239 / 255, 68 / 255, 68 / 255])

  const labels = legendItems('rebar-instance', v2).map((item) => item.label)
  assert.ok(labels.includes('交点'))
  assert.ok(labels.includes('归属待确认'))
  assert.ok(!legendItems('rebar-class', v2).some((item) => item.label === '归属待确认'))
})

test('V5 has no point-intersection color and reserves bit 1 for ownership ambiguity', () => {
  const v5 = {
    schema: 'rebar-visualization-v3', defaultMode: 'rebar-class', instanceStrategy: 'golden-angle-v1',
    attributes: { REBAR_CLASS: { componentType: 'UNSIGNED_BYTE' }, REBAR_INSTANCE: { componentType: 'UNSIGNED_INT' }, REBAR_FLAGS: { componentType: 'UNSIGNED_BYTE' }, CLASS_CONFIDENCE: { componentType: 'FLOAT' }, INSTANCE_CONFIDENCE: { componentType: 'FLOAT' } },
    values: { sceneClass: { unknown: 0, table: 1, rebar: 2, noise: 3, fixture: 4 }, rebarClass: { nonRebar: 0, rebar: 1 }, rebarFlags: { instanceAmbiguity: 2 } },
    colors: { unknown: '#334155', table: '#94a3b8', rebar: '#ef4444', noise: '#d946ef', fixture: '#10b981', directionA: '#22d3ee', directionB: '#f97316', ambiguity: '#a855f7' },
  } as const
  assert.ok(validateVisualization(v5))
  assert.deepEqual(v3Color('rebar-instance', v5, { sceneClass: 2, flags: 2, instance: 0 }), [168 / 255, 85 / 255, 247 / 255])
  assert.notDeepEqual(v3Color('rebar-class', v5, { sceneClass: 2, flags: 1, instance: 7 }), [250 / 255, 204 / 255, 21 / 255])
  assert.ok(!legendItems('rebar-class', v5).some((item) => /交点|交叉/.test(item.label)))
})

test('point visibility covers each V5 subtype and uses broad legacy fallbacks', () => {
  assert.equal(rebarPointVisibilityCategory({ sceneClass: 4, fixtureKind: 1 }), 'fixtureSquareTube')
  assert.equal(rebarPointVisibilityCategory({ sceneClass: 4, fixtureKind: 2 }), 'fixturePlate')
  assert.equal(rebarPointVisibilityCategory({ sceneClass: 4, fixtureKind: 3 }), 'fixtureBolt')
  assert.equal(rebarPointVisibilityCategory({ sceneClass: 4 }), 'fixtureUnknown')
  assert.equal(rebarPointVisibilityCategory({ sceneClass: 2, rebarRole: 1 }), 'rebarPlanar')
  assert.equal(rebarPointVisibilityCategory({ sceneClass: 2, rebarRole: 2 }), 'rebarWeb')
  assert.equal(rebarPointVisibilityCategory({ sceneClass: 2 }), 'rebarUnresolved')
  assert.equal(isRebarPointVisible({ sceneClass: 4, fixtureKind: 1 }, { fixtureSquareTube: false }), false)
  assert.equal(isRebarPointVisible({ sceneClass: 4 }, { fixtureSquareTube: false }), true)
  assert.equal(isRebarPointVisible({ sceneClass: 2 }, { rebarUnresolved: false }), false)
  assert.equal(isRebarPointVisible({ sceneClass: 0 }, { unknown: false }), false)
})

test('V5 scene mode colors steel roles independently', () => {
  const v5 = { schema: 'rebar-visualization-v3', defaultMode: 'rebar-class', instanceStrategy: 'golden-angle-v1', attributes: {}, values: { sceneClass: { unknown: 0, table: 1, rebar: 2, noise: 3, fixture: 4 }, rebarRole: { unresolved: 0, planar: 1, web: 2 } }, colors: { unknown: '#334155', table: '#94a3b8', rebar: '#ef4444', noise: '#d946ef', fixture: '#10b981', directionA: '#22d3ee', directionB: '#f97316' } } as const
  assert.deepEqual(v3Color('rebar-class', v5, { sceneClass: 2, flags: 0, rebarRole: 1 }), [45 / 255, 212 / 255, 191 / 255])
  assert.deepEqual(v3Color('rebar-class', v5, { sceneClass: 2, flags: 0, rebarRole: 2 }), [251 / 255, 146 / 255, 60 / 255])
})

test('legacy V5 visualization keeps its original steel color without role contract', () => {
  const legacy = { schema: 'rebar-visualization-v3', defaultMode: 'rebar-class', instanceStrategy: 'golden-angle-v1', attributes: {}, values: { sceneClass: { unknown: 0, table: 1, rebar: 2, noise: 3, fixture: 4 } }, colors: { unknown: '#334155', table: '#94a3b8', rebar: '#ef4444', noise: '#d946ef', fixture: '#10b981', directionA: '#22d3ee', directionB: '#f97316' } } as const
  assert.deepEqual(v3Color('rebar-class', legacy, { sceneClass: 2, flags: 0 }), [239 / 255, 68 / 255, 68 / 255])
})

test('visibility index has an all-visible fast path and restores every source index', () => {
  const points = [{ sceneClass: 0 }, { sceneClass: 4, fixtureKind: 1 }, { sceneClass: 2, rebarRole: 2 }, { sceneClass: 3 }]
  let calls = 0
  assert.equal(visibleRebarPointIndices(points.length, index => { calls += 1; return points[index] }, {}), null)
  assert.equal(calls, 0)
  assert.deepEqual(visibleRebarPointIndices(points.length, index => points[index], { fixtureSquareTube: false, rebarWeb: false }), [0, 3])
  assert.equal(visibleRebarPointIndices(points.length, index => points[index], {}), null)
})

test('semantic subtype colors and legends use producer colors with correct fixture and steel labels', () => {
  const v5 = { schema: 'rebar-visualization-v3', defaultMode: 'rebar-class', instanceStrategy: 'golden-angle-v1', attributes: {}, values: { sceneClass: { unknown: 0, table: 1, rebar: 2, noise: 3, fixture: 4 }, fixtureKind: { unknown: 0, squareTube: 1, plate: 2, bolt: 3 }, rebarRole: { unresolved: 0, planar: 1, web: 2 } }, colors: { unknown: '#334155', table: '#94a3b8', rebar: '#ef4444', noise: '#d946ef', fixture: '#11aa77', squareTube: '#60a5fa', plate: '#fbbf24', bolt: '#f472b6', unresolved: '#a855f7', planar: '#2dd4bf', web: '#fb923c', directionA: '#22d3ee', directionB: '#f97316' } } as const
  assert.equal(rebarSemanticColor(v5, 'fixtureUnknown'), '#11aa77')
  assert.equal(rebarSemanticColor(v5, 'fixtureSquareTube'), '#60a5fa')
  assert.equal(rebarSemanticColor(v5, 'rebarPlanar'), '#2dd4bf')
  const legend = legendItems('rebar-class', v5)
  assert.ok(legend.some((item) => item.label === '夹具·夹持板' && item.color[0] === 251 / 255))
  assert.ok(legend.some((item) => item.label === '钢筋·斜腹杆'))
})
