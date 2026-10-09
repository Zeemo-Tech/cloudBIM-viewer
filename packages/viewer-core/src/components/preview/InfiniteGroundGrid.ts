import * as THREE from 'three'
import type { TilesRenderer } from '3d-tiles-renderer'

/** A procedural world-space grid whose surface always extends past the frustum. */
export class InfiniteGroundGrid extends THREE.Mesh<THREE.PlaneGeometry, THREE.ShaderMaterial> {
  private readonly corner = new THREE.Vector3()
  private readonly viewBounds = new THREE.Box3()
  private readonly assetSize = new THREE.Vector3()

  constructor(color: THREE.ColorRepresentation = '#2a6f82') {
    super(new THREE.PlaneGeometry(2, 2).rotateX(-Math.PI / 2), new THREE.ShaderMaterial({
      uniforms: {
        gridColor: { value: new THREE.Color(color) },
        cellSize: { value: 1 },
        gridLevel: { value: 0 },
        gridOrigin: { value: new THREE.Vector2() },
        fadeDistance: { value: 5000 },
      },
      vertexShader: /* glsl */ `
        varying vec3 vWorldPosition;
        varying float vViewDepth;
        void main() {
          vec4 worldPosition = modelMatrix * vec4(position, 1.0);
          vWorldPosition = worldPosition.xyz;
          vec4 viewPosition = viewMatrix * worldPosition;
          vViewDepth = -viewPosition.z;
          gl_Position = projectionMatrix * viewPosition;
        }
      `,
      fragmentShader: /* glsl */ `
        uniform vec3 gridColor;
        uniform float cellSize;
        uniform float gridLevel;
        uniform vec2 gridOrigin;
        uniform float fadeDistance;
        varying vec3 vWorldPosition;
        varying float vViewDepth;

        float gridLine(vec2 coordinates) {
          vec2 pixelWidth = max(fwidth(coordinates), vec2(0.000001));
          vec2 distanceToLine = abs(fract(coordinates - 0.5) - 0.5) / pixelWidth;
          vec2 lines = 1.0 - min(distanceToLine, vec2(1.0));
          // Subpixel cells disappear smoothly instead of shimmering or becoming solid.
          lines *= 1.0 - smoothstep(vec2(0.25), vec2(1.0), pixelWidth);
          return max(lines.x, lines.y);
        }

        void main() {
          vec2 coordinates = (vWorldPosition.xz - gridOrigin) / cellSize;
          coordinates /= pow(10.0, floor(gridLevel));
          float fine = gridLine(coordinates);
          float coarse = gridLine(coordinates / 10.0);
          float broad = gridLine(coordinates / 100.0);
          float alpha = mix(max(fine * 0.3, coarse * 0.45), max(coarse * 0.3, broad * 0.45), fract(gridLevel));
          alpha *= 1.0 - smoothstep(fadeDistance * 0.75, fadeDistance, vViewDepth);
          if (alpha < 0.001) discard;
          gl_FragColor = vec4(gridColor, alpha);
          #include <colorspace_fragment>
        }
      `,
      transparent: true,
      depthWrite: false,
      side: THREE.DoubleSide,
      toneMapped: false,
    }))
    this.name = 'infinite-ground-grid'
    this.frustumCulled = false
  }

  setBounds(box: THREE.Box3) {
    if (box.isEmpty()) return
    const size = box.getSize(this.assetSize)
    const span = Math.max(size.x, size.y, size.z, 0.001)
    this.position.y = box.min.y - span * 0.002
    this.material.uniforms.cellSize!.value = span / 20
    this.material.uniforms.gridOrigin!.value.set((box.min.x + box.max.x) / 2, (box.min.z + box.max.z) / 2)
  }

  setColor(color: THREE.ColorRepresentation) {
    this.material.uniforms.gridColor!.value.set(color)
  }

  updateForCamera(camera: THREE.Camera) {
    camera.updateWorldMatrix(true, false)
    this.viewBounds.makeEmpty()
    // Every visible ground intersection lies inside this world-space frustum box.
    // This also covers orthographic views, wide windows and rolled cameras.
    for (const x of [-1, 1]) for (const y of [-1, 1]) for (const z of [-1, 1]) {
      this.viewBounds.expandByPoint(this.corner.set(x, y, z).unproject(camera))
    }
    const { min, max } = this.viewBounds
    this.position.x = (min.x + max.x) / 2
    this.position.z = (min.z + max.z) / 2
    this.scale.set(Math.max(max.x - min.x, 1) * 0.55, 1, Math.max(max.z - min.z, 1) * 0.55)
    this.material.uniforms.fadeDistance!.value = (camera as THREE.PerspectiveCamera | THREE.OrthographicCamera).far
    const height = Math.abs(camera.getWorldPosition(this.corner).y - this.position.y)
    const viewHeight = 2 * ((camera as THREE.OrthographicCamera).isOrthographicCamera ? 1 : height) / Math.abs(camera.projectionMatrix.elements[5]!)
    this.material.uniforms.gridLevel!.value = Math.max(0, Math.log10(Math.max(1, viewHeight / (this.material.uniforms.cellSize!.value * 80))))
    this.updateMatrixWorld(true)
  }

  dispose() {
    this.geometry.dispose()
    this.material.dispose()
  }
}

export function getTilesetWorldBounds(source: TilesRenderer): THREE.Box3 | null {
  source.group.updateWorldMatrix(true, false)
  const box = new THREE.Box3()
  if (source.getBoundingBox(box) && !box.isEmpty()) return box.applyMatrix4(source.group.matrixWorld)
  const sphere = new THREE.Sphere()
  if (!source.getBoundingSphere(sphere)) return null
  return sphere.applyMatrix4(source.group.matrixWorld).getBoundingBox(box)
}
