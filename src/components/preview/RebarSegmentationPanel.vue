<script setup lang="ts">
import { computed, onMounted, onBeforeUnmount, reactive, ref, watch } from 'vue'
import {
  computeRebarSegmentation,
  getLatestRebarSegmentation,
  listRebarAlgorithms,
  getRebarAnalysis,
  type RebarInstance,
  type RebarInspection,
  type RebarAlgorithmDescriptor,
  type RebarParameterProperty,
  type RebarSegmentationResult,
  type RebarIntersection,
  isRebarV5Result,
} from '@/api/backend-rebar'
import type { PointcloudColorMode } from './UnifiedViewer3D.vue'
import { legendItems, validateVisualization } from '@/features/rebar-visualization'

const props = withDefaults(defineProps<{
  assetId: number
  mode?: PointcloudColorMode
  selectedIntersectionId?: number | null
}>(), {
  mode: 'rgb',
  selectedIntersectionId: null,
})

const emit = defineEmits<{
  (event: 'result-change', value: RebarSegmentationResult | null): void
  (event: 'mode-change', value: PointcloudColorMode): void
  (event: 'inspection-change', value: RebarInspection): void
}>()

const algorithms = ref<RebarAlgorithmDescriptor[]>([])
const selectedAlgorithmId = ref('geometric-v5')
const latest = ref<RebarSegmentationResult | null>(null)
const loading = ref(false)
const computing = ref(false)
const errorMessage = ref('')
const maxInputPoints = ref(200_000)
const voxelSizeMm = ref<number | null>(null)
const parameterValues = reactive<Record<string, number | string | boolean>>({})
const persistedParameterBaseline = ref<Record<string, unknown>>({})
const selectedMode = ref<PointcloudColorMode>('rgb')
const instances = ref<RebarInstance[]>([])
const selectedInstance = ref<number | null>(null)
const showCenterlines = ref(false)
const showIntersections = ref(true)
const hideFixtures = ref(false)
const intersections = ref<RebarIntersection[]>([])
const selectedIntersection = ref<number | null>(null)
const detailError = ref('')
let detailToken = 0
let loadToken = 0

const selectedAlgorithm = computed(() =>
  algorithms.value.find((item) => item.id === selectedAlgorithmId.value) ?? algorithms.value[0] ?? null,
)

const advancedFields = computed(() => {
  const descriptor = selectedAlgorithm.value
  const properties = descriptor?.parameterSchema?.properties ?? {}
  const hinted = descriptor?.uiHints?.advanced ?? Object.keys(properties)
  const preferred = descriptor?.uiHints?.order ?? hinted
  const ordered = [
    ...preferred.filter((name) => hinted.includes(name)),
    ...hinted.filter((name) => !preferred.includes(name)),
  ]
  return [...new Set(ordered)]
    .filter((name) => properties[name])
    .map((name) => ({ name, property: properties[name] }))
})

const modeOptions = computed<Array<{ value: PointcloudColorMode; label: string }>>(() => {
  const options: Array<{ value: PointcloudColorMode; label: string }> = [
    { value: 'rgb', label: '真彩' },
    { value: 'intensity', label: '强度' },
  ]
  const capabilities = latest.value?.capabilities
  if (capabilities?.class) options.push({ value: 'rebar-class', label: '钢筋类别' })
  if (capabilities?.direction) options.push({ value: 'rebar-direction', label: '钢筋方向' })
  if (capabilities?.instance) options.push({ value: 'rebar-instance', label: '钢筋实例' })
  return options
})

const rebarRatio = computed(() => {
  const summary = latest.value?.summary
  if (summary?.rawSource?.finitePointCount) return (summary.rawSource.sceneClassCounts.rebar / summary.rawSource.finitePointCount) * 100
  if (!summary?.totalPointCount) return null
  return (summary.rebarPointCount / summary.totalPointCount) * 100
})

const visualization = computed(() => validateVisualization(latest.value?.visualization))
const legend = computed(() => {
  if (!latest.value || !['rebar-class', 'rebar-direction', 'rebar-instance'].includes(selectedMode.value)) return []
  return legendItems(selectedMode.value as 'rebar-class' | 'rebar-direction' | 'rebar-instance', visualization.value, latest.value.summary)
})

function errorStatus(error: unknown) {
  return (error as { response?: { status?: number } })?.response?.status
}

function errorText(error: unknown) {
  const data = (error as { response?: { data?: unknown } })?.response?.data
  if (data && typeof data === 'object') {
    const code = (data as { msg?: unknown; errorCode?: unknown }).errorCode ??
      (data as { msg?: unknown }).msg
    if (code === 'resource_limit_exceeded') {
      return 'V5 完整邻域超出资源预算，本次未生成新结果'
    }
  }
  return error instanceof Error ? error.message : '钢筋分割请求失败'
}

function usesMillimetres(property: RebarParameterProperty) {
  return property.unit === 'm'
}

function displayNumber(_name: string, property: RebarParameterProperty, value: number) {
  return usesMillimetres(property) ? value * 1000 : value
}

function requestNumber(_name: string, property: RebarParameterProperty, value: number) {
  return usesMillimetres(property) ? value / 1000 : value
}

function fieldLabel(name: string, property: RebarParameterProperty) {
  return selectedAlgorithm.value?.uiHints?.labels?.[name] || property.title || name
}

function fieldUnit(name: string, property: RebarParameterProperty) {
  if (usesMillimetres(property)) return 'mm'
  return selectedAlgorithm.value?.uiHints?.units?.[name] || property.unit || ''
}

function initializeParameters() {
  persistedParameterBaseline.value = {}
  Object.keys(parameterValues).forEach((key) => delete parameterValues[key])
  advancedFields.value.forEach(({ name, property }) => {
    if (typeof property.default === 'number') {
      parameterValues[name] = displayNumber(name, property, property.default)
    } else if (typeof property.default === 'boolean' || typeof property.default === 'string') {
      parameterValues[name] = property.default
    }
  })
  const inputProperties = selectedAlgorithm.value?.inputOptionSchema?.properties
  const maxDefault = inputProperties?.maxInputPoints?.default
  const voxelDefault = inputProperties?.voxelSize?.default
  if (typeof maxDefault === 'number') maxInputPoints.value = maxDefault
  voxelSizeMm.value = typeof voxelDefault === 'number' ? voxelDefault * 1000 : null
}

function applyPersistedSettings(value: RebarSegmentationResult) {
  const selectedId = selectedAlgorithm.value?.id
  if (value.algorithm.id !== selectedId) return
  const properties = selectedAlgorithm.value?.parameterSchema?.properties ?? {}
  persistedParameterBaseline.value = Object.fromEntries(
    Object.entries(value.effectiveParameters).filter(([name]) => properties[name]),
  )
  const persistedMaxPoints = value.inputOptions?.maxInputPoints
  const persistedVoxelSize = value.inputOptions?.voxelSize
  if (typeof persistedMaxPoints === 'number') maxInputPoints.value = persistedMaxPoints
  voxelSizeMm.value = typeof persistedVoxelSize === 'number' ? persistedVoxelSize * 1000 : null
  advancedFields.value.forEach(({ name, property }) => {
    const persisted = value.effectiveParameters[name]
    if (typeof persisted === 'number') {
      parameterValues[name] = displayNumber(name, property, persisted)
    } else if (typeof persisted === 'boolean' || typeof persisted === 'string') {
      parameterValues[name] = persisted
    }
  })
}

function emitLatest(value: RebarSegmentationResult | null) {
  latest.value = value
  emit('result-change', value)
  instances.value = []
  intersections.value = []
  selectedInstance.value = null
  selectedIntersection.value = null
  detailError.value = ''
  const token = ++detailToken
  if (value && ['rebar-visualization-v2', 'rebar-visualization-v3'].includes(value.visualization?.schema ?? '')) {
    getRebarAnalysis(value.resultUrl).then((detail) => {
      if (token !== detailToken) return
      instances.value = detail.analysis.instances ?? []
      intersections.value = isRebarV5Result(value) ? detail.analysis.intersections ?? [] : []
    }).catch(() => { if (token === detailToken) detailError.value = '实例详情读取失败，可重新加载结果' })
  }
}

function setMode(mode: PointcloudColorMode) {
  if (!modeOptions.value.some((option) => option.value === mode)) mode = 'rgb'
  selectedMode.value = mode
  emit('mode-change', mode)
}

async function loadState() {
  const token = ++loadToken
  emitLatest(null)
  loading.value = true
  errorMessage.value = ''
  try {
    const [algorithmState, latestState] = await Promise.allSettled([
      listRebarAlgorithms(),
      getLatestRebarSegmentation(props.assetId).catch((error) => {
        if (errorStatus(error) === 404) return null
        throw error
      }),
    ])
    if (token !== loadToken) return
    const persisted = latestState.status === 'fulfilled' ? latestState.value?.data ?? null : null
    emitLatest(persisted)
    if (persisted) setMode(persisted.capabilities.class ? 'rebar-class' : 'rgb')

    if (algorithmState.status === 'fulfilled') {
      algorithms.value = algorithmState.value.data.algorithms ?? []
      if (algorithms.value.some((item) => item.id === 'geometric-v5')) {
        selectedAlgorithmId.value = 'geometric-v5'
      } else if (persisted && algorithms.value.some((item) => item.id === persisted.algorithm.id)) {
        selectedAlgorithmId.value = persisted.algorithm.id
      } else if (!algorithms.value.some((item) => item.id === selectedAlgorithmId.value)) {
        selectedAlgorithmId.value = algorithms.value[0]?.id ?? 'geometric-v5'
      }
      initializeParameters()
      if (persisted) applyPersistedSettings(persisted)
    } else if (persisted) {
      errorMessage.value = '算法服务暂不可用，仍可查看已保存结果'
    } else {
      throw algorithmState.reason
    }
    if (latestState.status === 'rejected' && !persisted) throw latestState.reason
  } catch (error) {
    if (token === loadToken) errorMessage.value = errorText(error)
  } finally {
    if (token === loadToken) loading.value = false
  }
}

function buildParameters() {
  const result: Record<string, unknown> = { ...persistedParameterBaseline.value }
  advancedFields.value.forEach(({ name, property }) => {
    const value = parameterValues[name]
    if (typeof value === 'number' && Number.isFinite(value)) {
      result[name] = requestNumber(name, property, value)
    } else if (typeof value === 'boolean' || typeof value === 'string') {
      result[name] = value
    }
  })
  return result
}

async function compute(force = false) {
  if (computing.value) return
  computing.value = true
  errorMessage.value = ''
  emitLatest(null)
  try {
    const response = await computeRebarSegmentation(
      props.assetId,
      {
        algorithm: selectedAlgorithmId.value,
        inputOptions: {
          maxInputPoints: maxInputPoints.value,
          ...(voxelSizeMm.value && voxelSizeMm.value > 0
            ? { voxelSize: voxelSizeMm.value / 1000 }
            : {}),
        },
        parameters: buildParameters(),
      },
      { force },
    )
    emitLatest(response.data)
    setMode(response.data.capabilities.class ? 'rebar-class' : 'rgb')
  } catch (error) {
    errorMessage.value = errorText(error)
  } finally {
    computing.value = false
  }
}

watch(selectedAlgorithmId, initializeParameters, { flush: 'sync' })
watch(() => props.mode, (mode) => { selectedMode.value = mode })
watch(() => props.assetId, loadState)
watch(() => props.selectedIntersectionId, (id) => {
  selectedIntersection.value = id ?? null
})
watch([instances, intersections, selectedInstance, selectedIntersection, showCenterlines, showIntersections, hideFixtures], () => {
  emit('inspection-change', { instances: instances.value, intersections: intersections.value,
    selectedId: selectedInstance.value, selectedIntersectionId: selectedIntersection.value,
    showCenterlines: showCenterlines.value, showIntersections: showIntersections.value, hideFixtures: hideFixtures.value })
})
onMounted(loadState)
onBeforeUnmount(() => { ++loadToken; ++detailToken })
</script>

<template>
  <section class="rebar-panel" aria-label="钢筋分割">
    <div class="rebar-panel__head">
      <div>
        <span class="rebar-panel__eyebrow">Rebar segmentation</span>
        <h3>钢筋分割</h3>
      </div>
      <span v-if="latest" class="rebar-panel__badge">已持久化</span>
    </div>

    <p class="rebar-panel__hint">几何识别结果仅供辅助检查，不能替代工程验收。</p>

    <div v-if="loading" class="rebar-panel__status">正在读取最新结果…</div>
    <div v-else-if="errorMessage" class="rebar-panel__error">{{ errorMessage }}</div>

    <div v-if="latest" class="rebar-panel__summary">
      <span><strong>{{ latest.summary.rawSource?.instanceCount ?? latest.summary.instanceCount }}</strong> 个实例</span>
      <span><strong>{{ latest.summary.directionCount }}</strong> 个方向</span>
      <span v-if="isRebarV5Result(latest)"><strong>{{ latest.summary.intersectionCount ?? intersections.length }}</strong> 个交点</span>
      <span v-if="rebarRatio !== null"><strong>{{ rebarRatio.toFixed(1) }}%</strong> 钢筋点</span>
    </div>

    <p v-if="latest?.summary.rawSource" class="rebar-panel__hint">
      原始点 {{ latest.summary.rawSource.finitePointCount.toLocaleString() }} ·
      待确认 {{ latest.summary.rawSource.ambiguousPointCount.toLocaleString() }}
    </p>
    <div v-if="latest && ['rebar-visualization-v2', 'rebar-visualization-v3'].includes(latest.visualization?.schema ?? '')" class="rebar-panel__inspection">
      <label><input v-model="showCenterlines" type="checkbox" /> 显示中心线</label>
      <label v-if="isRebarV5Result(latest)"><input v-model="showIntersections" type="checkbox" /> 显示交点</label>
      <label><input v-model="hideFixtures" type="checkbox" /> 隐藏夹具／围挡</label>
      <label>聚焦单根钢筋
        <select v-model="selectedInstance">
          <option :value="null">全部实例</option>
          <option v-for="instance in instances" :key="instance.id" :value="instance.id">钢筋 {{ instance.id }}{{ instance.designId ? ' · BIM 已关联' : '' }}</option>
        </select>
      </label>
      <label v-if="isRebarV5Result(latest)">交点详情
        <select v-model="selectedIntersection">
          <option :value="null">未选择</option>
          <option v-for="intersection in intersections" :key="intersection.id" :value="intersection.id">交点 {{ intersection.id }} · {{ intersection.angleDegrees.toFixed(1) }}°</option>
        </select>
      </label>
      <p v-if="selectedIntersection !== null && intersections.find((item) => item.id === selectedIntersection)" class="rebar-panel__intersection-detail">
        交点 {{ selectedIntersection }} · 角度 {{ intersections.find((item) => item.id === selectedIntersection)?.angleDegrees.toFixed(2) }}° · 残差 {{ intersections.find((item) => item.id === selectedIntersection)?.residual.toFixed(4) }}
      </p>
      <small>虚线表示推断连接，不代表扫描已观测到。</small>
      <small v-if="detailError">{{ detailError }}</small>
    </div>

    <div v-if="latest" class="rebar-panel__modes" role="group" aria-label="钢筋结果着色">
      <button
        v-for="mode in modeOptions"
        :key="mode.value"
        type="button"
        :class="{ active: selectedMode === mode.value }"
        @click="setMode(mode.value)"
      >
        {{ mode.label }}
      </button>
    </div>

    <div v-if="legend.length" class="rebar-panel__legend" aria-label="钢筋分割图例">
      <span v-for="item in legend" :key="item.label">
        <i :style="{ backgroundColor: `rgb(${item.color.map((value) => Math.round(value * 255)).join(',')})` }" />
        {{ item.label }}
      </span>
    </div>

    <details v-if="advancedFields.length || algorithms.length > 1" class="rebar-panel__advanced">
      <summary>高级参数</summary>
      <label v-if="algorithms.length > 1">
        <span>算法</span>
        <select v-model="selectedAlgorithmId" :disabled="computing">
          <option v-for="algorithm in algorithms" :key="algorithm.id" :value="algorithm.id">
            {{ algorithm.name }}
          </option>
        </select>
      </label>
      <label v-if="selectedAlgorithmId !== 'geometric-v5'">
        <span>最大检测点数</span>
        <input v-model.number="maxInputPoints" type="number" min="1000" step="1000" :disabled="computing" />
      </label>
      <label v-if="selectedAlgorithmId !== 'geometric-v5'">
        <span>体素尺寸</span>
        <span class="rebar-panel__input">
          <input v-model.number="voxelSizeMm" type="number" min="0.001" step="0.1" placeholder="自动" :disabled="computing" />
          <small>mm</small>
        </span>
      </label>
      <label v-for="field in advancedFields" :key="field.name">
        <span>{{ fieldLabel(field.name, field.property) }}</span>
        <span class="rebar-panel__input">
          <input
            v-if="field.property.type === 'boolean'"
            v-model="parameterValues[field.name]"
            type="checkbox"
            :disabled="computing"
          />
          <input
            v-else-if="field.property.type === 'number' || field.property.type === 'integer'"
            v-model.number="parameterValues[field.name]"
            type="number"
            :min="field.property.minimum === undefined ? undefined : displayNumber(field.name, field.property, field.property.minimum)"
            :max="field.property.maximum === undefined ? undefined : displayNumber(field.name, field.property, field.property.maximum)"
            :step="field.property.step === undefined ? 'any' : displayNumber(field.name, field.property, field.property.step)"
            :disabled="computing"
          />
          <input
            v-else
            v-model="parameterValues[field.name]"
            type="text"
            :disabled="computing"
          />
          <small v-if="fieldUnit(field.name, field.property)">{{ fieldUnit(field.name, field.property) }}</small>
        </span>
      </label>
    </details>

    <div class="rebar-panel__actions">
      <button class="primary" type="button" :disabled="loading || computing" @click="compute(false)">
        {{ computing ? '计算中…' : latest ? '按当前参数计算' : '开始钢筋分割' }}
      </button>
      <button v-if="latest" type="button" :disabled="computing" @click="compute(true)">重新计算</button>
    </div>
  </section>
</template>

<style scoped>
.rebar-panel {
  position: absolute;
  z-index: 36;
  top: 18px;
  right: 18px;
  width: min(340px, calc(100% - 36px));
  padding: 16px;
  border: 1px solid rgb(148 163 184 / 24%);
  border-radius: 16px;
  color: #e5edf9;
  background: rgb(8 15 28 / 92%);
  box-shadow: 0 18px 44px rgb(2 6 23 / 36%);
  backdrop-filter: blur(18px);
}

.rebar-panel__head,
.rebar-panel__actions,
.rebar-panel__summary,
.rebar-panel__modes,
.rebar-panel__advanced label,
.rebar-panel__input {
  display: flex;
  align-items: center;
}

.rebar-panel__head { justify-content: space-between; gap: 12px; }
.rebar-panel h3 { margin: 2px 0 0; font-size: 17px; }
.rebar-panel__eyebrow { color: #67e8f9; font-size: 10px; letter-spacing: .12em; text-transform: uppercase; }
.rebar-panel__badge { padding: 4px 8px; border-radius: 999px; color: #86efac; background: rgb(22 101 52 / 35%); font-size: 11px; }
.rebar-panel__hint { margin: 10px 0; color: #94a3b8; font-size: 12px; line-height: 1.5; }
.rebar-panel__status { color: #bae6fd; font-size: 12px; }
.rebar-panel__error { margin: 8px 0; color: #fecaca; font-size: 12px; }
.rebar-panel__summary { gap: 12px; padding: 8px 0; color: #cbd5e1; font-size: 12px; }
.rebar-panel__summary strong { color: #fff; }
.rebar-panel__modes { flex-wrap: wrap; gap: 6px; margin: 8px 0; }
.rebar-panel__legend { display: flex; flex-wrap: wrap; gap: 6px 10px; margin: 8px 0; color: #cbd5e1; font-size: 11px; }
.rebar-panel__legend span { display: inline-flex; align-items: center; gap: 4px; }
.rebar-panel__legend i { width: 9px; height: 9px; border-radius: 50%; }
.rebar-panel button,
.rebar-panel select,
.rebar-panel input { min-height: 32px; border: 1px solid rgb(148 163 184 / 28%); border-radius: 8px; color: inherit; background: rgb(15 23 42 / 72%); }
.rebar-panel button { padding: 0 10px; cursor: pointer; }
.rebar-panel button.active,
.rebar-panel button.primary { border-color: rgb(34 211 238 / 55%); background: rgb(8 145 178 / 34%); }
.rebar-panel button:disabled { cursor: wait; opacity: .55; }
.rebar-panel__advanced { margin-top: 10px; color: #cbd5e1; font-size: 12px; }
.rebar-panel__advanced summary { cursor: pointer; }
.rebar-panel__advanced label { justify-content: space-between; gap: 10px; margin-top: 8px; }
.rebar-panel__advanced input,
.rebar-panel__advanced select { width: 142px; padding: 0 8px; box-sizing: border-box; }
.rebar-panel__input { gap: 5px; }
.rebar-panel__input input { width: 110px; }
.rebar-panel__input input[type='checkbox'] { width: 20px; min-height: 20px; }
.rebar-panel__input small { width: 26px; color: #94a3b8; }
.rebar-panel__actions { gap: 8px; margin-top: 12px; }
.rebar-panel__inspection { display: grid; gap: 8px; margin: 12px 0; font-size: 12px; }
.rebar-panel__inspection label { display: flex; align-items: center; gap: 7px; }
.rebar-panel__inspection select { min-width: 0; max-width: 200px; }
.rebar-panel__inspection small { color: #94a3b8; }
.rebar-panel__intersection-detail { margin: 0; color: #fde68a; font-size: 11px; }

@media (max-width: 760px) {
  .rebar-panel { top: 10px; right: 10px; max-height: calc(100% - 20px); overflow: auto; }
}
</style>
