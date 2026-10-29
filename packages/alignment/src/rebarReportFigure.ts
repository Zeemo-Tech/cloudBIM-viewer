import { Matrix4, Vector3 } from 'three'
import type { C2MRebarInspection, RebarProfileSample } from '@cloudbim/viewer-core'

type Vec3 = [number, number, number]
type Point2 = { x: number; y: number }
export type ReportMesh = { vertices: Vector3[]; indices: number[] }
export type ReportScanPoint = { position: Vector3; instanceId: number }
type ObservedSegment = C2MRebarInspection['bars'][number]['observedSegments'][number]
export interface RebarFigureInput {
  key: string
  design: ReportMesh[]
  scan: ReportScanPoint[]
  profile: RebarProfileSample[]
  fitted: ObservedSegment[]
  designRadiusM?: number
  instanceUnits: Map<number, string>
  sectionLayout?: 'stirrup'
}
export interface RebarFigurePanel { id: string; title: string; viewBox: string; markup: string; sectionRole?: 'body' | 'hook'; missing?: boolean }
export interface RebarFigure { longitudinal: RebarFigurePanel[]; sections: RebarFigurePanel[]; notes: string[]; locator?: Pick<RebarFigurePanel, 'viewBox' | 'markup'> }
export interface ReportFigureScales {
  station: [number, number]
  front: [number, number]
  top: [number, number]
}

const BLUE = '#246493', ORANGE = '#bc6024', INK = '#263238', MUTED = '#53636c'
const number = (value: number) => value.toFixed(2)
const escape = (value: string) => value.replace(/[&<>"']/g, ch => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[ch]!)
const finite = (point: Vector3) => point.toArray().every(Number.isFinite)
const vector = (point: Vec3) => new Vector3(...point)

/** preview.ply = raw scan - previewOrigin; saved A maps raw scan to raw GLB.
 * No scene centring, camera rotation, or centroid alignment belongs here. */
export function reportScanToModel(point: Vector3, previewOrigin: Vec3, alignment: Matrix4) {
  return point.clone().add(vector(previewOrigin)).applyMatrix4(alignment)
}

export function reportFrame(tangent: Vector3) {
  const s = tangent.clone().normalize()
  const helper = Math.abs(s.y) < .95 ? new Vector3(0, 1, 0) : new Vector3(0, 0, 1)
  const u = new Vector3().crossVectors(helper, s).normalize()
  const v = new Vector3().crossVectors(s, u).normalize()
  return { s, u, v } // u × v = s, right handed; v is projected model-up.
}

/** Engineering (X,Y,Z) = raw GLB (x,-z,y). Canonical positive axes make
 * projections independent of design-unit endpoint order. Vertical members
 * use a horizontal section, never a falsely labelled X/Y end view. */
export function reportViewFrame(tangent: Vector3) {
  const axes = [
    { axis: 'X', s: new Vector3(1, 0, 0), u: new Vector3(0, 0, -1), v: new Vector3(0, 1, 0), uLabel: 'Y', vLabel: 'Z' },
    { axis: 'Y', s: new Vector3(0, 0, -1), u: new Vector3(-1, 0, 0), v: new Vector3(0, 1, 0), uLabel: '−X', vLabel: 'Z' },
    { axis: 'Z', s: new Vector3(0, 1, 0), u: new Vector3(1, 0, 0), v: new Vector3(0, 0, -1), uLabel: 'X', vLabel: 'Y' },
  ]
  return axes.reduce((best, frame) => Math.abs(tangent.dot(frame.s)) > Math.abs(tangent.dot(best.s)) + 1e-9 ? frame : best)
}

/** Camera on the negative main axis, looking toward the positive axis.
 * Preserve screen-up and reverse screen-right; u × v points to the camera. */
export function reportSectionFrame(frame: ReturnType<typeof reportViewFrame>) {
  return { ...frame, s: frame.s.clone().negate(), u: frame.u.clone().negate(), v: frame.v.clone(),
    uLabel: frame.uLabel.startsWith('−') ? frame.uLabel.slice(1) : `−${frame.uLabel}` }
}

/** Only an accepted, independently measured radius with a saved delta is a
 * diameter observation. A design-prior tube radius is never a measurement. */
export function reportSectionDiameter(row: RebarProfileSample) {
  const source = row as RebarProfileSample & { radiusSource?: string }
  const radius = row.radiusM, delta = row.radiusDeltaM
  if (row.quality !== 'supported' || source.radiusSource === 'design-prior' ||
      !row.observedCenterM || !row.observedCenterM.every(Number.isFinite) ||
      radius == null || !Number.isFinite(radius) || radius <= 0 ||
      delta == null || !Number.isFinite(delta) || radius - delta <= 0) return null
  return { designMm: (radius - delta) * 2000, measuredMm: radius * 2000, deltaMm: delta * 2000 }
}

/** Equal weight per valid saved section, across the entire member, not merely
 * the five illustrated sections. Keep a signed mean and an absolute maximum. */
export function reportDiameterStatistics(profile: RebarProfileSample[], designRadiusM?: number) {
  const measured = profile.flatMap(row => { const diameter = reportSectionDiameter(row); return diameter ? [diameter] : [] })
  const mean = (values: number[]) => values.length ? values.reduce((sum, value) => sum + value, 0) / values.length : null
  return {
    designMm: designRadiusM != null && Number.isFinite(designRadiusM) && designRadiusM > 0 ? designRadiusM * 2000 : mean(measured.map(row => row.designMm)),
    measuredMeanMm: mean(measured.map(row => row.measuredMm)), meanDeltaMm: mean(measured.map(row => row.deltaMm)),
    maxAbsDeltaMm: measured.length ? Math.max(...measured.map(row => Math.abs(row.deltaMm))) : null,
    count: measured.length, total: profile.length,
  }
}

export function formatReportDiameterMm(value: number | null | undefined, signed = false) {
  if (value == null || !Number.isFinite(value)) return '—'
  const rounded = Math.round(value * 100) / 100
  return `${signed && rounded > 0 ? '+' : ''}${rounded.toFixed(2)}`
}

function longitudinalViews(frame: ReturnType<typeof reportViewFrame>) {
  return frame.axis === 'Z'
    ? [{ id: 'front' as const, title: '正视图', transverse: frame.u, label: 'X', from: '+Y → −Y' },
      { id: 'top' as const, title: '侧视图', transverse: frame.v, label: 'Y', from: '−X → +X' }]
    : [{ id: 'front' as const, title: '正视图', transverse: frame.v, label: frame.vLabel, from: frame.axis === 'X' ? '−Y → +Y' : '+X → −X' },
      { id: 'top' as const, title: '俯视图', transverse: frame.u, label: frame.uLabel, from: '+Z → −Z' }]
}

/** Intersection of a circular tube with the requested section plane.
 * A tilted design or fitted axis yields an ellipse; its radius is unchanged. */
export function reportTubeSection(center: Point2, radius: number, tangent: Vector3, frame: ReturnType<typeof reportFrame>): Point2[] {
  const t = tangent.clone().normalize(), cosine = Math.abs(t.dot(frame.s))
  if (cosine < 1e-6) return []
  const du = t.dot(frame.u), dv = t.dot(frame.v), length = Math.hypot(du, dv)
  const ux = length > 1e-9 ? du / length : 1, uy = length > 1e-9 ? dv / length : 0
  return Array.from({ length: 97 }, (_, i) => {
    const angle = i / 96 * 2 * Math.PI, a = radius / cosine * Math.cos(angle), b = radius * Math.sin(angle)
    return { x: center.x + ux * a - uy * b, y: center.y + uy * a + ux * b }
  })
}

function principalAxis(points: Vector3[]) {
  const center = points.reduce((sum, p) => sum.add(p), new Vector3()).divideScalar(Math.max(1, points.length))
  const multiply = (axis: Vector3) => points.reduce((sum, point) => {
    const d = point.clone().sub(center)
    return sum.addScaledVector(d, d.dot(axis))
  }, new Vector3())
  let best = new Vector3(1, 0, 0), score = -1
  for (const seed of [new Vector3(1, 0, 0), new Vector3(0, 1, 0), new Vector3(0, 0, 1)]) {
    let axis = seed
    for (let i = 0; i < 24; i++) {
      const next = multiply(axis)
      if (next.lengthSq() < 1e-24) break
      axis = next.normalize()
    }
    const variance = axis.dot(multiply(axis))
    if (variance > score) { best = axis; score = variance }
  }
  const dominant = best.toArray().reduce((a, b) => Math.abs(a) > Math.abs(b) ? a : b)
  return dominant < 0 ? best.negate() : best
}

function profileUnits(profile: RebarProfileSample[]) {
  const groups = new Map<string, RebarProfileSample[]>()
  for (const row of profile) {
    if (!finite(vector(row.designCenterM)) || !Number.isFinite(row.stationM)) continue
    const rows = groups.get(row.designUnitId) ?? []
    rows.push(row); groups.set(row.designUnitId, rows)
  }
  return [...groups.values()].map(rows => rows.sort((a, b) => a.stationM - b.stationM))
}

/** Fixed positions along design units; never select only the best-supported sections. */
export function selectReportSections(profile: RebarProfileSample[], viewFrame?: ReturnType<typeof reportViewFrame>) {
  const units = profileUnits(profile).filter(rows => rows.length >= 2)
  let length = 0
  const candidates = units.flatMap(rows => {
    const start = length
    length += rows.at(-1)!.stationM - rows[0]!.stationM
    return rows.map((row, index) => ({ row, rows, index, at: start + row.stationM - rows[0]!.stationM }))
  })
  const selected = new Set<RebarProfileSample>()
  return [.1, .3, .5, .7, .9].flatMap(fraction => {
    const candidate = candidates.filter(c => !selected.has(c.row)).sort((a, b) => Math.abs(a.at - fraction * length) - Math.abs(b.at - fraction * length))[0]
    if (!candidate) return []
    selected.add(candidate.row)
    const before = candidate.rows[Math.max(0, candidate.index - 1)]!
    const after = candidate.rows[Math.min(candidate.rows.length - 1, candidate.index + 1)]!
    const tangent = vector(after.designCenterM).sub(vector(before.designCenterM))
    if (tangent.lengthSq() < 1e-18) return []
    return [{ ...candidate, tangent, frame: reportSectionFrame(viewFrame ?? reportViewFrame(tangent)), origin: vector(candidate.row.designCenterM) }]
  }).sort((a, b) => viewFrame ? a.origin.dot(viewFrame.s) - b.origin.dot(viewFrame.s) || a.at - b.at : a.at - b.at)
}

type ReportSectionSelection = ReturnType<typeof selectReportSections>[number] & { slot: number; role: 'body' | 'hook'; title?: string }

/** A hook cut is normal to its actual local design tangent. Canonicalise the
 * tangent before choosing the negative-side observer so endpoint reversal
 * cannot mirror the image; u × v points at that observer, v projects model-up. */
export function reportHookSectionFrame(tangent: Vector3) {
  const along = tangent.clone().normalize()
  if (along.dot(reportViewFrame(along).s) < 0) along.negate()
  return { ...reportFrame(along.negate()), axis: 'local', uLabel: 'u', vLabel: 'v' }
}

/** Three body cuts (25/50/75%) and the midpoint of each terminal hook unit.
 * Saved design geometry chooses locations, never observation quality. Missing
 * units leave their reserved slot empty; they are not replaced by body cuts. */
export function selectStirrupReportSections(profile: RebarProfileSample[], viewFrame: ReturnType<typeof reportViewFrame>): ReportSectionSelection[] {
  const units = profileUnits(profile).filter(rows => rows.length >= 2)
  const main = [...units].sort((a, b) => b.at(-1)!.stationM - b[0]!.stationM - (a.at(-1)!.stationM - a[0]!.stationM))[0]
  if (!main) return []
  const height = (row: RebarProfileSample) => vector(row.designCenterM).dot(viewFrame.s)
  const ordered = [...main].sort((a, b) => height(a) - height(b))
  const used = new Set<RebarProfileSample>(), result: ReportSectionSelection[] = []
  const add = (rows: RebarProfileSample[], target: number, coordinate: (row: RebarProfileSample) => number, slot: number, role: 'body' | 'hook', title?: string) => {
    const row = rows.filter(r => !used.has(r)).sort((a, b) => Math.abs(coordinate(a) - target) - Math.abs(coordinate(b) - target) || height(a) - height(b))[0]
    if (!row) return
    const index = rows.indexOf(row), tangent = vector(rows[Math.min(index + 1, rows.length - 1)]!.designCenterM).sub(vector(rows[Math.max(0, index - 1)]!.designCenterM))
    if (tangent.lengthSq() < 1e-18) return
    used.add(row)
    result.push({ row, rows, index, at: row.stationM, tangent, origin: vector(row.designCenterM),
      frame: role === 'hook' ? reportHookSectionFrame(tangent) : reportSectionFrame(viewFrame), slot, role, title })
  }
  const low = height(ordered[0]!), high = height(ordered.at(-1)!)
  for (const [slot, fraction] of [.25, .5, .75].entries()) add(main, low + (high - low) * fraction, height, slot, 'body')
  const centre = (rows: RebarProfileSample[]) => (height(rows[0]!) + height(rows.at(-1)!)) / 2
  // A 180-degree return can be parallel to the body; do not reject a terminal
  // unit based on its angle. Its side is determined from the saved geometry.
  const hooks = units.filter(rows => rows !== main)
  const upper = hooks.filter(rows => centre(rows) > (low + high) / 2).sort((a, b) => centre(b) - centre(a))[0]
  const lower = hooks.filter(rows => centre(rows) <= (low + high) / 2).sort((a, b) => centre(a) - centre(b))[0]
  if (upper) add(upper, (upper[0]!.stationM + upper.at(-1)!.stationM) / 2, row => row.stationM, 3, 'hook', viewFrame.axis === 'Z' ? '上端弯钩截面' : '末端弯钩截面')
  if (lower) add(lower, (lower[0]!.stationM + lower.at(-1)!.stationM) / 2, row => row.stationM, 4, 'hook', viewFrame.axis === 'Z' ? '下端弯钩截面' : '起端弯钩截面')
  return result
}

/** Respect separate pieces and each piece's supported arc-length intervals. */
export function supportedReportCurves(segments: ObservedSegment[]) {
  return segments.flatMap(segment => {
    const points = segment.centerline.map(vector)
    if (points.length < 2 || !points.every(finite)) return []
    const stations = [0]
    for (let i = 1; i < points.length; i++) stations.push(stations[i - 1]! + points[i]!.distanceTo(points[i - 1]!))
    return segment.supportedIntervalsM.flatMap(([lo, hi]) => {
      if (!Number.isFinite(lo) || !Number.isFinite(hi) || hi <= lo) return []
      const curve: Vector3[] = []
      for (let i = 1; i < points.length; i++) {
        const a = stations[i - 1]!, b = stations[i]!, left = Math.max(a, lo), right = Math.min(b, hi)
        if (right <= left || b <= a) continue
        const p = points[i - 1]!, q = points[i]!
        if (!curve.length) curve.push(p.clone().lerp(q, (left - a) / (b - a)))
        curve.push(p.clone().lerp(q, (right - a) / (b - a)))
      }
      return curve.length >= 2 ? [{ ...segment, points: curve }] : []
    })
  })
}

function ticks(min: number, max: number, count = 4) {
  const rough = (max - min) / count, power = 10 ** Math.floor(Math.log10(rough))
  const step = ([1, 2, 2.5, 5, 10].find(n => n * power >= rough) ?? 10) * power
  const values = []
  for (let value = Math.ceil(min / step) * step; value <= max + step * 1e-6; value += step) values.push(Math.abs(value) < step * 1e-8 ? 0 : value)
  return values
}

function extent(points: Point2[]) {
  let xmin = Infinity, xmax = -Infinity, ymin = Infinity, ymax = -Infinity
  for (const p of points) { xmin = Math.min(xmin, p.x); xmax = Math.max(xmax, p.x); ymin = Math.min(ymin, p.y); ymax = Math.max(ymax, p.y) }
  return { xmin, xmax, ymin, ymax }
}

function plot(id: string, bounds: ReturnType<typeof extent>, width: number, height: number, square = false, compact = false, vertical = false) {
  const left = compact ? 37 : square ? 41 : 49, right = compact ? 137 : width - (square ? 7 : 13)
  const top = compact ? 35 : square ? 40 : vertical ? 48 : 28, bottom = square ? top + right - left : height - 35
  const x = (v: number) => left + (v - bounds.xmin) / (bounds.xmax - bounds.xmin) * (right - left)
  const y = (v: number) => bottom - (v - bounds.ymin) / (bounds.ymax - bounds.ymin) * (bottom - top)
  const clipId = `report-${id.replace(/[^a-zA-Z0-9_-]/g, '_')}`
  const axes = (xlabel: string, ylabel: string) => {
    const label = (v: number) => String(Number(v.toFixed(2)))
    return `<path d="M${left},${top}V${bottom}H${right}" fill="none" stroke="${INK}" stroke-width="1"/>` +
      ticks(bounds.xmin, bounds.xmax).map(v => `<path d="M${number(x(v))},${bottom}v4" stroke="${INK}"/><text x="${number(x(v))}" y="${bottom + 16}" text-anchor="middle">${label(v)}</text>`).join('') +
      ticks(bounds.ymin, bounds.ymax).map(v => `<path d="M${left - 4},${number(y(v))}h4" stroke="${INK}"/><text x="${left - 7}" y="${number(y(v) + 3.5)}" text-anchor="end">${label(v)}</text>`).join('') +
      `<text x="${(left + right) / 2}" y="${bottom + 33}" text-anchor="middle">${xlabel}</text><text transform="translate(12 ${(top + bottom) / 2}) rotate(-90)" text-anchor="middle">${ylabel}</text>`
  }
  const path = (points: Point2[]) => points.map((p, i) => `${i ? 'L' : 'M'}${number(x(p.x))},${number(y(p.y))}`).join('')
  const wrap = (body: string) => `<g clip-path="url(#${clipId})">${body}</g>`
  const defs = `<defs><clipPath id="${clipId}"><rect x="${left}" y="${top}" width="${right - left}" height="${bottom - top}"/></clipPath></defs>`
  return { x, y, axes, path, wrap, defs, left, right, top, bottom }
}

function scatter(points: Point2[], p: ReturnType<typeof plot>, radius: number, limit: number) {
  const stride = Math.max(1, Math.ceil(points.length / limit))
  const d = points.filter((_, i) => i % stride === 0).map(q => `M${number(p.x(q.x) - radius)},${number(p.y(q.y))}a${radius},${radius} 0 1,0 ${2 * radius},0a${radius},${radius} 0 1,0 ${-2 * radius},0`).join('')
  return `<path data-layer="classified-points" d="${d}" fill="${ORANGE}" fill-opacity=".65"/>`
}

function tubeMesh(points: Vector3[], radius: number): ReportMesh {
  const vertices: Vector3[] = [], indices: number[] = [], sides = 24
  points.forEach((point, i) => {
    const tangent = points[Math.min(i + 1, points.length - 1)]!.clone().sub(points[Math.max(0, i - 1)]!)
    const { u, v } = reportFrame(tangent)
    for (let j = 0; j < sides; j++) {
      const angle = j / sides * 2 * Math.PI
      vertices.push(point.clone().addScaledVector(u, radius * Math.cos(angle)).addScaledVector(v, radius * Math.sin(angle)))
      if (i) {
        const a = (i - 1) * sides + j, b = (i - 1) * sides + (j + 1) % sides, c = i * sides + j, d = i * sides + (j + 1) % sides
        indices.push(a, b, c, b, d, c)
      }
    }
  })
  return { vertices, indices }
}

function meshPath(meshes: ReportMesh[], project: (point: Vector3) => Point2, p: ReturnType<typeof plot>) {
  return meshes.map(mesh => {
    const projected = mesh.vertices.map(project)
    let d = ''
    for (let i = 0; i + 2 < mesh.indices.length; i += 3) {
      const a = projected[mesh.indices[i]!]!, b = projected[mesh.indices[i + 1]!]!, c = projected[mesh.indices[i + 2]!]!
      // Unify projected winding so front/back surfaces form a union rather
      // than cancelling under SVG's nonzero rule. Preserve bends and gaps.
      if (a && b && c) d += p.path((b.x - a.x) * (c.y - a.y) - (b.y - a.y) * (c.x - a.x) < 0 ? [a, c, b] : [a, b, c]) + 'Z'
    }
    return d
  }).join('')
}

function heading(letter: string, title: string) {
  return `<text x="4" y="16" font-size="15" font-weight="700">${letter}</text><text x="23" y="16" font-size="12" font-weight="500">${escape(title)}</text>`
}
function style(body: string) {
  return `<rect width="100%" height="100%" fill="white"/><g fill="${INK}" font-family="Arial, 'Noto Sans CJK SC', sans-serif" font-size="10">${body}</g>`
}

export function prepareRebarReportFigure(input: RebarFigureInput) {
  const meshes = input.design.filter(mesh => mesh.vertices.length && mesh.vertices.every(finite))
  const design = meshes.flatMap(mesh => mesh.vertices)
  const scan = input.scan.filter(p => finite(p.position))
  const curves = supportedReportCurves(input.fitted)
  const tubes = curves.filter(curve => curve.radiusM > 0).map(curve => tubeMesh(curve.points, curve.radiusM))
  const units = profileUnits(input.profile)
  const main = [...units].sort((a, b) => (b.at(-1)!.stationM - b[0]!.stationM) - (a.at(-1)!.stationM - a[0]!.stationM))[0]
  const origin = main ? vector(main[0]!.designCenterM) : design[0]?.clone() ?? new Vector3()
  const direction = main && main.length >= 2 ? vector(main.at(-1)!.designCenterM).sub(origin) : principalAxis(design)
  const frame = reportViewFrame(direction)
  if (main && main.length >= 2 && direction.dot(frame.s) < 0) origin.copy(vector(main.at(-1)!.designCenterM))
  const selected: ReportSectionSelection[] = input.sectionLayout === 'stirrup' ? selectStirrupReportSections(input.profile, frame)
    : selectReportSections(input.profile, frame).map((section, slot) => ({ ...section, slot, role: 'body' }))
  const all = [...design, ...scan.map(p => p.position), ...tubes.flatMap(tube => tube.vertices)]
  const sectionData = selected.map(section => {
    const { row, frame: f, origin: center } = section
    const project = (point: Vector3) => { const d = point.clone().sub(center); return { x: d.dot(f.u) * 1000, y: d.dot(f.v) * 1000 } }
    // Reuse the saved half-thickness for this explicitly labelled engineering
    // section plane. These preview counts are not the saved local-normal stats.
    const savedWindow = (row as RebarProfileSample & { windowM?: number }).windowM
    const halfThickness = savedWindow != null && savedWindow > 0 ? savedWindow : .005
    const points = scan.filter(point => (!input.instanceUnits.size || input.instanceUnits.get(point.instanceId) === row.designUnitId) && Math.abs(point.position.clone().sub(center).dot(f.s)) <= halfThickness).map(point => project(point.position))
    const candidates: Array<{ center: Vector3; radius: number; tangent: Vector3 }> = []
    for (const curve of curves.filter(curve => curve.designUnitId === row.designUnitId)) {
      for (let i = 1; i < curve.points.length; i++) {
        const a = curve.points[i - 1]!, b = curve.points[i]!, da = a.clone().sub(center).dot(f.s), db = b.clone().sub(center).dot(f.s)
        if (da * db > 0 || Math.abs(da - db) < 1e-12) continue
        candidates.push({ center: a.clone().lerp(b, da / (da - db)), radius: curve.radiusM * 1000, tangent: b.clone().sub(a) })
      }
    }
    const fitted = candidates.sort((a, b) => a.center.distanceToSquared(center) - b.center.distanceToSquared(center))[0]
    const radius = input.fitted.find(s => s.designUnitId === row.designUnitId && s.radiusM > 0)?.radiusM ?? input.designRadiusM ??
      (row.radiusM != null && row.radiusDeltaM != null ? row.radiusM - row.radiusDeltaM : null)
    const fitCenter = fitted ? project(fitted.center) : null
    const fitSection = fitted && fitCenter ? reportTubeSection(fitCenter, fitted.radius, fitted.tangent, f) : []
    const diameter = reportSectionDiameter(row), missing = !points.length && !fitted && !diameter
    const designSection = !missing && radius != null && radius > 0 ? reportTubeSection({ x: 0, y: 0 }, radius * 1000, section.tangent, f) : []
    return { ...section, points, halfThickness, radius: radius != null && radius > 0 ? radius * 1000 : null, fitted, fitCenter, fitSection, designSection, diameter, missing }
  })
  // The design centre stays at the exact plot centre on EVERY sheet. Enlarge
  // symmetric limits to fit all evidence; never pan toward the observations.
  // Put in-plot labels on the less occupied vertical side, with reserved space.
  const content = extent([{ x: 0, y: 0 }, ...sectionData.flatMap(s => [...s.points, ...s.designSection, ...s.fitSection])])
  const annotationEdge = content.ymax <= -content.ymin ? 'top' : 'bottom'
  const halfSpan = Math.max(7.5, Math.max(-content.xmin, content.xmax) / .88,
    Math.max(-content.ymin, content.ymax) / .9, (annotationEdge === 'top' ? content.ymax : -content.ymin) / .48)
  const sectionSpan = Math.ceil(halfSpan * 2 / 5) * 5
  const sectionBounds = { xmin: -sectionSpan / 2, xmax: sectionSpan / 2, ymin: -sectionSpan / 2, ymax: sectionSpan / 2 }
  const views = longitudinalViews(frame)
  const bounds = (transverse: Vector3) => extent(all.map(point => {
    const d = point.clone().sub(origin)
    return { x: d.dot(frame.s) * 1000, y: d.dot(transverse) * 1000 }
  }))
  return { input, meshes, design, scan, curves, tubes, selected, units, origin, frame, all, sectionData,
    sectionBounds, sectionSpan, annotationEdge, views, bounds: { front: bounds(views[0]!.transverse), top: bounds(views[1]!.transverse) } }
}
export type PreparedReportFigure = ReturnType<typeof prepareRebarReportFigure>

/** One set of physical axis ranges per chapter, including short and missing
 * members. Never hide an extreme observation by limiting a percentile. */
export function commonReportScales(figures: PreparedReportFigure[]): ReportFigureScales {
  const valid = figures.filter(figure => figure.all.length)
  const range = (values: number[], pad: number, fraction: number): [number, number] => {
    const lo = Math.min(...values), hi = Math.max(...values)
    if (!Number.isFinite(lo) || !Number.isFinite(hi)) return [-pad, pad]
    const padding = Math.max(pad, (hi - lo) * fraction)
    return [lo - padding, hi + padding]
  }
  return {
    station: range(valid.flatMap(f => [f.bounds.front.xmin, f.bounds.front.xmax]), 5, .04),
    front: range(valid.flatMap(f => [f.bounds.front.ymin, f.bounds.front.ymax]), 2, .18),
    top: range(valid.flatMap(f => [f.bounds.top.ymin, f.bounds.top.ymax]), 2, .18),
  }
}

export function buildRebarReportFigure(input: RebarFigureInput, scales?: ReportFigureScales): RebarFigure {
  return renderRebarReportFigure(prepareRebarReportFigure(input), scales)
}

/** Rotate the drawing basis, not the SVG text. Preserve the viewing direction:
 * old (station, transverse) becomes (-transverse, station), so height is up
 * and screen-right cross screen-up still points toward the same observer. */
export function reportLongitudinalProjection(point: Vector3, origin: Vector3, station: Vector3, transverse: Vector3, vertical: boolean) {
  const d = point.clone().sub(origin), s = d.dot(station) * 1000, t = d.dot(transverse) * 1000
  return vertical ? { x: -t, y: s } : { x: s, y: t }
}

export function renderRebarReportFigure(prepared: PreparedReportFigure, chapterScales?: ReportFigureScales, layout: 'landscape' | 'portrait' = 'landscape'): RebarFigure {
  const { input, meshes, design, scan, curves, tubes, selected, units, origin, frame, all, sectionData, sectionBounds, sectionSpan, annotationEdge, views } = prepared
  const scales = chapterScales ?? commonReportScales([prepared])
  const portrait = layout === 'portrait'
  const stirrup = input.sectionLayout === 'stirrup'
  const vertical = portrait && frame.axis === 'Z'
  const width = vertical ? 250 : 800, height = vertical ? 500 : 128
  const viewBox = `0 0 ${width} ${height}`
  const longitudinal: RebarFigurePanel[] = []
  for (const [index, view] of views.entries()) {
    const { id: name, transverse, title } = view
    const project = (point: Vector3) => reportLongitudinalProjection(point, origin, frame.s, transverse, vertical)
    if (!all.length) { longitudinal.push({ id: name!, title, viewBox, markup: style(heading(index ? 'b' : 'a', title) + `<text x="${width / 2}" y="${height / 2}" text-anchor="middle">构件几何尚未加载</text>`) }); continue }
    const bounds = vertical
      ? { xmin: -scales[name][1], xmax: -scales[name][0], ymin: scales.station[0], ymax: scales.station[1] }
      : { xmin: scales.station[0], xmax: scales.station[1], ymin: scales[name][0], ymax: scales[name][1] }
    const p = plot(`${input.key}-${name}`, bounds, width, height, false, false, vertical)
    // Project real triangles, never a convex hull (which fills bends and gaps).
    const triangles = meshPath(meshes, project, p)
    const model = `<path data-layer="design-model" d="${triangles}" fill="#d8e6f0"/>`
    const tube = `<path data-layer="flexible-tube" d="${meshPath(tubes, project, p)}" fill="${ORANGE}" fill-opacity=".14"/>`
    const designAxes = units.map(rows => `<path d="${p.path(rows.map(row => project(vector(row.designCenterM))))}" fill="none" stroke="${BLUE}" stroke-width="1.2"/>`).join('')
    const fitted = curves.map(curve => `<path data-layer="flexible-fit" d="${p.path(curve.points.map(project))}" fill="none" stroke="${ORANGE}" stroke-width="1.4" stroke-dasharray="5 3"/>`).join('')
    const markers = selected.map(section => {
      const letter = 'cdefg'[section.slot]
      if (section.role === 'hook') {
        const at = project(section.origin)
        // Trace the cut plane in each viewing plane, preserving the drawing's
        // independent axis scales instead of rotating a horizontal label.
        const trace = new Vector3().crossVectors(section.frame.s, new Vector3().crossVectors(frame.s, transverse)).normalize()
        const next = project(section.origin.clone().add(trace))
        const dx = p.x(next.x) - p.x(at.x), dy = p.y(next.y) - p.y(at.y), length = Math.hypot(dx, dy)
        const ux = length > 1e-9 ? dx / length : 1, uy = length > 1e-9 ? dy / length : 0
        const x = p.x(at.x), y = p.y(at.y)
        return `<g data-cut-role="hook" data-cut-letter="${letter}"><path d="M${number(x - 13 * ux)},${number(y - 13 * uy)}L${number(x + 13 * ux)},${number(y + 13 * uy)}" stroke="#53636c" stroke-width=".8" stroke-dasharray="3 2"/><text x="${number(x + 16)}" y="${number(y - 7)}" fill="${MUTED}">${letter}</text></g>`
      }
      if (vertical) {
        const y = p.y(project(section.origin).y)
        return `<path d="M${p.left},${number(y)}H${p.right - 14}" stroke="#85929a" stroke-width=".65" stroke-dasharray="2 4"/><text x="${p.right - 4}" y="${number(y + 3)}" text-anchor="end" fill="${MUTED}">${letter}</text>`
      }
      const x = p.x(project(section.origin).x)
      return `<path d="M${number(x)},${p.top + 14}V${p.bottom}" stroke="#85929a" stroke-width=".65" stroke-dasharray="2 4"/><text x="${number(x)}" y="${p.top + 10}" text-anchor="middle" fill="${MUTED}">${letter}</text>`
    }).join('')
    const xlabel = vertical ? `−${view.label}` : frame.axis, ylabel = vertical ? frame.axis : view.label
    const direction = `<text data-view-direction="${view.from}" x="${width - 13}" y="${vertical ? 33 : 16}" text-anchor="end" fill="${MUTED}">视向 ${view.from} · ${xlabel} 向右 / ${ylabel} 向上</text>`
    longitudinal.push({ id: name!, title, viewBox, markup: style(`<g data-axis-range="${[bounds.xmin, bounds.xmax, bounds.ymin, bounds.ymax].join(',')}" data-main-axis="${vertical ? 'vertical' : 'horizontal'}"/>` + p.defs + heading(index ? 'b' : 'a', title) + direction + p.wrap(model + tube + designAxes + scatter(scan.map(q => project(q.position)), p, .65, 6000) + fitted + markers) + p.axes(`${xlabel}′ / mm`, `${ylabel}′ / mm`)) })
  }

  const sections: RebarFigurePanel[] = Array.from({ length: 5 }, (_, index) => {
    const section = sectionData.find(row => row.slot === index)
    const role = stirrup && index >= 3 ? 'hook' : 'body', compact = portrait && role === 'hook'
    const station = section ? section.origin.clone().sub(origin).dot(frame.s) * 1000 : null
    const title = section?.title ?? (role === 'hook' ? index === 3 ? frame.axis === 'Z' ? '上端弯钩截面' : '末端弯钩截面' : frame.axis === 'Z' ? '下端弯钩截面' : '起端弯钩截面'
      : station === null ? `截面 ${index + 1}` : `${stirrup ? '主体' : ''}截面 ${frame.axis}′ = ${station.toFixed(1)} mm`)
    const p = plot(`${input.key}-section-${index}`, sectionBounds, 220, compact ? 170 : 252, true, compact)
    const missing = !section || section.missing
    const metadata = `<g data-section-role="${role}" data-section-missing="${missing}" data-section-unit="${escape(section?.row.designUnitId ?? '')}" data-section-normal="${section?.frame.s.toArray().join(',') ?? ''}" data-section-tangent="${section?.tangent.clone().normalize().toArray().join(',') ?? ''}" data-section-span="${sectionSpan}" data-design-origin="${p.x(0)},${p.y(0)}" data-section-bounds="${[sectionBounds.xmin, sectionBounds.xmax, sectionBounds.ymin, sectionBounds.ymax].join(',')}"/>`
    const directionText = role === 'hook' ? `${station === null ? '局部 u / v' : `${frame.axis}′ = ${station.toFixed(1)} mm`} · 垂直钩端轴线` : `视向 −${frame.axis} → +${frame.axis}`
    const direction = `<text data-section-direction="${role === 'hook' ? '局部法向' : `−${frame.axis} → +${frame.axis}`}" x="23" y="30" fill="${MUTED}" font-size="9">${directionText}</text>`
    const sectionFrame = section?.frame ?? (role === 'hook' ? { uLabel: 'u', vLabel: 'v' } : reportSectionFrame(frame))
    const axisLabel = (label: string) => label.startsWith('−') ? `−Δ${label.slice(1)} / mm` : `Δ${label} / mm`
    const finish = (body: string) => ({ id: `section-${index}`, title, sectionRole: role as 'body' | 'hook', missing, viewBox: `0 0 220 ${compact ? 170 : 252}`,
      markup: style(metadata + p.defs + heading('cdefg'[index]!, title) + direction + body + p.axes(axisLabel(sectionFrame.uLabel), axisLabel(sectionFrame.vLabel))) })
    if (!section || missing) return finish(`<g data-layer="missing-placeholder" fill="${MUTED}" text-anchor="middle"><text x="${p.x(0)}" y="${p.y(0) - 6}" font-size="12">缺测</text><text x="${p.x(0)}" y="${p.y(0) + 12}" font-size="9">${section ? '无实测截面数据' : '截面位置未提供'}</text></g>`)
    const cross = (center: Point2, color: string) => `<path d="M${number(p.x(center.x) - 3)},${number(p.y(center.y))}h6M${number(p.x(center.x))},${number(p.y(center.y) - 3)}v6" stroke="${color}" stroke-width="1.5"/>`
    let body = section.designSection.length ? `<path data-layer="design-section" d="${p.path(section.designSection)}Z" fill="none" stroke="${BLUE}" stroke-width="1.6"/>` + cross({ x: 0, y: 0 }, BLUE) : ''
    body += scatter(section.points, p, 1, 1600)
    if (section.fitCenter && section.fitSection.length) body += `<path data-layer="flexible-fit" d="${p.path(section.fitSection)}Z" fill="none" stroke="${ORANGE}" stroke-width="1.6" stroke-dasharray="5 3"/>` + cross(section.fitCenter, ORANGE)
    const offset = section.fitCenter ? Math.hypot(section.fitCenter.x, section.fitCenter.y) : null
    if (section.fitCenter && offset != null && offset > .01) {
      const dx = p.x(section.fitCenter.x) - p.x(0), dy = p.y(section.fitCenter.y) - p.y(0), l = Math.hypot(dx, dy)
      const ax = p.x(section.fitCenter.x), ay = p.y(section.fitCenter.y), ux = dx / l, uy = dy / l
      body += `<path d="M${number(p.x(0))},${number(p.y(0))}L${number(ax)},${number(ay)}m${number(-4 * ux + 2 * uy)},${number(-4 * uy - 2 * ux)}l${number(4 * ux - 2 * uy)},${number(4 * uy + 2 * ux)}l${number(-4 * ux - 2 * uy)},${number(-4 * uy + 2 * ux)}" fill="none" stroke="#377b77" stroke-width="1"/>`
    }
    const annotation = offset != null ? `Δ = ${offset.toFixed(2)} mm` : '该位置无可靠拟合轴线'
    const noteY = annotationEdge === 'top' ? p.top + 12 : p.bottom - 30
    const unit = units.length > 1 ? `<text x="${p.right - 5}" y="${noteY}" text-anchor="end" fill="${MUTED}" font-size="8">${escape(section.row.designUnitId.split('/').at(-1)!)}</text>` : ''
    const designDiameter = section.radius != null ? section.radius * 2 : null
    const diameter = `<text data-section-diameter="${section.diameter?.measuredMm ?? ''}" data-design-diameter="${designDiameter ?? ''}" x="${p.left + 6}" y="${noteY + 12}" font-size="9">D₀ = ${formatReportDiameterMm(designDiameter)} · Dₘ = ${formatReportDiameterMm(section.diameter?.measuredMm)} mm</text>`
    const notes = compact
      ? `<g data-section-annotation="side" font-size="9"><text x="147" y="52" fill="${offset == null ? MUTED : '#28615d'}">${offset == null ? '无可靠拟合' : `Δ ${offset.toFixed(2)} mm`}</text><text x="147" y="72">D₀ ${formatReportDiameterMm(designDiameter)} mm</text><text data-section-diameter="${section.diameter?.measuredMm ?? ''}" data-design-diameter="${designDiameter ?? ''}" x="147" y="87">Dₘ ${formatReportDiameterMm(section.diameter?.measuredMm)} mm</text><text x="147" y="102">t ${(section.halfThickness * 2000).toFixed(1)} mm</text><text x="147" y="117">n ${section.points.length}</text>${units.length > 1 ? `<text x="147" y="132" fill="${MUTED}">${escape(section.row.designUnitId.split('/').at(-1)!)}</text>` : ''}</g>`
      : `<g data-section-annotation="inside" data-annotation-edge="${annotationEdge}"><text x="${p.left + 6}" y="${noteY}" fill="${offset == null ? MUTED : '#28615d'}" font-size="10.5">${escape(annotation)}</text>${diameter}<text x="${p.left + 6}" y="${noteY + 24}" fill="${MUTED}" font-size="9">t = ${(section.halfThickness * 2000).toFixed(1)} mm · n = ${section.points.length}</text>${unit}</g>`
    return finish(p.wrap(body + (compact ? '' : notes)) + (compact ? notes : ''))
  })
  const notes = ['a、b：章内同尺度，横纵轴分别定标；′ 为设计起点相对坐标。' + (stirrup
    ? `c–e：主体截面；f、g：弯钩法向截面，u/v 为局部坐标。两组各自同尺度，截面幅宽 ${sectionSpan} mm。`
    : `c–g：设计中心固定，本页等比例同尺度（幅宽 ${sectionSpan} mm）。`),
    'Δ 为拟合轴线偏移；D₀ 为设计直径，Dₘ 为法向截面圆拟合直径，虚线管面沿用 D₀。ΔD = Dₘ − D₀，按全部有效截面统计，k 为截面数；— 为缺测。t 为切片厚度，n 为预览点数。']
  const unavailable: string[] = []
  if (!curves.length) unavailable.push('没有可用的拟合钢筋轴线')
  if (!input.profile.length) unavailable.push('没有截面位置数据')
  if (!design.length) unavailable.push('设计模型几何尚未加载')
  if (!scan.length) unavailable.push('没有可用的实测点云预览')
  if (unavailable.length) notes.push(`当前构件：${unavailable.join('；')}。`)
  return { longitudinal, sections, notes }
}
