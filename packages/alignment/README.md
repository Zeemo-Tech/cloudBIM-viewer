# @cloudbim/alignment

CloudBIM 配准与分析工作区：四步流程 **点云与工程坐标配准 → 点云分类与去噪 →
偏差对比 → 出报告**，包含 C2M 距离分析、钢筋巡检与逐钢筋对比。

## 安装

```bash
npm install @cloudbim/alignment @cloudbim/viewer-core vue three 3d-tiles-renderer element-plus @element-plus/icons-vue
```

`@cloudbim/viewer-core` 会自动作为依赖安装；`vue` / `three` / `3d-tiles-renderer` /
`element-plus` / `@element-plus/icons-vue` 是 peerDependencies，由宿主安装一次。

## 使用

```ts
// main.ts —— 每个应用只需调用一次
import { configureCloudBim } from '@cloudbim/viewer-core'
import '@cloudbim/viewer-core/style.css'
import '@cloudbim/alignment/style.css'

configureCloudBim({
  baseUrl: import.meta.env.VITE_API_BASE_URL ?? '',   // 留空表示与宿主同源
  getAccessToken: () => sessionStorage.getItem('token') ?? '',
  dracoDecoderPath: '/draco/',                        // 宿主需托管 three 的 Draco 解码器
})
```

```vue
<script setup lang="ts">
import { AlignmentPage } from '@cloudbim/alignment'
import { useRoute, useRouter } from 'vue-router'

const route = useRoute()
const router = useRouter()

// 包不知道宿主的路由结构，返回与步骤跳转都由宿主决定。
const handleBack = () => router.push(`/survey?projectId=${route.query.projectId}`)
const handleStepChange = (step: number) =>
  router.replace({ query: { ...route.query, step: String(step) } })
</script>

<template>
  <AlignmentPage
    :bim-asset-id="Number(route.query.bimAssetId) || null"
    :pointcloud-asset-id="Number(route.query.pointcloudAssetId) || null"
    :bim-display-name="String(route.query.displayName ?? '')"
    :pointcloud-display-name="String(route.query.pointcloudDisplayName ?? '')"
    :initial-step="Number(route.query.step) || 1"
    @back="handleBack"
    @step-change="handleStepChange"
  />
</template>
```

## Props 与事件

| Prop | 类型 | 说明 |
| --- | --- | --- |
| `bimAssetId` | `number \| null` | 设计模型资产 ID，为空时渲染"缺少选择"状态 |
| `pointcloudAssetId` | `number \| null` | 扫描点云资产 ID |
| `bimDisplayName` | `string?` | 顶部标题显示的模型名 |
| `pointcloudDisplayName` | `string?` | 顶部标题显示的点云名 |
| `initialStep` | `number?` | 初始步骤（1–4），宿主可从 URL 恢复 |
| `navigation` | `ViewerNavigationHandlers?` | 回调式导航；与下面的事件二选一 |

| 事件 | 载荷 | 说明 |
| --- | --- | --- |
| `back` | 无 | 用户请求返回宿主页面 |
| `stepChange` | `step: number` | 步骤变化，宿主可同步到 URL |

## 后端依赖

工作区调用以下后端能力（由 `configureCloudBim` 指定的 `baseUrl` 提供）：

- `GET /assets/:id`、`/assets/:id/tiles/*`、`/assets/:id/representations`：资产与几何资源
- `POST /alignments/bim`、`POST /alignments/bim/fine`：粗配准与精细配准
- `POST /alignments/bim/denoise`、`GET /alignments/bim/denoise/latest`：分类与去噪
- `POST /alignments/bim/c2m`、`POST /alignments/bim/analysis-c2m`：偏差对比与 C2M 距离分析
- `GET/POST/DELETE /assets/:id/measurements`：测量记录

## 其他导出

- `DenoisePanel`：去噪结果面板，可单独嵌入其他页面。

## 开发

```bash
npm run build -w @cloudbim/alignment                       # 构建 dist/
node --test --experimental-strip-types packages/alignment/src/*.test.ts
```
