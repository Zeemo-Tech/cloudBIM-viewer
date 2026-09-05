import * as THREE from 'three'

type TileModelEvent = { scene: THREE.Object3D }

export type TileRendererEvents = {
  addEventListener(
    type: 'load-model' | 'dispose-model',
    callback: (event: TileModelEvent) => void,
  ): void
  removeEventListener(
    type: 'load-model' | 'dispose-model',
    callback: (event: TileModelEvent) => void,
  ): void
}

type IndexedMesh = THREE.Mesh & {
  userData: {
    ifcGlobalId?: string
    partId?: string
    tileId?: string
    positionHash?: string
    [key: string]: unknown
  }
}

type MeshIdentity = Pick<IndexedMesh['userData'], 'ifcGlobalId' | 'partId' | 'tileId' | 'positionHash'>

export class AnalysisMeshSession {
  private readonly tiles: TileRendererEvents
  private readonly byGlobalId = new Map<string, Set<IndexedMesh>>()
  private readonly componentVisibility = new Map<string, boolean>()

  private readonly onLoad = (event: TileModelEvent) => this.index(event.scene)
  private readonly onUnload = (event: TileModelEvent) => this.unindex(event.scene)

  constructor(tiles: TileRendererEvents) {
    this.tiles = tiles
    tiles.addEventListener('load-model', this.onLoad)
    tiles.addEventListener('dispose-model', this.onUnload)
  }

  private index(scene: THREE.Object3D) {
    const visit = (object: THREE.Object3D, inherited: MeshIdentity) => {
      const identity: MeshIdentity = {
        ifcGlobalId:
          typeof object.userData.ifcGlobalId === 'string'
            ? object.userData.ifcGlobalId
            : inherited.ifcGlobalId,
        partId:
          typeof object.userData.partId === 'string' ? object.userData.partId : inherited.partId,
        tileId:
          typeof object.userData.tileId === 'string' ? object.userData.tileId : inherited.tileId,
        positionHash:
          typeof object.userData.positionHash === 'string'
            ? object.userData.positionHash
            : inherited.positionHash,
      }
      if ((object as THREE.Mesh).isMesh && identity.ifcGlobalId) {
        const mesh = object as IndexedMesh
        Object.assign(mesh.userData, identity)
        let meshes = this.byGlobalId.get(identity.ifcGlobalId)
        if (!meshes) {
          meshes = new Set()
          this.byGlobalId.set(identity.ifcGlobalId, meshes)
        }
        meshes.add(mesh)
        mesh.visible = this.componentVisibility.get(identity.ifcGlobalId) ?? true
      }
      object.children.forEach((child) => visit(child, identity))
    }
    visit(scene, {})
  }

  private unindex(scene: THREE.Object3D) {
    const unloaded = new Set<THREE.Object3D>()
    scene.traverse((object) => unloaded.add(object))
    for (const [globalId, meshes] of this.byGlobalId) {
      for (const mesh of meshes) {
        if (unloaded.has(mesh)) meshes.delete(mesh)
      }
      if (meshes.size === 0) this.byGlobalId.delete(globalId)
    }
  }

  setComponentVisible(globalId: string, visible: boolean) {
    this.componentVisibility.set(globalId, visible)
    this.byGlobalId.get(globalId)?.forEach((mesh) => {
      mesh.visible = visible
    })
  }

  forEachComponentMesh(globalId: string, callback: (mesh: IndexedMesh) => void) {
    this.byGlobalId.get(globalId)?.forEach(callback)
  }

  queryBounds(globalId: string) {
    const bounds = new THREE.Box3()
    let found = false
    this.byGlobalId.get(globalId)?.forEach((mesh) => {
      bounds.expandByObject(mesh)
      found = true
    })
    return found ? bounds : null
  }

  dispose() {
    this.tiles.removeEventListener('load-model', this.onLoad)
    this.tiles.removeEventListener('dispose-model', this.onUnload)
    this.byGlobalId.clear()
    this.componentVisibility.clear()
  }
}
