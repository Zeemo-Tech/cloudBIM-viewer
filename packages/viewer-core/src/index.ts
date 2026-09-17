/**
 * `@cloudbim/viewer-core` 公共入口。
 *
 * 这里导出的是稳定 API：运行时配置、导航契约、后端传输层与数据接口、查看器组件与
 * 分析工具。带同名冲突的模块（例如 `parseC2MDistances` 在旧版 C2M 色彩映射与
 * analysis-mesh 契约中是两个不同函数）在这里显式消歧。
 */

// 设计令牌与组件样式：构建为 dist/style.css，由宿主显式引入一次。
import '@/style.scss'

// ── 运行时配置 ────────────────────────────────────────────────────────────────
export {
  configureCloudBim,
  getCloudBimRuntime,
  isCloudBimDebug,
  type CloudBimRuntimeOptions,
} from '@/config/runtime'

// ── 导航契约（包不依赖 vue-router，由宿主注入） ────────────────────────────────
export type { ViewerNavigationHandlers } from '@/viewer/navigation'
export type {
  ViewerAssetPairProps,
  ViewerPageEmits,
  ViewerSingleAssetProps,
  ViewerStepProps,
} from '@/viewer/pageContract'

// 预览页共用的测量状态（叠加图层 + 后端测量记录读写）。
export { useViewerMeasurements, type UseViewerMeasurementsOptions } from '@/viewer/useViewerMeasurements'

// ── 传输层 ───────────────────────────────────────────────────────────────────
export {
  backendClient,
  backendFetch,
  backendRequest,
  backendRequestRaw,
  backendTusRequestRaw,
  getBackendBaseUrl,
  normalizeBackendUrl,
  readResponseHeader,
  type BackendResult,
} from '@/api/backend-http'

// ── 后端数据接口 ─────────────────────────────────────────────────────────────
export * from '@/api/backend-file'
export * from '@/api/backend-mesh'
export * from '@/api/backend-alignment'
export * from '@/api/backend-measurement'
export * from '@/api/backend-pointcloud-preprocess'
export * from '@/api/backend-denoise'
export * from '@/api/backend-rebar'
export * from '@/api/backend-c2m'

// ── C2M 色彩与取值工具 ───────────────────────────────────────────────────────
// 这里导出的是 `utils/c2mColormap` 版本；analysis-mesh 契约中的同名函数见下方
// `parseAnalysisMeshDistances`。
export * from '@/utils/c2mColormap'
export * from '@/utils/c2mRange'
export * from '@/utils/c2mPick'

// ── 点云分类与表达选择 ───────────────────────────────────────────────────────
export * from '@/features/pointcloud/tableVisibility'
export * from '@/features/pointcloud/representation'

// ── analysis-mesh 契约与产物校验 ─────────────────────────────────────────────
export * as analysisMesh from '@/features/analysis-mesh'
export {
  AnalysisMeshSession,
  C2MShaderMaterial,
  parseAnalysisC2MManifest,
  parseC2MDistances as parseAnalysisMeshDistances,
  resolveAnalysisArtifactURL,
  verifyPayloadHash,
  verifyPositionStreamHash,
  type AnalysisC2MStats,
  type C2MColorMode,
  type TileRendererEvents,
} from '@/features/analysis-mesh'

// ── 配准矩阵换算 ─────────────────────────────────────────────────────────────
// BIM ↔ 点云刚体变换的换算（保存 / 恢复双向），带往返一致性测试。配准类功能包
// 一律从这里取，避免各自实现导致静默偏移。
export * from '@/features/alignment/matrix'

// ── 钢筋可视化 ───────────────────────────────────────────────────────────────
export * from '@/features/rebar-visualization'
export * from '@/features/rebar-visualization/inspection'
export * from '@/features/rebar-visualization/instancePalette.js'

// ── 通用工具 ─────────────────────────────────────────────────────────────────
export * from '@/features/upload/upload.utils'
export * from '@/config/upload-backend'

// ── 查看器组件 ───────────────────────────────────────────────────────────────
export { default as UnifiedViewer3D } from '@/components/preview/UnifiedViewer3D.vue'
export type {
  CameraPose,
  CameraRotation,
  ClipAxis,
  ClipBoxOffsets,
  ClipBoxState,
  PointcloudColorMode,
  PointcloudColorRamp,
  PointcloudColorRange,
  PreviewBackgroundTheme,
  StandardView,
  UnifiedViewerProps,
  ViewerType,
} from '@/components/preview/UnifiedViewer3D.vue'

export { default as BimPreviewPanel } from '@/components/preview/BimPreviewPanel.vue'
export { default as PointcloudPreviewPanel } from '@/components/preview/PointcloudPreviewPanel.vue'
export { default as MeasurementToolbar } from '@/components/preview/MeasurementToolbar.vue'
export { default as MeasurementResultsPanel } from '@/components/preview/MeasurementResultsPanel.vue'
export { default as PointcloudAxesTriad } from '@/components/preview/PointcloudAxesTriad.vue'
export { default as PointcloudViewCube } from '@/components/preview/PointcloudViewCube.vue'
export { default as PointcloudColorRangeBar } from '@/components/preview/PointcloudColorRangeBar.vue'
export { default as C2MHistogramLegend } from '@/components/preview/C2MHistogramLegend.vue'
export { default as C2MResultPreviewPanel } from '@/components/preview/C2MResultPreviewPanel.vue'
export { default as ViewerMeasurementBadge } from '@/components/preview/ViewerMeasurementBadge.vue'
export type { ViewerMeasurementBadgeOverlay } from '@/components/preview/ViewerMeasurementBadge.vue'
export { default as ViewerAnalysisOverlay } from '@/components/preview/ViewerAnalysisOverlay.vue'
export type {
  AnalysisArea,
  AnalysisDistance,
  AnalysisMode,
  AnalysisPoint,
} from '@/components/preview/ViewerAnalysisOverlay.vue'
export { default as ViewportToolGlyph } from '@/components/analysis/ViewportToolGlyph.vue'

// ── 查看器底层工具 ───────────────────────────────────────────────────────────
export * from '@/components/preview/edlPipeline'
export * from '@/components/preview/pointcloudLoadLifecycle'
