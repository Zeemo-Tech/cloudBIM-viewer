import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { runInNewContext } from 'node:vm'
import test from 'node:test'
import ts from 'typescript'
import * as THREE from 'three'
import { InfiniteGroundGrid, getTilesetWorldBounds } from '../packages/viewer-core/src/components/preview/InfiniteGroundGrid.ts'

test('grid extends beyond the entire frustum after orbit, pan and zoom in both projections', () => {
  const grid = new InfiniteGroundGrid()
  grid.setBounds(new THREE.Box3(new THREE.Vector3(-2, 4, -3), new THREE.Vector3(2, 6, 3)))
  const height = grid.position.y
  const origin = grid.material.uniforms.gridOrigin.value.clone()
  const cell = grid.material.uniforms.cellSize.value
  try {
    for (const distance of [0.01, 10, 10000]) for (const aspect of [0.3, 1, 3]) for (const pitch of [0.001, 0.7, Math.PI / 2]) {
      const cameras = [
        new THREE.PerspectiveCamera(50, aspect, distance / 1000, distance * 100),
        new THREE.OrthographicCamera(-distance * aspect, distance * aspect, distance, -distance, 0.001, distance * 100),
      ]
      for (const camera of cameras) {
        const target = new THREE.Vector3(13000, 4, -17000)
        camera.position.copy(target).add(new THREE.Vector3(distance * Math.cos(pitch), distance * Math.sin(pitch), distance))
        camera.lookAt(target)
        grid.updateForCamera(camera)
        const planeBounds = new THREE.Box3().setFromObject(grid)
        for (const x of [-1, 1]) for (const y of [-1, 1]) for (const z of [-1, 1]) {
          const corner = new THREE.Vector3(x, y, z).unproject(camera)
          assert.ok(corner.x > planeBounds.min.x && corner.x < planeBounds.max.x)
          assert.ok(corner.z > planeBounds.min.z && corner.z < planeBounds.max.z)
        }
        assert.equal(grid.position.y, height)
        assert.ok(grid.material.uniforms.gridOrigin.value.equals(origin), 'camera motion must not slide the grid lines')
        assert.equal(grid.material.uniforms.cellSize.value, cell)
        assert.equal(grid.geometry.attributes.position.count, 4, 'zooming out must not allocate thousands of grid lines')
      }
    }
  } finally { grid.dispose() }
})

const alignmentSource = readFileSync(new URL('../packages/alignment/src/AlignmentPage.vue', import.meta.url), 'utf8').split('<script setup lang="ts">')[1].split('</script>')[0]
const ast = ts.createSourceFile('alignment.ts', alignmentSource, ts.ScriptTarget.Latest, true)
function alignmentHarness(globals) {
  const names = ['updateGridPlacement', 'syncGridVisibility', 'applyEditorTheme']
  const source = ast.statements.filter(node => ts.isFunctionDeclaration(node) && names.includes(node.name?.text)).map(node => node.getText(ast)).join('\n')
  const code = ts.transpileModule(source, { compilerOptions: { target: ts.ScriptTarget.ES2022 } }).outputText
  return runInNewContext(`${code}\n({ ${names.join(', ')} })`, globals)
}

test('analysis uses stable scan bounds for grounded grid, preserves toggles, and supports its light theme', () => {
  const grid = new InfiniteGroundGrid()
  const scan = new THREE.Group()
  scan.rotation.x = -Math.PI / 2
  scan.position.set(2, 5, -3)
  const bounds = new THREE.Box3(new THREE.Vector3(-3, -3, 0), new THREE.Vector3(3, 3, 1))
  const state = {
    gridHelper: grid, contentGroup: new THREE.Group(),
    tileset: { group: scan, getBoundingBox(box) { box.copy(bounds); return true } },
    activeCamera: new THREE.PerspectiveCamera(50, 2, 0.001, 5000),
    getTilesetWorldBounds,
    getContentWorldBox: () => { throw new Error('streamed child bounds must not determine ground height') },
    showGrid: { value: true }, isLightBackground: { value: false }, backgroundColor: { value: '' },
    onBackgroundColorChange() {}, requestRender() {},
  }
  state.activeCamera.position.set(2, 10, 10)
  state.activeCamera.lookAt(2, 5, -3)
  const api = alignmentHarness(state)
  try {
    api.updateGridPlacement()
    assert.ok(Math.abs(grid.position.y - (5 - 6 * 0.002)) < 1e-9, 'remove the old 10.5-unit gap')
    state.showGrid.value = false
    api.syncGridVisibility()
    api.updateGridPlacement()
    assert.equal(grid.visible, false)
    state.showGrid.value = true
    api.syncGridVisibility()
    assert.equal(grid.visible, true)
    state.isLightBackground.value = true
    api.applyEditorTheme()
    assert.equal(grid.material.uniforms.gridColor.value.getHexString(), '6d8399')
    state.tileset = null
    state.getContentWorldBox = () => new THREE.Box3(new THREE.Vector3(-1, 12, -1), new THREE.Vector3(1, 14, 1))
    api.updateGridPlacement()
    assert.ok(Math.abs(grid.position.y - (12 - 2 * 0.002)) < 1e-9, 'BIM-only analysis also grounds the grid')
  } finally { grid.dispose() }
})

test('grid colors change without rebuilding geometry, and GPU resources are disposed', () => {
  const grid = new InfiniteGroundGrid()
  const geometry = grid.geometry
  let disposed = 0
  grid.geometry.addEventListener('dispose', () => disposed++)
  grid.material.addEventListener('dispose', () => disposed++)
  grid.setColor('#123456')
  assert.equal(grid.material.uniforms.gridColor.value.getHexString(), '123456')
  assert.equal(grid.geometry, geometry)
  assert.equal(grid.material.depthWrite, false)
  grid.dispose()
  assert.equal(disposed, 2)
})
