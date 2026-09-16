import assert from 'node:assert/strict'
import fs from 'node:fs'
import vm from 'node:vm'
import ts from 'typescript'
import { parse } from '@vue/compiler-sfc'

// Exercise the actual script-setup bindings without loading a WebGL scene.
const source = fs.readFileSync(new URL('../src/views/alignment/BimPointcloudAlignView.vue', import.meta.url), 'utf8')
const { descriptor } = parse(source)
const content = descriptor.scriptSetup.content
const ast = ts.createSourceFile('settings.ts', content, ts.ScriptTarget.Latest, true, ts.ScriptKind.TS)
const names = new Set(['syncC2MControls', 'sameC2MValue', 'runC2M', 'c2mCalculationSettingsDirty'])
const statements = ast.statements.filter(node => names.has(node.name?.text) ||
  (ts.isVariableStatement(node) && node.declarationList.declarations.some(d => names.has(d.name.getText(ast)))))
assert.equal(statements.length, names.size)
const ref = value => ({ value })
const result = distance => ({ fresh: true, voxelSize: .002, diagnostics: { rebarComparison: { effective: {
  maxSearchDistance: distance, normalConstraintEnabled: true, normalMaxAngleDeg: 10,
} } } })
let sent
const context = { Math, Boolean, Error, computed: fn => ({ get value() { return fn() } }),
  c2mResult: ref(result(.025)), c2mMaxSearchDistanceMm: ref(200), c2mNormalConstraintEnabled: ref(true),
  c2mNormalMaxAngleDeg: ref(30), c2mVoxelSize: ref(.002), c2mRunning: ref(false),
  c2mError: ref(''), c2mResultRequestId: 0, c2mDistancesRequestId: 0, c2mDistances: ref(null),
  canRunC2M: ref(true), props: { pointcloudAssetId: 5, bimAssetId: 7 }, meshTaskActive: ref(false),
  denoiseResult: ref({ version: 'fixture' }), c2mRequestedVisualization: ref({ toleranceLimit: .005 }),
  clearC2MScene() {}, syncC2MDistances() {}, scheduleC2MAnalysisPolling() {}, rebarDebugActive: ref(false),
  isC2MResultFresh: value => value.fresh === true, activeWorkflowStep: ref(3), loadC2MToScene() {},
  ElMessage: { warning() {}, success() {}, error(message) { throw Error(message) } },
  computeC2M: async payload => { sent = payload; return { data: result(payload.maxSearchDistance) } },
}
vm.createContext(context)
const code = statements.map(node => node.getText(ast)).join('\n') + '\nglobalThis.dirty = c2mCalculationSettingsDirty'
vm.runInContext(ts.transpileModule(code, { compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.None } }).outputText, context)
context.syncC2MControls(context.c2mResult.value)
assert.equal(context.c2mMaxSearchDistanceMm.value, 25)
assert.equal(context.c2mNormalMaxAngleDeg.value, 10)
assert.equal(context.dirty.value, false)
context.c2mMaxSearchDistanceMm.value = 20
assert.equal(context.dirty.value, true)
await context.runC2M()
assert.equal(sent.maxSearchDistance, .02)
assert.equal(sent.normalMaxAngleDeg, 10)
assert.equal(sent.normalConstraintEnabled, true)
assert.equal(context.dirty.value, false)
context.syncC2MControls(result(undefined))
assert.equal(context.c2mMaxSearchDistanceMm.value, 200)
context.c2mMaxSearchDistanceMm.value = .1
await context.runC2M()
assert.equal(sent.maxSearchDistance, .0001)
console.log('PASS: saved settings restored, legacy 200 mm fallback, dirty state, and mm→m request mapping')
