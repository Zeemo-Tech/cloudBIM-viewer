import { ref, type Ref } from 'vue'
import { createMeasurement, deleteMeasurement, listMeasurements, type MeasurementKind } from '@/api/backend-measurement'
import type { AnalysisArea, AnalysisDistance, AnalysisMode, AnalysisPoint } from '@/components/preview/ViewerAnalysisOverlay.vue'

/** 面板需要实现的测量相关方法（可选实现）。 */
interface MeasurementPanel {
  cancelAnalysis?: () => void
  clearAnalysis?: () => void
  removeAnalysisVisual?: (kind: 'point' | 'distance' | 'area', id: string) => void
}

export interface UseViewerMeasurementsOptions {
  /** 当前资产 ID，切换资产时重新加载测量记录。 */
  assetId: Ref<number | null | undefined>
  /** 当前查看器面板引用（模型或点云），用于取消/清除叠加显示。 */
  panelRef: Ref<MeasurementPanel | null | undefined>
}

/**
 * 查看器测量状态：叠加图层数据 + 后端测量记录的读写。
 *
 * 模型预览与点云预览共用同一套测量交互（测距 / 定位 / 面积），因此把它抽成
 * composable，避免两个页面包各维护一份。
 */
export function useViewerMeasurements(options: UseViewerMeasurementsOptions) {
  const analysisMode = ref<AnalysisMode>('none')
  const analysisPoint = ref<AnalysisPoint | null>(null)
  const analysisDistance = ref<AnalysisDistance | null>(null)
  const analysisAreas = ref<AnalysisArea[]>([])
  const analysisPoints = ref<AnalysisPoint[]>([])
  const analysisDistances = ref<AnalysisDistance[]>([])

  const measurementBackendIds = new Map<string, number>()
  let measurementLoadToken = 0

  function selectAnalysisMode(mode: AnalysisMode) {
    options.panelRef.value?.cancelAnalysis?.()
    analysisMode.value = analysisMode.value === mode ? 'none' : mode
  }

  function handleAnalysisModeExit() {
    analysisMode.value = 'none'
  }

  function clearAnalysis() {
    analysisMode.value = 'none'
    analysisPoint.value = null
    analysisDistance.value = null
    analysisAreas.value = []
    analysisPoints.value = []
    analysisDistances.value = []
    options.panelRef.value?.clearAnalysis?.()
    const backendIds = [...new Set(measurementBackendIds.values())]
    measurementBackendIds.clear()
    backendIds.forEach((id) => {
      void deleteMeasurement(id).catch((error) => {
        console.warn('[viewer] 删除测量记录失败', error)
      })
    })
  }

  function removeAnalysisById(kind: 'point' | 'distance' | 'area', id: string) {
    options.panelRef.value?.removeAnalysisVisual?.(kind, id)

    if (kind === 'point') {
      analysisPoints.value = analysisPoints.value.filter(
        (record, index) => (record.id || `point-${index}`) !== id,
      )
      analysisPoint.value = analysisPoints.value.at(-1) ?? null
    }
    if (kind === 'distance') {
      analysisDistances.value = analysisDistances.value.filter(
        (record, index) => (record.id || `distance-${index}`) !== id,
      )
      analysisDistance.value = analysisDistances.value.at(-1) ?? null
    }
    if (kind === 'area') {
      analysisAreas.value = analysisAreas.value.filter(
        (record, index) => (record.id || `area-${index}`) !== id,
      )
    }

    const backendId = measurementBackendIds.get(id)
    measurementBackendIds.delete(id)
    if (backendId !== undefined) {
      void deleteMeasurement(backendId).catch((error) => {
        console.warn('[viewer] 删除测量记录失败', error)
      })
    }
  }

  function hasMeasurement(kind: MeasurementKind, id: string) {
    if (kind === 'locate') return analysisPoints.value.some((record) => record.id === id)
    if (kind === 'distance') return analysisDistances.value.some((record) => record.id === id)
    return analysisAreas.value.some((record) => record.id === id)
  }

  async function persistMeasurement(kind: MeasurementKind, payload: unknown) {
    const assetId = options.assetId.value
    if (!assetId) return
    const localId = typeof payload === 'object' && payload && 'id' in payload
      ? String((payload as { id?: unknown }).id ?? '')
      : ''
    try {
      const response = await createMeasurement(assetId, kind, payload)
      if (!localId) return
      if (hasMeasurement(kind, localId)) {
        measurementBackendIds.set(localId, response.data.id)
      } else {
        void deleteMeasurement(response.data.id).catch(() => undefined)
      }
    } catch (error) {
      console.warn('[viewer] 保存测量记录失败', error)
    }
  }

  function handleAnalysisPoint(point: AnalysisPoint) {
    analysisPoint.value = point
    analysisPoints.value = [...analysisPoints.value, point]
    void persistMeasurement('locate', point)
  }

  function handleAnalysisDistance(distance: AnalysisDistance) {
    analysisDistance.value = distance
    analysisDistances.value = [...analysisDistances.value, distance]
    void persistMeasurement('distance', distance)
  }

  function handleAnalysisArea(area: AnalysisArea) {
    analysisAreas.value = [...analysisAreas.value, area]
    void persistMeasurement('area', area)
  }

  async function loadMeasurements() {
    const assetId = options.assetId.value
    const token = ++measurementLoadToken
    analysisPoint.value = null
    analysisDistance.value = null
    analysisAreas.value = []
    analysisPoints.value = []
    analysisDistances.value = []
    measurementBackendIds.clear()
    if (!assetId) return

    try {
      const response = await listMeasurements(assetId)
      if (token !== measurementLoadToken) return
      response.data.forEach((record) => {
        const payload = record.payload && typeof record.payload === 'object'
          ? { ...(record.payload as Record<string, unknown>) }
          : {}
        const id = typeof payload.id === 'string' && payload.id
          ? payload.id
          : `${record.kind}-${record.id}`
        measurementBackendIds.set(id, record.id)
        if (record.kind === 'locate') analysisPoints.value.push({ ...payload, id } as AnalysisPoint)
        if (record.kind === 'distance') analysisDistances.value.push({ ...payload, id } as AnalysisDistance)
        if (record.kind === 'area') analysisAreas.value.push({ ...payload, id } as AnalysisArea)
      })
      analysisPoint.value = analysisPoints.value.at(-1) ?? null
      analysisDistance.value = analysisDistances.value.at(-1) ?? null
    } catch (error) {
      console.warn('[viewer] 读取测量记录失败', error)
    }
  }

  /** 退出测量模式并清空本地叠加层，不删除后端记录（用于切换资产）。 */
  function resetAnalysisState() {
    analysisMode.value = 'none'
    analysisPoint.value = null
    analysisDistance.value = null
    analysisAreas.value = []
    analysisPoints.value = []
    analysisDistances.value = []
    measurementBackendIds.clear()
  }

  return {
    analysisMode,
    analysisPoint,
    analysisDistance,
    analysisAreas,
    analysisPoints,
    analysisDistances,
    selectAnalysisMode,
    handleAnalysisModeExit,
    clearAnalysis,
    removeAnalysisById,
    handleAnalysisPoint,
    handleAnalysisDistance,
    handleAnalysisArea,
    loadMeasurements,
    resetAnalysisState,
  }
}
