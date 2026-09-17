import assert from 'node:assert/strict'
import test from 'node:test'
import * as THREE from 'three'
import { c2mColorCss, c2mDistancePosition, histogramFromC2MDistances, applyC2MVertexColors } from '../packages/viewer-core/src/utils/c2mColormap.ts'
import { summarizeC2MRange, resolveC2MRangeMm } from '../packages/viewer-core/src/utils/c2mRange.ts'

test('engineering boundaries remain green, outliers saturate visibly and unknown stays distinct', () => {
  assert.equal(c2mColorCss(0, .01, .03), '#22c55e')
  for (const discrete of [false, true]) {
    assert.equal(c2mColorCss(-.01, .01, .03, discrete), '#86efac')
    assert.equal(c2mColorCss(.01, .01, .03, discrete), '#86efac')
    for (const bands of [2, 7, 32]) {
      assert.notEqual(c2mColorCss(-.010001, .01, .03, discrete, bands), '#86efac')
      assert.notEqual(c2mColorCss(.010001, .01, .03, discrete, bands), '#86efac')
      assert.equal(c2mColorCss(-100, .01, .03, discrete, bands), '#3b82f6')
      assert.equal(c2mColorCss(100, .01, .03, discrete, bands), '#ff5252')
    }
  }
  assert.equal(c2mColorCss(NaN, .01, .03), '#a8b2c1')
  assert.equal(c2mDistancePosition(-.01, .01, .01), .25)
  assert.equal(c2mDistancePosition(.01, .01, .01), .75)
})

test('histogram partitions finite vertices into bins and directional tails without counting missing coverage', () => {
  const histogram = histogramFromC2MDistances(new Float32Array([-.5, -.125, 0, .125, .5, NaN]), .125, 10)
  assert.equal(histogram.counts.reduce((a, b) => a + b, 0), 3)
  assert.equal(histogram.counts[0], 1)
  assert.equal(histogram.counts.at(-1), 1)
  assert.equal(histogram.underflowCount, 1)
  assert.equal(histogram.positiveOverflowCount, 1)
  assert.equal(histogram.overflowCount, 2)
  assert.equal(histogram.unknownCount, 1)
})

test('P98 considers both signs, excludes missing values, and resists sparse extreme values', () => {
  const distances = new Float32Array([...Array(98).fill(-.02), 4, -5, NaN])
  const summary = summarizeC2MRange(distances)
  assert.ok(summary)
  assert.ok(Math.abs(summary.p98Abs - .02) < 1e-8)
  assert.equal(resolveC2MRangeMm('auto', 30, 10, summary), 20)
  assert.equal(resolveC2MRangeMm('full', 30, 10, summary), 5000)
  assert.equal(resolveC2MRangeMm('manual', 5, 40, summary), 50)
  assert.equal(distances[0], Math.fround(-.02), 'input vertex order is unchanged')
})

test('all-zero and all-unknown results produce a finite display range that contains tolerance', () => {
  for (const data of [new Float32Array(100), new Float32Array([NaN, NaN]), new Float32Array()]) {
    assert.equal(resolveC2MRangeMm('auto', 30, 10, summarizeC2MRange(data)), 12.5)
    assert.ok(resolveC2MRangeMm('auto', 30, .1, summarizeC2MRange(data)) >= 1)
  }
})

test('actual vertex attributes match legend colors in linear space without changing geometry', () => {
  const geometry = new THREE.BufferGeometry()
  geometry.setAttribute('position', new THREE.BufferAttribute(new Float32Array(15), 3))
  const position = geometry.getAttribute('position')
  const distances = new Float32Array([-.5, -.005, 0, .5, NaN])
  for (const discrete of [false, true]) {
    assert.equal(applyC2MVertexColors(geometry, distances, .03, .01, discrete), true)
    const colors = geometry.getAttribute('color')
    distances.forEach((distance, i) => {
      const expected = new THREE.Color(c2mColorCss(distance, .01, .03, discrete))
      const actual = new THREE.Color().fromBufferAttribute(colors, i)
      assert.ok(Math.abs(expected.r - actual.r) < .005)
      assert.equal(actual.getHexString(), expected.getHexString())
    })
    assert.equal(geometry.getAttribute('position'), position)
  }
  assert.equal(applyC2MVertexColors(geometry, new Float32Array(1), .03), false)
})

test('a 2–20% sparse tail cannot squeeze the main distribution back into a few histogram bins', () => {
  for (const tailCount of [21, 40, 100, 200]) {
    const core = Array.from({ length: 1000 - tailCount }, (_, i) => ((i % 101) - 50) / 5000)
    const tails = Array.from({ length: tailCount }, (_, i) => i % 2 ? .19 : -.18)
    const distances = new Float32Array([...core, ...tails, NaN])
    const summary = summarizeC2MRange(distances)
    assert.ok(summary.p98Abs > .17, 'reproduce the previous P98 failure')
    const limitMm = resolveC2MRangeMm('auto', 30, 5, summary)
    assert.ok(limitMm >= 10 && limitMm <= 25, `${tailCount} tails: ${limitMm} mm`)
    const histogram = histogramFromC2MDistances(distances, limitMm / 1000, 60)
    assert.ok(histogram.counts.filter(count => count > 0).length >= 24, 'main data should occupy at least 40% of bins')
    assert.equal(histogram.overflowCount, tailCount)
    assert.equal(histogram.unknownCount, 1)
    assert.equal(resolveC2MRangeMm('full', 30, 5, summary), 200)
  }
})

test('broad distributions and substantial secondary groups stay visible instead of being forced near tolerance', () => {
  const broad = new Float32Array(Array.from({ length: 1000 }, (_, i) => (i - 500) / 2500))
  assert.equal(resolveC2MRangeMm('auto', 30, 5, summarizeC2MRange(broad)), 200)
  const twoGroups = new Float32Array([...Array(600).fill(-.005), ...Array(400).fill(.18)])
  assert.equal(resolveC2MRangeMm('auto', 30, 5, summarizeC2MRange(twoGroups)), 200)
  const shifted = new Float32Array(Array.from({ length: 1000 }, (_, i) => .1 + i / 100000))
  assert.ok(resolveC2MRangeMm('auto', 30, 5, summarizeC2MRange(shifted)) >= 110)
})
