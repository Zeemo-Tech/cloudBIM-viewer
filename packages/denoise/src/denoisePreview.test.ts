import assert from 'node:assert/strict'
import test from 'node:test'
// @ts-ignore Node's strip-types runner uses the explicit source extension.
import { parseDenoisePreview, applyDenoisePreviewAppearance, DENOISE_CLASSES } from './denoisePreview.ts'
import { Color } from 'three'
import { instanceColorDistance } from '@cloudbim/viewer-core'

function preview(withInstances = true) {
  return new TextEncoder().encode(`ply
format ascii 1.0
element vertex 5
property float x
property float y
property float z
property uchar label
${withInstances ? 'property uint instance\n' : ''}end_header
${[[0, 3, 7], [1, 2, 0], [2, 3, 301], [3, 3, 7], [4, 4, 0]]
    .map(([x, label, instance]) => `${x} 0 0 ${label}${withInstances ? ` ${instance}` : ''}`).join('\n')}
`).buffer
}

function rgb(geometry: ReturnType<typeof parseDenoisePreview>, point: number) {
  const colors = geometry.getAttribute('color')
  return [colors.getX(point), colors.getY(point), colors.getZ(point)]
}

test('comparison isolates all fragments of the chosen instances and never falls back for a missing bar', () => {
  const geometry = parseDenoisePreview(preview(), 'cleaned')
  assert.equal(applyDenoisePreviewAppearance(geometry, 'cleaned', [3], [7]), 2)
  assert.deepEqual(Array.from(geometry.index!.array).slice(0, geometry.drawRange.count), [0, 3])
  assert.equal(applyDenoisePreviewAppearance(geometry, 'cleaned', [3], []), 0)
  assert.equal(applyDenoisePreviewAppearance(geometry, 'cleaned', [3]), 3)
  geometry.dispose()
})

test('final steel instances retain their IDs and colors across separated fragments', () => {
  const geometry = parseDenoisePreview(preview(), 'cleaned')
  assert.deepEqual(Array.from(geometry.index!.array).slice(0, geometry.drawRange.count), [0, 2, 3])
  assert.equal(geometry.getAttribute('instance').getX(2), 301)
  assert.deepEqual(rgb(geometry, 0), rgb(geometry, 3))
  assert.notDeepEqual(rgb(geometry, 0), rgb(geometry, 2))
  const reloaded = parseDenoisePreview(preview(), 'cleaned')
  assert.deepEqual(rgb(geometry, 2), rgb(reloaded, 2))
  geometry.dispose()
  reloaded.dispose()
})

test('semantic view preserves all classes independently of instance coloring', () => {
  const geometry = parseDenoisePreview(preview(), 'classes')
  assert.equal(geometry.index, null)
  assert.deepEqual(rgb(geometry, 0), rgb(geometry, 2))
  assert.notDeepEqual(rgb(geometry, 0), rgb(geometry, 1))
  geometry.dispose()
})

test('legacy previews require regeneration for instances but still support classes', () => {
  assert.throws(() => parseDenoisePreview(preview(false), 'cleaned'), /尚未包含钢筋实例信息/)
  parseDenoisePreview(preview(false), 'classes').dispose()
})

test('category switches isolate, combine, hide and restore points without changing their positions', () => {
  const geometry = parseDenoisePreview(preview(), 'classes')
  const positions = geometry.getAttribute('position')
  assert.equal(applyDenoisePreviewAppearance(geometry, 'classes', [2]), 1)
  assert.deepEqual(Array.from(geometry.index!.array).slice(0, geometry.drawRange.count), [1])
  assert.equal(applyDenoisePreviewAppearance(geometry, 'classes', [2, 4]), 2)
  assert.deepEqual(Array.from(geometry.index!.array).slice(0, geometry.drawRange.count), [1, 4])
  assert.equal(applyDenoisePreviewAppearance(geometry, 'classes', []), 0)
  assert.equal(geometry.drawRange.count, 0)
  assert.equal(applyDenoisePreviewAppearance(geometry, 'classes', [1]), 0, 'absent classes remain empty')
  assert.equal(applyDenoisePreviewAppearance(geometry, 'classes', DENOISE_CLASSES.map(item => item.id)), 5)
  assert.deepEqual(Array.from(geometry.index!.array).slice(0, geometry.drawRange.count), [0, 1, 2, 3, 4])
  assert.equal(geometry.getAttribute('position'), positions)
  geometry.dispose()
})

test('instance color changes preserve category filters and cache stable colors across repeated toggles', () => {
  const geometry = parseDenoisePreview(preview(), 'cleaned')
  const instanceColors = geometry.getAttribute('color')
  const indexBuffer = geometry.index
  const steelColor = rgb(geometry, 0)
  const fixtureColor = rgb(geometry, 1)
  assert.equal(applyDenoisePreviewAppearance(geometry, 'cleaned', [2, 3]), 4)
  assert.deepEqual(Array.from(geometry.index!.array).slice(0, geometry.drawRange.count), [0, 1, 2, 3])
  assert.deepEqual(rgb(geometry, 1), fixtureColor)
  applyDenoisePreviewAppearance(geometry, 'classes', [2, 3])
  assert.deepEqual(Array.from(geometry.index!.array).slice(0, geometry.drawRange.count), [0, 1, 2, 3])
  const categoryColors = geometry.getAttribute('color')
  applyDenoisePreviewAppearance(geometry, 'cleaned', [3])
  assert.equal(geometry.getAttribute('color'), instanceColors)
  assert.equal(geometry.index, indexBuffer, 'repeated filtering must reuse the GPU index buffer')
  assert.deepEqual(rgb(geometry, 0), steelColor)
  applyDenoisePreviewAppearance(geometry, 'classes', [4])
  assert.equal(geometry.getAttribute('color'), categoryColors)
  assert.deepEqual(Array.from(geometry.index!.array).slice(0, geometry.drawRange.count), [4])
  geometry.dispose()
})

test('a legacy instance-color error leaves the current category preview intact', () => {
  const geometry = parseDenoisePreview(preview(false), 'classes')
  applyDenoisePreviewAppearance(geometry, 'classes', [2])
  const colors = geometry.getAttribute('color')
  assert.throws(() => applyDenoisePreviewAppearance(geometry, 'cleaned', [3]), /尚未包含钢筋实例信息/)
  assert.equal(geometry.getAttribute('color'), colors)
  assert.deepEqual(Array.from(geometry.index!.array).slice(0, geometry.drawRange.count), [1])
  geometry.dispose()
})

test('alignment PLY rendering uses spatial colors for parallel long bars with similar old green hues', () => {
  // Production IDs 33 and 88 were both green in this separate rendering path.
  // Exercise the parser's final vertex colors, not just the palette generator.
  const rows: string[] = []
  for (const [id, y, z] of [[33, -.44, -.03], [88, -.41, .035], [20, .23, -.03], [28, .21, -.03]]) {
    for (let i = 0; i < 50; i++) {
      if (i > 20 && i < 30) continue // separated visible fragments keep one ID
      rows.push(`${-1.8 + i * .072} ${y} ${z} 3 ${id}`)
    }
  }
  const bytes = new TextEncoder().encode(`ply\nformat ascii 1.0\nelement vertex ${rows.length}\nproperty float x\nproperty float y\nproperty float z\nproperty uchar label\nproperty uint instance\nend_header\n${rows.join('\n')}\n`)
  const geometry = parseDenoisePreview(bytes.buffer, 'cleaned')
  const perInstance = new Map<number, number[]>()
  for (let i = 0; i < rows.length; i++) {
    const id = geometry.getAttribute('instance').getX(i), color = rgb(geometry, i)
    if (perInstance.has(id)) assert.deepEqual(color, perInstance.get(id))
    perInstance.set(id, color)
  }
  for (const [a, b] of [[33, 88], [20, 28]]) {
    assert.ok(instanceColorDistance(perInstance.get(a)!, perInstance.get(b)!) >= .18)
    const old = new Color().setHSL(((a * .61803398875) % 1 + 1) % 1, .72, .58).toArray()
    assert.notDeepEqual(perInstance.get(a), old, 'must not silently use the old golden-angle renderer')
  }
  assert.equal(geometry.index!.count, rows.length)
  geometry.dispose()
})
