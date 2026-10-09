# 消费方最小示例

一个**可运行**的最小项目，演示怎么把 CloudBIM 查看器包接进一个全新的 Vue 3 应用。
它不依赖 `cloudBIM-viewer` 仓库的任何源码，只依赖 `../` 下的 tarball。

## 运行

```bash
cd example
npm install
npm run dev          # http://localhost:5199
```

需要 CloudBIM 后端在 `http://127.0.0.1:8090`（见 `vite.config.ts` 的 proxy）。
后端不通时页面仍会渲染，但会提示接口 404 —— 那是预期现象，不是包的问题。

## URL 参数

| 参数 | 说明 | 默认 |
| --- | --- | --- |
| `tab` | `bim` / `pointcloud` / `split` / `alignment` | `alignment` |
| `bimAssetId` | 模型资产 ID | `49` |
| `pointcloudAssetId` | 点云资产 ID | `60` |
| `step` | 配准流程步骤 1–4 | `1` |

## 文件说明

| 文件 | 作用 |
| --- | --- |
| `src/cloudbim/setup.ts` | **必读**。`configureCloudBim()` + 5 个 `style.css` 引入，全项目只需一次 |
| `src/main.ts` | 注册 Element Plus，引入 `./cloudbim/setup`（必须排在 Element Plus 样式之后） |
| `src/App.vue` | 四个页面的 props / 事件接线示例，右下角有事件日志 |
| `vite.config.ts` | 后端代理配置 |

## 三段式接入（照抄 `src/App.vue` 即可）

```vue
<script setup lang="ts">
import { AlignmentPage } from '@cloudbim/alignment'

const handleBack = () => router.push('/survey')
const handleStepChange = (step: number) =>
  router.replace({ query: { ...route.query, step: String(step) } })
</script>

<template>
  <AlignmentPage
    :bim-asset-id="49"
    :pointcloud-asset-id="60"
    bim-display-name="model.glb"
    pointcloud-display-name="scan.las"
    :initial-step="1"
    back-label="返回扫描点云"
    @back="handleBack"
    @step-change="handleStepChange"
  />
</template>
```

包内部不引用 `vue-router`：返回目标、步骤是否写进 URL，全由宿主决定。
若不想用事件，可传 `:navigation="{ back, stepChange }"` 回调，行为一致。

## 换成自己的后端

编辑 `src/cloudbim/setup.ts`：

```ts
setupCloudBim({
  baseUrl: 'https://your-cloudbim.example.com', // 或与宿主同源时留空
  getAccessToken: () => localStorage.getItem('token') ?? '', // 返回空则用 Cookie 鉴权
  dracoDecoderPath: '/draco/',
})
```
