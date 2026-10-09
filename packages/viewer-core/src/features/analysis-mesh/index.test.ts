import assert from 'node:assert/strict'
import test from 'node:test'

import * as THREE from 'three'

import {
  AnalysisMeshContractError,
  AnalysisMeshSession,
  C2MShaderMaterial,
  parseAnalysisC2MManifest,
  parseAnalysisMeshComponents,
  parseAnalysisMeshManifest,
  parseC2MDistances,
  resolveAnalysisArtifactURL,
  sha256Hex,
  type TileRendererEvents,
} from './index'

const hash = 'a'.repeat(64)

test('v1 parsers reject wrong versions and retain the IFC tree', () => {
  assert.throws(() => parseAnalysisMeshManifest({ artifactVersion: 'wrong' }), AnalysisMeshContractError)
  const document = parseAnalysisMeshComponents({
    schema: 'analysis-mesh-components-v1',
    tree: { id: 'project', children: [{ id: 'G1', children: [] }] },
    components: [
      {
        ifcGlobalId: 'G1',
        parts: [
          {
            partId: 'G1:node',
            nodeName: 'G1',
            faceCount: 1,
            positionHash: hash,
            tiles: ['tile-0'],
          },
        ],
      },
    ],
    tiles: [
      {
        tileId: 'tile-0',
        uri: 'tiles/tile-0.glb',
        ifcGlobalId: 'G1',
        partId: 'G1:node',
        positionHash: hash,
        vertexCount: 3,
        faceCount: 1,
        byteLength: 10,
        sha256: hash,
      },
    ],
  })
  assert.deepEqual(document.tree, { id: 'project', children: [{ id: 'G1', children: [] }] })
  assert.throws(
    () => parseAnalysisMeshComponents({
      ...document,
      tiles: document.tiles.map((tile) => ({ ...tile, uri: '../outside.glb' })),
    }),
    AnalysisMeshContractError,
  )
  assert.throws(
    () => parseAnalysisMeshComponents({
      ...document,
      components: document.components.map((component) => ({
        ...component,
        parts: [...component.parts, { ...component.parts[0]! }],
      })),
    }),
    AnalysisMeshContractError,
  )
  assert.throws(
    () => parseAnalysisMeshComponents({
      ...document,
      tiles: document.tiles.map((tile) => ({ ...tile, partId: 'G1:unknown' })),
    }),
    AnalysisMeshContractError,
  )
  assert.throws(
    () => parseAnalysisMeshComponents({
      ...document,
      components: document.components.map((component) => ({
        ...component,
        parts: component.parts.map((part) => ({ ...part, tiles: ['tile-missing'] })),
      })),
    }),
    AnalysisMeshContractError,
  )
  assert.throws(
    () => parseAnalysisMeshComponents({
      ...document,
      components: document.components.map((component) => ({
        ...component,
        parts: component.parts.map((part) => ({ ...part, faceCount: 2 })),
      })),
    }),
    AnalysisMeshContractError,
  )
  assert.equal(
    resolveAnalysisArtifactURL('/artifacts/v1', 'tiles/tile-0.glb'),
    '/artifacts/v1/tiles/tile-0.glb',
  )
})

test('little-endian C2M parsing preserves NaN as unknown coverage', () => {
  const buffer = new ArrayBuffer(8)
  const view = new DataView(buffer)
  view.setFloat32(0, Number.NaN, true)
  view.setFloat32(4, 1.5, true)
  const values = parseC2MDistances(buffer, { vertexCount: 2 })
  assert.ok(Number.isNaN(values[0]))
  assert.equal(values[1], 1.5)
})

test('analysis C2M manifest binds per-tile, component, and global counts', () => {
  const stats = { knownCount: 1, unknownCount: 1, min: -0.1, max: -0.1, mean: -0.1, std: 0 }
  const manifest = {
    schema: 'analysis-c2m-result-v1',
    immutable: true,
    contentHash: hash,
    unknownEncoding: { type: 'ieee754-float32', value: 'NaN', byteOrder: 'little-endian' },
    inputAnalysisMesh: {
      contentHash: hash,
      artifactVersion: 'analysis-mesh-artifact-v1',
      modelFrame: { sourceBounds: { min: [-1, -1, -1], max: [1, 1, 1] }, normalizationCenter: [0, 0, 0] },
    },
    algorithm: { id: 'nearest', implementationVersion: '1', contractVersion: '1', effectiveParameters: {} },
    scan: { contentHash: hash, pointsBefore: 10, pointsAfter: 8 },
    transformColumnMajor: [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1],
    tiles: [{
      tileId: 'tile-0',
      distancePath: 'distances/tile-0.f32',
      vertexCount: 2,
      positionHash: hash,
      ifcGlobalId: 'G1',
      partId: 'G1:part',
      sha256: hash,
      byteLength: 8,
      stats,
    }],
    components: [{ ifcGlobalId: 'G1', stats }],
    global: stats,
    files: { 'distances/tile-0.f32': { sha256: hash, byteLength: 8 } },
  }
  assert.equal(parseAnalysisC2MManifest(manifest).global.knownCount, 1)
  assert.throws(
    () => parseAnalysisC2MManifest({ ...manifest, global: { ...stats, knownCount: 2 } }),
    AnalysisMeshContractError,
  )
})

test('browser-side SHA-256 binds downloaded artifact bytes', async () => {
  const bytes = new TextEncoder().encode('abc')
  assert.equal(await sha256Hex(bytes.buffer), 'ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad')
})

test('component visibility persists for future tile loads', () => {
  const listeners = new Map<string, (event: { scene: THREE.Object3D }) => void>()
  const renderer: TileRendererEvents = {
    addEventListener: (name, callback) => listeners.set(name, callback),
    removeEventListener: (name) => listeners.delete(name),
  }
  const session = new AnalysisMeshSession(renderer)
  session.setComponentVisible('G1', false)
  const scene = new THREE.Group()
  scene.userData.ifcGlobalId = 'G1'
  const mesh = new THREE.Mesh(new THREE.BufferGeometry())
  scene.add(mesh)
  listeners.get('load-model')?.({ scene })
  assert.equal(mesh.visible, false)
  session.setComponentVisible('G1', true)
  assert.equal(mesh.visible, true)
  session.dispose()
})

test('shader mode and thresholds update without replacing geometry', () => {
  const geometry = new THREE.BufferGeometry()
  geometry.setAttribute('position', new THREE.Float32BufferAttribute([0, 0, 0, 1, 0, 0], 3))
  const material = new C2MShaderMaterial().setDistances(
    geometry,
    new Float32Array([Number.NaN, 0.02]),
  )
  const positions = geometry.getAttribute('position')
  material.setMode('discrete')
  material.setThresholds(0.03, 0.1, 7)
  assert.equal(geometry.getAttribute('position'), positions)
  assert.equal(material.uniforms.discreteMode.value, 1)
  assert.equal(material.uniforms.bandCount.value, 7)
})
