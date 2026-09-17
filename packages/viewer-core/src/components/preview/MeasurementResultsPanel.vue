<script setup lang="ts">
import { computed } from 'vue'
import type { AnalysisArea, AnalysisDistance, AnalysisPoint } from './ViewerAnalysisOverlay.vue'

const props = defineProps<{
  points: AnalysisPoint[]
  distances: AnalysisDistance[]
  areas: AnalysisArea[]
}>()

const emit = defineEmits<{ clear: []; remove: [kind: 'point' | 'distance' | 'area', index: number] }>()
const hasResults = computed(() => props.points.length + props.distances.length + props.areas.length > 0)
const format = (value: number) => `${value.toFixed(3)} m`
</script>

<template>
  <aside v-if="hasResults" class="measurement-results" aria-label="测量结果">
    <header class="measurement-results__header">
      <strong>测量结果</strong>
      <button type="button" title="清除全部测量" @click="emit('clear')">清除</button>
    </header>
    <div v-for="(point, index) in props.points" :key="`p-${index}`" class="measurement-result">
      <div class="measurement-result__title"><span class="dot dot--locate" />坐标拾取 #{{ index + 1 }}<button type="button" @click="emit('remove', 'point', index)">×</button></div>
      <div class="measurement-result__value">X {{ format(point.x) }} · Y {{ format(point.z) }} · Z {{ format(point.y) }}</div>
    </div>
    <div v-for="(distance, index) in props.distances" :key="`d-${index}`" class="measurement-result">
      <div class="measurement-result__title"><span class="dot dot--distance" />测距 #{{ index + 1 }}<button type="button" @click="emit('remove', 'distance', index)">×</button></div>
      <div class="measurement-result__value">{{ format(distance.distance) }} <small>高差 {{ format(distance.heightDifference) }}</small></div>
    </div>
    <div v-for="(area, index) in props.areas" :key="`a-${index}`" class="measurement-result">
      <div class="measurement-result__title"><span class="dot dot--area" />面积 #{{ index + 1 }}<button type="button" @click="emit('remove', 'area', index)">×</button></div>
      <div class="measurement-result__value">{{ area.area.toFixed(2) }} m² <small>周长 {{ area.perimeter.toFixed(2) }} m</small></div>
    </div>
  </aside>
</template>

<style scoped>
.measurement-results { position:absolute; right:18px; bottom:18px; z-index:30; width:276px; max-height:min(52vh,420px); overflow:auto; padding:10px; border:1px solid rgba(115,162,243,.22); border-radius:11px; background:rgba(8,18,43,.9); color:#eef4ff; box-shadow:0 16px 38px rgba(0,0,0,.32); backdrop-filter:blur(14px); }
.measurement-results__header,.measurement-result__title { display:flex; align-items:center; justify-content:space-between; gap:8px; }
.measurement-results__header { padding:2px 2px 8px; border-bottom:1px solid rgba(255,255,255,.1); font-size:13px; }
.measurement-results__header button,.measurement-result__title button { border:0; background:transparent; color:#fca5a5; cursor:pointer; }
.measurement-result { padding:9px 3px; border-bottom:1px solid rgba(255,255,255,.08); }
.measurement-result:last-child { border-bottom:0; }
.measurement-result__title { justify-content:flex-start; color:rgba(255,255,255,.72); font-size:11px; }
.measurement-result__title button { margin-left:auto; font-size:16px; }
.measurement-result__value { margin-top:5px; color:#fff; font-size:12px; line-height:1.45; }
.measurement-result__value small { display:block; margin-top:2px; color:rgba(254,202,202,.78); font-size:11px; }
.dot { width:7px; height:7px; border-radius:50%; flex:0 0 auto; }
.dot--locate { background:#67e8f9; }.dot--distance { background:#ff5a5a; }.dot--area { background:#fbbf24; }
@media (max-width:640px) { .measurement-results { right:10px; bottom:10px; width:calc(100% - 20px); max-height:34vh; } }
</style>
