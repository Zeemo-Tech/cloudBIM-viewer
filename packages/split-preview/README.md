# @cloudbim/split-preview

CloudBIM 双屏对比页：设计模型与扫描点云同屏比对，支持视图联动、C2M 偏差分布展示与测量。

## 安装

```bash
npm install @cloudbim/split-preview @cloudbim/viewer-core vue three 3d-tiles-renderer element-plus @element-plus/icons-vue
```

## 使用

```ts
// main.ts
import { configureCloudBim } from '@cloudbim/viewer-core'
import '@cloudbim/viewer-core/style.css'
import '@cloudbim/split-preview/style.css'

configureCloudBim({
  baseUrl: import.meta.env.VITE_API_BASE_URL ?? '',
  getAccessToken: () => sessionStorage.getItem('token') ?? '',
  dracoDecoderPath: '/draco/',
})
```

```vue
<script setup lang="ts">
import { SplitPreviewPage } from '@cloudbim/split-preview'
import { useRouter } from 'vue-router'

const router = useRouter()
const handleBack = () => router.push('/survey')
</script>

<template>
  <SplitPreviewPage
    :bim-asset-id="49"
    :pointcloud-asset-id="60"
    :bim-display-name="'model.glb'"
    :pointcloud-display-name="'scan.las'"
    @back="handleBack"
  />
</template>
```

## Props 与事件

| Prop | 类型 | 说明 |
| --- | --- | --- |
| `bimAssetId` | `number \| null` | 左侧 BIM 资产 ID，为空时渲染"缺少选择"状态 |
| `pointcloudAssetId` | `number \| null` | 右侧点云资产 ID |
| `bimDisplayName` | `string?` | 左屏标题 |
| `pointcloudDisplayName` | `string?` | 右屏标题 |
| `navigation` | `ViewerNavigationHandlers?` | 回调式导航；与 `back` 事件二选一 |

| 事件 | 载荷 | 说明 |
| --- | --- | --- |
| `back` | 无 | 用户请求返回宿主页面 |

## 开发

```bash
npm run build -w @cloudbim/split-preview
```
