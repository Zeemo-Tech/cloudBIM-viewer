import assert from 'node:assert/strict'
import test from 'node:test'
import { Matrix4, Vector3 } from 'three'
// @ts-ignore Node source runner.
import { buildRebarReportFigure, commonReportScales, formatReportDiameterMm, prepareRebarReportFigure, renderRebarReportFigure, reportLongitudinalProjection, reportDiameterStatistics, reportFrame, reportHookSectionFrame, reportScanToModel, reportSectionDiameter, reportSectionFrame, reportTubeSection, reportViewFrame, selectReportSections, supportedReportCurves } from './rebarReportFigure.ts'
import type { RebarFigureInput } from './rebarReportFigure'

function fixture(): RebarFigureInput {
  return {
    key: 'asymmetric/member', design: [], instanceUnits: new Map([[7, 'a'], [8, 'other']]),
    profile: Array.from({ length: 11 }, (_, i) => ({ designUnitId: 'a', stationM: i / 10,
      designCenterM: [i / 10, 0, 0], observedCenterM: null, transverseOffsetM: null, windowM: .005 })),
    fitted: [{ designUnitId: 'a', kind: 'body', evidence: 'fitted-control-net',
      centerline: [[0, .006, 0], [1, .006, 0]], supportedIntervalsM: [[0, 1]],
      pointCount: 300, radiusM: .004, radiusSource: 'design-prior' }],
    scan: [.1, .3, .5, .7, .9].flatMap(x => Array.from({ length: 12 }, (_, j) => ({ instanceId: 7,
      position: new Vector3(x, .006 + .004 * Math.cos(j * Math.PI / 6), .004 * Math.sin(j * Math.PI / 6)) }))),
  }
}

test('upright stirrup elevations preserve viewing directions and asymmetric signed engineering coordinates', () => {
  const origin = new Vector3(17, -29, 43), point = origin.clone().add(new Vector3(2, 5, -3))
  const frame = reportViewFrame(new Vector3(0, 1, 0))
  assert.deepEqual(reportLongitudinalProjection(point, origin, frame.s, frame.u, true), { x: -2000, y: 5000 })
  assert.deepEqual(reportLongitudinalProjection(point, origin, frame.s, frame.v, true), { x: -3000, y: 5000 })
  for (const transverse of [frame.u, frame.v]) {
    assert.ok(new Vector3().crossVectors(transverse.clone().negate(), frame.s).distanceTo(new Vector3().crossVectors(frame.s, transverse)) < 1e-12)
  }
  const input = fixture()
  input.sectionLayout = 'stirrup'
  input.profile = Array.from({ length: 21 }, (_, i) => ({ designUnitId: 'a', stationM: i / 20, designCenterM: [0, i / 20, 0], observedCenterM: null, transverseOffsetM: null }))
  input.fitted[0]!.centerline = [[.006, 0, 0], [.006, 1, 0]]
  input.scan.forEach(row => { const { x, y, z } = row.position; row.position.set(y, x, z) })
  const prepared = prepareRebarReportFigure(input)
  const figure = renderRebarReportFigure(prepared, undefined, 'portrait')
  assert.deepEqual(figure.longitudinal.map(p => p.title), ['正视图', '侧视图'])
  assert.ok(figure.longitudinal.every(p => p.viewBox === '0 0 250 500' && p.markup.includes('Z 向上')))
  assert.match(figure.longitudinal[0]!.markup, /视向 \+Y → −Y · −X 向右/)
  assert.match(figure.longitudinal[1]!.markup, /视向 −X → \+X · −Y 向右/)
  assert.equal(figure.sections.length, 5)
  assert.match(figure.sections[0]!.title, /250.0 mm/)
  assert.match(figure.sections[2]!.title, /750.0 mm/)
  for (const panel of figure.sections.slice(0, 3)) {
    assert.equal(panel.viewBox, '0 0 220 252')
    assert.match(panel.markup, /data-design-origin="127,126"/)
    assert.match(panel.markup, /data-section-direction="−Z → \+Z"/)
  }
  for (const panel of figure.sections.slice(3)) {
    assert.equal(panel.viewBox, '0 0 220 170')
    assert.match(panel.markup, /data-design-origin="87,85"/)
    assert.match(panel.markup, /width="100" height="100"/)
    assert.equal(panel.sectionRole, 'hook')
    assert.equal(panel.missing, true)
    assert.match(panel.markup, /截面位置未提供/)
  }
  // The compact sheet changes neither the saved diameter nor section ranges.
  const landscape = renderRebarReportFigure(prepared)
  assert.deepEqual(figure.sections.map(p => p.markup.match(/data-section-bounds="([^"]+)"/)![1]), landscape.sections.map(p => p.markup.match(/data-section-bounds="([^"]+)"/)![1]))
})

test('scan preview uses the complete saved transform exactly once and preserves input', () => {
  const alignment = new Matrix4().makeRotationZ(.73).setPosition(17, -29, 43)
  const raw = new Vector3(2.3, -4.7, 8.9), origin: [number, number, number] = [1, -3, 7]
  const preview = raw.clone().sub(new Vector3(...origin)), before = preview.clone()
  const result = reportScanToModel(preview, origin, alignment)
  assert.ok(result.distanceTo(raw.clone().applyMatrix4(alignment)) < 1e-12)
  assert.deepEqual(preview, before)
  assert.ok(Math.abs(alignment.determinant() - 1) < 1e-12)
})

test('local cross-section is right handed, orthonormal, and uses projected model-up', () => {
  for (const tangent of [new Vector3(1, 0, 0), new Vector3(0, 1, 0), new Vector3(2, 3, -5)]) {
    const { s, u, v } = reportFrame(tangent)
    assert.ok(new Vector3().crossVectors(u, v).distanceTo(s) < 1e-12)
    assert.ok(Math.abs(u.dot(v)) < 1e-12)
    assert.ok(Math.abs(u.length() - 1) < 1e-12)
  }
  assert.deepEqual(reportFrame(new Vector3(1, 0, 0)).u.toArray(), [0, 0, -1])
  assert.deepEqual(reportFrame(new Vector3(1, 0, 0)).v.toArray(), [-0, 1, 0])
})

test('hook local planes preserve asymmetric coordinates, handedness and endpoint reversal', () => {
  const tangent = new Vector3(2, -3, 5), point = new Vector3(7, 11, -13)
  const frame = reportHookSectionFrame(tangent), reverse = reportHookSectionFrame(tangent.clone().negate())
  assert.ok(Math.abs(point.dot(frame.u) - 61 / Math.sqrt(29)) < 1e-12)
  assert.ok(Math.abs(point.dot(frame.v) - 166 / Math.sqrt(1102)) < 1e-12)
  assert.ok(Math.abs(point.dot(frame.s) + 84 / Math.sqrt(38)) < 1e-12)
  assert.ok(new Vector3().crossVectors(frame.u, frame.v).distanceTo(frame.s) < 1e-12)
  assert.ok(frame.v.y > 0)
  for (const axis of ['s', 'u', 'v'] as const) assert.ok(frame[axis].distanceTo(reverse[axis]) < 1e-12)
})

test('stirrup body and terminal hooks use distinct cuts, with local-normal thickness and unit ownership', () => {
  const input = fixture()
  input.sectionLayout = 'stirrup'
  input.designRadiusM = .004
  input.profile.forEach(row => { row.designCenterM = [0, row.stationM, 0] })
  input.fitted[0]!.centerline = [[.006, 0, 0], [.006, 1, 0]]
  input.scan = []
  for (const [id, instanceId, a, b] of [
    ['lower', 9, new Vector3(.06, -.04, 0), new Vector3(.02, 0, 0)],
    ['upper', 10, new Vector3(.02, 1, 0), new Vector3(.06, 1.04, 0)],
  ] as const) {
    const tangent = b.clone().sub(a), frame = reportHookSectionFrame(tangent)
    input.instanceUnits.set(instanceId, id)
    input.profile.push(...Array.from({ length: 11 }, (_, i) => ({ designUnitId: id, stationM: tangent.length() * i / 10,
      designCenterM: a.clone().lerp(b, i / 10).toArray() as [number, number, number], observedCenterM: null, transverseOffsetM: null, windowM: .001 })))
    input.fitted.push({ designUnitId: id, kind: 'body', evidence: 'fitted-control-net',
      centerline: [a, b].map(p => p.clone().addScaledVector(frame.u, .006).toArray() as [number, number, number]),
      supportedIntervalsM: [[0, tangent.length()]], pointCount: 12, radiusM: .004, radiusSource: 'design-prior' })
    const centre = a.clone().lerp(b, .5)
    input.scan.push({ instanceId, position: centre.clone().addScaledVector(frame.u, .003) })
    input.scan.push({ instanceId, position: centre.clone().addScaledVector(frame.s, .004) })
    input.scan.push({ instanceId: 8, position: centre.clone().addScaledVector(frame.u, .05) })
  }
  const prepared = prepareRebarReportFigure(input)
  assert.deepEqual(prepared.selected.map(s => s.row.designUnitId), ['a', 'a', 'a', 'upper', 'lower'])
  for (const section of prepared.sectionData.filter(s => s.role === 'hook')) {
    assert.ok(Math.abs(section.tangent.clone().normalize().dot(section.frame.s)) > 1 - 1e-12)
    assert.equal(section.points.length, 1)
    assert.ok(Math.abs(section.fitCenter!.x - 6) < 1e-10)
    assert.ok(Math.abs(section.fitCenter!.y) < 1e-10)
    assert.ok(section.designSection.every(p => Math.abs(Math.hypot(p.x, p.y) - 4) < 1e-10))
  }
  const figure = renderRebarReportFigure(prepared, undefined, 'portrait')
  assert.deepEqual(figure.sections.map(p => p.sectionRole), ['body', 'body', 'body', 'hook', 'hook'])
  assert.ok(figure.longitudinal.every(p => (p.markup.match(/data-cut-role="hook"/g) ?? []).length === 2))
  assert.ok(figure.sections.slice(3).every(p => p.markup.includes('data-section-direction="局部法向"') && p.markup.includes('Δu / mm')))
  const reversed = { ...input, profile: input.profile.map(row => ({ ...row, stationM: Math.max(...input.profile.filter(r => r.designUnitId === row.designUnitId).map(r => r.stationM)) - row.stationM })) }
  const other = prepareRebarReportFigure(reversed)
  prepared.selected.forEach((s, i) => {
    assert.ok(s.origin.distanceTo(other.selected[i]!.origin) < 1e-12)
    assert.ok(s.frame.u.distanceTo(other.selected[i]!.frame.u) < 1e-12)
  })
})

test('five deterministic section stations retain missing observations instead of choosing good fits', () => {
  const sections = selectReportSections(fixture().profile)
  assert.deepEqual(sections.map(s => s.row.stationM), [.1, .3, .5, .7, .9])
  assert.ok(sections.every(s => s.row.observedCenterM === null))
})

test('parallel terminal returns are retained as hook slots and never populated without evidence', () => {
  const input = fixture(); input.sectionLayout = 'stirrup'; input.scan = []; input.fitted = []; input.designRadiusM = .004
  input.profile.forEach(row => { row.designCenterM = [0, row.stationM, 0] })
  for (const [id, y] of [['lower', -.04], ['upper', 1]] as const) input.profile.push(...[0, .04].map(stationM => ({
    designUnitId: id, stationM, designCenterM: [.02, y + stationM, 0] as [number, number, number], observedCenterM: null, transverseOffsetM: null,
  })))
  const prepared = prepareRebarReportFigure(input)
  assert.deepEqual(prepared.selected.slice(3).map(s => s.row.designUnitId), ['upper', 'lower'])
  assert.ok(renderRebarReportFigure(prepared, undefined, 'portrait').sections.every(p => p.missing))
})

test('oblique fitted tubes intersect a section as an ellipse with the true design radius', () => {
  const frame = reportFrame(new Vector3(1, 0, 0))
  const tangent = new Vector3(.5, Math.sqrt(.75), 0)
  const section = reportTubeSection({ x: 3, y: 5 }, 4, tangent, frame)
  assert.ok(Math.abs(Math.max(...section.map(p => p.y)) - 13) < 1e-10)
  for (const p of section) {
    const radial = frame.u.clone().multiplyScalar(p.x - 3).addScaledVector(frame.v, p.y - 5)
    radial.addScaledVector(tangent, -radial.dot(tangent))
    assert.ok(Math.abs(radial.length() - 4) < 1e-10)
  }
})

test('sections use a thin slab and instance ownership, report the saved fit offset and square scale', () => {
  const input = fixture()
  input.scan.push({ instanceId: 7, position: new Vector3(.35, .004, 0) })
  input.scan.push({ instanceId: 8, position: new Vector3(.1, .2, .2) })
  const figure = buildRebarReportFigure(input)
  assert.equal(figure.longitudinal.length, 2)
  assert.equal(figure.sections.length, 5)
  for (const panel of figure.sections) {
    assert.match(panel.markup, /n = 12/)
    assert.match(panel.markup, /Δ = 6.00 mm/)
    assert.match(panel.markup, /t = 10.0 mm/)
    assert.match(panel.markup, /width="172" height="172"/)
    assert.match(panel.markup, /data-section-annotation="inside"/)
    assert.equal((panel.markup.match(/data-layer="design-section"/g) ?? []).length, 1)
  }
  assert.match(figure.longitudinal[0]!.markup, /classified-points/)
  assert.match(figure.longitudinal[0]!.markup, /flexible-fit/)
})

test('unsupported curve intervals stay disconnected, including cross-section fitting', () => {
  const input = fixture()
  input.fitted[0]!.supportedIntervalsM = [[0, .3], [.7, 1]]
  const curves = supportedReportCurves(input.fitted)
  assert.equal(curves.length, 2)
  assert.equal(curves[0]!.points.at(-1)!.x, .3)
  assert.equal(curves[1]!.points[0]!.x, .7)
  const figure = buildRebarReportFigure(input)
  assert.match(figure.sections[2]!.markup, /该位置无可靠拟合轴线/)
  assert.doesNotMatch(figure.sections[2]!.markup, /data-layer="flexible-fit"/)
  assert.equal((figure.longitudinal[0]!.markup.match(/data-layer="flexible-fit"/g) ?? []).length, 2)
})

test('large offsets survive without centroid correction or a fabricated pass verdict', () => {
  const input = fixture()
  input.fitted[0]!.centerline = [[0, .7, 0], [1, .7, 0]]
  const figure = buildRebarReportFigure(input)
  assert.match(figure.sections[0]!.markup, /700.00 mm/)
  assert.doesNotMatch(figure.sections[0]!.markup, /≤|合格|容差内/)
  assert.doesNotMatch(figure.sections.map(p => p.markup).join(''), /NaN|Infinity/)
})

test('an unsupported section retains the design circle from the inventory without inventing a fit', () => {
  const input = fixture()
  input.fitted = []
  input.designRadiusM = .004
  const figure = buildRebarReportFigure(input)
  assert.equal(figure.sections.length, 5)
  for (const section of figure.sections) {
    assert.match(section.markup, /data-layer="design-section"/)
    assert.doesNotMatch(section.markup, /data-layer="flexible-fit"/)
    assert.match(section.markup, /无可靠拟合轴线/)
  }
})

test('empty and missing evidence remains explicit and all clip identifiers are unique', () => {
  const input = fixture(); input.fitted = []; input.scan = []
  const figure = buildRebarReportFigure(input)
  assert.match(figure.notes.join(''), /没有可用的拟合钢筋轴线/)
  assert.match(figure.notes.join(''), /没有可用的实测点云/)
  assert.equal(figure.sections.length, 5)
  for (const panel of figure.sections) {
    assert.equal(panel.missing, true)
    assert.match(panel.markup, /data-layer="missing-placeholder"/)
    assert.doesNotMatch(panel.markup, /data-layer="(?:design-section|flexible-fit|classified-points)"/)
    assert.doesNotMatch(panel.markup, /Δ = 0|Dₘ = 0/)
  }
  const ids = [...figure.sections, ...figure.longitudinal].flatMap(p => [...p.markup.matchAll(/clipPath id="([^"]+)"/g)].map(m => m[1]))
  assert.equal(new Set(ids).size, ids.length)
  const noPositions = buildRebarReportFigure({ ...input, profile: [], sectionLayout: 'stirrup' })
  assert.equal(noPositions.sections.length, 5)
  assert.ok(noPositions.sections.every(p => p.missing && p.markup.includes('截面位置未提供')))
  assert.doesNotMatch(noPositions.sections.map(p => p.markup).join(''), /NaN|Infinity|data-section-normal="0,0,0"/)
})


test('chapter main views preserve length scales while sections magnify per member without clipping extremes', () => {
  const long = fixture(), short = fixture()
  short.key = 'short'
  short.profile = short.profile.map(row => ({ ...row, stationM: row.stationM * .3, designCenterM: [row.designCenterM[0] * .3, 0, 0] }))
  short.scan.forEach(point => { point.position.x *= .3 })
  short.fitted[0]!.centerline[1]![0] = .3
  // An extreme remains visible, but must not shrink every other member's sections.
  long.scan.push({ instanceId: 7, position: new Vector3(.1, .055, -.035) })
  const scales = commonReportScales([prepareRebarReportFigure(long), prepareRebarReportFigure(short)])
  assert.ok(scales.station[1] > 1000)
  assert.ok(scales.front[1] > 55)
  const figures = [long, short].map(input => buildRebarReportFigure(input, scales))
  for (const index of [0, 1]) {
    assert.equal(figures[0]!.longitudinal[index]!.markup.match(/data-axis-range="([^"]+)"/)![1], figures[1]!.longitudinal[index]!.markup.match(/data-axis-range="([^"]+)"/)![1])
    assert.equal(figures[0]!.longitudinal[index]!.viewBox, figures[1]!.longitudinal[index]!.viewBox)
  }
  const spans = figures.map(f => f.sections.map(p => Number(p.markup.match(/data-section-span="([^"]+)"/)![1])))
  assert.ok(spans[0]![0]! > spans[1]![0]! * 2)
  for (const span of spans) assert.equal(new Set(span).size, 1)
  for (const input of [long, short]) {
    const prepared = prepareRebarReportFigure(input), b = prepared.sectionBounds
    assert.ok(Math.abs((b.xmax - b.xmin) - (b.ymax - b.ymin)) < 1e-9)
    for (const section of prepared.sectionData) for (const p of [...section.points, ...section.designSection, ...section.fitSection]) {
      assert.ok(p.x > b.xmin && p.x < b.xmax && p.y > b.ymin && p.y < b.ymax)
      // Labels occupy the less populated side; no evidence is covered by them.
      assert.ok((prepared.annotationEdge === 'top' ? p.y : -p.y) <= prepared.sectionSpan / 2 * .48 + 1e-9)
    }
  }
  const paths = figures.map(f => f.longitudinal[0]!.markup.match(/data-layer="flexible-fit" d="([^"]+)"/)![1]!)
  const lengths = paths.map(path => { const x = [...path.matchAll(/[ML]([\d.-]+),/g)].map(m => Number(m[1])); return x.at(-1)! - x[0]! })
  assert.ok(Math.abs(lengths[1]! / lengths[0]! - .3) < 1e-4)
})

test('engineering end views preserve asymmetric X/Y/Z signs and ignore endpoint order', () => {
  const point = new Vector3(2, 5, -3) // Engineering X=2, Y=3, Z=5.
  for (const [tangent, axis, expected] of [
    [new Vector3(1, 0, 0), 'X', [2, 3, 5]],
    [new Vector3(0, 0, -1), 'Y', [3, -2, 5]],
    [new Vector3(0, 1, 0), 'Z', [5, 2, 3]],
  ] as const) {
    const frame = reportViewFrame(tangent), reversed = reportViewFrame(tangent.clone().negate())
    assert.equal(frame.axis, axis)
    assert.deepEqual([point.dot(frame.s), point.dot(frame.u), point.dot(frame.v)], expected)
    assert.ok(new Vector3().crossVectors(frame.u, frame.v).distanceTo(frame.s) < 1e-12)
    for (const direction of ['s', 'u', 'v'] as const) assert.deepEqual(frame[direction], reversed[direction])
  }
  const a = fixture(), b = fixture()
  b.profile = b.profile.map(row => ({ ...row, stationM: 1 - row.stationM }))
  const forward = prepareRebarReportFigure(a), backward = prepareRebarReportFigure(b)
  assert.deepEqual(forward.origin, backward.origin)
  assert.deepEqual(forward.frame, backward.frame)
  assert.deepEqual(forward.bounds, backward.bounds)
  assert.ok(backward.selected.every((s, i, list) => i === 0 || s.origin.x >= list[i - 1]!.origin.x))
  const figure = buildRebarReportFigure(b)
  assert.ok(figure.sections.every(s => s.markup.includes('视向 −X → +X') && s.markup.includes('−ΔY / mm') && s.markup.includes('ΔZ / mm')))
})

test('vertical connectors use one Z-normal plane including oblique hooks, and correctly named elevation views', () => {
  const input = fixture()
  const vertical = (p: number[]) => [p[1]!, p[0]!, -p[2]!] as [number, number, number]
  input.profile = input.profile.map(row => ({ ...row, designCenterM: vertical(row.designCenterM) }))
  input.scan.forEach(p => p.position.set(...vertical(p.position.toArray())))
  input.fitted.forEach(s => { s.centerline = s.centerline.map(vertical) })
  input.designRadiusM = .004
  input.profile.push(...Array.from({ length: 11 }, (_, i) => ({ designUnitId: 'hook', stationM: i * .03,
    designCenterM: [.5 + i * .02, 1 + i * .02, 0] as [number, number, number], observedCenterM: null, transverseOffsetM: null })))
  input.instanceUnits.set(9, 'hook')
  input.scan.push(...input.profile.filter(row => row.designUnitId === 'hook').map(row => ({ instanceId: 9, position: new Vector3(...row.designCenterM) })))
  const prepared = prepareRebarReportFigure(input)
  assert.equal(prepared.frame.axis, 'Z')
  assert.ok(prepared.selected.every(s => s.frame.axis === 'Z'))
  const hook = prepared.sectionData.find(s => s.row.designUnitId === 'hook')!
  assert.ok(hook)
  assert.ok(Math.abs(Math.max(...hook.designSection.map(p => p.x)) - 4 * Math.sqrt(2)) < 1e-9)
  const figure = buildRebarReportFigure(input)
  assert.deepEqual(figure.longitudinal.map(p => p.title), ['正视图', '侧视图'])
  assert.ok(figure.sections.every(s => s.markup.includes('视向 −Z → +Z') && s.markup.includes('−ΔX / mm') && s.markup.includes('ΔY / mm')))
})

test('negative-to-positive section views reverse screen-right, preserve screen-up, and do not change main projections', () => {
  const point = new Vector3(2, 5, -3) // Engineering X=2, Y=3, Z=5.
  for (const [tangent, expected] of [
    [new Vector3(1, 0, 0), [-2, -3, 5]],
    [new Vector3(0, 0, -1), [-3, 2, 5]],
    [new Vector3(0, 1, 0), [-5, -2, 3]],
  ] as const) {
    const main = reportViewFrame(tangent), saved = structuredClone(main), section = reportSectionFrame(main)
    assert.deepEqual([point.dot(section.s), point.dot(section.u), point.dot(section.v)], expected)
    assert.ok(new Vector3().crossVectors(section.u, section.v).distanceTo(section.s) < 1e-12)
    assert.deepEqual(structuredClone(main), saved)
  }
})

test('the design origin stays at the plot centre across offsets, magnifications, and annotation positions', () => {
  const centres = new Set<string>(), spans = new Set<number>(), edges = new Set<string>()
  for (const offset of [-.07, -.004, .006, .08]) {
    const input = fixture()
    input.scan.forEach(p => { p.position.y += offset - .006; p.position.z += offset * .5 })
    input.fitted[0]!.centerline = [[0, offset, offset * .5], [1, offset, offset * .5]]
    const prepared = prepareRebarReportFigure(input), b = prepared.sectionBounds
    assert.equal(b.xmin + b.xmax, 0)
    assert.equal(b.ymin + b.ymax, 0)
    spans.add(prepared.sectionSpan); edges.add(prepared.annotationEdge)
    for (const section of buildRebarReportFigure(input).sections) centres.add(section.markup.match(/data-design-origin="([^"]+)"/)![1]!)
  }
  assert.deepEqual([...centres], ['127,126'])
  assert.ok(spans.size > 1)
  assert.equal(edges.size, 2)
})

test('diameter statistics double radius differences and include all accepted sections, with signed mean and absolute maximum', () => {
  const row = fixture().profile[0]!
  const profile = [.0005, -.0001, 0].map((delta, i) => ({ ...row, stationM: i * .1, quality: 'supported',
    observedCenterM: [i * .1, .006, 0] as [number, number, number], radiusM: .004 + delta, radiusDeltaM: delta }))
  profile.push({ ...profile[0]!, radiusM: .04, radiusDeltaM: .036, quality: 'unstable-section' })
  const stats = reportDiameterStatistics(profile, .004)
  assert.equal(stats.count, 3)
  assert.equal(stats.total, 4)
  assert.equal(stats.designMm, 8)
  assert.ok(Math.abs(stats.measuredMeanMm! - 8.2666666667) < 1e-9)
  assert.ok(Math.abs(stats.meanDeltaMm! - .2666666667) < 1e-9)
  assert.equal(stats.maxAbsDeltaMm, 1)
  assert.equal(formatReportDiameterMm(stats.meanDeltaMm, true), '+0.27')
  assert.equal(formatReportDiameterMm(-.00001, true), '0.00')
  const input = fixture()
  input.profile[1] = { ...profile[0]!, stationM: .1, designCenterM: [.1, 0, 0] }
  const section = buildRebarReportFigure(input).sections[0]!
  assert.match(section.markup, /D₀ = 8.00 · Dₘ = 9.00 mm/)
})

test('missing, rejected, nonfinite, and design-prior radii never become measured diameters or zero deviations', () => {
  const input = fixture(), row = { ...input.profile[0]!, quality: 'supported', observedCenterM: [0, .006, 0] as [number, number, number], radiusM: .004, radiusDeltaM: .0001 }
  for (const candidate of [
    { ...row, radiusDeltaM: null }, { ...row, radiusM: Number.NaN }, { ...row, observedCenterM: null },
    { ...row, quality: 'unstable-section' }, { ...row, radiusSource: 'design-prior' },
  ]) assert.equal(reportSectionDiameter(candidate), null)
  const stats = reportDiameterStatistics(input.profile, .004)
  assert.equal(stats.designMm, 8)
  assert.equal(stats.count, 0)
  assert.equal(stats.measuredMeanMm, null)
  assert.equal(stats.meanDeltaMm, null)
  assert.equal(stats.maxAbsDeltaMm, null)
  assert.equal(formatReportDiameterMm(stats.meanDeltaMm, true), '—')
  assert.match(buildRebarReportFigure(input).sections[0]!.markup, /D₀ = 8.00 · Dₘ = — mm/)
})
