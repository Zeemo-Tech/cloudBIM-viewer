import type { TransformControls } from 'three/examples/jsm/controls/TransformControls.js'

/** Keep editor display changes separate from changes to the calibrated object. */
export function bindTransformChangeEvents(
  controller: TransformControls,
  onPoseChange: () => void,
  onDisplayChange: () => void,
) {
  // `change` also fires on attach/detach, hover, enabled, mode and size changes.
  controller.addEventListener('objectChange', onPoseChange)
  controller.addEventListener('change', onDisplayChange)
  return () => {
    controller.removeEventListener('objectChange', onPoseChange)
    controller.removeEventListener('change', onDisplayChange)
  }
}
