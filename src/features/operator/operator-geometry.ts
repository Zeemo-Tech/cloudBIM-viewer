import { Box3, Vector3, type BufferGeometry } from 'three'
import type { RebarComparisonBar } from '@cloudbim/viewer-core'

export type OperatorBarState = 'measured' | 'outlier' | 'missing' | 'review'

/** A vertex-only PLY is a point cloud, not a triangle stream. */
export function validateOperatorPlySurface(buffer: ArrayBuffer) {
  const header = new TextDecoder().decode(new Uint8Array(buffer, 0, Math.min(buffer.byteLength, 65536))).split(/\r?\nend_header\r?\n/)[0] ?? ''
  const faceCount = /^element face (\d+)\s*$/m.exec(header)?.[1]
  if (!/^ply\r?\n/.test(header) || !faceCount || Number(faceCount) === 0) {
    throw new Error('模型没有钢筋三角面，请重新生成表面模型后查看。')
  }
}

export function operatorBarState(bar: RebarComparisonBar, tolerance: number | null): OperatorBarState {
  if (bar.knownCount === 0 || bar.status === 'missing') return 'missing'
  if (bar.status === 'review' || !bar.stats || !Number.isFinite(bar.stats.p95Abs)) return 'review'
  if (tolerance === null || !Number.isFinite(tolerance) || tolerance < 0) return 'review'
  return bar.stats.p95Abs! > tolerance ? 'outlier' : 'measured'
}

/** The PLY is already in BIM XYZ metres. Never translate or flip its positions. */
export function mapOperatorGeometry(geometry: BufferGeometry, bars: readonly RebarComparisonBar[], expectedVertexCount: number) {
  const positions = geometry.getAttribute('position')
  if (!positions || !positions.count) throw new Error('模型没有可显示的顶点，请重新生成检测结果。')
  if (positions.count !== expectedVertexCount) throw new Error('模型顶点与检测记录不一致，无法可靠定位钢筋。')
  if (!bars.length) throw new Error('检测结果没有钢筋定位记录，请重新分析。')

  const owner = new Int32Array(positions.count).fill(-1)
  const bounds = bars.map(() => new Box3())
  const ids = new Set<string>()
  const point = new Vector3()
  bars.forEach((bar, barIndex) => {
    if (!bar.ifcGlobalId || ids.has(bar.ifcGlobalId)) throw new Error('钢筋编号重复或缺失，无法可靠定位。')
    ids.add(bar.ifcGlobalId)
    const start = bar.vertexStart, end = start + bar.vertexCount
    if (!Number.isSafeInteger(start) || !Number.isSafeInteger(bar.vertexCount) || start < 0 || bar.vertexCount < 0 || end > positions.count) {
      throw new Error('钢筋顶点范围越界，请重新生成检测结果。')
    }
    for (let i = start; i < end; i++) {
      if (owner[i] !== -1) throw new Error('钢筋顶点范围重叠，无法可靠定位。')
      owner[i] = barIndex
      bounds[barIndex]!.expandByPoint(point.fromBufferAttribute(positions, i))
    }
  })
  for (let i = 0; i < positions.count; i++) {
    if (![positions.getX(i), positions.getY(i), positions.getZ(i)].every(Number.isFinite)) {
      throw new Error('模型存在无效坐标，请重新生成检测结果。')
    }
  }

  const originalIndex = geometry.index
  const triangleCount = originalIndex?.count ?? positions.count
  if (triangleCount % 3 !== 0) throw new Error('模型没有完整的三角面，无法显示钢筋表面。')
  const triangles: number[][] = Array.from({ length: bars.length + 1 }, () => [])
  for (let i = 0; i < triangleCount; i += 3) {
    const a = originalIndex ? originalIndex.getX(i) : i
    const b = originalIndex ? originalIndex.getX(i + 1) : i + 1
    const c = originalIndex ? originalIndex.getX(i + 2) : i + 2
    if (![a, b, c].every(index => Number.isInteger(index) && index >= 0 && index < positions.count)) {
      throw new Error('模型三角面引用无效顶点，请重新生成检测结果。')
    }
    // Cross-range/unassigned faces remain visible but are never assigned to a bar.
    const mapped = owner[a]! >= 0 && owner[a] === owner[b] && owner[a] === owner[c] ? owner[a]! : bars.length
    triangles[mapped]!.push(a, b, c)
  }
  if (!triangles.some((indices, i) => i < bars.length && indices.length)) {
    throw new Error('模型三角面无法对应钢筋记录，请重新生成检测结果。')
  }
  const indices: number[] = []
  geometry.clearGroups()
  triangles.forEach((part, materialIndex) => {
    if (part.length) geometry.addGroup(indices.length, part.length, materialIndex)
    for (const index of part) indices.push(index)
  })
  geometry.setIndex(indices)
  geometry.computeBoundingBox()
  geometry.computeBoundingSphere()
  if (!geometry.getAttribute('normal')) geometry.computeVertexNormals()
  return { bounds, pickIds: bars.map(bar => bar.ifcGlobalId).concat('') }
}
