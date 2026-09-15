<template>
  <section class="system-page">
    <header class="page-heading">
      <div>
        <p class="eyebrow">SYSTEM</p>
        <h1>系统管理</h1>
        <span>管理当前工作区的系统配置与成员权限。</span>
      </div>
    </header>
    <div class="empty-state">
      <el-icon :size="28"><Setting /></el-icon>
      <strong>系统管理功能即将开放</strong>
      <span>当前版本已预留统一入口，后续配置将在这里集中管理。</span>
    </div>
  </section>
</template>

<script setup lang="ts">
import { Setting } from '@element-plus/icons-vue'
</script>

<style scoped>
.system-page { min-height: 100%; color: var(--text-primary); }
.page-heading { display: flex; align-items: flex-end; justify-content: space-between; padding: var(--spacing-sm) 0 var(--spacing-lg); border-bottom: 1px solid var(--border-color-light); }
.eyebrow { margin: 0 0 var(--spacing-xs); color: var(--color-primary); font-size: var(--font-size-xs); font-weight: 700; letter-spacing: .12em; }
h1 { margin: 0; font-size: var(--font-size-2xl); }
.page-heading span { display: block; margin-top: var(--spacing-sm); color: var(--text-tertiary); font-size: var(--font-size-sm); }
.empty-state { display: grid; place-items: center; gap: var(--spacing-sm); min-height: 280px; margin-top: var(--spacing-lg); border: 1px dashed var(--border-color); border-radius: var(--radius-sm); color: var(--text-tertiary); background: var(--bg-control); text-align: center; }
.empty-state .el-icon { color: var(--color-primary); }
.empty-state strong { color: var(--text-primary); font-size: var(--font-size-md); }
.empty-state span { font-size: var(--font-size-sm); }
</style>
