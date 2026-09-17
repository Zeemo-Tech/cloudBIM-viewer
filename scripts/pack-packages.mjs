import { execFileSync } from 'node:child_process'
import { createHash } from 'node:crypto'
import {
  chmodSync,
  copyFileSync,
  cpSync,
  mkdirSync,
  readFileSync,
  readdirSync,
  rmSync,
  statSync,
  writeFileSync,
} from 'node:fs'
import { dirname, join, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

/**
 * 构建全部工作区包，并把可直接交付的安装包写入 `dist-packages/`。
 *
 * 产物：
 *   dist-packages/
 *     cloudbim-*.tgz        5 个可 npm install 的 tarball（prepack 已确保构建最新）
 *     manifest.json         包清单：peer 版本范围、体积、sha256、接口依赖
 *     install.mjs           宿主侧一键安装脚本
 *     install.sh            同一脚本的 shell 包装
 *     draco/                three 的 Draco 解码器（安装脚本会部署到宿主静态目录）
 *     README.md             交付说明
 *
 * 用法：
 *   npm run pack:packages
 */
const root = resolve(dirname(fileURLToPath(import.meta.url)), '..')
const packagesRoot = join(root, 'packages')
const outputDir = join(root, 'dist-packages')
const dracoSource = join(root, 'public', 'draco')
const dracoPath = 'draco'

/** 各包对外导出的页面组件与接口依赖，写进 manifest 供安装脚本与交付文档使用。 */
const packageMeta = {
  '@cloudbim/viewer-core': {
    feature: 'core',
    kind: '基础设施',
    description: 'Three.js 查看器内核、测量工具、后端 API 客户端、设计令牌',
    components: [],
    endpoints: [
      'GET /assets/:id',
      'GET /assets/:id/tiles/*',
      'GET/POST/DELETE /assets/:id/measurements',
    ],
  },
  '@cloudbim/denoise': {
    feature: 'denoise',
    kind: '功能页',
    description: '设计辅助点云去噪（按类别/实例着色、类别显隐、导出清洗点云）',
    components: ['DenoisePage'],
    endpoints: [
      'GET /assets/:id、GET /assets/:id/representations',
      'GET/POST /assets/:id/pointcloud-preprocess',
      'GET /alignments/bim（读取已保存的配准矩阵）',
      'POST /alignments/bim/denoise、GET /alignments/bim/denoise/latest',
      'GET /alignments/bim/denoise/artifacts/preview.ply、…/cleaned.las',
    ],
  },
  '@cloudbim/bim-preview': {
    feature: 'bim-preview',
    kind: '页面',
    description: '设计模型预览（含保形重网格化）',
    components: ['BimPreviewPage'],
    endpoints: [
      'GET /assets/:id',
      'GET /assets/:id/model.glb',
      'GET /assets/:id/mesh/remesh/status',
      'POST /assets/:id/mesh/remesh',
      'GET /assets/:id/mesh/remesh/latest',
      'GET/POST/DELETE /assets/:id/measurements',
    ],
  },
  '@cloudbim/pointcloud-preview': {
    feature: 'pointcloud-preview',
    kind: '页面',
    description: '扫描点云预览（含台面识别与预处理）',
    components: ['PointcloudPreviewPage'],
    endpoints: [
      'GET /assets/:id',
      'GET /assets/:id/tiles/*',
      'GET/POST /assets/:id/pointcloud-preprocess',
      'GET/POST/DELETE /assets/:id/measurements',
    ],
  },
  '@cloudbim/split-preview': {
    feature: 'split-preview',
    kind: '页面',
    description: '模型 / 点云双屏对比',
    components: ['SplitPreviewPage'],
    endpoints: [
      'GET /assets/:id',
      'GET /assets/:id/model.glb',
      'GET /assets/:id/tiles/*',
    ],
  },
  '@cloudbim/alignment': {
    feature: 'alignment',
    kind: '页面',
    description: '配准与分析工作区（粗配准、精细配准、去噪、C2M、钢筋对比）',
    components: ['AlignmentPage'],
    endpoints: [
      'GET /assets/:id、/assets/:id/tiles/*、/assets/:id/representations',
      'POST /alignments/bim、POST /alignments/bim/fine',
      'POST /alignments/bim/denoise、GET /alignments/bim/denoise/latest',
      'POST /alignments/bim/c2m、GET /alignments/bim/c2m/latest、POST /alignments/bim/analysis-c2m',
      'GET/POST/DELETE /assets/:id/measurements',
    ],
  },
}

const readJson = (file) => JSON.parse(readFileSync(file, 'utf8'))
const sha256 = (file) => createHash('sha256').update(readFileSync(file)).digest('hex')

rmSync(outputDir, { recursive: true, force: true })
mkdirSync(outputDir, { recursive: true })

// 按包间依赖做拓扑排序：页面包的 prepack 依赖其 @cloudbim/* 依赖已产出 dist/。
function orderByLocalDependencies(names) {
  const localDeps = new Map()
  for (const name of names) {
    const pkgJson = readJson(join(packagesRoot, name, 'package.json'))
    const deps = Object.keys(pkgJson.dependencies ?? {})
      .filter((dep) => dep.startsWith('@cloudbim/'))
      .map((dep) => dep.replace('@cloudbim/', ''))
      .filter((dep) => names.includes(dep))
    localDeps.set(name, deps)
  }

  const ordered = []
  const visiting = new Set()
  const visit = (name) => {
    if (ordered.includes(name)) return
    if (visiting.has(name)) {
      throw new Error(`pack-packages: 依赖存在循环：${[...visiting, name].join(' → ')}`)
    }
    visiting.add(name)
    for (const dep of localDeps.get(name) ?? []) visit(dep)
    visiting.delete(name)
    ordered.push(name)
  }
  for (const name of [...names].sort()) visit(name)
  return ordered
}

const names = orderByLocalDependencies(
  readdirSync(packagesRoot).filter((name) => statSync(join(packagesRoot, name)).isDirectory()),
)

if (!names.length) {
  console.error('pack-packages: packages/ 目录下没有可打包的工作区')
  process.exit(1)
}

for (const name of names) {
  console.log(`\n=== packing ${name} ===`)
  execFileSync('npm', ['pack', '--pack-destination', outputDir], {
    cwd: join(packagesRoot, name),
    stdio: 'inherit',
  })
}

/* ------------------------------------------------------------- manifest --- */

const packages = []
const peers = {}
for (const name of names) {
  const pkgJson = readJson(join(packagesRoot, name, 'package.json'))
  const file = `${pkgJson.name.replace('@', '').replace('/', '-')}-${pkgJson.version}.tgz`
  const tarball = join(outputDir, file)
  const meta = packageMeta[pkgJson.name] ?? {}

  for (const [peer, range] of Object.entries(pkgJson.peerDependencies ?? {})) {
    if (!(peer in peers)) peers[peer] = range
    else if (peers[peer] !== range) {
      // 同一 peer 在不同包里范围不一致会让宿主更难满足，提醒维护者对齐。
      console.warn(`pack-packages: peer ${peer} 范围不一致：${peers[peer]} vs ${range}`)
    }
  }

  packages.push({
    name: pkgJson.name,
    version: pkgJson.version,
    file,
    bytes: statSync(tarball).size,
    sha256: sha256(tarball),
    feature: meta.feature ?? name,
    kind: meta.kind ?? '库',
    description: meta.description ?? pkgJson.description ?? '',
    components: meta.components ?? [],
    // 宿主按功能安装时，安装器靠这份依赖闭包自动补齐前置包。
    dependencies: Object.keys(pkgJson.dependencies ?? {}).filter((dep) => dep.startsWith('@cloudbim/')),
    endpoints: meta.endpoints ?? [],
    hasStyle: statSync(join(packagesRoot, name, 'dist', 'style.css'), { throwIfNoEntry: false }) != null,
  })
}

const dracoFiles = statSync(dracoSource, { throwIfNoEntry: false })?.isDirectory()
  ? readdirSync(dracoSource).filter((name) => statSync(join(dracoSource, name)).isFile())
  : []

const version = packages[0]?.version ?? '0.0.0'
const features = packages.map((pkg) => ({
  feature: pkg.feature,
  package: pkg.name,
  kind: pkg.kind,
  description: pkg.description,
  components: pkg.components,
}))
const manifest = {
  name: 'cloudbim-viewer-packages',
  version,
  generatedAt: new Date().toISOString(),
  nodeEngine: '>=20',
  peers,
  packages,
  // 安装器用 `--features=denoise` 时按此清单挑包，再按 dependencies 补齐前置。
  features,
  draco: { dir: dracoPath, files: dracoFiles },
  dracoPath,
}
writeFileSync(join(outputDir, 'manifest.json'), `${JSON.stringify(manifest, null, 2)}\n`, 'utf8')

/* ------------------------------------------------------- 安装脚本与说明 --- */

copyFileSync(join(root, 'scripts', 'install-cloudbim.mjs'), join(outputDir, 'install.mjs'))
writeFileSync(
  join(outputDir, 'install.sh'),
  `#!/bin/sh
# CloudBIM 查看器包 · 一键安装（install.mjs 的 shell 包装）
set -e
DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
exec node "$DIR/install.mjs" "$@"
`,
  'utf8',
)

if (dracoFiles.length) {
  mkdirSync(join(outputDir, dracoPath), { recursive: true })
  for (const file of dracoFiles) {
    cpSync(join(dracoSource, file), join(outputDir, dracoPath, file))
  }
}

/* ------------------------------------------------------------- 示例项目 --- */

const exampleSource = join(root, 'examples', 'consumer-demo')
let hasExample = false
if (statSync(exampleSource, { throwIfNoEntry: false })?.isDirectory()) {
  const exampleDir = join(outputDir, 'example')
  cpSync(exampleSource, exampleDir, { recursive: true })

  // package.json 由清单生成：包的路径、peer 范围、构建工具版本始终与本次交付一致。
  const rootDevDeps = readJson(join(root, 'package.json')).devDependencies ?? {}
  writeFileSync(
    join(exampleDir, 'package.json'),
    `${JSON.stringify(
      {
        name: 'cloudbim-consumer-demo',
        private: true,
        version: '0.0.0',
        type: 'module',
        scripts: { dev: 'vite', build: 'vite build', preview: 'vite preview' },
        dependencies: {
          ...Object.fromEntries(packages.map((pkg) => [pkg.name, `file:../${pkg.file}`])),
          ...peers,
        },
        devDependencies: {
          '@vitejs/plugin-vue': rootDevDeps['@vitejs/plugin-vue'] ?? '^6.0.5',
          vite: rootDevDeps.vite ?? '^8.0.0',
        },
      },
      null,
      2,
    )}\n`,
    'utf8',
  )
  hasExample = true
}

const sizeText = (bytes) =>
  bytes > 1024 * 1024 ? `${(bytes / 1024 / 1024).toFixed(1)} MB` : `${Math.round(bytes / 1024)} KB`

const packageRows = packages
  .map(
    (pkg) =>
      `| \`${pkg.name}\` | \`${pkg.feature}\` | ${pkg.kind} | ${pkg.components.join('、') || '—'} | ${sizeText(pkg.bytes)} |`,
  )
  .join('\n')

const peerRows = Object.entries(peers)
  .map(([peer, range]) => `| \`${peer}\` | \`${range}\` |`)
  .join('\n')

const endpointList = packages
  .filter((pkg) => pkg.endpoints.length)
  .map((pkg) => `- \`${pkg.name}\`\n${pkg.endpoints.map((endpoint) => `  - \`${endpoint}\``).join('\n')}`)
  .join('\n')

writeFileSync(
  join(outputDir, 'README.md'),
  `# CloudBIM 查看器包 · 交付包

版本 ${version} · 生成于 ${manifest.generatedAt}

本目录是**自包含**的交付单元：包含 ${packages.length} 个 npm 包、peer 版本要求、Draco 解码器与安装脚本，
拷贝到目标机器即可离线安装。

可以整包装，也可以**只装需要的功能**：

\`\`\`bash
node install.mjs --list-features        # 查看本交付里可选的功能
sh install.sh --features=denoise        # 只装查看器内核 + 去噪页
sh install.sh --features=alignment      # 自动补全前置包（denoise → viewer-core）
\`\`\`

## 一键安装

在宿主项目根目录执行：

\`\`\`bash
sh /path/to/dist-packages/install.sh
# 或
node /path/to/dist-packages/install.mjs
\`\`\`

脚本依次完成：

1. 校验交付清单与 tarball 完整性
2. 探测包管理器（npm / pnpm / yarn）
3. 把交付物复制到宿主 \`.cloudbim/\`（之后可 \`node .cloudbim/install.mjs\` 重装）
4. 按声明范围对齐 peer 依赖（缺失或超范围时安装对应版本）
5. 同批次安装选中的 @cloudbim 包（内部依赖靠同批 tarball 解析，registry 中不存在）
6. 校验 \`node_modules\` 下的产物入口
7. 部署 Draco 解码器到宿主静态目录
8. 生成 \`src/cloudbim/setup.ts\` 并接入 \`src/main.ts\`

常用选项：\`--features <list>\`、\`--list-features\`、\`--project <dir>\`、\`--pm <npm|pnpm|yarn>\`、\`--dry-run\`、\`--no-patch\`、\`--no-vendor\`、\`--help\`。

## 目录内容

| 路径 | 说明 |
| --- | --- |
| \`cloudbim-*.tgz\` | ${packages.length} 个可 \`npm install\` 的 tarball |
| \`manifest.json\` | 包清单：名称、版本、体积、sha256、peer 范围、接口依赖 |
| \`install.mjs\` / \`install.sh\` | 宿主侧一键安装脚本 |
| \`${dracoPath}/\` | three 的 Draco 解码器（${dracoFiles.length} 个文件） |
| \`example/\` | 可运行的接入示例：\`cd example && npm install && npm run dev\` |
| \`README.md\` | 本文件 |

## 别人怎么用：三步

### 第一步：装进你的项目

在宿主项目根目录执行（见上一节），脚本会装包、配 peer、部署 Draco、生成 \`src/cloudbim/setup.ts\` 并接进 \`main.ts\`。

### 第二步：确认入口长这样

\`\`\`ts
// src/main.ts
import { createApp } from 'vue'
import ElementPlus from 'element-plus'
import 'element-plus/dist/index.css' // 必须先于包样式
import './cloudbim/setup'           // 安装脚本自动插入到最后一条 import 之后

import App from './App.vue'

createApp(App).use(ElementPlus).mount('#app')
\`\`\`

### 第三步：把页面挂到路由上

各页面都是「props 进、事件出」，内部**不引用 vue-router**：

\`\`\`vue
<script setup lang="ts">
import { BimPreviewPage } from '@cloudbim/bim-preview'
import { PointcloudPreviewPage } from '@cloudbim/pointcloud-preview'
import { SplitPreviewPage } from '@cloudbim/split-preview'
import { AlignmentPage } from '@cloudbim/alignment'
import { DenoisePage } from '@cloudbim/denoise'
import { useRoute, useRouter } from 'vue-router'

const route = useRoute()
const router = useRouter()

const goBack = () => router.push('/survey')
const onStepChange = (step: number) =>
  router.replace({ query: { ...route.query, step: String(step) } })
</script>

<template>
  <!-- 模型预览 -->
  <BimPreviewPage
    :asset-id="49"
    display-name="model.glb"
    :project-id="1"
    project-name="示例项目"
    back-label="返回模型列表"
    @back="goBack"
  />

  <!-- 点云预览 -->
  <PointcloudPreviewPage
    :asset-id="60"
    display-name="scan.las"
    :project-id="1"
    project-name="示例项目"
    back-label="返回扫描列表"
    @back="goBack"
  />

  <!-- 双屏对比：一对资产 -->
  <SplitPreviewPage
    :bim-asset-id="49"
    :pointcloud-asset-id="60"
    bim-display-name="model.glb"
    pointcloud-display-name="scan.las"
    @back="goBack"
  />

  <!-- 只想要去噪：单独成页，自己从后端读配准结果 -->
  <DenoisePage
    :bim-asset-id="49"
    :pointcloud-asset-id="60"
    bim-display-name="model.glb"
    pointcloud-display-name="scan.las"
    back-label="返回扫描点云"
    @back="goBack"
  />

  <!-- 配准与分析：一对资产 + 步骤 -->
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
\`\`\`

不想用事件时，可传 \`:navigation="{ back, stepChange }"\` 回调，行为一致。

### props / 事件对照

| 页面 | 必填 props | 可选 props | 事件 |
| --- | --- | --- | --- |
| \`BimPreviewPage\` | \`assetId\` | \`displayName\`、\`projectId\`、\`projectName\`、\`backLabel\` | \`back\` |
| \`PointcloudPreviewPage\` | \`assetId\` | \`displayName\`、\`projectId\`、\`projectName\`、\`backLabel\` | \`back\` |
| \`SplitPreviewPage\` | \`bimAssetId\`、\`pointcloudAssetId\` | \`bimDisplayName\`、\`pointcloudDisplayName\`、\`projectId\`、\`projectName\`、\`backLabel\` | \`back\` |
| \`DenoisePage\` | \`bimAssetId\`、\`pointcloudAssetId\` | \`bimDisplayName\`、\`pointcloudDisplayName\`、\`projectName\`、\`backLabel\`、\`backgroundTheme\` | \`back\` |
| \`AlignmentPage\` | \`bimAssetId\`、\`pointcloudAssetId\` | \`bimDisplayName\`、\`pointcloudDisplayName\`、\`projectId\`、\`projectName\`、\`backLabel\`、\`initialStep\` | \`back\`、\`stepChange(step)\` |

### 需要更底层能力时

\`@cloudbim/viewer-core\` 还导出了查看器组件与 API 客户端，可自行组装界面：

\`\`\`ts
import {
  UnifiedViewer3D,          // 裸 Three.js 查看器
  BimPreviewPanel,          // 模型面板
  PointcloudPreviewPanel,   // 点云面板
  MeasurementToolbar,       // 测量工具条
  backendRequest,           // 统一传输层（baseUrl / token / 错误归一化）
  normalizeBackendUrl,      // 相对路径补成绝对 URL（<img>/<video> 用）
  type ViewerAssetPairProps,
  type ViewerPageEmits,
} from '@cloudbim/viewer-core'
\`\`\`

## 跑通示例

交付目录自带一个可运行的最小项目，不用改一行代码就能看到四个页面：

\`\`\`bash
cd example
npm install
npm run dev          # http://localhost:5199
\`\`\`

\`example/vite.config.ts\` 默认把 \`/assets\`、\`/alignments\`、\`/auth\` 代理到
\`http://127.0.0.1:8090\`，后端不通时页面照样渲染，只是会提示接口 404。
详见 \`example/README.md\`。

## 包一览

| 包 | 功能 ID | 类型 | 导出组件 | 体积 | 说明 |
| --- | --- | --- | --- | --- | --- |
${packageRows}

## peer 依赖

| 包 | 版本范围 |
| --- | --- |
${peerRows}

> 注意：\`three\` 与 \`3d-tiles-renderer\` 处于 0.x，caret 范围语义很窄
> （\`^0.173.0\` 等价于 \`>=0.173.0 <0.174.0\`）。**不要直接装 latest**，安装脚本会按上表对齐版本。

## 宿主必须满足的前置条件

1. **后端**：需实现下列接口，响应统一为 \`{ code, msg, data }\` 包络
${endpointList}
2. **Element Plus**：已安装并在入口注册 \`app.use(ElementPlus)\`
3. **Vue 3.5+** 与支持 ESM 的打包器（Vite 最省事）
4. **静态资源**：\`${dracoPath}/\` 随应用发布，路径与 \`dracoDecoderPath\` 一致

## 手动安装（不使用脚本时）

\`\`\`bash
npm install vue three@^0.173.0 3d-tiles-renderer@^0.4.19 element-plus @element-plus/icons-vue
npm install ${packages.map((pkg) => './' + pkg.file).join(' ')}
\`\`\`

所有 tarball 必须在**同一条命令**里安装，页面包依赖的 \`@cloudbim/viewer-core\` 只能由同批 tarball 满足。
只装部分功能时，把对应的几个 tarball 一起装即可（功能包的依赖关系见 \`manifest.json\` 的 \`dependencies\` 字段）。

## 踩坑提示

- **样式必须显式引入**：库模式构建把 SFC 样式抽取为独立 \`style.css\`，包不会自动注入。
  引入 \`@cloudbim/viewer-core/style.css\` 与各页面包 \`style.css\`，否则组件渲染正常但完全没样式。
- **peer 依赖重复**：宿主自行安装 peer 依赖，避免出现多份 Vue / Three 实例。
- **升级包版本**：把新版本的 \`dist-packages\` 覆盖过去，重跑 \`install.sh\` 即可。
`,
  'utf8',
)

try {
  chmodSync(join(outputDir, 'install.sh'), 0o755)
  chmodSync(join(outputDir, 'install.mjs'), 0o755)
} catch {
  // Windows 上 chmod 无意义，忽略。
}

console.log(`\n产物目录: ${outputDir}`)
console.log(`  包：${packages.map((pkg) => pkg.file).join(', ')}`)
console.log(
  `  清单：manifest.json · 脚本：install.sh / install.mjs · 说明：README.md${hasExample ? ' · 示例：example/' : ''}`,
)
console.log('\n交付方式：把整个 dist-packages 目录拷给对方，在宿主项目根目录执行 sh install.sh')
