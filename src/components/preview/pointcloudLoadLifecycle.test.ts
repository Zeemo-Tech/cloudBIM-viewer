import assert from 'node:assert/strict'
import test from 'node:test'
// @ts-ignore Node's strip-types runner intentionally uses the explicit source extension.
import { createPointcloudLoadLifecycle } from './pointcloudLoadLifecycle.ts'

test('the first renderable tile completes the visible load before refinement settles', () => {
  const lifecycle = createPointcloudLoadLifecycle()

  assert.equal(lifecycle.hasRenderableModel, false)
  assert.equal(lifecycle.allowEdl, false)
  assert.equal(lifecycle.markModelReady(), true)
  assert.equal(lifecycle.hasRenderableModel, true)
  assert.equal(lifecycle.markModelReady(), false)
  assert.equal(lifecycle.allowEdl, false)

  lifecycle.markInitialLoadSettled()
  assert.equal(lifecycle.allowEdl, true)
})

test('each point-cloud load gets independent readiness state', () => {
  const previous = createPointcloudLoadLifecycle()
  previous.markModelReady()
  previous.markInitialLoadSettled()

  const next = createPointcloudLoadLifecycle()
  assert.equal(next.hasRenderableModel, false)
  assert.equal(next.allowEdl, false)
  assert.equal(next.markModelReady(), true)
})
