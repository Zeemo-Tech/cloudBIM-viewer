/**
 * 查看器与宿主之间的导航契约。
 *
 * 包不依赖 vue-router：宿主通过 props 传入回调，把返回、步骤同步等行为映射到自己的
 * 路由实现（URL query、栈路由或单页状态均可）。
 */
export interface ViewerNavigationHandlers {
  /** 返回宿主页面。未提供时查看器不显示返回入口。 */
  back?: () => void
  /** 配准步骤变化。未提供时步骤只保留在查看器内部状态。 */
  stepChange?: (step: number) => void
}
