# @cloudbim/pointcloud-preview

CloudBIM 扫描点云预览页：3D Tiles 全屏查看、真彩/强度/台面分色与色带、EDL 显示增强、
点大小、台面隐藏与补充预处理、视角导航立方体、剖切，以及测量（测距 / 定位 / 面积）。

## 安装

```bash
npm install @cloudbim/pointcloud-preview @cloudbim/viewer-core vue three 3d-tiles-renderer element-plus @element-plus/icons-vue
```

## 使用

```ts
// main.ts
import { configureCloudBim } from '@cloudbim/viewer-core'
import '@cloudbim/viewer-core/style.css'
import '@cloudbim/pointcloud-preview/style.css'

configureCloudBim({
  baseUrl: import.meta.env.VITE_API_BASE_URL ?? '',
  getAccessToken: () => sessionStorage.getItem('token') ?? '',
})
```

```vue
<script setup lang="ts">
import { PointcloudPreviewPage } from '@cloudbim/pointcloud-preview'
import { useRouter } from 'vue-router'

const router = useRouter()
const handleBack = () => router.push('/survey')
</script>

<template>
  <PointcloudPreviewPage
    :asset-id="5"
    :display-name="'YB-1mesh2.0.las'"
    :project-id="1"
    :project-name="'测试项目'"
    @back="handleBack"
  />
</template>
```

## Props 与事件

| Prop | 类型 | 说明 |
| --- | --- | --- |
| `assetId` | `number \| null` | 点云资产 ID，为空时渲染"缺少选择"状态 |
| `displayName` | `string?` | 页头显示的点云名 |
| `projectId` | `number \| null` | 项目 ID |
| `projectName` | `string?` | 页头副标题中的项目名 |
| `backLabel` | `string?` | 保留字段（本页页头使用关闭按钮，不显示返回文案） |
| `navigation` | `ViewerNavigationHandlers?` | 回调式导航；与 `back` 事件二选一 |

| 事件 | 载荷 | 说明 |
| --- | --- | --- |
| `back` | — | 用户点击"关闭预览"时触发 |

## 后端依赖

- `GET /assets/:id`：点云元数据与 tileset 地址
- `GET /assets/:id/pointcloud-preprocess`、`POST /assets/:id/pointcloud-preprocess`：台面识别与预处理
- `GET /assets/:id/tiles/*`：3D Tiles 数据
- `GET/POST/DELETE /assets/:id/measurements`：测量记录

## 开发

```bash
npm run build -w @cloudbim/pointcloud-preview
```
