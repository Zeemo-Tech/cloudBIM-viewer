export type PointcloudLoadLifecycle = {
  readonly hasRenderableModel: boolean
  readonly allowEdl: boolean
  markModelReady: () => boolean
  markInitialLoadSettled: () => void
}

/**
 * Tracks readiness for one TilesRenderer instance.
 *
 * A point cloud is useful as soon as its first model can be rendered. The
 * remaining tile work is refinement and must not keep the whole page in its
 * blocking loading state. EDL stays off until the initial refinement cycle is
 * settled so tile parsing and GPU upload get the first share of resources.
 */
export function createPointcloudLoadLifecycle(): PointcloudLoadLifecycle {
  let hasRenderableModel = false
  let initialLoadSettled = false

  return {
    get hasRenderableModel() {
      return hasRenderableModel
    },
    get allowEdl() {
      return initialLoadSettled
    },
    markModelReady() {
      if (hasRenderableModel) return false
      hasRenderableModel = true
      return true
    },
    markInitialLoadSettled() {
      initialLoadSettled = true
    },
  }
}
