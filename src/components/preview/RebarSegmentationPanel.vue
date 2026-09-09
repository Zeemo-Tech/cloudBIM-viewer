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
  type RebarPointVisibilityCategory,
  type RebarRole,
  isRebarV5Result,
} from '@/api/backend-rebar'
import type { PointcloudColorMode } from './UnifiedViewer3D.vue'
import { legendItems, rebarSemanticColor, validateVisualization } from '@/features/rebar-visualization'

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
const elapsedSeconds = ref(0)
let elapsedTimer: ReturnType<typeof setInterval> | null = null
const elapsedLabel = computed(() => `${Math.floor(elapsedSeconds.value / 60)}:${String(elapsedSeconds.value % 60).padStart(2, '0')}`)
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
const ownershipReviewEnabled = ref(false)
const visibilityCollapsed = ref(true)
const collapsedVisibilityGroups = reactive<Record<string, boolean>>({})
const pointVisibility = reactive<Partial<Record<RebarPointVisibilityCategory, boolean>>>({
  unknown: true, table: true, noise: true, fixtureUnknown: true, fixtureSquareTube: true,
  fixturePlate: true, fixtureBolt: true, rebarUnresolved: true, rebarPlanar: true, rebarWeb: true,
})
const visibleRebarRoles = computed<Partial<Record<RebarRole, boolean>>>(() => ({
  unresolved: pointVisibility.rebarUnresolved !== false,
  planar: pointVisibility.rebarPlanar !== false,
  web: pointVisibility.rebarWeb !== false,
}))
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
    .filter((name) => properties[name] && name !== 'ownership_review_enabled')
    .map((name) => ({ name, property: properties[name] }))
})

const supportsOwnershipReview = computed(() => Boolean(selectedAlgorithm.value?.parameterSchema?.properties?.ownership_review_enabled))
const visibilityGroups = computed(() => {
  const summary = latest.value?.summary
  const scenes = summary?.sceneClassCounts ?? latest.value?.summary.rawSource?.sceneClassCounts ?? {}
  const fixtures = summary?.fixtureKindCounts
  const roles = summary?.rebarRoleCounts
  const colors = latest.value?.visualization
  return [
    { title: '场景', rows: [
      { key: 'unknown' as const, label: '未知', color: colors?.colors.unknown ?? '#64748b', count: scenes.unknown ?? 0 },
      { key: 'table' as const, label: '台面', color: colors?.colors.table ?? '#94a3b8', count: scenes.table ?? 0 },
      { key: 'noise' as const, label: '噪声', color: colors?.colors.noise ?? '#d946ef', count: scenes.noise ?? 0 },
    ] },
    { title: '夹具', rows: [
      { key: 'fixtureUnknown' as const, label: '未细分夹具', color: rebarSemanticColor(colors, 'fixtureUnknown'), count: fixtures?.unknown ?? (scenes.fixture ?? 0) },
      { key: 'fixtureSquareTube' as const, label: '方管', color: rebarSemanticColor(colors, 'fixtureSquareTube'), count: fixtures?.squareTube ?? 0 },
      { key: 'fixturePlate' as const, label: '夹持板', color: rebarSemanticColor(colors, 'fixturePlate'), count: fixtures?.plate ?? 0 },
      { key: 'fixtureBolt' as const, label: '螺栓', color: rebarSemanticColor(colors, 'fixtureBolt'), count: fixtures?.bolt ?? 0 },
    ] },
    { title: '钢筋', rows: [
      { key: 'rebarUnresolved' as const, label: '待判定', color: rebarSemanticColor(colors, 'rebarUnresolved'), count: roles?.unresolved ?? (scenes.rebar ?? 0) },
      { key: 'rebarPlanar' as const, label: '平面筋', color: rebarSemanticColor(colors, 'rebarPlanar'), count: roles?.planar ?? 0 },
      { key: 'rebarWeb' as const, label: '斜腹杆', color: rebarSemanticColor(colors, 'rebarWeb'), count: roles?.web ?? 0 },
    ] },
  ]
})

function toggleVisibilityGroup(title: string) {
  collapsedVisibilityGroups[title] = !collapsedVisibilityGroups[title]
}

const modeOptions = computed<Array<{ value: PointcloudColorMode; label: string }>>(() => {
  const options: Array<{ value: PointcloudColorMode; label: string }> = [
    { value: 'rgb', label: '原色' },
    { value: 'intensity', label: '强度' },
  ]
  const capabilities = latest.value?.capabilities
  if (capabilities?.class) options.push({ value: 'rebar-class', label: '类别' })
  if (capabilities?.direction) options.push({ value: 'rebar-direction', label: '方向' })
  if (capabilities?.instance) options.push({ value: 'rebar-instance', label: '实例' })
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
  ownershipReviewEnabled.value = false
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
  ownershipReviewEnabled.value = value.effectiveParameters.ownership_review_enabled === true
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
  if (supportsOwnershipReview.value) result.ownership_review_enabled = ownershipReviewEnabled.value
  return result
}

async function compute(force = false) {
  if (computing.value) return
  computing.value = true
  errorMessage.value = ''
  const startedAt = Date.now()
  elapsedSeconds.value = 0
  elapsedTimer = setInterval(() => { elapsedSeconds.value = Math.floor((Date.now() - startedAt) / 1000) }, 1000)
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
    if (elapsedTimer) clearInterval(elapsedTimer)
    elapsedTimer = null
    computing.value = false
  }
}

onBeforeUnmount(() => { if (elapsedTimer) clearInterval(elapsedTimer) })

watch(selectedAlgorithmId, initializeParameters, { flush: 'sync' })
watch(() => props.mode, (mode) => { selectedMode.value = mode })
watch(() => props.assetId, loadState)
watch(() => props.selectedIntersectionId, (id) => {
  selectedIntersection.value = id ?? null
})
watch([instances, intersections, selectedInstance, selectedIntersection, showCenterlines, showIntersections, hideFixtures, pointVisibility], () => {
  emit('inspection-change', { instances: instances.value, intersections: intersections.value,
    selectedId: selectedInstance.value, selectedIntersectionId: selectedIntersection.value,
    showCenterlines: showCenterlines.value, showIntersections: showIntersections.value, hideFixtures: hideFixtures.value,
    pointVisibility: { ...pointVisibility }, visibleRebarRoles: visibleRebarRoles.value })
}, { deep: true })
onMounted(loadState)
onBeforeUnmount(() => { ++loadToken; ++detailToken })
</script>

<template>
  <section class="rebar-panel" aria-label="点云分析">
    <div class="rebar-panel__head">
      <div>
        <span class="rebar-panel__eyebrow">POINT CLOUD</span>
        <h3>点云分析</h3>
      </div>
      <span class="rebar-panel__badge">{{ latest ? '结果已保存' : loading ? '读取中' : '待运行' }}</span>
    </div>

    <div v-if="errorMessage" class="rebar-panel__error">{{ errorMessage }}</div>

    <div v-if="latest" class="rebar-panel__summary">
      <span><strong>{{ latest.summary.rawSource?.instanceCount ?? latest.summary.instanceCount }}</strong> 个实例</span>
      <span><strong>{{ latest.summary.directionCount }}</strong> 个方向</span>
      <span v-if="isRebarV5Result(latest)"><strong>{{ latest.summary.intersectionCount ?? intersections.length }}</strong> 个交点</span>
      <span v-if="rebarRatio !== null"><strong>{{ rebarRatio.toFixed(1) }}%</strong> 钢筋点</span>
    </div>

    <div class="rebar-panel__run">
      <label v-if="supportsOwnershipReview" class="rebar-panel__ownership">
        <span><strong>归属复核</strong><small>默认关闭，调试分类时无需启用</small></span>
        <span class="rebar-panel__switch"><input v-model="ownershipReviewEnabled" aria-label="归属复核" type="checkbox" :disabled="computing" /><i /></span>
      </label>
      <button class="primary" type="button" :disabled="loading || computing" @click="compute(false)">
        {{ computing ? `计算中 · ${elapsedLabel}` : latest ? '按当前设置运行' : '开始分析' }}
      </button>
    </div>

    <p v-if="computing" class="rebar-panel__hint">正在分析点云{{ latest ? '，可继续查看上次结果' : '' }}。</p>

    <div v-if="latest" class="rebar-panel__modes" role="group" aria-label="颜色模式">
      <button v-for="mode in modeOptions" :key="mode.value" type="button" :class="{ active: selectedMode === mode.value }" @click="setMode(mode.value)">{{ mode.label }}</button>
    </div>

    <div v-if="latest" class="rebar-panel__visibility" aria-label="点类别可见性">
      <button
        class="rebar-panel__section-toggle"
        type="button"
        :aria-expanded="!visibilityCollapsed"
        aria-controls="rebar-point-visibility-groups"
        @click="visibilityCollapsed = !visibilityCollapsed"
      >
        <strong>点类别可见性</strong>
        <span>{{ visibilityCollapsed ? '展开' : '收起' }}</span>
      </button>

      <div v-show="!visibilityCollapsed" id="rebar-point-visibility-groups" class="rebar-panel__visibility-groups">
        <div v-for="group in visibilityGroups" :key="group.title" class="rebar-panel__visibility-group">
          <button
            class="rebar-panel__group-toggle"
            type="button"
            :aria-expanded="!collapsedVisibilityGroups[group.title]"
            :aria-controls="`rebar-visibility-${group.title}`"
            @click="toggleVisibilityGroup(group.title)"
          >
            <strong>{{ group.title }}</strong>
            <span>{{ collapsedVisibilityGroups[group.title] ? '展开' : '收起' }}</span>
          </button>
          <div v-show="!collapsedVisibilityGroups[group.title]" :id="`rebar-visibility-${group.title}`">
            <label v-for="row in group.rows" :key="row.key" class="rebar-panel__visibility-row" :title="`${pointVisibility[row.key] === false ? '显示' : '隐藏'}${row.label}`">
              <input v-model="pointVisibility[row.key]" type="checkbox" :aria-label="`显示${row.label}`" />
              <i :style="{ backgroundColor: row.color }" />
              <span>{{ row.label }}</span><small>{{ row.count.toLocaleString() }}</small>
              <b aria-hidden="true"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"><path d="M2 12s3.5-7 10-7 10 7 10 7-3.5 7-10 7S2 12 2 12Z" /><circle cx="12" cy="12" r="3" /><path v-if="pointVisibility[row.key] === false" d="M3 3 21 21" /></svg></b>
            </label>
          </div>
        </div>
      </div>
    </div>

    <p v-if="latest?.summary.rawSource" class="rebar-panel__hint">
      原始点 {{ latest.summary.rawSource.finitePointCount.toLocaleString() }} ·
      待确认 {{ latest.summary.rawSource.ambiguousPointCount.toLocaleString() }}
    </p>
    <div v-if="latest && ['rebar-visualization-v2', 'rebar-visualization-v3'].includes(latest.visualization?.schema ?? '')" class="rebar-panel__inspection">
      <label class="rebar-panel__overlay-toggle"><span>显示中心线</span><span class="rebar-panel__switch"><input v-model="showCenterlines" aria-label="显示中心线" type="checkbox" /><i /></span></label>
      <label v-if="isRebarV5Result(latest)" class="rebar-panel__overlay-toggle"><span>显示理论交点</span><span class="rebar-panel__switch"><input v-model="showIntersections" aria-label="显示理论交点" type="checkbox" /><i /></span></label>
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
      <small>虚线为推断连接；红色球体为中心线推算的理论交点。</small>
      <small v-if="detailError">{{ detailError }}</small>
    </div>

    <div v-if="legend.length" class="rebar-panel__legend" aria-label="钢筋分割图例">
      <span v-for="item in legend" :key="item.label">
        <i :style="{ backgroundColor: `rgb(${item.color.map((value) => Math.round(value * 255)).join(',')})` }" />
        {{ item.label }}
      </span>
    </div>

    <details v-if="advancedFields.length || algorithms.length > 1" class="rebar-panel__advanced">
      <summary>更多运行参数</summary>
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
  width: min(366px, calc(100% - 28px));
  max-height: calc(100% - 36px);
  box-sizing: border-box;
  overflow-x: hidden;
  overflow-y: auto;
  overscroll-behavior: contain;
  scrollbar-gutter: stable;
  scrollbar-color: rgb(100 116 139 / 65%) transparent;
  scrollbar-width: thin;
  padding: 14px;
  border: 1px solid rgb(148 163 184 / 28%);
  border-radius: 12px;
  color: #e5edf9;
  background: rgb(8 15 28 / 92%);
  box-shadow: 0 12px 32px rgb(2 6 23 / 34%);
}

.rebar-panel::-webkit-scrollbar { width: 7px; }
.rebar-panel::-webkit-scrollbar-track { background: transparent; }
.rebar-panel::-webkit-scrollbar-thumb { border-radius: 999px; background: rgb(100 116 139 / 65%); }
.rebar-panel::-webkit-scrollbar-thumb:hover { background: rgb(125 211 252 / 72%); }

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
.rebar-panel__status { color: #bae6fd; font-size: 12px; }
.rebar-panel__error { margin: 8px 0; color: #fecaca; font-size: 12px; }
.rebar-panel__summary { gap: 12px; padding: 8px 0; color: #cbd5e1; font-size: 12px; }
.rebar-panel__summary strong { color: #fff; }
.rebar-panel__run { display: flex; gap: 8px; margin: 10px 0; }
.rebar-panel__run .primary { flex: 0 0 auto; }
.rebar-panel__modes { display: grid; grid-template-columns: repeat(5, minmax(0, 1fr)); gap: 2px; margin: 10px 0; padding: 3px; border: 1px solid rgb(148 163 184 / 20%); border-radius: 9px; }
.rebar-panel__modes button { min-width: 0; padding: 0 4px; border: 0; background: transparent; font-size: 11px; }
.rebar-panel__legend { display: flex; flex-wrap: wrap; gap: 6px 10px; margin: 8px 0; color: #cbd5e1; font-size: 11px; }
.rebar-panel__legend span { display: inline-flex; align-items: center; gap: 4px; }
.rebar-panel__legend i { width: 9px; height: 9px; border-radius: 50%; }
.rebar-panel__visibility { display: grid; gap: 8px; padding: 8px 0; border-block: 1px solid rgb(148 163 184 / 16%); font-size: 12px; }
.rebar-panel__visibility-groups { display: grid; gap: 8px; }
.rebar-panel__visibility-group { display: grid; gap: 2px; }
.rebar-panel__section-toggle,
.rebar-panel__group-toggle { width: 100%; min-height: 28px !important; padding: 0 5px !important; display: flex; align-items: center; justify-content: space-between; border: 0 !important; border-radius: 6px !important; color: #cbd5e1; background: transparent !important; }
.rebar-panel__section-toggle:hover,
.rebar-panel__group-toggle:hover { color: #fff; background: rgb(30 41 59 / 70%) !important; }
.rebar-panel__section-toggle:focus-visible,
.rebar-panel__group-toggle:focus-visible { outline: 2px solid #67e8f9; outline-offset: 1px; }
.rebar-panel__section-toggle strong { color: #e5edf9; font-size: 12px; }
.rebar-panel__group-toggle strong { color: #cbd5e1; font-size: 11px; }
.rebar-panel__section-toggle span,
.rebar-panel__group-toggle span { color: #7dd3fc; font-size: 10px; font-weight: 500; }
.rebar-panel__visibility-row { display: grid; grid-template-columns: 10px minmax(100px, 1fr) 9ch 16px; align-items: center; gap: 8px; min-height: 29px; padding: 0 7px; border-radius: 6px; color: #cbd5e1; cursor: pointer; }
.rebar-panel__visibility-row:hover { background: rgb(30 41 59 / 70%); }
.rebar-panel__visibility-row:focus-within { outline: 1px solid #22d3ee; outline-offset: 1px; }
.rebar-panel__visibility-row input { position: absolute; opacity: 0; }
.rebar-panel__visibility-row i { width: 8px; height: 8px; border-radius: 50%; }
.rebar-panel__visibility-row small { color: #7f91aa; font-variant-numeric: tabular-nums; text-align: right; }
.rebar-panel__visibility-row b { display: flex; color: #67e8f9; }
.rebar-panel__visibility-row svg { width: 16px; height: 16px; }
.rebar-panel__visibility-row input:not(:checked) ~ span { color: #64748b; }
.rebar-panel__visibility-row input:not(:checked) ~ b { color: #64748b; }
.rebar-panel__ownership { display: flex; align-items: center; gap: 8px; flex: 1; justify-content: space-between; margin: 0; padding: 6px 8px; border: 1px solid rgb(148 163 184 / 20%); border-radius: 8px; }
.rebar-panel__ownership span { display: grid; gap: 2px; }
.rebar-panel__ownership strong { color: #e5edf9; font-size: 12px; }
.rebar-panel__ownership small { color: #94a3b8; font-size: 10px; }
.rebar-panel__switch { position: relative; flex: 0 0 auto; width: 30px; height: 18px; }
.rebar-panel__switch input { position: absolute; inset: 0; z-index: 1; width: 100%; min-height: 0; margin: 0; opacity: 0; cursor: pointer; }
.rebar-panel__switch i { display: block; width: 30px; height: 18px; border: 1px solid #475569; border-radius: 999px; background: #334155; transition: background .16s, border-color .16s; }
.rebar-panel__switch i::after { display: block; width: 12px; height: 12px; margin: 2px; border-radius: 50%; background: #e2e8f0; content: ''; transition: transform .16s; }
.rebar-panel__switch input:checked + i { border-color: #22d3ee; background: #0891b2; }
.rebar-panel__switch input:checked + i::after { transform: translateX(12px); }
.rebar-panel__switch input:focus-visible + i { outline: 2px solid #67e8f9; outline-offset: 2px; }
.rebar-panel__switch input:disabled + i { opacity: .5; }
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
.rebar-panel__inspection .rebar-panel__overlay-toggle { justify-content: space-between; padding: 5px 7px; border: 1px solid rgb(148 163 184 / 18%); border-radius: 7px; }
.rebar-panel__inspection select { min-width: 0; max-width: 200px; }
.rebar-panel__inspection small { color: #94a3b8; }
.rebar-panel__intersection-detail { margin: 0; color: #fde68a; font-size: 11px; }

@media (max-width: 760px) {
  .rebar-panel { top: 10px; right: 10px; max-height: calc(100% - 20px); }
  .rebar-panel__modes { grid-template-columns: repeat(3, minmax(0, 1fr)); }
}
</style>
  const colors = latest.value?.visualization
