import * as THREE from 'three';
import { OrbitControls } from 'three/addons/controls/OrbitControls.js';

const $ = id => document.getElementById(id);
const parameterKeys = ['normalMaxAngleDeg', 'maxSearchDistanceMm', 'knnK', 'maxSamples', 'windowScale', 'minArcCoverageDeg'];
const fitReasons = {'prior-axis-supported':'整根拟合 · 局部有扫描支撑','prior-axis-inferred':'整根拟合 · 局部证据稀少 / 遮挡','missing-design-radius':'缺少设计直径','insufficient-axis-evidence':'整根轴向证据不足','inconsistent-design-radius':'扫描与设计直径不一致','ambiguous-axis':'整根轴方向证据不足','too-few-points':'截面点数不足 12','ill-conditioned-circle':'圆拟合条件不良','non-positive-radius':'拟合半径无效','insufficient-inliers':'有效内点不足','radius-out-of-range':'半径超出设计允许范围','excessive-fit-residual':'圆拟合残差过大','insufficient-angular-coverage':'圆弧覆盖不足','center-too-far':'圆心偏移超过 200 mm','uncertain-center':'圆心不确定性过大','unstable-section':'宽窄窗口圆心不稳定','supported':'可靠截面','insufficient-coverage':'截面不可靠'};
const reasonNames = {'bar-not-matched':'实例缺测或待复核','no-topology-unit':'无设计单元','no-supported-observed-points':'无可靠扫描截面','unsupported-design-normal-or-station':'法向或轴向支撑不足',matched:'已匹配', 'no-valid-candidate':'无有效同侧候选', 'no_valid_candidate':'无有效同侧候选',
  'unsupported-section':'无可靠截面支撑', 'unsupported_section':'无可靠截面支撑',
  'invalid-normal':'设计法向无效', 'invalid_normal':'设计法向无效', 'end-cap':'端盖', 'end_cap':'端盖',
  'outside-unit':'超出单元轴向范围', 'outside_unit':'超出单元轴向范围',
  'missing':'缺少扫描实例', 'review':'实例待复核', 'no-scan-points':'单元无扫描点',
  'no_scan_points':'单元无扫描点', 'no-topology':'无设计单元', 'no_topology':'无设计单元'};
const stepNames = {input:['输入与实例归属','核对真实扫描实例与设计单元；保留上游归属，不按邻近关系重新分配。'],
  design:['设计网格与外法向','查看本次分析网格的可信外法向；端盖、零法向与侧面分别检查。'],
  sections:['连续中心线与截面','每个点云簇独立拟合位置、方向与观测范围，仅辅助采用设计直径。实际放偏予以保留；表面匹配仍要求真实扫描点。'],
  normals:['扫描点径向法向','橙色箭头为求解器实际使用的宏观径向；灰点没有可靠截面或半径支撑。'],
  matches:['同侧对应点','点击设计顶点查看真实选点与法向分量。候选受同一单元、轴向窗口、朝向和三维距离限制。'],
  deviation:['偏差与缺测','蓝色向内、橙色向外、灰色缺测；表面法向偏差与中心线位移分别统计。']};
let catalog, result, baseline, selectedStep = 'input', sectionIndex = 0, vertexIndex = 0;
let activeBar = '', loading = false, loadedRun = '', awaitingRun = false;
let awaitingRequestId = '';
let resultSerial = 0, pollTimer, filteredBars = [], sectionDrawing = null;
// Pin the complete initial snapshot. Only extra parameter/history variants use
// a small cache; visiting another bar must never evict a preloaded bar.
const availableRuns = new Map(), resultCache = new Map(), resultLoads = new Map();
const pinnedResults = new Set(), geometryCache = new Map();
let preloading = false, preloadComplete = false, preloadCount = 0;
const runKey = (bar, params) => JSON.stringify([bar, ...parameterKeys.map(key => Number(params[key]))]);
function releaseGeometry(value){
  const cached=geometryCache.get(value);
  if(cached){for(const object of cached.objects)object.geometry.dispose();geometryCache.delete(value);}
}
function cacheResult(url, value) {
  // A forced rerun replaces the pinned result for that bar/parameter pair.
  for(const key of pinnedResults){
    const old=resultCache.get(key);
    if(key!==url&&old&&old.sessionId===value.sessionId&&runKey(old.ifcGlobalId,old.parameters)===runKey(value.ifcGlobalId,value.parameters)){
      pinnedResults.delete(key);pinnedResults.add(url);break;
    }
  }
  resultCache.delete(url);resultCache.set(url,value);
  const extras=[...resultCache.keys()].filter(key=>!pinnedResults.has(key));
  while(extras.length>3){
    const key=extras.shift(),old=resultCache.get(key);
    if(old===result||old===baseline)continue;
    releaseGeometry(old);resultCache.delete(key);
  }
}
async function fetchResult(url) {
  const cached=resultCache.get(url);
  if(cached)return cached;
  if(!resultLoads.has(url)){
    const version=catalog.sessionId;
    const promise=api(url).then(value=>{if(catalog.sessionId===version)cacheResult(url,value);return value;})
      .finally(()=>{if(resultLoads.get(url)===promise)resultLoads.delete(url);});
    resultLoads.set(url,promise);
  }
  return resultLoads.get(url);
}
const pause = ms => new Promise(resolve=>setTimeout(resolve,ms));
function preloadProgress(message,count,total){
  $('preloadMessage').textContent=message;
  $('preloadProgress').max=Math.max(1,total);$('preloadProgress').value=count;
  $('preloadCount').textContent=`${count} / ${total} 根`;
}
async function prepareRun(bar,params){
  const key=runKey(bar.ifcGlobalId,params),saved=availableRuns.get(key);
  if(saved)return saved;
  const deadline=performance.now()+180000;
  let requestId;
  while(performance.now()<deadline){
    if(!requestId){
      try{requestId=(await api('/api/run',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({ifcGlobalId:bar.ifcGlobalId,parameters:params})})).requestId;}
      catch(exc){if(exc.status!==409)throw exc;}
    }
    const state=await api('/api/status');
    if(requestId&&state.requestId===requestId&&state.status==='error')throw new Error(state.error);
    if(requestId&&state.requestId===requestId&&state.status==='complete'){
      availableRuns.set(key,state.latest);return state.latest;
    }
    if(!requestId||state.requestId!==requestId){
      const runs=await refreshHistory(),own=requestId?runs.find(run=>run.requestId===requestId):availableRuns.get(key);
      if(own){availableRuns.set(key,own);return own;}
    }
    await pause(200);
  }
  throw new Error(`准备 ${bar.name||bar.ifcGlobalId} 超时，请重试`);
}
async function preloadAll(){
  if(preloading)return;
  const wanted=activeBar,params=parameterValues();
  preloading=true;preloadComplete=false;preloadCount=0;setBusy(true);error(null);
  $('app').inert=true;$('preloadOverlay').hidden=false;$('preloadRetry').hidden=true;
  try{
    preloadProgress('正在读取全部钢筋目录…',0,catalog.bars.length);
    await refreshHistory();
    // Reuse already loaded runs on retry. Old snapshot geometry is released by
    // refreshHistory; superseded parameter snapshots can be evicted normally.
    pinnedResults.clear();
    const runs=[];
    for(const bar of catalog.bars){
      preloadProgress('正在准备全部钢筋结果…',runs.length,catalog.bars.length);
      const run=await prepareRun(bar,params);runs.push(run);pinnedResults.add(run.resultUrl);
    }
    // A bounded download queue avoids buffering seventy large JSON responses at
    // once. All parsed results remain pinned after this queue completes.
    let cursor=0;
    const downloads=await Promise.allSettled(Array.from({length:2},async()=>{
      while(cursor<runs.length){
        const run=runs[cursor++];await fetchResult(run.resultUrl);
        preloadCount++;preloadProgress('正在加载全部钢筋到浏览器…',preloadCount,runs.length);
        await pause(0);
      }
    }));
    const failed=downloads.find(item=>item.status==='rejected');
    if(failed)throw failed.reason;
    // Build indices, colors and GPU buffers before enabling navigation too.
    for(let i=0;i<runs.length;i++){
      preloadProgress('正在准备三维显示…',i,runs.length);
      await loadRun(runs[i].resultUrl);
      if(!$('error').hidden)throw new Error($('error').textContent);
      await pause(0);
    }
    const chosen=runs.find(run=>run.ifcGlobalId===wanted)||runs[0];
    if(chosen)await loadRun(chosen.resultUrl);
    preloadComplete=true;
    preloadProgress('全部钢筋已准备好',runs.length,runs.length);
    $('preloadSummary').textContent=`已全部加载 ${runs.length} / ${runs.length} 根`;
    $('preloadOverlay').hidden=true;$('app').inert=false;
  }catch(exc){
    preloadProgress(`全部加载未完成：${exc.message}`,preloadCount,catalog.bars.length);
    $('preloadRetry').hidden=false;
  }finally{preloading=false;setBusy(!preloadComplete);}
}
let meshObject, scanObject, pickPoints;
let geometryColorKey = '';
let scanIdsByUnit = new Map(), vertexIdsByUnit = new Map(), facesByUnit = new Map();
function groupIndices(ids) {
  const groups = new Map();
  ids.forEach((id, index) => { if (!groups.has(id)) groups.set(id, []); groups.get(id).push(index); });
  return groups;
}
function scopeIds(groups, count, id = $('unitSelect').value) {
  return id === 'all' ? Array.from({length:count}, (_,i)=>i) : groups.get(id) || [];
}
// These few materials outlive individual bars, retaining their compiled shaders.
const materials = new Map(), unitColors = new Map();
function sharedMaterial(type, options) {
  const key = JSON.stringify([type, options]);
  if (!materials.has(key)) {
    const Material = {mesh:THREE.MeshBasicMaterial, points:THREE.PointsMaterial, line:THREE.LineBasicMaterial, dashed:THREE.LineDashedMaterial}[type];
    materials.set(key, new Material(options));
  }
  return materials.get(key);
}
const baseGroup = new THREE.Group(), overlayGroup = new THREE.Group(), selectionGroup = new THREE.Group();
const scene = new THREE.Scene(); scene.background = new THREE.Color(0x0b1020);
scene.add(baseGroup, overlayGroup, selectionGroup);
const camera = new THREE.PerspectiveCamera(40, 1, .00001, 10000);
let renderer, controls, rebuildingScene = false;
const COLORS = {design:0x65c8ff, scan:0xffb36b, muted:0x687792, rejected:0x9b5f75, match:0x67d8b5, baseline:0xbc97ec};
const fmt = (value, digits=2) => value == null || !Number.isFinite(value) ? '—' : Number(value).toFixed(digits);
const number = value => Number(value || 0).toLocaleString('zh-CN');
const reasonName = reason => reasonNames[reason] || reason || '未提供诊断';
const vec = (array, index=0) => new THREE.Vector3().fromArray(array, index*3);
const triple = v => v.toArray();
const mm = v => `${fmt(v == null ? null : v * 1000)} mm`;
function error(message) { $('error').hidden = !message; $('error').textContent = message || ''; }
async function api(path, options) {
  const response = await fetch(path, options);
  const value = await response.json();
  if (!response.ok) {const exc=new Error(value.error || `请求失败 (${response.status})`);exc.status=response.status;throw exc;}
  return value;
}
function setDL(element, rows) {
  element.replaceChildren();
  for (const [key, value] of rows) {
    const dt = document.createElement('dt'), dd = document.createElement('dd');
    dt.textContent = key; dd.textContent = String(value ?? '—'); element.append(dt, dd);
  }
}
function download(blob, filename) {
  const url = URL.createObjectURL(blob), anchor = document.createElement('a');
  anchor.href = url; anchor.download = filename; anchor.click(); setTimeout(() => URL.revokeObjectURL(url), 1000);
}
function parameterValues() { return Object.fromEntries(parameterKeys.map(key => [key, Number($(key).value)])); }
function restoreParameters(params) {
  for (const key of parameterKeys) $(key).value = params[key] ?? catalog.defaults[key];
  updateDirty();
}
function updateDirty() {
  $('dirty').hidden = !result || parameterKeys.every(key => Number($(key).value) === Number(result.parameters[key]));
}
function setBusy(busy, computing = false) {
  busy=busy||preloading;
  loading = busy; $('run').disabled = busy || !activeBar;
  $('run').textContent = busy ? computing ? '正在运行当前钢筋…' : '正在加载当前钢筋…' : '重新运行当前钢筋';
  for (const id of ['barSelect', 'prevBar', 'nextBar', 'history', 'barSearch', 'defaults', ...parameterKeys]) $(id).disabled = busy;
  updateUnitNavigation();
}
function refreshBars() {
  const query = $('barSearch').value.toLowerCase().trim();
  filteredBars = catalog.bars.filter(bar => `${bar.name} ${bar.ifcGlobalId}`.toLowerCase().includes(query));
  $('barSelect').replaceChildren(...filteredBars.map(bar => {
    const opt = document.createElement('option'); opt.value = bar.ifcGlobalId;
    opt.textContent = `${bar.name || bar.ifcGlobalId} · ${number(bar.pointCount)} 点`; return opt;
  }));
  if (filteredBars.some(bar => bar.ifcGlobalId === activeBar)) $('barSelect').value = activeBar;
  else { activeBar = filteredBars[0]?.ifcGlobalId || ''; $('barSelect').value = activeBar; }
  $('barInfo').textContent = filteredBars.length ? `${filteredBars.length} / ${catalog.bars.length} 根 · ${activeBar}` : '没有符合条件的钢筋';
  $('run').disabled = loading || !activeBar;
}
async function runCurrent() {
  if (loading || !$('parameters').reportValidity() || !activeBar) return;
  error(null); setBusy(true, true); awaitingRun = true;
  $('status').textContent = '运行真实算法：读取钢筋 → 整根拟合中心轴 → 径向法向 → 同侧匹配…';
  try {
    const accepted = await api('/api/run', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({ifcGlobalId:activeBar, parameters:parameterValues()})});
    awaitingRequestId = accepted.requestId || '';
    schedulePoll(100);
  } catch (exc) { awaitingRun = false; setBusy(false); error(exc.message); }
}
async function selectCurrent() {
  if (loading || !$('parameters').reportValidity() || !activeBar) return;
  const params = parameterValues();
  error(null); setBusy(true); $('status').textContent = '正在加载所选钢筋…';
  try {
    // Browsing the preloaded snapshot is entirely local. Refresh explicitly
    // when inputs change; a missing parameter variant still queries history.
    if(!availableRuns.has(runKey(activeBar,params)))await refreshHistory();
    const saved = availableRuns.get(runKey(activeBar, params));
    if (saved) { await loadRun(saved.resultUrl); return; }
    setBusy(false); await runCurrent();
  } catch (exc) { setBusy(false); error(`加载钢筋失败：${exc.message}`); }
}
function schedulePoll(delay=600) { clearTimeout(pollTimer); pollTimer = setTimeout(poll, delay); }
async function poll() {
  try {
    const state = await api('/api/status');
    const ownRequest = !awaitingRequestId || state.requestId === awaitingRequestId;
    if (state.status === 'running' && ownRequest) { setBusy(true, true); schedulePoll(); return; }
    if (state.status === 'error' && ownRequest) { setBusy(false); awaitingRun = false; error(state.error); $('status').textContent = '计算失败，可调整输入或参数后重试。'; return; }
    if (awaitingRun) {
      const runs = await refreshHistory();
      // Another browser can start/finish a job before our next poll. Locate our
      // immutable result instead of replacing this selection with its latest.
      const completed = !awaitingRequestId ? state.latest
        : state.latest?.requestId === awaitingRequestId ? state.latest
        : runs.find(run => run.requestId === awaitingRequestId);
      if (!completed) throw new Error('未找到本次计算结果，请重新运行当前钢筋。');
      awaitingRun = false; awaitingRequestId = ''; await loadRun(completed.resultUrl);
    } else setBusy(false);
  } catch (exc) { setBusy(false); awaitingRun=false; error(`无法读取计算状态：${exc.message}`); }
}
async function refreshHistory() {
  const [runs, currentCatalog] = await Promise.all([api('/api/runs'), api('/api/catalog')]);
  if (catalog.sessionId !== currentCatalog.sessionId) {
    for(const value of geometryCache.keys())releaseGeometry(value);
    availableRuns.clear();pinnedResults.clear();resultCache.clear();resultLoads.clear();baseline=null;catalog=currentCatalog;
    refreshBars();
    $('sourceName').textContent = `${catalog.source.scanName || '真实输入快照'} · ${catalog.bars.length} 根钢筋`;
  }
  $('history').replaceChildren(new Option('选择已保存的调试批次', ''));
  for (const run of runs) {
    if (catalog.sessionId && run.sessionId === catalog.sessionId) {
      const key = runKey(run.ifcGlobalId, run.parameters), previous = availableRuns.get(key);
      if (!previous || run.createdAt > previous.createdAt) availableRuns.set(key, run);
    }
    const opt = new Option(`${run.name} · ${new Date(run.createdAt).toLocaleTimeString()} · ${run.parameters.normalMaxAngleDeg}°`, run.resultUrl);
    $('history').append(opt);
    if (run.runId === loadedRun) $('history').value = run.resultUrl;
  }
  return runs;
}
async function loadRun(url) {
  const serial = ++resultSerial; setBusy(true); error(null);
  try {
    const next = await fetchResult(url); if (serial !== resultSerial) return;
    cacheResult(url, next);
    const previousUnit = result?.ifcGlobalId === next.ifcGlobalId ? $('unitSelect').value : null;
    result = next; loadedRun = next.runId; activeBar = next.ifcGlobalId;
    $('unitSelect').replaceChildren(new Option(`整根概览 · 全部 ${next.units.length} 段`, 'all'), ...next.units.map((unit,i)=>new Option(`段 ${i+1} ↔ ${unit.instanceIds?.length ? '簇 '+unit.instanceIds.join(', ') : '无点云簇'} · ${number(unit.pointCount)} 点`,unit.id)));
    // New multi-segment bars open on a comparable pair. Explicit overview or
    // segment selection survives reloading/rerunning this same bar.
    $('unitSelect').value = previousUnit === 'all' || next.units.some(u=>u.id===previousUnit)
      ? previousUnit : next.units.length>1 ? (next.units.find(u=>u.pointCount>0)||next.units[0]).id : 'all';
    $('barSearch').value = ''; refreshBars(); restoreParameters(next.parameters);
    vertexIndex = Math.max(0, next.distances.findIndex(v => v == null));
    const unstableIndex = next.profile.findIndex(row => row.fitEvidence?.reason === 'unstable-section');
    const priorSupportedIndex = next.profile.findIndex(row => row.quality === 'prior-axis-supported');
    sectionIndex = priorSupportedIndex >= 0 ? priorSupportedIndex : unstableIndex >= 0 ? unstableIndex : Math.max(0, next.profile.findIndex(row => row.observedCenterM));
    $('vertexIndex').max = Math.max(0, next.distances.length - 1); $('vertexIndex').value = vertexIndex;
    refreshSections();
    const reasons = [...new Set(next.reasons)]; $('reasonFilter').replaceChildren(new Option('全部顶点', 'all'));
    for (const reason of reasons) $('reasonFilter').append(new Option(reasonName(reason), reason));
    $('export').disabled = false; $('pinBaseline').disabled = false; $('empty').hidden = true;
    $('history').value = url;
    // Fit the new bar before drawing it. Intermediate renders otherwise use
    // the previous bar's camera and can fill the viewport with oversized points.
    rebuildingScene = true;
    try { buildGeometry(); setStep(selectedStep); updateSummary(); }
    finally { rebuildingScene = false; }
    render();
  } catch (exc) { error(`加载调试结果失败：${exc.message}`); }
  finally { if (serial === resultSerial) setBusy(false); }
}
function clearGroup(group) {
  while (group.children.length) {
    const object = group.children[0]; group.remove(object);
    // Geometry belongs to this scene; materials are shared across scene rebuilds.
    object.traverse(child => { child.geometry?.dispose(); });
  }
}
function buildGeometry() {
  baseGroup.clear();clearGroup(overlayGroup);clearGroup(selectionGroup);
  const cached=geometryCache.get(result);
  if(cached){
    [meshObject,scanObject,pickPoints]=cached.objects;
    ({scanIdsByUnit,vertexIdsByUnit,facesByUnit}=cached);
    geometryColorKey=cached.colorKey;
    unitColors.clear();for(const [id,color] of cached.unitColors)unitColors.set(id,color);
    baseGroup.add(...cached.objects);return;
  }
  geometryColorKey = '';
  scanIdsByUnit = groupIndices(result.scan.unitIds);
  vertexIdsByUnit = groupIndices(result.distances.map((_,i)=>result.mesh.unitIds?.[i]||result.vertexEvidence?.unitIds[i]));
  facesByUnit = new Map();
  if (result.mesh.faceUnitIds) result.mesh.faceUnitIds.forEach((id,face)=>{
    if (!facesByUnit.has(id)) facesByUnit.set(id, []);
    const offset=face*3; facesByUnit.get(id).push(...result.mesh.indices.slice(offset,offset+3));
  });
  unitColors.clear();
  result.units.forEach((unit,index)=>unitColors.set(unit.id,new THREE.Color().setHSL((index*.61803398875+.53)%1,.65,.62)));
  const geometry = new THREE.BufferGeometry();
  geometry.setAttribute('position',new THREE.Float32BufferAttribute(result.mesh.positions,3));
  geometry.setAttribute('normal',new THREE.Float32BufferAttribute(result.mesh.normals,3));
  geometry.setAttribute('color',new THREE.Float32BufferAttribute(new Float32Array(result.mesh.positions.length),3));
  geometry.setIndex(result.mesh.indices); geometry.computeBoundingSphere();
  meshObject = new THREE.Mesh(geometry,sharedMaterial('mesh',{vertexColors:true,side:THREE.DoubleSide,transparent:true,opacity:.65,depthWrite:false}));
  baseGroup.add(meshObject);
  const pg = new THREE.BufferGeometry(); pg.setAttribute('position',new THREE.Float32BufferAttribute(result.scan.positions,3));
  pg.setAttribute('color',new THREE.Float32BufferAttribute(new Float32Array(result.scan.positions.length),3));
  scanObject = new THREE.Points(pg,sharedMaterial('points',{sizeAttenuation:false,vertexColors:true}));
  baseGroup.add(scanObject);
  const pickGeometry = new THREE.BufferGeometry(); pickGeometry.setAttribute('position', geometry.getAttribute('position'));
  // CPU raycasting does not require drawing. Transparent, size-attenuated pick
  // points still rasterize enormous sprites when a short segment is zoomed in.
  pickPoints = new THREE.Points(pickGeometry,sharedMaterial('points',{visible:false}));
  baseGroup.add(pickPoints);
  geometryCache.set(result,{objects:[meshObject,scanObject,pickPoints],scanIdsByUnit,vertexIdsByUnit,facesByUnit,unitColors:new Map(unitColors),colorKey:''});
}
function lineSegments(segments, color, group=overlayGroup, opacity=1, material=sharedMaterial('line',{color,transparent:opacity<1,opacity})) {
  if (!segments.length) return;
  const geometry = new THREE.BufferGeometry(); geometry.setAttribute('position',new THREE.Float32BufferAttribute(segments,3));
  const object = new THREE.LineSegments(geometry,material); group.add(object); return object;
}
function line(a,b,color,group=overlayGroup) {return lineSegments([...a,...b],color,group);}
function arrows(positions,normals,ids,color,length,group=overlayGroup) {
  const segments=[];
  for(const i of ids){
    const start=vec(positions,i), direction=vec(normals,i); if(direction.lengthSq()<1e-12)continue; direction.normalize();
    const end=start.clone().addScaledVector(direction,length), back=end.clone().addScaledVector(direction,-length*.22);
    const side=new THREE.Vector3().crossVectors(direction, Math.abs(direction.y)<.9?new THREE.Vector3(0,1,0):new THREE.Vector3(1,0,0)).normalize().multiplyScalar(length*.075);
    segments.push(...start,...end,...back.clone().add(side),...end,...back.clone().sub(side),...end);
  }
  lineSegments(segments,color,group);
}
function sampleIds(count,limit,predicate=()=>true){
  const ids=[]; for(let i=0;i<count;i++)if(predicate(i))ids.push(i);
  const stride=Math.max(1,Math.ceil(ids.length/limit)); return ids.filter((_,i)=>i%stride===0);
}
function currentSection(){return result?.profile?.[sectionIndex];}
function unitFor(row){return result?.units.find(unit=>unit.id===row?.designUnitId);}
// Read controls once per action, never once per vertex/point. In particular,
// HTMLSelectElement.value searches its options on every access.
let filters={unit:'all',reason:'all',sectionOnly:false};
function readFilters(){filters={unit:$('unitSelect').value,reason:$('reasonFilter').value,sectionOnly:$('sectionOnly').checked};}
function unitVisible(id){return filters.unit==='all'||id===filters.unit;}
function scanVisible(i){return unitVisible(result.scan.unitIds[i])&&(!filters.sectionOnly||inSection(vec(result.scan.positions,i)));}
const unknownUnitColor=new THREE.Color().setHSL((-.61803398875+.53)%1,.65,.62);
function unitColor(id){return unitColors.get(id)||unknownUnitColor;}
function updateUnitNavigation(){
  const units=result?.units||[],index=units.findIndex(u=>u.id===$('unitSelect').value);
  $('unitNavigation').hidden=units.length<2;
  $('unitSelect').disabled=loading||!units.length;
  $('prevUnit').disabled=loading||!units.length||index===0;
  $('nextUnit').disabled=loading||!units.length||index===units.length-1;
  $('allUnits').disabled=loading||index<0;
}
function selectUnit(id){
  if(loading||!result||(id!=='all'&&!result.units.some(u=>u.id===id)))return;
  $('unitSelect').value=id;
  // A segment starts at its full extent, independent of the previous slice.
  $('sectionOnly').checked=false;$('reasonFilter').value='all';
  rebuildingScene=true;
  try{refreshSections();renderLayers();updateSummary();fit('fit');}
  finally{rebuildingScene=false;}
  render();
}
function refreshSections(){
  readFilters();
  updateUnitNavigation();
  const rows=result.profile.map((row,index)=>({row,index})).filter(({row})=>unitVisible(row.designUnitId));
  if(!rows.some(({index})=>index===sectionIndex))sectionIndex=rows[0]?.index||0;
  $('sectionSelect').replaceChildren(...rows.map(({row,index})=>new Option(`段 ${result.units.findIndex(u=>u.id===row.designUnitId)+1} · ${fmt(row.stationM*1000,1)} mm · ${row.observedCenterM?(row.quality==='prior-axis-inferred'?'推断轴':'连续轴'):'轴证据不足'}`,index)));
  if(!rows.length)$('sectionSelect').append(new Option('无可检查截面',''));else $('sectionSelect').value=String(sectionIndex);
  const unit=result.units.find(u=>u.id===$('unitSelect').value);
  const fit=result.measurement.clusterFits?.find(f=>f.designUnitId===unit?.id);
  const scope=unit?`第 ${result.units.indexOf(unit)+1} / ${result.units.length} 段`:'整根概览';
  $('unitInfo').textContent=unit ? `${scope} · 簇 ${unit.instanceIds?.join(', ')||'缺测'} · ${number(unit.pointCount)} 点${fit?` · 设计长 ${mm(fit.designLengthM)} / 观测跨度 ${mm(fit.observedLengthM)} · 拟合残差 ${mm(fit.fitRmseM)}`:''}` : `全部 ${result.mesh.parts?.length||result.units.length} 段按原位置显示。选择设计段或点击下一段，放大逐段对比。`;
  $('status').textContent=`${result.name||result.ifcGlobalId} · ${scope} · ${number(unit?.pointCount??result.stats.pointCount)} 扫描点 · 批次 ${result.runId}`;
}
function inSection(position){
  if(!filters.sectionOnly)return true;
  const row=currentSection(), unit=unitFor(row); if(!unit)return true;
  const axis=row.axisTangent?vec(row.axisTangent):vec(unit.end).sub(vec(unit.start)).normalize(); return Math.abs(position.clone().sub(vec(row.observedCenterM||row.designCenterM)).dot(axis))<=row.windowM+1e-9;
}
function vertexVisible(i){return unitVisible(result.mesh.unitIds?.[i]||result.vertexEvidence?.unitIds[i])&&(filters.reason==='all'||result.reasons[i]===filters.reason)&&(!filters.sectionOnly||inSection(vec(result.mesh.positions,i)));}
function colorGeometry(){
  const unitId=$('unitSelect').value, allUnits=unitId==='all';
  const reason=$('reasonFilter').value, allReasons=reason==='all', sectionOnly=$('sectionOnly').checked;
  const palette=['input','design'].includes(selectedStep)&&result.units.length>1&&allUnits;
  const colorKey=JSON.stringify([selectedStep,palette]);
  // Segment selection changes indices, not the colors of every point in the bar.
  if(geometryColorKey!==colorKey){
    const c=meshObject.geometry.getAttribute('color'), s=scanObject.geometry.getAttribute('color'), temp=new THREE.Color();
    let cap=.001; if(selectedStep==='deviation')for(const value of result.distances)if(value!=null)cap=Math.max(cap,Math.abs(value));
    for(let i=0;i<c.count;i++){
      const value=result.distances[i];
      if(selectedStep==='deviation')temp.set(value==null?COLORS.muted:value>=0?COLORS.scan:COLORS.design).multiplyScalar(value==null?1:.45+.55*Math.min(1,Math.abs(value)/cap));
      else if(selectedStep==='matches')temp.set(value==null?COLORS.muted:COLORS.match);
      else if(palette)temp.copy(unitColor(result.mesh.unitIds?.[i]));
      else temp.set(COLORS.design);
      c.setXYZ(i,temp.r,temp.g,temp.b);
    }
    for(let i=0;i<s.count;i++){
      if(palette)temp.copy(unitColor(result.scan.unitIds[i]));else temp.set(selectedStep==='normals'&&!result.scan.supported[i]?COLORS.muted:COLORS.scan);
      s.setXYZ(i,temp.r,temp.g,temp.b);
    }
    c.needsUpdate=true;s.needsUpdate=true;geometryColorKey=colorKey;geometryCache.get(result).colorKey=colorKey;
  }
  const indices=!allUnits&&result.mesh.faceUnitIds ? facesByUnit.get(unitId)||[] : result.mesh.indices;
  if(allReasons&&!sectionOnly&&(allUnits||result.mesh.faceUnitIds))meshObject.geometry.setIndex(indices);
  else {
    const accepted=i=>(allReasons||result.reasons[i]===reason)&&(!sectionOnly||inSection(vec(result.mesh.positions,i)))
      &&(result.mesh.faceUnitIds||vertexVisible(i));
    const visible=[];
    for(let start=0;start<indices.length;start+=3)if(accepted(indices[start])&&accepted(indices[start+1])&&accepted(indices[start+2]))visible.push(indices[start],indices[start+1],indices[start+2]);
    meshObject.geometry.setIndex(visible);
  }
  const ids=scopeIds(scanIdsByUnit,result.scan.positions.length/3,unitId).filter(i=>!sectionOnly||inSection(vec(result.scan.positions,i)));
  const stride=Math.max(1,Math.ceil(ids.length/200000));
  scanObject.geometry.setIndex(stride===1?ids:ids.filter((_,i)=>i%stride===0));
}
function drawAxes(data, color, includeDesign=true){
  // Three draw calls at most: design, supported scan, and inferred scan.
  // Disjoint line segments preserve the original gaps and per-segment dash phase.
  const design=[], supported=[], inferred=[];
  if(includeDesign&&$('showDesign').checked)for(const unit of data.units)if(unitVisible(unit.id))design.push(...unit.start,...unit.end);
  let previous=null;
  for(const row of data.profile||[]){
    if(!unitVisible(row.designUnitId)){previous=null;continue;}
    if(row.observedCenterM){
      if(previous?.observedCenterM && previous.designUnitId===row.designUnitId){
        const segments=previous.quality==='prior-axis-inferred'||row.quality==='prior-axis-inferred'?inferred:supported;
        segments.push(...previous.observedCenterM,...row.observedCenterM);
      }
      if(row.quality!=='prior-axis-inferred'){
        const [x,y,z]=row.observedCenterM, size=.0006;
        supported.push(x-size,y,z,x+size,y,z,x,y-size,z,x,y+size,z);
      }
    }
    previous=row;
  }
  for(const [segments,tint,dashed] of [[design,COLORS.design,false],[supported,color,false],[inferred,color,true]]){
    if(!segments.length)continue;
    const material=sharedMaterial(dashed?'dashed':'line',dashed
      ? {color:tint,dashSize:.003,gapSize:.002,transparent:true,opacity:.55,depthTest:false}
      : {color:tint,depthTest:false});
    const object=lineSegments(segments,tint,overlayGroup,1,material);object.renderOrder=2;
    if(dashed){
      // LineSegments.computeLineDistances accumulates across segments; the old
      // individual objects restarted each dash at zero. Keep that exact pattern.
      const positions=object.geometry.attributes.position.array,distances=new Float32Array(positions.length/3);
      for(let i=0;i<positions.length;i+=6)distances[i/3+1]=Math.hypot(positions[i+3]-positions[i],positions[i+4]-positions[i+1],positions[i+5]-positions[i+2]);
      object.geometry.setAttribute('lineDistance',new THREE.BufferAttribute(distances,1));
    }
  }
}
function renderLayers(){
  if(!result||!renderer)return;
  readFilters();
  updateLegend();clearGroup(overlayGroup); colorGeometry();
  meshObject.visible=$('showDesign').checked;scanObject.visible=$('showScan').checked;
  meshObject.material.opacity=selectedStep==='deviation'?.95:.5;
  scanObject.material.size=Number($('pointSize').value);
  const limit=Math.max(20,Math.min(20000,Number($('displayLimit').value)||1000)),length=Math.max(.0001,Math.min(.1,Number($('arrowLength').value)/1000||.005));
  let designArrows=0,scanArrows=0,links=0;
  if($('showAxes').checked){
    drawAxes(result,COLORS.scan);
    if(baseline?.ifcGlobalId===result.ifcGlobalId){
      // Baseline coordinates are relative to its own origin; normalize before overlay.
      const offset=vec(baseline.origin).sub(vec(result.origin));
      const profile=baseline.profile.map(row=>({...row,observedCenterM:row.observedCenterM?triple(vec(row.observedCenterM).add(offset)):null}));
      drawAxes({...baseline,profile},COLORS.baseline,false);
    }
  }
  if($('showDesignNormals').checked){const ids=sampleIds(result.distances.length,limit,vertexVisible);arrows(result.mesh.positions,result.mesh.normals,ids,COLORS.design,length);designArrows=ids.length;}
  if($('showScanNormals').checked){const ids=sampleIds(result.scan.supported.length,limit,i=>result.scan.supported[i]&&scanVisible(i));arrows(result.scan.positions,result.scan.normals,ids,COLORS.scan,length);scanArrows=ids.length;}
  if($('showMatches').checked){const segments=[];const ids=sampleIds(result.distances.length,limit,i=>result.matchedScanIndices[i]>=0&&vertexVisible(i));for(const i of ids)segments.push(...vec(result.mesh.positions,i),...vec(result.scan.positions,result.matchedScanIndices[i]));lineSegments(segments,COLORS.match);links=ids.length;}
  $('sampling').textContent=`计算使用本筋全部点。当前显示扫描 ${number(scanObject.geometry.index.count)} / ${number(result.stats.pointCount)} 点；设计箭头 ${number(designArrows)}，扫描箭头 ${number(scanArrows)}，连线 ${number(links)}。显示上限不改变计算。`;
  if(!vertexVisible(vertexIndex))vertexIndex=Math.max(0,result.distances.findIndex((_,i)=>vertexVisible(i)));
  inspect(vertexIndex); drawSection(); render();
}
function setStep(step){
  selectedStep=step;const [title,help]=stepNames[step];$('stageTitle').textContent=title;$('stageHelp').textContent=help;
  for(const button of document.querySelectorAll('[data-step]')){if(button.dataset.step===step)button.setAttribute('aria-current','step');else button.removeAttribute('aria-current');}
  $('showDesign').checked=true;$('showScan').checked=step!=='deviation';$('showAxes').checked=['input','sections','normals'].includes(step);
  $('showDesignNormals').checked=step==='design';$('showScanNormals').checked=step==='normals';$('showMatches').checked=step==='matches';$('sectionOnly').checked=false;
  renderLayers(); if(result)fit('fit');
}
function updateLegend(){
  const step=selectedStep;
  $('legend').textContent=step==='deviation'?'蓝：负偏差 / 向内 · 橙：正偏差 / 向外 · 灰：缺测':step==='matches'?'绿：实际接受的对应 · 灰：缺测 · 粉：选中顶点':'蓝：设计 · 橙：实测 · 灰：无可靠支撑 · 紫：对照中心线';
  if(['input','design'].includes(step)&&result?.units.length>1&&$('unitSelect').value==='all')$('legend').textContent='相同颜色：对应的设计段与点云簇 · 下拉选择可单独查看一段';
  if(step==='sections')$('legend').textContent+=' · 虚线：局部缺点的推断轴';
}
function inspect(index){
  if(!result||!result.distances.length)return;
  readFilters();
  vertexIndex=Math.max(0,Math.min(result.distances.length-1,Math.floor(Number(index)||0)));$('vertexIndex').value=vertexIndex;
  clearGroup(selectionGroup);
  if(!vertexVisible(vertexIndex)){setDL($('inspection'),[['当前段与筛选范围','无可见顶点']]);render();return;}
  const p=vec(result.mesh.positions,vertexIndex),normal=vec(result.mesh.normals,vertexIndex),scanId=result.matchedScanIndices[vertexIndex];
  const rows=[['设计顶点',vertexIndex],['匹配结果',reasonName(result.reasons[vertexIndex])],['表面法向偏差',mm(result.distances[vertexIndex])],['扫描点索引',scanId<0?'—':scanId],['设计法向',normal.toArray().map(n=>fmt(n,3)).join(', ')]];
  arrows(result.mesh.positions,result.mesh.normals,[vertexIndex],0xff8ca5,Number($('arrowLength').value)/1000,selectionGroup);
  const evidence=result.vertexEvidence;
  if(evidence){
    const tn=Array.isArray(evidence.transverseNormals[vertexIndex])?evidence.transverseNormals[vertexIndex]:null;
    rows.push(['所属设计单元',evidence.unitIds[vertexIndex]||'—']);
    if(tn)rows.push(['计算横向法向',tn.map(n=>fmt(n,3)).join(', ')]);
    rows.push(['末批候选 / 各门限独立通过',`${evidence.candidateCount[vertexIndex]} / 朝向 ${evidence.orientationPassCount[vertexIndex]} / 轴向 ${evidence.axialPassCount[vertexIndex]} / 距离 ${evidence.distancePassCount[vertexIndex]}`]);
  }
  if(scanId>=0){
    const q=vec(result.scan.positions,scanId),sn=vec(result.scan.normals,scanId);
    line(triple(p),triple(q),0xffffff,selectionGroup);arrows(result.scan.positions,result.scan.normals,[scanId],COLORS.scan,Number($('arrowLength').value)/1000,selectionGroup);
    rows.push(['三维连线长度',mm(q.distanceTo(p))],['扫描实例',result.scan.instanceIds[scanId]],['设计单元',result.scan.unitIds[scanId]||'单单元归属'],['扫描径向',sn.toArray().map(n=>fmt(n,3)).join(', ')]);
  }
  if(baseline?.ifcGlobalId===result.ifcGlobalId&&baseline.distances.length===result.distances.length)rows.push(['对照批次偏差',mm(baseline.distances[vertexIndex])]);
  rows.push(['模型坐标 m',p.clone().add(vec(result.origin)).toArray().map(n=>fmt(n,5)).join(', ')]);
  setDL($('inspection'),rows);render();
}
function updateSummary(){
  const unit=result.units.find(u=>u.id===$('unitSelect').value);
  const profile=(result.profile||[]).filter(row=>!unit||row.designUnitId===unit.id),supported=profile.filter(p=>p.observedCenterM).length;
  let stats=result.stats;
  if(unit){
    stats={vertexCount:0,knownCount:0,reasonCounts:{}};
    result.distances.forEach((value,i)=>{
      if((result.mesh.unitIds?.[i]||result.vertexEvidence?.unitIds[i])!==unit.id)return;
      stats.vertexCount++;if(value!=null)stats.knownCount++;
      const reason=result.reasons[i];stats.reasonCounts[reason]=(stats.reasonCounts[reason]||0)+1;
    });
  }
  $('summaryTitle').textContent=unit?'第 '+(result.units.indexOf(unit)+1)+' / '+result.units.length+' 段结果':'整根计算结果';
  const measurement=result.measurement||{}, bending=measurement.bending||{}, cross=measurement.crossSection||{};
  const rows=[['有效覆盖',`${number(stats.knownCount)} / ${number(stats.vertexCount)} (${fmt(stats.vertexCount?100*stats.knownCount/stats.vertexCount:0,1)}%)`],[profile.some(p=>p.axisMethod)?'连续轴采样':'旧版可靠截面',`${supported} / ${profile.length}`],['中心线最大偏移',mm(bending.maxCentrelineDepartureM)],['半径',profile.some(p=>p.axisMethod)?'采用设计半径（未测径）':mm(cross.maxAbsRadiusDeltaM)],['残余弓高',mm(bending.residualBowM)],['计算耗时',`${fmt(stats.elapsedSeconds,3)} s`]];
  if(profile.some(p=>p.axisMethod))rows.splice(2,0,['局部有扫描支撑',`${profile.filter(p=>p.quality==='prior-axis-supported').length} / ${profile.length}`]);
  if(unit){
    // Whole-bar bow and elapsed time are not per-segment measurements.
    rows.splice(2);
    if(profile.some(p=>p.axisMethod))rows.push(['局部有扫描支撑',`${profile.filter(p=>p.quality==='prior-axis-supported').length} / ${profile.length}`]);
    const fit=result.measurement.clusterFits?.find(row=>row.designUnitId===unit.id);
    rows.push(['扫描点数',number(unit.pointCount)],['设计长度',mm(fit?.designLengthM)],['观测跨度',mm(fit?.observedLengthM)],['方向夹角',fit?.directionDifferenceDeg==null?'—':fmt(fit.directionDifferenceDeg)+'°'],['轴拟合残差',mm(fit?.fitRmseM)]);
  }
  if(baseline?.ifcGlobalId===result.ifcGlobalId){
    const known=unit?baseline.distances.filter((v,i)=>v!=null&&(baseline.mesh.unitIds?.[i]||baseline.vertexEvidence?.unitIds[i])===unit.id).length:baseline.stats.knownCount;
    rows.push(['对照有效顶点',number(known)],['有效顶点变化',number(stats.knownCount-known)]);
  }
  setDL($('summary'),rows);$('reasons').replaceChildren();
  for(const [reason,count] of Object.entries(stats.reasonCounts||{})){
    const button=document.createElement('button');button.textContent=`${reasonName(reason)} ${number(count)}`;
    button.addEventListener('click',()=>{setStep('matches');$('reasonFilter').value=reason;renderLayers();});$('reasons').append(button);
  }
}
function sectionProjection(){
  const row=currentSection(),unit=unitFor(row);if(!unit)return null;
  const axis=row.axisTangent?vec(row.axisTangent):vec(unit.end).sub(vec(unit.start)).normalize(),origin=vec(row.designCenterM),sliceOrigin=vec(row.observedCenterM||row.designCenterM);
  const u=new THREE.Vector3().crossVectors(axis,Math.abs(axis.z)<.9?new THREE.Vector3(0,0,1):new THREE.Vector3(0,1,0)).normalize();
  const v=new THREE.Vector3().crossVectors(axis,u).normalize();
  const xy=p=>{const d=vec(p).sub(origin);return [d.dot(u)*1000,d.dot(v)*1000];};
  const points=[];
  // Unassigned points retain the legacy section-projection fallback.
  const sectionIds=[...(scanIdsByUnit.get(unit.id)||[]),...Array.from(scanIdsByUnit).filter(([id])=>!id).flatMap(([,ids])=>ids)];
  for(const i of sectionIds){
    const p=vec(result.scan.positions,i);if(Math.abs(p.clone().sub(sliceOrigin).dot(axis))<=row.windowM+1e-9)points.push({xy:xy(triple(p)),supported:result.scan.supported[i]});
  }
  return {row,unit,points,center:row.observedCenterM?xy(row.observedCenterM):null,candidate:row.fitEvidence?.candidateCenterM?xy(row.fitEvidence.candidateCenterM):null};
}
function drawSection(){
  const canvas=$('sectionCanvas'),rect=canvas.getBoundingClientRect(),ratio=window.devicePixelRatio||1;
  if(rect.width<1||rect.height<1)return;
  canvas.width=Math.round(rect.width*ratio);canvas.height=Math.round(rect.height*ratio);
  const ctx=canvas.getContext('2d');ctx.scale(ratio,ratio);const w=rect.width,h=rect.height;ctx.clearRect(0,0,w,h);
  ctx.font='12px "Noto Sans CJK SC",sans-serif';const projection=result?sectionProjection():null;sectionDrawing=projection;
  if(!projection){ctx.fillStyle='#a7b5ce';ctx.fillText('无截面数据；仍可检查输入和设计网格。',12,30);return;}
  const {row,unit,points,center,candidate}=projection, r=(unit.radiusM||.004)*1000,observedRadius=(row.radiusM||0)*1000;
  let xmin=-r*1.4,xmax=r*1.4,ymin=-r*1.4,ymax=r*1.4;
  for(const p of points){xmin=Math.min(xmin,p.xy[0]);xmax=Math.max(xmax,p.xy[0]);ymin=Math.min(ymin,p.xy[1]);ymax=Math.max(ymax,p.xy[1]);}
  if(center){xmin=Math.min(xmin,center[0]-observedRadius);xmax=Math.max(xmax,center[0]+observedRadius);ymin=Math.min(ymin,center[1]-observedRadius);ymax=Math.max(ymax,center[1]+observedRadius);}
  const left=40,top=12,right=w-12,bottom=h-30,scale=.86*Math.min((right-left)/(xmax-xmin||1),(bottom-top)/(ymax-ymin||1));
  const cx=(xmin+xmax)/2,cy=(ymin+ymax)/2;const x=n=>(left+right)/2+(n-cx)*scale,y=n=>(top+bottom)/2-(n-cy)*scale;
  ctx.strokeStyle='#34435f';ctx.lineWidth=1;ctx.strokeRect(left,top,right-left,bottom-top);
  ctx.save();ctx.beginPath();ctx.rect(left,top,right-left,bottom-top);ctx.clip();
  ctx.strokeStyle='#34435f';ctx.beginPath();ctx.moveTo(x(0),top);ctx.lineTo(x(0),bottom);ctx.moveTo(left,y(0));ctx.lineTo(right,y(0));ctx.stroke();
  function circle(c,radius,color){ctx.strokeStyle=color;ctx.lineWidth=1.5;ctx.beginPath();ctx.arc(x(c[0]),y(c[1]),radius*scale,0,Math.PI*2);ctx.stroke();ctx.beginPath();ctx.moveTo(x(c[0])-4,y(c[1]));ctx.lineTo(x(c[0])+4,y(c[1]));ctx.moveTo(x(c[0]),y(c[1])-4);ctx.lineTo(x(c[0]),y(c[1])+4);ctx.stroke();}
  circle([0,0],r,'#65c8ff');if(center)circle(center,observedRadius,'#ffb36b');else if(candidate&&row.fitEvidence.candidateRadiusM){ctx.setLineDash([4,4]);circle(candidate,row.fitEvidence.candidateRadiusM*1000,'#ff8ca5');ctx.setLineDash([]);}
  const stride=Math.max(1,Math.ceil(points.length/20000));for(let i=0;i<points.length;i+=stride){const p=points[i];ctx.fillStyle=p.supported?'#ffb36b':'#8291ac';ctx.fillRect(x(p.xy[0])-1,y(p.xy[1])-1,2,2);}
  ctx.restore();ctx.fillStyle='#a7b5ce';ctx.textAlign='center';for(const value of [xmin,(xmin+xmax)/2,xmax])ctx.fillText(fmt(value,1),x(value),h-13);ctx.textAlign='left';ctx.fillText('u / mm',w-62,h-1);ctx.fillText('v / mm',2,10);ctx.fillText(fmt(ymax,1),2,y(ymax)+4);ctx.fillText(fmt(ymin,1),2,y(ymin)+4);
  $('sectionInfo').textContent=`${row.designUnitId} · 轴向 ${fmt(row.stationM*1000,1)} mm · 窗口 ±${fmt(row.windowM*1000,1)} mm · ${points.length} 点 · ${fitReasons[row.fitEvidence?.reason||row.quality]||row.quality} · ${row.axisMethod ? '整根径向残差' : '圆拟合 RMSE'} ${mm(row.fitRmseM??row.fitEvidence?.fitRmseM)} · 角覆盖 ${fmt(row.arcCoverageDeg??row.fitEvidence?.arcCoverageDeg,1)}° · ${row.axisMethod ? '设计半径' : '半径'} ${mm(row.radiusM)}${!center&&candidate?' · 粉色虚线为被拒绝的拟合圆，不参与匹配':''}`;
}
function fit(mode){
  if(!result||!controls)return;
  if(mode==='section'){$('sectionOnly').checked=true;renderLayers();}
  readFilters();
  const showDesign=$('showDesign').checked,showScan=$('showScan').checked;
  const box=new THREE.Box3();if(showDesign)for(let i=0;i<result.mesh.positions.length/3;i++){if(vertexVisible(i))box.expandByPoint(vec(result.mesh.positions,i));}
  if(showScan)for(const i of scopeIds(scanIdsByUnit,result.scan.positions.length/3)){if(scanVisible(i))box.expandByPoint(vec(result.scan.positions,i));}
  if($('showAxes').checked)for(const row of result.profile)if(unitVisible(row.designUnitId)&&row.observedCenterM&&inSection(vec(row.observedCenterM)))box.expandByPoint(vec(row.observedCenterM));
  if(box.isEmpty()&&currentSection()){box.setFromCenterAndSize(vec(currentSection().designCenterM),new THREE.Vector3(.03,.03,.03));}
  if(box.isEmpty())return;
  const center=box.getCenter(new THREE.Vector3()),size=Math.max(box.getSize(new THREE.Vector3()).length(),.02);
  const unit=result.units.find(u=>u.id===$('unitSelect').value)||result.units[0];let direction=new THREE.Vector3(1,.7,1);
  camera.up.set(0,1,0);
  if(mode==='top'){direction.set(0,1,.0001);camera.up.set(0,0,-1);}else if(mode==='side')direction.set(0,0,1);
  else if(mode==='end'&&unit){direction=vec(unit.end).sub(vec(unit.start)).normalize();if(Math.abs(direction.y)>.9)camera.up.set(0,0,1);}
  const distance=size/(2*Math.tan(THREE.MathUtils.degToRad(camera.fov/2)))*1.15/Math.min(1,camera.aspect);
  controls.target.copy(center);camera.position.copy(center).addScaledVector(direction.normalize(),distance);camera.near=Math.max(size/100000,.000001);camera.far=Math.max(distance*100,10);camera.updateProjectionMatrix();controls.update();render();
}
function render(){if(renderer && !rebuildingScene)renderer.render(scene,camera);}
function resize(){if(!renderer)return;const r=$('view').getBoundingClientRect();if(!r.width||!r.height)return;renderer.setSize(r.width,r.height,false);camera.aspect=r.width/r.height;camera.updateProjectionMatrix();render();drawSection();}
function initViewer(){
  try{renderer=new THREE.WebGLRenderer({antialias:true,preserveDrawingBuffer:true});renderer.setPixelRatio(Math.min(devicePixelRatio,2));$('view').append(renderer.domElement);controls=new OrbitControls(camera,renderer.domElement);controls.enableDamping=false;controls.screenSpacePanning=true;controls.addEventListener('change',render);new ResizeObserver(resize).observe($('view'));new ResizeObserver(drawSection).observe($('sectionCanvas'));
    let down;renderer.domElement.addEventListener('pointerdown',event=>{down=[event.clientX,event.clientY];});
    renderer.domElement.addEventListener('pointerup',event=>{
      if(!result||!down||Math.hypot(event.clientX-down[0],event.clientY-down[1])>4)return;
      const rect=renderer.domElement.getBoundingClientRect(),pointer=new THREE.Vector2((event.clientX-rect.left)/rect.width*2-1,-(event.clientY-rect.top)/rect.height*2+1),ray=new THREE.Raycaster();
      ray.params.Points.threshold=camera.position.distanceTo(controls.target)*Math.tan(THREE.MathUtils.degToRad(camera.fov/2))*10/rect.height;
      ray.setFromCamera(pointer,camera);const hit=ray.intersectObject(pickPoints).find(h=>vertexVisible(h.index));if(hit)inspect(hit.index);
    });resize();
  }catch(exc){error(`三维视图无法初始化：${exc.message}。可继续检查截面和导出诊断 JSON。`);}
}
for(const button of document.querySelectorAll('[data-step]'))button.addEventListener('click',()=>setStep(button.dataset.step));
for(const button of document.querySelectorAll('[data-view]'))button.addEventListener('click',()=>fit(button.dataset.view));
for(const id of ['showDesign','showScan','showAxes','showDesignNormals','showScanNormals','showMatches','sectionOnly','arrowLength','displayLimit','pointSize','reasonFilter'])$(id).addEventListener('input',renderLayers);
$('sectionSelect').addEventListener('change',()=>{sectionIndex=Number($('sectionSelect').value);renderLayers();if($('sectionOnly').checked)fit('fit');});
$('unitSelect').addEventListener('change',()=>selectUnit($('unitSelect').value));
$('allUnits').addEventListener('click',()=>selectUnit('all'));
for(const [id,delta] of [['prevUnit',-1],['nextUnit',1]])$(id).addEventListener('click',()=>{
  if(!result)return;
  const index=result.units.findIndex(u=>u.id===$('unitSelect').value);
  const next=index<0?(delta>0?0:result.units.length-1):index+delta;
  if(result.units[next])selectUnit(result.units[next].id);
});
$('inspectVertex').addEventListener('click',()=>inspect($('vertexIndex').value));$('vertexIndex').addEventListener('change',()=>inspect($('vertexIndex').value));
$('parameters').addEventListener('submit',event=>{event.preventDefault();void runCurrent();});
for(const key of parameterKeys)$(key).addEventListener('input',updateDirty);
$('defaults').addEventListener('click',()=>restoreParameters(catalog.defaults));
$('barSearch').addEventListener('input',refreshBars);
$('barSelect').addEventListener('change',()=>{activeBar=$('barSelect').value;$('barInfo').textContent=activeBar;void selectCurrent();});
for(const [id,delta] of [['prevBar',-1],['nextBar',1]])$(id).addEventListener('click',()=>{const index=filteredBars.findIndex(b=>b.ifcGlobalId===activeBar);const bar=filteredBars[index+delta];if(bar){activeBar=bar.ifcGlobalId;$('barSelect').value=activeBar;void selectCurrent();}});
$('history').addEventListener('change',()=>{if($('history').value)void loadRun($('history').value);});
$('pinBaseline').addEventListener('click',()=>{baseline=result;$('baselineInfo').textContent=`对照：${result.name} · ${result.runId}。紫色为对照实测中心线。`;updateSummary();renderLayers();});
$('export').addEventListener('click',()=>download(new Blob([JSON.stringify(result,null,2)],{type:'application/json'}),`scan-bim-${result.ifcGlobalId}-${result.runId}.json`));
$('png').addEventListener('click',()=>{if(renderer){render();renderer.domElement.toBlob(blob=>{if(blob)download(blob,`scan-bim-${selectedStep}.png`);});}});
const pcUrl=new URL(location.href);pcUrl.port='8766';pcUrl.pathname='/';pcUrl.search='';$('pointcloudLink').href=pcUrl.href;
$('preloadRetry').addEventListener('click',()=>{if(catalog)void preloadAll();else location.reload();});
$('reloadAll').addEventListener('click',()=>void preloadAll());
initViewer();
try{
  catalog=await api('/api/catalog');activeBar=new URLSearchParams(location.search).get('bar')||catalog.bars.find(b=>b.pointCount>0)?.ifcGlobalId||catalog.bars[0]?.ifcGlobalId||'';
  $('sourceName').textContent=`${catalog.source.scanName||'真实输入快照'} · ${catalog.bars.length} 根钢筋`;
  refreshBars();restoreParameters(catalog.defaults);await refreshHistory();
  selectedStep='sections';await preloadAll();
}catch(exc){error(`加载输入失败：${exc.message}`);preloadProgress(`连接失败：${exc.message}`,0,0);$('preloadRetry').hidden=false;$('status').textContent='连接失败，请检查练武场服务。';}
// Public read-only diagnostics for automated geometry-binding checks.
window.scanBimWorkbench={get result(){return result;},get stage(){return selectedStep;},get section(){return sectionDrawing;},get scene(){return scene;},get preload(){return {complete:preloadComplete,loaded:preloadCount,total:catalog?.bars.length||0,cached:pinnedResults.size,geometries:geometryCache.size};}};
