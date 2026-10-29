import * as THREE from 'three'

type Renderable = THREE.Mesh | THREE.Points
type State = { original: THREE.Material | THREE.Material[]; faded: THREE.Material | THREE.Material[]; foreground?: Renderable }
const materials = (value: THREE.Material | THREE.Material[]) => Array.isArray(value) ? value : [value]

/** Separate opaque selected evidence from the translucent context. The original
 * positions, ownership, distances and indices are never edited by this layer.
 */
export function createInspectionAppearance() {
  const states = new Map<Renderable, State>()
  const restore = (object: Renderable) => {
    const state = states.get(object)
    if (!state) return
    if (object.material === state.faded) object.material = state.original
    if (state.foreground) {
      state.foreground.removeFromParent()
      state.foreground.geometry.dispose()
    }
    materials(state.faded).forEach(material => material.dispose())
    states.delete(object)
  }
  return {
    restore,
    clear() { for (const object of states.keys()) restore(object) },
    fade(object: Renderable, opacity: number, selectedVertex?: (index: number) => boolean) {
      let state = states.get(object)
      if (state && object.material !== state.faded) { restore(object); state = undefined }
      if (!state) {
        const cloned = materials(object.material).map(material => material.clone())
        state = { original: object.material, faded: Array.isArray(object.material) ? cloned : cloned[0] }
        states.set(object, state)
        object.material = state.faded
      }
      materials(state.faded).forEach((material, i) => {
        material.opacity = materials(state!.original)[i].opacity * opacity
        if (!material.transparent) { material.transparent = true; material.needsUpdate = true }
        material.depthWrite = false
      })
      if (!selectedVertex) return
      const source = object.geometry
      const count = source.index?.count ?? source.getAttribute('position').count
      const end = Math.min(count, source.drawRange.start + source.drawRange.count)
      const stride = (object as THREE.Points).isPoints ? 1 : 3
      const indices: number[] = []
      for (let i = source.drawRange.start; i + stride <= end; i += stride) {
        const a = source.index ? source.index.getX(i) : i
        if (!selectedVertex(a)) continue
        if (stride === 1) { indices.push(a); continue }
        const b = source.index ? source.index.getX(i + 1) : i + 1
        const c = source.index ? source.index.getX(i + 2) : i + 2
        if (selectedVertex(b) && selectedVertex(c)) indices.push(a, b, c)
      }
      if (!state.foreground) {
        const geometry = new THREE.BufferGeometry()
        state.foreground = stride === 1 ? new THREE.Points(geometry, state.original as THREE.PointsMaterial)
          : new THREE.Mesh(geometry, state.original)
        state.foreground.name = 'Inspection selected evidence'
        state.foreground.userData.__viewerPickIgnore = true
        state.foreground.userData.__inspectionForeground = true
        state.foreground.renderOrder = object.renderOrder
        object.add(state.foreground)
      }
      const geometry = state.foreground.geometry
      for (const [name, attribute] of Object.entries(source.attributes)) geometry.setAttribute(name, attribute)
      geometry.setIndex(indices)
      geometry.boundingBox = source.boundingBox
      geometry.boundingSphere = source.boundingSphere
    },
    setPointSize(size: number) {
      for (const [object, state] of states) if ((object as THREE.Points).isPoints) {
        materials(state.original).forEach(material => { (material as THREE.PointsMaterial).size = size })
        materials(state.faded).forEach(material => { (material as THREE.PointsMaterial).size = size })
      }
    },
  }
}
