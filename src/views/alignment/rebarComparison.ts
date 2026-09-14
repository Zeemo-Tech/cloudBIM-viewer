import type { BufferGeometry, BufferAttribute } from 'three'
import type { C2MResult, RebarComparisonBar } from '../../api/backend-c2m'

const originalIndices = new WeakMap<BufferGeometry, BufferAttribute | null>()

/** Keep the original vertex stream so distances and report ranges remain bound. */
export function filterComparisonGeometry(geometry: BufferGeometry, bar?: RebarComparisonBar) {
  if (!originalIndices.has(geometry)) originalIndices.set(geometry, geometry.index)
  const original = originalIndices.get(geometry) ?? null
  if (!bar) { geometry.setIndex(original); return }
  const start = bar.vertexStart, end = start + bar.vertexCount
  const indices: number[] = []
  const count = original?.count ?? geometry.getAttribute('position').count
  for (let i = 0; i + 2 < count; i += 3) {
    const triangle = [0, 1, 2].map(offset => original ? original.getX(i + offset) : i + offset)
    if (triangle.every(index => index >= start && index < end)) indices.push(...triangle)
  }
  geometry.setIndex(indices)
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
