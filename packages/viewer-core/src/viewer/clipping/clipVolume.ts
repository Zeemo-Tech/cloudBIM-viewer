import * as THREE from 'three'

export interface OrientedClipFrame {
  worldFromLocal: THREE.Quaternion
  localFromWorld: THREE.Quaternion
  fullBounds: THREE.Box3
}

/** Viewer-world metres, Y-up. Content matrices are never changed by clipping. */
export function clipFrameFromBox(box: THREE.Box3): OrientedClipFrame {
  return { worldFromLocal: new THREE.Quaternion(), localFromWorld: new THREE.Quaternion(), fullBounds: box.clone() }
}

/** 围绕当前选中盒中心旋转裁切坐标系；原 frame 与模型坐标均保持不变。 */
export function rotateOrientedClipFrame(
  frame: OrientedClipFrame,
  selectedBounds: THREE.Box3,
  worldAxis: THREE.Vector3,
  angleRad: number,
): OrientedClipFrame {
  const axis = worldAxis.clone();
  if (
    ![axis.x, axis.y, axis.z, angleRad].every(Number.isFinite) ||
    axis.lengthSq() < 1e-20 ||
    selectedBounds.isEmpty()
  ) {
    return {
      worldFromLocal: frame.worldFromLocal.clone(),
      localFromWorld: frame.localFromWorld.clone(),
      fullBounds: frame.fullBounds.clone(),
    };
  }
  const worldFromLocal = new THREE.Quaternion()
    .setFromAxisAngle(axis.normalize(), angleRad)
    .multiply(frame.worldFromLocal);
  const localFromWorld = worldFromLocal.clone().invert();
  const selectedCenter = selectedBounds.getCenter(new THREE.Vector3());
  const originalWorldCenter = selectedCenter
    .clone()
    .applyQuaternion(frame.worldFromLocal);
  const offset = originalWorldCenter
    .applyQuaternion(localFromWorld)
    .sub(selectedCenter);
  return {
    worldFromLocal,
    localFromWorld,
    fullBounds: frame.fullBounds.clone().translate(offset),
  };
}

/** Three.js 保留 distanceToPoint >= 0 的一侧，因此六个法向均朝向盒体内部。 */
export function orientedClipPlanes(
  frame: OrientedClipFrame,
  bounds: THREE.Box3,
): THREE.Plane[] {
  const worldAxis = (axis: "x" | "y" | "z") => {
    const local =
      axis === "x"
        ? new THREE.Vector3(1, 0, 0)
        : axis === "y"
          ? new THREE.Vector3(0, 1, 0)
          : new THREE.Vector3(0, 0, 1);
    return local.applyQuaternion(frame.worldFromLocal).normalize();
  };
  const x = worldAxis("x");
  const y = worldAxis("y");
  const z = worldAxis("z");
  return [
    new THREE.Plane(x, -bounds.min.x),
    new THREE.Plane(x.clone().negate(), bounds.max.x),
    new THREE.Plane(y, -bounds.min.y),
    new THREE.Plane(y.clone().negate(), bounds.max.y),
    new THREE.Plane(z, -bounds.min.z),
    new THREE.Plane(z.clone().negate(), bounds.max.z),
  ];
}
