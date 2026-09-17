<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import type { RebarComparisonBar, C2MRebarInspection } from '@cloudbim/viewer-core'

const props = defineProps<{
  inspection: C2MRebarInspection
  bars: RebarComparisonBar[]
  selectedId?: string
}>()
const emit = defineEmits<{ select: [ifcGlobalId: string] }>()
const page = ref(1)
const pairs = computed(() => props.inspection.spacing.filter(pair => !props.selectedId || pair.ifcGlobalIds.includes(props.selectedId)))
const visiblePairs = computed(() => pairs.value.slice((page.value - 1) * 10, page.value * 10))
const names = computed(() => new Map(props.bars.map(bar => [bar.ifcGlobalId, bar.name || bar.designBarId])))
const mm = (value: number | null | undefined) => typeof value === 'number' && Number.isFinite(value) ? (value * 1000).toFixed(1) : '—'
watch(() => [props.inspection, props.selectedId], () => { page.value = 1 })
</script>

<template>
  <details class="inspection-summary">
    <summary>钢筋间距 · {{ inspection.summary.spacingPairCount }} 组</summary>
    <button v-if="selectedId" type="button" @click="emit('select', '')">查看全部间距</button>
    <p>沿设计间距方向检查中心距，容差 ±{{ mm(inspection.summary.toleranceM) }} mm。下列数据来自已保存的计算批次。</p>
    <p v-if="!pairs.length">{{ selectedId ? '当前钢筋没有可确认的相邻检查组。' : '没有可确认的同组相邻钢筋，请结合分组与观测覆盖复核。' }}</p>
    <ul v-else>
      <li v-for="pair in visiblePairs" :key="pair.pairId">
        <div class="inspection-pair">
          <button v-for="id in pair.ifcGlobalIds" :key="id" type="button" @click="emit('select', id)">{{ names.get(id) || id }}</button>
        </div>
        <span>设计 {{ mm(pair.designCenterDistanceM) }} · 实测 {{ mm(pair.actualCenterDistanceM) }} mm</span>
        <strong>{{ pair.withinTolerance === false ? '间距超差' : pair.coverage.status === 'unavailable' ? '观测不足' : pair.coverage.status === 'partial' ? '局部可测，请复核覆盖' : pair.withinTolerance === true ? '已测范围内符合容差' : '待复核' }}</strong>
      </li>
    </ul>
    <el-pagination v-if="pairs.length > 10" v-model:current-page="page" :page-size="10" :total="pairs.length" layout="prev, pager, next" small />
    <p>完整沿程测量与缺测状态包含在检测 JSON 中。设计模型颜色继续表示表面偏差。</p>
  </details>
</template>

<style scoped>
.inspection-summary { padding: 12px 16px; border-top: 1px solid var(--border-color-light); font-size: 12px; }
summary { cursor: pointer; font-weight: 600; }
p { color: var(--text-secondary); line-height: 1.7; margin: 10px 0; }
ul { padding: 0; margin: 0; list-style: none; }
li { display: grid; gap: 5px; padding: 10px 0; border-top: 1px solid var(--border-color-light); }
.inspection-pair { display: flex; gap: 8px; flex-wrap: wrap; }
button { border: 0; padding: 4px 0; color: var(--color-primary); background: transparent; font: inherit; cursor: pointer; text-align: left; overflow-wrap: anywhere; }
button:focus-visible, summary:focus-visible { outline: 2px solid var(--color-primary); outline-offset: 2px; }
strong { font-weight: 500; }
</style>
