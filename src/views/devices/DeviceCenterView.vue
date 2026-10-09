<template>
  <section class="device-page">
    <header class="page-heading">
      <div>
        <p class="eyebrow">DEVICE CENTER</p>
        <h1>设备中心</h1>
        <span>集中查看设备状态、连接信息与运行记录。</span>
      </div>
      <el-tag type="info" effect="plain">功能建设中</el-tag>
    </header>
    <div class="summary-grid">
      <div v-for="item in summaries" :key="item.label" class="summary-item"><span>{{ item.label }}</span><strong>{{ item.value }}</strong></div>
    </div>
    <div class="empty-state">
      <el-icon :size="28"><Monitor /></el-icon>
      <strong>暂无设备数据</strong>
      <span>设备接入后，将在此查看在线状态、告警和最近活动。</span>
    </div>
  </section>
</template>

<script setup lang="ts">
import { Monitor } from '@element-plus/icons-vue'

const summaries = [
  { label: '设备总数', value: '0' },
  { label: '在线设备', value: '0' },
  { label: '待处理告警', value: '0' },
]
</script>

<style scoped>
.device-page { min-height: 100%; color: var(--text-primary); }
.page-heading { display: flex; align-items: center; justify-content: space-between; padding: var(--spacing-sm) 0 var(--spacing-lg); border-bottom: 1px solid var(--border-color-light); }
.eyebrow { margin: 0 0 var(--spacing-xs); color: var(--color-primary); font-size: var(--font-size-xs); font-weight: 700; letter-spacing: .12em; }
h1 { margin: 0; font-size: var(--font-size-2xl); }
.page-heading span { display: block; margin-top: var(--spacing-sm); color: var(--text-tertiary); font-size: var(--font-size-sm); }
.summary-grid { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: var(--spacing-md); margin-top: var(--spacing-lg); }
.summary-item { display: flex; flex-direction: column; gap: var(--spacing-sm); padding: var(--spacing-md); border: 1px solid var(--border-color-light); border-radius: var(--radius-sm); background: var(--bg-card); }
.summary-item span { color: var(--text-tertiary); font-size: var(--font-size-xs); }
.summary-item strong { color: var(--text-primary); font-size: var(--font-size-2xl); font-weight: 650; }
.empty-state { display: grid; place-items: center; gap: var(--spacing-sm); min-height: 240px; margin-top: var(--spacing-lg); border: 1px dashed var(--border-color); border-radius: var(--radius-sm); color: var(--text-tertiary); background: var(--bg-control); text-align: center; }
.empty-state .el-icon { color: var(--color-primary); }
.empty-state strong { color: var(--text-primary); font-size: var(--font-size-md); }
.empty-state span { font-size: var(--font-size-sm); }
@media (max-width: 620px) { .summary-grid { grid-template-columns: 1fr; } }
</style>
