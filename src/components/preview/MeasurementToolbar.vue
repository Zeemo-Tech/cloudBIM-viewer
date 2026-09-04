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
  }>(),
  {
    disabled: false,
    placement: 'right',
    position: 'fixed',
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
</script>

<template>
  <aside
    class="measurement-toolbar"
    :class="[
      `placement-${props.placement}`,
      `position-${props.position}`,
      { 'is-collapsed': collapsed },
    ]"
    aria-label="测量工具"
  >
    <button
      class="measurement-toggle"
      type="button"
      :aria-expanded="!collapsed"
      :title="collapsed ? '展开测量工具' : '收起测量工具'"
      @click="collapsed = !collapsed"
    >
      <img v-if="collapsed" class="measurement-toggle-icon" src="/celiang.svg" alt="" />
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
  padding: 6px;
  border: 1px solid rgba(148, 163, 184, 0.22);
  border-radius: 10px;
  background: rgba(8, 17, 29, 0.86);
  box-shadow: 0 16px 36px rgba(1, 8, 13, 0.3);
  backdrop-filter: blur(18px) saturate(135%);
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
  left: 18px;
}

.placement-left .measurement-actions {
  flex-direction: column;
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
  min-height: 32px;
  padding: 0 10px;
  font-size: 12px;
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
