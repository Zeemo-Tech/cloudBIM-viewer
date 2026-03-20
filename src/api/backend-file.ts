import {
  backendRequest,
  backendRequestRaw,
  normalizeBackendUrl,
  readResponseHeader,
  type BackendResult,
} from '@/api/backend-http'

export type AssetType = 'bim' | 'pointcloud'
export type AssetStatus =
  | 'uploading'
  | 'queued'
  | 'processing'
  | 'ready'
  | 'failed'
  | 'terminated'

export interface PaginationParams {
  page?: number
  pageSize?: number
}

export interface PaginatedData<T> {
  total: number
  page: number
  pageSize: number
  list: T[]
}

export interface AssetSummary {
  id: number
  type: AssetType
  sourceName: string
  sourceSize: number
  status: AssetStatus
  errorMessage: string | null
  createdAt: number
}

export interface AssetDetail extends AssetSummary {
  glbUrl?: string
  metadataUrl?: string
  tilesBaseUrl?: string
  tilesetUrl?: string
}

export interface UploadStatus {
  uploadId: string
  assetId: number | null
  assetType: AssetType
  fileName: string
  fileSize: number
  uploadOffset: number
  uploadLength: number
  status: AssetStatus
  errorMessage: string | null
}

export interface ListAssetsParams extends PaginationParams {
  type?: AssetType
  status?: AssetStatus
}

export interface CreateTusUploadParams {
  fileName: string
  fileSize: number
  assetType: AssetType
}

export interface TusUploadSession {
  location: string
  uploadId: string
  uploadLength: number
  uploadOffset: number
}

export interface TusUploadOffset {
  uploadLength: number
  uploadOffset: number
}

const TUS_VERSION = '1.0.0'

function encodeBase64(value: string) {
  return window.btoa(unescape(encodeURIComponent(value)))
}

export function encodeTusMetadata(data: Record<string, string>) {
  return Object.entries(data)
    .map(([key, value]) => `${key} ${encodeBase64(value)}`)
    .join(',')
}

function createTusHeaders(headers?: Record<string, string>) {
  return {
    'Tus-Resumable': TUS_VERSION,
    ...headers,
  }
}

function parseUploadId(location: string) {
  const trimmed = location.trim()
  const parts = trimmed.split('/').filter(Boolean)
  const uploadId = parts.at(-1)

  if (!uploadId) {
    throw new Error('创建上传会话成功，但未解析到 uploadId')
  }

  return uploadId
}

function normalizeAssetPath(path: string) {
  if (!path.startsWith('/')) {
    return `/${path}`
  }

  return path
}

export function listAssets(params: ListAssetsParams = {}) {
  return backendRequest<BackendResult<PaginatedData<AssetSummary>>>('/assets', {
    method: 'GET',
    params: {
      page: params.page ?? 1,
      pageSize: params.pageSize ?? 100,
      type: params.type,
      status: params.status,
    },
  })
}

export function getAssetDetail(assetId: number) {
  return backendRequest<BackendResult<AssetDetail>>(`/assets/${assetId}`, {
    method: 'GET',
  })
}

export function getUploadStatus(uploadId: string) {
  return backendRequest<BackendResult<UploadStatus>>(`/uploads/${uploadId}`, {
    method: 'GET',
  })
}

export async function createTusUpload(params: CreateTusUploadParams) {
  const response = await backendRequestRaw<never>('/uploads', {
    method: 'POST',
    headers: createTusHeaders({
      'Upload-Length': String(params.fileSize),
      'Upload-Metadata': encodeTusMetadata({
        filename: params.fileName,
        assetType: params.assetType,
      }),
    }),
  })

  const location = readResponseHeader(response, 'Location')
  const uploadOffset = Number(readResponseHeader(response, 'Upload-Offset') || '0')
  const uploadLength = Number(
    readResponseHeader(response, 'Upload-Length') || String(params.fileSize),
  )

  if (!location) {
    throw new Error('创建上传会话成功，但响应头中缺少 Location')
  }

  return {
    location,
    uploadId: parseUploadId(location),
    uploadLength,
    uploadOffset,
  } satisfies TusUploadSession
}

export async function getTusUploadOffset(uploadId: string) {
  const response = await backendRequestRaw<never>(`/uploads/${uploadId}`, {
    method: 'HEAD',
    headers: createTusHeaders(),
  })

  return {
    uploadLength: Number(readResponseHeader(response, 'Upload-Length') || '0'),
    uploadOffset: Number(readResponseHeader(response, 'Upload-Offset') || '0'),
  } satisfies TusUploadOffset
}

export async function uploadTusChunk(
  uploadId: string,
  chunk: Blob,
  uploadOffset: number,
) {
  const response = await backendRequestRaw<never>(`/uploads/${uploadId}`, {
    method: 'PATCH',
    data: chunk,
    headers: createTusHeaders({
      'Content-Type': 'application/offset+octet-stream',
      'Upload-Offset': String(uploadOffset),
    }),
  })

  return {
    uploadLength: Number(readResponseHeader(response, 'Upload-Length') || '0'),
    uploadOffset: Number(readResponseHeader(response, 'Upload-Offset') || '0'),
  } satisfies TusUploadOffset
}

export function terminateTusUpload(uploadId: string) {
  return backendRequestRaw<never>(`/uploads/${uploadId}`, {
    method: 'DELETE',
    headers: createTusHeaders(),
  })
}

export function getBimGlbFile(resourcePath: string) {
  return backendRequest<Blob>(normalizeAssetPath(resourcePath), {
    method: 'GET',
    responseType: 'blob',
  })
}

export function getBimMetadata(resourcePath: string) {
  return backendRequest<unknown>(normalizeAssetPath(resourcePath), {
    method: 'GET',
  })
}

export function getPointcloudTilesetUrl(resourcePath: string) {
  return normalizeBackendUrl(normalizeAssetPath(resourcePath))
}
