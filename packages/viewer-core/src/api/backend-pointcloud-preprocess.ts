import { backendRequest, type BackendResult } from '@/api/backend-http'
import type { PointcloudTablePlane } from '@/features/pointcloud/tableVisibility'

export interface PointcloudPreprocessResult {
  version: string
  result: {
    pointsBefore: number
    pointsAfter: number
    tablePoints: number
    normalK: number
    detected: boolean
    algorithmVersion: string
    plane?: PointcloudTablePlane | null
  }
}

export function getPointcloudPreprocess(assetId: number) {
  return backendRequest<BackendResult<PointcloudPreprocessResult | null>>(
    `/assets/${assetId}/pointcloud-preprocess`,
    { method: 'GET' },
  )
}

export function computePointcloudPreprocess(assetId: number) {
  return backendRequest<BackendResult<PointcloudPreprocessResult>>(
    `/assets/${assetId}/pointcloud-preprocess`,
    { method: 'POST', timeout: 3_600_000 },
  )
}
