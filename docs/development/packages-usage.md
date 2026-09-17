# 查看器包使用指南（workspace 内部交付）

> 📘 **面向外部集成方的完整教程**见 `docs/development/packages-tutorial.md`
> （随交付目录一起发出，文件名 `TUTORIAL.md`）：五分钟上手、按需选装、各页面 props/事件表、
> 后端接口清单与排错手册。本文侧重 workspace 内部的开发与交付流程。

本仓库同时是 **包的源码** 与 **宿主示例应用**。包位于 `packages/`，通过 npm workspaces
链接；应用（`src/`）是第一个消费者，也是可运行的参考实现。

## 包一览

| 包 | 内容 | 入口组件 |
| --- | --- | --- |
| `@cloudbim/viewer-core` | Three.js 查看器内核、测量工具、后端 API 客户端、C2M/analysis-mesh/钢筋可视化算法、设计令牌 | —（基础设施 + 查看器组件） |
| `@cloudbim/bim-preview` | 设计模型预览页 | `BimPreviewPage` |
| `@cloudbim/pointcloud-preview` | 扫描点云预览页 | `PointcloudPreviewPage` |
| `@cloudbim/split-preview` | 模型 / 点云双屏对比页 | `SplitPreviewPage` |
| `@cloudbim/denoise` | 设计辅助点云去噪（可单独安装使用） | `DenoisePage` |
| `@cloudbim/alignment` | 配准与分析工作区（四步流程，去噪步骤复用 `@cloudbim/denoise`） | `AlignmentPage` |

依赖方向：功能包 / 页面包 → `@cloudbim/viewer-core`；`@cloudbim/alignment` 额外依赖
`@cloudbim/denoise`。除此之外各包之间没有依赖，可以只装需要的那一个（见 3.2.1）。

## 快速开始

最短路径是一条命令——在宿主项目根目录执行交付目录里的安装脚本：

```bash
sh /path/to/dist-packages/install.sh
```

它会自动完成下面「宿主需要做三件事」里的全部机械步骤：对齐 peer 依赖版本、
安装包、部署 Draco 解码器、生成 `src/cloudbim/setup.ts` 并接入 `main.ts`。

只想要某一项功能时加 `--features`，例如只装去噪：

```bash
node /path/to/dist-packages/install.mjs --list-features      # 看有哪些可选
sh /path/to/dist-packages/install.sh --features=denoise      # 只装 viewer-core + denoise
```
每一步的细节见第三节「构建与交付」。

## 一、宿主需要做三件事

### 1. 安装 peer 依赖

```bash
npm install @cloudbim/viewer-core @cloudbim/alignment \
  vue three 3d-tiles-renderer element-plus @element-plus/icons-vue
```

`vue` / `three` / `3d-tiles-renderer` / `element-plus` / `@element-plus/icons-vue`
都是 peerDependencies，必须由宿主安装一次——否则会出现多份 Vue、Three 实例与重复主题。

### 2. 引入样式并配置运行时

```ts
// main.ts
import { configureCloudBim } from '@cloudbim/viewer-core'
import '@cloudbim/viewer-core/style.css'   // 设计令牌 + 查看器样式
import '@cloudbim/alignment/style.css'     // 按需引入用到的页面包样式

configureCloudBim({
  baseUrl: import.meta.env.VITE_API_BASE_URL ?? '', // 留空 = 与宿主同源
  getAccessToken: () => sessionStorage.getItem('token') ?? '', // 空值则用 Cookie
  debug: import.meta.env.DEV,
  dracoDecoderPath: '/draco/',  // 宿主需把 three 的 Draco 解码器放在该目录
  onUnauthorized: () => { /* 可选：401 时跳登录 */ },
})
```

包**不读取** `import.meta.env`、**不依赖**宿主的登录态实现，因此同一套包可以部署在
同源反代或独立域名下。

### 3. 用 props 传数据、用事件收导航

```vue
<script setup lang="ts">
import { AlignmentPage } from '@cloudbim/alignment'
import { useRoute, useRouter } from 'vue-router'

const route = useRoute()
const router = useRouter()

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

包内部不引用 `vue-router`：返回目标、步骤是否写进 URL，全部由宿主决定。
若宿主不想用事件，也可以传 `:navigation="{ back, stepChange }"` 回调，行为一致。

## 二、常用能力（按需从 core 取）

```ts
import {
  backendRequest,            // 统一传输层（带 baseUrl / token / 错误归一化）
  normalizeBackendUrl,       // 把相对路径补成绝对 URL（用于 <img>/<video> 等）
  UnifiedViewer3D,           // 裸查看器组件，可自行组装页面
  BimPreviewPanel,           // 模型面板
  PointcloudPreviewPanel,    // 点云面板
  MeasurementToolbar,        // 测量工具条
  POINTCLOUD_CATEGORY_COLORS,// 点云分类配色
  formatFileSize,            // 体积格式化
  getAssetDetail,            // 资产接口
  createMeasurement,         // 测量记录接口
  type ViewerAssetPairProps, // 页面 props 契约
  type ViewerPageEmits,
} from '@cloudbim/viewer-core'
```

深导入（可选，用于避开同名符号）：

```ts
import { parseC2MDistances } from '@cloudbim/viewer-core/utils/c2mColormap'
import { parseC2MDistances as parseAnalysisMeshDistances } from '@cloudbim/viewer-core/features/analysis-mesh/contracts'
```

> `parseC2MDistances` 在两个模块里语义不同：`utils/c2mColormap` 版本返回
> `Float32Array | null`，`analysis-mesh/contracts` 版本在顶点数不匹配时抛错。
> 包的顶层入口里后者叫 `parseAnalysisMeshDistances`。

## 三、构建与交付

```bash
npm run build            # 按依赖顺序构建全部包，再构建宿主应用
npm run build:packages   # 只构建包（core → 各页面包）
npm run pack:packages    # 产出可交付的 dist-packages/，见下节
```

### 3.1 交付产物

`npm run pack:packages` 会生成一个**自包含**的交付目录，整个目录拷给对方即可（无需 registry）：

```
dist-packages/
  cloudbim-viewer-core-0.3.0.tgz        6 个可 npm install 的 tarball
  cloudbim-denoise-0.3.0.tgz
  cloudbim-bim-preview-0.3.0.tgz
  cloudbim-pointcloud-preview-0.3.0.tgz
  cloudbim-split-preview-0.3.0.tgz
  cloudbim-alignment-0.3.0.tgz
  manifest.json                         包清单：版本、体积、sha256、peer 范围、接口依赖、功能分组
  install.mjs / install.sh              宿主侧一键安装脚本（支持 --features 按功能装）
  draco/                                three 的 Draco 解码器（安装脚本会部署到宿主静态目录）
  example/                              可运行的接入示例（含 props / 事件接线示范）
  README.md                             交付说明（自动生成：安装步骤 + 代码示例 + props/事件表）
```

### 3.2 一键安装（推荐）

在宿主项目根目录执行一条命令：

```bash
sh /path/to/dist-packages/install.sh
# 或
node /path/to/dist-packages/install.mjs
```

脚本依次完成：

1. 校验交付清单与 tarball 完整性
2. 探测包管理器（npm / pnpm / yarn，可用 `--pm` 指定）
3. 把交付物复制到宿主 `.cloudbim/`（同时写入 `.gitignore`，之后可 `node .cloudbim/install.mjs` 重装）
4. **按 manifest 声明范围对齐 peer 依赖**——这一步很关键，见下方「0.x 版本陷阱」
5. 同批次安装选中的 `@cloudbim/*` 包（默认全部；`--features` 时只装指定功能 + 依赖闭包）
6. 校验 `node_modules` 下的 `dist/index.js` 与 `dist/index.d.ts`
7. 部署 `draco/` 到宿主静态目录（读 vite `publicDir`，缺省 `public/`）
8. 生成 `src/cloudbim/setup.ts`，并把 `import './cloudbim/setup'` 插到 `main.ts` **最后一条 import 之后**

常用选项：

| 选项 | 说明 |
| --- | --- |
| `--project <dir>` | 宿主项目根目录（默认当前目录） |
| `--from <dir>` | 交付目录（默认脚本所在目录） |
| `--pm <npm\|pnpm\|yarn>` | 指定包管理器 |
| `--features <list>` | 只安装指定功能（逗号分隔，如 `denoise` 或 `core,denoise`），依赖的前置包自动补全 |
| `--list-features` | 列出交付目录内可用的功能后退出 |
| `--dry-run` | 只打印将要执行的命令，不做任何改动 |
| `--no-peer` / `--no-draco` / `--no-patch` / `--no-vendor` | 跳过对应步骤 |
| `--force` | 覆盖已存在的 `src/cloudbim/setup.ts` |
| `--help` | 显示用法 |

脚本可重复执行：已装好的 peer 会跳过，已存在的 `setup.ts` 会保留，`main.ts` 不会重复插入。

> **为什么 `import './cloudbim/setup'` 必须在最后一条 import？**
> ESM 按声明顺序求值。包的 `style.css` 含设计令牌（`--color-primary` 等），
> 需要排在 `element-plus/dist/index.css` 之后加载，因此脚本刻意插在末尾而非文件顶部。

### 3.2.1 按功能安装

交付目录里的每个包都带一个功能 ID（写在 `manifest.json` 的 `features` 字段里）：

| 功能 ID | 包 | 说明 |
| --- | --- | --- |
| `core` | `@cloudbim/viewer-core` | 基础设施，所有功能的前置 |
| `denoise` | `@cloudbim/denoise` | 点云分类与去噪 |
| `bim-preview` | `@cloudbim/bim-preview` | 设计模型预览 |
| `pointcloud-preview` | `@cloudbim/pointcloud-preview` | 扫描点云预览 |
| `split-preview` | `@cloudbim/split-preview` | 双屏对比 |
| `alignment` | `@cloudbim/alignment` | 四步配准与分析工作区 |

```bash
node install.mjs --list-features          # 列出可用功能
sh install.sh --features=denoise          # 只装 viewer-core + denoise
sh install.sh --features=core,denoise     # 等价写法（core 会自动补全）
sh install.sh --features=alignment        # 自动补全 denoise + viewer-core
```

安装器只安装选中的包，并按其 `dependencies` 字段补齐前置包；不传 `--features` 时行为不变（装全部）。
交付目录里的 tarball 会全部 vendor 到宿主 `.cloudbim/`，所以之后可以随时
`node .cloudbim/install.mjs --features=...` 换一组功能重装。

> `@cloudbim/alignment` 是**元包**：它把 `DenoisePage` 的去噪能力接进四步流程，因此依赖
> `@cloudbim/denoise`。只去噪、不做配准流程时，装 `--features=denoise` 即可，不必装 alignment。

### 3.2.2 交给别人的示例项目

交付目录里的 `example/` 是一个**可运行**的最小接入示范，不依赖本仓库任何源码，
只依赖 `../` 下的 tarball，可直接给别人参考或改造：

```bash
cd example && npm install && npm run dev    # http://localhost:5199
```

- `src/cloudbim/setup.ts`：`configureCloudBim()` + 5 个 `style.css` 引入（全项目只需一次）
- `src/App.vue`：四个页面的 props / 事件接线，右下角有事件日志，可直接看到 `back` / `stepChange` 被宿主接收
- `vite.config.ts`：`/assets`、`/alignments`、`/auth`、`/system` 代理到 `http://127.0.0.1:8090`

源码模板在仓库 `examples/consumer-demo/`，`npm run pack:packages` 时复制进交付目录，
其 `package.json` 由 `manifest.json` 生成，保证包路径与 peer 范围始终与本次交付一致。

### 3.3 0.x 版本陷阱

`three` 与 `3d-tiles-renderer` 处于 0.x，**caret 范围语义很窄**：

- `^0.173.0` ≡ `>=0.173.0 <0.174.0`
- `^0.4.19` ≡ `>=0.4.19 <0.5.0`

所以 `npm install three 3d-tiles-renderer`（装 latest）会直接 ERESOLVE 失败。
务必按 `manifest.json` 的 `peers` 字段安装，一键脚本已自动处理。

### 3.4 手动安装（等价做法）

```bash
cd /path/to/other-app
npm install three@^0.173.0 3d-tiles-renderer@^0.4.19 vue element-plus @element-plus/icons-vue
npm install /path/to/cloudBIM-viewer/dist-packages/cloudbim-viewer-core-0.3.0.tgz \
            /path/to/cloudBIM-viewer/dist-packages/cloudbim-denoise-0.3.0.tgz \
            /path/to/cloudBIM-viewer/dist-packages/cloudbim-bim-preview-0.3.0.tgz \
            /path/to/cloudBIM-viewer/dist-packages/cloudbim-pointcloud-preview-0.3.0.tgz \
            /path/to/cloudBIM-viewer/dist-packages/cloudbim-split-preview-0.3.0.tgz \
            /path/to/cloudBIM-viewer/dist-packages/cloudbim-alignment-0.3.0.tgz
```

所有 tarball 必须在**同一条命令**里安装：功能包依赖 `@cloudbim/viewer-core@^0.3.0`，
而 registry 中没有该包，只能由同批 tarball 满足。

或先用 `npm link` 做开发期联调：

```bash
cd packages/viewer-core && npm link
cd /path/to/other-app && npm link @cloudbim/viewer-core
```


## 四、注意事项

- **样式令牌**：包内组件使用 CSS 自定义属性（`--color-primary` 等）。宿主必须引入
  `@cloudbim/viewer-core/style.css`，否则控件会失去配色。
- **Draco 解码器**：宿主需把 three 的 `draco_decoder.js` / `.wasm` 放到
  `dracoDecoderPath` 指定的目录（本仓库在 `public/draco/`，构建后随应用发布）。
- **Element Plus**：页面包大量使用 Element Plus 组件与图标，宿主需安装并注册。
- **构建顺序**：`@cloudbim/viewer-core` 必须先于页面包构建（根脚本已固定顺序）。
- **更新包后的宿主**：因为宿主消费的是 `dist/`，改包源码后要重新 `npm run build:packages`
  （`npm run dev` 已通过 `predev` 自动执行一次）。
