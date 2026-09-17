# @cloudbim/denoise

CloudBIM **设计辅助点云去噪**页。只做一件事：拿「BIM 设计模型 + 已配准的扫描点云」，
跑设计辅助分类与去噪，按类别 / 钢筋实例查看结果，并导出清洗后的钢筋点云。

这个包**可以单独安装使用**：不依赖 `@cloudbim/alignment` 等其它功能包，只依赖
`@cloudbim/viewer-core`。配准矩阵由页面自己从后端读取。

## 安装

随交付目录安装（推荐）：

```bash
node install.mjs --list-features          # 查看可选功能
sh install.sh --features=denoise          # 只装 viewer-core + denoise
```

手动安装：

```bash
npm install vue@^3.5 three@^0.173.0 3d-tiles-renderer@^0.4.19 element-plus @element-plus/icons-vue
npm install ./cloudbim-viewer-core-0.2.0.tgz ./cloudbim-denoise-0.2.0.tgz
```

样式必须显式引入一次：

```ts
// src/main.ts
import ElementPlus from 'element-plus'
import 'element-plus/dist/index.css'
import './cloudbim/setup' // 安装脚本自动插入，内部引入 viewer-core / denoise 的 style.css
```

## 用法

```vue
<script setup lang="ts">
import { DenoisePage } from '@cloudbim/denoise'
import '@cloudbim/denoise/style.css'

const handleBack = () => router.push('/survey')
</script>

<template>
  <DenoisePage
    :bim-asset-id="49"
    :pointcloud-asset-id="60"
    bim-display-name="model.glb"
    pointcloud-display-name="scan.las"
    back-label="返回扫描点云"
    @back="handleBack"
  />
</template>
```

不需要事件时也可以传回调，行为一致：

```vue
<DenoisePage :navigation="{ back: () => router.back() }" ... />
```

### props

| prop | 必填 | 说明 |
| --- | --- | --- |
| `bimAssetId` | 是 | BIM 设计模型资产 ID |
| `pointcloudAssetId` | 是 | 扫描点云资产 ID |
| `bimDisplayName` / `pointcloudDisplayName` | 否 | 页头展示名 |
| `projectName` | 否 | 页头副标题 |
| `backLabel` | 否 | 返回按钮文案，默认「返回扫描点云」 |
| `backgroundTheme` | 否 | `deep`（默认）/ `gradient` / `light` / `black` |
| `navigation` | 否 | `{ back? }` 回调，优先级高于 `back` 事件 |

### 事件

| 事件 | 说明 |
| --- | --- |
| `back` | 请求返回宿主页面 |

## 前置条件（页面会自己检查并给出提示）

1. **无台面点云**：页面读取 `GET /assets/:id/pointcloud-preprocess` 与
   `GET /assets/:id/representations`，需要存在 `table-free` 切片；缺失时页面内可直接
   「补充预处理」（`POST /assets/:id/pointcloud-preprocess`）。
2. **已保存的配准矩阵**：页面调用
   `GET /alignments/bim?modelScanFileId=&modelBimFileId=`，只有拿到 16 位
   `modelMatrix` 才允许运行去噪。缺失时提示「请先完成配准」。
3. **Element Plus**：宿主需 `app.use(ElementPlus)` 并引入其样式。

## 后端接口

| 接口 | 用途 |
| --- | --- |
| `GET /assets/:id` | 资产详情（校验 `status === 'ready'`） |
| `GET /assets/:id/representations` | 选择无台面切片 |
| `GET/POST /assets/:id/pointcloud-preprocess` | 读取 / 补充台面识别与预处理 |
| `GET /alignments/bim` | 读取已保存的配准矩阵 |
| `POST /alignments/bim/denoise` | 运行分类与去噪（可能耗时数分钟） |
| `GET /alignments/bim/denoise/latest` | 读取最新结果与新鲜度 |
| `GET /alignments/bim/denoise/artifacts/preview.ply` | 预览几何（分类 + 实例属性） |
| `GET /alignments/bim/denoise/artifacts/cleaned.las` | 导出清洗后的钢筋点云 |

## 导出的其它内容

```ts
import {
  DenoisePanel,                 // 仅右侧控制面板，可接进你自己的页面
  DENOISE_CLASSES,              // 钢筋 / 夹具 / 台面残留 / 噪声 / 未分类
  parseDenoisePreview,          // preview.ply → BufferGeometry（校验 label 完整性）
  applyDenoisePreviewAppearance,// 按类别或实例着色 + drawRange 过滤
  type DenoiseColorMode,        // 'classes' | 'cleaned'
} from '@cloudbim/denoise'
```

`applyDenoisePreviewAppearance` 直接改几何的颜色属性、索引与 `drawRange`，返回当前可见
点数；配合 `PointcloudPreviewPanel` 的 `setPointcloudOverlay` / `refreshPointcloudOverlay`
可以把自定义几何叠加到查看器里（本页就是这么实现的）。

## 开发

```bash
npm run build -w @cloudbim/denoise                          # 构建（prepack 自动执行）
node --test --experimental-strip-types packages/denoise/src/*.test.ts
```
