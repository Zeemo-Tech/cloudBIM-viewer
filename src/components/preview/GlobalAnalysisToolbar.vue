<script setup lang="ts">
import { Aim, Delete, Fold, Tools, LocationInformation, FullScreen } from '@element-plus/icons-vue'
import type { AnalysisMode } from './ViewerAnalysisOverlay.vue'

const props = withDefaults(
  defineProps<{
    mode: AnalysisMode
    disabled?: boolean
    placement?: 'left' | 'right'
  }>(),
  { disabled: false, placement: 'right' },
)

const emit = defineEmits<{
  (event: 'update:mode', mode: AnalysisMode): void
  (event: 'clear'): void
}>()

const collapsed = defineModel<boolean>('collapsed', { default: true })

function select(mode: Exclude<AnalysisMode, 'none'>) {
  emit('update:mode', props.mode === mode ? 'none' : mode)
}
</script>

<template>
  <aside class="global-analysis-toolbar" :class="[`placement-${props.placement}`, { 'is-collapsed': collapsed }]" aria-label="全局分析工具">
    <button
      class="analysis-toggle"
      type="button"
      :aria-expanded="!collapsed"
      :title="collapsed ? '展开全局分析工具' : '收起全局分析工具'"
      @click="collapsed = !collapsed"
    >
      <el-icon><Tools v-if="collapsed" /><Fold v-else /></el-icon>
    </button>

    <div v-if="!collapsed" class="analysis-actions">
      <button
        class="analysis-action"
        :class="{ 'is-active': props.mode === 'distance' }"
        type="button"
        :disabled="props.disabled"
        title="全局测距"
        @click="select('distance')"
      >
        <el-icon><Aim /></el-icon><span>测距</span>
      </button>
      <button
        class="analysis-action"
        :class="{ 'is-active': props.mode === 'locate' }"
        type="button"
        :disabled="props.disabled"
        title="全局定位"
        @click="select('locate')"
      >
        <el-icon><LocationInformation /></el-icon><span>定位</span>
      </button>
      <button
        class="analysis-action"
        :class="{ 'is-active': props.mode === 'area' }"
        type="button"
        :disabled="props.disabled"
        title="面积测量"
        @click="select('area')"
      >
        <el-icon><FullScreen /></el-icon><span>面积</span>
      </button>
      <button class="analysis-action analysis-action--clear" type="button" title="清除分析结果" @click="emit('clear')">
        <el-icon><Delete /></el-icon><span>清除</span>
      </button>
    </div>
  </aside>
</template>

<style scoped>
.global-analysis-toolbar {
  position: fixed;
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

.global-analysis-toolbar.placement-left {
  right: auto;
  left: 18px;
}

.placement-left .analysis-actions {
  flex-direction: column;
}

.analysis-toggle,
.analysis-action {
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

.analysis-toggle {
  width: 34px;
  height: 34px;
  font-size: 17px;
}

.analysis-toggle:hover,
.analysis-action:hover:not(:disabled) {
  border-color: rgba(103, 232, 249, 0.3);
  background: rgba(34, 211, 238, 0.12);
}

.analysis-actions {
  display: flex;
  gap: 4px;
}

.analysis-action {
  min-height: 32px;
  padding: 0 10px;
  font-size: 12px;
}

.analysis-action.is-active {
  border-color: rgba(248, 113, 113, 0.62);
  background: rgba(220, 38, 38, 0.2);
  color: #fecaca;
}

.analysis-action--clear {
  color: #cbd5e1;
}

.analysis-action:disabled {
  cursor: not-allowed;
  opacity: 0.45;
}

@media (max-width: 640px) {
  .global-analysis-toolbar {
    top: 12px;
    right: 12px;
  }

  .analysis-action span {
    display: none;
  }

  .analysis-action {
    width: 32px;
    padding: 0;
  }
}
</style>
