/** Geometry and persisted measurements use metres in a right-handed frame. */
export type XYZ = [number, number, number]
export type FitResult<T> = { ok: true; value: T } | { ok: false; reason: string }
export interface FitOptions { radius: number; tolerance: number; facing?: XYZ }
export interface PlaneGeometry {
  kind: 'plane'
  origin: XYZ
  normal: XYZ
  radius: number
  rmse: number
  pointCount: number
  inlierCount: number
  support: XYZ[]
  algorithm: string
}
export interface FeatureGeometry {
  kind: 'edge' | 'corner'
  position: XYZ
  direction?: XYZ
  planes: PlaneGeometry[]
  rmse: number
  algorithm: string
}
export interface CylinderGeometry {
  kind: 'cylinder'
  center: XYZ
  axis: XYZ
  radius: number
  length: number
  rmse: number
  pointCount: number
  inlierCount: number
  angularCoverage: number
  support: XYZ[]
  algorithm: string
}
export type FittedGeometry = PlaneGeometry | FeatureGeometry | CylinderGeometry
export type MeasurementTool = 'pick' | 'plane' | 'point-plane' | 'plane-plane' | 'edge' | 'corner' | 'angle' | 'line-angle' | 'plane-angle' | 'cylinder' | 'cylinder-distance' | 'frame'
export interface MeasurementSettings {
  sourceFilter: 'all' | 'pointcloud' | 'bim'
  snap: 'point' | 'vertex' | 'edge' | 'corner'
  radius: number
  tolerance: number
  magnifier: boolean
  axis: 'free' | 'x' | 'y' | 'z'
}
export interface MeasurementSource {
  kind: 'pointcloud' | 'bim' | 'surface' | 'construction'
  assetId?: number | null
  objectName: string
  objectId?: string
  vertexIndex?: number
  triangleIndex?: number
  localPoint: XYZ
  worldMatrix: number[]
  representation: string
}
export interface MeasurementFrame { origin: XYZ; x: XYZ; y: XYZ; z: XYZ }
export interface MeasurementEvidence {
  version: 1
  type: MeasurementTool | 'distance' | 'locate' | 'area'
  sources: MeasurementSource[]
  anchors: XYZ[]
  geometry?: FittedGeometry
  referencePlane?: PlaneGeometry
  referenceCylinder?: CylinderGeometry
  frame?: MeasurementFrame
  value?: number
  unit?: 'm' | 'deg'
  settings: MeasurementSettings
  capturedAt: string
  dataLevel: string
  /** Different viewer workspaces can have different world origins/poses. */
  contextKey?: string
}
export interface MeasurementAssistState {
  active: boolean
  advanced: boolean
  tool: MeasurementTool
  settings: MeasurementSettings
  status: string
  error: string
  hover: { x: number; y: number; label: string; position: XYZ; sourceLabel: string } | null
  preview: FittedGeometry | null
  pickedCount: number
  canUndo: boolean
  hasPlane: boolean
  hasFrame: boolean
  canRestoreView: boolean
}
export function defaultMeasurementState(): MeasurementAssistState {
  return { active: false, advanced: false, tool: 'plane',
    settings: { sourceFilter: 'all', snap: 'point', radius: 0.15, tolerance: 0.005, magnifier: true, axis: 'free' },
    status: '移动到表面查看取点；点击确认，拖动调整视角', error: '', hover: null, preview: null,
    pickedCount: 0, canUndo: false, hasPlane: false, hasFrame: false, canRestoreView: false }
}
