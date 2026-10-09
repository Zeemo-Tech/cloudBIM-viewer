import assert from 'node:assert/strict'
import test from 'node:test'
import { effectScope, nextTick, ref } from 'vue'
// @ts-ignore Node's strip-types runner uses the explicit source extension.
import { useBimRemeshDisplay } from './bimRemeshDisplay.ts'

async function settle() {
  await nextTick()
  await Promise.resolve()
}

function setup(render: (visible: boolean) => Promise<unknown> = async () => {}) {
  const loaded = ref(false)
  const artifact = ref('')
  const calls: boolean[] = []
  const scope = effectScope()
  const display = scope.run(() => useBimRemeshDisplay(loaded, artifact, async (visible) => {
    calls.push(visible)
    return render(visible)
  }))!
  return { loaded, artifact, calls, scope, display }
}

for (const first of ['scene', 'artifact']) {
  test(`automatically displays the generated mesh when ${first} loads first`, async (t) => {
    const state = setup()
    t.after(() => state.scope.stop())
    if (first === 'scene') state.loaded.value = true
    else state.artifact.value = '7:hash-a:time-a'
    await settle()
    assert.equal(state.display.visible.value, false)
    state.loaded.value = true
    state.artifact.value = '7:hash-a:time-a'
    await settle()
    assert.equal(state.display.visible.value, true)
    assert.equal(state.calls.filter(Boolean).length, 1)
  })
}

test('a new artifact reloads, but repeated status polls do not', async (t) => {
  const state = setup()
  t.after(() => state.scope.stop())
  state.loaded.value = true
  state.artifact.value = 'a'
  await settle()
  state.artifact.value = 'a'
  await settle()
  assert.equal(state.calls.filter(Boolean).length, 1)
  state.artifact.value = 'b'
  await settle()
  assert.equal(state.calls.filter(Boolean).length, 2)
  assert.equal(state.display.visible.value, true)
})

test('manual source selection persists across status updates; switching assets resets it', async (t) => {
  const state = setup()
  t.after(() => state.scope.stop())
  state.loaded.value = true
  state.artifact.value = 'a'
  await settle()
  state.display.toggle()
  await settle()
  state.artifact.value = 'b'
  await settle()
  assert.equal(state.display.visible.value, false)
  assert.equal(state.calls.filter(Boolean).length, 1)
  state.loaded.value = false
  state.artifact.value = ''
  state.display.reset()
  await settle()
  state.loaded.value = true
  state.artifact.value = 'next-asset'
  await settle()
  assert.equal(state.display.visible.value, true)
})

test('download failure reports the source honestly and allows explicit retry', async (t) => {
  let fail = true
  const state = setup(async (visible) => {
    if (visible && fail) throw new Error('download failed')
  })
  t.after(() => state.scope.stop())
  state.loaded.value = true
  state.artifact.value = 'a'
  await settle()
  assert.equal(state.display.visible.value, false)
  assert.equal(state.display.error.value, 'download failed')
  assert.equal(state.display.busy.value, false)
  fail = false
  state.display.toggle()
  await settle()
  assert.equal(state.display.visible.value, true)
  assert.equal(state.display.error.value, '')
})

test('an obsolete pending download cannot mark an invalidated mesh visible', async (t) => {
  let complete!: () => void
  const state = setup((visible) => visible ? new Promise<void>((resolve) => { complete = resolve }) : Promise.resolve())
  t.after(() => state.scope.stop())
  state.loaded.value = true
  state.artifact.value = 'a'
  await settle()
  assert.equal(state.display.busy.value, true)
  state.artifact.value = ''
  await settle()
  complete()
  await settle()
  assert.equal(state.calls.at(-1), false)
  assert.equal(state.display.visible.value, false)
  assert.equal(state.display.busy.value, false)
})
