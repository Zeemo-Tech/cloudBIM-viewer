// Migrated from Tunnel-Section-Analyzer; keep the viewport interaction and visual contract aligned.
import * as THREE from "three";

export interface ClipRotationDrag {
  axis: THREE.Vector3;
  center: THREE.Vector3;
  plane?: THREE.Plane;
  startRadial?: THREE.Vector3;
  startPointer: THREE.Vector2;
  screenTangent: THREE.Vector2;
  viewport: THREE.Vector2;
  previousAngle: number;
  angle: number;
}

/** 正视旋转平面时按圆周拖动；侧视退化时按投影切向拖动，避免射线近乎平行导致跳转。 */
export function beginClipRotation(
  ray: THREE.Ray,
  camera: THREE.Camera,
  center: THREE.Vector3,
  worldAxis: THREE.Vector3,
  grabPoint: THREE.Vector3,
  pointer: THREE.Vector2,
  viewport: THREE.Vector2,
): ClipRotationDrag {
  const axis = worldAxis.clone().normalize();
  const plane = new THREE.Plane().setFromNormalAndCoplanarPoint(axis, center);
  const intersect = ray.intersectPlane(plane, new THREE.Vector3());
  const radial = grabPoint.clone().sub(center);
  radial.addScaledVector(axis, -radial.dot(axis));
  const start = center.clone().add(radial).project(camera);
  const end = radial
    .clone()
    .applyAxisAngle(axis, 0.01)
    .add(center)
    .project(camera);
  const screenTangent = new THREE.Vector2(
    ((end.x - start.x) * viewport.x) / 0.02,
    ((end.y - start.y) * viewport.y) / 0.02,
  );
  if (screenTangent.lengthSq() < 1e-6) {
    const projectedAxis = center
      .clone()
      .add(axis)
      .project(camera)
      .sub(center.clone().project(camera));
    screenTangent.set(
      -projectedAxis.y * viewport.y,
      projectedAxis.x * viewport.x,
    );
    if (screenTangent.lengthSq() < 1e-6) screenTangent.set(1, 0);
  }
  screenTangent.setLength(
    THREE.MathUtils.clamp(screenTangent.length(), 40, 240),
  );
  const stablePlane =
    Math.abs(ray.direction.dot(axis)) > 0.15 &&
    intersect &&
    intersect.distanceToSquared(center) > 1e-8;
  return {
    axis,
    center: center.clone(),
    plane: stablePlane ? plane : undefined,
    startRadial: stablePlane ? intersect.sub(center).normalize() : undefined,
    startPointer: pointer.clone(),
    screenTangent,
    viewport: viewport.clone(),
    previousAngle: 0,
    angle: 0,
  };
}

/** 保留跨过 ±π 的连续角度，输出右手系绕 worldAxis 的弧度。 */
export function updateClipRotation(
  drag: ClipRotationDrag,
  ray: THREE.Ray,
  pointer: THREE.Vector2,
) {
  if (drag.plane && drag.startRadial) {
    const point = ray.intersectPlane(drag.plane, new THREE.Vector3());
    if (!point || point.distanceToSquared(drag.center) < 1e-8)
      return drag.angle;
    const current = point.sub(drag.center).normalize();
    const angle = Math.atan2(
      new THREE.Vector3()
        .crossVectors(drag.startRadial, current)
        .dot(drag.axis),
      drag.startRadial.dot(current),
    );
    const difference = angle - drag.previousAngle;
    drag.angle += Math.atan2(Math.sin(difference), Math.cos(difference));
    drag.previousAngle = angle;
  } else {
    const delta = pointer
      .clone()
      .sub(drag.startPointer)
      .multiply(drag.viewport)
      .multiplyScalar(0.5);
    drag.angle = delta.dot(drag.screenTangent) / drag.screenTangent.lengthSq();
  }
  return drag.angle;
}
