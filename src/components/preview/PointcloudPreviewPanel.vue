<script setup lang="ts">
import { onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { RefreshRight, View } from '@element-plus/icons-vue'
import * as THREE from 'three'
import { NodeMaterial, WebGPURenderer } from 'three/webgpu'
import { color as tslColor, vertexColor as tslVertexColor } from 'three/tsl'
import { OrbitControls } from 'three/examples/jsm/controls/OrbitControls.js'
import { DRACOLoader } from 'three/examples/jsm/loaders/DRACOLoader.js'
import { TilesRenderer } from '3d-tiles-renderer/three'
import { GLTFExtensionsPlugin } from '3d-tiles-renderer/three/plugins'
import { createUploadHeaders } from '@/config/upload-backend'
import { backendFetch, normalizeBackendUrl } from '@/api/backend-http'
import { getAssetDetail, getPointcloudTilesetUrl } from '@/api/backend-file'

type CameraPose = {
  camera: THREE.Vector3
  target: THREE.Vector3
}

const props = withDefaults(
  defineProps<{
    assetId: number | null
    minimal?: boolean
  }>(),
  {
    minimal: false,
  },
)

const emit = defineEmits<{
  (event: 'loaded-change', value: boolean): void
  (event: 'camera-change', pose: CameraPose | null): void
}>()

const viewportEl = ref<HTMLDivElement | null>(null)
const statusText = ref('等待加载点云')
const loaded = ref(false)

const defaultBgColor = '#0b1020'
const dprCap = 1.25
const tilesErrorTargetMin = 2
const tilesErrorTargetMax = 64
const tilesErrorTargetNear = 0.6
const tilesErrorTargetFar = 4
const materialMode = 'unlit'
const unlitMaterialCache = new WeakMap<any, THREE.Material>()
const unlitTSLMaterialCache = new WeakMap<any, { v0?: any; v1?: any }>()

let scene: THREE.Scene | null = null
let camera: THREE.PerspectiveCamera | null = null
let renderer: WebGPURenderer | THREE.WebGLRenderer | null = null
let controls: OrbitControls | null = null
let tileset: TilesRenderer | null = null
let tilesetWrapper: THREE.Group | null = null
let animationId = 0
let isRendering = false
let needsRender = false
let tilesLoadingCount = 0
let isPointcloudLoading = false
let resizeObserver: ResizeObserver | null = null
let pointcloudMaxDim = 1
let fixedViewSize: number | null = null
let lastTilesErrorTarget = -1
let rendererReady = false
let initPromise: Promise<void> | null = null
let rendererMode: 'webgpu' | 'webgl' | null = null
let isMountedReady = false
let loadToken = 0

const clamp = (value: number, min: number, max: number) =>
  Math.min(max, Math.max(min, value))

function disposeObject3D(obj: THREE.Object3D) {
  obj.traverse((child: any) => {
    if (child?.geometry) child.geometry.dispose?.()
    const material = child?.material
    if (Array.isArray(material)) {
      material.forEach((item) => item?.dispose?.())
    } else {
      material?.dispose?.()
    }
  })
}

function applySharedMaterialFlags(mat: any, src: any) {
  const alphaTest = src?.alphaTest ?? 0
  const opacity = src?.opacity ?? 1
  mat.alphaTest = alphaTest
  mat.opacity = opacity
  mat.transparent = alphaTest > 0 ? false : !!src?.transparent || opacity < 1
  mat.side = src?.side ?? THREE.FrontSide
}

function getOrCreateUnlitMaterialWebGL(
  src: any,
  opts: { vertexColors: boolean; isPoints: boolean },
) {
  const cached = unlitMaterialCache.get(src)
  if (cached) return cached

  const baseColor = opts.vertexColors
    ? new THREE.Color(0xffffff)
    : src?.color?.clone?.() ?? new THREE.Color(0xffffff)

  let material: THREE.Material
  if (opts.isPoints) {
    const next = new THREE.PointsMaterial({
      size: src?.size ?? 1,
      sizeAttenuation: src?.sizeAttenuation ?? true,
      color: baseColor,
      vertexColors: opts.vertexColors,
    })
    if (src?.map) next.map = src.map
    applySharedMaterialFlags(next, src)
    next.toneMapped = false
    material = next
  } else {
    const next = new THREE.MeshBasicMaterial({
      color: baseColor,
      vertexColors: opts.vertexColors,
    })
    if (src?.map) next.map = src.map
    if (src?.alphaMap) next.alphaMap = src.alphaMap
    applySharedMaterialFlags(next, src)
    next.toneMapped = false
    material = next
  }

  unlitMaterialCache.set(src, material)
  return material
}

function getOrCreateUnlitTSLMaterial(src: any, opts: { vertexColors: boolean }) {
  const entry = unlitTSLMaterialCache.get(src) ?? {}
  const cached = opts.vertexColors ? entry.v1 : entry.v0
  if (cached) return cached

  const material = new NodeMaterial()
  material.name = src?.name ? `${src.name} (TSL Unlit)` : 'TSL Unlit'
  material.fog = false
  material.lights = false

  applySharedMaterialFlags(material, src)
  material.toneMapped = false
  material.colorNode = opts.vertexColors
    ? tslVertexColor()
    : tslColor(src?.color ?? 0xffffff)
  material.vertexColors = opts.vertexColors

  ;(material as any).__viewerOriginalMaterial = src
  if (opts.vertexColors) entry.v1 = material
  else entry.v0 = material
  unlitTSLMaterialCache.set(src, entry)

  return material
}

function applyMaterialMode(root: any) {
  if (materialMode !== 'unlit') return

  root.traverse((obj: any) => {
    if (!obj?.material) return

    const hasVertexColors = !!obj.geometry?.attributes?.color
    if (rendererMode === 'webgpu') {
      if (Array.isArray(obj.material)) return
      const src = (obj.material as any)?.__viewerOriginalMaterial ?? obj.material
      const next = getOrCreateUnlitTSLMaterial(src, {
        vertexColors: hasVertexColors,
      })
      if (obj.material !== next) obj.material = next
      return
    }

    const opts = {
      vertexColors: hasVertexColors,
      isPoints: Boolean(obj.isPoints),
    }
    if (Array.isArray(obj.material)) {
      const next = obj.material.map((src: any) =>
        getOrCreateUnlitMaterialWebGL(src, opts),
      )
      const isSame = obj.material.every((src: any, index: number) => src === next[index])
      if (!isSame) obj.material = next
      return
    }

    const next = getOrCreateUnlitMaterialWebGL(obj.material, opts)
    if (obj.material !== next) obj.material = next
  })
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

function emitCameraPose() {
  emit('camera-change', getCameraPose())
}

function estimateGeometryBytes(geometry: any) {
  let bytes = 0
  const index = geometry?.index
  if (index?.array?.byteLength) bytes += index.array.byteLength

  const attrs = geometry?.attributes ?? {}
  for (const attr of Object.values(attrs) as any[]) {
    const array = attr?.isInterleavedBufferAttribute ? attr.data?.array : attr?.array
    if (array?.byteLength) bytes += array.byteLength
  }

  return bytes
}

function ensureWebGPUVertexAlignment(geometry: any) {
  if (!geometry?.attributes) return 0

  let fixed = 0
  for (const [name, attr] of Object.entries(geometry.attributes)) {
    const source = attr as any
    if (!source || source.isInterleavedBufferAttribute) continue

    const array = source.array
    const bytesPerElement = array?.BYTES_PER_ELEMENT ?? 0
    const itemSize = source.itemSize ?? 0
    const stride = bytesPerElement * itemSize

    if (!bytesPerElement || !itemSize || stride % 4 === 0) continue
    if (itemSize > 4) continue

    const count = source.count ?? 0
    const paddedSize = 4
    const paddedArray = new array.constructor(count * paddedSize)

    for (let index = 0; index < count; index++) {
      const srcIndex = index * itemSize
      const dstIndex = index * paddedSize
      for (let component = 0; component < itemSize; component++) {
        paddedArray[dstIndex + component] = array[srcIndex + component]
      }

      if (name === 'color' && bytesPerElement === 1 && source.normalized) {
        paddedArray[dstIndex + 3] = 255
      } else {
        paddedArray[dstIndex + 3] = 1
      }
    }

    const interleaved = new THREE.InterleavedBuffer(paddedArray, paddedSize)
    interleaved.usage = source.usage ?? THREE.StaticDrawUsage

    const nextAttr = new THREE.InterleavedBufferAttribute(
      interleaved,
      itemSize,
      0,
      source.normalized,
    )
    ;(nextAttr as any).gpuType = source.gpuType
    geometry.setAttribute(name, nextAttr)
    fixed++
  }

  return fixed
}

function sanitizeObjectForWebGPU(root: any) {
  let fixedAttributes = 0
  let oversizedGeometries = 0

  const maxBufferBytes = 256 * 1024 * 1024
  const softLimit = maxBufferBytes - 8 * 1024 * 1024

  root.traverse((obj: any) => {
    const geometry = obj?.geometry
    if (!geometry?.isBufferGeometry) return

    fixedAttributes += ensureWebGPUVertexAlignment(geometry)
    const bytes = estimateGeometryBytes(geometry)
    if (bytes > softLimit) {
      oversizedGeometries++
      obj.visible = false
    }
  })

  return { fixedAttributes, oversizedGeometries }
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
  pointcloudMaxDim = maxDim

  const fov = THREE.MathUtils.degToRad(nextCamera.fov)
  const distance = maxDim / 2 / Math.tan(fov / 2)
  nextControls.target.set(0, 0, 0)
  nextCamera.position.set(0, maxDim * 0.15, distance * 2.2)
  nextCamera.near = Math.max(0.01, distance / 100)
  nextCamera.far = Math.max(100000, distance * 200)
  nextCamera.updateProjectionMatrix()
  nextControls.update()
}

function fitCameraToRadius(
  nextCamera: THREE.PerspectiveCamera,
  nextControls: OrbitControls,
  radius: number,
) {
  const safeRadius = Math.max(radius, 1)
  const maxDim = safeRadius * 2
  pointcloudMaxDim = maxDim
  const fov = THREE.MathUtils.degToRad(nextCamera.fov)
  const distance = maxDim / 2 / Math.tan(fov / 2)

  nextControls.target.set(0, 0, 0)
  nextCamera.position.set(0, maxDim * 0.15, distance * 2.2)
  nextCamera.near = Math.max(0.01, distance / 100)
  nextCamera.far = Math.max(100000, distance * 200)
  nextCamera.updateProjectionMatrix()
  nextControls.update()
}

function setTopView(
  nextCamera: THREE.PerspectiveCamera,
  nextControls: OrbitControls,
  height: number,
) {
  const safeHeight = Number.isFinite(height) && height > 0 ? height : 10
  nextControls.target.set(0, 0, 0)
  nextCamera.position.set(0, safeHeight, 0.1)
  nextCamera.lookAt(0, 0, 0)
  nextCamera.near = 0.01
  nextCamera.far = Math.max(5000, safeHeight * 200)
  nextCamera.updateProjectionMatrix()
  nextControls.update()
}

function getTilesErrorTarget() {
  if (!camera || !controls) return tilesErrorTargetMin
  const base = (fixedViewSize ?? pointcloudMaxDim ?? 1) || 1
  const distance = camera.position.distanceTo(controls.target)
  const ratio = distance / Math.max(base, 1)
  const t = clamp(
    (ratio - tilesErrorTargetNear) / (tilesErrorTargetFar - tilesErrorTargetNear),
    0,
    1,
  )

  return tilesErrorTargetMin + t * (tilesErrorTargetMax - tilesErrorTargetMin)
}

function applyTilesErrorTarget() {
  if (!tileset) return
  const next = Math.round(getTilesErrorTarget() * 10) / 10
  if (Math.abs(next - lastTilesErrorTarget) < 0.1) return

  lastTilesErrorTarget = next
  tileset.errorTarget = next
}

function updateTilesetResolution() {
  if (!viewportEl.value || !tileset || !camera || !renderer) {
    return
  }

  const rect = viewportEl.value.getBoundingClientRect()
  const width = Math.max(1, Math.floor(rect.width || 1))
  const height = Math.max(1, Math.floor(rect.height || 1))
  tileset.setResolution?.(camera, width, height)
  if (rendererMode === 'webgl') {
    tileset.setResolutionFromRenderer?.(camera, renderer as THREE.WebGLRenderer)
  }
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
  updateTilesetResolution()
  return true
}

async function initViewer() {
  if (!viewportEl.value) return
  if (renderer && rendererReady) return
  if (initPromise) return initPromise

  initPromise = (async () => {
    if (!viewportEl.value) return

    const supportsWebGPU = typeof navigator !== 'undefined' && 'gpu' in navigator
    const width = viewportEl.value.clientWidth || 1
    const height = viewportEl.value.clientHeight || 1

    scene = new THREE.Scene()
    scene.background = new THREE.Color(defaultBgColor)
    camera = new THREE.PerspectiveCamera(55, width / height, 0.1, 100000)
    camera.position.set(0, 10, 20)

    const setupRendererCommon = () => {
      if (!renderer || !camera) return

      controls = new OrbitControls(camera, renderer.domElement)
      controls.enableDamping = false
      controls.addEventListener('change', () => {
        requestRender()
        emitCameraPose()
      })

      scene?.add(new THREE.AmbientLight(0xffffff, 0.7))
      const keyLight = new THREE.DirectionalLight(0xffffff, 0.9)
      keyLight.position.set(10, 10, 10)
      scene?.add(keyLight)
      const fillLight = new THREE.DirectionalLight(0x99bbff, 0.45)
      fillLight.position.set(-8, 6, -6)
      scene?.add(fillLight)

      if (!resizeObserver && typeof ResizeObserver !== 'undefined' && viewportEl.value) {
        resizeObserver = new ResizeObserver(() => {
          if (syncRendererSize()) {
            requestRender()
          }
        })
        resizeObserver.observe(viewportEl.value)
      }
    }

    const buildWebGLRenderer = () => {
      rendererMode = 'webgl'
      const nextRenderer = new THREE.WebGLRenderer({
        antialias: true,
        preserveDrawingBuffer: true,
      })
      nextRenderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, dprCap))
      nextRenderer.setSize(width, height)
      nextRenderer.setClearColor(new THREE.Color(defaultBgColor), 1)
      nextRenderer.toneMapping = THREE.ACESFilmicToneMapping
      nextRenderer.toneMappingExposure = 1
      if ('outputColorSpace' in nextRenderer) {
        nextRenderer.outputColorSpace = THREE.SRGBColorSpace
      }
      viewportEl.value?.appendChild(nextRenderer.domElement)
      renderer = nextRenderer
      rendererReady = true
      setupRendererCommon()
    }

    const buildWebGPURenderer = async () => {
      rendererMode = 'webgpu'
      const nextRenderer = new WebGPURenderer({ antialias: true })
      nextRenderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, dprCap))
      nextRenderer.setSize(width, height)
      nextRenderer.setClearColor(new THREE.Color(defaultBgColor), 1)
      nextRenderer.toneMapping = THREE.ACESFilmicToneMapping
      nextRenderer.toneMappingExposure = 1
      viewportEl.value?.appendChild(nextRenderer.domElement)
      await nextRenderer.init?.()
      renderer = nextRenderer
      rendererReady = true
      setupRendererCommon()
    }

    rendererReady = false
    if (supportsWebGPU) {
      try {
        await buildWebGPURenderer()
      } catch (error) {
        console.error('[PointcloudPreview] WebGPU 初始化失败:', error)
        rendererReady = false
      }
    }

    if (!rendererReady) {
      buildWebGLRenderer()
    }

    if (!rendererReady) return
    requestRender()
  })()

  await initPromise
}

function requestRender() {
  needsRender = true
  if (isRendering) return
  isRendering = true
  animationId = requestAnimationFrame(renderPointcloud)
}

function bumpTilesLoading(delta: number) {
  tilesLoadingCount = Math.max(0, tilesLoadingCount + delta)
  requestRender()
}

function getPlaceholderText() {
  if (props.assetId) {
    return '自动加载点云中...'
  }

  return '暂无可预览点云文件'
}

function syncFromExternalPose(pose: CameraPose | null) {
  if (!camera || !controls || !pose) {
    return
  }

  camera.position.copy(pose.camera)
  controls.target.copy(pose.target)
  camera.lookAt(controls.target)
  camera.updateProjectionMatrix()
  camera.updateMatrixWorld()
  controls.update()
  requestRender()
}

function resetPointcloudView() {
  if (!camera || !controls) {
    return
  }

  if (fixedViewSize) {
    setTopView(camera, controls, fixedViewSize * 1.2)
    requestRender()
    return
  }

  if (tileset) {
    const sphere = new THREE.Sphere()
    if (tileset.getBoundingSphere?.(sphere)) {
      fitCameraToRadius(camera, controls, sphere.radius)
      setTopView(camera, controls, sphere.radius * 2.2)
      requestRender()
      return
    }

    fitCameraToObject(camera, controls, tileset.group)
    requestRender()
    return
  }

  controls.target.set(0, 0, 0)
  camera.position.set(0, 10, 20)
  camera.updateProjectionMatrix()
  requestRender()
}

function renderPointcloud() {
  if (!renderer || !scene || !camera || !rendererReady) {
    isRendering = false
    animationId = 0
    return
  }

  try {
    if (syncRendererSize()) {
      needsRender = true
    }

    const didUpdate = controls?.update() ?? false
    camera.updateMatrixWorld()

    if (tileset) {
      applyTilesErrorTarget()
      try {
        tileset.setCamera(camera)
        tileset.update()
      } catch {
        // ignore renderer update errors
      }
    }

    const isActiveLoading = tilesLoadingCount > 0 || isPointcloudLoading
    if (needsRender || didUpdate || isActiveLoading) {
      renderer.render(scene, camera)
      needsRender = false
    }

    if (needsRender || didUpdate || isActiveLoading) {
      animationId = requestAnimationFrame(renderPointcloud)
    } else {
      isRendering = false
      animationId = 0
    }
  } catch {
    isRendering = false
    animationId = 0
  }
}

async function loadTileset(assetId: number) {
  const activeToken = ++loadToken
  await initViewer()
  if (!scene || !camera || !renderer || !controls || !rendererReady) {
    return
  }

  statusText.value = '加载点云中...'
  loaded.value = false
  emit('loaded-change', false)
  tilesLoadingCount = 0
  isPointcloudLoading = true
  lastTilesErrorTarget = -1
  pointcloudMaxDim = 1
  fixedViewSize = null
  requestRender()

  if (tileset) {
    if (tilesetWrapper) {
      scene.remove(tilesetWrapper)
      tilesetWrapper = null
    } else {
      scene.remove(tileset.group)
    }
    tileset.dispose?.()
    tileset = null
  }

  const assetDetailResult = await getAssetDetail(assetId)
  const assetDetail = assetDetailResult.data

  if (assetDetail.type !== 'pointcloud') {
    throw new Error('当前资产不是点云资源')
  }

  if (assetDetail.status !== 'ready' || !assetDetail.tilesetUrl) {
    throw new Error('点云资源尚未就绪，暂时无法预览')
  }

  const url = getPointcloudTilesetUrl(assetDetail.tilesetUrl)
  const nextTileset = new TilesRenderer(url)
  nextTileset.errorTarget = getTilesErrorTarget()
  const prefix = normalizeBackendUrl(assetDetail.tilesBaseUrl || assetDetail.tilesetUrl)

  nextTileset.fetchOptions = {
    headers: createUploadHeaders({
      Accept: 'application/json, text/plain, */*',
      'X-Requested-With': 'XMLHttpRequest',
    }),
  }

  nextTileset.registerPlugin({
    fetchData: async (uri: any, options: any) => {
      const raw = typeof uri === 'string' ? uri : uri?.toString?.() || ''
      if (!raw) {
        return null
      }

      let pathname = ''
      try {
        pathname = new URL(raw, window.location.href).toString()
      } catch {
        pathname = raw
      }

      if (!pathname.startsWith(prefix)) {
        return fetch(raw, options)
      }

      return backendFetch(raw, options)
    },
  } as any)

  const dracoLoader = new DRACOLoader(nextTileset.manager)
  dracoLoader.setDecoderPath('https://www.gstatic.com/draco/v1/decoders/')
  dracoLoader.preload()
  nextTileset.registerPlugin(new GLTFExtensionsPlugin({ dracoLoader }))

  nextTileset.setCamera(camera)
  updateTilesetResolution()

  const wrapper = new THREE.Group()
  wrapper.rotation.x = -Math.PI / 2
  wrapper.add(nextTileset.group)
  scene.add(wrapper)
  tilesetWrapper = wrapper

  nextTileset.addEventListener('tiles-load-start', () => {
    if (activeToken !== loadToken) return
    statusText.value = '点云加载中...'
    loaded.value = false
    emit('loaded-change', false)
    bumpTilesLoading(1)
    isPointcloudLoading = true
  })
  nextTileset.addEventListener('tiles-load-end', () => {
    if (activeToken !== loadToken) return
    statusText.value = '点云加载完成'
    loaded.value = true
    emit('loaded-change', true)
    bumpTilesLoading(-1)
    if (tilesLoadingCount === 0) {
      isPointcloudLoading = false
    }
  })
  nextTileset.addEventListener('load-model', ({ scene: tileScene }: any) => {
    if (activeToken !== loadToken) return
    if (!tileScene) return

    if (rendererMode === 'webgpu') {
      sanitizeObjectForWebGPU(tileScene)
    }
    applyMaterialMode(tileScene)
    requestRender()
  })
  nextTileset.addEventListener('load-error', (event: any) => {
    if (activeToken !== loadToken) return
    console.error(event)
    statusText.value = '点云加载失败'
    loaded.value = false
    emit('loaded-change', false)
    tilesLoadingCount = 0
    isPointcloudLoading = false
    requestRender()
  })
  nextTileset.addEventListener('load-root-tileset', () => {
    if (activeToken !== loadToken) return
    if (!controls || !camera) {
      return
    }

    if (rendererMode === 'webgpu') {
      sanitizeObjectForWebGPU(nextTileset.group)
    }
    applyMaterialMode(nextTileset.group)

    const sphere = new THREE.Sphere()
    if (nextTileset.getBoundingSphere?.(sphere)) {
      nextTileset.group.position.copy(sphere.center).multiplyScalar(-1)
      fitCameraToRadius(camera, controls, sphere.radius)
      setTopView(camera, controls, sphere.radius * 2.2)
      emitCameraPose()
      requestRender()
      return
    }

    fitCameraToObject(camera, controls, nextTileset.group)
    emitCameraPose()
    requestRender()
  })

  tileset = nextTileset
  requestRender()
}

function cleanup() {
  loadToken += 1

  if (animationId) {
    cancelAnimationFrame(animationId)
    animationId = 0
  }
  isRendering = false
  needsRender = false
  tilesLoadingCount = 0
  isPointcloudLoading = false

  resizeObserver?.disconnect()
  resizeObserver = null

  if (tileset) {
    if (tilesetWrapper && scene) {
      scene.remove(tilesetWrapper)
      tilesetWrapper = null
    }
    disposeObject3D(tileset.group)
    tileset.dispose?.()
    tileset = null
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
  rendererMode = null
  rendererReady = false
  initPromise = null
}

function reload() {
  if (!props.assetId) {
    statusText.value = '暂无可预览点云文件'
    loaded.value = false
    emit('loaded-change', false)
    requestRender()
    return
  }

  void loadTileset(props.assetId).catch((error) => {
    console.error(error)
    statusText.value = error instanceof Error ? error.message : '点云加载失败'
    loaded.value = false
    emit('loaded-change', false)
    requestRender()
  })
}

defineExpose({
  reload,
  resetPointcloudView,
  getCameraPose,
  syncFromExternalPose,
})

watch(
  () => [props.assetId] as const,
  () => {
    if (!isMountedReady) {
      return
    }

    reload()
  },
)

onMounted(() => {
  isMountedReady = true
  void initViewer()
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
      <span class="panel-title">点云预览</span>
      <div class="panel-actions">
        <span class="panel-status">{{ statusText }}</span>
        <button
          class="panel-refresh-btn"
          type="button"
          aria-label="重置视角"
          @click="resetPointcloudView"
        >
          <el-icon><RefreshRight /></el-icon>
        </button>
      </div>
    </div>

    <div v-if="minimal" class="panel-chip" :class="{ 'is-loaded': loaded }">
      {{ statusText }}
    </div>

    <div v-if="!loaded" class="empty-state" :class="{ 'is-minimal': minimal }">
      <el-icon><View /></el-icon>
      <p>{{ getPlaceholderText() }}</p>
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

.panel-actions {
  display: flex;
  align-items: center;
  gap: 12px;
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

.panel-refresh-btn {
  width: 32px;
  height: 32px;
  border: 0;
  border-radius: 999px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  color: #e2e8f0;
  background: rgba(30, 41, 59, 0.9);
  cursor: pointer;
  transition: background-color 0.2s ease, transform 0.2s ease;
}

.panel-refresh-btn:hover {
  background: rgba(51, 65, 85, 0.95);
}

.panel-refresh-btn:active {
  transform: scale(0.96);
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
