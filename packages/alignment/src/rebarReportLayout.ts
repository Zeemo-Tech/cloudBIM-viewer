import { Matrix4, Vector3 } from 'three'
import type { RebarComparisonBar } from '@cloudbim/viewer-core'

type Vec3 = [number, number, number]
export interface ReportDesignInventory {
  bars: { ifcGlobalId: string; designBarId: string; name: string; points?: Vec3[]; radiusM?: number }[]
  units?: { designBarId: string; designUnitId: string; startM: Vec3; endM: Vec3; kind: string }[]
}
export type ReportChapterId = 'transverse' | 'longitudinal' | 'connector' | 'unresolved'
export interface ReportMemberLocation {
  key: string
  chapterId: ReportChapterId
  path: Vector3[]
  paths: Vector3[][]
  center: Vector3
  height: number
  radius: number
  layers: number[]
  layerLabel: string
}
export interface ReportChapter { id: ReportChapterId; title: string; number: number; keys: string[]; start: number }
export interface RebarReportLayout {
  members: Map<string, ReportMemberLocation>
  chapters: ReportChapter[]
  layers: { index: number; min: number; max: number; height: number }[]
  orderedKeys: string[]
}
const valid = (p: Vec3) => p.length === 3 && p.every(Number.isFinite)
const median = (values: number[]) => [...values].sort((a, b) => a - b)[Math.floor(values.length / 2)] ?? 0

/** Design inventory is in raw scan coordinates (inverse A was applied by the
 * mesh service). Profiles already use raw BIM coordinates. Never mix the two. */
export function buildRebarReportLayout(bars: RebarComparisonBar[], inventory?: ReportDesignInventory, scanToModel?: Matrix4): RebarReportLayout {
  const candidates = bars.map(bar => {
    const source = inventory?.bars.find(row => row.ifcGlobalId === bar.ifcGlobalId)
    const units = (inventory?.units ?? []).filter(row => row.designBarId === source?.designBarId)
    const groups = new Map<string, Vector3[]>()
    for (const row of [...(bar.measurement?.longitudinalProfile ?? [])].sort((a, b) => a.stationM - b.stationM)) {
      if (!valid(row.designCenterM)) continue
      const points = groups.get(row.designUnitId) ?? []
      points.push(new Vector3(...row.designCenterM)); groups.set(row.designUnitId, points)
    }
    if (!groups.size && scanToModel) for (const unit of units) {
      if (valid(unit.startM) && valid(unit.endM)) groups.set(unit.designUnitId, [unit.startM, unit.endM].map(p => new Vector3(...p).applyMatrix4(scanToModel)))
    }
    const main = [...groups.values()].sort((a, b) => b[0]!.distanceTo(b.at(-1)!) - a[0]!.distanceTo(a.at(-1)!))[0] ?? []
    const direction = main.length >= 2 ? main.at(-1)!.clone().sub(main[0]!).normalize() : new Vector3()
    const center = main.length ? main[0]!.clone().add(main.at(-1)!).multiplyScalar(.5) : new Vector3()
    // The navigation keeps the complete analytic path, including terminal bends.
    const paths = scanToModel && source?.points?.length ? [source.points.filter(valid).map(p => new Vector3(...p).applyMatrix4(scanToModel))] : [...groups.values()]
    const path = paths.flat()
    const connector = units.some(unit => unit.kind === 'web') || Math.abs(direction.y) > .3
    return { key: bar.ifcGlobalId, path, paths, center, direction, connector, radius: source?.radiusM ?? .004 }
  })
  // A common plan basis from both perpendicular bar families. Fourfold angles
  // make reversed endpoints and the orthogonal family vote for the same basis.
  let cosine = 0, sine = 0
  for (const bar of candidates.filter(bar => !bar.connector && bar.direction.lengthSq() > .5)) {
    const angle = Math.atan2(-bar.direction.z, bar.direction.x)
    cosine += Math.cos(4 * angle); sine += Math.sin(4 * angle)
  }
  const angle = Math.atan2(sine, cosine) / 4
  const transverse = new Vector3(Math.cos(angle), 0, -Math.sin(angle))
  const longitudinal = new Vector3(-Math.sin(angle), 0, -Math.cos(angle))
  const horizontal = candidates.filter(bar => !bar.connector && bar.direction.lengthSq() > .5).sort((a, b) => a.center.y - b.center.y)
  // Algorithm layerId uses 5 mm bins, not physical mats. Group design body
  // heights across a gap of at least 25 mm / 2.5 median diameters instead.
  const gap = Math.max(.025, median(horizontal.map(bar => bar.radius * 2)) * 2.5)
  const bands: typeof horizontal[] = []
  for (const bar of horizontal) {
    if (!bands.length || bar.center.y - bands.at(-1)!.at(-1)!.center.y > gap) bands.push([])
    bands.at(-1)!.push(bar)
  }
  const layers = bands.map((band, index) => ({ index: index + 1, min: band[0]!.center.y, max: band.at(-1)!.center.y, height: median(band.map(bar => bar.center.y)) }))
  const members = new Map<string, ReportMemberLocation>()
  for (const bar of candidates) {
    const chapterId: ReportChapterId = bar.direction.lengthSq() < .5 ? 'unresolved' : bar.connector ? 'connector'
      : Math.abs(bar.direction.dot(transverse)) >= .85 ? 'transverse' : Math.abs(bar.direction.dot(longitudinal)) >= .85 ? 'longitudinal' : 'unresolved'
    const heights = bar.path.map(p => p.y)
    const min = bar.connector ? Math.min(...heights) : bar.center.y, max = bar.connector ? Math.max(...heights) : bar.center.y
    const indices = layers.filter(layer => max >= layer.min - .01 && min <= layer.max + .01).map(layer => layer.index)
    let layerLabel = '层位未判定'
    if (indices.length === 1) layerLabel = `第 ${indices[0]} 层` + (layers.length === 2 ? indices[0] === 1 ? '（下层）' : '（上层）' : '')
    if (indices.length > 1) layerLabel = `连接第 ${indices.join('、')} 层`
    members.set(bar.key, { key: bar.key, chapterId, path: bar.path, paths: bar.paths, center: bar.center, height: bar.center.y, radius: bar.radius, layers: indices, layerLabel })
  }
  const titles: [ReportChapterId, string][] = [['transverse', '横向钢筋'], ['longitudinal', '纵向钢筋'], ['connector', '箍筋'], ['unresolved', '待分组钢筋']]
  let start = 0
  const chapters: ReportChapter[] = []
  for (const [id, title] of titles) {
    const keys = [...members.values()].filter(bar => bar.chapterId === id).sort((a, b) =>
      (a.layers[0] ?? Infinity) - (b.layers[0] ?? Infinity) ||
      (id === 'transverse' ? b.center.z - a.center.z : a.center.x - b.center.x) || b.center.z - a.center.z || a.key.localeCompare(b.key)).map(bar => bar.key)
    if (keys.length) { chapters.push({ id, title, number: chapters.length + 1, keys, start }); start += keys.length }
  }
  return { members, chapters, layers, orderedKeys: chapters.flatMap(chapter => chapter.keys) }
}

/** Engineering plan (X,Y) = raw GLB (x,-z), height Z = raw GLB y.
 * Viewed from above; X points right, Y points up. Equal mm scale on both axes. */
export function reportPlanPoint(point: Vector3) { return { x: point.x * 1000, y: -point.z * 1000 } }

export function buildReportLocator(layout: RebarReportLayout, key: string) {
  const member = layout.members.get(key), all = [...layout.members.values()]
  const points = all.flatMap(bar => bar.path.map(reportPlanPoint))
  if (!member?.path.length || !points.length) return { viewBox: '0 0 250 256', markup: '<text x="10" y="30" font-size="12">设计模型定位数据未就绪</text>' }
  const n = (value: number) => value.toFixed(2), purple = '#823f68'
  const xmin = Math.min(...points.map(p => p.x)), xmax = Math.max(...points.map(p => p.x))
  const ymin = Math.min(...points.map(p => p.y)), ymax = Math.max(...points.map(p => p.y))
  const scale = Math.min(154 / Math.max(1, xmax - xmin), 168 / Math.max(1, ymax - ymin))
  const ox = 6 + (154 - (xmax - xmin) * scale) / 2, oy = 38 + (168 - (ymax - ymin) * scale) / 2
  const x = (v: number) => ox + (v - xmin) * scale, y = (v: number) => oy + (ymax - v) * scale
  const path = (bar: ReportMemberLocation) => bar.paths.map(points => points.map((p, i) => { const q = reportPlanPoint(p); return `${i ? 'L' : 'M'}${n(x(q.x))},${n(y(q.y))}` }).join('')).join('')
  const background = all.filter(bar => bar.key !== key).sort((a, b) => Number(a.layers.some(i => member.layers.includes(i))) - Number(b.layers.some(i => member.layers.includes(i)))).map(bar =>
    `<path d="${path(bar)}" stroke="${bar.layers.some(i => member.layers.includes(i)) ? '#9aa6ad' : '#e0e5e8'}" stroke-width=".7"/>`).join('')
  const focus = reportPlanPoint(member.center), fx = x(focus.x), fy = y(focus.y)
  const highlight = `<path d="${path(member)}" stroke="white" stroke-width="4"/><path data-layer="locator-current" d="${path(member)}" stroke="${purple}" stroke-width="2"/><circle cx="${n(fx)}" cy="${n(fy)}" r="4.5" stroke="${purple}" stroke-width="1.2" fill="white"/><circle cx="${n(fx)}" cy="${n(fy)}" r="1.5" fill="${purple}" stroke="none"/>`
  const heights = all.flatMap(bar => bar.path.map(p => p.y))
  const lo = Math.min(...heights), hi = Math.max(...heights), z = (h: number) => 194 - (h - lo) / Math.max(.001, hi - lo) * 145
  const levels = layout.layers.map(layer => `<path d="M177,${n(z(layer.height))}h62" stroke="#c5cdd2"/><text x="208" y="${n(z(layer.height) - 7)}" text-anchor="middle" fill="${member.layers.includes(layer.index) ? purple : '#75838d'}">第 ${layer.index} 层</text>`).join('')
  const zlow = member.chapterId === 'connector' ? Math.min(...member.path.map(p => p.y)) : member.height
  const zhigh = member.chapterId === 'connector' ? Math.max(...member.path.map(p => p.y)) : member.height
  const elevation = `<path d="M174,40V207" stroke="#65747e"/><text x="208" y="32" text-anchor="middle">层位 · 自下向上</text>${levels}<path d="M185,${n(z(zlow))}V${n(z(zhigh))}" stroke="${purple}" stroke-width="3"/><circle cx="185" cy="${n(z(member.height))}" r="3" fill="${purple}"/><text x="208" y="222" text-anchor="middle">Z = ${(member.height * 1000).toFixed(0)} mm</text>`
  const barLength = (xmax - xmin) >= 1000 ? 500 : 100
  return { viewBox: '0 0 250 256', markup: `<rect width="250" height="256" fill="white"/><g font-family="Arial, 'Noto Sans CJK SC', sans-serif" font-size="10" fill="#263238"><text x="4" y="16" font-size="14" font-weight="700">h</text><text x="23" y="16" font-size="12">全局定位 · 设计俯视</text><g fill="none" stroke-linejoin="round">${background}${highlight}</g>${elevation}<path d="M8,226h${n(barLength * scale)}m0,-3v6M8,223v6" stroke="#263238"/><text x="${n(8 + barLength * scale / 2)}" y="239" text-anchor="middle">${barLength} mm</text><path d="M120,226h25m-4,-3l4,3l-4,3M120,226v-23m-3,4l3,-4l3,4" fill="none" stroke="#65747e"/><text x="149" y="230">X</text><text x="117" y="198">Y</text><text x="174" y="244" fill="${purple}">● 当前钢筋</text></g>` }
}
