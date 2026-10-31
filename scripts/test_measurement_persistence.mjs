import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { runInNewContext } from 'node:vm'
import ts from 'typescript'
import * as Vue from 'vue'
const transpile=source=>ts.transpileModule(source,{compilerOptions:{target:ts.ScriptTarget.ES2022,module:ts.ModuleKind.CommonJS}}).outputText
const payload=(id,contextKey)=>({id,x:1,y:2,z:3,measurement:{contextKey,type:'locate'}})
const records=[{id:11,kind:'locate',payload:payload('own','preview:pointcloud:14')},{id:12,kind:'locate',payload:payload('foreign','alignment:14:13')},{id:13,kind:'locate',payload:{id:'legacy',x:3,y:2,z:1}}]
const deleted=[],created=[];let list=async()=>({data:records})
const api={listMeasurements:()=>list(),deleteMeasurement:async id=>deleted.push(id),createMeasurement:async(...args)=>{created.push(args);return{data:{id:91}}}}
const exports={}
runInNewContext(transpile(readFileSync('packages/viewer-core/src/viewer/useViewerMeasurements.ts','utf8')),{exports,console,require:name=>name==='vue'?Vue:api})
const measurements=exports.useViewerMeasurements({assetId:Vue.ref(14),panelRef:Vue.ref(null),contextKey:()=> 'preview:pointcloud:14'})
await measurements.loadMeasurements()
assert.equal(measurements.analysisPoints.value.length,2);assert.equal(measurements.analysisPoints.value[0].id,'own')
measurements.clearAnalysis();await Promise.resolve()
assert.deepEqual(deleted.sort(),[11,13],'clear must preserve other coordinate workspaces')
let finish;list=()=>new Promise(resolve=>finish=resolve)
const pending=measurements.loadMeasurements();measurements.resetAnalysisState();finish({data:records});await pending
assert.equal(measurements.analysisPoints.value.length,0,'late load cannot repopulate a cancelled workspace')
measurements.handleAnalysisPoint(payload('new','preview:pointcloud:14'));await Promise.resolve();await Promise.resolve()
assert.equal(created[0][0],14);assert.equal(created[0][1],'locate');assert.equal(created[0][2].measurement.contextKey,'preview:pointcloud:14')
console.log('PASS preview persistence: workspace filtering, historical compatibility, scoped clear, stale-load rejection, metadata writes')

const sfc=readFileSync('packages/alignment/src/AlignmentPage.vue','utf8').split('<script setup lang="ts">')[1].split('</script>')[0]
const ast=ts.createSourceFile('AlignmentPage.ts',sfc,ts.ScriptTarget.Latest,true)
const names=['loadStoredMeasurements','clearAllMeasurements']
const functions=ast.statements.filter(n=>ts.isFunctionDeclaration(n)&&names.includes(n.name?.text)).map(n=>n.getText(ast))
assert.equal(functions.length,2)
const ref=value=>({value}), calls=[],deletedAlignment=[]
let resolveList
const state={console,measurementLoadGeneration:0,measurementAssetId:()=>14,measurementContextKey:()=> 'alignment:14:13',
  analysisPoints:ref([]),analysisDistances:ref([]),analysisAreas:ref([]),analysisPoint:ref(null),analysisDistance:ref(null),
  measurementBackendIds:new Map(),measurementAssistant:{reset(){}},
  listMeasurements:async()=>({data:[...records,{id:14,kind:'locate',payload:payload('aligned','alignment:14:13')}] }),
  deleteMeasurement:async id=>deletedAlignment.push(id),
  rebuildAnalysisVisualsFromState:()=>calls.push('geometry'),syncMeasurementBadges:()=>calls.push('badges'),
  ElMessage:{error:message=>assert.fail(message)},
  clearAnalysis:()=>{state.analysisPoints.value=[];state.analysisDistances.value=[];state.analysisAreas.value=[]},
}
const alignment=runInNewContext(`${transpile(functions.join('\n'))}\n({${names.join(',')}})`,state)
await alignment.loadStoredMeasurements()
assert.equal(state.analysisPoints.value.length,2);assert.deepEqual([...state.measurementBackendIds.values()],[12,14])
assert.ok(calls.includes('geometry')&&calls.includes('badges'))
state.listMeasurements=()=>new Promise(resolve=>resolveList=resolve)
const old=alignment.loadStoredMeasurements();await alignment.clearAllMeasurements()
resolveList({data:[{id:14,kind:'locate',payload:payload('aligned','alignment:14:13')}]});await old
assert.equal(state.analysisPoints.value.length,0);assert.deepEqual(deletedAlignment,[12,14])
console.log('PASS alignment persistence: exact workspace restore, geometry refresh, scoped deletion and clear-during-load race')
