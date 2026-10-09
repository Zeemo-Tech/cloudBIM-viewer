# @cloudbim/viewer-core

CloudBIM 查看器内核：Three.js 场景、点云与模型渲染、测量工具、后端 API 客户端与设计令牌。
模型预览、点云预览、双屏对比与配准工作区四个包都依赖它。

## 安装

```bash
npm install @cloudbim/viewer-core vue three 3d-tiles-renderer element-plus @element-plus/icons-vue
```

`vue` / `three` / `3d-tiles-renderer` / `element-plus` / `@element-plus/icons-vue` 是
peerDependencies，必须由宿主应用安装，避免出现多份 Vue、Three 实例与重复主题。

样式令牌与组件样式在独立 CSS 中，需要显式引入一次：

```ts
import '@cloudbim/viewer-core/style.css'
```

## 使用

### 1. 配置运行时

包不读取宿主的 `import.meta.env`，也不依赖宿主的登录态实现，全部通过
`configureCloudBim` 注入：

```ts
import { configureCloudBim } from '@cloudbim/viewer-core'
import { getStoredAccessToken } from './auth'

configureCloudBim({
  // 留空表示与宿主同源（Vite/Nginx 反向代理部署）
  baseUrl: import.meta.env.VITE_API_BASE_URL ?? '',
  // 返回空值时依赖后端下发的 HttpOnly Cookie
  getAccessToken: getStoredAccessToken,
  debug: import.meta.env.DEV,
  // three 的 Draco 解码器目录；宿主需把解码器放到该路径下（默认 /draco/）
  dracoDecoderPath: '/draco/',
  onUnauthorized: () => {
    // 可选：收到 401 时跳转登录
  },
})
```

应用启动时调用一次即可；未传入的字段保持原值，因此可以分多次合并配置。

### 2. 调用后端 API

```ts
import { backendRequest, type BackendResult } from '@cloudbim/viewer-core'

const result = await backendRequest<BackendResult<{ total: number }>>('/assets', {
  method: 'GET',
})
```

导出的传输层接口：`backendRequest` / `backendRequestRaw` / `backendTusRequestRaw` /
`backendFetch` / `readResponseHeader` / `normalizeBackendUrl` / `getBackendBaseUrl`。
未在宿主的 `configureCloudBim` 中提供 `getAccessToken` 时，请求不带 `Authorization`
头，仅依靠 Cookie。

### 3. 导航契约

查看器包不依赖 `vue-router`。宿主把返回、步骤同步映射到自己的路由实现，
再通过页面组件的 props 传入：

```ts
import type { ViewerNavigationHandlers } from '@cloudbim/viewer-core'

const navigation: ViewerNavigationHandlers = {
  back: () => router.push('/survey'),
  stepChange: (step) => router.replace({ query: { ...route.query, step: String(step) } }),
}
```

页面组件也支持事件形式（`@back` / `@step-change`），两者二选一；
若同时提供，`navigation` 回调优先。

### 4. 页面组件 props 契约

四个页面包共用同一组 props 类型，便于宿主统一接线：

```ts
import type {
  ViewerAssetPairProps,   // bimAssetId / pointcloudAssetId / 两个显示名 / navigation
  ViewerSingleAssetProps, // assetId / displayName / projectId / navigation
  ViewerStepProps,        // initialStep
  ViewerPageEmits,        // back / stepChange
} from '@cloudbim/viewer-core'
```

## 开发

```bash
npm run build --workspace @cloudbim/viewer-core   # 构建 dist/（含 .d.ts）
npm run pack:packages                            # 在仓库根执行，产出 dist-packages/*.tgz
```

包内源码沿用宿主应用的 `@/` 根别名（指向本包 `src/`），构建时由 Vite 解析、
输出时改写成相对路径，因此 `dist/index.d.ts` 不含别名。
