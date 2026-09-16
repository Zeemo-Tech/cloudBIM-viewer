import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'

const viewer = readFileSync(new URL('./pointcloud-debug/viewer.js', import.meta.url), 'utf8')

function functionSource(name) {
  const functionStart = viewer.indexOf(`function ${name}(`)
  assert.ok(functionStart >= 0, `${name} is present`)
  const start = viewer.slice(Math.max(0, functionStart - 6), functionStart) === 'async ' ? functionStart - 6 : functionStart
  const open = viewer.indexOf('{', start)
  let depth = 0
  for (let index = open; index < viewer.length; index++) {
    if (viewer[index] === '{') depth++
    if (viewer[index] === '}' && --depth === 0) return viewer.slice(start, index + 1)
  }
  throw new Error(`could not isolate ${name}`)
}

const loadControlNet = new Function([
  functionSource('controlPreviewUrl'),
  functionSource('controlNetVec3'),
  functionSource('controlNetPolylineLength'),
  functionSource('controlNetCurvedPiecesForUnit'),
  functionSource('controlNetCurveStats'),
  functionSource('validateControlNetCurvedPieces'),
  functionSource('controlNetInputStage'),
  functionSource('loadControlNetPreview'),
  'return loadControlNetPreview',
].join('\n'))()

const inferredCenterlineM = [[10.5, 20, 30], [10.55, 20.05, 30], [10.6, 20.1, 30]]
const inferredLengthM = Math.hypot(.05, .05) * 2

function fixture() {
  return {
    preview: {pointCount: 3, origin: [9, 19, 29], control_statusUrl: 'status', control_instanceUrl: 'instance'},
    controlNet: {
      version: 'design-control-net-v16', mode: 'aligned', inputPolicy: 'post-table-candidates',
      registration: {method: 'saved-coarse-pose'},
      counts: {input: 3, table: 0, matched: 2, pending: 1, removed: 0, designUnits: 2,
        fittedUnits: 2, designBars: 1, extendedShortUnits: 0},
      policy: {ownershipPolicy: 'progressive-unique-support', ownershipStages: [
        {kind: 'web', designUnits: 2, fittedUnits: 2, lockedPoints: 2, elapsedS: .01},
      ]},
      inventory: {units: [
        {designUnitId: 'W-1', designBarId: 'B-WEB', startM: [10, 20, 30], endM: [10.5, 20, 30], direction: [1, 0, 0], lengthM: .5, diameterM: .02, kind: 'web'},
        {designUnitId: 'W-2', designBarId: 'B-WEB', startM: [10.6, 20.1, 30], endM: [11.1, 20.1, 30], direction: [1, 0, 0], lengthM: .5, diameterM: .02, kind: 'web'},
      ]},
      instances: [
        {id: 1, designUnitId: 'W-1', designBarId: 'B-WEB', kind: 'web', status: 'fitted', reason: 'supported', pointCount: 1, diameterM: .02, designLengthM: .5, observedLengthM: .48, rmseM: .001, centerlineM: [[10, 20, 30], [10.5, 20, 30]]},
        {id: 2, designUnitId: 'W-2', designBarId: 'B-WEB', kind: 'web', status: 'fitted', reason: 'supported', pointCount: 1, diameterM: .02, designLengthM: .5, observedLengthM: .47, rmseM: .002, centerlineM: [[10.6, 20.1, 30], [11.1, 20.1, 30]]},
      ],
      curvedPieces: [{
        id: 'C-WEB-JOIN', designBarId: 'B-WEB', kind: 'join', unitIds: [1, 2], status: 'pending',
        reason: 'insufficient-continuous-curved-surface', diameterM: .02, designLengthM: .15,
        designCenterlineM: [[10.5, 20, 30], [10.55, 20.2, 30], [10.6, 20.1, 30]], pointCount: 0,
        geometrySource: 'design-prior', inferredCenterlineM, connectionStatus: 'design-inferred',
        inferredLengthM, inferenceMethod: 'body-anchored-design-join',
      }],
      curveSummary: {designPieces: 1, fittedPieces: 0, pendingPieces: 1, inferredPieces: 1, matchedPoints: 0},
      warnings: [], elapsedS: .1,
    },
  }
}

const buffers = {
  status: Uint8Array.from([1, 1, 2]).buffer,
  instance: Uint32Array.from([1, 2, 0]).buffer,
}

test('v16 loads a body-anchored inferred web join without claiming fit evidence', async () => {
  const manifest = fixture()
  const loaded = await loadControlNet(manifest, async url => buffers[url])
  assert.equal(loaded.instanceById.size, 2)
  assert.equal(manifest.controlNet.curvedPieces[0].centerlineM, undefined)
  assert.equal(manifest.controlNet.curvedPieces[0].pointCount, 0)

  const stats = new Function([
    functionSource('controlNetPolylineLength'),
    functionSource('controlNetCurvedPiecesForUnit'),
    functionSource('controlNetCurveStats'),
    'return controlNetCurveStats',
  ].join('\n'))()(manifest.controlNet)
  assert.deepEqual({fitted: stats.fittedPieces, pending: stats.pendingPieces, inferred: stats.inferredPieces,
    points: stats.matchedPoints, fittedLength: stats.fittedLengthM},
  {fitted: 0, pending: 1, inferred: 1, points: 0, fittedLength: 0})
  assert.ok(Math.abs(stats.inferredLengthM - inferredLengthM) < 1e-12)
})

test('inferred join renders native generated coordinates as a distinct dashed cyan tube', async () => {
  const THREE = await import('three')
  const create = new Function('THREE', [
    functionSource('controlNetIdentityColor'),
    functionSource('createControlNetOverlay'),
    'return createControlNetOverlay',
  ].join('\n'))(THREE)
  const report = fixture().controlNet
  const group = create(report, [9, 19, 29], {selectedId: '2', colorMode: 'parents', showFit: true, showInitialization: true})
  const inferred = group.children.find(child => child.userData.kind === 'curve-design-inferred')
  assert.ok(inferred)
  assert.equal(inferred.userData.connectionStatus, 'design-inferred')
  assert.equal(inferred.userData.inferenceMethod, 'body-anchored-design-join')
  const axis = inferred.children.find(child => child.isLine)
  assert.equal(axis.material.type, 'LineDashedMaterial')
  assert.equal(axis.material.color.getHex(), 0x22d3ee)
  assert.deepEqual([...axis.geometry.attributes.position.array].map(value => Number(value.toFixed(3))),
    [1.5, 1, 1, 1.55, 1.05, 1, 1.6, 1.1, 1])
  const tubes = inferred.children.filter(child => child.isMesh)
  assert.equal(tubes.length, 1)
  assert.equal(tubes[0].isInstancedMesh, true)
  assert.equal(tubes[0].count, 2, 'both inferred segments remain in the batch')
  assert.equal(tubes[0].material.opacity, .1)
  assert.equal(group.children.some(child => child.userData.kind === 'curve-design-pending'), false,
    'fit and initialization toggles do not draw the inferred piece twice')
  const bounds = new THREE.Box3().setFromObject(group)
  assert.ok(bounds.max.x >= 1.6 && bounds.max.y >= 1.1, 'display bounds include the inferred join')

  const designOnly = create(report, [9, 19, 29], {selectedId: '2', colorMode: 'parents', showFit: false, showInitialization: true})
  assert.ok(designOnly.children.some(child => child.userData.kind === 'curve-design-pending'),
    'the historical pending design overlay remains available when fit geometry is hidden')
  for (const model of [group, designOnly]) model.traverse(object => { object.geometry?.dispose?.(); object.material?.dispose?.() })
})

test('local unit focus follows inferred geometry instead of the unanchored design prior', () => {
  const predicate = new Function(`${functionSource('controlNetCurveLocalPredicate')}\nreturn controlNetCurveLocalPredicate`)()
    (fixture().controlNet.curvedPieces, [9, 19, 29])
  const positions = new Float32Array([1.55, 1.05, 1, 1.55, 1.2, 1])
  assert.equal(predicate(positions, 0), true)
  assert.equal(predicate(positions, 1), false)
})

test('v16 rejects malformed inference and any pending-to-fitted evidence confusion', async () => {
  const cases = [
    report => { report.curvedPieces[0].inferredLengthM += .01 },
    report => { delete report.curvedPieces[0].inferenceMethod },
    report => { report.curvedPieces[0].pointCount = 1; report.curveSummary.matchedPoints = 1 },
    report => { report.curvedPieces[0].centerlineM = inferredCenterlineM },
    report => { report.curvedPieces[0].status = 'fitted'; report.curveSummary.fittedPieces = 1; report.curveSummary.pendingPieces = 0 },
    report => { report.instances[1].kind = report.inventory.units[1].kind = 'straight' },
    report => { report.version = 'design-control-net-v15' },
    report => { report.curveSummary.inferredPieces = 0 },
  ]
  for (const mutate of cases) {
    const manifest = fixture()
    mutate(manifest.controlNet)
    await assert.rejects(loadControlNet(manifest, async url => buffers[url]))
  }
})

test('v15 and older pending design curves remain backward compatible', async () => {
  for (const version of [15, 12]) {
    const manifest = fixture()
    const report = manifest.controlNet
    report.version = `design-control-net-v${version}`
    const piece = report.curvedPieces[0]
    for (const key of ['inferredCenterlineM', 'connectionStatus', 'inferredLengthM', 'inferenceMethod']) delete piece[key]
    delete report.curveSummary.inferredPieces
    if (version < 15) { delete report.policy; delete report.counts.extendedShortUnits }
    await loadControlNet(manifest, async url => buffers[url])
  }
})

test('viewer labels inference explicitly and retains fitted overlay bounds', () => {
  assert.match(viewer, /补接弯段（含推断）/)
  assert.match(viewer, /补接，含缺测推断（虚线 \/ 半透明管）/)
  assert.match(viewer, /整段仍不计入确认拟合长度/)
  assert.match(viewer, /new THREE\.Box3\(\)\.setFromObject\(controlNetOverlay\)/)
})

function terminalFixture() {
  const manifest = fixture(); const r = manifest.controlNet;
  r.version = 'design-control-net-v17';
  r.instances[0].kind = 'straight'; r.inventory.units[0].kind = 'straight';
  Object.assign(r.curvedPieces[0], {kind: 'terminal', unitIds: [1],
    inferenceMethod: 'body-anchored-design-terminal',
    inferenceBasis: {anchorSide: 'end', bendRadiusM: .03, bendAngleRad: 2.35,
      tailLengthM: .04, orientation: 'body-tangent-and-design-plane'}});
  r.curveSummary.unresolvedPieces = 0;
  return manifest;
}

test('v17 loads a single-body design-completed hook and retains zero observed support', async () => {
  const manifest = terminalFixture();
  await loadControlNet(manifest, async url => buffers[url]);
  assert.equal(manifest.controlNet.curvedPieces[0].pointCount, 0);
  assert.equal(manifest.controlNet.curvedPieces[0].status, 'pending');
  assert.equal(manifest.controlNet.curveSummary.unresolvedPieces, 0);
});

test('terminal completion rejects wrong version, unsupported body, invalid design frame and false unresolved count', async () => {
  for (const mutate of [
    m => {m.controlNet.version = 'design-control-net-v16'},
    m => {m.controlNet.instances[0].kind = 'web'},
    m => {delete m.controlNet.curvedPieces[0].inferenceBasis},
    m => {m.controlNet.curvedPieces[0].inferenceBasis.bendRadiusM = .001},
    m => {m.controlNet.curvedPieces[0].inferenceBasis.tailLengthM = -1},
    m => {m.controlNet.curveSummary.unresolvedPieces = 1},
  ]) {
    const manifest = terminalFixture(); mutate(manifest);
    await assert.rejects(() => loadControlNet(manifest, async url => buffers[url]));
  }
});

function parametricTerminalFixture() {
  const manifest = terminalFixture(), r = manifest.controlNet, piece = r.curvedPieces[0];
  r.version = 'design-control-net-v18';
  const radius = .03, sweep = 2.35, tail = .04;
  const arc = Array.from({length: 41}, (_, i) => {
    const a = sweep * i / 40; return [10.5 + radius * Math.sin(a), 20 + radius * (1-Math.cos(a)), 30];
  });
  const lead = Array.from({length: 16}, (_, i) => [10.45 + .05*i/16, 20, 30]);
  const end = arc.at(-1).map((v,i) => v + tail * [Math.cos(sweep),Math.sin(sweep),0][i]);
  piece.inferredCenterlineM = [...lead,...arc,end];
  piece.inferredLengthM = piece.inferredCenterlineM.slice(1).reduce((s,p,i) => s+Math.hypot(...p.map((v,j) => v-piece.inferredCenterlineM[i][j])),0);
  piece.inferenceMethod = 'scan-guided-parametric-terminal';
  piece.inferenceValidation = {heldSupport: 60, observedP90ChangeM: .001, coveredBins: 4};
  piece.terminalParameters = {model:'circular-arc-tangent-tail',offsetM:[0,0,0],rollRad:0,pitchRad:0,yawRad:0,
    radiusM:radius,sweepRad:sweep,tailLengthM:tail,arcCenterM:[10.5,20.03,30],arcNormal:[0,0,1],arcTangent:[1,0,0]};
  return manifest;
}

test('v18 loads a scan-guided parametric hook without assigning points to missing geometry', async () => {
  const manifest = parametricTerminalFixture();
  await loadControlNet(manifest, async url => buffers[url]);
  assert.equal(manifest.controlNet.curvedPieces[0].pointCount,0);
  assert.match(viewer, /上翘/);
  assert.match(viewer, /偏转/);
});

test('v18 rejects arbitrary arc deformation, broken tangency and unsupported inference', async () => {
  for (const mutate of [
    p => {p.inferredCenterlineM[30][2] += .001},
    p => {p.inferredCenterlineM.at(-1)[2] += .001},
    p => {p.terminalParameters.arcNormal = [0,0,2]},
    p => {p.terminalParameters.pitchRad = NaN},
    p => {p.inferenceValidation.observedP90ChangeM = .02},
    p => {delete p.terminalParameters},
  ]) {
    const manifest = parametricTerminalFixture();mutate(manifest.controlNet.curvedPieces[0]);
    await assert.rejects(() => loadControlNet(manifest, async url => buffers[url]));
  }
});

function partialTerminalFixture() {
  const manifest = parametricTerminalFixture(), r = manifest.controlNet;
  r.version = 'design-control-net-v20';
  Object.assign(r.counts, {input:30, matched:26, pending:4});
  r.instances[0].pointCount = 25;
  r.curvedPieces[0].localSupport = {method:'independent-continuous-surface-intervals',
    pointCount:24, intervalsM:[[.01,.035],[.06,.08]], validationMaxChangeM:.001};
  r.curveSummary.matchedPoints = 24;
  r.curveSummary.locallyMatchedPoints = 24;
  r.curveSummary.locallySupportedPieces = 1;
  return manifest;
}

test('v20 counts local observations while keeping the full hook inferred', async () => {
  const manifest = partialTerminalFixture();
  await loadControlNet(manifest, async url => buffers[url]);
  assert.equal(manifest.controlNet.curvedPieces[0].pointCount,0);
  assert.equal(manifest.controlNet.curvedPieces[0].status,'pending');
  assert.equal(manifest.controlNet.curveSummary.matchedPoints,24);
});

test('local support rejects invented design evidence, overlapping ranges and unstable poses', async () => {
  for (const mutate of [
    r => { r.version = 'design-control-net-v19' },
    r => { r.curvedPieces[0].inferenceMethod = 'body-anchored-design-terminal' },
    r => { r.curvedPieces[0].localSupport.intervalsM = [[.01,.07],[.06,.08]] },
    r => { r.curvedPieces[0].localSupport.intervalsM = [[.01,9]] },
    r => { r.curvedPieces[0].localSupport.validationMaxChangeM = .1 },
    r => { r.curveSummary.locallyMatchedPoints = 25 },
    r => { r.curvedPieces[0].localSupport.pointCount = 26 },
  ]) {
    const manifest = partialTerminalFixture(); mutate(manifest.controlNet);
    await assert.rejects(() => loadControlNet(manifest, async url => buffers[url]));
  }
});

test('locally confirmed tubes render only the declared physical intervals', async () => {
  const THREE = await import('three');
  const create = new Function('THREE', [functionSource('controlNetIdentityColor'),
    functionSource('createControlNetOverlay'), 'return createControlNetOverlay'].join('\n'))(THREE);
  const report = partialTerminalFixture().controlNet;
  const group = create(report, [0,0,0], {selectedId:'all',colorMode:'units',showFit:true,showInitialization:false});
  const local = group.children.filter(o => o.userData.kind === 'curve-local-supported');
  assert.equal(local.length,2);
  for (let i=0;i<local.length;i++) {
    const values = local[i].children.find(o => o.isLine).geometry.attributes.position.array;
    let length=0;
    for(let j=3;j<values.length;j+=3) length+=Math.hypot(values[j]-values[j-3],values[j+1]-values[j-2],values[j+2]-values[j-1]);
    assert.ok(Math.abs(length-(report.curvedPieces[0].localSupport.intervalsM[i][1]-report.curvedPieces[0].localSupport.intervalsM[i][0]))<1e-5);
  }
  group.traverse(o => {o.geometry?.dispose();o.material?.dispose()});
});

test('v22 accepts locally certified gaps without relaxing older pose certificates', async () => {
  const manifest = partialTerminalFixture(), r = manifest.controlNet;
  r.version = 'design-control-net-v22';
  const local = r.curvedPieces[0].localSupport;
  local.method = 'independent-continuous-surface-intervals-v2';
  local.validationMaxChangeM = r.curvedPieces[0].diameterM*.4;
  await loadControlNet(manifest, async url => buffers[url]);
  r.version = 'design-control-net-v21';
  await assert.rejects(() => loadControlNet(manifest, async url => buffers[url]));
  r.version = 'design-control-net-v22';
  local.validationMaxChangeM = r.curvedPieces[0].diameterM;
  await assert.rejects(() => loadControlNet(manifest, async url => buffers[url]));
});
