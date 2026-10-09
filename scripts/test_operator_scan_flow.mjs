import assert from 'node:assert/strict'
import fs from 'node:fs'
import vm from 'node:vm'
import ts from 'typescript'
import { parse } from '@vue/compiler-sfc'

// Run the actual controller with a deterministic animation clock. WebGL and
// DOM rendering are exercised separately against the running worker station.
const source = fs.readFileSync(new URL('../src/views/operator/OperatorStation.vue', import.meta.url), 'utf8')
const { descriptor } = parse(source)
const ast = ts.createSourceFile('scan.ts', descriptor.scriptSetup.content, ts.ScriptTarget.Latest, true, ts.ScriptKind.TS)
const names = new Set(['stage', 'scanState', 'scanProgress', 'scanCompletedVersion', 'scanDemoUsed', 'reducedMotion', 'scanning', 'canShowResult', 'scanFrame', 'scanEpoch', 'scanElapsed', 'previousFrame', 'advanceScan', 'motionPreference', 'scanDuration', 'stopScan', 'prepare', 'cancelScan', 'startScan', 'scanVisibilityChanged', 'motionPreferenceChanged', 'geometryLoaded', 'showResult'])
const statements = ast.statements.filter(node => names.has(node.name?.text) || ts.isVariableStatement(node) && node.declarationList.declarations.some(d => names.has(d.name.getText(ast))))
assert.equal(statements.length, names.size)
const ref = value => ({ value })
let now = 0, nextId = 0
const frames = new Map()
const context = { Math, ref, computed: fn => ({ get value() { return fn() } }),
  requestAnimationFrame: callback => { frames.set(++nextId, callback); return nextId },
  cancelAnimationFrame: id => frames.delete(id), performance: { now: () => now }, document: { hidden: false },
  version: ref('v1'), fresh: ref(true), geometryReady: ref(true), loading: ref(false), saving: ref(false), task: ref({scanId: 5}), generation: 1,
  viewer: ref({ resetView() {} }), nextTick: fn => fn(), selectedId: ref(''), issues: ref([{ifcGlobalId:'bar-1'}]),
}
vm.createContext(context)
const exports = `\nglobalThis.controller = {${[...names].join(',')}}`
vm.runInContext(ts.transpileModule(statements.map(node => node.getText(ast)).join('\n') + exports, {compilerOptions:{target:ts.ScriptTarget.ES2022,module:ts.ModuleKind.None}}).outputText, context)
const c = context.controller
function tick(time) { now=time; const pending=[...frames.values()]; frames.clear(); pending.forEach(callback=>callback(now)) }
assert.equal(c.canShowResult.value, false)
c.showResult(); assert.equal(c.stage.value, 'ready')
context.geometryReady.value=false; c.startScan(); assert.equal(c.stage.value,'ready'); assert.equal(frames.size,0)
context.geometryReady.value=true; c.startScan(); assert.equal(c.stage.value,'scan'); assert.equal(frames.size,1)
c.startScan(); assert.equal(frames.size,1,'a repeated click must not start another animation')
tick(2000)
assert.equal(c.scanProgress.value,.25); assert.equal(c.scanDemoUsed.value,true); assert.equal(c.canShowResult.value,false)
c.showResult(); assert.equal(c.stage.value,'scan')
context.document.hidden=true; c.scanVisibilityChanged(); tick(20000)
assert.equal(c.scanProgress.value,.25); assert.equal(frames.size,0)
context.document.hidden=false; c.scanVisibilityChanged(); tick(22000)
assert.equal(c.scanProgress.value,.5,'hidden time must not complete the scan')
tick(26000); assert.equal(c.scanState.value,'complete'); assert.equal(c.canShowResult.value,true)
assert.equal(c.stage.value,'result','completion automatically shows results without a second click'); assert.equal(context.selectedId.value,'bar-1')
c.startScan(); assert.equal(c.stage.value,'result'); assert.equal(frames.size,0,'results cannot silently restart a scan')
c.prepare(); assert.equal(c.stage.value,'ready'); assert.equal(c.canShowResult.value,false)
c.startScan(); tick(28000); c.cancelScan(); tick(40000)
assert.equal(c.stage.value,'ready'); assert.equal(c.scanDemoUsed.value,false); assert.equal(c.scanState.value,'idle'); assert.equal(c.canShowResult.value,false); assert.equal(frames.size,0)
c.startScan(); context.generation++; tick(50000)
assert.equal(c.stage.value,'ready'); assert.equal(c.scanState.value,'idle','task changes must invalidate the animation callback')
c.startScan(); context.version.value='v2'; tick(60000)
assert.equal(c.stage.value,'ready'); assert.equal(c.scanState.value,'idle','a new result version must invalidate the animation callback')
c.startScan(); c.geometryLoaded(false); tick(70000)
assert.equal(c.stage.value,'ready'); assert.equal(c.scanState.value,'idle'); assert.equal(frames.size,0); assert.equal(c.canShowResult.value,false)
context.geometryReady.value=true; c.reducedMotion.value=true; c.startScan(); tick(78000)
assert.equal(c.stage.value,'result','reduced motion retains automatic results')
console.log('PASS: single-click scanning, automatic results, cancellation/restart, hidden-tab pause, task/version invalidation and lost geometry')
