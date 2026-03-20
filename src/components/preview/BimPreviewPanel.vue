<script setup lang="ts">
import { onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { View } from '@element-plus/icons-vue'
import * as THREE from 'three'
import { OrbitControls } from 'three/examples/jsm/controls/OrbitControls.js'
import { GLTFLoader, type GLTF } from 'three/examples/jsm/loaders/GLTFLoader.js'
import { DRACOLoader } from 'three/examples/jsm/loaders/DRACOLoader.js'
import { getAssetDetail, getBimGlbFile } from '@/api/backend-file'

type CameraPose = {
  camera: THREE.Vector3
  target: THREE.Vector3
}

const props = withDefaults(
  defineProps<{
    assetId: number | null
    displayName?: string
    minimal?: boolean
  }>(),
  {
    displayName: undefined,
    minimal: false,
  },
)

const emit = defineEmits<{
  (event: 'loaded-change', value: boolean): void
  (event: 'camera-change', pose: CameraPose | null): void
}>()

const viewportEl = ref<HTMLDivElement | null>(null)
const statusText = ref('等待加载 BIM 模型')
const modelLoaded = ref(false)

const defaultBgColor = '#0b1020'
const dprCap = 1.25

let scene: THREE.Scene | null = null
let camera: THREE.PerspectiveCamera | null = null
let renderer: THREE.WebGLRenderer | null = null
let controls: OrbitControls | null = null
let modelRoot: THREE.Object3D | null = null
let animationId = 0
let resizeObserver: ResizeObserver | null = null
let isRendering = false
let needsRender = false
let isMountedReady = false
let loadToken = 0

function emitCameraPose() {
  emit('camera-change', getCameraPose())
}

function disposeObject3D(obj: THREE.Object3D) {
  obj.traverse((child: any) => {
    child?.geometry?.dispose?.()
    const material = child?.material
    if (Array.isArray(material)) {
      material.forEach((item) => item?.dispose?.())
    } else {
      material?.dispose?.()
    }
  })
}

function fitCameraToObject(
  nextCamera: THREE.PerspectiveCamera,
  nextControls: OrbitControls,
  object: THREE.Object3D,
) {
  const box = new THREE.Box3().setFromObject(object)
  const size = box.getSize(new THREE.Vector3())
  const center = box.getCenter(new THREE.Vector3())

  object.position.sub(center)

  const maxDim = Math.max(size.x, size.y, size.z)
  if (maxDim <= 0) {
    return
  }

  const fov = THREE.MathUtils.degToRad(nextCamera.fov)
  const distance = maxDim / 2 / Math.tan(fov / 2)
  nextControls.target.set(0, 0, 0)
  nextCamera.position.set(0, maxDim * 0.18, distance * 2.1)
  nextCamera.near = Math.max(0.01, distance / 100)
  nextCamera.far = Math.max(5000, distance * 100)
  nextCamera.updateProjectionMatrix()
  nextCamera.updateMatrixWorld()
  nextControls.update()
}

function syncRendererSize() {
  if (!renderer || !camera || !viewportEl.value) {
    return false
  }

  const rect = viewportEl.value.getBoundingClientRect()
  const width = Math.max(1, Math.floor(rect.width || 1))
  const height = Math.max(1, Math.floor(rect.height || 1))
  const dpr = Math.min(window.devicePixelRatio || 1, dprCap)
  const canvasWidth = Math.floor(width * dpr)
  const canvasHeight = Math.floor(height * dpr)

  if (
    renderer.domElement.width === canvasWidth &&
    renderer.domElement.height === canvasHeight
  ) {
    return false
  }

  renderer.setPixelRatio(dpr)
  renderer.setSize(width, height)
  camera.aspect = width / height
  camera.updateProjectionMatrix()
  return true
}

function requestRender() {
  needsRender = true
  if (isRendering) return
  isRendering = true
  animationId = requestAnimationFrame(renderFrame)
}

function renderFrame() {
  if (!renderer || !scene || !camera) {
    animationId = 0
    isRendering = false
    return
  }

  const resized = syncRendererSize()
  if (!needsRender && !resized) {
    animationId = 0
    isRendering = false
    return
  }

  renderer.render(scene, camera)
  needsRender = false
  animationId = 0
  isRendering = false
}

function initViewer() {
  if (!viewportEl.value || renderer) {
    return
  }

  scene = new THREE.Scene()
  scene.background = new THREE.Color(defaultBgColor)

  const width = viewportEl.value.clientWidth || 1
  const height = viewportEl.value.clientHeight || 1

  camera = new THREE.PerspectiveCamera(55, width / height, 0.1, 5000)
  camera.position.set(3, 2, 5)

  renderer = new THREE.WebGLRenderer({
    antialias: true,
    powerPreference: 'high-performance',
  })
  renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, dprCap))
  renderer.setSize(width, height)
  renderer.setClearColor(new THREE.Color(defaultBgColor), 1)
  renderer.toneMapping = THREE.ACESFilmicToneMapping
  renderer.toneMappingExposure = 1
  if ('outputColorSpace' in renderer) {
    renderer.outputColorSpace = THREE.SRGBColorSpace
  }
  viewportEl.value.appendChild(renderer.domElement)

  controls = new OrbitControls(camera, renderer.domElement)
  controls.enableDamping = false
  controls.addEventListener('change', () => {
    requestRender()
    emitCameraPose()
  })

  scene.add(new THREE.AmbientLight(0xffffff, 0.7))
  const keyLight = new THREE.DirectionalLight(0xffffff, 0.9)
  keyLight.position.set(10, 10, 10)
  scene.add(keyLight)
  const fillLight = new THREE.DirectionalLight(0x99bbff, 0.45)
  fillLight.position.set(-8, 6, -6)
  scene.add(fillLight)

  resizeObserver = new ResizeObserver(() => {
    if (syncRendererSize()) {
      requestRender()
    }
  })
  resizeObserver.observe(viewportEl.value)
  requestRender()
}

async function loadByAssetId(assetId: number, displayName: string) {
  const activeToken = ++loadToken
  initViewer()
  if (!scene || !camera || !controls) {
    return
  }

  statusText.value = '加载 BIM 模型中...'
  modelLoaded.value = false
  emit('loaded-change', false)
  requestRender()

  const assetDetailResult = await getAssetDetail(assetId)
  const assetDetail = assetDetailResult.data

  if (assetDetail.type !== 'bim') {
    throw new Error('当前资产不是 BIM 模型')
  }

  if (assetDetail.status !== 'ready' || !assetDetail.glbUrl) {
    throw new Error('BIM 模型尚未就绪，暂时无法预览')
  }

  const blob = await getBimGlbFile(assetDetail.glbUrl)
  if (activeToken !== loadToken) {
    return
  }

  const objectUrl = URL.createObjectURL(blob)
  const loader = new GLTFLoader()
  const dracoLoader = new DRACOLoader()
  dracoLoader.setDecoderPath('https://www.gstatic.com/draco/v1/decoders/')
  loader.setDRACOLoader(dracoLoader)

  loader.load(
    objectUrl,
    (gltf: GLTF) => {
      URL.revokeObjectURL(objectUrl)
      dracoLoader.dispose()

      if (activeToken !== loadToken || !scene || !camera || !controls) {
        return
      }

      if (modelRoot) {
        scene.remove(modelRoot)
        disposeObject3D(modelRoot)
      }

      modelRoot = gltf.scene
      scene.add(modelRoot)
      modelRoot.updateMatrixWorld(true)
      fitCameraToObject(camera, controls, modelRoot)
      modelLoaded.value = true
      statusText.value = `已加载：${displayName}`
      emit('loaded-change', true)
      emitCameraPose()
      requestRender()
    },
    undefined,
    (error: unknown) => {
      URL.revokeObjectURL(objectUrl)
      dracoLoader.dispose()
      if (activeToken !== loadToken) {
        return
      }

      console.error(error)
      modelLoaded.value = false
      statusText.value = 'BIM 模型加载失败'
      emit('loaded-change', false)
      requestRender()
    },
  )
}

function getCameraPose(): CameraPose | null {
  if (!camera || !controls) {
    return null
  }

  return {
    camera: camera.position.clone(),
    target: controls.target.clone(),
  }
}

function setCameraPose(pose: CameraPose | null) {
  if (!camera || !controls || !pose) {
    return
  }

  camera.position.copy(pose.camera)
  controls.target.copy(pose.target)
  camera.lookAt(controls.target)
  camera.updateProjectionMatrix()
  camera.updateMatrixWorld()
  controls.update()
  emitCameraPose()
  requestRender()
}

function resetView() {
  if (!camera || !controls) {
    return
  }

  if (modelRoot) {
    fitCameraToObject(camera, controls, modelRoot)
  } else {
    controls.target.set(0, 0, 0)
    camera.position.set(3, 2, 5)
    camera.lookAt(controls.target)
    camera.updateProjectionMatrix()
    camera.updateMatrixWorld()
    controls.update()
  }

  emitCameraPose()
  requestRender()
}

function reload() {
  if (!props.assetId) {
    statusText.value = '暂无可预览 BIM 文件'
    modelLoaded.value = false
    emit('loaded-change', false)
    requestRender()
    return
  }

  void loadByAssetId(props.assetId, props.displayName || `BIM-${props.assetId}`).catch(
    (error) => {
      console.error(error)
      modelLoaded.value = false
      statusText.value =
        error instanceof Error ? error.message : 'BIM 模型加载失败'
      emit('loaded-change', false)
      requestRender()
    },
  )
}

function cleanup() {
  loadToken += 1

  if (animationId) {
    cancelAnimationFrame(animationId)
    animationId = 0
  }
  isRendering = false
  needsRender = false

  resizeObserver?.disconnect()
  resizeObserver = null

  if (modelRoot && scene) {
    scene.remove(modelRoot)
    disposeObject3D(modelRoot)
    modelRoot = null
  }

  controls?.dispose()
  renderer?.dispose()

  if (renderer?.domElement?.parentElement) {
    renderer.domElement.parentElement.removeChild(renderer.domElement)
  }

  scene = null
  camera = null
  renderer = null
  controls = null
}

defineExpose({
  reload,
  getCameraPose,
  setCameraPose,
  resetView,
})

watch(
  () => [props.assetId, props.displayName] as const,
  () => {
    if (!isMountedReady) {
      return
    }

    reload()
  },
)

onMounted(() => {
  isMountedReady = true
  initViewer()
  reload()
})

onBeforeUnmount(() => {
  isMountedReady = false
  cleanup()
})
</script>

<template>
  <div class="preview-panel" :class="{ 'is-minimal': minimal }">
    <div v-if="!minimal" class="panel-header">
      <span class="panel-title">BIM 预览</span>
      <span class="panel-status">{{ statusText }}</span>
    </div>

    <div v-if="minimal" class="panel-chip" :class="{ 'is-loaded': modelLoaded }">
      {{ statusText }}
    </div>

    <div
      v-if="!modelLoaded"
      class="empty-state"
      :class="{ 'is-minimal': minimal }"
    >
      <el-icon><View /></el-icon>
      <p>{{ statusText }}</p>
    </div>
    <div ref="viewportEl" class="viewport" />
  </div>
</template>

<style scoped>
.preview-panel {
  height: 100%;
  min-height: 520px;
  display: flex;
  flex-direction: column;
  border: 1px solid rgba(148, 163, 184, 0.18);
  border-radius: 20px;
  overflow: hidden;
  background:
    radial-gradient(circle at top, rgba(56, 189, 248, 0.08), transparent 28%),
    #0b1020;
  position: relative;
}

.preview-panel.is-minimal {
  border: 0;
  border-radius: 28px;
  box-shadow:
    inset 0 1px 0 rgba(255, 255, 255, 0.04),
    0 18px 48px rgba(2, 6, 23, 0.42);
}

.panel-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  padding: 14px 18px;
  border-bottom: 1px solid rgba(148, 163, 184, 0.16);
  background: rgba(9, 14, 30, 0.92);
}

.panel-title {
  font-weight: 700;
  color: #e2e8f0;
}

.panel-status {
  font-size: 0.84rem;
  color: #94a3b8;
}

.panel-chip {
  position: absolute;
  left: 18px;
  bottom: 18px;
  z-index: 2;
  max-width: calc(100% - 36px);
  padding: 8px 14px;
  border-radius: 999px;
  color: #cbd5e1;
  font-size: 12px;
  line-height: 1;
  backdrop-filter: blur(18px);
  background: rgba(8, 15, 30, 0.62);
  box-shadow:
    inset 0 0 0 1px rgba(148, 163, 184, 0.16),
    0 0 24px rgba(56, 189, 248, 0.1);
}

.panel-chip.is-loaded {
  color: #d9fbff;
  box-shadow:
    inset 0 0 0 1px rgba(34, 211, 238, 0.2),
    0 0 28px rgba(34, 211, 238, 0.16);
}

.empty-state {
  position: absolute;
  inset: 54px 0 0;
  z-index: 1;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 10px;
  color: #cbd5e1;
  background: linear-gradient(180deg, rgba(11, 16, 32, 0.82), rgba(11, 16, 32, 0.72));
}

.empty-state.is-minimal {
  inset: 0;
}

.viewport {
  flex: 1;
  min-height: 460px;
}
</style>
