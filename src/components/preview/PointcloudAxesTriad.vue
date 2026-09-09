<script setup lang="ts">
import { onBeforeUnmount, onMounted, ref } from 'vue'
import * as THREE from 'three'
import type { CameraPose } from './UnifiedViewer3D.vue'

const props = defineProps<{
  pose: CameraPose | null
}>()

const hostRef = ref<HTMLDivElement | null>(null)
const SIZE = 112
const AXIS_LENGTH = 0.78
const DEFAULT_UP = new THREE.Vector3(0, 1, 0)
const WORLD_AXIS_DIRECTIONS: ReadonlyArray<readonly [number, number, number]> = [
  [1, 0, 0],
  [0, 1, 0],
  [0, 0, 1],
]
const WORLD_AXIS_COLORS = [0xd95c65, 0x55a873, 0x4e83cf]
const WORLD_AXIS_LABELS = ['X', 'Y', 'Z']

const miniScene = new THREE.Scene()
const miniCamera = new THREE.OrthographicCamera(-1.5, 1.5, 1.5, -1.5, 0.1, 10)
const mainCameraProxy = new THREE.PerspectiveCamera()
const axesGroup = new THREE.Group()
let renderer: THREE.WebGLRenderer | null = null
let animationFrame = 0

function createAxisLabelTexture(color: number, label: string) {
  const canvas = document.createElement('canvas')
  canvas.width = 96
  canvas.height = 96
  const context = canvas.getContext('2d')
  if (!context) return null

  context.clearRect(0, 0, canvas.width, canvas.height)
  context.fillStyle = `#${color.toString(16).padStart(6, '0')}`
  context.beginPath()
  context.arc(48, 48, 36, 0, Math.PI * 2)
  context.fill()
  context.fillStyle = '#ffffff'
  context.font = '700 44px sans-serif'
  context.textAlign = 'center'
  context.textBaseline = 'middle'
  context.fillText(label, 48, 52)

  const texture = new THREE.CanvasTexture(canvas)
  texture.colorSpace = THREE.SRGBColorSpace
  return texture
}

function createLabeledAxis(direction: readonly [number, number, number], color: number, label: string) {
  const unit = new THREE.Vector3(...direction).normalize()
  const group = new THREE.Group()
  const shaft = new THREE.Mesh(
    new THREE.CylinderGeometry(0.05, 0.05, AXIS_LENGTH, 10),
    new THREE.MeshBasicMaterial({ color }),
  )
  shaft.quaternion.setFromUnitVectors(new THREE.Vector3(0, 1, 0), unit)
  shaft.position.copy(unit).multiplyScalar(AXIS_LENGTH / 2)
  group.add(shaft)

  const tip = new THREE.Mesh(
    new THREE.ConeGeometry(0.12, 0.24, 12),
    new THREE.MeshBasicMaterial({ color }),
  )
  tip.quaternion.copy(shaft.quaternion)
  tip.position.copy(unit).multiplyScalar(AXIS_LENGTH + 0.1)
  group.add(tip)

  const texture = createAxisLabelTexture(color, label)
  if (texture) {
    const sprite = new THREE.Sprite(new THREE.SpriteMaterial({
      map: texture,
      transparent: true,
      depthTest: false,
    }))
    sprite.scale.set(0.5, 0.5, 0.5)
    sprite.position.copy(unit).multiplyScalar(AXIS_LENGTH + 0.4)
    group.add(sprite)
  }

  return group
}

function syncAxesPose() {
  const pose = props.pose
  if (pose) {
    mainCameraProxy.position.copy(pose.camera)
    mainCameraProxy.up.copy(pose.up ?? DEFAULT_UP)
    mainCameraProxy.lookAt(pose.target)
    mainCameraProxy.updateMatrixWorld(true)
    axesGroup.quaternion.copy(mainCameraProxy.quaternion).invert()
  } else {
    axesGroup.quaternion.identity()
  }
  axesGroup.updateMatrixWorld(true)
}

function render() {
  animationFrame = window.requestAnimationFrame(render)
  if (!renderer) return
  syncAxesPose()
  renderer.render(miniScene, miniCamera)
}

function init() {
  const host = hostRef.value
  if (!host) return

  miniCamera.position.set(0, 0, 4)
  miniCamera.lookAt(0, 0, 0)
  renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true })
  renderer.setClearColor(0x000000, 0)
  renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2))
  renderer.setSize(SIZE, SIZE, false)
  renderer.domElement.setAttribute('aria-hidden', 'true')
  renderer.domElement.style.display = 'block'
  renderer.domElement.style.width = `${SIZE}px`
  renderer.domElement.style.height = `${SIZE}px`
  host.appendChild(renderer.domElement)

  WORLD_AXIS_DIRECTIONS.forEach((direction, index) => {
    axesGroup.add(createLabeledAxis(direction, WORLD_AXIS_COLORS[index], WORLD_AXIS_LABELS[index]))
  })
  miniScene.add(axesGroup)
  render()
}

function dispose() {
  window.cancelAnimationFrame(animationFrame)
  axesGroup.traverse((object) => {
    const mesh = object as THREE.Mesh
    mesh.geometry?.dispose()
    const materials = Array.isArray(mesh.material) ? mesh.material : [mesh.material]
    materials.filter(Boolean).forEach((material) => {
      if (material instanceof THREE.SpriteMaterial) material.map?.dispose()
      material.dispose()
    })
  })
  renderer?.dispose()
  renderer?.forceContextLoss()
  renderer?.domElement.remove()
  axesGroup.clear()
  renderer = null
}

onMounted(init)
onBeforeUnmount(dispose)
</script>

<template>
  <div
    ref="hostRef"
    class="pointcloud-axes-triad"
    role="img"
    aria-label="视口坐标轴 X Y Z"
    title="视口坐标轴 X Y Z"
  />
</template>

<style scoped>
.pointcloud-axes-triad {
  width: 112px;
  height: 112px;
  overflow: visible;
  pointer-events: none;
}
</style>
