# @cloudbim/bim-preview

CloudBIM 设计模型预览页：IFC/GLB 全屏查看、线框与剖切、坐标轴/参考网格、画布背景、
测量（测距 / 定位 / 面积），以及保形网格（重新生成网格）的状态与结果切换。

## 安装

```bash
npm install @cloudbim/bim-preview @cloudbim/viewer-core vue three 3d-tiles-renderer element-plus @element-plus/icons-vue
```

## 使用

```ts
// main.ts
import { configureCloudBim } from '@cloudbim/viewer-core'
import '@cloudbim/viewer-core/style.css'
import '@cloudbim/bim-preview/style.css'

configureCloudBim({
  baseUrl: import.meta.env.VITE_API_BASE_URL ?? '',
  getAccessToken: () => sessionStorage.getItem('token') ?? '',
  dracoDecoderPath: '/draco/',   // 宿主需托管 three 的 Draco 解码器
})
```

```vue
<script setup lang="ts">
import { BimPreviewPage } from '@cloudbim/bim-preview'
import { useRouter } from 'vue-router'

const router = useRouter()
const handleBack = () => router.push('/design/bim')
</script>

<template>
  <BimPreviewPage
    :asset-id="49"
    :display-name="'model.glb'"
    :project-id="1"
    :project-name="'测试项目'"
    back-label="返回模型列表"
    @back="handleBack"
  />
</template>
```

## Props 与事件

| Prop | 类型 | 说明 |
| --- | --- | --- |
| `assetId` | `number \| null` | 模型资产 ID，为空时渲染"缺少选择"状态 |
| `displayName` | `string?` | 页头显示的模型名 |
| `projectId` | `number \| null` | 项目 ID |
| `projectName` | `string?` | 页头副标题中的项目名 |
| `backLabel` | `string?` | 返回按钮文案（如"返回模型列表"），默认"返回" |
| `navigation` | `ViewerNavigationHandlers?` | 回调式导航；与 `back` 事件二选一 |

| 事件 | 载荷 | 说明 |
| --- | --- | --- |
| `back` | 无 | 用户请求返回宿主页面 |

## 后端依赖

- `GET /assets/:id`、`GET /assets/:id/model.glb`：模型与元数据
- `GET /assets/:id/mesh/remesh/status`、`POST /assets/:id/mesh/remesh`：保形网格状态与生成
- `GET /assets/:id/mesh/remesh/latest`：网格结果文件
- `GET/POST/DELETE /assets/:id/measurements`：测量记录

## 其他导出

- `useBimRemeshDisplay(...)`：保形网格显示状态机（可单独复用到其他模型页面）。

## 开发

```bash
npm run build -w @cloudbim/bim-preview
node --test --experimental-strip-types packages/bim-preview/src/*.test.ts
```
