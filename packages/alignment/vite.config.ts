import { coreExternal, createLibConfig, peerExternals } from '../../scripts/lib-vite.config.mjs'

export default createLibConfig(import.meta.url, {
  external: [...peerExternals, coreExternal],
  // 报告封面使用内置品牌标记（4.3KB），内联后无需宿主托管资源。
  assetsInlineLimit: 16 * 1024,
})
