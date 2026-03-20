import type { AssetDetail, AssetType, AssetStatus, UploadStatus } from '@/api/backend-file'
import {
  createTusUpload,
  getAssetDetail,
  getTusUploadOffset,
  getUploadStatus,
  terminateTusUpload,
  uploadTusChunk,
} from '@/api/backend-file'
import { UploadStateManager } from '@/features/upload/upload-state-manager'

const CHUNK_SIZE = 5 * 1024 * 1024
const POLL_INTERVAL_MS = 2_000
const POLL_TIMEOUT_MS = 10 * 60 * 1000

const readyStatuses = new Set<AssetStatus>(['ready'])
const processingStatuses = new Set<AssetStatus>([
  'uploading',
  'queued',
  'processing',
])

export interface UploadFileParams {
  type: AssetType
  file: File
  onProgress?: (progress: number) => void
  onChunkProgress?: (current: number, total: number) => void
  onUploadIdCreated?: (uploadId: string) => void
  onCancelCheck?: () => boolean
  resumeFromState?: boolean
  existingUploadId?: string
}

interface ResolvedUploadSession {
  uploadId: string
  uploadOffset: number
  uploadLength: number
  fileFingerprint: string
}

function wait(duration: number) {
  return new Promise((resolve) => {
    window.setTimeout(resolve, duration)
  })
}

function createFileFingerprint(file: File, type: AssetType) {
  return [type, file.name, file.size, file.lastModified].join(':')
}

function isUploadGone(error: any) {
  return error?.response?.status === 404
}

function getUploadedChunksCount(uploadOffset: number, chunkSize: number) {
  return Math.ceil(uploadOffset / chunkSize)
}

function getTotalChunks(fileSize: number, chunkSize: number) {
  return Math.max(1, Math.ceil(fileSize / chunkSize))
}

function updateUploadProgress(
  uploadOffset: number,
  uploadLength: number,
  onProgress?: (progress: number) => void,
) {
  const ratio = uploadLength > 0 ? uploadOffset / uploadLength : 0
  const percentage = Math.min(90, Math.max(0, Math.round(ratio * 90)))
  onProgress?.(percentage)
}

function updateChunkProgress(
  uploadOffset: number,
  fileSize: number,
  onChunkProgress?: (current: number, total: number) => void,
) {
  const totalChunks = getTotalChunks(fileSize, CHUNK_SIZE)
  const completedChunks = Math.min(
    totalChunks,
    getUploadedChunksCount(uploadOffset, CHUNK_SIZE),
  )

  onChunkProgress?.(completedChunks, totalChunks)
}

function saveUploadState(
  uploadId: string,
  file: File,
  type: AssetType,
  uploadOffset: number,
  uploadLength: number,
) {
  UploadStateManager.saveUploadState({
    uploadId,
    fileFingerprint: createFileFingerprint(file, type),
    fileName: file.name,
    fileSize: file.size,
    assetType: type,
    uploadLength,
    uploadOffset,
    progress: uploadLength > 0 ? Math.round((uploadOffset / uploadLength) * 100) : 0,
    timestamp: Date.now(),
  })
}

async function resolveUploadSession(
  params: Pick<UploadFileParams, 'existingUploadId' | 'file' | 'resumeFromState' | 'type'>,
) {
  const { existingUploadId, file, resumeFromState, type } = params
  const fileFingerprint = createFileFingerprint(file, type)
  const savedState = resumeFromState
    ? UploadStateManager.getUploadStateByFingerprint(fileFingerprint)
    : null
  const preferredUploadId = existingUploadId || savedState?.uploadId || null

  if (preferredUploadId) {
    try {
      const offsetResult = await getTusUploadOffset(preferredUploadId)
      saveUploadState(
        preferredUploadId,
        file,
        type,
        offsetResult.uploadOffset,
        offsetResult.uploadLength || file.size,
      )

      return {
        uploadId: preferredUploadId,
        uploadOffset: offsetResult.uploadOffset,
        uploadLength: offsetResult.uploadLength || file.size,
        fileFingerprint,
      } satisfies ResolvedUploadSession
    } catch (error) {
      if (!isUploadGone(error)) {
        throw error
      }

      UploadStateManager.removeUploadState(preferredUploadId)
    }
  }

  const createdSession = await createTusUpload({
    fileName: file.name,
    fileSize: file.size,
    assetType: type,
  })

  saveUploadState(
    createdSession.uploadId,
    file,
    type,
    createdSession.uploadOffset,
    createdSession.uploadLength || file.size,
  )

  return {
    uploadId: createdSession.uploadId,
    uploadOffset: createdSession.uploadOffset,
    uploadLength: createdSession.uploadLength || file.size,
    fileFingerprint,
  } satisfies ResolvedUploadSession
}

async function uploadFileChunks(
  session: ResolvedUploadSession,
  params: Pick<
    UploadFileParams,
    'file' | 'onCancelCheck' | 'onChunkProgress' | 'onProgress' | 'type'
  >,
) {
  const { file, onCancelCheck, onChunkProgress, onProgress, type } = params
  let nextOffset = session.uploadOffset

  updateUploadProgress(nextOffset, session.uploadLength, onProgress)
  updateChunkProgress(nextOffset, file.size, onChunkProgress)

  while (nextOffset < file.size) {
    if (onCancelCheck?.()) {
      await terminateTusUpload(session.uploadId).catch(() => undefined)
      UploadStateManager.removeUploadState(session.uploadId)
      throw new Error('上传已取消')
    }

    const chunk = file.slice(nextOffset, nextOffset + CHUNK_SIZE)
    const offsetResult = await uploadTusChunk(session.uploadId, chunk, nextOffset)

    nextOffset = offsetResult.uploadOffset
    saveUploadState(
      session.uploadId,
      file,
      type,
      nextOffset,
      offsetResult.uploadLength || file.size,
    )
    updateUploadProgress(nextOffset, file.size, onProgress)
    updateChunkProgress(nextOffset, file.size, onChunkProgress)
  }

  return nextOffset
}

async function pollUploadUntilReady(
  uploadId: string,
  params: Pick<UploadFileParams, 'file' | 'onCancelCheck' | 'onProgress' | 'type'>,
) {
  const { file, onCancelCheck, onProgress, type } = params
  const startedAt = Date.now()
  let pollCount = 0

  for (;;) {
    if (onCancelCheck?.()) {
      await terminateTusUpload(uploadId).catch(() => undefined)
      UploadStateManager.removeUploadState(uploadId)
      throw new Error('上传已取消')
    }

    const response = await getUploadStatus(uploadId)
    const uploadStatus = response.data

    saveUploadState(
      uploadId,
      file,
      type,
      uploadStatus.uploadOffset,
      uploadStatus.uploadLength || file.size,
    )

    if (readyStatuses.has(uploadStatus.status)) {
      if (!uploadStatus.assetId) {
        throw new Error('资产已就绪，但未返回 assetId')
      }

      const detailResponse = await getAssetDetail(uploadStatus.assetId)
      UploadStateManager.removeUploadState(uploadId)
      onProgress?.(100)
      return detailResponse.data
    }

    if (uploadStatus.status === 'failed') {
      UploadStateManager.removeUploadState(uploadId)
      throw new Error(uploadStatus.errorMessage || '后端解析失败，请检查文件内容')
    }

    if (uploadStatus.status === 'terminated') {
      UploadStateManager.removeUploadState(uploadId)
      throw new Error('上传已被终止')
    }

    if (!processingStatuses.has(uploadStatus.status)) {
      throw new Error(`未知上传状态: ${uploadStatus.status}`)
    }

    pollCount += 1
    onProgress?.(Math.min(99, 90 + pollCount))

    if (Date.now() - startedAt > POLL_TIMEOUT_MS) {
      throw new Error('文件处理超时，请稍后在列表中刷新查看状态')
    }

    await wait(POLL_INTERVAL_MS)
  }
}

function handleUploadError(error: any) {
  const statusCode = error?.response?.status
  const responseMessage =
    error?.response?.data?.msg || error?.response?.data?.message || ''

  const defaultMessages: Record<number, string> = {
    400: responseMessage || '请求参数错误，请检查文件类型和大小',
    401: '登录状态已失效，请重新登录',
    404: '上传会话不存在，请重新上传',
    409: responseMessage || '上传偏移不一致，已自动中断，请重新上传',
    413: '文件过大，超出服务端限制',
    415: responseMessage || '当前文件类型不受支持',
  }

  if (statusCode && defaultMessages[statusCode]) {
    return new Error(defaultMessages[statusCode])
  }

  if (statusCode && statusCode >= 500) {
    return new Error('服务器暂时不可用，请稍后重试')
  }

  return error instanceof Error ? error : new Error('上传失败，请稍后重试')
}

export async function uploadFile(params: UploadFileParams): Promise<AssetDetail> {
  const { file, onUploadIdCreated, onProgress, onChunkProgress, onCancelCheck, type } =
    params

  try {
    onProgress?.(0)

    const session = await resolveUploadSession(params)
    onUploadIdCreated?.(session.uploadId)

    await uploadFileChunks(session, {
      file,
      onCancelCheck,
      onChunkProgress,
      onProgress,
      type,
    })

    return await pollUploadUntilReady(session.uploadId, {
      file,
      onCancelCheck,
      onProgress,
      type,
    })
  } catch (error) {
    throw handleUploadError(error)
  }
}

export async function cancelFileUpload(uploadId: string) {
  await terminateTusUpload(uploadId)
  UploadStateManager.removeUploadState(uploadId)
}

export function isAssetReady(assetStatus?: UploadStatus['status']) {
  return assetStatus === 'ready'
}
