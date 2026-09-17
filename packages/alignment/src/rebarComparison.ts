import { BufferAttribute, Color, type BufferGeometry } from 'three'
import type { C2MResult, RebarComparisonBar } from '@cloudbim/viewer-core'

const originalIndices = new WeakMap<BufferGeometry, BufferAttribute | null>()
const originalColors = new WeakMap<BufferGeometry, BufferAttribute | null>()

/** Refresh the deviation-color baseline after the user changes C2M coloring. */
export function rememberComparisonGeometryColors(geometry: BufferGeometry) {
  originalColors.set(geometry, (geometry.getAttribute('color') as BufferAttribute | undefined)?.clone() ?? null)
}

/** Keep the original vertex stream so distances and report ranges remain bound. */
export function filterComparisonGeometry(geometry: BufferGeometry, selection?: RebarComparisonBar | readonly RebarComparisonBar[]) {
  if (!originalIndices.has(geometry)) originalIndices.set(geometry, geometry.index)
  const original = originalIndices.get(geometry) ?? null
  if (!selection) { geometry.setIndex(original); return }
  const bars = Array.isArray(selection) ? selection : [selection]
  const ranges = bars.map(bar => [bar.vertexStart, bar.vertexStart + bar.vertexCount] as const)
  const indices: number[] = []
  const count = original?.count ?? geometry.getAttribute('position').count
  for (let i = 0; i + 2 < count; i += 3) {
    const triangle = [0, 1, 2].map(offset => original ? original.getX(i + offset) : i + offset)
    if (ranges.some(([start, end]) => triangle.every(index => index >= start && index < end))) indices.push(...triangle)
  }
  geometry.setIndex(indices)
}

/** Keep every comparison mesh visible while dimming vertices outside the inspected bar. */
export function dimComparisonGeometry(geometry: BufferGeometry, bar?: RebarComparisonBar, dimColor = '#8b97a8') {
  if (!originalColors.has(geometry)) {
    rememberComparisonGeometryColors(geometry)
  }
  const original = originalColors.get(geometry)
  if (!bar) {
    if (original) geometry.setAttribute('color', original.clone())
    else geometry.deleteAttribute('color')
    return
  }
  const positions = geometry.getAttribute('position')
  if (!positions) return
  const colors = original ? new Float32Array(original.array) : new Float32Array(positions.count * 3).fill(1)
  const color = new Color(dimColor)
  const start = bar.vertexStart
  const end = start + bar.vertexCount
  for (let index = 0; index < positions.count; index += 1) {
    if (index >= start && index < end) continue
    color.toArray(colors, index * 3)
  }
  geometry.setAttribute('color', new BufferAttribute(colors, 3))
}

export function comparisonBarsAtTolerance(bars: RebarComparisonBar[], distances: Float32Array | null, tolerance: number) {
  if (!distances) return bars
  return bars.map(bar => {
    if (!bar.stats) return bar
    let known = 0, within = 0
    for (let i = bar.vertexStart; i < bar.vertexStart + bar.vertexCount; i++) {
      const value = distances[i]
      if (!Number.isFinite(value)) continue
      known++
      if (Math.abs(value) <= tolerance) within++
    }
    return { ...bar, stats: known ? { ...bar.stats, withinToleranceRatio: within / known } : null }
  })
}

export function rebarStatusLabel(status: RebarComparisonBar['status']) {
  return { matched: '已对应', missing: '缺测', review: '待复核' }[status]
}

export function isRebarInspectionAbnormal(bar: RebarComparisonBar, tolerance: number) {
  if (bar.status !== 'matched' || !bar.stats) return true
  if (typeof bar.stats.p95Abs !== 'number' || !Number.isFinite(bar.stats.p95Abs)) return true
  return bar.stats.p95Abs > tolerance
}

export function rebarReportCSV(result: C2MResult, bars: RebarComparisonBar[], toleranceMm: number) {
  const numeric = (value: number | undefined, scale = 1) => typeof value === 'number' && Number.isFinite(value) ? +(value * scale).toFixed(4) : ''
  const cell = (value: unknown) => {
    const text = String(value ?? '')
    const safe = /^[=+@\-\t\r]/.test(text) ? `'${text}` : text
    return `"${safe.replaceAll('"', '""')}"`
  }
  const rows: unknown[][] = [[
    'IFC GlobalId', '设计钢筋ID', '名称', '点云实例ID', '待复核实例ID', '待复核点数', '对应状态', '扫描点数', '参与计算点数',
    '已覆盖顶点', '未覆盖顶点', '覆盖率', '平均绝对偏差(mm)', 'RMSE(mm)', 'P95绝对偏差(mm)', '容差(mm)', '容差内比例(已覆盖)',
    '结果版本', '实例映射SHA256', '点云资产ID', 'BIM资产ID', '测量方向',
  ]]
  for (const bar of bars) rows.push([
    bar.ifcGlobalId, bar.designBarId, bar.name, bar.instanceIds.join(';'), (bar.reviewInstanceIds ?? []).join(';'), bar.reviewPointCount ?? 0, rebarStatusLabel(bar.status), bar.pointCount, bar.pointsAfter,
    bar.knownCount, bar.unknownCount, bar.vertexCount ? numeric(bar.knownCount / bar.vertexCount) : '',
    numeric(bar.stats?.meanAbs, 1000), numeric(bar.stats?.rmse, 1000), numeric(bar.stats?.p95Abs, 1000), toleranceMm,
    numeric(bar.stats?.withinToleranceRatio), result.resultVersion, result.diagnostics?.rebarComparison?.instanceMapHash,
    result.modelScanFileId, result.modelBimFileId, result.metricDirection,
  ])
  return '\uFEFF' + rows.map(row => row.map(cell).join(',')).join('\r\n')
}
