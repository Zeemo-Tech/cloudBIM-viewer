import assert from 'node:assert/strict'
import test from 'node:test'
// @ts-ignore Node's strip-types runner uses the explicit source extension.
import { selectPointcloudRepresentation } from './representation.ts'

test('prefers a ready table-free 3D Tiles representation', () => {
  assert.deepEqual(selectPointcloudRepresentation('/source/tileset.json', [{
    kind: 'table-free', format: '3d-tiles', status: 'ready', url: '/clean/tileset.json', version: 'v2',
  }], true), {
    kind: 'table-free', url: '/clean/tileset.json', version: 'v2', tableFreeAvailable: true,
  })
})

test('resolves a representation base URL and supports explicit source display', () => {
  const representations = [{
    kind: 'table-free', format: '3d-tiles', status: 'ready', baseUrl: '/clean/v3/', version: 'v3',
  }]
  assert.equal(selectPointcloudRepresentation('/source.json', representations, true).url, '/clean/v3/tiles/tileset.json')
  assert.deepEqual(selectPointcloudRepresentation('/source.json', representations, false), {
    kind: 'source', url: '/source.json', tableFreeAvailable: true,
  })
})

test('falls back to source when table-free data is missing or unfinished', () => {
  const selected = selectPointcloudRepresentation('/source.json', [{
    kind: 'table-free', format: '3d-tiles', status: 'processing', url: '/clean.json',
  }], true)
  assert.deepEqual(selected, {
    kind: 'source', url: '/source.json', tableFreeAvailable: false,
  })
})

test('rejects a stale ready representation from a different preprocess version', () => {
  const selected = selectPointcloudRepresentation('/source.json', [{
    kind: 'table-free', format: '3d-tiles', status: 'ready', url: '/stale.json', version: 'old',
  }], true, 'current')
  assert.equal(selected.kind, 'source')
  assert.equal(selected.tableFreeAvailable, false)
})
