# CloudBIM 查看器包 · 接入教程

> 面向对象：拿到 `dist-packages/` 交付目录、要在自己的前端项目里使用这些三维页面的人。
> 适用版本：**0.3.0**（6 个包）

---

## 0. 你会拿到什么

交付目录是**自包含**的，不需要 npm registry、不需要联网（peer 依赖首次安装除外）：

```
dist-packages/
├── cloudbim-viewer-core-0.3.0.tgz          # 基础设施（必装）
├── cloudbim-denoise-0.3.0.tgz               # 点云去噪页
├── cloudbim-bim-preview-0.3.0.tgz           # 模型预览页
├── cloudbim-pointcloud-preview-0.3.0.tgz    # 点云预览页
├── cloudbim-split-preview-0.3.0.tgz         # 双屏对比页
├── cloudbim-alignment-0.3.0.tgz             # 配准与分析工作区（四步流程）
├── manifest.json                            # 清单：版本、sha256、peer 范围、功能分组、接口依赖
├── install.mjs / install.sh                 # 一键安装脚本
├── draco/                                   # three 的 Draco 解码器（3 个文件）
├── example/                                 # 可运行的最小接入示范
├── TUTORIAL.md                              # 本文件
└── README.md                                # 安装步骤速查
```

**包的依赖关系**（安装器会自动补齐）：

```
denoise · bim-preview · pointcloud-preview · split-preview
        └── 都只依赖 viewer-core

alignment ──→ denoise ──→ viewer-core
```

---

## 1. 环境要求

| 项目 | 要求 | 说明 |
| --- | --- | --- |
| Node.js | ≥ 20 | 安装脚本与构建工具需要 |
| Vue | ^3.5.30 | 包以 peerDependency 声明，由你安装 |
| Element Plus | ^2.11.4 | UI 组件库，需在入口 `app.use(ElementPlus)` |
| @element-plus/icons-vue | ^2.3.1 | 图标 |
| three | ^0.173.0 | **0.x 版本 caret 极窄**，见第 7 节 |
| 3d-tiles-renderer | ^0.4.19 | 点云/模型切片加载 |
| 打包器 | 支持 ESM（Vite 最省事） | 库产物为 ESM |

后端要求见第 6 节（响应包络 `{ code, msg, data }`）。

---

## 2. 五分钟接入（三步）

### 第一步：安装

在你的项目根目录执行（把 `<交付目录>` 换成实际路径）：

```bash
sh <交付目录>/install.sh
# 或
node <交付目录>/install.mjs
```

脚本会依次完成：

1. 校验清单与 tarball 完整性（sha256）
2. 探测包管理器（npm / pnpm / yarn）
3. 把交付物复制到项目里的 `.cloudbim/`（之后可 `node .cloudbim/install.mjs` 重装）
4. 按 `manifest.peers` 对齐 peer 依赖版本（缺失或超范围时安装对应版本）
5. 同批次安装全部 `@cloudbim/*` 包（内部依赖靠同批 tarball 解析）
6. 校验 `node_modules` 下各包的 `dist/index.js` 与 `dist/index.d.ts`
7. 把 `draco/` 部署到静态目录（读 Vite 的 `publicDir`，默认 `public/`）
8. 生成 `src/cloudbim/setup.ts`，并把 `import './cloudbim/setup'` 插到 `main.ts` 的最后一条 import 之后

**只想要部分功能**见第 3 节。

### 第二步：确认入口长这样

```ts
// src/main.ts
import { createApp } from 'vue'
import ElementPlus from 'element-plus'
import 'element-plus/dist/index.css' // 必须先于包样式
import './cloudbim/setup'           // 安装脚本自动插到最后一条 import 之后

import App from './App.vue'

createApp(App).use(ElementPlus).mount('#app')
```

> **为什么 `./cloudbim/setup` 必须在最后？**
> 它内部引入各包的 `style.css`，其中含设计令牌（`--color-primary` 等）。ESM 按声明顺序求值，
> 必须排在 `element-plus/dist/index.css` 之后，否则 Element Plus 会覆盖你的主题色。

`src/cloudbim/setup.ts` 由脚本生成，长这样（可自由修改）：

```ts
import { configureCloudBim } from '@cloudbim/viewer-core'
import '@cloudbim/viewer-core/style.css'
import '@cloudbim/denoise/style.css'
// …其余已安装包的 style.css

export function setupCloudBim(overrides = {}) {
  configureCloudBim({
    baseUrl: import.meta.env.VITE_API_BASE_URL ?? '',  // 留空 = 与前端同源
    getAccessToken: () => sessionStorage?.getItem('cloudbim_token') ?? '', // 返回空则由后端 Cookie 鉴权
    dracoDecoderPath: '/draco/',                       // 安装脚本已部署到静态目录
    debug: import.meta.env.DEV,
    ...overrides,
  })
}

setupCloudBim()
```

### 第三步：把页面挂到路由上

所有页面都是「**props 进、事件出**」，包内部**不使用 vue-router**：

```vue
<script setup lang="ts">
import { BimPreviewPage } from '@cloudbim/bim-preview'
import { PointcloudPreviewPage } from '@cloudbim/pointcloud-preview'
import { SplitPreviewPage } from '@cloudbim/split-preview'
import { DenoisePage } from '@cloudbim/denoise'
import { AlignmentPage } from '@cloudbim/alignment'
import { useRoute, useRouter } from 'vue-router'

const route = useRoute()
const router = useRouter()

const goBack = () => router.push('/survey')
const onStepChange = (step: number) =>
  router.replace({ query: { ...route.query, step: String(step) } })
</script>

<template>
  <BimPreviewPage
    :asset-id="49"
    display-name="model.glb"
    :project-id="1"
    project-name="示例项目"
    back-label="返回模型列表"
    @back="goBack"
  />

  <PointcloudPreviewPage :asset-id="60" display-name="scan.las" @back="goBack" />

  <SplitPreviewPage
    :bim-asset-id="49"
    :pointcloud-asset-id="60"
    bim-display-name="model.glb"
    pointcloud-display-name="scan.las"
    @back="goBack"
  />

  <!-- 只要去噪：自己读后端的配准结果，不依赖其它功能包 -->
  <DenoisePage
    :bim-asset-id="49"
    :pointcloud-asset-id="60"
    bim-display-name="model.glb"
    pointcloud-display-name="scan.las"
    @back="goBack"
  />

  <!-- 完整四步流程：配准 → 去噪 → 偏差对比 → 出报告 -->
  <AlignmentPage
    :bim-asset-id="49"
    :pointcloud-asset-id="60"
    bim-display-name="model.glb"
    pointcloud-display-name="scan.las"
    :initial-step="Number(route.query.step) || 1"
    back-label="返回扫描点云"
    @back="goBack"
    @step-change="onStepChange"
  />
</template>
```

不想用事件时可以传回调（优先级高于事件，行为一致）：

```vue
<DenoisePage :navigation="{ back: goBack }" ... />
<AlignmentPage :navigation="{ back: goBack, stepChange: onStepChange }" ... />
```

**先跑示例验证环境**：交付目录自带 `example/`，不用改代码：

```bash
cd example
npm install
npm run dev          # http://localhost:5199
```

它的 `vite.config.ts` 默认把 `/assets`、`/alignments`、`/auth` 代理到 `http://127.0.0.1:8090`；
后端不通时页面照样渲染，只是提示接口 404。

---

## 3. 按需选装

每个包带一个**功能 ID**（写在 `manifest.json` 的 `features` 里）：

| 功能 ID | 包 | 导出组件 | 用途 |
| --- | --- | --- | --- |
| `core` | `@cloudbim/viewer-core` | —（基础设施） | 所有功能的公共前置，必装 |
| `denoise` | `@cloudbim/denoise` | `DenoisePage` | 点云分类与去噪（独立成页） |
| `bim-preview` | `@cloudbim/bim-preview` | `BimPreviewPage` | 设计模型预览（含保形重网格化） |
| `pointcloud-preview` | `@cloudbim/pointcloud-preview` | `PointcloudPreviewPage` | 扫描点云预览（含台面识别） |
| `split-preview` | `@cloudbim/split-preview` | `SplitPreviewPage` | 模型 / 点云双屏对比 |
| `alignment` | `@cloudbim/alignment` | `AlignmentPage` | 完整四步流程（含偏差对比与报告） |

```bash
node install.mjs --list-features        # 列出本交付里可选的功能
sh install.sh --features=denoise        # 只装 viewer-core + denoise（2 个包）
sh install.sh --features=core,denoise   # 等价写法（core 会自动补全）
sh install.sh --features=alignment      # 自动补全 denoise + viewer-core（3 个包）
sh install.sh                           # 不传 = 全部安装（6 个包）
```

不想要的功能**不会被安装**，也不会出现在 `setup.ts` 的样式引入里。

**只要部分步骤？** 不必换包 —— `AlignmentPage` 支持 `steps` 开关：

```vue
<!-- 只要配准 + 去噪两步，后两步（偏差对比、报告）不出现 -->
<AlignmentPage :steps="[1, 2]" ... />
```

四个步骤分别是：`1` 点云与工程坐标配准 · `2` 点云分类与去噪 · `3` 偏差对比 · `4` 出报告。

---

## 4. 各页面 props / 事件对照

| 页面 | 必填 props | 可选 props | 事件 |
| --- | --- | --- | --- |
| `BimPreviewPage` | `assetId` | `displayName`、`projectId`、`projectName`、`backLabel` | `back` |
| `PointcloudPreviewPage` | `assetId` | `displayName`、`projectId`、`projectName`、`backLabel` | `back` |
| `SplitPreviewPage` | `bimAssetId`、`pointcloudAssetId` | `bimDisplayName`、`pointcloudDisplayName`、`projectId`、`projectName`、`backLabel` | `back` |
| `DenoisePage` | `bimAssetId`、`pointcloudAssetId` | `bimDisplayName`、`pointcloudDisplayName`、`projectName`、`backLabel`、`backgroundTheme` | `back` |
| `AlignmentPage` | `bimAssetId`、`pointcloudAssetId` | `bimDisplayName`、`pointcloudDisplayName`、`projectId`、`projectName`、`backLabel`、`initialStep`、`steps` | `back`、`stepChange(step)` |

`assetId` / `bimAssetId` / `pointcloudAssetId` 传 `null` 时页面渲染「缺少选择」的空状态，不会报错。

### 前置条件（页面会自己从后端读取并给出提示）

| 页面 | 前置条件 | 缺失时的表现 |
| --- | --- | --- |
| `BimPreviewPage` | 资产 `status === 'ready'` 且已生成 `model.glb` | 提示模型未就绪 |
| `PointcloudPreviewPage` | 资产就绪且有切片 | 提供「补充预处理」按钮 |
| `DenoisePage` | ① 无台面点云切片 ② 已保存的配准矩阵 | 分别提示「补充预处理」/「请先完成配准」 |
| `AlignmentPage` | 第 1 步需 BIM + 点云；后续步骤有门禁 | 步骤按钮置灰并给出原因 |
| `SplitPreviewPage` | 两侧资产都要就绪 | 提示缺少选择 |

---

## 5. 配置后端与鉴权

所有网络请求都走 `viewer-core` 的统一传输层，配置一次即可：

```ts
import { configureCloudBim } from '@cloudbim/viewer-core'

configureCloudBim({
  baseUrl: 'https://api.example.com',      // 留空 = 与前端同源
  getAccessToken: () => store.token,       // 返回空字符串则由后端 Cookie / 网关鉴权
  onUnauthorized: (error) => router.push('/login'), // 401 时回调
  dracoDecoderPath: '/draco/',             // Draco 解码器静态路径
  debug: false,
})
```

需要更底层能力时，core 还导出了查看器组件与 API：

```ts
import {
  UnifiedViewer3D,          // 裸 three.js 查看器
  PointcloudPreviewPanel,   // 点云面板（可注入自定义几何，见 setPointcloudOverlay）
  MeasurementToolbar,       // 测量工具条
  backendRequest,           // 统一传输层
  C2MHistogramLegend,       // 偏差直方图图例
  // …以及配准矩阵换算、C2M 色彩工具、钢筋可视化等
} from '@cloudbim/viewer-core'
```

---

## 6. 后端接口清单

响应统一为 `{ code, msg, data }` 包络。各包实际使用的接口（也记录在 `manifest.json` 里）：

**`@cloudbim/viewer-core`**
- `GET /assets/:id`、`GET /assets/:id/tiles/*`
- `GET/POST/DELETE /assets/:id/measurements`

**`@cloudbim/denoise`**
- `GET /assets/:id`、`GET /assets/:id/representations`
- `GET/POST /assets/:id/pointcloud-preprocess`
- `GET /alignments/bim`（读取已保存的配准矩阵）
- `POST /alignments/bim/denoise`、`GET /alignments/bim/denoise/latest`
- `GET /alignments/bim/denoise/artifacts/preview.ply`、`…/cleaned.las`

**`@cloudbim/bim-preview`**
- `GET /assets/:id`、`GET /assets/:id/model.glb`
- `GET /assets/:id/mesh/remesh/status`、`POST /assets/:id/mesh/remesh`、`GET /assets/:id/mesh/remesh/latest`

**`@cloudbim/pointcloud-preview`**
- `GET /assets/:id`、`GET /assets/:id/tiles/*`、`GET/POST /assets/:id/pointcloud-preprocess`

**`@cloudbim/split-preview`**
- `GET /assets/:id`、`GET /assets/:id/model.glb`、`GET /assets/:id/tiles/*`

**`@cloudbim/alignment`**
- `GET /assets/:id`、`/assets/:id/tiles/*`、`/assets/:id/representations`
- `POST /alignments/bim`、`POST /alignments/bim/fine`
- `POST /alignments/bim/denoise`、`GET /alignments/bim/denoise/latest`
- `POST /alignments/bim/c2m`、`GET /alignments/bim/c2m/latest`、`POST /alignments/bim/analysis-c2m`
- `GET/POST/DELETE /assets/:id/measurements`

---

## 7. 常见问题排查

### 页面渲染出来了，但完全没有样式

库模式构建会把 SFC 样式抽取成独立 `style.css`，**包不会自动注入**。检查：

1. `src/cloudbim/setup.ts` 是否存在且被 `main.ts` 引入
2. 它是否引入了**每个已安装包**的 `style.css`（漏一个包，那个包的组件就没样式）
3. `import './cloudbim/setup'` 是否排在 `element-plus/dist/index.css` 之后

### 主题色不对 / Element Plus 被覆盖

包的 `style.css` 里含设计令牌，顺序必须在 Element Plus 样式**之后**。把 `./cloudbim/setup` 放到入口的最后一条 import。

### 安装时报 ERESOLVE / peer 冲突

`three`、`3d-tiles-renderer` 处于 **0.x**，caret 范围语义很窄：`^0.173.0` 等价于 `>=0.173.0 <0.174.0`。
**不要直接 `npm i three` 装 latest**，让安装脚本按 `manifest.peers` 的范围装：

```bash
node install.mjs          # 会自动对齐；也可先 --dry-run 看它打算装什么
```

### 模型是黑色的 / 加载不出来

Draco 解码器没部署。确认静态目录下有 `/draco/`（3 个文件），且 `dracoDecoderPath` 与实际路径一致：

```bash
ls public/draco/          # 应有 draco_decoder.js / draco_decoder.wasm / draco_wasm_wrapper.js
```

### 页面一片空白 / 提示 401

包的请求默认带 `Authorization` 头（来自 `getAccessToken`）。若你的后台用 Cookie 鉴权，让 `getAccessToken` 返回空字符串；
`configureCloudBim({ onUnauthorized })` 可接管 401 跳登录。

### 想只显示部分步骤

用 `:steps="[1, 2]"`（见第 3 节），不要为了裁剪步骤去改包源码。

### 升级后页面报错

清掉打包器缓存（Vite 的 `node_modules/.vite`）后重启 dev server；包是按 tarball 安装的，
升级需要**重新跑一次安装脚本**，不会自动跟随你的仓库更新。

---

## 8. 升级与维护

安装脚本是**幂等**的：已满足的 peer 会跳过、已存在的 `setup.ts` 会保留（`--force` 可覆盖）、
`main.ts` 不会重复插入 import。

```bash
# 拿到新版本的 dist-packages 后，在项目根目录重跑
sh <新交付目录>/install.sh

# 常用选项
node install.mjs --dry-run              # 只打印将执行的命令
node install.mjs --features=denoise     # 换一组功能重装
node install.mjs --no-draco             # 跳过 Draco 部署（你自己管理）
node install.mjs --project ./frontend   # 指定项目根目录
```

版本对照：`manifest.json` 里的 `version` 与各 tarball 的文件名一致，交付前可用它核对。

---

## 9. 不使用脚本的手动安装

```bash
npm install vue@^3.5.30 three@^0.173.0 3d-tiles-renderer@^0.4.19 \
            element-plus@^2.11.4 @element-plus/icons-vue@^2.3.1

# 只装去噪：
npm install ./cloudbim-viewer-core-0.3.0.tgz ./cloudbim-denoise-0.3.0.tgz

# 或全装：
npm install ./cloudbim-viewer-core-0.3.0.tgz ./cloudbim-denoise-0.3.0.tgz \
            ./cloudbim-alignment-0.3.0.tgz ./cloudbim-bim-preview-0.3.0.tgz \
            ./cloudbim-pointcloud-preview-0.3.0.tgz ./cloudbim-split-preview-0.3.0.tgz
```

⚠️ **所有 tarball 必须在同一条命令里安装**：功能包依赖 `@cloudbim/viewer-core@^0.3.0`，
而 registry 中没有该包，只能由同批 tarball 满足。

手动安装时别忘了：引入各包 `style.css`、部署 `draco/`、调用 `configureCloudBim`。
