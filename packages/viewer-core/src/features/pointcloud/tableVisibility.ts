import * as THREE from 'three'

export const POINTCLOUD_CATEGORY_COLORS = { table: '#94a3b8', nonTable: '#5f8275' } as const
const tableColor = new THREE.Color(POINTCLOUD_CATEGORY_COLORS.table)
const nonTableColor = new THREE.Color(POINTCLOUD_CATEGORY_COLORS.nonTable)

/** The source LAS frame and threshold used by upload-time _table_mask. */
export interface PointcloudTablePlane {
  origin: [number, number, number]
  slopes: [number, number]
  clearanceM: number
}

export function validPointcloudTablePlane(plane: PointcloudTablePlane | null | undefined): plane is PointcloudTablePlane {
  return Boolean(plane && Array.isArray(plane.origin) && plane.origin.length === 3 &&
    Array.isArray(plane.slopes) && plane.slopes.length === 2 &&
    [...plane.origin, ...plane.slopes, plane.clearanceM].every(Number.isFinite) && plane.clearanceM >= 0)
}

type PositionAttribute = THREE.BufferAttribute | THREE.InterleavedBufferAttribute
type FilterState = {
  position: PositionAttribute
  original: THREE.BufferAttribute | null
  filtered: THREE.BufferAttribute | null
  signature: string
  nonTable: Uint8Array
  colors: THREE.BufferAttribute | null
}

/** Filters draw indices only: positions, colors, bounds and materials stay intact. */
export class PointcloudTableVisibility {
  private states = new WeakMap<THREE.BufferGeometry, FilterState>()

  apply(root: THREE.Object3D, plane: PointcloudTablePlane | null | undefined, visible: boolean, prepareColors = false) {
    const validPlane = validPointcloudTablePlane(plane) ? plane : null
    const visit = (object: THREE.Object3D, parentMatrix: THREE.Matrix4) => {
      if (object.matrixAutoUpdate) object.updateMatrix()
      const sourceMatrix = parentMatrix.clone().multiply(object.matrix)
      const points = object as THREE.Points
      if (points.isPoints) this.filter(points.geometry, sourceMatrix, validPlane, visible, prepareColors)
      for (const child of object.children) visit(child, sourceMatrix)
    }
    // TilesRenderer has already composed tile transforms / RTC_CENTER into the
    // model root. Do not use matrixWorld: cached LODs are detached, and visible
    // models have the viewer's Z-up to Y-up rotation above this source frame.
    visit(root, new THREE.Matrix4())
  }

  colorAttribute(geometry: THREE.BufferGeometry) {
    return this.states.get(geometry)?.colors ?? null
  }

  private filter(geometry: THREE.BufferGeometry, sourceMatrix: THREE.Matrix4, plane: PointcloudTablePlane | null, visible: boolean, prepareColors: boolean) {
    const position = geometry.getAttribute('position')
    if (!position) return
    let state = this.states.get(geometry)
    if (state && geometry.index !== state.original && geometry.index !== state.filtered) state = undefined
    if (!state || state.position !== position) {
      if ((visible && !prepareColors) || !plane) return
      const original = state && geometry.index === state.filtered ? state.original : geometry.index
      state = { position, original, filtered: null, signature: '', nonTable: new Uint8Array(), colors: null }
      this.states.set(geometry, state)
    }
    if ((visible && !prepareColors) || !plane) {
      geometry.setIndex(state.original)
      return
    }
    const version = position instanceof THREE.InterleavedBufferAttribute ? position.data.version : position.version
    const signature = JSON.stringify([plane, sourceMatrix.elements, position.count, version, state.original?.version])
    if (signature !== state.signature) {
      state.nonTable = new Uint8Array(position.count)
      state.colors = null
      const point = new THREE.Vector3()
      for (let index = 0; index < position.count; index++) {
        point.fromBufferAttribute(position, index).applyMatrix4(sourceMatrix)
        const height = point.z - plane.origin[2] -
          (point.x - plane.origin[0]) * plane.slopes[0] -
          (point.y - plane.origin[1]) * plane.slopes[1]
        state.nonTable[index] = height > plane.clearanceM ? 1 : 0
      }
      const count = state.original?.count ?? position.count
      const indices = new Uint32Array(count)
      let retained = 0
      for (let i = 0; i < count; i++) {
        const index = state.original ? state.original.getX(i) : i
        if (state.nonTable[index]) indices[retained++] = index
      }
      state.filtered = retained === count ? state.original : new THREE.BufferAttribute(indices.slice(0, retained), 1)
      state.signature = signature
    }
    if (prepareColors && !state.colors) {
      const colors = new Float32Array(position.count * 3)
      for (let i = 0; i < position.count; i++) {
        const color = state.nonTable[i] ? nonTableColor : tableColor
        color.toArray(colors, i * 3)
      }
      state.colors = new THREE.BufferAttribute(colors, 3)
    }
    geometry.setIndex(visible ? state.original : state.filtered)
  }
}
