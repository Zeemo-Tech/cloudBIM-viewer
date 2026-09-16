<script setup lang="ts">
import type { RebarComparison, RebarComparisonBar } from '@/api/backend-c2m'
import { rebarStatusLabel } from './rebarComparison'

defineProps<{
  bars: RebarComparisonBar[]; selectedId: string; active: boolean; surface: string; scan: boolean;
  cluster: string; normals: boolean; scanNormals: boolean; lengthMm: number; limit: number;
  normalMode: 'vertex' | 'face'; allNormals: boolean; meshCounts: { vertices: number; faces: number };
  pointCount: number; normalCount: number; scanNormalCount: number; hasResult: boolean;
  resultVersion?: string; loading: boolean;
  sceneLoaded: boolean; sceneError: string;
  effective?: RebarComparison['effective']; algorithm?: string;
}>()
defineEmits<{
  toggle: []; select: [id: string]; move: [delta: number]; focus: []; open: []; export: [];
  result: []; pair: [];
  surface: [value: string]; scan: [value: boolean]; cluster: [value: string];
  normals: [value: boolean]; scanNormals: [value: boolean]; length: [value: number]; limit: [value: number];
  normalMode: [value: 'vertex' | 'face'];
  allNormals: [value: boolean];
}>()
</script>

<template>
  <section class="rebar-debug" aria-label="Scan-vs-BIM 逐根调试">
    <div class="rebar-debug__heading">
      <strong>Scan-vs-BIM 调试</strong>
      <el-button @click="$emit('open')">独立端口调试</el-button>
      <el-button :type="active ? 'primary' : 'default'" :aria-pressed="active" @click="$emit('toggle')">{{ active ? '退出隔离' : '逐根调试' }}</el-button>
    </div>
    <template v-if="active">
      <div class="rebar-debug__actions" role="group" aria-label="调试显示快捷切换">
        <el-button :type="surface === 'result' ? 'primary' : 'default'" :disabled="!hasResult" @click="$emit('result')">偏差着色</el-button>
        <el-button :type="surface === (hasResult ? 'mesh' : 'source') && scan ? 'primary' : 'default'" @click="$emit('pair')">{{ hasResult ? '计算网格 + 钢筋簇' : '原模型 + 钢筋簇' }}</el-button>
      </div>
      <p v-if="hasResult && surface === 'result' && sceneLoaded" role="status">正在显示偏差着色{{ scan ? '，叠加钢筋簇' : '，钢筋簇已隐藏' }}。灰色区域为缺测。</p>
      <p v-else-if="hasResult && surface !== 'result'" role="status">当前显示{{ surface === 'source' ? '原设计模型' : surface === 'mesh' ? '中性计算网格' : '钢筋簇' }}，点击“偏差着色”查看计算结果。</p>
      <p v-if="sceneError" role="alert">{{ sceneError }}</p>
      <el-button v-if="hasResult && !sceneLoaded && !loading" @click="$emit('result')">加载偏差结果</el-button>
      <p v-if="loading" role="status">正在加载模型、钢筋簇或计算网格…</p>
      <p v-if="!bars.length">暂无钢筋对应目录，请先完成第二步分类和去噪。</p>
      <el-select :model-value="selectedId" filterable aria-label="选择调试钢筋" placeholder="按名称或 IFC ID 查找" @update:model-value="$emit('select', $event)">
        <el-option v-for="(bar, i) in bars" :key="bar.ifcGlobalId" :value="bar.ifcGlobalId" :label="`${i + 1}. ${bar.name || bar.designBarId} · ${rebarStatusLabel(bar.status)} · ${bar.ifcGlobalId}`" />
      </el-select>
      <div class="rebar-debug__navigation">
        <el-button :disabled="!bars.length || bars[0]?.ifcGlobalId === selectedId" @click="$emit('move', -1)">上一根</el-button>
        <span>{{ Math.max(0, bars.findIndex(bar => bar.ifcGlobalId === selectedId) + 1) }} / {{ bars.length }}</span>
        <el-button :disabled="!bars.length || bars.at(-1)?.ifcGlobalId === selectedId" @click="$emit('move', 1)">下一根</el-button>
      </div>
      <div class="rebar-debug__actions">
        <el-button :disabled="!selectedId" @click="$emit('focus')">聚焦当前</el-button>
      </div>
      <label>设计显示
        <el-select :model-value="surface" aria-label="调试设计显示方式" @update:model-value="$emit('surface', $event)">
          <el-option label="原设计模型（未细分）" value="source" />
          <el-option label="细分计算网格（中性色）" value="mesh" :disabled="!hasResult" />
          <el-option label="Scan-vs-BIM 偏差着色" value="result" :disabled="!hasResult" />
          <el-option label="隐藏设计，仅看钢筋簇" value="hidden" />
        </el-select>
      </label>
      <p v-if="surface !== 'hidden'">当前网格：{{ meshCounts.vertices.toLocaleString() }} 个顶点 · {{ meshCounts.faces.toLocaleString() }} 个三角面。</p>
      <p v-if="surface === 'source'" role="status">当前是原始显示模型，未使用细分结果；长直段的顶点可能只在两端。检查本次比对请切换“细分计算网格”。</p>
      <label>显示钢筋簇 <el-switch :model-value="scan" aria-label="调试显示钢筋簇" @update:model-value="$emit('scan', Boolean($event))" /></label>
      <label>钢筋簇范围
        <el-select :model-value="cluster" aria-label="调试钢筋簇范围" @update:model-value="$emit('cluster', $event)">
          <el-option label="可靠对应簇" value="matched" />
          <el-option label="待复核簇（未参与计算）" value="review" />
          <el-option label="可靠对应 + 待复核" value="all" />
        </el-select>
      </label>
      <label>设计网格法向量 <el-switch :model-value="normals" aria-label="显示设计网格法向量" @update:model-value="$emit('normals', Boolean($event))" /></label>
      <label v-if="normals">设计法向类型
        <el-select :model-value="normalMode" aria-label="设计法向类型" @update:model-value="$emit('normalMode', $event)">
          <el-option label="顶点法向（网格保存值）" value="vertex" />
          <el-option label="面法向（从三角面心出发）" value="face" />
        </el-select>
      </label>
      <p v-if="normals">{{ normalMode === 'face' ? '面法向按当前三角面绕序绘制，可检查端盖朝向；不会自动翻转，也不代表求解器的顶点采样。' : '顶点箭头保留当前网格的法向；计算网格中端盖与侧面共用的边缘顶点可能呈斜向。检查端面请切换面法向。' }}</p>
      <label v-if="normals">显示全部设计法向 <el-switch :model-value="allNormals" aria-label="显示全部设计法向" @update:model-value="$emit('allNormals', Boolean($event))" /></label>
      <p v-if="normals" role="status">设计法向：{{ allNormals ? '全量显示' : '抽样显示' }} · {{ normalCount.toLocaleString() }} 条箭头 / {{ (normalMode === 'face' ? meshCounts.faces : meshCounts.vertices).toLocaleString() }} 个{{ normalMode === 'face' ? '三角面' : '顶点' }}。{{ allNormals ? '仅跳过无效或零长度法向；箭头过密时可关闭全量显示。' : '箭头已抽稀，不能据此判断网格密度。' }}</p>
      <label>扫描截面径向量 <el-switch :model-value="scanNormals" :disabled="!hasResult" aria-label="显示扫描截面径向量" @update:model-value="$emit('scanNormals', Boolean($event))" /></label>
      <div v-if="normals || scanNormals" class="rebar-debug__numbers">
        <label>箭头长度 · mm <el-input-number :model-value="lengthMm" :min="1" :max="200" :value-on-clear="10" controls-position="right" aria-label="调试法向箭头长度毫米" @update:model-value="$emit('length', $event ?? 10)" /></label>
        <label v-if="scanNormals || (normals && !allNormals)">{{ normals && !allNormals ? '每组采样上限' : '扫描箭头采样上限' }} <el-input-number :model-value="limit" :min="20" :max="1000" :step="20" :value-on-clear="200" controls-position="right" aria-label="调试法向采样上限" @update:model-value="$emit('limit', $event ?? 200)" /></label>
      </div>
      <p class="rebar-debug__legend">青色：设计网格法向 {{ normalCount }} 条；橙色：扫描截面径向 {{ scanNormalCount }} 条。当前显示 {{ pointCount.toLocaleString() }} 个预览点。</p>
      <p>扫描箭头根据已保存的实测截面复现，仅绘制有支撑的预览点；不是求解器下采样点或逐点匹配连线。切换“原设计 / 计算网格”可对照法向来源。</p>
      <template v-for="bar in bars.filter(item => item.ifcGlobalId === selectedId)" :key="bar.ifcGlobalId">
        <dl>
          <dt>IFC GlobalId</dt><dd>{{ bar.ifcGlobalId }}</dd>
          <dt>可靠实例 ID</dt><dd>{{ bar.instanceIds.join(', ') || '无' }}</dd>
          <dt>待复核实例 ID</dt><dd>{{ bar.reviewInstanceIds?.join(', ') || '无' }}</dd>
          <dt>对应状态</dt><dd>{{ rebarStatusLabel(bar.status) }}</dd>
          <template v-if="hasResult">
            <dt>计算点数 / 原始点数</dt><dd>{{ bar.pointsAfter }} / {{ bar.pointCount }}</dd>
            <dt>已覆盖 / 缺测顶点</dt><dd>{{ bar.knownCount }} / {{ bar.unknownCount }}</dd>
            <dt>顶点范围</dt><dd>[{{ bar.vertexStart }}, {{ bar.vertexStart + bar.vertexCount }})</dd>
            <dt>最大绝对偏差</dt><dd>{{ bar.stats ? `${(Math.max(Math.abs(bar.stats.min), Math.abs(bar.stats.max)) * 1000).toFixed(2)} mm` : '缺测，未出具结论' }}</dd>
          </template>
        </dl>
        <p v-if="!bar.instanceIds.length">该设计钢筋没有可靠对应簇；空场景表示缺测。可切换到待复核簇检查候选。</p>
      </template>
      <p v-if="!hasResult">尚无有效计算结果，当前仅检查设计与钢筋簇对应关系。</p>
      <p v-else>结果版本：{{ resultVersion }}</p>
      <p v-if="hasResult && effective">本次计算：{{ algorithm }} · {{ effective.normalConstraintEnabled ? `同侧法向 ${effective.normalMaxAngleDeg}°` : '最近点' }} · 搜索距离 {{ ((effective.maxSearchDistance ?? .2) * 1000).toFixed(1) }} mm · K={{ effective.knnK }}。</p>
      <el-button :disabled="!selectedId" @click="$emit('export')">导出当前调试 JSON</el-button>
    </template>
  </section>
</template>

<style scoped>
.rebar-debug { border-bottom: 1px solid var(--border-color); padding: 12px 0 16px; font-size: 12px; }
.rebar-debug__heading, .rebar-debug__navigation, .rebar-debug label { display: flex; align-items: center; justify-content: space-between; gap: 8px; }
.rebar-debug__heading { margin-bottom: 10px; flex-wrap: wrap; }
.rebar-debug > .el-select { width: 100%; }
.rebar-debug__navigation, .rebar-debug__actions { display: flex; gap: 8px; margin: 8px 0; }
.rebar-debug label { margin: 10px 0; }
.rebar-debug label > .el-select { width: 185px; }
.rebar-debug p { line-height: 1.6; color: var(--text-secondary); overflow-wrap: anywhere; margin: 8px 0; }
.rebar-debug__numbers { display: grid; grid-template-columns: 1fr 1fr; gap: 8px; }
.rebar-debug__numbers label { display: flex; align-items: stretch; flex-direction: column; }
.rebar-debug__numbers .el-input-number { width: 100%; }
.rebar-debug dl { display: grid; grid-template-columns: auto 1fr; gap: 6px 12px; border-top: 1px solid var(--border-color); padding-top: 10px; }
.rebar-debug dt { color: var(--text-secondary); }
.rebar-debug dd { margin: 0; overflow-wrap: anywhere; font-variant-numeric: tabular-nums; }
</style>
