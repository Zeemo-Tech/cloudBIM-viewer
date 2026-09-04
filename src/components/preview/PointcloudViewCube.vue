<script setup lang="ts">
import { onBeforeUnmount, onMounted, ref } from 'vue'
import * as THREE from 'three'
import type { CameraPose } from './UnifiedViewer3D.vue'

const props = defineProps<{
  pose: CameraPose | null
}>()

const emit = defineEmits<{
  (event: 'home'): void
  (event: 'select-direction', direction: [number, number, number]): void
  (event: 'orbit', delta: { lon: number; lat: number }): void
  (event: 'roll', direction: -1 | 1): void
}>()

const hostRef = ref<HTMLDivElement | null>(null)
const SIZE = 84
const HALF = 0.68
const BAND = 0.2
const LABELS = ['右', '左', '顶', '底', '前', '后']
const raycaster = new THREE.Raycaster()
const pointer = new THREE.Vector2()
const miniScene = new THREE.Scene()
const miniCamera = new THREE.OrthographicCamera(-1.5, 1.5, 1.5, -1.5, 0.1, 10)
const cubeGroup = new THREE.Group()
const faceMaterials: THREE.MeshStandardMaterial[] = []
let renderer: THREE.WebGLRenderer | null = null
let cube: THREE.Mesh | null = null
let solidOutline: THREE.LineSegments | null = null
let dashedOutline: THREE.LineSegments | null = null
let animationFrame = 0
let activeFaces: number[] = []
let pointerDown = false
let dragging = false
let startX = 0
let startY = 0

function faceTexture(label: string) {
  const canvas = document.createElement('canvas')
  canvas.width = 192
  canvas.height = 192
  const context = canvas.getContext('2d')!
  context.fillStyle = '#f4f7f9'
  context.fillRect(0, 0, 192, 192)
  context.fillStyle = '#26343b'
  context.font = '600 54px sans-serif'
  context.textAlign = 'center'
  context.textBaseline = 'middle'
  context.fillText(label, 96, 98)
  const texture = new THREE.CanvasTexture(canvas)
  texture.colorSpace = THREE.SRGBColorSpace
  texture.anisotropy = 4
  return texture
}

function directionFaces(direction: THREE.Vector3) {
  const faces: number[] = []
  if (direction.x) faces.push(direction.x > 0 ? 0 : 1)
  if (direction.y) faces.push(direction.y > 0 ? 2 : 3)
  if (direction.z) faces.push(direction.z > 0 ? 4 : 5)
  return faces
}

function updateHighlight(next: number[]) {
  activeFaces = next
  faceMaterials.forEach((material, index) => {
    const active = activeFaces.includes(index)
    material.color.set(active ? 0x6bbdf5 : 0xffffff)
    material.opacity = active ? 1 : 0.52
    material.emissive.set(active ? 0x174f75 : 0x000000)
    material.emissiveIntensity = active ? 0.2 : 0
  })
}

function poseQuaternion() {
  if (!props.pose) return new THREE.Quaternion()
  const camera = new THREE.PerspectiveCamera()
  camera.position.copy(props.pose.camera)
  camera.up.copy(props.pose.up ?? new THREE.Vector3(0, 1, 0))
  camera.lookAt(props.pose.target)
  camera.updateMatrixWorld(true)
  return camera.quaternion
}

function render() {
  animationFrame = requestAnimationFrame(render)
  if (!renderer || !cube) return
  cubeGroup.quaternion.copy(poseQuaternion()).invert()
  cubeGroup.updateMatrixWorld(true)
  const direction = props.pose
    ? props.pose.camera.clone().sub(props.pose.target).normalize()
    : new THREE.Vector3(0, 0, 1)
  const max = Math.max(Math.abs(direction.x), Math.abs(direction.y), Math.abs(direction.z), 1e-8)
  const snapped = new THREE.Vector3(
    Math.abs(direction.x) >= max * 0.7 ? Math.sign(direction.x) : 0,
    Math.abs(direction.y) >= max * 0.7 ? Math.sign(direction.y) : 0,
    Math.abs(direction.z) >= max * 0.7 ? Math.sign(direction.z) : 0,
  )
  const aligned = direction.dot(snapped.clone().normalize()) > 0.985
  if (solidOutline) solidOutline.visible = aligned
  if (dashedOutline) dashedOutline.visible = !aligned
  renderer.render(miniScene, miniCamera)
}

function hitDirection(event: PointerEvent) {
  if (!renderer || !cube) return null
  const rect = renderer.domElement.getBoundingClientRect()
  if (!rect.width || !rect.height) return null
  pointer.set(
    ((event.clientX - rect.left) / rect.width) * 2 - 1,
    -((event.clientY - rect.top) / rect.height) * 2 + 1,
  )
  raycaster.setFromCamera(pointer, miniCamera)
  const hit = raycaster.intersectObject(cube, false)[0]
  if (!hit) return null
  const local = cube.worldToLocal(hit.point.clone())
  const values = [local.x, local.y, local.z]
  const direction = new THREE.Vector3()
  const faceAxis = values.reduce(
    (best, value, index) => (Math.abs(value) > Math.abs(values[best]) ? index : best),
    0,
  )
  const threshold = HALF - BAND
  values.forEach((value, index) => {
    if (index === faceAxis || Math.abs(value) >= threshold) {
      direction.setComponent(index, Math.sign(value) || 1)
    }
  })
  return direction
}

function handlePointerEnter() {
  hostRef.value?.classList.add('is-active')
}

function handlePointerLeave() {
  if (dragging) return
  pointerDown = false
  updateHighlight([])
  hostRef.value?.classList.remove('is-active')
}

function handlePointerDown(event: PointerEvent) {
  if (event.button !== 0 || !hitDirection(event)) return
  pointerDown = true
  dragging = false
  startX = event.clientX
  startY = event.clientY
  hostRef.value?.setPointerCapture(event.pointerId)
  event.preventDefault()
}

function handlePointerMove(event: PointerEvent) {
  if (pointerDown && !dragging && Math.hypot(event.clientX - startX, event.clientY - startY) >= 5) {
    dragging = true
  }
  if (dragging) {
    emit('orbit', { lon: -event.movementX * (180 / SIZE), lat: event.movementY * (180 / SIZE) })
    return
  }
  const direction = hitDirection(event)
  updateHighlight(direction ? directionFaces(direction) : [])
}

function handlePointerUp(event: PointerEvent) {
  const direction = hitDirection(event)
  const wasDragging = dragging
  pointerDown = false
  dragging = false
  if (hostRef.value?.hasPointerCapture(event.pointerId)) {
    hostRef.value.releasePointerCapture(event.pointerId)
  }
  if (!wasDragging && direction) {
    emit('select-direction', [direction.x, direction.y, direction.z])
  }
}

function init() {
  const host = hostRef.value
  if (!host) return
  miniCamera.position.set(0, 0, 5)
  miniCamera.lookAt(0, 0, 0)
  renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true })
  renderer.setClearColor(0x000000, 0)
  renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2))
  renderer.setSize(SIZE, SIZE, false)
  renderer.domElement.setAttribute('aria-hidden', 'true')
  host.prepend(renderer.domElement)

  LABELS.forEach((label) => {
    faceMaterials.push(new THREE.MeshStandardMaterial({
      map: faceTexture(label),
      color: 0xffffff,
      transparent: true,
      opacity: 0.52,
      roughness: 0.62,
      metalness: 0.04,
    }))
  })
  cube = new THREE.Mesh(new THREE.BoxGeometry(HALF * 2, HALF * 2, HALF * 2), faceMaterials)
  cubeGroup.add(cube)
  const edges = new THREE.EdgesGeometry(cube.geometry, 24)
  solidOutline = new THREE.LineSegments(edges, new THREE.LineBasicMaterial({
    color: 0x71838c,
    transparent: true,
    opacity: 0.55,
  }))
  dashedOutline = new THREE.LineSegments(edges.clone(), new THREE.LineDashedMaterial({
    color: 0x71838c,
    transparent: true,
    opacity: 0.45,
    dashSize: 0.07,
    gapSize: 0.045,
  }))
  dashedOutline.computeLineDistances()
  cubeGroup.add(solidOutline, dashedOutline)
  miniScene.add(cubeGroup)
  miniScene.add(new THREE.HemisphereLight(0xffffff, 0x62727a, 2.2))
  const light = new THREE.DirectionalLight(0xffffff, 2.6)
  light.position.set(-3, 4, 5)
  miniScene.add(light)
  render()
}

function cleanup() {
  cancelAnimationFrame(animationFrame)
  faceMaterials.forEach((material) => {
    material.map?.dispose()
    material.dispose()
  })
  cube?.geometry.dispose()
  solidOutline?.geometry.dispose()
  dashedOutline?.geometry.dispose()
  renderer?.dispose()
  renderer?.forceContextLoss()
  renderer?.domElement.remove()
}

onMounted(init)
onBeforeUnmount(cleanup)
</script>

<template>
  <div
    ref="hostRef"
    class="pointcloud-view-cube"
    role="group"
    tabindex="0"
    aria-label="视角导航立方体"
    title="拖动旋转视角；点击面、棱或角切换视角"
    @pointerenter="handlePointerEnter"
    @pointerleave="handlePointerLeave"
    @pointerdown="handlePointerDown"
    @pointermove="handlePointerMove"
    @pointerup="handlePointerUp"
    @pointercancel="handlePointerLeave"
  >
    <button class="view-cube-home" type="button" title="回到主视图" aria-label="回到主视图" @pointerdown.stop="emit('home')">
      <svg viewBox="0 0 16 16" aria-hidden="true">
        <path d="M2.5 7.2 8 2.8l5.5 4.4V13a.8.8 0 0 1-.8.8H9.2V9.6H6.8V13.8H3.3A.8.8 0 0 1 2.5 13V7.2z" />
      </svg>
    </button>
    <div class="view-cube-roll">
      <button type="button" title="逆时针旋转 90°" aria-label="逆时针旋转 90°" @pointerdown.stop="emit('roll', -1)">
        <svg viewBox="0 0 16 16" aria-hidden="true"><path d="M12.4 10.6a5.2 5.2 0 0 0-8.6-3.8" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" /><path d="M2.4 7.6 6.1 5.8 4.6 9.6z" fill="currentColor" /></svg>
      </button>
      <button type="button" title="顺时针旋转 90°" aria-label="顺时针旋转 90°" @pointerdown.stop="emit('roll', 1)">
        <svg viewBox="0 0 16 16" aria-hidden="true"><path d="M3.6 10.6a5.2 5.2 0 0 1 8.6-3.8" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" /><path d="M13.6 7.6 9.9 5.8 11.4 9.6z" fill="currentColor" /></svg>
      </button>
    </div>
  </div>
</template>

<style scoped>
.pointcloud-view-cube {
  position: absolute;
  z-index: 31;
  top: 16px;
  right: 14px;
  width: 84px;
  height: 84px;
  overflow: visible;
  border: 0;
  outline: none;
  color: #9aa8af;
  cursor: pointer;
  touch-action: none;
}

.pointcloud-view-cube :deep(canvas) {
  width: 84px;
  height: 84px;
  display: block;
}

.view-cube-home {
  position: absolute;
  z-index: 2;
  top: -1px;
  left: -18px;
  width: 16px;
  height: 16px;
  padding: 0;
  display: grid;
  place-items: center;
  border: 0;
  color: inherit;
  background: transparent;
  opacity: 0;
  cursor: pointer;
  transition: opacity 120ms ease;
}

.view-cube-home svg {
  width: 14px;
  height: 14px;
  fill: currentColor;
}

.view-cube-roll {
  position: absolute;
  z-index: 2;
  top: 0;
  left: 0;
  right: 0;
  display: flex;
  justify-content: space-between;
  padding: 0 1px;
  opacity: 0;
  pointer-events: none;
  transition: opacity 120ms ease;
}

.view-cube-roll button {
  width: 18px;
  height: 18px;
  padding: 0;
  display: grid;
  place-items: center;
  border: 0;
  border-radius: 4px;
  color: inherit;
  background: transparent;
  cursor: pointer;
}

.view-cube-roll button:hover {
  color: #e8eef1;
  background: rgb(255 255 255 / 12%);
}

.view-cube-roll svg {
  width: 16px;
  height: 16px;
}

.pointcloud-view-cube.is-active .view-cube-home,
.pointcloud-view-cube.is-active .view-cube-roll {
  opacity: 0.95;
}

.pointcloud-view-cube.is-active .view-cube-roll button {
  pointer-events: auto;
}
</style>
