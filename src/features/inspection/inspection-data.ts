import { backendRequest, type BackendResult, type RebarComparisonBar } from '@cloudbim/viewer-core'

export type ProblemKind = 'missing' | 'review' | 'outlier' | 'observed'
export const problemLabels: Record<ProblemKind, string> = { missing: '缺测', review: '待复核', outlier: '偏差超阈值', observed: '已有测量' }
export function problemKind(bar: RebarComparisonBar, tolerance: number | null): ProblemKind {
  if (bar.status === 'missing' || !bar.knownCount) return 'missing'
  if (bar.status === 'review' || !bar.stats || !Number.isFinite(bar.stats.p95Abs) || tolerance === null) return 'review'
  return bar.stats.p95Abs! > tolerance ? 'outlier' : 'observed'
}
export function millimetres(value: number | null | undefined) {
  return typeof value === 'number' && Number.isFinite(value) ? `${(value * 1000).toFixed(2)} mm` : '缺少测量'
}
export function coverage(bar: RebarComparisonBar) {
  const total = bar.knownCount + bar.unknownCount
  return total > 0 ? `${(bar.knownCount / total * 100).toFixed(1)}%` : '无可评估数据'
}
export type InspectionAction = 'acknowledge' | 'record_adjustment' | 'request_recheck'
export const actionLabels: Record<InspectionAction, string> = { acknowledge: '确认问题', record_adjustment: '记录处理', request_recheck: '申请复检' }
export interface InspectionActionRecord {
  id: number
  resultVersion: string
  ifcGlobalId: string
  action: InspectionAction
  note: string
  demonstration: boolean
  createdAt: string
}
const actionsPath = (version: string) => `/alignments/bim/c2m/reports/${encodeURIComponent(version)}/actions`
export function listInspectionActions(version: string) {
  return backendRequest<BackendResult<{ items: InspectionActionRecord[] }>>(actionsPath(version), { method: 'GET' })
}
export function saveInspectionAction(version: string, data: { ifcGlobalId: string; action: InspectionAction; note: string; demonstration: boolean }) {
  return backendRequest<BackendResult<unknown>>(actionsPath(version), { method: 'POST', data })
}

/** BIM XZ projection, equal scale; never connect missing samples or separate design units. */
export function projectMeshBars(bars: RebarComparisonBar[]) {
  const valid = (p: number[] | null): p is [number, number, number] => Boolean(p?.length === 3 && p.every(Number.isFinite))
  const all = bars.flatMap(bar => (bar.measurement?.longitudinalProfile ?? []).flatMap(s => [s.designCenterM, s.observedCenterM])).filter(valid)
  if (!all.length) return null
  const xmin = Math.min(...all.map(p => p[0])), xmax = Math.max(...all.map(p => p[0]))
  const zmin = Math.min(...all.map(p => p[2])), zmax = Math.max(...all.map(p => p[2]))
  const scale = Math.min(800 / Math.max(xmax - xmin, .001), 390 / Math.max(zmax - zmin, .001))
  const point = (p: number[]) => ({ x: 450 + (p[0]! - (xmin + xmax) / 2) * scale, y: 235 - (p[2]! - (zmin + zmax) / 2) * scale })
  const rows = bars.map(bar => {
    const samples = bar.measurement?.longitudinalProfile ?? []
    function paths(observed: boolean) {
      const result: string[] = []; let path = ''; let unit = ''
      for (const sample of samples) {
        const p = observed ? sample.observedCenterM : sample.designCenterM
        if (!valid(p) || unit !== sample.designUnitId) { if (path) result.push(path); path = '' }
        if (valid(p)) { const xy = point(p); path += `${path ? 'L' : 'M'}${xy.x.toFixed(2)},${xy.y.toFixed(2)} ` }
        unit = sample.designUnitId
      }
      if (path) result.push(path)
      return result
    }
    return { id: bar.ifcGlobalId, design: paths(false), observed: paths(true), points: samples.filter(s => valid(s.observedCenterM)).map(s => point(s.observedCenterM!)) }
  })
  return { rows, xRange: [xmin, xmax], zRange: [zmin, zmax], scale }
}
