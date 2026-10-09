<script setup lang="ts">
/**
 * 四个查看器页面的最小接线示例。
 *
 * 关键点：包内部**不使用 vue-router**。
 *   - 数据通过 props 传入
 *   - 导航通过事件抛出（`back` / `stepChange`），由宿主决定跳到哪儿
 *   - 也可以用 `:navigation="{ back, stepChange }"` 传回调，行为一致
 */
import { computed, ref } from 'vue'
import { BimPreviewPage } from '@cloudbim/bim-preview'
import { PointcloudPreviewPage } from '@cloudbim/pointcloud-preview'
import { SplitPreviewPage } from '@cloudbim/split-preview'
import { AlignmentPage } from '@cloudbim/alignment'

type Tab = 'bim' | 'pointcloud' | 'split' | 'alignment'

const tabs: Array<{ key: Tab; label: string }> = [
  { key: 'bim', label: '模型预览' },
  { key: 'pointcloud', label: '点云预览' },
  { key: 'split', label: '双屏对比' },
  { key: 'alignment', label: '配准与分析' },
]

// 真实项目里这些值来自路由 query 或接口；示例从 URL 读，方便直接改地址栏验证。
const query = new URLSearchParams(location.search)
const bimAssetId = computed(() => Number(query.get('bimAssetId')) || 49)
const pointcloudAssetId = computed(() => Number(query.get('pointcloudAssetId')) || 60)

const active = ref<Tab>((query.get('tab') as Tab) || 'alignment')
const currentStep = ref(Number(query.get('step')) || 1)

const messages = ref<string[]>([])
const log = (text: string) => {
  messages.value = [`${new Date().toLocaleTimeString()} ${text}`, ...messages.value].slice(0, 6)
}
</script>

<template>
  <div class="demo-tabs">
    <button
      v-for="tab in tabs"
      :key="tab.key"
      type="button"
      :data-active="active === tab.key"
      @click="active = tab.key"
    >
      {{ tab.label }}
    </button>
    <span style="color: #64748b; font-size: 13px; align-self: center; margin-left: auto">
      bimAssetId={{ bimAssetId }} · pointcloudAssetId={{ pointcloudAssetId }}
    </span>
  </div>

  <div class="demo-page">
    <!-- 模型预览：单个资产 -->
    <BimPreviewPage
      v-if="active === 'bim'"
      :asset-id="bimAssetId"
      display-name="model.glb"
      :project-id="1"
      project-name="示例项目"
      back-label="返回模型列表"
      @back="log('模型预览触发了 back 事件')"
    />

    <!-- 点云预览：单个资产 -->
    <PointcloudPreviewPage
      v-else-if="active === 'pointcloud'"
      :asset-id="pointcloudAssetId"
      display-name="scan.las"
      :project-id="1"
      project-name="示例项目"
      back-label="返回扫描列表"
      @back="log('点云预览触发了 back 事件')"
    />

    <!-- 双屏对比：一对资产 -->
    <SplitPreviewPage
      v-else-if="active === 'split'"
      :bim-asset-id="bimAssetId"
      :pointcloud-asset-id="pointcloudAssetId"
      bim-display-name="model.glb"
      pointcloud-display-name="scan.las"
      back-label="返回"
      @back="log('双屏对比触发了 back 事件')"
    />

    <!-- 配准与分析：一对资产 + 步骤。宿主可把 step 写进 URL 以支持刷新/分享 -->
    <AlignmentPage
      v-else
      :bim-asset-id="bimAssetId"
      :pointcloud-asset-id="pointcloudAssetId"
      bim-display-name="model.glb"
      pointcloud-display-name="scan.las"
      :project-id="1"
      :initial-step="currentStep"
      back-label="返回扫描点云"
      @back="log('配准页触发了 back 事件')"
      @step-change="
        (step: number) => {
          currentStep = step
          log(`配准页切换到第 ${step} 步（宿主可以把它写进 URL）`)
        }
      "
    />
  </div>

  <!-- 事件日志：证明宿主完全掌控导航 -->
  <div
    v-if="messages.length"
    style="
      position: fixed;
      right: 12px;
      bottom: 12px;
      max-width: 380px;
      padding: 10px 12px;
      border-radius: 8px;
      background: rgba(15, 23, 42, 0.92);
      color: #cbd5e1;
      font-size: 12px;
      line-height: 1.7;
      z-index: 9999;
    "
  >
    <div v-for="(message, index) in messages" :key="index">{{ message }}</div>
  </div>
</template>
