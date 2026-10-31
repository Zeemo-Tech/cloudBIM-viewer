<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, useId, watch } from 'vue'
import type {
  MeasurementAssistState,
  MeasurementSettings,
  MeasurementTool,
  XYZ,
} from '../../viewer/measurement/types'

const props = defineProps<{ state: MeasurementAssistState }>()
const emit = defineEmits<{
  settings: [patch: Partial<MeasurementSettings>]
  tool: [tool: MeasurementTool]
  undo: []
  reset: []
  flip: []
  faceOn: []
  restoreView: []
  clearFrame: []
  coordinates: [payload: { point: XYZ; relative: boolean }]
}>()

const panelId = useId()
const overlayRef = ref<HTMLDivElement | null>(null)
const feedbackRef = ref<HTMLDivElement | null>(null)
const lensRef = ref<HTMLCanvasElement | null>(null)
const expanded = ref(true)
const viewport = ref({ width: 0, height: 0 })
const feedbackSize = ref({ width: 232, height: 220 })
const coordinateMode = ref<'absolute' | 'relative'>('absolute')
const coordinateValues = ref<[string | number, string | number, string | number]>(['', '', ''])
const coordinateError = ref('')
const lensReady = ref(false)
let resizeObserver: ResizeObserver | null = null

const tools: { value: MeasurementTool; label: string }[] = [
  { value: 'pick', label: '精确取点' },
  { value: 'plane', label: '局部平面与法线' },
  { value: 'point-plane', label: '点到平面距离' },
  { value: 'plane-plane', label: '平面间距' },
  { value: 'edge', label: '提取边缘' },
  { value: 'corner', label: '提取角点' },
  { value: 'angle', label: '三点角度' },
  { value: 'line-angle', label: '线线夹角（四点）' },
  { value: 'plane-angle', label: '平面夹角' },
  { value: 'cylinder', label: '圆柱拟合' },
  { value: 'cylinder-distance', label: '两圆柱轴距' },
  { value: 'frame', label: '建立局部坐标系' },
]
const axes = ['X', 'Y', 'Z'] as const
const canUseDelta = computed(() => props.state.canUndo || props.state.hasFrame)
const coordinateValid = computed(() => coordinateValues.value.every((value) => String(value).trim() !== '' && Number.isFinite(Number(value))))
const fitQuality = computed(() => {
  const geometry = props.state.preview
  if (!geometry) return null
  if ('planes' in geometry) {
    return {
      label: geometry.kind === 'edge' ? '边缘' : '角点',
      count: geometry.planes.reduce((sum, plane) => sum + plane.inlierCount, 0),
      total: geometry.planes.reduce((sum, plane) => sum + plane.pointCount, 0),
      rmse: geometry.rmse,
      coverage: null,
    }
  }
  return {
    label: geometry.kind === 'cylinder' ? '圆柱' : '平面',
    count: geometry.inlierCount,
    total: geometry.pointCount,
    rmse: geometry.rmse,
    coverage: geometry.kind === 'cylinder' ? geometry.angularCoverage : null,
  }
})
const hoverVisible = computed(() => {
  const hover = props.state.hover
  return !!hover && Number.isFinite(hover.x) && Number.isFinite(hover.y)
    && hover.x >= 0 && hover.y >= 0
    && hover.x <= viewport.value.width && hover.y <= viewport.value.height
    && viewport.value.width > 0 && viewport.value.height > 0
})
const feedbackStyle = computed(() => {
  const hover = props.state.hover
  if (!hover) return {}
  const margin = 8
  const width = Math.min(feedbackSize.value.width, Math.max(0, viewport.value.width - margin * 2))
  const height = Math.min(feedbackSize.value.height, Math.max(0, viewport.value.height - margin * 2))
  const preferredX = hover.x + 24 + width <= viewport.value.width - margin
    ? hover.x + 24 : hover.x - width - 24
  const preferredY = hover.y - height / 2
  return {
    left: `${Math.max(margin, Math.min(preferredX, viewport.value.width - width - margin))}px`,
    top: `${Math.max(margin, Math.min(preferredY, viewport.value.height - height - margin))}px`,
  }
})

function measureOverlay() {
  const overlay = overlayRef.value
  const feedback = feedbackRef.value
  if (overlay) viewport.value = { width: overlay.clientWidth, height: overlay.clientHeight }
  if (feedback) feedbackSize.value = { width: feedback.offsetWidth, height: feedback.offsetHeight }
}

function observeOverlay() {
  resizeObserver?.disconnect()
  if (overlayRef.value) resizeObserver?.observe(overlayRef.value)
  if (feedbackRef.value) resizeObserver?.observe(feedbackRef.value)
  measureOverlay()
}

onMounted(() => {
  if (typeof ResizeObserver !== 'undefined') resizeObserver = new ResizeObserver(measureOverlay)
  observeOverlay()
})
watch([overlayRef, feedbackRef], observeOverlay, { flush: 'post' })
onBeforeUnmount(() => resizeObserver?.disconnect())
watch(() => props.state.active, () => {
  lensReady.value = false
  coordinateError.value = ''
})
watch(canUseDelta, (available) => {
  if (!available) coordinateMode.value = 'absolute'
})

function changeSelect(key: 'sourceFilter' | 'snap' | 'axis', event: Event) {
  const value = (event.target as HTMLSelectElement).value
  // Native option values are the shared controller's settings contract.
  emit('settings', { [key]: value } as Partial<MeasurementSettings>)
}

function changePositive(key: 'radius' | 'tolerance', event: Event, multiplier = 1) {
  const input = event.target as HTMLInputElement
  const value = input.valueAsNumber / multiplier
  if (!Number.isFinite(value) || value <= 0 || !input.checkValidity()) {
    input.reportValidity()
    input.value = String(props.state.settings[key] * multiplier)
    return
  }
  emit('settings', { [key]: value })
}

function submitCoordinates() {
  if (!coordinateValid.value) {
    coordinateError.value = '请填写完整的 X、Y、Z 数值。'
    return
  }
  const relative = coordinateMode.value === 'relative'
  if (relative && !canUseDelta.value) {
    coordinateError.value = '先确认一个点或建立局部坐标系，再输入增量。'
    return
  }
  coordinateError.value = ''
  emit('coordinates', {
    point: coordinateValues.value.map(Number) as XYZ,
    relative,
  })
}

function formatNumber(value: number, digits = 3) {
  return Number.isFinite(value) ? value.toFixed(digits) : '—'
}

/** Called by the viewer immediately after its own render; this component has no render loop. */
function drawLens(source: HTMLCanvasElement) {
  const canvas = lensRef.value
  const context = canvas?.getContext('2d')
  if (!canvas || !context) {
    lensReady.value = false
    return
  }

  const size = 144
  const zoom = 3
  const displayRatio = canvas.ownerDocument.defaultView?.devicePixelRatio || 1
  const dpr = Number.isFinite(displayRatio) && displayRatio > 0 ? displayRatio : 1
  const backingSize = Math.round(size * dpr)
  if (canvas.width !== backingSize || canvas.height !== backingSize) {
    canvas.width = backingSize
    canvas.height = backingSize
  }
  // Use the actual rounded backing ratio, including fractional display scaling.
  const outputScale = backingSize / size
  context.setTransform(outputScale, 0, 0, outputScale, 0, 0)
  context.clearRect(0, 0, size, size)
  const hover = props.state.hover
  if (!props.state.active || !props.state.settings.magnifier || !hover || !source
    || !source.width || !source.height || typeof source.getBoundingClientRect !== 'function') {
    lensReady.value = false
    return
  }

  const bounds = source.getBoundingClientRect()
  if (!(bounds.width > 0 && bounds.height > 0) || !Number.isFinite(hover.x) || !Number.isFinite(hover.y)
    || hover.x < 0 || hover.y < 0 || hover.x > bounds.width || hover.y > bounds.height) {
    lensReady.value = false
    return
  }

  const scaleX = source.width / bounds.width
  const scaleY = source.height / bounds.height
  const cropSize = size / zoom
  const left = (hover.x - cropSize / 2) * scaleX
  const top = (hover.y - cropSize / 2) * scaleY
  const sx = Math.max(0, left)
  const sy = Math.max(0, top)
  const sw = Math.min(source.width, left + cropSize * scaleX) - sx
  const sh = Math.min(source.height, top + cropSize * scaleY) - sy
  if (!(sw > 0 && sh > 0)) {
    lensReady.value = false
    return
  }

  try {
    context.imageSmoothingEnabled = false
    context.drawImage(source, sx, sy, sw, sh,
      (sx - left) / scaleX * zoom, (sy - top) / scaleY * zoom,
      sw / scaleX * zoom, sh / scaleY * zoom)
    lensReady.value = true
  } catch {
    // A disposed or temporarily unavailable renderer must never leave a stale crop.
    context.clearRect(0, 0, size, size)
    lensReady.value = false
  }
}

defineExpose({ drawLens })
</script>

<template>
  <div v-if="state.active" ref="overlayRef" class="measurement-assist">
    <section
      class="measurement-assist__panel"
      aria-label="测量辅助"
      @pointerdown.stop
      @pointerup.stop
      @pointermove.stop
      @mousedown.stop
      @mouseup.stop
      @click.stop
      @dblclick.stop
      @wheel.stop
      @keydown.stop
      @keyup.stop
      @contextmenu.stop
    >
      <header class="measurement-assist__header">
        <h3>{{ state.advanced ? '工程测量' : '测量辅助' }}</h3>
        <span class="measurement-assist__exit">Esc 退出</span>
        <button
          type="button"
          class="measurement-assist__disclosure"
          :aria-expanded="expanded"
          :aria-controls="`${panelId}-body`"
          @click="expanded = !expanded"
        >{{ expanded ? '收起' : '展开' }}</button>
      </header>

      <div v-show="expanded" :id="`${panelId}-body`" class="measurement-assist__body">
        <label v-if="state.advanced" class="measurement-assist__field">
          <span>测量方式</span>
          <select aria-label="测量方式" :value="state.tool" @change="emit('tool', ($event.target as HTMLSelectElement).value as MeasurementTool)">
            <option v-for="tool in tools" :key="tool.value" :value="tool.value">{{ tool.label }}</option>
          </select>
        </label>

        <div class="measurement-assist__row">
          <label class="measurement-assist__field">
            <span>取点来源</span>
            <select aria-label="取点来源" :value="state.settings.sourceFilter" @change="changeSelect('sourceFilter', $event)">
              <option value="all">所有来源</option>
              <option value="pointcloud">仅点云</option>
              <option value="bim">仅 BIM</option>
            </select>
          </label>
          <label class="measurement-assist__field">
            <span>捕捉方式</span>
            <select aria-label="捕捉方式" :value="state.settings.snap" @change="changeSelect('snap', $event)">
              <option value="point">采样点 / 表面</option>
              <option value="vertex">模型顶点 / 中点</option>
              <option value="edge">边缘</option>
              <option value="corner">角点</option>
            </select>
          </label>
        </div>

        <button
          type="button"
          class="measurement-assist__magnifier"
          :class="{ 'is-selected': state.settings.magnifier }"
          :aria-pressed="state.settings.magnifier"
          @click="emit('settings', { magnifier: !state.settings.magnifier })"
        >
          <span>放大镜 3×</span>
          <span>{{ state.settings.magnifier ? '已开启' : '已关闭' }}</span>
        </button>

        <template v-if="state.active">
          <div v-if="state.advanced || state.settings.snap === 'edge' || state.settings.snap === 'corner'" class="measurement-assist__row">
            <label class="measurement-assist__field">
              <span>邻域半径 <span class="measurement-assist__unit">m</span></span>
              <input aria-label="邻域半径，米" type="number" min="0.005" max="10" step="any" :value="state.settings.radius" @change="changePositive('radius', $event)" />
            </label>
            <label class="measurement-assist__field">
              <span>拟合容差 <span class="measurement-assist__unit">mm</span></span>
              <input aria-label="拟合容差，毫米" type="number" min="0.05" step="any" :value="Number((state.settings.tolerance * 1000).toPrecision(8))" @change="changePositive('tolerance', $event, 1000)" />
            </label>
          </div>

          <label class="measurement-assist__field measurement-assist__axis">
            <span>{{ state.hasFrame ? '沿局部坐标轴' : '沿世界坐标轴（Y 向上）' }}</span>
            <select aria-label="轴向约束" :value="state.settings.axis" @change="changeSelect('axis', $event)">
              <option value="free">自由取点</option>
              <option value="x">锁定 X 轴</option>
              <option value="y">锁定 Y 轴</option>
              <option value="z">锁定 Z 轴</option>
            </select>
          </label>

          <div v-if="fitQuality" class="measurement-assist__quality" aria-label="拟合质量">
            <span>{{ fitQuality.label }} · {{ fitQuality.count }} / {{ fitQuality.total }} 点</span>
            <span>均方根误差 {{ formatNumber(fitQuality.rmse * 1000, 2) }} mm</span>
            <span v-if="fitQuality.coverage !== null">覆盖圆弧 {{ formatNumber(fitQuality.coverage, 1) }}°</span>
          </div>

          <div v-if="state.advanced" class="measurement-assist__actions" aria-label="参考平面与视角">
            <button type="button" :disabled="!state.hasPlane" title="反转参考平面的法线方向" @click="emit('flip')">反转法线</button>
            <button type="button" :disabled="!state.hasPlane" title="沿参考平面法线观察" @click="emit('faceOn')">正对平面</button>
            <button v-if="state.canRestoreView" type="button" @click="emit('restoreView')">恢复视角</button>
          </div>

          <div v-if="state.hasFrame" class="measurement-assist__frame">
            <span>已使用局部坐标系</span>
            <button type="button" aria-label="清除局部坐标系" @click="emit('clearFrame')">清除</button>
          </div>

          <details class="measurement-assist__coordinates">
            <summary>精确坐标 / 增量</summary>
            <form @submit.prevent="submitCoordinates">
              <label class="measurement-assist__field">
                <span>输入方式 <span class="measurement-assist__unit">m</span></span>
                <select v-model="coordinateMode" aria-label="坐标输入方式" @change="coordinateError = ''">
                  <option value="absolute">{{ state.hasFrame ? '局部坐标' : '全局坐标' }}</option>
                  <option value="relative" :disabled="!canUseDelta">相对上一点的增量</option>
                </select>
              </label>
              <div class="measurement-assist__xyz">
                <label v-for="(axis, index) in axes" :key="axis" class="measurement-assist__field">
                  <span>{{ coordinateMode === 'relative' ? `Δ${axis}` : axis }}</span>
                  <input
                    v-model="coordinateValues[index]"
                    type="number"
                    step="any"
                    required
                    :aria-label="`${coordinateMode === 'relative' ? '增量' : '坐标'} ${axis}，米`"
                    :aria-invalid="coordinateError ? 'true' : undefined"
                    :aria-describedby="coordinateError ? `${panelId}-coordinate-error` : undefined"
                    @input="coordinateError = ''"
                  />
                </label>
              </div>
              <p v-if="coordinateError" :id="`${panelId}-coordinate-error`" class="measurement-assist__error" role="alert">{{ coordinateError }}</p>
              <button type="submit" class="measurement-assist__primary" :disabled="!coordinateValid">确认坐标</button>
            </form>
          </details>
        </template>

        <p v-if="state.status" class="measurement-assist__status" role="status">{{ state.status }}</p>
        <p v-if="state.error" class="measurement-assist__error" role="alert">{{ state.error }}</p>
      </div>

      <footer class="measurement-assist__footer">
        <span>{{ state.pickedCount > 0 ? `已确认 ${state.pickedCount} 点` : '等待取点' }}</span>
        <button type="button" :disabled="!state.canUndo" @click="emit('undo')">撤销</button>
        <button type="button" :disabled="!state.canUndo && !state.hasPlane && !state.hasFrame && !state.preview" @click="emit('reset')">重置</button>
      </footer>
    </section>

    <div
      v-if="hoverVisible && state.hover"
      ref="feedbackRef"
      class="measurement-assist__feedback"
      :style="feedbackStyle"
      aria-hidden="true"
    >
      <div v-if="state.settings.magnifier" class="measurement-assist__lens">
        <canvas ref="lensRef" width="144" height="144" />
        <span v-if="lensReady" class="measurement-assist__crosshair" />
        <span class="measurement-assist__zoom">3×</span>
      </div>
      <div class="measurement-assist__candidate">
        <strong>{{ state.hover.label }}</strong>
        <span>{{ state.hover.sourceLabel }}</span>
        <span class="measurement-assist__position">{{ state.hover.position.map(value => formatNumber(value, 4)).join(', ') }} m</span>
      </div>
    </div>
  </div>
</template>

<style scoped>
.measurement-assist {
  --assist-surface: #172233;
  --assist-input: #0f1928;
  --assist-foreground: #edf2fc;
  --assist-muted: #b4c2d7;
  --assist-border: #40516b;
  --assist-hover: #2a3a53;
  --assist-accent: #4e66cc;
  position: absolute;
  inset: 0;
  z-index: 70;
  pointer-events: none;
  color: var(--assist-foreground);
  font-family: inherit;
  font-size: 14px;
  line-height: 1.5;
  text-align: left;
  font-variant-numeric: tabular-nums;
  color-scheme: dark;
}
.measurement-assist *,
.measurement-assist *::before,
.measurement-assist *::after { box-sizing: border-box; }
.measurement-assist ::selection { background: #4e66cc; color: #fff; }
.measurement-assist__panel {
  position: absolute;
  bottom: 12px;
  left: 12px;
  display: flex;
  flex-direction: column;
  width: min(330px, calc(100% - 24px));
  max-height: calc(100% - 24px);
  overflow: hidden;
  border: 1px solid var(--assist-border);
  border-radius: 8px;
  background: var(--assist-surface);
  pointer-events: auto;
}
.measurement-assist__header,
.measurement-assist__footer {
  display: flex;
  align-items: center;
  gap: 8px;
  flex: 0 0 auto;
  padding: 8px 12px;
}
.measurement-assist__header { border-bottom: 1px solid var(--assist-border); }
.measurement-assist__header h3 { color: var(--assist-foreground); }
.measurement-assist__header h3 { margin: 0; font-size: 14px; font-weight: 600; }
.measurement-assist__exit { margin-left: auto; color: var(--assist-muted); font-size: 12px; }
.measurement-assist__body {
  display: flex;
  flex-direction: column;
  gap: 12px;
  min-height: 0;
  overflow-y: auto;
  padding: 12px;
  overscroll-behavior: contain;
  scrollbar-width: thin;
  scrollbar-color: #64748b var(--assist-surface);
}
.measurement-assist__body[style*="display: none"] { padding: 0; }
.measurement-assist__row { display: grid; grid-template-columns: minmax(0, 1fr) minmax(0, 1fr); gap: 8px; }
.measurement-assist__field { display: flex; flex-direction: column; gap: 4px; min-width: 0; color: var(--assist-muted); font-size: 12px; }
.measurement-assist__unit { margin-left: 3px; }
.measurement-assist button,
.measurement-assist input,
.measurement-assist select {
  min-width: 0;
  min-height: 36px;
  margin: 0;
  padding: 6px 10px;
  border: 1px solid var(--assist-border);
  border-radius: 6px;
  background: var(--assist-input);
  color: var(--assist-foreground);
  font: inherit;
  font-size: 14px;
  line-height: 1.4;
  caret-color: #b7dff7;
}
.measurement-assist select,
.measurement-assist input { width: 100%; }
.measurement-assist select { padding-right: 24px; }
.measurement-assist button { cursor: pointer; }
.measurement-assist button:hover:not(:disabled),
.measurement-assist input:hover,
.measurement-assist select:hover { background: var(--assist-hover); }
.measurement-assist button:active:not(:disabled) { background: #35496a; }
.measurement-assist :is(button, input, select, summary):focus-visible { outline: 2px solid #b7dff7; outline-offset: 2px; }
.measurement-assist button:disabled,
.measurement-assist option:disabled { cursor: not-allowed; color: #8794a9; background: #1c283a; }
.measurement-assist button.is-selected,
.measurement-assist button.measurement-assist__primary { border-color: var(--assist-accent); background: var(--assist-accent); color: #fff; }
.measurement-assist button.is-selected:hover,
.measurement-assist button.measurement-assist__primary:hover:not(:disabled) { background: #4056b8; }
.measurement-assist button.measurement-assist__primary:disabled { border-color: var(--assist-border); background: #283449; color: #8794a9; }
.measurement-assist button.measurement-assist__disclosure { padding-inline: 8px; font-size: 12px; }
.measurement-assist__magnifier { display: flex; align-items: center; justify-content: space-between; gap: 8px; }
.measurement-assist__magnifier span:last-child { font-size: 12px; }
.measurement-assist__axis { display: grid; grid-template-columns: 1fr minmax(0, 1.1fr); align-items: center; gap: 8px; }
.measurement-assist__quality { display: flex; flex-direction: column; gap: 2px; color: var(--assist-muted); font-size: 12px; }
.measurement-assist__actions { display: flex; flex-wrap: wrap; gap: 8px; }
.measurement-assist__actions button { flex: 1 1 auto; padding-inline: 8px; font-size: 12px; }
.measurement-assist__frame { display: flex; align-items: center; justify-content: space-between; gap: 8px; color: var(--assist-muted); font-size: 12px; }
.measurement-assist__frame button { font-size: 12px; }
.measurement-assist__coordinates { padding-top: 4px; border-top: 1px solid var(--assist-border); }
.measurement-assist__coordinates summary { display: list-item; min-height: 36px; padding: 8px 2px; cursor: pointer; color: var(--assist-foreground); font-size: 12px; }
.measurement-assist__coordinates form { display: flex; flex-direction: column; gap: 8px; padding: 4px 0; }
.measurement-assist__xyz { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 8px; }
.measurement-assist__xyz input { padding-inline: 6px; }
.measurement-assist__status,
.measurement-assist__error { margin: 0; overflow-wrap: anywhere; font-size: 12px; }
.measurement-assist__status { color: var(--assist-muted); }
.measurement-assist__error { color: #ffbbc5; }
.measurement-assist__footer { border-top: 1px solid var(--assist-border); }
.measurement-assist__footer > span { flex: 1; color: var(--assist-muted); font-size: 12px; }
.measurement-assist__footer button { padding-inline: 10px; font-size: 12px; }
.measurement-assist__feedback {
  position: absolute;
  width: 232px;
  max-width: calc(100% - 16px);
  max-height: calc(100% - 16px);
  overflow: hidden;
  border: 1px solid var(--assist-border);
  border-radius: 6px;
  background: var(--assist-input);
  pointer-events: none;
}
.measurement-assist__lens { position: relative; width: min(144px, 100%); aspect-ratio: 1; margin: 8px auto 0; overflow: hidden; background: #0b1220; }
.measurement-assist__lens canvas { display: block; width: 100%; height: 100%; pointer-events: none; }
.measurement-assist__crosshair { position: absolute; inset: 0; pointer-events: none; }
.measurement-assist__crosshair::before,
.measurement-assist__crosshair::after { content: ''; position: absolute; top: 50%; left: 50%; background: #fff; outline: 1px solid #111827; transform: translate(-50%, -50%); }
.measurement-assist__crosshair::before { width: 17px; height: 1px; }
.measurement-assist__crosshair::after { width: 1px; height: 17px; }
.measurement-assist__zoom { position: absolute; right: 4px; bottom: 4px; padding: 0 4px; background: #0b1220; color: #edf2fc; font-size: 12px; }
.measurement-assist__candidate { display: flex; flex-direction: column; gap: 2px; padding: 8px 10px; overflow-wrap: anywhere; font-size: 12px; }
.measurement-assist__candidate strong { font-weight: 600; }
.measurement-assist__candidate > span { color: var(--assist-muted); }
.measurement-assist__position { font-variant-numeric: tabular-nums; }
@media (max-width: 480px) {
  .measurement-assist__panel { bottom: 8px; left: 8px; width: min(330px, calc(100% - 16px)); max-height: calc(100% - 16px); }
}
</style>
