import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { runInNewContext } from 'node:vm'
import { test } from 'node:test'
import { computed, effectScope, nextTick, ref, watch } from 'vue'
import ts from 'typescript'

// Exercise the page's actual watcher, lookup, denoise completion and POST chain.
const source = readFileSync(new URL('../packages/alignment/src/AlignmentPage.vue', import.meta.url), 'utf8')
  .split('<script setup lang="ts">')[1].split('</script>')[0]
const ast = ts.createSourceFile('page.ts', source, ts.ScriptTarget.Latest, true)
const names = ['ensureAutomaticC2M', 'loadLatestC2M', 'fetchLatestC2M', 'runC2M', 'runDenoise']
const nodes = ast.statements.filter(node => ts.isFunctionDeclaration(node) && names.includes(node.name?.text)
  || node.getText(ast).startsWith('watch([c2mInputKey,'))
assert.equal(nodes.length, names.length + 1)
const code = ts.transpileModule(nodes.map(node => node.getText(ast)).join('\n'), {
  compilerOptions: { target: ts.ScriptTarget.ES2022 },
}).outputText
const noop = () => {}
const result = (fresh = true) => ({ resultVersion: 'saved', fresh, diagnostics: { rebarComparison: { bars: [] } } })
const httpError = status => Object.assign(new Error(`HTTP ${status}`), { response: { status } })
const settle = async () => { for (let i = 0; i < 6; i++) { await nextTick(); await new Promise(setImmediate) } }

function fixture(t, read = async () => { throw httpError(404) }) {
  const calls = { reads: 0, posts: [], denoise: 0 }
  const scope = effectScope()
  const s = {
    watch, Error, comparisonDisposed: false, workflowRouteReady: ref(false), activeWorkflowStep: ref(2),
    props: { pointcloudAssetId: 14, bimAssetId: 13 }, denoiseResult: ref({ version: 'd1', fresh: true }),
    c2mAutomaticAttempts: new Set(), c2mLatestPromise: null, c2mResult: ref(null), c2mRunning: ref(false), c2mError: ref(''),
    c2mResultRequestId: 0, c2mDistancesRequestId: 0, c2mDistances: ref(null),
    c2mSceneLoaded: ref(false), c2mSceneLoading: ref(false), c2mSceneArtifactAvailable: ref(true), c2mTileset: null,
    c2mVoxelSize: ref(.002), c2mNormalConstraintEnabled: ref(true), c2mNormalMaxAngleDeg: ref(30), c2mMaxSearchDistanceMm: ref(200),
    c2mRequestedVisualization: ref({ toleranceLimit: .01 }), rebarDebugActive: ref(false), meshTaskActive: ref(false),
    clearC2MScene: noop, syncC2MControls: noop, syncC2MDistances: noop, scheduleC2MAnalysisPolling: noop,
    clearC2MAnalysisPolling: noop, loadC2MToScene: noop, selectRebarDebugResult: noop,
    isC2MResultFresh: value => value?.fresh === true,
    getLatestC2M: async () => { calls.reads++; return { data: await read() } },
    computeC2M: async request => { calls.posts.push(request); return { data: result() } },
    denoiseRunning: ref(false), denoiseLoading: ref(false), denoiseRequestId: 0, denoiseError: ref(''),
    canOpenDenoiseStep: ref(true), clearDenoisePreview: noop, showDenoisePreview: noop,
    computeDenoise: async () => { calls.denoise++; return { data: { version: 'd2', fresh: true } } },
    invalidateC2MResult: () => { if (s.c2mResult.value) s.c2mResult.value.fresh = false },
    ElMessage: { warning: noop, success: noop, error: noop },
  }
  s.c2mInputKey = computed(() => `14:13:${s.denoiseResult.value.version}:mesh1`)
  s.canUseC2MResult = computed(() => s.c2mResult.value?.fresh === true)
  s.canRunC2M = computed(() => !s.c2mRunning.value && !s.denoiseRunning.value && !s.denoiseLoading.value && s.canOpenDenoiseStep.value)
  const api = scope.run(() => runInNewContext(`${code}\n({ ${names.join(', ')} })`, s))
  t.after(() => { s.comparisonDisposed = true; scope.stop() })
  return { s, api, calls }
}

test('opening a ready denoise step computes an absent result exactly once', async t => {
  const { s, api, calls } = fixture(t)
  s.workflowRouteReady.value = true
  await settle()
  await Promise.all([api.ensureAutomaticC2M(), api.ensureAutomaticC2M()])
  assert.equal(calls.reads, 1); assert.equal(calls.posts.length, 1)
  assert.equal(calls.posts[0].denoiseVersion, 'd1')
  assert.equal(calls.posts[0].normalConstraintEnabled, true)
  assert.equal(calls.posts[0].downsampleEnabled, false)
  assert.equal(calls.posts[0].maxSearchDistance, .2)
})

test('fresh saved results are reused across workflow steps without a POST', async t => {
  const { s, calls } = fixture(t, async () => result())
  s.workflowRouteReady.value = true; await settle()
  s.activeWorkflowStep.value = 3; await settle()
  s.activeWorkflowStep.value = 4; await settle()
  assert.equal(calls.reads, 1); assert.equal(calls.posts.length, 0)
})

test('denoise completion automatically continues with the new version', async t => {
  let stale = false
  const { s, api, calls } = fixture(t, async () => result(!stale))
  s.workflowRouteReady.value = true; await settle()
  assert.equal(calls.posts.length, 0)
  stale = true
  await api.runDenoise(); await settle()
  assert.equal(calls.denoise, 1)
  assert.equal(calls.posts.length, 1)
  assert.equal(calls.posts[0].denoiseVersion, 'd2')
})

test('lookup failure does not trigger a compute or an automatic retry loop', async t => {
  const { s, calls } = fixture(t, async () => { throw httpError(503) })
  s.workflowRouteReady.value = true; await settle()
  s.activeWorkflowStep.value = 3; await settle()
  assert.equal(calls.reads, 1); assert.equal(calls.posts.length, 0)
  assert.match(s.c2mError.value, /503/)
})

test('compute failure stops automatic retries and allows explicit retry', async t => {
  const { s, api, calls } = fixture(t)
  s.computeC2M = async request => { calls.posts.push(request); throw httpError(500) }
  s.workflowRouteReady.value = true; await settle()
  assert.equal(calls.posts.length, 1)
  assert.equal(s.c2mRunning.value, false)
  await api.runC2M(); await settle()
  assert.equal(calls.posts.length, 2)
})

test('a shared latest lookup never invalidates an in-flight compute', async t => {
  let release
  const { s, api, calls } = fixture(t, () => new Promise(resolve => { release = resolve }))
  const first = api.loadLatestC2M(), second = api.loadLatestC2M()
  assert.equal(first, second); assert.equal(calls.reads, 1)
  release(result()); await first
  const generation = s.c2mResultRequestId
  s.c2mRunning.value = true
  assert.equal(await api.loadLatestC2M(), false)
  assert.equal(s.c2mResultRequestId, generation)
})

test('late lookup after leaving or disposing never submits computation', async t => {
  let release
  const { s, calls } = fixture(t, () => new Promise(resolve => { release = resolve }))
  s.workflowRouteReady.value = true; await nextTick()
  s.comparisonDisposed = true
  release(result(false)); await settle()
  assert.equal(calls.posts.length, 0)
})

test('leaving during lookup permits a later attempt when returning', async t => {
  let release
  const { s, calls } = fixture(t, () => new Promise(resolve => { release = resolve }))
  s.workflowRouteReady.value = true; await nextTick()
  s.activeWorkflowStep.value = 1
  release(result(false)); await settle()
  assert.equal(calls.posts.length, 0)
  s.activeWorkflowStep.value = 2; await nextTick()
  release(result(false)); await settle()
  assert.equal(calls.posts.length, 1)
})
