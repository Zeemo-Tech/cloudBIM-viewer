import * as THREE from 'three'
import type { FittedGeometry, MeasurementEvidence, PlaneGeometry, XYZ } from './types'

export function transformMeasurementEvidence(evidence: MeasurementEvidence, matrix: THREE.Matrix4): MeasurementEvidence {
  const next: MeasurementEvidence = JSON.parse(JSON.stringify(evidence))
  const point = (p: XYZ) => new THREE.Vector3(...p).applyMatrix4(matrix).toArray() as XYZ
  const direction = (p: XYZ) => new THREE.Vector3(...p).transformDirection(matrix).toArray() as XYZ
  const normalMatrix = new THREE.Matrix3().getNormalMatrix(matrix)
  const normal = (p: XYZ) => new THREE.Vector3(...p).applyMatrix3(normalMatrix).normalize().toArray() as XYZ
  const scale = new THREE.Vector3().setFromMatrixScale(matrix)
  const uniform = Math.abs(scale.x - scale.y) < 1e-8 && Math.abs(scale.y - scale.z) < 1e-8
  const plane = (p: PlaneGeometry) => {
    p.origin = point(p.origin); p.normal = normal(p.normal); p.support = p.support.map(point)
    if (uniform) { p.radius *= scale.x; p.rmse *= scale.x }
  }
  const geometry = (g: FittedGeometry) => {
    if (g.kind === 'plane') plane(g)
    else if (g.kind === 'cylinder') {
      if (!uniform) throw new Error('圆柱测量不支持非均匀缩放，请在变换后重新测量')
      g.center = point(g.center); g.axis = direction(g.axis); g.support = g.support.map(point)
      g.radius *= scale.x; g.length *= scale.x; g.rmse *= scale.x
    } else { g.position = point(g.position); if (g.direction) g.direction = direction(g.direction); g.planes.forEach(plane) }
  }
  next.anchors = next.anchors.map(point)
  if (next.geometry) geometry(next.geometry)
  if (next.referencePlane) plane(next.referencePlane)
  if (next.referenceCylinder) geometry(next.referenceCylinder)
  if (next.frame) {
    next.frame.origin = point(next.frame.origin)
    next.frame.x = direction(next.frame.x); next.frame.y = direction(next.frame.y); next.frame.z = direction(next.frame.z)
  }
  next.sources.forEach(source => {
    if (source.worldMatrix.length === 16) source.worldMatrix = matrix.clone().multiply(new THREE.Matrix4().fromArray(source.worldMatrix)).toArray()
  })
  if (uniform && next.unit === 'm' && next.value !== undefined) next.value *= scale.x
  return next
}

export function measurementPresentation(evidence?: MeasurementEvidence) {
  if (!evidence || evidence.version !== 1) return null
  const names: Record<string, string> = { plane: '局部平面', 'point-plane': '点到面', 'plane-plane': '平面间距', edge: '边缘', corner: '角点',
    angle: '三点角度', 'line-angle': '线线夹角', 'plane-angle': '面面夹角', cylinder: '圆柱', 'cylinder-distance': '圆柱轴距', frame: '局部坐标系' }
  const title = names[evidence.type]
  if (!title) return null
  const rows: Array<{ label: string; value: string }> = []
  let mainLabel: string | undefined, mainValue: string | undefined
  const g = evidence.geometry
  const metres = (value: number) => `${value.toFixed(4)} m`
  if (g?.kind === 'cylinder' && evidence.type === 'cylinder') {
    mainLabel = '直径'; mainValue = metres(g.radius * 2)
    rows.push({ label: '半径', value: metres(g.radius) }, { label: '支撑长度', value: metres(g.length) },
      { label: '圆弧覆盖', value: `${g.angularCoverage.toFixed(1)}°` })
  } else if (evidence.value !== undefined) {
    mainLabel = evidence.unit === 'deg' ? '夹角' : evidence.type === 'cylinder-distance' ? '无限轴线最短距' : '垂直距离'
    mainValue = evidence.unit === 'deg' ? `${evidence.value.toFixed(2)}°` : metres(Math.abs(evidence.value))
    if (evidence.unit === 'm' && evidence.type !== 'cylinder-distance') rows.push({ label: '沿法向', value: `${evidence.value >= 0 ? '+' : ''}${metres(evidence.value)}` })
  }
  if (g) {
    rows.push({ label: g.kind === 'plane' && g.algorithm === 'mesh-face-normal-v1' ? '几何依据' : '拟合残差',
      value: g.kind === 'plane' && g.algorithm === 'mesh-face-normal-v1' ? '模型三角面' : `${(g.rmse * 1000).toFixed(2)} mm` })
    if ('inlierCount' in g) rows.push({ label: '支撑点', value: `${g.inlierCount} / ${g.pointCount}` })
    if (g.kind === 'plane') rows.push({ label: '法向', value: g.normal.map(n => n.toFixed(3)).join(', ') })
  }
  rows.push({ label: '来源', value: evidence.dataLevel })
  return { title, mainLabel, mainValue, rows }
}
