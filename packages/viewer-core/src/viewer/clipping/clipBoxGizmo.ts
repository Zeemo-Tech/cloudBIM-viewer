// Migrated from Tunnel-Section-Analyzer; keep the viewport interaction and visual contract aligned.
import * as THREE from "three";
import { Line2 } from "three/examples/jsm/lines/Line2.js";
import { LineGeometry } from "three/examples/jsm/lines/LineGeometry.js";
import { LineMaterial } from "three/examples/jsm/lines/LineMaterial.js";

export type ClipAxis = "x" | "y" | "z";
export type ClipSide = 0 | 1;
export interface ClipFace {
  kind: "face";
  axis: ClipAxis;
  side: ClipSide;
}
export interface ClipRotationHandle {
  kind: "edge" | "corner";
  axis: ClipAxis;
  index: number;
}
export type ClipHandle = ClipFace | ClipRotationHandle;
interface EdgeEntry {
  axis: ClipAxis;
  sides: ClipSide[];
  picker: THREE.Mesh;
  line: Line2;
  outline: Line2;
}

const EDGE_COLOR = 0x80bcc4;
const ACTIVE_COLOR = 0xe4f7f4;
const FILL_COLOR = 0x76acae;
const AXES: ClipAxis[] = ["x", "y", "z"];

interface FaceEntry extends ClipFace {
  group: THREE.Group;
  fill: THREE.Mesh<THREE.PlaneGeometry, THREE.MeshBasicMaterial>;
  arrow: THREE.Group;
  arrowMaterial: THREE.MeshBasicMaterial;
  arrowPicker: THREE.Mesh;
  normal: THREE.Vector3;
  width: number;
  height: number;
}

/** 以屏幕像素控制拾取宽度，避免长隧道的手柄随纵向长度膨胀。 */
export function clipWorldUnitsPerPixel(
  camera: THREE.PerspectiveCamera | THREE.OrthographicCamera,
  position: THREE.Vector3,
  viewportHeight: number,
) {
  const height = Math.max(viewportHeight, 1);
  if (camera instanceof THREE.OrthographicCamera) {
    return (camera.top - camera.bottom) / camera.zoom / height;
  }
  const depth = -position.clone().applyMatrix4(camera.matrixWorldInverse).z;
  return (
    (2 *
      Math.max(depth, camera.near) *
      Math.tan(THREE.MathUtils.degToRad(camera.fov / 2))) /
    camera.zoom /
    height
  );
}

/** 拖面改尺寸，拖边绕边对应的轴旋转；正对某个面时允许拖角旋转。 */
export class ClipBoxGizmo {
  readonly group = new THREE.Group();
  private readonly faces: FaceEntry[] = [];
  private readonly edges: EdgeEntry[] = [];
  private readonly corners: THREE.Mesh[] = [];
  private readonly cornerIndicators: THREE.Mesh[] = [];
  private cornerAxis?: ClipAxis;

  constructor() {
    this.group.name = "Section box";
    for (const axis of AXES) {
      for (const side of [0, 1] as const) {
        const group = new THREE.Group();
        const normal = new THREE.Vector3();
        normal[axis] = side === 0 ? -1 : 1;
        group.quaternion.setFromUnitVectors(new THREE.Vector3(0, 0, 1), normal);
        const fill = new THREE.Mesh(
          new THREE.PlaneGeometry(1, 1),
          new THREE.MeshBasicMaterial({
            color: FILL_COLOR,
            transparent: true,
            opacity: 0.035,
            side: THREE.DoubleSide,
            depthTest: false,
            depthWrite: false,
          }),
        );
        const arrowMaterial = new THREE.MeshBasicMaterial({
          color: EDGE_COLOR,
          transparent: true,
          opacity: 1,
          depthTest: false,
          depthWrite: false,
        });
        const arrow = new THREE.Group();
        // 短箭头从面上起步，不额外向框外偏移。
        const shaft = new THREE.Mesh(
          new THREE.CylinderGeometry(0.065, 0.065, 0.65, 12),
          arrowMaterial,
        );
        shaft.position.y = 0.325;
        const head = new THREE.Mesh(
          new THREE.ConeGeometry(0.2, 0.35, 16),
          arrowMaterial,
        );
        head.position.y = 0.825;
        const foot = new THREE.Mesh(
          new THREE.CylinderGeometry(0.18, 0.18, 0.07, 20),
          arrowMaterial,
        );
        foot.position.y = 0.035;
        const rim = new THREE.Mesh(
          new THREE.CylinderGeometry(0.23, 0.23, 0.045, 20),
          new THREE.MeshBasicMaterial({
            color: 0x142b31,
            transparent: true,
            depthTest: false,
            depthWrite: false,
          }),
        );
        arrow.add(rim, foot, shaft, head);
        arrow.rotation.x = Math.PI / 2;
        const pickerMaterial = new THREE.MeshBasicMaterial({
          transparent: true,
          opacity: 0,
          depthTest: false,
          depthWrite: false,
        });
        const arrowPicker = new THREE.Mesh(
          new THREE.CylinderGeometry(0.28, 0.28, 0.7, 8),
          pickerMaterial,
        );
        arrowPicker.position.y = 0.65;
        arrow.add(arrowPicker);
        group.add(fill, arrow);
        fill.renderOrder = 9996;
        arrow.traverse((object) => {
          object.renderOrder = 10000;
        });
        this.group.add(group);
        this.faces.push({
          kind: "face",
          axis,
          side,
          group,
          fill,
          arrow,
          arrowMaterial,
          arrowPicker,
          normal,
          width: 1,
          height: 1,
        });
      }
    }
    const pickerMaterial = new THREE.MeshBasicMaterial({
      transparent: true,
      opacity: 0,
      depthTest: false,
      depthWrite: false,
    });
    for (const axis of AXES) {
      for (const first of [0, 1] as const) {
        for (const second of [0, 1] as const) {
          const picker = new THREE.Mesh(
            new THREE.BoxGeometry(1, 1, 1),
            pickerMaterial,
          );
          const geometry = new LineGeometry();
          geometry.setPositions([0, 0, 0, 0, 0, 0]);
          const line = new Line2(
            geometry,
            new LineMaterial({
              color: EDGE_COLOR,
              linewidth: 2.2,
              transparent: true,
              opacity: 1,
              depthTest: false,
              depthWrite: false,
            }),
          );
          const outline = new Line2(
            geometry,
            new LineMaterial({
              color: 0x142b31,
              linewidth: 4.2,
              transparent: true,
              opacity: 1,
              depthTest: false,
              depthWrite: false,
            }),
          );
          outline.renderOrder = 9997;
          line.renderOrder = 9998;
          this.group.add(picker, outline, line);
          this.edges.push({
            axis,
            sides: [first, second],
            picker,
            line,
            outline,
          });
        }
      }
    }
    for (let index = 0; index < 8; index++) {
      const corner = new THREE.Mesh(
        new THREE.SphereGeometry(1, 8, 6),
        pickerMaterial,
      );
      const indicator = new THREE.Mesh(
        new THREE.SphereGeometry(0.4, 8, 6),
        new THREE.MeshBasicMaterial({
          color: ACTIVE_COLOR,
          depthTest: false,
          depthWrite: false,
        }),
      );
      indicator.visible = false;
      indicator.renderOrder = 10001;
      corner.add(indicator);
      this.cornerIndicators.push(indicator);
      this.corners.push(corner);
      this.group.add(corner);
    }
  }

  updateBounds(bounds: THREE.Box3, rotation: THREE.Quaternion) {
    this.group.quaternion.copy(rotation);
    const size = bounds.getSize(new THREE.Vector3());
    const center = bounds.getCenter(new THREE.Vector3());
    for (const face of this.faces) {
      face.group.position.copy(center);
      face.group.position[face.axis] =
        face.side === 0 ? bounds.min[face.axis] : bounds.max[face.axis];
      face.width = Math.max(face.axis === "x" ? size.z : size.x, 1e-5);
      face.height = Math.max(face.axis === "y" ? size.z : size.y, 1e-5);
      face.fill.scale.set(face.width, face.height, 1);
    }
    for (const edge of this.edges) {
      const otherAxes = AXES.filter((axis) => axis !== edge.axis);
      const start = center.clone();
      otherAxes.forEach((axis, index) => {
        start[axis] =
          edge.sides[index] === 0 ? bounds.min[axis] : bounds.max[axis];
      });
      edge.picker.position.copy(start);
      edge.picker.userData.length = size[edge.axis];
      const end = start.clone();
      start[edge.axis] = bounds.min[edge.axis];
      end[edge.axis] = bounds.max[edge.axis];
      edge.line.geometry.setPositions([...start.toArray(), ...end.toArray()]);
      edge.line.computeLineDistances();
    }
    this.corners.forEach((corner, index) => {
      corner.position.set(
        index & 1 ? bounds.max.x : bounds.min.x,
        index & 2 ? bounds.max.y : bounds.min.y,
        index & 4 ? bounds.max.z : bounds.min.z,
      );
    });
    this.group.updateMatrixWorld(true);
  }

  updateCamera(
    camera: THREE.PerspectiveCamera | THREE.OrthographicCamera,
    viewportHeight: number,
  ) {
    const viewDirection = camera.getWorldDirection(new THREE.Vector3());
    for (const face of this.faces) {
      const center = face.group.getWorldPosition(new THREE.Vector3());
      const pixel = clipWorldUnitsPerPixel(camera, center, viewportHeight);
      const worldNormal = face.normal
        .clone()
        .applyQuaternion(this.group.quaternion);
      const towardFace =
        camera instanceof THREE.PerspectiveCamera
          ? center.clone().sub(camera.position).normalize()
          : viewDirection;
      // 背面不铺色、不摆箭头，边线仍保留轮廓；正对视线的法向无法稳定拉伸。
      const facing = worldNormal.dot(towardFace) < 0.08;
      const draggable = Math.abs(worldNormal.dot(viewDirection)) < 0.985;
      face.arrow.visible = facing && draggable;
      face.arrow.scale.setScalar(pixel * 32);
      face.fill.visible = facing;
    }
    this.cornerAxis = AXES.find((axis) => {
      const direction = new THREE.Vector3();
      direction[axis] = 1;
      return (
        Math.abs(
          direction.applyQuaternion(this.group.quaternion).dot(viewDirection),
        ) > 0.995
      );
    });
    const aspect =
      camera instanceof THREE.PerspectiveCamera
        ? camera.aspect
        : (camera.right - camera.left) / (camera.top - camera.bottom);
    for (const edge of this.edges) {
      edge.line.material.resolution.set(
        viewportHeight * aspect,
        viewportHeight,
      );
      edge.outline.material.resolution.copy(edge.line.material.resolution);
      const pixel = clipWorldUnitsPerPixel(
        camera,
        edge.picker.getWorldPosition(new THREE.Vector3()),
        viewportHeight,
      );
      edge.picker.scale.setScalar(pixel * 10);
      edge.picker.scale[edge.axis] = Math.max(
        edge.picker.userData.length,
        1e-5,
      );
    }
    for (const corner of this.corners) {
      const pixel = clipWorldUnitsPerPixel(
        camera,
        corner.getWorldPosition(new THREE.Vector3()),
        viewportHeight,
      );
      corner.scale.setScalar(pixel * 8);
    }
    this.group.updateMatrixWorld(true);
  }

  setActive(handle?: ClipHandle) {
    for (const entry of this.faces) {
      const selected =
        handle?.kind === "face" &&
        entry.axis === handle.axis &&
        entry.side === handle.side;
      entry.fill.material.opacity = selected ? 0.1 : 0.035;
      entry.arrowMaterial.color.setHex(selected ? ACTIVE_COLOR : EDGE_COLOR);
    }
    this.cornerIndicators.forEach((indicator, index) => {
      indicator.visible = handle?.kind === "corner" && handle.index === index;
    });
    this.edges.forEach((edge, index) => {
      const faceAxes = AXES.filter((axis) => axis !== edge.axis);
      const selected =
        (handle?.kind === "edge" && handle.index === index) ||
        (handle?.kind === "face" &&
          edge.axis !== handle.axis &&
          edge.sides[faceAxes.indexOf(handle.axis)] === handle.side);
      edge.line.material.color.setHex(selected ? ACTIVE_COLOR : EDGE_COLOR);
      edge.line.material.linewidth = selected ? 3.2 : 2.2;
      edge.outline.material.linewidth = selected ? 5.2 : 4.2;
    });
  }

  pick(
    raycaster: THREE.Raycaster,
    camera: THREE.Camera,
    innerFaces = false,
  ): (ClipHandle & { point: THREE.Vector3 }) | undefined {
    if (!this.group.visible) return;
    const viewDirection = camera.getWorldDirection(new THREE.Vector3());
    const candidates: Array<{
      handle: ClipHandle;
      point: THREE.Vector3;
      priority: number;
      distance: number;
    }> = [];
    // 四周边线有独立拾取体，不再映射为相邻面的拉伸。
    this.edges.forEach((edge, index) => {
      const hit = raycaster.intersectObject(edge.picker, false)[0];
      if (hit)
        candidates.push({
          handle: { kind: "edge", axis: edge.axis, index },
          point: hit.point,
          priority: 1,
          distance: hit.distance,
        });
    });
    if (this.cornerAxis)
      this.corners.forEach((corner, index) => {
        const hit = raycaster.intersectObject(corner, false)[0];
        if (hit)
          candidates.push({
            handle: { kind: "corner", axis: this.cornerAxis!, index },
            point: hit.point,
            priority: 0,
            distance: hit.distance,
          });
      });
    for (const face of this.faces) {
      const worldNormal = face.normal
        .clone()
        .applyQuaternion(this.group.quaternion);
      if (Math.abs(worldNormal.dot(viewDirection)) >= 0.985) continue;
      if (!innerFaces && !face.fill.visible) continue;
      const arrowHit = face.arrow.visible
        ? raycaster.intersectObject(face.arrowPicker, false)[0]
        : undefined;
      const faceHit = raycaster.intersectObject(face.fill, false)[0];
      const hit = arrowHit ?? faceHit;
      if (hit)
        candidates.push({
          handle: { kind: "face", axis: face.axis, side: face.side },
          point: hit.point,
          priority: arrowHit ? 0 : 2,
          distance: hit.distance,
        });
    }
    candidates.sort(
      (a, b) =>
        a.priority - b.priority ||
        (innerFaces && a.handle.kind === "face" && b.handle.kind === "face"
          ? b.distance - a.distance
          : a.distance - b.distance),
    );
    const selected = candidates[0];
    return selected ? { ...selected.handle, point: selected.point } : undefined;
  }

  dispose() {
    const geometries = new Set<THREE.BufferGeometry>();
    const materials = new Set<THREE.Material>();
    this.group.traverse((object) => {
      const mesh = object as THREE.Mesh;
      if (mesh.geometry) geometries.add(mesh.geometry);
      if (mesh.material)
        (Array.isArray(mesh.material)
          ? mesh.material
          : [mesh.material]
        ).forEach((material) => materials.add(material));
    });
    geometries.forEach((geometry) => geometry.dispose());
    materials.forEach((material) => material.dispose());
    this.group.removeFromParent();
  }
}
