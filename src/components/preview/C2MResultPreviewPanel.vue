<script setup lang="ts">
import { onBeforeUnmount, onMounted, ref, watch } from 'vue'
import * as THREE from 'three'
import { OrbitControls } from 'three/examples/jsm/controls/OrbitControls.js'
import { PLYLoader } from 'three/examples/jsm/loaders/PLYLoader.js'
import { backendRequest } from '@/api/backend-http'
import { getC2MColoredPlyUrl, getLatestC2M, type C2MResult } from '@/api/backend-c2m'

const props = defineProps<{
  scanAssetId: number | null
  bimAssetId: number | null
  result?: C2MResult | null
  calibration?: { modelMatrix: number[] } | null
  bimWorldPose?: {
    position: THREE.Vector3
    quaternion: THREE.Quaternion
    scale: THREE.Vector3
  } | null
}>()

const emit = defineEmits<{
  (event: 'loaded-change', value: boolean): void
  (event: 'camera-change', pose: { camera: THREE.Vector3; target: THREE.Vector3 } | null): void
}>()

const viewportEl = ref<HTMLDivElement | null>(null)
const statusText = ref('等待实模一致对比结果')
const loaded = ref(false)
const loading = ref(false)
const localResult = ref<C2MResult | null>(props.result ?? null)
type PreviewBackgroundTheme = 'deep' | 'light' | 'black' | 'gradient'
let backgroundTheme: PreviewBackgroundTheme = 'deep'

let scene: THREE.Scene | null = null
let camera: THREE.PerspectiveCamera | null = null
let renderer: THREE.WebGLRenderer | null = null
let controls: OrbitControls | null = null
let meshRoot: THREE.Object3D | null = null
let resizeObserver: ResizeObserver | null = null

function disposeObject(object: THREE.Object3D) {
  object.traverse((child: any) => {
    child.geometry?.dispose?.()
    if (Array.isArray(child.material)) child.material.forEach((material: any) => material?.dispose?.())
    else child.material?.dispose?.()
  })
}

function render() {
  if (renderer && scene && camera) renderer.render(scene, camera)
}

function setBackgroundTheme(theme: PreviewBackgroundTheme) {
  backgroundTheme = theme
  const colors: Record<PreviewBackgroundTheme, string> = {
    deep: '#08111d',
    light: '#f7fbff',
    black: '#000000',
    gradient: '#17365f',
  }
  if (scene) scene.background = new THREE.Color(colors[theme])
  if (renderer) renderer.setClearColor(new THREE.Color(colors[theme]), 1)
  render()
}

function setBackgroundColor(color: string) {
  if (!/^#[0-9a-f]{6}$/i.test(color)) return
  const next = new THREE.Color(color)
  if (scene) scene.background = next
  renderer?.setClearColor(next, 1)
  render()
}

function getCameraPose() {
  if (!camera || !controls) return null
  return { camera: camera.position.clone(), target: controls.target.clone() }
}

function syncFromExternalPose(pose: { camera: THREE.Vector3; target: THREE.Vector3 } | null) {
  if (!pose || !camera || !controls) return
  camera.position.copy(pose.camera)
  controls.target.copy(pose.target)
  controls.update()
  render()
}

// Copy only viewing direction and distance from another panel. Each preview
// owns an independent scene (and the point-cloud scene recenters its tileset),
// so copying absolute camera coordinates would offset the C2M mesh on screen.
function syncInitialViewFromExternalPose(pose: { camera: THREE.Vector3; target: THREE.Vector3 } | null) {
  if (!pose || !camera || !controls || !meshRoot) return
  const box = new THREE.Box3().setFromObject(meshRoot)
  const center = box.isEmpty() ? meshRoot.position.clone() : box.getCenter(new THREE.Vector3())
  const offset = pose.camera.clone().sub(pose.target)
  if (offset.lengthSq() < 1e-8) return
  controls.target.copy(center)
  camera.position.copy(center).add(offset)
  camera.lookAt(center)
  controls.update()
  render()
}

function getCameraDistance() {
  if (!camera || !controls) return 0
  return camera.position.distanceTo(controls.target)
}

function getCameraOrientation() {
  if (!camera || !controls) return null
  const offset = camera.position.clone().sub(controls.target)
  const radius = Math.max(offset.length(), 1e-6)
  return {
    lon: THREE.MathUtils.radToDeg(Math.atan2(offset.x, offset.z)),
    lat: THREE.MathUtils.radToDeg(Math.asin(THREE.MathUtils.clamp(offset.y / radius, -1, 1))),
  }
}

function syncFromRotation(rotation: { lon: number; lat: number } | null) {
  if (!rotation || !camera || !controls) return
  const distance = getCameraDistance() || 5
  const lon = THREE.MathUtils.degToRad(rotation.lon)
  const lat = THREE.MathUtils.degToRad(rotation.lat)
  camera.position.set(
    controls.target.x + distance * Math.cos(lat) * Math.sin(lon),
    controls.target.y + distance * Math.sin(lat),
    controls.target.z + distance * Math.cos(lat) * Math.cos(lon),
  )
  controls.update()
  render()
}

function syncFromCameraDistance(distance: number) {
  if (!camera || !controls) return
  const current = camera.position.clone().sub(controls.target)
  if (current.lengthSq() < 1e-8) current.set(1, 1, 1)
  camera.position.copy(controls.target).add(current.normalize().multiplyScalar(distance))
  controls.update()
  render()
}

function applyBimWorldPose() {
  if (!meshRoot || !props.bimWorldPose) return
  meshRoot.matrixAutoUpdate = true
  meshRoot.position.copy(props.bimWorldPose.position)
  meshRoot.quaternion.copy(props.bimWorldPose.quaternion)
  // The reference attaches only world position and orientation. C2M PLY
  // vertices are already expressed in the calibrated model units, so copying
  // a GLB root scale would distort the result when that root carries a unit
  // conversion.
  meshRoot.scale.set(1, 1, 1)
  meshRoot.updateMatrixWorld(true)
  render()
}

function resetView() {
  if (!camera || !controls) return
  camera.position.set(4, 3, 4)
  controls.target.set(0, 0, 0)
  controls.update()
  render()
}

function resize() {
  if (!renderer || !camera || !viewportEl.value) return
  const width = Math.max(1, viewportEl.value.clientWidth)
  const height = Math.max(1, viewportEl.value.clientHeight)
  camera.aspect = width / height
  camera.updateProjectionMatrix()
  renderer.setSize(width, height, false)
  renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 1.5))
  render()
}

async function refreshResult() {
  if (props.result) {
    localResult.value = props.result
    return
  }
  if (!props.scanAssetId || !props.bimAssetId) return
  try {
    localResult.value = (await getLatestC2M(props.scanAssetId, props.bimAssetId)).data
  } catch {
    localResult.value = null
  }
}

async function reload() {
  clearResult()
  await refreshResult()
  if (localResult.value?.coloredPlyAvailable) await loadResult()
}

async function loadResult() {
  if (!props.scanAssetId || !props.bimAssetId || !localResult.value?.coloredPlyAvailable || !scene || loading.value) return
  loading.value = true
  statusText.value = '正在加载实模一致对比结果...'
  try {
    const blob = await backendRequest<Blob>(getC2MColoredPlyUrl(props.scanAssetId, props.bimAssetId), {
      method: 'GET',
      responseType: 'blob',
    })
    const objectUrl = URL.createObjectURL(blob)
    try {
      const geometry = await new PLYLoader().loadAsync(objectUrl)
      if (!geometry.attributes.position) throw new Error('着色 PLY 缺少顶点数据')
      if (!geometry.attributes.normal) geometry.computeVertexNormals()
      geometry.computeBoundingBox()
      const center = geometry.boundingBox?.getCenter(new THREE.Vector3()) ?? new THREE.Vector3()
      geometry.translate(-center.x, -center.y, -center.z)
      clearResult()
      const group = new THREE.Group()
      group.name = 'c2m-consistency-result'
      // The reference viewer centers the result geometry and attaches it to
      // the calibrated BIM root's world pose. Reusing that pose avoids
      // re-deriving the matrix and accidentally applying axis corrections
      // twice when the GLB contains its own root transform.
      if (props.bimWorldPose) {
        group.position.copy(props.bimWorldPose.position)
        group.quaternion.copy(props.bimWorldPose.quaternion)
        group.scale.set(1, 1, 1)
      } else {
        group.rotation.x = -Math.PI / 2
      }
      const material = new THREE.MeshBasicMaterial({
        vertexColors: Boolean(geometry.attributes.color),
        side: THREE.DoubleSide,
      })
      group.add(new THREE.Mesh(geometry, material))
      scene.add(group)
      meshRoot = group
      loaded.value = true
      statusText.value = '实模一致对比结果已加载'
      emit('loaded-change', true)
      const box = new THREE.Box3().setFromObject(group)
      const size = box.getSize(new THREE.Vector3())
      const maxDim = Math.max(size.x, size.y, size.z, 1)
      camera?.position.set(maxDim * 1.8, maxDim * 1.4, maxDim * 1.8)
      controls?.target.copy(box.getCenter(new THREE.Vector3()))
      controls?.update()
      render()
    } finally {
      URL.revokeObjectURL(objectUrl)
    }
  } catch (error) {
    statusText.value = error instanceof Error ? error.message : '加载结果失败'
  } finally {
    loading.value = false
  }
}

function clearResult() {
  if (meshRoot && scene) {
    scene.remove(meshRoot)
    disposeObject(meshRoot)
  }
  meshRoot = null
  loaded.value = false
  emit('loaded-change', false)
  render()
}

defineExpose({ reload, loadResult, clearResult, resetView, setBackgroundTheme, setBackgroundColor, getCameraPose, syncFromExternalPose, syncInitialViewFromExternalPose, getCameraDistance, getCameraOrientation, syncFromRotation, syncFromCameraDistance, applyBimWorldPose })

onMounted(async () => {
  scene = new THREE.Scene()
  scene.background = new THREE.Color('#08111d')
  // Match the point-cloud panel's initial field of view so equal camera
  // distances produce the same on-screen scale across the split panes.
  camera = new THREE.PerspectiveCamera(55, 1, 0.01, 100000)
  camera.position.set(4, 3, 4)
  renderer = new THREE.WebGLRenderer({ antialias: true, alpha: false })
  renderer.outputColorSpace = THREE.SRGBColorSpace
  viewportEl.value?.appendChild(renderer.domElement)
  controls = new OrbitControls(camera, renderer.domElement)
  controls.enableDamping = true
  controls.addEventListener('change', () => { render(); emit('camera-change', getCameraPose()) })
  resizeObserver = new ResizeObserver(resize)
  if (viewportEl.value) resizeObserver.observe(viewportEl.value)
  resize()
  await refreshResult()
  if (localResult.value?.coloredPlyAvailable) await loadResult()
})

watch(() => props.result, async (value) => {
  localResult.value = value ?? null
  if (value?.coloredPlyAvailable && !loaded.value) await loadResult()
})

watch(
  () => props.bimWorldPose,
  () => {
    applyBimWorldPose()
  },
  { deep: true },
)

onBeforeUnmount(() => {
  resizeObserver?.disconnect()
  controls?.removeEventListener('change', render)
  controls?.dispose()
  clearResult()
  renderer?.dispose()
  renderer?.domElement.remove()
})
</script>

<template>
  <div class="c2m-result-preview-panel">
    <div ref="viewportEl" class="c2m-result-preview-panel__viewport" />
    <div class="c2m-result-preview-panel__label">实模一致对比</div>
    <div v-if="!loaded" class="c2m-result-preview-panel__empty">
      <strong>{{ statusText }}</strong>
      <span v-if="localResult?.stats">Mean {{ localResult.stats.mean.toFixed(4) }} m · P95 {{ localResult.stats.p95.toFixed(4) }} m</span>
      <el-button v-if="localResult?.coloredPlyAvailable" size="small" type="primary" :loading="loading" @click="loadResult">加载结果</el-button>
    </div>
  </div>
</template>

<style scoped>
.c2m-result-preview-panel { position: relative; min-height: 0; height: 100%; overflow: hidden; background: #08111d; }
.c2m-result-preview-panel__viewport { position: absolute; inset: 0; }
.c2m-result-preview-panel__viewport :deep(canvas) { display: block; width: 100%; height: 100%; }
.c2m-result-preview-panel__label { position: absolute; top: 14px; left: 14px; z-index: 2; padding: 6px 10px; border: 1px solid rgba(244,114,182,.35); background: rgba(15,23,42,.78); color: #fbcfe8; font-size: 12px; }
.c2m-result-preview-panel__empty { position: absolute; inset: 0; z-index: 1; display: flex; flex-direction: column; align-items: center; justify-content: center; gap: 10px; color: rgba(226,232,240,.8); font-size: 12px; text-align: center; pointer-events: none; }
.c2m-result-preview-panel__empty strong, .c2m-result-preview-panel__empty span, .c2m-result-preview-panel__empty :deep(button) { pointer-events: auto; }
.c2m-result-preview-panel__clear { position: absolute; right: 14px; top: 14px; z-index: 2; padding: 5px 9px; border: 1px solid rgba(248,113,113,.5); background: rgba(127,29,29,.72); color: #fee2e2; cursor: pointer; }
</style>
