import type { ViewerNavigationHandlers } from './navigation'

/**
 * 查看器页面组件的统一 props 契约。
 *
 * 宿主负责把 URL/路由状态映射成这些 props，并通过 `navigation` 回调把返回与步骤变化
 * 写回自己的路由；包内部不依赖 vue-router。
 */
export interface ViewerAssetPairProps {
  /** BIM 设计模型资产 ID；为空时组件渲染“缺少选择”状态。 */
  bimAssetId: number | null
  /** 扫描点云资产 ID；为空时组件渲染“缺少选择”状态。 */
  pointcloudAssetId: number | null
  /** 顶部标题显示的模型名。 */
  bimDisplayName?: string
  /** 顶部标题显示的点云名。 */
  pointcloudDisplayName?: string
  /** 可选的导航回调集合。 */
  navigation?: ViewerNavigationHandlers
}

export interface ViewerSingleAssetProps {
  /** 单个资产 ID（模型预览或点云预览）。 */
  assetId: number | null
  /** 资产显示名。 */
  displayName?: string
  /** 所属项目 ID，用于读取测量记录等资源。 */
  projectId?: number | null
  /** 所属项目名称，用于页头副标题。 */
  projectName?: string
  /** 返回入口文案，例如“返回模型列表”；宿主按来源页决定。 */
  backLabel?: string
  /** 可选的导航回调集合。 */
  navigation?: ViewerNavigationHandlers
}

/** 配准工作区的步骤入口，宿主可从 URL 的 `step` 参数恢复。 */
export interface ViewerStepProps {
  initialStep?: number
  /**
   * 只开放其中几步（1 配准 / 2 去噪 / 3 偏差对比 / 4 报告）。不传则四步全开；
   * 传入的步骤按 1→4 顺序排列，用于“只要配准 + 去噪”这类按需交付。
   */
  steps?: number[]
}

/** 查看器页面统一发出的事件。 */
export interface ViewerPageEmits {
  /** 请求返回宿主页面。 */
  back: []
  /** 配准步骤变化，宿主可同步到 URL。 */
  stepChange: [step: number]
}
