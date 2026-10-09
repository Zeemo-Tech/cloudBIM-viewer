import type { PointcloudTablePlane } from './tableVisibility'

export const SEGMENTATION_CLASSES = [
  { id: 1, name: '台面', color: '#94a3b8' },
  { id: 2, name: '夹具', color: '#f59e0b' },
  { id: 3, name: '钢筋', color: '#2dd4bf' },
  { id: 4, name: '噪点', color: '#ef476f' },
] as const

export interface PointcloudSegmentationPreview {
  schema: 'pointcloud-segmentation-preview-v1'
  sourcePointCount: number
  sourceSha256: string
  algorithmVersion: string
  runId: string
  plane: PointcloudTablePlane | null
  instanceCount: number
  instanceQuality?: {
    wholeBarValidated: boolean
    unit: string
    shortCandidateCount: number
  }
  assignedPointCount: number
  classCounts: Record<string, number>
}
