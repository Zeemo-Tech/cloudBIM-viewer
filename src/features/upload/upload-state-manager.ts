import type { AssetType } from '@/api/backend-file'

export interface UploadState {
  uploadId: string
  fileFingerprint: string
  fileName: string
  fileSize: number
  assetType: AssetType
  uploadLength: number
  uploadOffset: number
  progress: number
  timestamp: number
}

const STORAGE_KEY = 'cloudbim-viewer.pending-uploads'
const EXPIRY_DURATION = 7 * 24 * 60 * 60 * 1000

export class UploadStateManager {
  static saveUploadState(state: UploadState): void {
    try {
      const states = this.getAllUploadStates()
      const index = states.findIndex((item) => item.uploadId === state.uploadId)
      const nextState = { ...state, timestamp: Date.now() }

      if (index >= 0) {
        states[index] = nextState
      } else {
        states.push(nextState)
      }

      localStorage.setItem(STORAGE_KEY, JSON.stringify(states))
    } catch (error) {
      console.error('[UploadStateManager] 保存上传状态失败:', error)
    }
  }

  static getAllUploadStates(): UploadState[] {
    try {
      const raw = localStorage.getItem(STORAGE_KEY)
      if (!raw) {
        return []
      }

      const states = JSON.parse(raw) as UploadState[]
      const expiryTime = Date.now() - EXPIRY_DURATION
      return states.filter((state) => state.timestamp > expiryTime)
    } catch (error) {
      console.error('[UploadStateManager] 获取上传状态失败:', error)
      return []
    }
  }

  static getUploadState(uploadId: string): UploadState | null {
    return this.getAllUploadStates().find((state) => state.uploadId === uploadId) || null
  }

  static getUploadStateByFingerprint(fileFingerprint: string): UploadState | null {
    return (
      this.getAllUploadStates().find(
        (state) => state.fileFingerprint === fileFingerprint,
      ) || null
    )
  }

  static removeUploadState(uploadId: string): void {
    try {
      const states = this.getAllUploadStates().filter(
        (state) => state.uploadId !== uploadId,
      )
      localStorage.setItem(STORAGE_KEY, JSON.stringify(states))
    } catch (error) {
      console.error('[UploadStateManager] 删除上传状态失败:', error)
    }
  }

  static updateProgress(
    uploadId: string,
    progress: number,
    uploadOffset: number,
  ): void {
    const state = this.getUploadState(uploadId)
    if (!state) {
      return
    }

    state.progress = progress
    state.uploadOffset = uploadOffset
    state.timestamp = Date.now()
    this.saveUploadState(state)
  }
}
