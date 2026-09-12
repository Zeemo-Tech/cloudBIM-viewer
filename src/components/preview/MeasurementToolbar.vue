<script setup lang="ts">
import { Aim, Delete, Fold, FullScreen, LocationInformation } from '@element-plus/icons-vue'
import type { Component } from 'vue'
import type { AnalysisMode } from './ViewerAnalysisOverlay.vue'

type MeasurementAction = {
  mode: Exclude<AnalysisMode, 'none'>
  label: string
  title: string
  icon: Component
}

const props = withDefaults(
  defineProps<{
    mode: AnalysisMode
    disabled?: boolean
    placement?: 'left' | 'right'
    position?: 'fixed' | 'absolute' | 'static'
    orientation?: 'horizontal' | 'vertical'
    toggleIcon?: 'fold' | 'ruler'
    clearOnToggleOff?: boolean
    defaultModeOnOpen?: Exclude<AnalysisMode, 'none'>
  }>(),
  {
    disabled: false,
    placement: 'right',
    position: 'fixed',
    orientation: 'horizontal',
    toggleIcon: 'fold',
    clearOnToggleOff: false,
    defaultModeOnOpen: undefined,
  },
)

const emit = defineEmits<{
  (event: 'update:mode', mode: AnalysisMode): void
  (event: 'clear'): void
}>()

const collapsed = defineModel<boolean>('collapsed', { default: true })

const measurementActions: MeasurementAction[] = [
  { mode: 'distance', label: '测距', title: '全局测距', icon: Aim },
  { mode: 'locate', label: '定位', title: '全局定位', icon: LocationInformation },
  { mode: 'area', label: '面积', title: '面积测量', icon: FullScreen },
]

function select(mode: Exclude<AnalysisMode, 'none'>) {
  emit('update:mode', props.mode === mode ? 'none' : mode)
}

function toggleToolbar() {
  if (props.clearOnToggleOff && props.mode !== 'none') {
    emit('update:mode', 'none')
    emit('clear')
    collapsed.value = true
    return
  }
  if (collapsed.value && props.defaultModeOnOpen && props.mode === 'none') {
    emit('update:mode', props.defaultModeOnOpen)
  }
  collapsed.value = !collapsed.value
}
</script>

<template>
  <aside
    class="measurement-toolbar"
    :class="[
      `placement-${props.placement}`,
      `position-${props.position}`,
      `orientation-${props.orientation}`,
      { 'is-collapsed': collapsed },
    ]"
    aria-label="测量工具"
  >
    <button
      class="measurement-toggle"
      :class="{ 'is-active': !collapsed || props.mode !== 'none' }"
      type="button"
      :aria-expanded="!collapsed"
      :aria-label="collapsed ? '展开测量工具' : '收起测量工具'"
      :title="collapsed ? '展开测量工具' : '收起测量工具'"
      @click="toggleToolbar"
    >
      <img v-if="props.toggleIcon === 'ruler' || collapsed" class="measurement-toggle-icon" src="/celiang.svg" alt="" />
      <el-icon v-else><Fold /></el-icon>
    </button>

    <div v-if="!collapsed" class="measurement-actions">
      <button
        v-for="action in measurementActions"
        :key="action.mode"
        class="measurement-action"
        :class="{ 'is-active': props.mode === action.mode }"
        type="button"
        :disabled="props.disabled"
        :aria-label="action.title"
        :aria-pressed="props.mode === action.mode"
        :title="action.title"
        @click="select(action.mode)"
      >
        <el-icon><component :is="action.icon" /></el-icon>
        <span>{{ action.label }}</span>
      </button>

      <button
        class="measurement-action measurement-action--clear"
        type="button"
        :disabled="props.disabled"
        title="清除全部测量结果"
        aria-label="清除全部测量结果"
        @click="emit('clear')"
      >
        <el-icon><Delete /></el-icon>
        <span>清除</span>
      </button>
    </div>
  </aside>
</template>

<style scoped>
.measurement-toolbar {
  top: 18px;
  right: 18px;
  z-index: 80;
  display: flex;
  align-items: center;
  gap: 8px;
  border-radius: 10px;
  background: rgba(8, 17, 29, 0.6);
}

.measurement-toolbar.position-fixed {
  position: fixed;
}

.measurement-toolbar.position-absolute {
  position: absolute;
}

.measurement-toolbar.position-static {
  position: static;
}

.measurement-toolbar.placement-left {
  right: auto;
  left: 0;
}

.orientation-vertical {
  flex-direction: column;
}

.orientation-vertical .measurement-actions {
  flex-direction: column;
}

.orientation-vertical .measurement-action {
  width: 100%;
}

.measurement-toggle,
.measurement-action {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  gap: 7px;
  border: 1px solid transparent;
  border-radius: 7px;
  color: #d8f3ff;
  background: transparent;
  cursor: pointer;
}

.measurement-toggle {
  width: 34px;
  height: 34px;
  padding: 0;
  font-size: 17px;
}

.measurement-toggle-icon {
  display: block;
  width: 20px;
  height: 20px;
  object-fit: contain;
  filter: brightness(0) invert(1);
  opacity: 0.9;
  pointer-events: none;
}

.measurement-toggle:hover,
.measurement-action:hover:not(:disabled) {
  border-color: rgba(103, 232, 249, 0.3);
  background: rgba(34, 211, 238, 0.12);
}

.measurement-actions {
  display: flex;
  gap: 4px;
}

.measurement-action {
  min-height: 36px;
  padding: 0 10px;
  font-size: 13px;
}

.measurement-action.is-active {
  border-color: rgba(248, 113, 113, 0.62);
  color: #fecaca;
  background: rgba(220, 38, 38, 0.2);
}

.measurement-action--clear {
  color: #cbd5e1;
}

.measurement-action:disabled {
  cursor: not-allowed;
  opacity: 0.45;
}

@media (max-width: 640px) {
  .measurement-toolbar {
    top: 12px;
    right: 12px;
  }

  .measurement-action {
    width: 32px;
    padding: 0;
  }

  .measurement-action span {
    display: none;
  }
}
</style>
