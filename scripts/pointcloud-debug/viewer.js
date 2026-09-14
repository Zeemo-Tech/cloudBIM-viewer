import * as THREE from 'three';
import { OrbitControls } from 'three/addons/controls/OrbitControls.js';
import { TilesRenderer } from '3d-tiles-renderer';
import { buildInstancePalette } from './instancePalette.js';

const $ = (id) => document.getElementById(id);
const api = '/api';
const COMPLETE_TILE_SETTLE_MS = 180;
const runQuery = new URLSearchParams(window.location.search).get('run');
let requestedRun = /^\d{8}T\d{6}-[0-9a-f]{8}$/.test(runQuery || '') ? runQuery : null;
let current = null, rawGeometry = null, normalGeometry = null, tableRemovalGeometry = null, controlNetGeometry = null, partitionGeometry = null, floatingZonesGeometry = null, classGeometry = null, projectionGeometry = null, fusionGeometry = null, refinementGeometry = null, internalRebarGeometry = null, designPriorGeometry = null, cylinderDenoiseGeometry = null, arrowLines = null;
let frameOverlays = [];
let floatingGeometryOverlays = [];
let internalAxisGroup = null;
let designPriorLines = null;
let cylinderFitOverlay = null;
let controlNetOverlay = null;
let poller = null, loadToken = 0, frame = 0, rightScene = 'normal';
let historyRefreshAt = 0;
let canvasWidth = 0, canvasHeight = 0;
let workerLimitInitialized = false;
let compareSource = false, lastView = 'oblique', projectionView = '3d';
let completeTiles = null;
let completeTileRecords = new Map();
let completeTileReadyCount = 0;
let completeTileLoadToken = 0;
let completeTileFailed = false;
let completeTileInteracting = false;
let completeTileSettleTimer = 0;
let completeTilesSuspendedForRun = false;
const completeTileColorCache = new Map();
let spatialInstancePalettes = { internalRebar: new Map(), completeRebar: new Map() };

let renderer;
try {
  renderer = new THREE.WebGLRenderer({ antialias: true });
  renderer.setClearColor(0x0b1020);
  renderer.setPixelRatio(Math.min(devicePixelRatio, 1.5));
  $('view').append(renderer.domElement);
} catch (error) {
  $('empty').hidden = false;
  $('empty').innerHTML = `<div><strong>无法初始化 WebGL</strong>${error.message}</div>`;
  throw error;
}

const rawScene = new THREE.Scene();
const normalScene = new THREE.Scene();
const tableRemovalScene = new THREE.Scene();
const controlNetScene = new THREE.Scene();
const partitionScene = new THREE.Scene();
const floatingZonesScene = new THREE.Scene();
const classScene = new THREE.Scene();
const projectionScene = new THREE.Scene();
const fusionScene = new THREE.Scene();
const refinementScene = new THREE.Scene();
const internalRebarScene = new THREE.Scene();
const completeRebarScene = new THREE.Scene();
const designPriorScene = new THREE.Scene();
const cylinderDenoiseScene = new THREE.Scene();
const completeMaterial = new THREE.PointsMaterial({size: 2, sizeAttenuation: false, vertexColors: true});
const completePoints = new THREE.Points(new THREE.BufferGeometry(), completeMaterial);
let completeGeometry = null, completeAxisLines = null;
completeRebarScene.add(completePoints);
const designPriorPoints = new THREE.Points(new THREE.BufferGeometry(), new THREE.PointsMaterial({size: 2, sizeAttenuation: false, vertexColors: true}));
designPriorScene.add(designPriorPoints);
const cylinderDenoisePoints = new THREE.Points(new THREE.BufferGeometry(), new THREE.PointsMaterial({size: 2, sizeAttenuation: false, vertexColors: true}));
cylinderDenoiseScene.add(cylinderDenoisePoints);
const camera = new THREE.PerspectiveCamera(50, 1, 0.001, 1e7);
camera.up.set(0, 0, 1);
const controls = new OrbitControls(camera, renderer.domElement);
controls.enableDamping = true;
controls.dampingFactor = 0.08;
const rawMaterial = new THREE.PointsMaterial({ size: 2, sizeAttenuation: false, vertexColors: true });
const normalMaterial = new THREE.PointsMaterial({ size: 2, sizeAttenuation: false, vertexColors: true });
const tableRemovalMaterial = new THREE.PointsMaterial({ size: 2, sizeAttenuation: false, vertexColors: true });
const controlNetMaterial = new THREE.PointsMaterial({ size: 2, sizeAttenuation: false, vertexColors: true });
const partitionMaterial = new THREE.PointsMaterial({ size: 2, sizeAttenuation: false, vertexColors: true });
const floatingZonesMaterial = new THREE.PointsMaterial({ size: 2, sizeAttenuation: false, vertexColors: true });
const classMaterial = new THREE.PointsMaterial({ size: 2, sizeAttenuation: false, vertexColors: true });
const projectionMaterial = new THREE.PointsMaterial({ size: 2, sizeAttenuation: false, vertexColors: true });
const fusionMaterial = new THREE.PointsMaterial({ size: 2, sizeAttenuation: false, vertexColors: true });
const refinementMaterial = new THREE.PointsMaterial({ size: 2, sizeAttenuation: false, vertexColors: true });
const internalRebarMaterial = new THREE.PointsMaterial({ size: 2, sizeAttenuation: false, vertexColors: true });
const rawPoints = new THREE.Points(new THREE.BufferGeometry(), rawMaterial);
const normalPoints = new THREE.Points(new THREE.BufferGeometry(), normalMaterial);
const emptyClassGeometry = new THREE.BufferGeometry();
const tableRemovalPoints = new THREE.Points(emptyClassGeometry, tableRemovalMaterial);
const controlNetPoints = new THREE.Points(emptyClassGeometry, controlNetMaterial);
const partitionPoints = new THREE.Points(emptyClassGeometry, partitionMaterial);
const floatingZonesPoints = new THREE.Points(emptyClassGeometry, floatingZonesMaterial);
const classPoints = new THREE.Points(emptyClassGeometry, classMaterial);
const projectionPoints = new THREE.Points(emptyClassGeometry, projectionMaterial);
const fusionPoints = new THREE.Points(emptyClassGeometry, fusionMaterial);
const refinementPoints = new THREE.Points(emptyClassGeometry, refinementMaterial);
const internalRebarPoints = new THREE.Points(emptyClassGeometry, internalRebarMaterial);
rawScene.add(rawPoints);
normalScene.add(normalPoints);
tableRemovalScene.add(tableRemovalPoints);
controlNetScene.add(controlNetPoints);
partitionScene.add(partitionPoints);
floatingZonesScene.add(floatingZonesPoints);
classScene.add(classPoints);
projectionScene.add(projectionPoints);
fusionScene.add(fusionPoints);
refinementScene.add(refinementPoints);
internalRebarScene.add(internalRebarPoints);

function requestRender() {
  if (!frame) frame = requestAnimationFrame(render);
}

function resizeRenderer() {
  const width = $('view').clientWidth;
  const height = $('view').clientHeight;
  if (!width || !height) return false;
  if (width !== canvasWidth || height !== canvasHeight) {
    canvasWidth = width;
    canvasHeight = height;
    renderer.setSize(width, height, false);
  }
  camera.aspect = (compareSource ? width / 2 : width) / height;
  camera.updateProjectionMatrix();
  return true;
}

function render() {
  frame = 0;
  if (rightScene === 'projection' && projectionView !== '3d') return;
  if (!resizeRenderer()) return;
  const half = compareSource ? Math.floor(canvasWidth / 2) : 0;
  if (completeTiles && rightScene === 'completeRebar') {
    completeTiles.setResolution(camera, Math.max(1, canvasWidth - half), Math.max(1, canvasHeight));
    completeTiles.update();
  }
  renderer.setScissorTest(true);
  if (compareSource) {
    renderer.setViewport(0, 0, half, canvasHeight);
    renderer.setScissor(0, 0, half, canvasHeight);
    renderer.render(rightScene === 'controlNet'
      ? (controlNetInputStage(current?.controlNet) === 'post-fusion' ? fusionScene : tableRemovalScene)
      : rawScene, camera);
  }
  renderer.setViewport(half, 0, canvasWidth - half, canvasHeight);
  renderer.setScissor(half, 0, canvasWidth - half, canvasHeight);
  const scene = rightScene === 'normal' ? normalScene
    : rightScene === 'tableRemoval' ? tableRemovalScene
      : rightScene === 'controlNet' ? controlNetScene
      : rightScene === 'partition' ? partitionScene
    : rightScene === 'floatingZones' ? floatingZonesScene
    : rightScene === 'classification' ? classScene
      : rightScene === 'projection' ? projectionScene
        : rightScene === 'fusion' ? fusionScene
          : rightScene === 'refinement' ? refinementScene
            : rightScene === 'internalRebar' ? internalRebarScene : rightScene === 'completeRebar' ? completeRebarScene : rightScene === 'cylinderDenoise' ? cylinderDenoiseScene : rightScene === 'designPrior' ? designPriorScene : rawScene;
  renderer.render(scene, camera);
  renderer.setScissorTest(false);
  if (controls.update()) requestRender();
}

controls.addEventListener('change', requestRender);
controls.addEventListener('start', beginCompleteTileInteraction);
controls.addEventListener('end', endCompleteTileInteraction);
new ResizeObserver(requestRender).observe($('view'));

function fmtHeight(metres) {
  return Number.isFinite(metres) ? `${(metres * 1000).toFixed(1)} mm` : '—';
}

function fmt(value, unit = '') {
  return Number.isFinite(value)
    ? `${value.toLocaleString('zh-CN', { maximumFractionDigits: 2 })}${unit}`
    : '—';
}

function setStatus(text, error = false) {
  $('status').textContent = text;
  $('status').classList.toggle('error', error);
}

async function getJSON(url, options) {
  const response = await fetch(url, options);
  if (!response.ok) throw new Error((await response.text()) || `HTTP ${response.status}`);
  return response.json();
}

async function fetchBytes(url) {
  const response = await fetch(url);
  if (!response.ok) throw new Error(`预览数据读取失败：HTTP ${response.status}`);
  return response.arrayBuffer();
}

function completeTilesContract(manifest) {
  const tiles = manifest?.tiles;
  const attributes = tiles?.completeRebar;
  if (tiles?.schema !== 'pointcloud-tiles-v1'
    || tiles.mode !== 'source-3d-tiles-sidecar'
    || tiles.coordinateFrame !== 'native LAS XYZ; source-coordinate PNTS; no RTC_CENTER'
    || typeof tiles.tilesetUrl !== 'string'
    || typeof attributes?.attributeUrlTemplate !== 'string'
    || attributes.schema !== 'pointcloud-tile-attributes-v1'
    || attributes.format?.magic !== 'PCTA'
    || attributes.format?.version !== 1
    || attributes.format?.headerBytes !== 32
    || attributes.format?.recordBytes !== 9) return null;
  const expected = [['complete_class', 'uint8'], ['complete_instance', 'uint32'], ['complete_cluster', 'uint32']];
  if (!Array.isArray(attributes.format.properties)
    || expected.some(([name, type], index) => {
      const property = attributes.format.properties[index];
      return property?.name !== name || property?.type !== type || property?.offset !== [0, 1, 5][index];
    })) return null;
  return {tilesetUrl: tiles.tilesetUrl, attributeUrlTemplate: attributes.attributeUrlTemplate};
}

function tileAttributeUrl(tilesetUrl, template, contentUrl) {
  const tileset = new URL(tilesetUrl, window.location.href);
  const base = new URL('.', tileset);
  const content = new URL(contentUrl, base);
  if (content.origin !== base.origin || !content.pathname.startsWith(base.pathname)) {
    throw new Error('瓦片内容地址不属于当前 tileset');
  }
  const encodedRelative = content.pathname.slice(base.pathname.length);
  const segments = encodedRelative.split('/').map(segment => decodeURIComponent(segment));
  if (!segments.length || segments.some(segment => !segment || segment === '.' || segment === '..')
    || !segments.at(-1).toLowerCase().endsWith('.pnts')) throw new Error('瓦片内容路径无效');
  const safePath = segments.map(segment => encodeURIComponent(segment)).join('/');
  return template.replace('{tilePath}', safePath);
}

function parseCompleteTileAttributes(buffer) {
  if (!(buffer instanceof ArrayBuffer) || buffer.byteLength < 32) throw new Error('瓦片属性文件过短');
  const bytes = new Uint8Array(buffer);
  const view = new DataView(buffer);
  const magic = String.fromCharCode(...bytes.subarray(0, 4));
  const version = view.getUint16(4, true);
  const headerBytes = view.getUint16(6, true);
  const pointCount = view.getUint32(8, true);
  const recordBytes = view.getUint16(12, true);
  const propertyCount = view.getUint16(14, true);
  const flags = view.getUint32(16, true);
  if (magic !== 'PCTA' || version !== 1 || headerBytes !== 32 || recordBytes !== 9
    || propertyCount !== 3 || flags !== 0 || bytes.subarray(20, 32).some(value => value !== 0)
    || buffer.byteLength !== headerBytes + pointCount * recordBytes) {
    throw new Error('瓦片属性文件头或长度无效');
  }
  const classes = new Uint8Array(pointCount);
  const instances = new Uint32Array(pointCount);
  const clusters = new Uint32Array(pointCount);
  for (let index = 0, offset = headerBytes; index < pointCount; index += 1, offset += recordBytes) {
    classes[index] = view.getUint8(offset);
    instances[index] = view.getUint32(offset + 1, true);
    clusters[index] = view.getUint32(offset + 5, true);
    if (classes[index] < 1 || classes[index] > 4 || (classes[index] !== 3 && instances[index] !== 0)) {
      throw new Error('瓦片属性包含无效类别或实例编号');
    }
  }
  return {pointCount, classes, instances, clusters};
}

function completeOperationSets() {
  const operations = current?.completeRebar?.designReview?.operations || [];
  const components = current?.completeRebar?.designReview?.components || [];
  const separatedClusters = new Set(operations.filter(o => o.action === 'separate').map(o => o.clusterId));
  const mergedIds = new Set(operations.filter(o => o.action === 'merge').flatMap(o => o.sourceInstanceIds));
  const bridgeIds = new Set(operations.filter(o => o.action === 'merge' && o.acrossFixture).flatMap(o => o.sourceInstanceIds));
  for (const operation of operations) {
    if (operation.action === 'attach' && operation.phase === 'before_filter') {
      operation.instanceIds?.forEach(id => bridgeIds.add(id));
    }
  }
  for (const component of components) {
    if (mergedIds.has(component.originalInstanceId) || mergedIds.has(component.instanceId)) mergedIds.add(component.instanceId);
    if (bridgeIds.has(component.originalInstanceId) || bridgeIds.has(component.instanceId)) bridgeIds.add(component.instanceId);
  }
  const splitIds = new Set(operations.filter(o => o.action === 'split').flatMap(o => o.instanceIds));
  for (const operation of operations) {
    if (operation.action === 'merge' && operation.sourceInstanceIds.some(id => splitIds.has(id))) {
      operation.sourceInstanceIds.forEach(id => splitIds.add(id));
    }
  }
  const hookClusters = new Set((current?.completeRebar?.clusters || []).filter(c => c.category === 'curved-exterior').map(c => c.id));
  const finalRejectedClusters = new Set((current?.completeRebar?.designReview?.finalClusterFilter?.decisions || []).map(c => c.clusterId));
  return {separatedClusters, mergedIds, bridgeIds, splitIds, hookClusters, finalRejectedClusters};
}

function completeTilesSupported() {
  if (semanticVisibilityCustomized) return false;
  if ($('resultColorMode')?.value === 'layers') return false;
  if ($('completeColorMode')?.value === 'score') return false;
  if (!completeTiles || completeTileFailed || $('completeCompare').value === 'baseline') return false;
  const filter = $('completeClassFilter').value;
  if (['all', 'resolved', '3'].includes(filter)
    && !new Set(['before', 'all', 'steel', 'fixture', 'table', 'noise', 'pending', 'unknown']).has(preferredSemanticTag)) return false;
  return new Set(['all', 'resolved', '1', '2', '3', '4', 'pending', 'merged', 'bridged', 'split', 'separated', 'hooks', 'final-rejected']).has(filter);
}

function completeTileSemanticLabel(cls, instance) {
  if (cls === 1) return 1;
  if (cls === 2) return 2;
  if (cls === 3) return instance ? 3 : 9;
  if (cls === 4) return 10;
  return 0;
}

function completeTilesHideHardNoise(cls, filter) {
  if (cls !== 4 || current?.preprocessing?.floatingZones?.forbiddenRule?.scope !== 'all-source-points') return false;
  return !(['4', 'filtered'].includes(filter) || ['before', 'noise'].includes(preferredSemanticTag)
    || (semanticVisibilityCustomized && visibleSemanticCodes.has(10)));
}

function completeTileStyleTargets(records, targetRecord) {
  return targetRecord ? [targetRecord] : records.values();
}

function completeTileErrorTarget(interacting) {
  return interacting ? 64 : 24;
}

function applyCompleteTileQuality(interacting) {
  if (!completeTiles) return;
  completeTiles.errorTarget = completeTileErrorTarget(interacting);
  completeTiles.downloadQueue.maxJobs = interacting ? 2 : 8;
  completeTiles.parseQueue.maxJobs = interacting ? 1 : 2;
}

function beginCompleteTileInteraction() {
  if (completeTileSettleTimer) {
    clearTimeout(completeTileSettleTimer);
    completeTileSettleTimer = 0;
  }
  completeTileInteracting = true;
  applyCompleteTileQuality(true);
  requestRender();
}

function endCompleteTileInteraction() {
  if (completeTileSettleTimer) clearTimeout(completeTileSettleTimer);
  completeTileSettleTimer = setTimeout(() => {
    completeTileSettleTimer = 0;
    completeTileInteracting = false;
    applyCompleteTileQuality(false);
    requestRender();
  }, COMPLETE_TILE_SETTLE_MS);
}

function completeTileColorBytes(id, cluster = false) {
  const key = cluster ? -id : id;
  if (!completeTileColorCache.has(key)) {
    const color = cluster ? instanceColor(id) : rebarInstanceColor(id, 'completeRebar');
    completeTileColorCache.set(key, color.map(value => Math.round(value * 255)));
  }
  return completeTileColorCache.get(key);
}

function refreshCompleteTilePresentation(supported) {
  let loadedPoints = 0;
  for (const record of completeTileRecords.values()) {
    for (const part of record.parts) {
      part.object.visible = supported && record.ready;
      if (record.ready) loadedPoints += part.selectedCount || 0;
    }
  }
  const useTiles = supported && completeTileReadyCount > 0;
  if (completeTiles) completeTiles.group.visible = supported;
  completePoints.visible = !useTiles;
  if (useTiles) {
    $('sample').textContent = `源 Tiles LOD ${fmt(current.tiles.completeRebar.pointCount)} 点 · 当前已载入 ${fmt(loadedPoints)} 点 · 分步样本 ${fmt(current.preview.pointCount)}`;
  } else if (current?.preview) {
    $('sample').textContent = `样本 ${fmt(current.preview.pointCount)} / 全量 ${fmt(current.preview.totalPointCount)}`;
  }
  return useTiles;
}

function applyCompleteTileAppearance(targetRecord = null) {
  const supported = completeTilesSupported();
  if (!supported) return refreshCompleteTilePresentation(false);
  const filter = $('completeClassFilter').value;
  const instanceFilter = $('completeInstanceFilter').value;
  const colorMode = $('completeColorMode').value;
  const {separatedClusters, mergedIds, bridgeIds, splitIds, hookClusters, finalRejectedClusters} = completeOperationSets();
  const filterKey = `${filter}/${instanceFilter}/${preferredSemanticTag}`;
  const classColors = {1:[0x64, 0x74, 0x8b], 2:[0xf5, 0x9e, 0x0b], 3:[0x2d, 0xd4, 0xbf], 4:[0xef, 0x47, 0x6f]};
  for (const record of completeTileStyleTargets(completeTileRecords, targetRecord)) {
    if (!record.ready) continue;
    for (const part of record.parts) {
      const {object, classes, instances, clusters} = part;
      const count = classes.length;
      const updateColors = part.colorMode !== colorMode;
      const updateFilter = part.filterKey !== filterKey;
      if (!part.colors || part.colors.length !== count * 3) {
        part.colors = new Uint8Array(count * 3);
        object.geometry.setAttribute('color', new THREE.BufferAttribute(part.colors, 3, true));
      }
      if (!part.indices || part.indices.length !== count) part.indices = new Uint32Array(count);
      if (!updateColors && !updateFilter) continue;
      let selected = 0;
      for (let index = 0; index < count; index += 1) {
        const cls = classes[index], instance = instances[index], cluster = clusters[index];
        if (updateColors) {
          const color = colorMode === 'clusters' && cluster ? completeTileColorBytes(cluster, true)
            : cls === 3 && !instance ? [0x94, 0xa3, 0xb8]
              : cls === 3 && colorMode === 'instances' ? completeTileColorBytes(instance)
                : classColors[cls] || [0x94, 0xa3, 0xb8];
          const colorOffset = index * 3;
          part.colors[colorOffset] = color[0];
          part.colors[colorOffset + 1] = color[1];
          part.colors[colorOffset + 2] = color[2];
        }
        if (!updateFilter) continue;
        const classMatch = filter === 'resolved' ? cls === 3 && instance > 0
          : filter === 'pending' ? cls === 3 && instance === 0
            : filter === 'merged' ? cls === 3 && instance > 0 && mergedIds.has(instance)
              : filter === 'bridged' ? cls === 3 && instance > 0 && bridgeIds.has(instance)
                : filter === 'split' ? cls === 3 && instance > 0 && splitIds.has(instance)
                  : filter === 'separated' ? separatedClusters.has(cluster)
                    : filter === 'hooks' ? hookClusters.has(cluster)
                      : filter === 'final-rejected' ? finalRejectedClusters.has(cluster)
                    : filter === 'all' || cls === Number(filter);
        const semanticMatch = !['all', 'resolved', '3'].includes(filter)
          || semanticTagMatches(completeTileSemanticLabel(cls, instance), preferredSemanticTag);
        if (!completeTilesHideHardNoise(cls, filter) && classMatch && semanticMatch && (instanceFilter === 'all' || instance === Number(instanceFilter))) {
          part.indices[selected++] = index;
        }
      }
      if (updateColors) {
        part.colorMode = colorMode;
        object.geometry.attributes.color.needsUpdate = true;
      }
      if (updateFilter) {
        part.filterKey = filterKey;
        part.selectedCount = selected;
        object.geometry.setIndex(new THREE.BufferAttribute(part.indices.subarray(0, selected), 1));
        object.geometry.setDrawRange(0, selected);
      }
      if (!part.materialReady) {
        const materials = Array.isArray(object.material) ? object.material : [object.material];
        for (const material of materials) {
          material.vertexColors = true;
          material.sizeAttenuation = false;
          material.size = Number($('size').value);
          material.needsUpdate = true;
        }
        part.materialReady = true;
      }
    }
  }
  return refreshCompleteTilePresentation(true);
}

function attachCompleteTileAttributes(scene, attributes) {
  const points = [];
  scene.traverse(object => {
    if (object.isPoints && object.geometry?.attributes?.position) points.push(object);
  });
  const actualCount = points.reduce((sum, object) => sum + object.geometry.attributes.position.count, 0);
  if (!points.length || actualCount !== attributes.pointCount) {
    throw new Error(`瓦片点数与属性不一致：${actualCount} / ${attributes.pointCount}`);
  }
  const parts = [];
  let offset = 0;
  for (const object of points) {
    const count = object.geometry.attributes.position.count;
    parts.push({
      object,
      classes: attributes.classes.subarray(offset, offset + count),
      instances: attributes.instances.subarray(offset, offset + count),
      clusters: attributes.clusters.subarray(offset, offset + count),
      colors: null,
      indices: null,
    });
    object.visible = false;
    offset += count;
  }
  return parts;
}

function disposeCompleteTiles() {
  completeTileLoadToken += 1;
  if (completeTileSettleTimer) {
    clearTimeout(completeTileSettleTimer);
    completeTileSettleTimer = 0;
  }
  if (completeTiles) {
    completeRebarScene.remove(completeTiles.group);
    completeTiles.deleteCamera(camera);
    completeTiles.dispose();
  }
  completeTiles = null;
  completeTileRecords.clear();
  completeTileReadyCount = 0;
  completeTileFailed = false;
  completeTileInteracting = false;
  completeTileColorCache.clear();
  completePoints.visible = true;
}

function suspendCompleteTilesForRun() {
  if (completeTilesSuspendedForRun) return;
  completeTilesSuspendedForRun = true;
  disposeCompleteTiles();
  requestRender();
}

function installCompleteTiles() {
  const contract = completeTilesContract(current);
  if (!contract || !current?._complete) return;
  const token = ++completeTileLoadToken;
  const tiles = new TilesRenderer(contract.tilesetUrl);
  completeTiles = tiles;
  completeTileFailed = false;
  tiles.displayActiveTiles = true;
  applyCompleteTileQuality(completeTileInteracting);
  tiles.setCamera(camera);
  const origin = current.preview.origin || [0, 0, 0];
  tiles.group.position.set(-origin[0], -origin[1], -origin[2]);
  completeRebarScene.add(tiles.group);
  const update = () => requestRender();
  for (const eventName of ['needs-update', 'load-root-tileset', 'tiles-load-start', 'tiles-load-end', 'tile-visibility-change']) {
    tiles.addEventListener(eventName, update);
  }
  tiles.addEventListener('load-error', event => {
    console.warn('高密度瓦片加载失败，保留样本预览：', event.url, event.error);
    completeTileFailed = true;
    applyCompleteTileAppearance();
    requestRender();
  });
  tiles.addEventListener('dispose-model', event => {
    const record = completeTileRecords.get(event.scene);
    if (record?.ready) completeTileReadyCount -= 1;
    completeTileRecords.delete(event.scene);
    applyCompleteTileAppearance();
    requestRender();
  });
  tiles.addEventListener('load-model', async event => {
    let record;
    try {
      const url = tileAttributeUrl(contract.tilesetUrl, contract.attributeUrlTemplate, event.url);
      const pendingParts = [];
      event.scene.traverse(object => {
        if (object.isPoints && object.geometry?.attributes?.position) {
          object.visible = false;
          pendingParts.push({object});
        }
      });
      if (!pendingParts.length) throw new Error('PNTS 瓦片没有可显示的点对象');
      record = {parts: pendingParts, ready: false};
      completeTileRecords.set(event.scene, record);
      const attributes = parseCompleteTileAttributes(await fetchBytes(url));
      if (token !== completeTileLoadToken || completeTiles !== tiles || completeTileRecords.get(event.scene) !== record) return;
      record.parts = attachCompleteTileAttributes(event.scene, attributes);
      record.ready = true;
      completeTileReadyCount += 1;
      applyCompleteTileAppearance(record);
      requestRender();
    } catch (error) {
      if (record) completeTileRecords.delete(event.scene);
      console.warn('瓦片属性加载失败，继续使用抽样预览：', event.url, error);
      completeTileFailed = true;
      applyCompleteTileAppearance();
      requestRender();
    }
  });
  requestRender();
}

function disposeArrows() {
  if (!arrowLines) return;
  normalScene.remove(arrowLines);
  arrowLines.geometry.dispose();
  arrowLines.material.dispose();
  arrowLines = null;
}

function releasePreview() {
  disposeCompleteTiles();
  disposeArrows();
  disposeInternalAxes();
  rawGeometry?.dispose();
  normalGeometry?.dispose();
  tableRemovalGeometry?.dispose();
  controlNetGeometry?.dispose(); controlNetGeometry = null; controlNetPoints.geometry = emptyClassGeometry;
  clearControlNetOverlay();
  partitionGeometry?.dispose();
  floatingZonesGeometry?.dispose();
  classGeometry?.dispose();
  projectionGeometry?.dispose();
  fusionGeometry?.dispose();
  refinementGeometry?.dispose();
  completeGeometry?.dispose(); completeGeometry = null;
  completePoints.geometry = emptyClassGeometry;
  clearCompleteAxes();
  internalRebarGeometry?.dispose();
  designPriorGeometry?.dispose(); designPriorGeometry = null; designPriorPoints.geometry = emptyClassGeometry;
  clearDesignPriorLines();
  cylinderDenoiseGeometry?.dispose(); cylinderDenoiseGeometry = null; cylinderDenoisePoints.geometry = emptyClassGeometry;
  clearCylinderFitOverlay();
  rawGeometry = null;
  normalGeometry = null;
  tableRemovalGeometry = null;
  partitionGeometry = null;
  floatingZonesGeometry = null;
  classGeometry = null;
  projectionGeometry = null;
  fusionGeometry = null;
  refinementGeometry = null;
  internalRebarGeometry = null;
  classPoints.geometry = emptyClassGeometry;
  tableRemovalPoints.geometry = emptyClassGeometry;
  partitionPoints.geometry = emptyClassGeometry;
  floatingZonesPoints.geometry = emptyClassGeometry;
  projectionPoints.geometry = emptyClassGeometry;
  fusionPoints.geometry = emptyClassGeometry;
  refinementPoints.geometry = emptyClassGeometry;
  internalRebarPoints.geometry = emptyClassGeometry;
  disposeFrameOverlays();
  disposeFloatingGeometryOverlays();
}

function normalColors(normals, valid, mode) {
  const colors = new Float32Array(normals.length);
  for (let i = 0; i < normals.length; i += 3) {
    const index = i / 3;
    const magnitude = Math.hypot(normals[i], normals[i + 1], normals[i + 2]);
    if (!valid[index] || magnitude < 1e-8) {
      colors[i] = 1; colors[i + 1] = 0; colors[i + 2] = 0.65;
    } else {
      for (let channel = 0; channel < 3; channel += 1) {
        colors[i + channel] = mode === 'absolute'
          ? Math.abs(normals[i + channel])
          : normals[i + channel] * 0.5 + 0.5;
      }
    }
  }
  return colors;
}

function applyNormalColors() {
  if (!normalGeometry || !current) return;
  normalGeometry.setAttribute('color', new THREE.BufferAttribute(
    normalColors(current._normals, current._valid, $('mode').value), 3,
  ));
  requestRender();
}

const classPalette = {
  0: [0x94 / 255, 0xa3 / 255, 0xb8 / 255],
  1: [0x64 / 255, 0x74 / 255, 0x8b / 255],
  2: [0xf5 / 255, 0x9e / 255, 0x0b / 255],
  3: [0x2d / 255, 0xd4 / 255, 0xbf / 255],
  4: [0xef / 255, 0x47 / 255, 0x6f / 255],
};

function classColors(classes) {
  const colors = new Float32Array(classes.length * 3);
  for (let index = 0; index < classes.length; index += 1) {
    const color = classPalette[classes[index]] || classPalette[0];
    const offset = index * 3;
    colors[offset] = color[0]; colors[offset + 1] = color[1]; colors[offset + 2] = color[2];
  }
  return colors;
}

const defaultRegionNames = { 0: '台面', 1: '内部钢筋', 2: '外部钢筋', 3: '夹具', 4: '未定位钢筋' };
const defaultRegionColors = { 0: '#64748b', 1: '#2dd4bf', 2: '#f472b6', 3: '#f59e0b', 4: '#60a5fa' };

function hexColor(value, fallback) {
  const match = /^#([0-9a-f]{6})$/i.exec(value || '') || /^#([0-9a-f]{6})$/i.exec(fallback);
  const number = Number.parseInt(match[1], 16);
  return [(number >> 16) / 255, ((number >> 8) & 0xff) / 255, (number & 0xff) / 255];
}

function regionColors(regions, palette = {}) {
  const colors = new Float32Array(regions.length * 3);
  for (let index = 0; index < regions.length; index += 1) {
    const color = hexColor(palette[String(regions[index])], defaultRegionColors[regions[index]] || '#94a3b8');
    colors.set(color, index * 3);
  }
  return colors;
}

const defaultZoneNames = { 0: '未定位', 1: '内框内部', 2: '夹具边带', 3: '外框外部' };
const defaultZoneColors = { 0: '#94a3b8', 1: '#3b82f6', 2: '#f59e0b', 3: '#f472b6' };

function zoneColors(zones) {
  const colors = new Float32Array(zones.length * 3);
  for (let index = 0; index < zones.length; index += 1) {
    colors.set(hexColor(defaultZoneColors[zones[index]], '#94a3b8'), index * 3);
  }
  return colors;
}

function filterIndexedGeometry(geometry, count, predicate) {
  if (!geometry) return 0;
  const indices = new Uint32Array(count);
  let selected = 0;
  for (let index = 0; index < count; index += 1) {
    if (predicate(index)) indices[selected++] = index;
  }
  geometry.setIndex(new THREE.BufferAttribute(indices.subarray(0, selected), 1));
  geometry.setDrawRange(0, selected);
  return selected;
}

function applyTableRemovalAppearance() {
  if (!tableRemovalGeometry || !current?._sharedTableMask) return;
  const data = stepSemanticData(current, 'tableRemoval');
  tableRemovalGeometry.setAttribute('color', new THREE.BufferAttribute(resultDisplayColors(data, 'tableRemoval') || semanticColors(data), 3));
  const selected = filterSemanticGeometry(tableRemovalGeometry, 'tableRemoval');
  $('tableRemovalHint').textContent = `当前显示 ${fmt(selected)} 个预览点。`;
  requestRender();
}

function updatePartitionLegend() {
  const names = current?.preprocessing?.partition?.zoneNames || defaultZoneNames;
  $('partitionLegend').replaceChildren(...[0, 1, 2, 3].map((value) => {
    const row = document.createElement('span');
    const swatch = document.createElement('i');
    swatch.className = 'swatch';
    swatch.style.background = defaultZoneColors[value];
    row.append(swatch, names[String(value)] || defaultZoneNames[value]);
    return row;
  }));
}

function applyPartitionAppearance() {
  if (!partitionGeometry || !current?._partitionZones) return;
  const data = stepSemanticData(current, 'partition');
  partitionGeometry.setAttribute('color', new THREE.BufferAttribute(resultDisplayColors(data, 'partition') || semanticColors(data), 3));
  filterSemanticGeometry(partitionGeometry, 'partition');
  updatePartitionLegend();
  requestRender();
}

const floatingLayerNames = { 0: '未分层', 1: '底层钢筋', 2: '顶层钢筋', 3: '腹杆层' };
const floatingLayerColors = { 0: '#94a3b8', 1: '#38bdf8', 2: '#fb7185', 3: '#facc15' };
function floatingLayerColor(id) { return floatingLayerColors[id] || ['#a78bfa','#fb923c','#34d399','#f472b6'][Math.max(0, Number(id) - 4) % 4]; }

function floatingZoneColors(layers, forbidden) {
  const colors = new Float32Array(layers.length * 3);
  for (let index = 0; index < layers.length; index += 1) {
    colors.set(hexColor(forbidden?.[index] ? '#ef476f' : floatingLayerColor(layers[index]), '#94a3b8'), index * 3);
  }
  return colors;
}

function updateFloatingLegend() {
  const ids = current?.preprocessing?.layering?.layers?.map(layer => Number(layer.id)).filter(Number.isFinite)
    || [...new Set(current?._sharedLayers || [])].sort((a, b) => a - b);
  $('floatingLegend').replaceChildren(...ids.map((value) => {
    const row = document.createElement('span');
    const swatch = document.createElement('i');
    swatch.className = 'swatch'; swatch.style.background = floatingLayerColor(value);
    const layer = current?.preprocessing?.layering?.layers?.find(item => Number(item.id) === value);
    row.append(swatch, layer?.name || (value === 0 ? '未分层' : `层 ${value}${Number.isFinite(layer?.heightM) ? ` · ${(layer.heightM * 1000).toFixed(1)} mm` : ''}`));
    return row;
  }), (() => {
    const row = document.createElement('span');
    const swatch = document.createElement('i');
    swatch.className = 'swatch'; swatch.style.background = '#ef476f';
    row.append(swatch, ['review-only', 'step05-residual-veto', 'step05-steel-boundary'].includes(current?.preprocessing?.floatingZones?.forbiddenRule?.action) ? '禁飞区待复核' : '历史禁飞区命中');
    return row;
  })());
  if (!current?.preprocessing?.floatingZones) $('floatingLegend').lastElementChild?.remove();
}

function applyFloatingZonesAppearance() {
  if (!floatingZonesGeometry || !current?._sharedLayers || !current?._sharedFloatingNoise) return;
  const data = stepSemanticData(current, 'floatingZones');
  floatingZonesGeometry.setAttribute('color', new THREE.BufferAttribute(resultDisplayColors(data, 'floatingZones') || semanticColors(data), 3));
  const selected = filterSemanticGeometry(floatingZonesGeometry, 'floatingZones');
  $('floatingHint').textContent = `当前显示 ${fmt(selected)} 个预览点。`;
  updateFloatingLegend();
  requestRender();
}

function hardMaskVisible(index, step, explicit = false) {
  if (['review-only', 'step05-residual-veto', 'step05-steel-boundary'].includes(current?.preprocessing?.floatingZones?.forbiddenRule?.action)) return true;
  if (!['classification', 'projection', 'fusion', 'refinement', 'internalRebar', 'completeRebar'].includes(step)
    || current?._sharedFloatingNoise?.[index] !== 1) return true;
  return explicit || preferredSemanticTag === 'before' || preferredSemanticTag === 'noise'
    || (semanticVisibilityCustomized && visibleSemanticCodes.has(10));
}

const internalTypeNames = { 0: '非内部钢筋', 1: '下层钢筋', 2: '上层钢筋', 3: '腹杆', 4: '待定钢筋', 5: '噪音' };
const internalTypeColors = { 1: '#38bdf8', 2: '#fb7185', 3: '#facc15', 4: '#94a3b8', 5: '#ef476f' };
const defaultInternalFamilyNames = { 1: '纵向钢筋', 2: '横向整长钢筋', 3: '短钢筋', 4: '腹杆直段' };
const internalFamilyColors = { 1: '#22c55e', 2: '#a78bfa', 3: '#f97316', 4: '#facc15' };

// One display vocabulary; persisted stage IDs retain their original meaning.
const semanticTags = [
  {id:'all', name:'全部（不含噪音）'},
  {id:'allSteel', name:'全部钢筋', codes:[3,4,5,6,7,8,9,11], color:'#34d399'},
  {id:'cleanSteel', name:'全部钢筋（去噪后）', codes:[4,5,6,7,8,9], color:'#34d399', steps:['internalRebar','completeRebar']},
  {id:'table', name:'台面', codes:[1], color:'#64748b'},
  {id:'fixture', name:'夹具', codes:[2], color:'#f59e0b'},
  {id:'internal', name:'内部钢筋', codes:[4,6,7,8,9], color:'#2dd4bf'},
  {id:'external', name:'外部钢筋', codes:[5], color:'#f472b6'},
  {id:'upper', name:'上层钢筋', codes:[7], color:'#fb7185'},
  {id:'web', name:'腹杆', codes:[8], color:'#facc15'},
  {id:'lower', name:'下层钢筋', codes:[6], color:'#38bdf8'},
  {id:'noise', name:'噪音', codes:[10], color:'#ef476f'},
];
const semanticAliasTags = {
  before: {id:'before', codes:[0,1,2,3,4,5,6,7,8,9,10,11]},
  steel: {id:'steel', codes:[3,4,5,6,7,8,9,11]},
  pending: {id:'pending', codes:[9]},
  unlocated: {id:'unlocated', codes:[11]},
  unknown: {id:'unknown', codes:[0]},
};
function semanticTagById(id) { return semanticTags.find(tag => tag.id === id) || semanticAliasTags[id]; }
const semanticPaletteHex = ['#94a3b8','#64748b','#f59e0b','#2dd4bf','#2dd4bf','#f472b6',
  '#38bdf8','#fb7185','#facc15','#2dd4bf','#ef476f','#2dd4bf'];
const categoryPaletteHex = ['#94a3b8','#64748b','#f59e0b','#2dd4bf','#2dd4bf','#a78bfa',
  '#2dd4bf','#2dd4bf','#2dd4bf','#2dd4bf','#ef476f','#2dd4bf'];
const semanticPalette = semanticPaletteHex.map(color => hexColor(color, color));
const categoryPalette = categoryPaletteHex.map(color => hexColor(color, color));
let preferredSemanticTag = 'all';
let preferredResultColorMode = 'layers';
let semanticVisibilityCustomized = false;
const visibleSemanticCodes = new Set([0,1,2,3,4,5,6,7,8,9,11]);

function stepSemanticData(manifest, step) {
  if (!manifest) return null;
  manifest._semanticCache ||= new Map();
  if (manifest._semanticCache.has(step)) return manifest._semanticCache.get(step);
  const classes = step === 'classification' ? manifest._classes : step === 'projection' ? manifest._projectionClasses
    : step === 'fusion' ? manifest._fusedClasses : step === 'completeRebar' ? manifest._complete?.complete_class : ['refinement','internalRebar'].includes(step) ? manifest._refinedClasses : null;
  const regions = step === 'fusion' ? manifest._fusedRegions
    : ['refinement','internalRebar','completeRebar'].includes(step) ? manifest._refinedRegions : null;
  const types = step === 'internalRebar' ? manifest._internalTypes : null;
  const completeTypes = step === 'completeRebar' ? new Map((manifest.completeRebar?.instances || []).map(i => [i.id, i.type])) : null;
  const count = classes?.length ?? manifest.preview?.pointCount ?? 0;
  const labels = new Uint8Array(count), counts = new Uint32Array(12);
  const regionLabels = [0,4,5,2,4], typeLabels = [0,6,7,8,9,10];
  for (let i = 0; i < count; i++) {
    let label = classes?.[i] ?? 0;
    if (['tableRemoval', 'partition', 'floatingZones'].includes(step)) {
      if (manifest._sharedTableMask?.[i] === 1) label = 1;
      else if (manifest._partitionZones?.[i] === 2) label = 2;
      else if (manifest._partitionZones?.[i] === 3) label = 5;
      else label = 4;
    }
    // Class 4 is the stable cross-stage representation of removed noise.
    if (classes && label === 4) label = 10;
    if (label === 0 && ((step === 'classification' && manifest.classification?.pendingClass === 0)
      || (step === 'projection' && manifest.projection?.pendingClass === 0))) label = 9;
    if (label === 3 && regions) label = regionLabels[regions[i]] ?? 4;
    if (label === 3) label = manifest._partitionZones?.[i] === 3 ? 5 : 4;
    if ([4,9,11].includes(label) && manifest._sharedLayers?.[i] > 0) label = typeLabels[manifest._sharedLayers[i]] ?? label;
    if (types?.[i] > 0) label = typeLabels[types[i]] ?? label;
    if (step === 'completeRebar') {
      if (classes?.[i] === 4) label = 10;
      else if (classes?.[i] === 3) {
        const owner = manifest._complete.complete_instance[i];
        label = owner ? (manifest._refinedZones?.[i] === 1 ? typeLabels[completeTypes.get(owner)] || 4 : 5) : 9;
      }
    }
    labels[i] = label;
    counts[label]++;
  }
  const data = {labels, counts, step};
  manifest._semanticCache.set(step, data);
  return data;
}

function semanticTagAvailable(data, tag) {
  return tag.id === 'all' || tag.id === 'before'
    || Boolean(data && (!tag.steps || tag.steps.includes(data.step)) && tag.codes.some(code => data.counts[code] > 0));
}

function semanticTagMatches(label, tagId) {
  if (tagId === 'before') return true;
  if (tagId === 'all') return label !== 10;
  return semanticTagById(tagId)?.codes.includes(label) ?? false;
}

function semanticTagCodes(tag) {
  return tag.id === 'all' ? [0,1,2,3,4,5,6,7,8,9,11] : (tag.codes || []);
}

function semanticTagChecked(data, tag) {
  const present = semanticTagCodes(tag).filter(code => data?.counts?.[code] > 0);
  return present.length > 0 && present.every(code => visibleSemanticCodes.has(code));
}

function setSemanticTagVisible(tag, visible) {
  for (const code of semanticTagCodes(tag)) {
    if (visible) visibleSemanticCodes.add(code); else visibleSemanticCodes.delete(code);
  }
  semanticVisibilityCustomized = true;
}

function showOnlySemanticTag(tag) {
  visibleSemanticCodes.clear();
  for (const code of semanticTagCodes(tag)) visibleSemanticCodes.add(code);
  semanticVisibilityCustomized = true;
  preferredSemanticTag = tag.id;
}

function semanticColors(data) {
  const colors = new Float32Array(data.labels.length * 3);
  for (let i = 0; i < data.labels.length; i++) colors.set(semanticPalette[data.labels[i]], i*3);
  return colors;
}

function categoryColors(data) {
  const colors = new Float32Array(data.labels.length * 3);
  for (let i = 0; i < data.labels.length; i++) colors.set(categoryPalette[data.labels[i]], i*3);
  return colors;
}

function resultColorModeAvailable(mode, step = rightScene, manifest = current) {
  if (step === 'cylinderDenoise') step = 'completeRebar';
  if (mode === 'categories') return true;
  if (mode === 'layers') {
    if (step === 'internalRebar') return Boolean(manifest?._internalTypes);
    if (step === 'completeRebar') return Boolean(manifest?._complete && manifest?.completeRebar?.instances);
    return Boolean(manifest?._sharedLayers)
      && ['floatingZones','classification','projection','fusion','refinement'].includes(step);
  }
  if (mode === 'instances') return step === 'internalRebar'
    ? Boolean(manifest?._internalInstances)
    : step === 'completeRebar' && Boolean(manifest?._complete?.complete_instance);
  return false;
}

function activeResultColorMode(step = rightScene) {
  return resultColorModeAvailable(preferredResultColorMode, step) ? preferredResultColorMode : 'categories';
}

function resultDisplayColors(data, step = rightScene) {
  const mode = $('resultColorMode')?.value;
  if (!data || !['categories','layers','instances'].includes(mode)) return null;
  if (mode === 'layers') return semanticColors(data);
  const colors = categoryColors(data);
  if (mode !== 'instances') return colors;
  const instances = step === 'completeRebar' ? current?._complete?.complete_instance : current?._internalInstances;
  if (!instances) return colors;
  for (let i = 0; i < data.labels.length; i++) {
    if (instances[i] > 0 && [4,5,6,7,8,9,11].includes(data.labels[i])) colors.set(rebarInstanceColor(instances[i], step), i*3);
  }
  return colors;
}

function updateResultColorControls() {
  const select = $('resultColorMode');
  if (!select) return;
  for (const option of Array.from(select.options || [])) option.disabled = !resultColorModeAvailable(option.value);
  const active = activeResultColorMode();
  select.value = active;
  if (active === 'instances') {
    $('resultColorHint').textContent = '每根已编号钢筋使用独立颜色；未编号钢筋保持类别颜色。';
  } else if (active === 'layers') {
    $('resultColorHint').textContent = '下层蓝、上层粉、腹杆黄；台面、夹具和外部钢筋保持固定颜色。';
  } else if (preferredResultColorMode === 'instances') {
    $('resultColorHint').textContent = '逐个实例着色在第 05、06、07 步可用；当前暂用类别着色。';
  } else if (preferredResultColorMode === 'layers') {
    $('resultColorHint').textContent = '当前步骤尚未产生分层结果，暂用类别着色。';
  } else {
    $('resultColorHint').textContent = '台面、夹具、内部钢筋、外部钢筋和噪音使用固定颜色。';
  }
  if ($('internalColorMode')) $('internalColorMode').value = active === 'instances' ? 'instances' : 'types';
  if ($('completeColorMode')) $('completeColorMode').value = active === 'instances' ? 'instances' : 'classes';
}

function filterSemanticGeometry(geometry, step, additional = null) {
  const data = stepSemanticData(current, step);
  if (!geometry || !data) return 0;
  const desired = semanticTagById(preferredSemanticTag);
  const tagId = semanticTagAvailable(data, desired) ? desired.id : 'all';
  const allowed = Array.from({length:12}, (_, code) => semanticVisibilityCustomized
    ? visibleSemanticCodes.has(code) : semanticTagMatches(code, tagId));
  const indices = new Uint32Array(data.labels.length);
  let count = 0;
  for (let i = 0; i < data.labels.length; i++) {
    if (allowed[data.labels[i]] && hardMaskVisible(i, step) && (!additional || additional(i))) indices[count++] = i;
  }
  geometry.setIndex(new THREE.BufferAttribute(indices.subarray(0, count), 1));
  geometry.setDrawRange(0, count);
  return count;
}

function updateSemanticControls() {
  const data = stepSemanticData(current, rightScene);
  const desired = semanticTagById(preferredSemanticTag);
  const available = semanticTagAvailable(data, desired);
  const hasClassifiedResults = semanticTags.slice(1).some(tag => semanticTagAvailable(data, tag));
  $('semanticFilter').replaceChildren(...semanticTags.map(tag => {
    const option = new Option(tag.name, tag.id);
    option.disabled = !semanticTagAvailable(data, tag);
    return option;
  }));
  $('semanticFilter').value = available ? preferredSemanticTag : 'all';
  $('semanticHint').textContent = !hasClassifiedResults
    ? '这一步尚未产生分类；后续步骤仍使用同一组分类名称。'
    : semanticVisibilityCustomized
      ? '使用右侧开关组合显示类别；点击类别名称可只看这一类。'
    : available
      ? '使用右侧开关控制显隐；点击类别名称可快速只看这一类。'
      : `这一步没有“${desired.name}”，暂时显示全部结果。`;
  $('semanticLegend').replaceChildren(...semanticTags.map(tag => {
    const enabled = semanticTagAvailable(data, tag);
    const activeTagId = available ? preferredSemanticTag : 'all';
    const presentCodes = semanticTagCodes(tag).filter(code => data?.counts?.[code] > 0);
    const checked = semanticVisibilityCustomized
      ? semanticTagChecked(data, tag)
      : presentCodes.length > 0 && presentCodes.every(code => semanticTagMatches(code, activeTagId));
    const row = document.createElement('div');
    row.className = `categoryChoice${tag.id === 'all' ? ' categoryAll' : ''}`;
    row.classList.toggle('active', checked);
    row.classList.toggle('disabled', !enabled);
    if (tag.color) {
      const swatch = document.createElement('i');
      const colorMode = $('resultColorMode')?.value;
      const isSteel = tag.codes?.some(code => [4,5,6,7,8,9,11].includes(code));
      swatch.className = 'swatch';
      swatch.style.background = colorMode === 'instances' && isSteel
        ? 'linear-gradient(90deg,#22c55e,#a78bfa,#f97316)'
        : colorMode === 'categories' && tag.codes?.length ? categoryPaletteHex[tag.codes[0]] : tag.color;
      row.append(swatch);
    } else {
      const spacer = document.createElement('i');
      row.append(spacer);
    }
    const name = document.createElement('button');
    name.type = 'button';
    name.className = 'categorySolo';
    name.textContent = tag.name;
    name.disabled = !enabled;
    name.title = `只看${tag.name}`;
    name.addEventListener('click', () => {
      showOnlySemanticTag(tag);
      $('semanticFilter').value = tag.id;
      updateSemanticControls();
      applyCurrentSemanticFilter();
    });
    const count = document.createElement('span');
    count.className = 'count';
    count.textContent = data
      ? fmt(tag.codes ? tag.codes.reduce((sum, code) => sum + data.counts[code], 0) : data.labels.length - data.counts[10])
      : '—';
    const toggle = document.createElement('label');
    toggle.className = 'visibilityToggle';
    toggle.title = `${checked ? '隐藏' : '显示'}${tag.name}`;
    const input = document.createElement('input');
    input.type = 'checkbox';
    input.checked = checked;
    input.disabled = !enabled;
    input.setAttribute('aria-label', `${checked ? '隐藏' : '显示'}${tag.name}`);
    const track = document.createElement('span');
    track.className = 'visibilityTrack';
    input.addEventListener('change', () => {
      setSemanticTagVisible(tag, input.checked);
      updateSemanticControls();
      applyCurrentSemanticFilter();
    });
    toggle.append(input, track);
    row.append(name, count, toggle);
    return row;
  }));
}

function applyCurrentSemanticFilter() {
  if (rightScene === 'tableRemoval') applyTableRemovalAppearance();
  else if (rightScene === 'controlNet') applyControlNetAppearance();
  else if (rightScene === 'partition') applyPartitionAppearance();
  else if (rightScene === 'floatingZones') applyFloatingZonesAppearance();
  else if (rightScene === 'classification') applyClassFilter();
  else if (rightScene === 'projection') applyProjectionFilter();
  else if (rightScene === 'fusion') applyFusionAppearance();
  else if (rightScene === 'refinement') applyRefinementAppearance();
  else if (rightScene === 'internalRebar') applyInternalRebarAppearance();
  else if (rightScene === 'completeRebar') applyCompleteAppearance();
  else if (rightScene === 'cylinderDenoise') applyCylinderDenoiseAppearance();
  else if (rightScene === 'raw' || rightScene === 'normal') {
    filterSemanticGeometry(rightScene === 'raw' ? rawGeometry : normalGeometry, rightScene);
    requestRender();
  }
}

function internalFamilyNames(report = current?.internalRebar) {
  return { ...defaultInternalFamilyNames, ...(report?.tracks?.familyNames || {}) };
}

function diameterPriorSummary(internalRebar) {
  return Object.entries(internalRebar?.tracks?.diameterPriors || {})
    .filter(([, prior]) => Number.isFinite(prior?.nominalM))
    .map(([type, prior]) => {
      const source = prior.source === 'ifc' ? 'IFC 先验' : '点云众数';
      const observed = Number.isFinite(prior.observedModeM) && Math.abs(prior.observedModeM - prior.nominalM) > 1e-6
        ? `，观测 Ø${fmtHeight(prior.observedModeM)}` : '';
      const diameters = (prior.nominalsM || [prior.nominalM]).filter(Number.isFinite).map(fmtHeight).join(' / ');
      return `${internalTypeNames[type] || `类型 ${type}`} Ø${diameters}（主直径：${source}${observed}）`;
    }).join('；');
}

function observedLengthSummary(internalRebar) {
  return (internalRebar?.tracks?.lengthFamilies || [])
    .filter((family) => Number.isFinite(family?.lengthM))
    .map((family) => `${family.name || internalFamilyNames(internalRebar)[family.id] || `族 ${family.id}`} ${fmt(family.count)} 根 · ${fmtHeight(family.lengthM)}`)
    .join('；');
}

function ifcHorizontalLengthSummary(internalRebar) {
  const priors = internalRebar?.tracks?.ifcPriors;
  if (!priors?.available || !Array.isArray(priors.horizontalLengthsM)) return '';
  const lengths = priors.horizontalLengthsM.filter(Number.isFinite).map(fmtHeight);
  return lengths.length ? lengths.join(' / ') : '';
}

function priorColor(value) { return hexColor(value, '#94a3b8'); }
function stableParentColorId(id) {
  let hash = 2166136261;
  for (const char of String(id)) hash = Math.imul(hash ^ char.charCodeAt(0), 16777619);
  return (hash >>> 0) || 1;
}
function designPriorCoverageSummary(coverage = {}) {
  const pick = (...keys) => keys.map((key) => coverage[key]).find(Number.isFinite);
  const complete = pick('complete', 'completeBars', 'matchedBars');
  const partial = pick('partial', 'partialBars');
  const unresolved = pick('unresolved', 'unresolvedBars', 'missingBars');
  const bars = pick('physicalBarCount', 'designBars', 'barCount');
  const diagonal = pick('webStraightUnitCount', 'designDiagonalUnits', 'designWebUnits', 'diagonalUnits');
  const observed = pick('observedDiagonalInstances', 'observedWebInstances');
  return [Number.isFinite(bars) && `设计对象 ${fmt(bars)}`, Number.isFinite(complete) && `完整 ${fmt(complete)}`,
    Number.isFinite(partial) && `部分 ${fmt(partial)}`, Number.isFinite(unresolved) && `未解析 ${fmt(unresolved)}`,
    Number.isFinite(diagonal) && `已解析设计斜段 ${fmt(diagonal)}`, Number.isFinite(observed) && `观测斜段 ${fmt(observed)}`].filter(Boolean).join(' · ') || '—';
}
function clearDesignPriorLines() {
  if (!designPriorLines) return;
  designPriorScene.remove(designPriorLines); designPriorLines.geometry.dispose(); designPriorLines.material.dispose(); designPriorLines = null;
}
async function loadDesignPriorPreview(manifest, fetcher = fetchBytes) {
  if (!manifest.designPrior?.enabled) return null;
  const p = manifest.preview || {}, count = p.pointCount;
  const specs = {prior_class: Uint8Array, prior_instance: Uint32Array, prior_component: Uint32Array, prior_status: Uint8Array, prior_action: Uint8Array};
  const arrays = Object.fromEntries(await Promise.all(Object.entries(specs).map(async ([name, Type]) => {
    if (!p[name + 'Url']) throw new Error(`设计先验预览缺少 ${name}`);
    const array = new Type(await fetcher(p[name + 'Url']));
    if (array.length !== count) throw new Error('设计先验预览长度不一致');
    return [name, array];
  })));
  const report = manifest.designPrior, components = report.components, inventory = report.inventory;
  if (!Array.isArray(components) || !Array.isArray(report.instances) || !Array.isArray(inventory?.bars) || !Array.isArray(inventory?.units)) throw new Error('设计先验实例、组件和设计清单必须为数组');
  const instanceIds = new Set();
  for (const instance of report.instances) {
    if (!Number.isSafeInteger(instance?.id) || instance.id <= 0 || instanceIds.has(instance.id)) throw new Error('设计先验实例清单包含无效或重复编号');
    instanceIds.add(instance.id);
  }
  const barIds = new Set();
  for (const bar of inventory.bars) {
    if (typeof bar?.designBarId !== 'string' || !bar.designBarId || barIds.has(bar.designBarId)) throw new Error('设计母筋清单包含缺失或重复编号');
    barIds.add(bar.designBarId);
  }
  const unitById = new Map();
  for (const unit of inventory.units) {
    if (typeof unit?.designBarId !== 'string' || !barIds.has(unit.designBarId) || typeof unit.designUnitId !== 'string' || !unit.designUnitId || unitById.has(unit.designUnitId)
      || !['straight','short','web'].includes(unit.kind) || [unit.startM, unit.endM].some((point) => !Array.isArray(point) || point.length !== 3 || point.some((value) => !Number.isFinite(value)))) throw new Error('设计单元清单包含无效父级、编号、类型或坐标');
    unitById.set(unit.designUnitId, unit);
  }
  const componentIds = new Set(), componentById = new Map(), shortComponentIds = new Set(), parentColorIds = new Map([...barIds].map((id) => [id, stableParentColorId(id)]));
  for (const item of components) {
    if (!Number.isSafeInteger(item?.id) || item.id <= 0 || componentIds.has(item.id) || ![1,2,3].includes(item.status) || !Number.isSafeInteger(item.action) || item.action < 0 || item.action > 7) throw new Error('设计先验组件清单无效');
    const hasBar = typeof item.designBarId === 'string' && item.designBarId.length > 0, hasUnit = typeof item.designUnitId === 'string' && item.designUnitId.length > 0;
    if ((item.designBarId !== null && !hasBar) || (item.designUnitId !== null && !hasUnit) || hasBar !== hasUnit
      || (hasBar && (!barIds.has(item.designBarId) || unitById.get(item.designUnitId)?.designBarId !== item.designBarId))
      || (item.instanceId != null && (!Number.isSafeInteger(item.instanceId) || item.instanceId < 0 || (item.instanceId > 0 && !instanceIds.has(item.instanceId))))) throw new Error('设计先验组件与设计母筋/单元映射不一致');
    componentIds.add(item.id);
    componentById.set(item.id, item);
    if (hasUnit && unitById.get(item.designUnitId).kind === 'short') shortComponentIds.add(item.id);
  }
  for (let i = 0; i < count; i++) {
    const cls = arrays.prior_class[i], status = arrays.prior_status[i], action = arrays.prior_action[i], component = arrays.prior_component[i], instance = arrays.prior_instance[i];
    if (cls < 1 || cls > 4 || status > 3 || action > 7 || (component && !componentIds.has(component)) || (instance && !instanceIds.has(instance))) throw new Error('设计先验预览类别、编号或动作无效');
  }
  const mergedInstanceIds = new Set(components.filter(c => (c.action & 4) && Number.isSafeInteger(c.instanceId) && c.instanceId > 0).map(c => c.instanceId));
  arrays._meta = { unitById, componentById, shortComponentIds, parentColorIds, mergedInstanceIds };
  return arrays;
}
function installDesignPriorPreview() {
  $('designPriorStep').disabled = !current?._designPrior;
  for (const [id, key] of [['priorSteelLas','priorSteelLasUrl'], ['priorNoiseLas','priorNoiseLasUrl'], ['designPrior','designPriorUrl']]) { $(id).hidden = !current?.files?.[key]; $(id).href = current?.files?.[key] || '#'; }
  if (!current?._designPrior) return;
  designPriorGeometry = new THREE.BufferGeometry(); designPriorGeometry.setAttribute('position', new THREE.BufferAttribute(current._positions, 3));
  designPriorGeometry.boundingSphere = typeof rawGeometry !== 'undefined' ? rawGeometry?.boundingSphere?.clone() || null : null;
  if (!designPriorGeometry.boundingSphere) designPriorGeometry.computeBoundingSphere();
  designPriorPoints.geometry = designPriorGeometry;
  const prior = current.designPrior, c = prior.counts || {}, timing = prior.timings || {};
  $('designPriorSummary').replaceChildren(...[
    ['本次模型', prior.modelInfo?.name ? `${prior.modelInfo.name} · BIM #${prior.modelInfo.bimAssetId ?? '—'}` : '历史结果未记录模型名称'],
    ['处理范围', prior.inputPolicy === 'complete_class == 3' ? '仅串联已保留钢筋簇，语义分类和点数不变' : '历史版本：包含候选重新分类'],
    ['匹配 / 待定 / 噪音', `${fmt(c.matched)} / ${fmt(c.pending)} / ${fmt(c.noise)}`],
    ['过滤 / 恢复 / 合并点', `${fmt(c.filteredPoints)} / ${fmt(c.recoveredPoints)} / ${fmt(c.mergedPoints)}`],
    ['合并实例', fmt(c.mergedInstances)],
    ...(prior.associationCounts ? [
      ['编号实例（前 → 后）', `${fmt(prior.associationCounts.observedInstancesBefore)} → ${fmt(prior.associationCounts.observedInstancesAfter)}`],
      ['未编号簇（前 → 后）', `${fmt(prior.associationCounts.unassignedClustersBefore)} → ${fmt(prior.associationCounts.unassignedClustersAfter)}`],
      ['尚无确定观测对应的设计单元', fmt(prior.associationCounts.unobservedUnitIds?.length)],
    ] : []),
    ['设计提取范围', designPriorCoverageSummary({...prior.inventory?.coverage,
      observedDiagonalInstances: (current.completeRebar?.instances || []).filter(i => i.family === 4 || i.type === 3).length})],
    ['耗时', Object.entries(timing).map(([k,v]) => `${k} ${fmt(v, ' s')}`).join(' · ') || '—'],
  ].map(([name, value]) => { const row=document.createElement('span'); row.textContent=`${name}：${value}`; return row; }));
}
function applyDesignPriorAppearance() {
  if (!designPriorGeometry || !current?._designPrior) return;
  const prior = current._designPrior, report = current.designPrior, baseline = current._complete;
  const useResult = $('priorCompare').value === 'result'; const classes = useResult ? prior.prior_class : baseline?.complete_class || prior.prior_class; const instances = useResult ? prior.prior_instance : baseline?.complete_instance || prior.prior_instance;
  const colors = new Float32Array(classes.length * 3), selected = new Uint32Array(classes.length); let size=0;
  const meta = prior._meta, mode=$('priorColorMode').value, filter=$('priorFilter').value;
  for (let i=0;i<classes.length;i++) {
    const componentId=prior.prior_component[i], component=meta.componentById.get(componentId), action=prior.prior_action[i], status=prior.prior_status[i];
    let color = mode === 'observed' ? instanceColor(instances[i]) : mode === 'design' ? instanceColor(component?.designBarId ? meta.parentColorIds.get(component.designBarId) : 0) : mode === 'status' ? priorColor(({0:'#64748b',1:'#22c55e',2:'#facc15',3:'#ef476f'})[status]) : priorColor(({1:'#64748b',2:'#f59e0b',3:'#2dd4bf',4:'#ef476f'})[classes[i]]);
    colors.set(color, i*3);
    const short = meta.shortComponentIds.has(componentId) || component?.family === 3 || (!component && current._internalFamilies?.[i] === 3);
    const matches = filter === 'all' || (filter === 'steel' && classes[i] === 3) || (filter === 'short' && short) || (filter === 'filtered' && (action&1)) || (filter === 'recovered' && (action&2)) || (filter === 'merged' && ((action&4) || meta.mergedInstanceIds?.has(component?.instanceId))) || (filter === 'pending' && status===2);
    if (matches) selected[size++]=i;
  }
  designPriorGeometry.setAttribute('color', new THREE.BufferAttribute(colors,3)); designPriorGeometry.setIndex(new THREE.BufferAttribute(selected.subarray(0,size),1)); designPriorGeometry.setDrawRange(0,size);
  const legend = mode === 'semantic' ? [['台面','#64748b'],['夹具','#f59e0b'],['钢筋','#2dd4bf'],['噪音','#ef476f']]
    : mode === 'status' ? [['静态','#64748b'],['匹配','#22c55e'],['待定','#facc15'],['噪音','#ef476f']]
      : mode === 'design' ? [['不同颜色','#a78bfa'],['每色为一根设计母筋','#38bdf8']]
        : [['不同颜色','#a78bfa'],['每色为一个观测实例','#38bdf8']];
  $('designPriorLegend').replaceChildren(...legend.map(([label, color]) => { const row=document.createElement('span'); row.innerHTML=`<i class="swatch" style="background:${color}"></i>${label}`; return row; }));
  clearDesignPriorLines();
  if ($('priorLines').checked) { const vertices=[], colorsLine=[], origin=current.preview.origin || [0,0,0]; for (const unit of report.inventory?.units || []) { vertices.push(...unit.startM.map((v,a)=>v-origin[a]),...unit.endM.map((v,a)=>v-origin[a])); const color=instanceColor(meta.parentColorIds.get(unit.designBarId)); colorsLine.push(...color,...color); } const geometry=new THREE.BufferGeometry(); geometry.setAttribute('position',new THREE.Float32BufferAttribute(vertices,3)); geometry.setAttribute('color',new THREE.Float32BufferAttribute(colorsLine,3)); designPriorLines=new THREE.LineSegments(geometry,new THREE.LineBasicMaterial({vertexColors:true})); designPriorScene.add(designPriorLines); }
  $('designPriorHint').textContent=`显示 ${fmt(size)} 个样本点；设计母筋分组只改变颜色，不改变观测实例编号。`;
  requestRender();
}

async function loadCompletePreview(manifest, fetcher = fetchBytes) {
  if (!manifest.completeRebar) return null;
  const specs = {complete_class: Uint8Array, complete_instance: Uint32Array, complete_segment: Uint32Array, complete_confidence: Float32Array};
  if (manifest.completeRebar.clusters) specs.complete_cluster = Uint32Array;
  const arrays = Object.fromEntries(await Promise.all(Object.entries(specs).map(async ([name, Type]) => {
    const url = manifest.preview[name + 'Url'];
    if (!url) throw new Error('整根钢筋预览缺少 ' + name);
    const array = new Type(await fetcher(url));
    if (array.length !== manifest.preview.pointCount) throw new Error('整根钢筋预览长度不一致');
    return [name, array];
  })));
  const clusterIds = new Set((manifest.completeRebar.clusters || []).map(c => c.id));
  const instances = new Set(manifest.completeRebar.instances.map(i => i.id));
  const segments = new Map(manifest.completeRebar.segments.map(s => [s.id, s]));
  for (let i = 0; i < arrays.complete_class.length; i++) {
    const cls = arrays.complete_class[i], id = arrays.complete_instance[i], seg = arrays.complete_segment[i], score = arrays.complete_confidence[i];
    if (arrays.complete_cluster?.[i] && !clusterIds.has(arrays.complete_cluster[i])) throw new Error('外部簇编号无效');
    if (cls < 1 || cls > 4 || (id && (cls !== 3 || !instances.has(id) || segments.get(seg)?.instanceId !== id)) || (!id && seg) || !Number.isFinite(score) || score < 0 || score > 1) throw new Error('整根钢筋类别或实例属性无效');
  }
  return arrays;
}

function controlPreviewUrl(preview, name) {
  return preview?.[`${name}Url`] || preview?.files?.[name] || null;
}

function controlNetVec3(value) {
  return Array.isArray(value) && value.length === 3 && value.every(Number.isFinite);
}

function controlNetPolylineLength(points) {
  return points.slice(1).reduce((sum, point, index) => sum
    + Math.hypot(...point.map((value, axis) => value - points[index][axis])), 0);
}

function controlNetOwnershipSummary(report) {
  const stages = report?.policy?.ownershipStages;
  if (!Array.isArray(stages) || !stages.length) return '';
  const names = {straight: '直筋', short: '短筋', web: '腹杆'};
  return `${stages.map((stage) => `${names[stage.kind] || stage.kind} ${fmt(stage.fittedUnits)} / ${fmt(stage.designUnits)} · 锁定 ${fmt(stage.lockedPoints)} 点`).join(' → ')}；待定点保留`;
}

function controlNetOwnershipHint(report) {
  const stages = report?.policy?.ownershipStages;
  if (!Array.isArray(stages) || !stages.length) return '';
  const names = {straight: '直筋', short: '短筋', web: '腹杆'};
  return `归属顺序为 ${stages.map((stage) => names[stage.kind] || stage.kind).join(' → ')}；每阶段锁定唯一支持点，待定点继续保留。`;
}

function controlNetLengthDetail(item) {
  if (item?.lengthCheck !== 'extended-observed-span') return '';
  const evidence = item.lengthEvidence;
  return `主体设计 / 拟合长度 ${fmtHeight(item.designLengthM)} / ${fmtHeight(item.fittedLengthM)}；首端 / 末端延长 ${fmtHeight(evidence.extensionStartM)} / ${fmtHeight(evidence.extensionEndM)}。`;
}

function controlNetLengthObservation(item) {
  if (item?.lengthCheck === 'extended-observed-span') {
    return `主体观测跨度 ${fmtHeight(item.observedLengthM)}。${controlNetLengthDetail(item)}`;
  }
  const label = item?.kind === 'web' && item.axisModel === 'fixed-length-straight-cylinder'
    ? '主体支持跨度 / 设计直段长度' : '主体观测 / 设计直段长度';
  return `${label} ${fmtHeight(item?.observedLengthM)} / ${fmtHeight(item?.designLengthM)}。`;
}

function controlNetCurvedPiecesForUnit(report, unitId) {
  return (report?.curvedPieces || []).filter((piece) => piece.unitIds.includes(Number(unitId)));
}

function controlNetCurveStats(report, unitId = null) {
  const pieces = unitId == null ? (report?.curvedPieces || []) : controlNetCurvedPiecesForUnit(report, unitId);
  const fitted = pieces.filter((piece) => piece.status === 'fitted');
  const inferred = pieces.filter((piece) => piece.connectionStatus === 'design-inferred');
  return {
    designPieces: pieces.length,
    fittedPieces: fitted.length,
    pendingPieces: pieces.length - fitted.length,
    inferredPieces: inferred.length,
    scanGuidedPieces: inferred.filter(piece => piece.inferenceMethod === 'scan-guided-parametric-terminal').length,
    unresolvedPieces: pieces.length - fitted.length - inferred.length,
    matchedPoints: pieces.reduce((sum, piece) => sum + piece.pointCount + (piece.localSupport?.pointCount || 0), 0),
    locallyMatchedPoints: pieces.reduce((sum, piece) => sum + (piece.localSupport?.pointCount || 0), 0),
    locallySupportedPieces: pieces.filter(piece => piece.localSupport?.pointCount > 0).length,
    designLengthM: pieces.reduce((sum, piece) => sum + piece.designLengthM, 0),
    fittedLengthM: fitted.reduce((sum, piece) => sum + (piece.fittedLengthM ?? controlNetPolylineLength(piece.centerlineM)), 0),
    inferredLengthM: inferred.reduce((sum, piece) => sum + piece.inferredLengthM, 0),
  };
}

function validateControlNetCurvedPieces(report) {
  if (report?.curvedPieces == null) {
    if (report?.curveSummary != null) throw new Error('控制网弯曲段汇总缺少弯曲段清单');
    return;
  }
  if (!Array.isArray(report.curvedPieces)) throw new Error('控制网弯曲段清单必须为数组');
  const units = new Map(report.instances.map((item) => [item.id, item]));
  const reportVersion = Number(report.version?.match(/^design-control-net-v(\d+)$/)?.[1]) || 0;
  const pieceIds = new Set();
  const validTerminal = (piece) => {
    const p = piece.terminalParameters;
    if (p == null) return piece.inferenceMethod !== 'scan-guided-parametric-terminal';
    const curve = piece.centerlineM || piece.inferredCenterlineM;
    if (reportVersion < 18 || piece.kind !== 'terminal' || p.model !== 'circular-arc-tangent-tail'
      || ![p.offsetM, p.arcCenterM, p.arcNormal, p.arcTangent].every(controlNetVec3)
      || !['rollRad', 'pitchRad', 'yawRad', 'radiusM', 'sweepRad', 'tailLengthM'].every(k => Number.isFinite(p[k]))
      || p.radiusM <= piece.diameterM * .525 || p.sweepRad <= .2 || p.sweepRad >= Math.PI * 1.8 || p.tailLengthM < 0
      || !Array.isArray(curve) || curve.length !== (p.tailLengthM > 1e-6 ? 58 : 57)) return false;
    const n = p.arcNormal, t = p.arcTangent;
    if (Math.abs(Math.hypot(...n) - 1) > 1e-6 || Math.abs(Math.hypot(...t) - 1) > 1e-6
      || Math.abs(n.reduce((sum, value, i) => sum + value * t[i], 0)) > 1e-6) return false;
    const side = [n[1]*t[2]-n[2]*t[1], n[2]*t[0]-n[0]*t[2], n[0]*t[1]-n[1]*t[0]];
    for (let i = 0; i <= 40; i++) {
      const a = p.sweepRad * i / 40;
      const expected = p.arcCenterM.map((v, j) => v + p.radiusM * (Math.sin(a)*t[j] - Math.cos(a)*side[j]));
      if (!controlNetVec3(curve[16+i]) || Math.hypot(...expected.map((v,j) => v-curve[16+i][j])) > 1e-6) return false;
    }
    if (p.tailLengthM > 1e-6) {
      const expected = curve[56].map((v,j) => v+p.tailLengthM*(Math.cos(p.sweepRad)*t[j]+Math.sin(p.sweepRad)*side[j]));
      if (!controlNetVec3(curve[57]) || Math.hypot(...expected.map((v,j) => v-curve[57][j])) > 1e-6) return false;
    }
    return true;
  };
  for (const piece of report.curvedPieces) {
    const fitted = piece?.status === 'fitted';
    const expectedUnits = piece?.kind === 'join' ? 2 : piece?.kind === 'terminal' ? 1 : 0;
    const references = Array.isArray(piece?.unitIds) ? piece.unitIds : [];
    const referencedUnits = references.map((id) => units.get(id));
    const fittedCenterline = piece?.centerlineM;
    const designCenterline = piece?.designCenterlineM;
    const inferenceFields = ['inferredCenterlineM', 'connectionStatus', 'inferredLengthM', 'inferenceMethod'];
    const hasInference = inferenceFields.some((field) => piece?.[field] != null);
    const inferredCenterline = piece?.inferredCenterlineM;
    const local = piece?.localSupport;
    const validLocal = local == null || (reportVersion >= 20
      && piece.status === 'pending' && piece.connectionStatus === 'design-inferred'
      && piece.inferenceMethod === 'scan-guided-parametric-terminal'
      && local.method === 'independent-continuous-surface-intervals'
      && Number.isSafeInteger(local.pointCount) && local.pointCount >= 12
      && local.pointCount <= referencedUnits.reduce((sum, unit) => sum + (unit?.pointCount || 0), 0)
      && Number.isFinite(local.validationMaxChangeM) && local.validationMaxChangeM >= 0
      && local.validationMaxChangeM <= Math.max(.001, .25*piece.diameterM)
      && Array.isArray(local.intervalsM) && local.intervalsM.length > 0
      && local.intervalsM.every((range, i) => Array.isArray(range) && range.length === 2
        && range.every(Number.isFinite) && range[0] >= 0 && range[1] > range[0]
        && range[1] <= piece.inferredLengthM + 1e-8
        && (i === 0 || range[0] > local.intervalsM[i-1][1])));
    const validInference = !hasInference || (reportVersion >= 16
      && piece?.status === 'pending' && piece?.pointCount === 0
      && piece?.centerlineM == null && piece?.fittedLengthM == null
      && piece?.connectionStatus === 'design-inferred'
      && ((piece?.kind === 'join' && piece?.inferenceMethod === 'body-anchored-design-join'
        && referencedUnits.length === 2 && referencedUnits.every((unit) => unit?.kind === 'web' && unit.status === 'fitted'))
        || (reportVersion >= 17 && piece?.kind === 'terminal'
          && (piece?.inferenceMethod === 'body-anchored-design-terminal'
            || (reportVersion >= 18 && piece?.inferenceMethod === 'scan-guided-parametric-terminal'
              && Number.isSafeInteger(piece.inferenceValidation?.heldSupport) && piece.inferenceValidation.heldSupport >= 24
              && Number.isFinite(piece.inferenceValidation?.observedP90ChangeM)
              && piece.inferenceValidation.observedP90ChangeM >= 0
              && piece.inferenceValidation.observedP90ChangeM <= Math.max(.002, .5*piece.diameterM)))
          && referencedUnits.length === 1 && referencedUnits[0]?.kind === 'straight' && referencedUnits[0].status === 'fitted'
          && ['start', 'end'].includes(piece.inferenceBasis?.anchorSide)
          && piece.inferenceBasis?.orientation === 'body-tangent-and-design-plane'
          && Number.isFinite(piece.inferenceBasis?.bendRadiusM) && piece.inferenceBasis.bendRadiusM > piece.diameterM / 2 * 1.05
          && Number.isFinite(piece.inferenceBasis?.bendAngleRad) && piece.inferenceBasis.bendAngleRad > .2 && piece.inferenceBasis.bendAngleRad < Math.PI * 1.8
          && Number.isFinite(piece.inferenceBasis?.tailLengthM) && piece.inferenceBasis.tailLengthM >= 0))
      && Array.isArray(inferredCenterline) && inferredCenterline.length >= 2
      && inferredCenterline.every((point) => controlNetVec3(point))
      && Number.isFinite(piece?.inferredLengthM) && piece.inferredLengthM > 0
      && Math.abs(controlNetPolylineLength(inferredCenterline) - piece.inferredLengthM) <= 1e-6
      );
    if (typeof piece?.id === 'string' && pieceIds.has(piece.id)) throw new Error(`控制网弯曲段编号重复：${piece.id}`);
    if (references.length && new Set(references).size !== references.length) throw new Error(`控制网弯曲段引用重复：${piece?.id || '未编号'}`);
    if (references.length && referencedUnits.some((unit) => !unit)) throw new Error(`控制网弯曲段引用不存在：${piece?.id || '未编号'}`);
    if (referencedUnits.length && referencedUnits.every(Boolean) && referencedUnits.some((unit) => String(unit.designBarId) !== piece?.designBarId)) {
      throw new Error(`控制网弯曲段引用跨越物理母筋：${piece?.id || '未编号'}`);
    }
    if (typeof piece?.id !== 'string' || !piece.id.trim()
      || !expectedUnits || references.length !== expectedUnits
      || references.some((id) => !Number.isSafeInteger(id) || id <= 0)
      || typeof piece.designBarId !== 'string' || !piece.designBarId
      || !['fitted', 'pending'].includes(piece.status) || typeof piece.reason !== 'string'
      || !Number.isFinite(piece.diameterM) || piece.diameterM <= 0
      || !Number.isFinite(piece.designLengthM) || piece.designLengthM <= 0
      || !Number.isSafeInteger(piece.pointCount) || piece.pointCount < 0
      || (piece.rmseM != null && (!Number.isFinite(piece.rmseM) || piece.rmseM < 0))
      || (piece.attachmentLengthM != null && (!Number.isFinite(piece.attachmentLengthM) || piece.attachmentLengthM < 0))
      || (piece.fittedLengthM != null && (!Number.isFinite(piece.fittedLengthM) || piece.fittedLengthM <= 0))
      || typeof piece.geometrySource !== 'string' || !piece.geometrySource.trim()
      || (designCenterline != null && (!Array.isArray(designCenterline) || designCenterline.length < 2
        || designCenterline.some((point) => !controlNetVec3(point))))
      || !validInference || !validLocal || !validTerminal(piece)
      || (fitted ? !Array.isArray(fittedCenterline) || fittedCenterline.length < 2
        || fittedCenterline.some((point) => !controlNetVec3(point))
        || (piece.fittedLengthM != null && Math.abs(controlNetPolylineLength(fittedCenterline) - piece.fittedLengthM) > 1e-6)
        : fittedCenterline != null)) {
      throw new Error('控制网弯曲段包含无效身份、状态、引用或长度几何');
    }
    pieceIds.add(piece.id);
  }
  if (report.curveSummary != null) {
    const summary = report.curveSummary, actual = controlNetCurveStats(report);
    const keys = ['designPieces', 'fittedPieces', 'pendingPieces', 'matchedPoints'];
    if (summary.unresolvedPieces != null) keys.push('unresolvedPieces');
    for (const key of ['locallySupportedPieces', 'locallyMatchedPoints']) if (summary[key] != null) keys.push(key);
    if (typeof summary !== 'object' || summary == null
      || keys.some((key) => !Number.isSafeInteger(summary[key]) || summary[key] < 0 || summary[key] !== actual[key])
      || (summary.inferredPieces != null && (!Number.isSafeInteger(summary.inferredPieces)
        || summary.inferredPieces < 0 || summary.inferredPieces !== actual.inferredPieces))) {
      throw new Error('控制网弯曲段汇总与清单不一致');
    }
  }
}

function controlNetInputStage(report) {
  return ['post-fusion', 'post-layering'].includes(report?.inputStage) ? report.inputStage : 'post-table';
}

function controlNetStageStatusValid(status, tableMask, fusedClass, postFusion) {
  if (tableMask !== 0) return status === 0;
  if (!postFusion) return status >= 1 && status <= 3;
  return fusedClass === 3 ? status >= 1 && status <= 3 : status === 4;
}

function controlNetStageLabel(report, compact = false) {
  if (controlNetInputStage(report) === 'post-fusion') return compact ? '03 后分层控制网' : '03X · 分层控制网';
  if (controlNetInputStage(report) === 'post-layering') return compact ? '禁飞区前控制网' : '01E · 分层后控制网';
  return compact ? '台面后控制网' : '01B-X · 台面后控制网';
}

function controlNetFitLayerId(item) {
  return Number.isSafeInteger(item?.fitLayerId) && item.fitLayerId >= 0 && item.fitLayerId <= 3 ? item.fitLayerId : 0;
}

function controlNetHasSemanticLayers(report) {
  return Boolean(report && (Array.isArray(report.layers)
    || report.instances?.some((item) => Number.isSafeInteger(item?.fitLayerId) && item.fitLayerId >= 1 && item.fitLayerId <= 3)));
}

function controlNetLayerName(id, report = current?.controlNet) {
  return report?.layers?.find((layer) => Number(layer.id) === Number(id))?.name
    || ({0: '未归层', 1: '下层钢筋', 2: '上层钢筋', 3: '腹杆层'})[id]
    || `层 ${id}`;
}

function controlNetLayerColor(id) {
  return ({0: [.58, .64, .74], 1: [.23, .63, .98], 2: [.67, .42, .96], 3: [.98, .55, .18]})[id] || [.58, .64, .74];
}

function controlNetPointLayer(item, sharedLayer) {
  return controlNetFitLayerId(item) || ([1, 2, 3].includes(sharedLayer) ? sharedLayer : 0);
}

function controlNetPointMatchesLayer(selectedLayer, pointLayer, selectedItem, inSelectedNeighborhood) {
  if (selectedLayer === 'all' || pointLayer === Number(selectedLayer)) return true;
  return selectedLayer === '3' && selectedItem?.kind === 'web' && controlNetFitLayerId(selectedItem) === 3
    && inSelectedNeighborhood;
}

async function loadControlNetPreview(manifest, fetcher = fetchBytes) {
  const report = manifest.controlNet;
  if (!report) return null;
  const count = manifest.preview?.pointCount;
  const urls = ['control_status', 'control_instance'].map((name) => controlPreviewUrl(manifest.preview, name));
  if (!Number.isSafeInteger(count) || count < 0 || urls.some((url) => !url)) throw new Error('控制网预览缺少状态或设计单元编号');
  const [status, instance] = await Promise.all([
    fetcher(urls[0]).then((bytes) => new Uint8Array(bytes)),
    fetcher(urls[1]).then((bytes) => new Uint32Array(bytes)),
  ]);
  if (status.length !== count || instance.length !== count) throw new Error('控制网预览长度与 positions 不一致');
  if (typeof report.version !== 'string' || !report.version || !['aligned', 'auto'].includes(report.mode) || !report.inputPolicy || typeof report.registration?.method !== 'string' || !report.registration.method
    || (report.inputStage != null && !['post-table', 'post-layering', 'post-fusion'].includes(report.inputStage))
    || !report.counts || !Array.isArray(report.instances) || !report.inventory || !Array.isArray(report.inventory.units)
    || !Array.isArray(report.warnings) || report.warnings.some((warning) => typeof warning !== 'string')
    || !Number.isFinite(report.elapsedS) || report.elapsedS < 0) {
    throw new Error('控制网报告缺少模式、输入策略、配准、清单或警告');
  }
  const countNames = ['input', 'table', 'matched', 'pending', 'removed', 'designUnits', 'fittedUnits', 'designBars'];
  const excluded = report.counts.excluded ?? 0;
  if (countNames.some((name) => !Number.isSafeInteger(report.counts[name]) || report.counts[name] < 0)
    || !Number.isSafeInteger(excluded) || excluded < 0
    || report.counts.input !== report.counts.table + report.counts.matched + report.counts.pending + report.counts.removed + excluded
    || report.counts.designUnits !== report.inventory.units.length
    || (Number.isSafeInteger(manifest.source?.pointCount) && report.counts.input !== manifest.source.pointCount)) throw new Error('控制网报告计数无效或不守恒');
  if (report.layers != null && (!Array.isArray(report.layers) || report.layers.some((layer) => ![1, 2, 3].includes(layer?.id)
    || typeof layer.name !== 'string' || !layer.name.trim()
    || ['inputPoints', 'matched', 'pending', 'designUnits', 'fittedUnits'].some((name) => !Number.isSafeInteger(layer[name]) || layer[name] < 0)
    || !Number.isFinite(layer.elapsedS) || layer.elapsedS < 0)
    || new Set(report.layers.map((layer) => layer.id)).size !== report.layers.length)) {
    throw new Error('控制网分层统计无效');
  }
  const reportVersion = Number(report.version.match(/^design-control-net-v(\d+)$/)?.[1]) || 0;
  const lengthEvidenceValid = (item) => {
    const extended = item?.lengthCheck === 'extended-observed-span';
    if (!extended) return item?.lengthEvidence == null;
    const evidence = item.lengthEvidence;
    const startShift = evidence?.endpointShiftStartM ?? -evidence?.extensionStartM;
    const endShift = evidence?.endpointShiftEndM ?? evidence?.extensionEndM;
    return reportVersion >= 15 && item.kind === 'short' && item.status === 'fitted'
      && Number.isFinite(item.fittedLengthM) && item.fittedLengthM > item.designLengthM
      && evidence && typeof evidence.method === 'string' && evidence.method.trim()
      && Number.isFinite(evidence.extensionStartM) && evidence.extensionStartM >= 0
      && Number.isFinite(evidence.extensionEndM) && evidence.extensionEndM >= 0
      && Array.isArray(evidence.observedRangeM) && evidence.observedRangeM.length === 2
      && evidence.observedRangeM.every(Number.isFinite) && evidence.observedRangeM[1] > evidence.observedRangeM[0]
      && Number.isSafeInteger(evidence.supportPoints) && evidence.supportPoints > 0
      && Number.isFinite(evidence.searchMarginM) && evidence.searchMarginM > 0
      && Number.isFinite(startShift) && Number.isFinite(endShift)
      && Math.abs(evidence.extensionStartM - Math.max(0, -startShift)) <= 1e-6
      && Math.abs(evidence.extensionEndM - Math.max(0, endShift)) <= 1e-6
      && Math.abs(item.fittedLengthM - item.designLengthM
        - endShift + startShift) <= 1e-6;
  };
  const ownershipStages = report?.policy?.ownershipStages;
  if (reportVersion >= 15 && (typeof report?.policy?.ownershipPolicy !== 'string' || !report.policy.ownershipPolicy.trim()
    || !Array.isArray(ownershipStages) || ownershipStages.some((stage) => !['straight', 'short', 'web'].includes(stage?.kind)
      || ['designUnits', 'fittedUnits', 'lockedPoints'].some((name) => !Number.isSafeInteger(stage[name]) || stage[name] < 0)
      || stage.fittedUnits > stage.designUnits || !Number.isFinite(stage.elapsedS) || stage.elapsedS < 0)
    || new Set(ownershipStages.map((stage) => stage.kind)).size !== ownershipStages.length
    || ownershipStages.some((stage, index) => index > 0
      && ['straight', 'short', 'web'].indexOf(stage.kind) <= ['straight', 'short', 'web'].indexOf(ownershipStages[index - 1].kind)))) {
    throw new Error('控制网分阶段归属策略无效');
  }
  if (reportVersion >= 15 && (!Number.isSafeInteger(report.counts.extendedShortUnits) || report.counts.extendedShortUnits < 0)) {
    throw new Error('控制网短筋延长计数无效');
  }
  const unitKeys = new Set();
  for (const unit of report.inventory.units) {
    const key = String(unit?.designUnitId ?? '');
    if (!key || unitKeys.has(key) || !String(unit?.designBarId ?? '') || !controlNetVec3(unit.startM) || !controlNetVec3(unit.endM)
      || !controlNetVec3(unit.direction) || !Number.isFinite(unit.lengthM) || unit.lengthM <= 0
      || !Number.isFinite(unit.diameterM) || unit.diameterM <= 0 || !['straight', 'short', 'web'].includes(unit.kind)) {
      throw new Error('控制网初始化清单包含无效或重复的设计单元');
    }
    unitKeys.add(key);
  }
  const ids = new Set();
  const unitByKey = new Map(report.inventory.units.map((unit) => [String(unit.designUnitId), unit]));
  for (let index = 0; index < report.instances.length; index += 1) {
    const item = report.instances[index];
    const fitted = item?.status === 'fitted';
    const centerline = item?.centerlineM;
    const unit = unitByKey.get(String(item?.designUnitId ?? ''));
    if (!Number.isSafeInteger(item?.id) || item.id !== index + 1 || ids.has(item.id)
      || !unit || String(report.inventory.units[index]?.designUnitId ?? '') !== String(item.designUnitId)
      || String(item.designBarId ?? '') !== String(unit.designBarId)
      || !['straight', 'short', 'web'].includes(item.kind) || !['fitted', 'pending', 'missing'].includes(item.status)
      || (item.fitLayerId != null && (!Number.isSafeInteger(item.fitLayerId) || item.fitLayerId < 0 || item.fitLayerId > 3))
      || (controlNetInputStage(report) !== 'post-table' && item.fitLayerId == null)
      || (item.reason !== null && typeof item.reason !== 'string') || !Number.isSafeInteger(item.pointCount) || item.pointCount < 0
      || !Number.isFinite(item.diameterM) || item.diameterM <= 0 || !Number.isFinite(item.designLengthM) || item.designLengthM <= 0
      || item.kind !== unit.kind || Math.abs(item.diameterM - unit.diameterM) > 1e-9 || Math.abs(item.designLengthM - unit.lengthM) > 1e-9
      || (item.observedLengthM != null && (!Number.isFinite(item.observedLengthM) || item.observedLengthM < 0))
      || (item.rmseM != null && (!Number.isFinite(item.rmseM) || item.rmseM < 0))
      || (item.fittedLengthM != null && (item.kind !== 'short' || !Number.isFinite(item.fittedLengthM)
        || item.fittedLengthM < item.designLengthM || item.fittedLengthM > 1.5 * item.designLengthM))
      || (reportVersion >= 8 && reportVersion < 15 && item.fittedLengthM != null
        && Math.abs(item.fittedLengthM-item.designLengthM) > 1e-9)
      || (item.lengthCheck != null && (item.kind !== 'short'
        || !['consistent-visible-span', 'review-observed-span', 'extended-observed-span'].includes(item.lengthCheck)))
      || !lengthEvidenceValid(item)
      || !Array.isArray(centerline) || centerline.some((point) => !controlNetVec3(point))
      || (item.bodyDisplayCenterlineM != null && (!fitted || !Array.isArray(item.bodyDisplayCenterlineM)
        || item.bodyDisplayCenterlineM.length < 2 || item.bodyDisplayCenterlineM.some((point) => !controlNetVec3(point))))
      || (fitted ? item.pointCount < 1 || !Number.isFinite(item.observedLengthM) || item.observedLengthM <= 0 || !Number.isFinite(item.rmseM)
        || centerline.length < 2 || Math.abs(controlNetPolylineLength(centerline) - (item.fittedLengthM ?? item.designLengthM)) > 1e-6
        : centerline.length !== 0 || (item.status === 'missing' && item.pointCount !== 0))) {
      throw new Error('控制网实例包含无效身份、状态或设计长度几何');
    }
    ids.add(item.id);
    if (item.candidateCenterlineM != null && (item.status !== 'pending' || !Array.isArray(item.candidateCenterlineM)
      || item.candidateCenterlineM.length < 2 || item.candidateCenterlineM.some(point => !controlNetVec3(point))
      || Math.abs(controlNetPolylineLength(item.candidateCenterlineM)-(item.fittedLengthM ?? item.designLengthM)) > 1e-6)) {
      throw new Error('控制网候选轴线的状态或长度无效');
    }
  }
  validateControlNetCurvedPieces(report);
  const unitDesignBars = new Set(report.inventory.units.map((unit) => String(unit.designBarId)));
  const designBarCount = Array.isArray(report.inventory.bars) ? report.inventory.bars.length : unitDesignBars.size;
  if (report.counts.designUnits !== report.instances.length || report.counts.designBars !== designBarCount
    || report.instances.some((item) => !unitDesignBars.has(String(item.designBarId)))
    || report.counts.fittedUnits !== report.instances.filter((item) => item.status === 'fitted').length
    || status.some((value) => value > 4)
    || instance.some((id, index) => status[index] === 1 ? !ids.has(id) || report.instances[id - 1].status !== 'fitted' : id !== 0)) {
    throw new Error('控制网预览状态与设计单元身份不一致');
  }
  if (reportVersion >= 15 && report.counts.extendedShortUnits
    !== report.instances.filter((item) => item.lengthCheck === 'extended-observed-span').length) {
    throw new Error('控制网短筋延长计数与实例不一致');
  }
  return {status, instance, instanceById: new Map(report.instances.map((item) => [item.id, item]))};
}

function controlNetIdentityColor(value, salt = 0) {
  let hash = 2166136261 ^ salt;
  for (const character of String(value)) hash = Math.imul(hash ^ character.charCodeAt(0), 16777619);
  const color = new THREE.Color().setHSL(((hash >>> 0) % 360) / 360, .68, .57);
  return [color.r, color.g, color.b];
}

function clearControlNetOverlay() {
  if (!controlNetOverlay) return;
  controlNetScene.remove(controlNetOverlay);
  controlNetOverlay.traverse((object) => { if (object.isInstancedMesh) object.dispose(); object.geometry?.dispose?.(); object.material?.dispose?.(); });
  controlNetOverlay = null;
}

function createControlNetOverlay(report, origin, options = null) {
  const {selectedId = 'all', selectedLayer = 'all', colorMode = 'layers', showFit = true, showInitialization = false} = options || {};
  const group = new THREE.Group();
  const instanceByUnit = new Map(report.instances.map((item) => [String(item.designUnitId), item]));
  const colorFor = (item) => colorMode === 'parents'
    ? controlNetIdentityColor(item.designBarId, 29)
    : colorMode === 'layers' ? controlNetLayerColor(controlNetFitLayerId(item))
      : controlNetIdentityColor(item.id ?? item.designUnitId, 71);
  const visibleItem = (item) => (selectedId === 'all' || item.id === Number(selectedId))
    && (selectedLayer === 'all' || controlNetFitLayerId(item) === Number(selectedLayer));
  const itemById = new Map(report.instances.map((item) => [item.id, item]));
  const visiblePiece = (piece) => {
    const units = piece.unitIds.map((id) => itemById.get(id)).filter(Boolean);
    return selectedId === 'all'
      ? selectedLayer === 'all' || units.some((item) => controlNetFitLayerId(item) === Number(selectedLayer))
      : piece.unitIds.includes(Number(selectedId));
  };
  // A sampled centerline can contain hundreds of short segments. Submit its
  // cylinders together, rather than issuing two transparent draws per segment.
  // Keep each unit/piece separate so its metadata and filtering remain intact.
  const addTubes = (model, centerline, diameter, color, opacity) => {
    const segments = [];
    const offset = new THREE.Vector3(...origin);
    for (let index = 1; index < centerline.length; index += 1) {
      const a = new THREE.Vector3(...centerline[index - 1]).sub(offset);
      const b = new THREE.Vector3(...centerline[index]).sub(offset);
      const direction = b.clone().sub(a), length = direction.length();
      if (length > 1e-7) segments.push({a, b, direction, length});
    }
    if (!segments.length) return;
    const baseLength = segments[0].length;
    const geometry = new THREE.CylinderGeometry(diameter / 2, diameter / 2, baseLength, 12, 1, true);
    const material = new THREE.MeshBasicMaterial({color, transparent: true, opacity, depthWrite: false, side: THREE.DoubleSide});
    const tubes = new THREE.InstancedMesh(geometry, material, segments.length);
    const transform = new THREE.Object3D();
    const up = new THREE.Vector3(0, 1, 0);
    segments.forEach(({a, b, direction, length}, index) => {
      transform.position.copy(a).add(b).multiplyScalar(.5);
      transform.quaternion.setFromUnitVectors(up, direction.normalize());
      transform.scale.set(1, length / baseLength, 1);
      transform.updateMatrix();
      tubes.setMatrixAt(index, transform.matrix);
    });
    tubes.instanceMatrix.needsUpdate = true;
    tubes.computeBoundingBox();
    tubes.computeBoundingSphere();
    model.add(tubes);
  };
  const addCurve = (piece, centerline, kind, color, dashed = false, showTube = !dashed) => {
    const model = new THREE.Group();
    model.userData = {kind, pieceId: piece.id, status: piece.status, unitIds: [...piece.unitIds], designBarId: piece.designBarId};
    if (piece.connectionStatus) model.userData.connectionStatus = piece.connectionStatus;
    if (piece.inferenceMethod) model.userData.inferenceMethod = piece.inferenceMethod;
    const points = centerline.flatMap((point) => point.map((value, axis) => value - origin[axis]));
    const geometry = new THREE.BufferGeometry(); geometry.setAttribute('position', new THREE.Float32BufferAttribute(points, 3));
    const material = dashed
      ? new THREE.LineDashedMaterial({color, dashSize: .01, gapSize: .006, transparent: true, opacity: .85, depthTest: false})
      : new THREE.LineBasicMaterial({color});
    const line = new THREE.Line(geometry, material);
    if (dashed) line.computeLineDistances();
    model.add(line);
    if (showTube) addTubes(model, centerline, piece.diameterM, color, dashed ? .1 : .16);
    group.add(model);
  };
  if (showInitialization) {
    for (const unit of report.inventory.units) {
      const item = instanceByUnit.get(String(unit.designUnitId));
      if (!item || !visibleItem(item)) continue;
      const points = [unit.startM, unit.endM].flatMap((point) => point.map((value, axis) => value - origin[axis]));
      const geometry = new THREE.BufferGeometry(); geometry.setAttribute('position', new THREE.Float32BufferAttribute(points, 3));
      const material = new THREE.LineDashedMaterial({color: new THREE.Color().setRGB(...colorFor(item)), dashSize: .02, gapSize: .01, transparent: true, opacity: .7});
      const line = new THREE.Line(geometry, material); line.computeLineDistances(); line.userData = {kind: 'initialization', instanceId: item.id, designUnitId: item.designUnitId, designBarId: item.designBarId};
      group.add(line);
    }
    for (const piece of report.curvedPieces || []) {
      if (!piece.designCenterlineM?.length || !visiblePiece(piece)) continue;
      if (showFit && piece.connectionStatus === 'design-inferred') continue;
      addCurve(piece, piece.designCenterlineM, piece.status === 'pending' ? 'curve-design-pending' : 'curve-design',
        piece.status === 'pending' ? 0x38bdf8 : 0x94a3b8, true);
    }
  }
  if (showFit) {
    for (const item of report.instances) {
      if (item.status === 'pending' && item.candidateCenterlineM?.length >= 2 && visibleItem(item)) {
        const positions = item.candidateCenterlineM.flatMap(point => point.map((value,axis) => value-origin[axis]));
        const geometry = new THREE.BufferGeometry(); geometry.setAttribute('position', new THREE.Float32BufferAttribute(positions,3));
        const line = new THREE.Line(geometry,new THREE.LineDashedMaterial({color:0xfbbf24,dashSize:.012,gapSize:.006,depthTest:false}));
        line.computeLineDistances(); line.userData = {kind:'candidate', instanceId:item.id}; group.add(line);
      }
      if (item.status !== 'fitted' || !visibleItem(item)) continue;
      const model = new THREE.Group();
      model.userData = {kind: 'fit', instanceId: item.id, designUnitId: item.designUnitId, designBarId: item.designBarId};
      const color = new THREE.Color(0xff8a2b); // Fit overlay stays distinct from blue scan points.
      const bodyCenterline = item.bodyDisplayCenterlineM || item.centerlineM;
      const points = bodyCenterline.flatMap((point) => point.map((value, axis) => value - origin[axis]));
      const geometry = new THREE.BufferGeometry(); geometry.setAttribute('position', new THREE.Float32BufferAttribute(points, 3));
      model.add(new THREE.Line(geometry, new THREE.LineBasicMaterial({color})));
      addTubes(model, bodyCenterline, item.diameterM, color, .16);
      group.add(model);
    }
    for (const piece of report.curvedPieces || []) {
      if (piece.status !== 'fitted' || !visiblePiece(piece)) continue;
      addCurve(piece, piece.centerlineM, 'curve-fit', 0xff8a2b);
    }
    for (const piece of report.curvedPieces || []) {
      if (piece.connectionStatus !== 'design-inferred' || !visiblePiece(piece)) continue;
      addCurve(piece, piece.inferredCenterlineM, 'curve-design-inferred', 0x22d3ee, true, true);
      for (const [low, high] of piece.localSupport?.intervalsM || []) {
        const clipped = []; let station = 0;
        const curve = piece.inferredCenterlineM;
        for (let i = 1; i < curve.length; i++) {
          const a = curve[i-1], b = curve[i], length = Math.hypot(...b.map((v,j) => v-a[j]));
          const left = Math.max(low, station), right = Math.min(high, station+length);
          if (length > 1e-12 && right > left) {
            const at = s => a.map((v,j) => v+(b[j]-v)*(s-station)/length);
            if (!clipped.length) clipped.push(at(left));
            clipped.push(at(right));
          }
          station += length;
        }
        if (clipped.length >= 2) addCurve(piece, clipped, 'curve-local-supported', 0xff8a2b);
      }
    }
  }
  return group;
}

function rebuildControlNetOverlay() {
  clearControlNetOverlay();
  if (!current?.controlNet) return;
  controlNetOverlay = createControlNetOverlay(current.controlNet, current.preview.origin || [0, 0, 0], {
    selectedId: $('controlNetUnitFilter').value,
    selectedLayer: $('controlNetLayerFilter').value,
    colorMode: $('controlNetColorMode').value,
    showFit: $('controlNetFitOverlay').checked,
    showInitialization: $('controlNetInitializationOverlay').checked,
  });
  controlNetScene.add(controlNetOverlay);
}

function rebuildControlNetUnitOptions() {
  const previous = $('controlNetUnitFilter').value;
  const selectedLayer = $('controlNetLayerFilter').value;
  const instances = (current?.controlNet?.instances || []).filter((item) => selectedLayer === 'all'
    || controlNetFitLayerId(item) === Number(selectedLayer));
  const parents = new Map((current?.controlNet?.inventory?.bars || []).map(bar => [bar.designBarId,bar.name || bar.ifcGlobalId || bar.designBarId]));
  $('controlNetUnitFilter').replaceChildren(...[['all', '全部设计单元'], ...instances.map((item) => [String(item.id),
    `#${item.id} · ${controlNetHasSemanticLayers(current?.controlNet) ? `${controlNetLayerName(controlNetFitLayerId(item))} · ` : ''}母筋 ${parents.get(item.designBarId) || item.designBarId} · ${item.status === 'fitted' ? `${item.lengthCheck === 'review-observed-span' ? '已匹配 · 长度待核查' : item.lengthCheck === 'extended-observed-span' ? '已拟合 · 实测延长' : '已拟合'} · RMSE ${fmtHeight(item.rmseM)}` : item.status === 'pending' ? '待定' : '未拟合'}`])]
    .map(([value, label]) => new Option(label, value)));
  $('controlNetUnitFilter').value = instances.some((item) => String(item.id) === previous) ? previous : 'all';
}

function installControlNetPreview() {
  $('controlNetStep').disabled = !current?._controlNet;
  $('controlNetMode').value = current?.controlNet?.mode || 'off';
  updateControlNetRunMode();
  for (const [id, key] of [['controlNetSteelLas', 'controlNetSteelLasUrl'], ['controlNetPendingLas', 'controlNetPendingLasUrl'], ['controlNetRemovedLas', 'controlNetRemovedLasUrl'], ['controlNetExcludedLas', 'controlNetExcludedLasUrl'], ['controlNetReport', 'controlNetReportUrl']]) {
    $(id).hidden = !current?.files?.[key]; $(id).href = current?.files?.[key] || '#';
  }
  const controlStep = $('controlNetStep');
  if (!current?._controlNet) {
    controlStep.innerHTML = '<span class="num">01B-X</span>台面后控制网';
    $('tableRemovalStep').after(controlStep);
    return;
  }
  controlNetGeometry = new THREE.BufferGeometry();
  controlNetGeometry.setAttribute('position', new THREE.BufferAttribute(current._positions, 3));
  controlNetGeometry.boundingSphere = rawGeometry?.boundingSphere?.clone() || null;
  if (!controlNetGeometry.boundingSphere) controlNetGeometry.computeBoundingSphere();
  controlNetPoints.geometry = controlNetGeometry;
  const report = current.controlNet, counts = report.counts;
  const inputStage = controlNetInputStage(report);
  $('controlNetHeading').textContent = controlNetStageLabel(report);
  controlStep.innerHTML = inputStage === 'post-fusion'
    ? '<span class="num">03X</span>分层控制网'
    : inputStage === 'post-layering' ? '<span class="num">01E</span>分层后控制网'
      : '<span class="num">01B-X</span>台面后控制网';
  (inputStage === 'post-fusion' ? $('fusionStep') : inputStage === 'post-layering' ? $('floatingZonesStep') : $('tableRemovalStep')).after(controlStep);
  const sourceOption = Array.from($('controlNetView').options).find((option) => option.value === 'source');
  if (sourceOption) sourceOption.textContent = inputStage === 'post-fusion' ? '03 融合后的全部候选' : inputStage === 'post-layering' ? '分层后全部非台面点（禁飞区前）' : '台面移除后的原始候选';
  const excludedOption = Array.from($('controlNetView').options).find(option => option.value === 'excluded');
  if (excludedOption) excludedOption.disabled = inputStage !== 'post-fusion';
  $('controlNetExcludedLas').hidden = inputStage !== 'post-fusion' || !current?.files?.controlNetExcludedLasUrl;
  if (inputStage !== 'post-fusion' && $('controlNetView').value === 'excluded') $('controlNetView').value = 'source';
  const semanticLayersAvailable = controlNetHasSemanticLayers(report);
  $('controlNetLayerFilter').disabled = !semanticLayersAvailable;
  $('controlNetLayerFilter').hidden = !semanticLayersAvailable;
  document.querySelector('label[for="controlNetLayerFilter"]').hidden = !semanticLayersAvailable;
  const layerColorOption = Array.from($('controlNetColorMode').options).find((option) => option.value === 'layers');
  if (layerColorOption) { layerColorOption.disabled = !semanticLayersAvailable; layerColorOption.hidden = !semanticLayersAvailable; }
  if (!semanticLayersAvailable) {
    $('controlNetLayerFilter').value = 'all';
    if ($('controlNetColorMode').value === 'layers') $('controlNetColorMode').value = 'units';
  }
  rebuildControlNetUnitOptions();
  $('controlNetInitializationOverlay').checked = counts.fittedUnits === 0;
  const mode = report.mode === 'auto' ? '自动全局初始化' : '粗对齐初始化';
  const fittedRmse = report.instances.filter((item) => item.status === 'fitted').map((item) => item.rmseM).sort((a, b) => a - b);
  const medianRmse = fittedRmse.length ? fittedRmse[Math.floor(fittedRmse.length / 2)] : null;
  const curveStats = controlNetCurveStats(report);
  $('controlNetSummary').replaceChildren(...[
    ['初始化', mode],
    ['位姿 / 编号判断', `${report.registration.status === 'provided' ? '沿用保存的粗配准' : report.registration.status === 'supported' ? '有几何支持' : '几何支持不足'} / ${report.registration.identityStatus === 'ambiguous' ? '存在歧义，保留待定' : report.registration.identityStatus === 'unresolved' ? '尚未确定' : report.mode === 'aligned' ? '以已有粗配准为起点' : '当前候选通过'}`],
    ...(report.mode === 'auto' ? [['线特征 / 一一对应 / 近似候选', `${fmt(report.registration.featureLines)} / ${fmt(report.registration.matchedUnits)} / ${fmt(report.registration.alternatives?.length || 0)}`]] : []),
    [inputStage === 'post-fusion' ? '输入 / 03 融合候选' : inputStage === 'post-layering' ? '输入 / 分层后候选' : '输入 / 台面后候选', `${fmt(counts.input)} / ${fmt(counts.input - counts.table - (counts.excluded ?? 0))}`],
    ['支持 / 待定 / 局部离群 / 融合排除', `${fmt(counts.matched)} / ${fmt(counts.pending)} / ${fmt(counts.removed)} / ${fmt(counts.excluded ?? 0)}`],
    ...(counts.lengthReviewUnits ? [['短筋长度待核查', `${fmt(counts.lengthReviewUnits)} 根（模型保持设计长度）`]] : []),
    ...(Number.isSafeInteger(counts.extendedShortUnits) ? [['实测延长短筋', `${fmt(counts.extendedShortUnits)} 根`]] : []),
    ['拟合单元 / 设计单元 / 物理母筋', `${fmt(counts.fittedUnits)} / ${fmt(counts.designUnits)} / ${fmt(counts.designBars)}`],
    ...(counts.surfaceResampledUnits ? [['管面复核修正', `${fmt(counts.surfaceResampledUnits)} 根`]] : []),
    ...(controlNetOwnershipSummary(report) ? [['分阶段归属（拟合 / 设计）', controlNetOwnershipSummary(report)]] : []),
    ...(report.curvedPieces ? [
      ['弯段：点云确认 / 未恢复 / 总数', `${fmt(curveStats.fittedPieces)} / ${fmt(curveStats.unresolvedPieces)} / ${fmt(curveStats.designPieces)}`],
      ...(curveStats.inferredPieces ? [['补接弯段（含推断）', `${fmt(curveStats.inferredPieces)} 段 · ${fmtHeight(curveStats.inferredLengthM)}`]] : []),
      ...(curveStats.locallyMatchedPoints ? [['补接中的局部确认', `${fmt(curveStats.locallySupportedPieces)} 段 · ${fmt(curveStats.locallyMatchedPoints)} 点`]] : []),
      ['弯曲段支持点', fmt(curveStats.matchedPoints)],
      ['弯曲段拟合 / 设计长度', `${fmtHeight(curveStats.fittedLengthM)} / ${fmtHeight(curveStats.designLengthM)}`],
    ] : []),
    ...(counts.candidateFittedUnits ? [['候选轴线（编号未确认）', fmt(counts.candidateFittedUnits)]] : []),
    ['拟合 RMSE 中位 / 最大', `${fmtHeight(medianRmse)} / ${fmtHeight(fittedRmse.at(-1))}`],
    ...(report.layers || []).map((layer) => [controlNetLayerName(layer.id, report), `${fmt(layer.inputPoints)} 点 · 支持 ${fmt(layer.matched)} · 待定 ${fmt(layer.pending)} · 单元 ${fmt(layer.fittedUnits)} / ${fmt(layer.designUnits)} · ${fmt(layer.elapsedS, ' s')}`]),
    ['耗时', fmt(report.elapsedS, ' s')],
  ].map(([name, value]) => { const row = document.createElement('span'); const label = document.createElement('b'); const output = document.createElement('i'); label.textContent = name; output.textContent = value; row.append(label, output); return row; }));
  $('controlNetWarnings').replaceChildren(...report.warnings.map((warning) => { const item = document.createElement('li'); item.textContent = controlNetReason(warning); return item; }));
}

function controlNetReason(reason) {
  const reasons = {'fixed-radius-surface-supported':'固定直径拟合有表面支持', 'ambiguous-parallel-support':'多个近似圆柱候选尚无法区分，暂不强行编号',
    'relocated-short-supported':'已从未归属点中找回错位短筋；位置匹配不代表长度已验收',
    'ambiguous-short-recovery':'找到多根近似短筋，候选身份仍需确认',
    'no-unclaimed-short-support':'扩大搜索后仍缺少独立的短筋证据',
    'ambiguous-planar-support':'截面曲率不足，可能为平面夹具', 'ambiguous-planar-normals':'法向变化不足，暂不能确认圆柱表面',
    'short-interval-ambiguous':'短筋所在区间与设计不一致，保留待定', 'ambiguous-design-support':'几何或身份有歧义',
    'inconsistent-design-radius':'扫描截面与设计半径支持不足',
    'insufficient-continuous-curved-surface':'弯段缺少连续的曲面观测',
    'unstable-held-out-curve':'弯段在分组复拟合中不稳定',
    'unobserved-terminal-leg':'弯钩尾段缺少分布充分的侧面观测',
    'unsupported-or-inconsistent-attachment':'现有主体姿态无法可靠连接该设计弯段',
    'insufficient-unique-curve-support':'弯段缺少可唯一归属的支持点',
    'ambiguous-curve-support':'存在形状不同但支持相近的弯段候选',
    'insufficient-curve-validation':'弯段缺少独立检查所需的观测',
    'self-overlapping-bend':'弯曲半径过小，表面可能自交',
    'analytic-design-required':'缺少可用的精确设计弯曲几何',
    'body-not-supported':'相邻主体尚未确认',
    'automatic global pose has insufficient geometric support':'自动位姿的几何支持不足',
    'competing or insufficient global pose evidence; candidate axes are not confirmed identities':'存在相近的位姿候选；橙色虚线仅为候选轴线，编号尚未确认'};
  return reasons[reason] || String(reason || '').replace(/^(\d+) design units remain pending or missing$/, '$1 个设计单元待定或未拟合').replace(/^(\d+) short bars have discrepant visible spans; design length retained, endpoints require review$/, '$1 根短筋观测跨度与设计不符：模型保持设计长度，端点需核查');
}

function controlNetAxisLabel(item) {
  const labels = {'evidence-selected-straight': '截面证据优先选择直线。',
    'evidence-selected-bow': '截面证据支持简单弧形。',
    'supported-spline': '连续截面支持样条弯曲；证据范围外按端部切线延伸。'};
  const refined = item.axisRefinement?.method === 'held-out-full-surface-end-correction'
    ? '端部已根据独立管面证据回正，保留可靠主体与设计总长。'
    : item.axisRefinement?.method === 'held-out-full-surface-short-pose'
      ? '短筋位置与倾斜已通过独立管面证据修正。' : '';
  return (refined || labels[item.axisModel] || '') + (item.webEvidence?.method === 'radial-normal-ambiguity-resolution'
    ? '初始圆柱候选存在歧义，经表面法向补充判别后通过拟合检查。' : '')
    + (item.recoveryEvidence?.method === 'full-source-finite-interval'
      ? '错位恢复已用完整局部点云复核有限长度。' : '')
    + (item.surfaceReview?.method === 'held-out-long-body-axis'
      ? '局部弯曲已根据独立管面证据修正，保留设计直径与总长。' : '');
}

function controlNetShortReviewUnits(report) {
  return (report?.instances || []).filter(item => item.kind === 'short'
    && (item.status !== 'fitted' || item.fitCandidateScope === 'unclaimed-short-pose-search'
      || item.lengthCheck === 'extended-observed-span'));
}

function focusControlNetShort() {
  const items = controlNetShortReviewUnits(current?.controlNet);
  if (!items.length) { setStatus('当前没有延长、错位找回或未拟合的短筋'); return; }
  const index = items.findIndex(item => String(item.id) === $('controlNetUnitFilter').value);
  $('controlNetLayerFilter').value = 'all';
  rebuildControlNetUnitOptions();
  $('controlNetUnitFilter').value = String(items[(index + 1) % items.length].id);
  $('controlNetView').value = 'source';
  $('controlNetFitOverlay').checked = true;
  $('controlNetInitializationOverlay').checked = true;
  if ($('compare').checked) {
    $('compare').checked = false;
    $('compare').dispatchEvent(new Event('change'));
  }
  applyControlNetAppearance();
  fit('top');
}

function controlNetLocalPredicate(item, unit, origin) {
  const curve = item?.bodyDisplayCenterlineM?.length >= 2 ? item.bodyDisplayCenterlineM
    : item?.centerlineM?.length >= 2 ? item.centerlineM : item?.candidateCenterlineM?.length >= 2 ? item.candidateCenterlineM : [unit.startM, unit.endM];
  const radius = Math.max(.04, unit.diameterM * 6);
  const lo = [Infinity, Infinity, Infinity], hi = [-Infinity, -Infinity, -Infinity];
  const points = curve.map(point => point.map((value, axis) => {
    const v = value - origin[axis]; lo[axis] = Math.min(lo[axis], v-radius); hi[axis] = Math.max(hi[axis], v+radius); return v;
  }));
  const segments = points.slice(1).map((end, i) => {
    const start = points[i], delta = end.map((v, a) => v-start[a]);
    return {start, delta, length2: delta.reduce((sum,v) => sum+v*v,0)};
  });
  return (positions, index) => {
    const x=positions[3*index], y=positions[3*index+1], z=positions[3*index+2];
    if (x<lo[0] || x>hi[0] || y<lo[1] || y>hi[1] || z<lo[2] || z>hi[2]) return false;
    for (const {start, delta, length2} of segments) {
      const dx=x-start[0], dy=y-start[1], dz=z-start[2];
      const t=Math.max(0,Math.min(1,(dx*delta[0]+dy*delta[1]+dz*delta[2])/Math.max(length2,1e-18)));
      if ((dx-t*delta[0])**2+(dy-t*delta[1])**2+(dz-t*delta[2])**2<=radius*radius) return true;
    }
    return false;
  };
}

function controlNetCurveLocalPredicate(pieces, origin) {
  const paths = pieces.flatMap((piece) => {
    const curve = piece.status === 'fitted' ? piece.centerlineM
      : piece.connectionStatus === 'design-inferred' ? piece.inferredCenterlineM : piece.designCenterlineM;
    if (!curve?.length) return [];
    const radius = Math.max(.02, piece.diameterM * 4);
    return curve.slice(1).map((end, index) => {
      const start = curve[index].map((value, axis) => value - origin[axis]);
      const finish = end.map((value, axis) => value - origin[axis]);
      const delta = finish.map((value, axis) => value - start[axis]);
      return {start, delta, radius2: radius * radius, length2: delta.reduce((sum, value) => sum + value * value, 0)};
    });
  });
  return (positions, index) => paths.some(({start, delta, radius2, length2}) => {
    const dx = positions[3 * index] - start[0], dy = positions[3 * index + 1] - start[1], dz = positions[3 * index + 2] - start[2];
    const t = Math.max(0, Math.min(1, (dx * delta[0] + dy * delta[1] + dz * delta[2]) / Math.max(length2, 1e-18)));
    return (dx - t * delta[0]) ** 2 + (dy - t * delta[1]) ** 2 + (dz - t * delta[2]) ** 2 <= radius2;
  });
}

function applyControlNetAppearance() {
  if (!controlNetGeometry || !current?._controlNet) return;
  const {status, instance, instanceById} = current._controlNet;
  const view = $('controlNetView').value, colorMode = $('controlNetColorMode').value, selectedId = $('controlNetUnitFilter').value;
  const selectedLayer = $('controlNetLayerFilter').value;
  const selectedItem = selectedId === 'all' ? null : instanceById.get(Number(selectedId));
  const selectedCurves = selectedItem ? controlNetCurvedPiecesForUnit(current.controlNet, selectedItem.id) : [];
  const nearby = selectedItem && view !== 'steel' ? controlNetLocalPredicate(selectedItem,
    current.controlNet.inventory.units[selectedItem.id-1], current.preview.origin || [0,0,0]) : null;
  const curveNearby = selectedCurves.length ? controlNetCurveLocalPredicate(selectedCurves, current.preview.origin || [0,0,0]) : null;
  const rawColors = rawGeometry.getAttribute('color').array;
  const colors = new Float32Array(status.length * 3), selected = new Uint32Array(status.length); let size = 0;
  const statusColors = [[.39,.45,.55], [.18,.83,.68], [.98,.76,.17], [.94,.28,.43], [.49,.36,.66]];
  for (let index = 0; index < status.length; index += 1) {
    const code = status[index], id = instance[index], item = instanceById.get(id);
    const matchesView = view === 'source' ? code > 0 && code < 4 : view === 'steel' ? code === 1 : view === 'pending' ? code === 2 : view === 'removed' ? code === 3 : code === 4;
    const matchesUnit = selectedId === 'all' || id === Number(selectedId)
      || Boolean(curveNearby?.(current._positions, index)) || Boolean(nearby?.(current._positions, index));
    const pointLayer = controlNetPointLayer(item, current._sharedLayers?.[index]);
    const matchesLayer = controlNetPointMatchesLayer(selectedLayer, pointLayer, selectedItem,
      Boolean(nearby?.(current._positions, index)));
    if (!matchesView || !matchesUnit || !matchesLayer) continue;
    selected[size++] = index;
    const color = colorMode === 'units' && item ? controlNetIdentityColor(id, 71)
        : colorMode === 'parents' && item ? controlNetIdentityColor(item.designBarId, 29)
          : colorMode === 'layers' ? controlNetLayerColor(pointLayer)
            : colorMode === 'status' ? statusColors[code]
              : [rawColors[index * 3] / 255, rawColors[index * 3 + 1] / 255, rawColors[index * 3 + 2] / 255];
    colors.set(color, index * 3);
  }
  controlNetGeometry.setAttribute('color', new THREE.BufferAttribute(colors, 3));
  controlNetGeometry.setIndex(new THREE.BufferAttribute(selected.subarray(0, size), 1)); controlNetGeometry.setDrawRange(0, size);
  if (controlNetInputStage(current.controlNet) !== 'post-fusion') {
    const sourceIndices = new Uint32Array(status.length); let sourceSize = 0;
    for (let index = 0; index < status.length; index += 1) if (status[index] !== 0) sourceIndices[sourceSize++] = index;
    tableRemovalGeometry.setAttribute('color', rawGeometry.getAttribute('color'));
    tableRemovalGeometry.setIndex(new THREE.BufferAttribute(sourceIndices.subarray(0, sourceSize), 1)); tableRemovalGeometry.setDrawRange(0, sourceSize);
  }
  rebuildControlNetOverlay();
  const selectedCurveStats = selectedItem ? controlNetCurveStats(current.controlNet, selectedItem.id) : null;
  const hookDetail = selectedCurves.filter(piece => piece.terminalParameters).map(piece => {
    const p = piece.terminalParameters;
    const deg = v => (v * 180 / Math.PI).toFixed(1);
    const lengthSource = piece.terminalLengthEvidence?.physicalEndObserved ? '端面定位'
      : p.tailExtentSource === 'continuous-scan-support' ? '侧面观测，端面未确认' : '设计长度';
    return `弯钩参数：平面旋转 ${deg(p.rollRad)}°，上翘 ${deg(p.pitchRad)}°，偏转 ${deg(p.yawRad)}°，半径 ${(p.radiusM*1000).toFixed(1)} mm，转角 ${deg(p.sweepRad)}°，尾腿 ${(p.tailLengthM*1000).toFixed(1)} mm（${lengthSource}）。`;
  }).join('');
  const curveDetail = selectedCurveStats?.designPieces
    ? `相邻弯曲段 ${fmt(selectedCurveStats.designPieces)} 段（点云确认 ${fmt(selectedCurveStats.fittedPieces)} / 补接 ${fmt(selectedCurveStats.inferredPieces)} / 未恢复 ${fmt(selectedCurveStats.unresolvedPieces)}）· 曲段新增支持 ${fmt(selectedCurveStats.matchedPoints)} 点 · 拟合曲线总长 ${fmtHeight(selectedCurveStats.fittedLengthM)}，设计弯段长 ${fmtHeight(selectedCurveStats.designLengthM)}。${selectedCurveStats.inferredPieces ? `其中 ${fmt(selectedCurveStats.inferredPieces)} 段补接（${fmtHeight(selectedCurveStats.inferredLengthM)}），其中 ${fmt(selectedCurveStats.scanGuidedPieces)} 段由观测调整参数；缺测部分仍依赖设计；局部确认 ${fmt(selectedCurveStats.locallyMatchedPoints)} 点已归属，整段仍不计入确认拟合长度。` : ''}${selectedCurves.some(piece => piece.status === 'fitted' && piece.attachmentStatus === 'body-anchored-transition') ? '拟合总长包含接入主体的过渡段；遮挡处为模型插值，不能视作全长实测。' : ''}${selectedCurves.some(piece => piece.status === 'pending' && piece.connectionStatus !== 'design-inferred') ? `弯段待定原因：${[...new Set(selectedCurves.filter(piece => piece.status === 'pending' && piece.connectionStatus !== 'design-inferred').map(piece => controlNetReason(piece.reason)))].join('；')}。` : ''}`
    : '';
  $('controlNetHint').textContent = selectedItem
    ? `单元 #${selectedItem.id} · ${controlNetHasSemanticLayers(current.controlNet) ? `拟合层 ${controlNetLayerName(controlNetFitLayerId(selectedItem))} · ` : ''}母筋 ${current.controlNet.inventory.bars?.find(bar=>bar.designBarId===selectedItem.designBarId)?.name || selectedItem.designBarId} · ${selectedItem.status === 'fitted' ? (selectedItem.lengthCheck === 'review-observed-span' ? '位置已匹配，长度待核查' : selectedItem.lengthCheck === 'extended-observed-span' ? '拟合成功，端部延长有连续表面支持' : '拟合成功') : selectedItem.status === 'pending' ? '待定' : '未拟合'} · 单元支持 ${fmt(selectedItem.pointCount)} 点 · 主体 RMSE ${fmtHeight(selectedItem.rmseM)} · ${controlNetLengthObservation(selectedItem)}${curveDetail}${hookDetail}${selectedItem.displacementM ? `中心错位 ${fmtHeight(Math.hypot(...selectedItem.displacementM))}；主体显示长度 ${fmtHeight(controlNetPolylineLength(selectedItem.bodyDisplayCenterlineM || selectedItem.centerlineM))}。` : ''}${selectedItem.axisModel === 'fixed-length-straight-cylinder' ? '主体采用直线圆柱约束，端部弯曲单独显示。' : ''}${nearby || curveNearby ? `当前显示本单元及相邻弯曲段附近的点${selectedItem.kind === 'web' ? '，并保留腹杆端点附近的原始点' : ''}，不代表这些点均已归属本筋。` : ''}${selectedItem.lengthCheck === 'review-observed-span' ? '观测跨度不等于实测钢筋长度，可能包含邻筋或端部噪点；本模型保持设计长度。' : ''}${controlNetAxisLabel(selectedItem)}${controlNetReason(selectedItem.reason)}`
    : `显示 ${fmt(size)} 个样本点。亮橙色实线及圆柱表示已确认拟合的直段和弯曲段；${current.controlNet.curvedPieces?.some(piece => piece.connectionStatus === 'design-inferred') ? '青色虚线及半透明圆管表示含推断的补接，参数拟合与纯设计补接可在单元详情区分；' : ''}黄色虚线为待确认直段候选。初始化 / 设计开关中的蓝色虚线弯曲段仅为未确认设计曲线，不代表拟合成功。${controlNetOwnershipHint(current.controlNet) || (controlNetInputStage(current.controlNet) === 'post-fusion' ? '融合排除点单独查看，不混入完整候选。' : '输入为全部非台面原始点，尚未进行禁飞区划分或分类筛选。')}`;
  const legends = colorMode === 'parents' ? [['不同颜色', '物理母筋'], ['#facc15', '待定'], ['#ef476f', '局部离群']]
    : colorMode === 'units' ? [['不同颜色', '设计单元'], ['#facc15', '待定'], ['#ef476f', '局部离群']]
      : colorMode === 'layers' ? [['#3ba1fa', '下层钢筋'], ['#ab6bf5', '上层钢筋'], ['#fa8c2e', '腹杆层'], ['#949fba', '未归层']]
        : [['#2dd4bf', '支持钢筋'], ['#facc15', '待定 / 歧义'], ['#ef476f', '局部离群'], ['#7d5ca8', '融合排除']];
  if ($('controlNetFitOverlay').checked) legends.unshift(['#ff8a2b', '拟合轴线 / 圆柱']);
  if ($('controlNetFitOverlay').checked && current.controlNet.curvedPieces?.some((piece) => piece.connectionStatus === 'design-inferred')) legends.unshift(['#22d3ee', '补接，含缺测推断（虚线 / 半透明管）']);
  if ($('controlNetFitOverlay').checked && current.controlNet.curveSummary?.locallyMatchedPoints > 0) legends.unshift(['#ff8a2b', '补接中的局部确认管面']);
  if ($('controlNetInitializationOverlay').checked && current.controlNet.curvedPieces?.some((piece) => piece.status === 'pending'
    && piece.designCenterlineM && (!$('controlNetFitOverlay').checked || piece.connectionStatus !== 'design-inferred'))) {
    legends.unshift(['#38bdf8', '未确认设计弯曲线（未拟合）']);
  }
  $('controlNetLegend').replaceChildren(...legends.map(([color, label]) => { const row = document.createElement('span'); const swatch = document.createElement('i'); swatch.className = 'swatch'; swatch.style.background = color === '不同颜色' ? 'linear-gradient(90deg,#22c55e,#a78bfa,#f97316)' : color; row.append(swatch, label); return row; }));
  requestRender();
}

function cylinderPreviewUrl(preview, name) {
  return preview?.[`${name}Url`] || preview?.files?.[name] || null;
}

async function loadCylinderDenoisePreview(manifest, fetcher = fetchBytes) {
  const report = manifest.cylinderDenoise;
  if (!report) return null;
  if (!manifest.completeRebar) throw new Error('圆柱先验结果缺少第六步实例基线');
  const count = manifest.preview?.pointCount;
  const urls = ['cylinder_keep', 'cylinder_removed'].map((name) => cylinderPreviewUrl(manifest.preview, name));
  if (!Number.isSafeInteger(count) || count < 0 || urls.some((url) => !url)) throw new Error('圆柱先验预览缺少保留或移除掩码');
  const [keep, removed] = await Promise.all(urls.map(async (url) => new Uint8Array(await fetcher(url))));
  if (keep.length !== count || removed.length !== count) throw new Error('圆柱先验预览长度与 positions 不一致');
  if (keep.some((value) => value > 1) || removed.some((value) => value > 1)) throw new Error('圆柱先验掩码只能包含 0 或 1');
  if (!Array.isArray(report.instances) || !report.validation || typeof report.validation.status !== 'string') throw new Error('圆柱先验报告缺少实例或验证状态');
  const ids = new Set();
  for (const item of report.instances) {
    const hasCenterline = Array.isArray(item.centerlineM) && item.centerlineM.length >= 2
      && !item.centerlineM.some((point) => !Array.isArray(point) || point.length !== 3 || point.some((value) => !Number.isFinite(value)));
    if (!Number.isSafeInteger(item?.id) || item.id <= 0 || ids.has(item.id) || !Number.isFinite(item.pointsBefore) || !Number.isFinite(item.pointsAfter)
      || !Number.isFinite(item.removedPointCount) || !['applied', 'retained'].includes(item.status) || (item.reason !== null && typeof item.reason !== 'string')
      || (item.centerlineM !== null && !hasCenterline) || ((hasCenterline || item.status === 'applied') && (!hasCenterline || !Number.isFinite(item.radiusM) || item.radiusM <= 0))) throw new Error('圆柱先验实例报告无效');
    if (['instance-cylinder-denoise-v2', 'instance-cylinder-denoise-v3'].includes(report.version)) {
      if (item.fitStatus !== (hasCenterline ? 'fitted' : 'not_fitted')) throw new Error('圆柱拟合状态与几何不一致');
      if (hasCenterline) {
        const length = item.centerlineM.slice(1).reduce((sum, point, index) => sum + Math.hypot(...point.map((value, axis) => value - item.centerlineM[index][axis])), 0);
        const expectedShapeLength = report.version === 'instance-cylinder-denoise-v3' ? item.expectedShapeLengthM : item.expectedLengthM;
        if (!Number.isFinite(item.expectedLengthM) || item.expectedLengthM <= 0 || !Number.isFinite(expectedShapeLength) || expectedShapeLength <= 0 || !Number.isFinite(item.fittedLengthM)
          || Math.abs(length - expectedShapeLength) > 1e-8 || Math.abs(length - item.fittedLengthM) > 1e-8) throw new Error('拟合圆柱长度与设计长度不一致');
        if (report.version === 'instance-cylinder-denoise-v3') {
          if (!Number.isFinite(item.fittedStraightLengthM) || Math.abs(item.fittedStraightLengthM - item.expectedLengthM) > 1e-8) throw new Error('拟合直段长度与设计长度不一致');
          if (item.shapeKind === 'straight-with-bends') {
            const straight = item.straightCenterlineM;
            if (!Array.isArray(straight) || straight.length < 2 || straight.some(point => !Array.isArray(point) || point.length !== 3 || point.some(value => !Number.isFinite(value)))) throw new Error('弯钩模型缺少有效主直段');
            const straightLength = straight.slice(1).reduce((sum, point, index) => sum + Math.hypot(...point.map((value, axis) => value - straight[index][axis])), 0);
            if (Math.abs(straightLength - item.expectedLengthM) > 1e-8 || expectedShapeLength <= item.expectedLengthM) throw new Error('弯钩或直段长度与设计长度不一致');
          }
        }
      }
    }
    ids.add(item.id);
  }
  return { keep, removed, instanceById: new Map(report.instances.map((item) => [item.id, item])) };
}

function clearCylinderFitOverlay() {
  if (!cylinderFitOverlay) return;
  cylinderDenoiseScene.remove(cylinderFitOverlay);
  cylinderFitOverlay.traverse((object) => { object.geometry?.dispose?.(); object.material?.dispose?.(); });
  cylinderFitOverlay = null;
}

function rebuildCylinderInstanceOptions() {
  const report = current?.cylinderDenoise;
  const selected = $('cylinderInstanceFilter').value;
  const instances = report?.instances || [];
  $('cylinderInstanceFilter').replaceChildren(...[['all', '全部实例'], ...instances.map((item) => [String(item.id), `#${item.id} · ${item.centerlineM ? '已拟合' : '未拟合'} · ${item.status === 'applied' ? '已去噪' : '保留点云'}`])].map(([value, label]) => new Option(label, value)));
  $('cylinderInstanceFilter').value = instances.some((item) => String(item.id) === selected) ? selected : 'all';
}

function installCylinderDenoisePreview() {
  $('cylinderDenoiseStep').disabled = !current?._cylinderDenoise;
  for (const [id, key] of [['cylinderSteelLas', 'cylinderSteelLasUrl'], ['cylinderRemovedLas', 'cylinderRemovedLasUrl'], ['cylinderReport', 'cylinderReportUrl']]) {
    $(id).hidden = !current?.files?.[key]; $(id).href = current?.files?.[key] || '#';
  }
  if (!current?._cylinderDenoise) return;
  cylinderDenoiseGeometry = new THREE.BufferGeometry();
  cylinderDenoiseGeometry.setAttribute('position', new THREE.BufferAttribute(current._positions, 3));
  cylinderDenoiseGeometry.boundingSphere = rawGeometry?.boundingSphere?.clone() || null;
  if (!cylinderDenoiseGeometry.boundingSphere) cylinderDenoiseGeometry.computeBoundingSphere();
  cylinderDenoisePoints.geometry = cylinderDenoiseGeometry;
  rebuildCylinderInstanceOptions();
  const validation = current.cylinderDenoise.validation || {};
  const checks = [
    ['实例编号保持', validation.instanceIdsPreserved],
    ['设计关联唯一', validation.designAssignmentsUnique],
    ['设计数量核对', validation.countMatchesDesign],
    ['圆柱拟合数量核对', validation.fittedCountMatchesDesign],
    ['圆柱设计长度核对', validation.cylinderLengthsMatchDesign],
    ['弯钩核心点保留', validation.lockedBendPointsPreserved],
  ].filter(([, value]) => typeof value === 'boolean').map(([name, value]) => `${name}${value ? '满足' : '不满足'}`).join(' · ');
  $('cylinderValidation').textContent = `应用状态：${validation.status === 'applied' ? '已应用' : '未应用'}${validation.reason ? ` · ${cylinderReason(validation.reason)}` : ''}${checks ? `；${checks}` : ''}。数量核对不等同于圆柱拟合有效；请查看每根的拟合状态和 RMSE。`;
  const counts = validation.counts || current.cylinderDenoise.counts || current.cylinderDenoise || {};
  $('cylinderSummary').replaceChildren(...[
    ['点云实例：去噪前 / 去噪后', `${fmt(validation.observedInstanceCount)} / ${fmt(validation.observedInstanceCountAfter)}`],
    ['实际拟合圆柱 / 设计单元', `${fmt(current.cylinderDenoise.instances.filter(item => item.centerlineM?.length >= 2).length)} / ${fmt(validation.expectedMatchingUnitCount)}`],
    ['模型实际钢筋 / 保留待复核实例', `${fmt(counts.physicalBarCount)} / ${fmt(validation.retainedInstanceCount)}`],
    ['拟合前 / 保留 / 移除', `${fmt(counts.pointsBefore)} / ${fmt(counts.pointsAfter)} / ${fmt(counts.removedPointCount)}`],
    ['允许去噪 / 已应用去噪', `${fmt(validation.candidateInstanceCount)} / ${fmt(validation.appliedInstanceCount)}`],
    ...(Number.isFinite(validation.curvedInstanceCount) ? [['含弯钩或腹杆弯曲的实例 / 保护点', `${fmt(validation.curvedInstanceCount)} / ${fmt(validation.protectedBendPointCount)}`]] : []),
    ...(Number.isFinite(validation.scanSupportedCurvedInstanceCount) ? [['弯曲定位：点云支持 / 证据有限', `${fmt(validation.scanSupportedCurvedInstanceCount)} / ${fmt(validation.limitedEvidenceCurvedInstanceIds?.length || 0)}`]] : []),
    ['验证说明', validation.message || cylinderReason(validation.reason)],
  ].map(([name, value]) => { const row = document.createElement('span'); row.textContent = `${name}：${value}`; return row; }));
}

const cylinderReasonNames = {
  applied: '已按圆柱残差移除离群点', matching_unit_count_mismatch: '观测实例数与设计匹配单元数不一致',
  design_unit_assignment_mismatch: '设计单元关联不唯一或不完整', unparsed_design_present: '存在未解析设计钢筋',
  inadequate_instance_evidence: '没有足够的实例拟合证据', missing_matched_dimension: '缺少已匹配的直径或长度',
  bent_or_hook_protected: '弯曲或弯钩实例受保护', insufficient_axis_evidence: '轴线证据不足',
  observed_span_exceeds_dimension: '扫描跨度超过设计长度容限；保留该长度差异，不据此裁掉实测点', inadequate_cylinder_support: '圆柱支持点不足',
  radial_outlier: '径向离群点已移除', global_validation_not_applied: '全局验证未通过，拟合结果保留未应用',
};
function cylinderReason(reason) { return reason ? (cylinderReasonNames[reason] || reason) : '未报告原因'; }

function rebuildCylinderFitOverlay() {
  clearCylinderFitOverlay();
  if (!current?._cylinderDenoise || !$('cylinderOverlay').checked) return;
  const selectedId = $('cylinderInstanceFilter').value;
  const origin = current.preview.origin || [0, 0, 0];
  const group = new THREE.Group();
  const entries = current.cylinderDenoise.instances.filter((item) => selectedId === 'all' || item.id === Number(selectedId));
  for (const item of entries) {
    if (!Array.isArray(item.centerlineM) || !Number.isFinite(item.radiusM) || item.radiusM <= 0) continue;
    const model = new THREE.Group();
    model.name = `cylinder-instance-${item.id}`;
    model.userData.instanceId = item.id;
    group.add(model);
    const [red, green, blue] = rebarInstanceColor(item.id, 'completeRebar');
    const color = new THREE.Color().setRGB(red, green, blue);
    const line = [], lineColors = [];
    for (const point of item.centerlineM) { line.push(point[0] - origin[0], point[1] - origin[1], point[2] - origin[2]); lineColors.push(color.r, color.g, color.b); }
    const lineGeometry = new THREE.BufferGeometry(); lineGeometry.setAttribute('position', new THREE.Float32BufferAttribute(line, 3)); lineGeometry.setAttribute('color', new THREE.Float32BufferAttribute(lineColors, 3));
    model.add(new THREE.Line(lineGeometry, new THREE.LineBasicMaterial({vertexColors: true})));
    for (let index = 1; index < item.centerlineM.length; index++) {
      const a = new THREE.Vector3(...item.centerlineM[index - 1]).sub(new THREE.Vector3(...origin));
      const b = new THREE.Vector3(...item.centerlineM[index]).sub(new THREE.Vector3(...origin));
      const direction = b.clone().sub(a), length = direction.length();
      if (length <= 1e-7) continue;
      const geometry = new THREE.CylinderGeometry(item.radiusM, item.radiusM, length, 16, 1, true);
      const mesh = new THREE.Mesh(geometry, new THREE.MeshBasicMaterial({color, transparent: true, opacity: .18, depthWrite: false, side: THREE.DoubleSide}));
      mesh.position.copy(a).add(b).multiplyScalar(.5);
      mesh.quaternion.setFromUnitVectors(new THREE.Vector3(0, 1, 0), direction.normalize());
      model.add(mesh);
    }
    // Close the joins of the swept bend. A joint is part of its owner, so
    // adding a hook never increases the displayed instance/model count.
    if (item.shapeKind === 'straight-with-bends') {
      for (let index = 1; index + 1 < item.centerlineM.length; index++) {
        const previous = new THREE.Vector3(...item.centerlineM[index - 1]);
        const at = new THREE.Vector3(...item.centerlineM[index]);
        const next = new THREE.Vector3(...item.centerlineM[index + 1]);
        if (at.clone().sub(previous).normalize().dot(next.clone().sub(at).normalize()) > .999) continue;
        const joint = new THREE.Mesh(new THREE.SphereGeometry(item.radiusM, 16, 12), new THREE.MeshBasicMaterial({color, transparent: true, opacity: .18, depthWrite: false}));
        joint.position.copy(at).sub(new THREE.Vector3(...origin));
        model.add(joint);
      }
    }
  }
  cylinderFitOverlay = group;
  cylinderDenoiseScene.add(group);
}

function applyCylinderDenoiseAppearance() {
  if (!cylinderDenoiseGeometry || !current?._cylinderDenoise || !current?._complete) return;
  const {keep, removed, instanceById} = current._cylinderDenoise;
  const instances = current._complete.complete_instance, classes = current._complete.complete_class;
  const mode = $('cylinderCompare').value, selectedId = $('cylinderInstanceFilter').value;
  const data = stepSemanticData(current, 'completeRebar');
  const colors = resultDisplayColors(data, 'completeRebar') || categoryColors(data);
  const selected = new Uint32Array(instances.length); let size = 0;
  for (let index = 0; index < instances.length; index++) {
    const id = instances[index], report = instanceById.get(id);
    const before = classes[index] === 3 && report;
    const matches = mode === 'before' ? before : mode === 'after' ? before && keep[index] === 1 : before && removed[index] === 1;
    if (!matches || (selectedId !== 'all' && id !== Number(selectedId))) continue;
    selected[size++] = index;
    if (mode === 'removed' && $('resultColorMode').value === 'categories') colors.set([0.94, .28, .43], index * 3);
  }
  cylinderDenoiseGeometry.setAttribute('color', new THREE.BufferAttribute(colors, 3));
  cylinderDenoiseGeometry.setIndex(new THREE.BufferAttribute(selected.subarray(0, size), 1)); cylinderDenoiseGeometry.setDrawRange(0, size);
  rebuildCylinderFitOverlay();
  const item = selectedId === 'all' ? null : instanceById.get(Number(selectedId));
  $('cylinderHint').textContent = item
    ? `实例 #${item.id}：${item.centerlineM ? '圆柱已拟合' : '圆柱未拟合'} · ${item.status === 'applied' ? '去噪已应用' : '点云保留'} · RMSE ${fmtHeight(item.fitRmseM)} · 设计直径 ${fmtHeight(item.diameterM)} · 直段 ${fmtHeight(item.fittedStraightLengthM ?? item.fittedLengthM)} / 设计 ${fmtHeight(item.expectedLengthM)} · 含弯曲总长 ${fmtHeight(item.fittedLengthM)} / 设计 ${fmtHeight(item.expectedShapeLengthM ?? item.expectedLengthM)}${item.shapeKind === 'straight-with-bends' ? ` · 弯曲定位：${item.bendFitStatus === 'scan-supported' ? '点云支持' : '证据有限，请复核'} · 弯曲 RMSE ${fmtHeight(item.bendFitRmseM)} · 弯曲区域保留 ${fmt(item.protectedBendPointCount)} 点` : ''} · 扫描跨度 ${fmtHeight(item.observedLengthM)}。${cylinderReason(item.reason)}`
    : `显示 ${fmt(size)} 个样本点；沿用第六步的钢筋实例编号。按设计直段与端部弯曲形状，在扫描点云上拟合位置和方向。弯曲区域保留实测点；腹杆连接弯曲分配到相邻小段。`;
  requestRender();
}

function clearCompleteAxes() {
  if (!completeAxisLines) return;
  completeRebarScene.remove(completeAxisLines);
  completeAxisLines.geometry.dispose(); completeAxisLines.material.dispose(); completeAxisLines = null;
}

function rebuildCompleteInstanceOptions() {
  const report = $('completeCompare').value === 'baseline' ? current.internalRebar : current.completeRebar;
  const instances = report?.instances || [];
  const selected = $('completeInstanceFilter').value;
  $('completeInstanceFilter').replaceChildren(...[['all', '全部实例'], ...instances.map(i => [i.id, `#${i.id} · ${fmt(i.lengthM, ' m')}${i.reviewStatus ? (i.reviewStatus === 'matched' ? ' · 已关联设计' : ' · 设计待核对') : ''}`])].map(([value, text]) => new Option(text, value)));
  $('completeInstanceFilter').value = instances.some(i => String(i.id) === selected) ? selected : 'all';
}

function installCompletePreview() {
  const report = current.completeRebar;
  $('completeRebarStep').disabled = !current._complete;
  for (const [id, key] of [['resolvedSteelLas', 'resolvedSteelLasUrl'], ['pendingSteelLas', 'pendingSteelLasUrl'], ['completeSteelLas', 'completeSteelLasUrl'], ['noiseLas', 'noiseLasUrl'], ['completeInstances', 'completeInstancesUrl']]) {
    $(id).hidden = !current.files?.[key]; $(id).href = current.files?.[key] || '#';
  }
  $('completeClusterColorOption').disabled = !current._complete?.complete_cluster;
  if (!current._complete?.complete_cluster && $('completeColorMode').value === 'clusters') $('completeColorMode').value = 'instances';
  if (!report) return;
  completeGeometry = new THREE.BufferGeometry();
  completeGeometry.setAttribute('position', new THREE.BufferAttribute(current._positions, 3));
  completeGeometry.boundingSphere = typeof rawGeometry !== 'undefined' ? rawGeometry?.boundingSphere?.clone() || null : null;
  if (!completeGeometry.boundingSphere) completeGeometry.computeBoundingSphere();
  completePoints.geometry = completeGeometry;
  rebuildCompleteInstanceOptions();
  const rows = [...Object.entries(report.counts).map(([key, value]) => [({table:'台面',fixture:'夹具',rebar:'钢筋',noise:'噪音'})[key], `${fmt(value)} 点`]),
    ['整簇接续点', fmt(report.matchedExteriorPointCount)], ['过滤外露噪音', fmt(report.rejectedExteriorPointCount)], ['待定钢筋', fmt(report.unassignedRebarPointCount)], ...(report.clusters ? [['匹配 / 待定簇', `${fmt(report.matchedClusterCount)} / ${fmt(report.ambiguousClusterCount)}`], ['随簇保留点', fmt(report.clusterCarriedPointCount)]] : []), ['延伸步骤耗时', fmt(report.elapsedS, ' s')]];
  if (report.designReview) {
    const d = report.designReview;
    rows.splice(4, rows.length - 4, ['设计模型', d.modelInfo?.name || '当前模型'],
      ['设计整筋 / 匹配单元', `${fmt(d.designPhysicalBars)} / ${fmt(d.designMatchingUnits)}`],
      ...(d.clusterQuality ? [
        ['目标簇数（腹杆逐段） / 实际簇数', `${fmt(d.clusterQuality.expectedClusterCount)} / ${fmt(d.clusterQuality.observedClusterCount)}`],
        ['数量与逐根对应', d.clusterQuality.countMatches ? '一致' : `待核对（差 ${d.clusterQuality.countDelta > 0 ? '+' : ''}${d.clusterQuality.countDelta}）`],
        ['长度 / 宽度异常簇', fmt(d.clusterQuality.shapeMismatchCount)],
        ['明显过短 / 弯钩宽度缺失', `${fmt(d.clusterQuality.tooShortCount)} / ${fmt(d.clusterQuality.hookWidthMissingCount)}`],
        ['弯钩保护 / 新接续点', `${fmt(d.hookProtectedPointCount)} / ${fmt(d.hookAttachedPointCount)}`],
        ...(d.hookClusters ? [
          ['弯曲外筋区域 / 保护簇 / 已整簇合并', `${fmt(d.hookClusters.expectedRegionCount)} / ${fmt(d.hookClusters.detectedClusterCount)} / ${fmt(d.hookClusters.mergedClusterCount)}`],
          ['弯曲外筋拆分 / 删除点', `${fmt(d.hookClusters.splitClusterCount)} / ${fmt(d.hookClusters.filteredPointCount)}`],
          ...(d.hookClusters.nonHookTerminalPolish ? [[
            '弯钩实例直端圆柱打磨', `${fmt(d.hookClusters.nonHookTerminalPolish.removedPointCount)} 点 / 复核 ${fmt(d.hookClusters.nonHookTerminalPolish.fixtureContactPointCount)} 个夹具接触点`,
          ]] : []),
        ] : []),
        ...(d.finalClusterFilter ? [
          ['最后整簇过滤', `${fmt(d.finalClusterFilter.removedInstanceCount)} 个实例 / ${fmt(d.finalClusterFilter.removedComponentCount)} 个残片 / ${fmt(d.finalClusterFilter.removedPointCount)} 点`],
        ] : []),
        ['末尾细小悬浮噪音', `${fmt(d.finalDenoising?.removedComponentCount)} 簇 / ${fmt(d.finalDenoising?.removedPointCount)} 点`],
        ...(d.overlengthTailFilter?.enabled ? [[
          '异常超长尾部回收', `${fmt(d.overlengthTailFilter.reclaimedInstanceCount)} 个实例 / ${fmt(d.overlengthTailFilter.removedPointCount)} 点 · ${d.overlengthTailFilter.elapsedS.toFixed(3)} 秒`,
        ]] : []),
        ...(d.finalUnassignedNoise ? [['最终未成实例残点归噪音', `${fmt(d.finalUnassignedNoise.removedPointCount)} 点`]] : []),
      ] : []),
      ['原内部实例 / 最终内外实例', `${fmt(d.observedInstancesBefore)} / ${fmt(d.observedInstancesAfter)}`],
      ['合并 / 拆分 / 新建', `${fmt(d.mergedInstances)} / ${fmt(d.splitInstances)} / ${fmt(d.newInstances)}`],
      ...(Number.isFinite(d.earlyExtensionPoints) ? [['删除前轴向接续', `${fmt(d.earlyExtensionClusters)} 簇 / ${fmt(d.earlyExtensionPoints)} 点`]] : []),
      ...(Number.isFinite(d.separatedExteriorClusters) ? [['钢筋与夹具分离', `${fmt(d.separatedExteriorClusters)} 个粘连簇`]] : []),
      ...(Number.isFinite(d.acrossFixtureMerges) ? [[Number.isFinite(d.earlyExtensionPoints) ? '后续内外实例合并' : '跨夹具内外接续', fmt(d.acrossFixtureMerges)]] : []),
      ['本步过滤点', fmt(d.filteredPoints)], ['未分配实例点', fmt(report.unassignedRebarPointCount)],
      ['设计关联待核对实例', fmt(d.pendingInstances)], ['未观测 / 多实例对应的设计单元', `${fmt(d.unobservedUnits)} / ${fmt(d.conflictingUnits)}`],
      ['本步耗时', fmt(report.elapsedS, ' s')]);
  }
  $('completeSummary').replaceChildren(...rows.map(([name, value]) => { const span = document.createElement('span'); span.textContent = `${name}：${value}`; return span; }));
}

function applyCompleteAppearance() {
  if (!completeGeometry || !current?._complete) return;
  const baseline = $('completeCompare').value === 'baseline' && current._refinedClasses;
  const baselineClasses = baseline ? Uint8Array.from(current._refinedClasses, (cls, i) => current._internalTypes?.[i] === 5 ? 4 : cls) : null;
  const classes = baselineClasses || current._complete.complete_class;
  const instances = baseline ? current._internalInstances : current._complete.complete_instance;
  const operations = current.completeRebar.designReview?.operations || [];
  const separatedClusters = new Set(operations.filter(o => o.action === 'separate').map(o => o.clusterId));
  const hookClusters = new Set((current.completeRebar.clusters || []).filter(c => c.category === 'curved-exterior').map(c => c.id));
  const finalRejectedClusters = new Set((current.completeRebar.designReview?.finalClusterFilter?.decisions || []).map(c => c.clusterId));
  const mergedIds = new Set(operations.filter(o => o.action === 'merge').flatMap(o => o.sourceInstanceIds));
  const bridgeIds = new Set(operations.filter(o => o.action === 'merge' && o.acrossFixture).flatMap(o => o.sourceInstanceIds));
  for (const o of operations) if (o.action === 'attach' && o.phase === 'before_filter') o.instanceIds.forEach(id => bridgeIds.add(id));
  for (const c of current.completeRebar.designReview?.components || []) {
    if (bridgeIds.has(c.originalInstanceId) || bridgeIds.has(c.instanceId)) bridgeIds.add(c.instanceId);
  }
  const splitIds = new Set(operations.filter(o => o.action === 'split').flatMap(o => o.instanceIds));
  for (const operation of operations) if (operation.action === 'merge') {
    if (operation.sourceInstanceIds.some(id => splitIds.has(id))) operation.sourceInstanceIds.forEach(id => splitIds.add(id));
  }
  const colors = new Float32Array(classes.length * 3), selected = new Uint32Array(classes.length);
  const filter = $('completeClassFilter').value, instance = $('completeInstanceFilter').value;
  const semantic = typeof stepSemanticData === 'function' ? stepSemanticData(current, baseline ? 'internalRebar' : 'completeRebar') : null;
  const useSemantic = semantic && (semanticVisibilityCustomized || ['resolved', '3', 'all'].includes(filter));
  const desiredTag = typeof preferredSemanticTag === 'string' ? preferredSemanticTag : 'all';
  const semanticId = semantic && semanticTagAvailable(semantic, semanticTagById(desiredTag)) ? desiredTag : 'all';
  const palette = {1:'#64748b',2:'#f59e0b',3:'#2dd4bf',4:'#ef476f'};
  let size = 0;
  for (let i = 0; i < classes.length; i++) {
    colors.set($('completeColorMode').value === 'clusters' && current._complete.complete_cluster?.[i] ? instanceColor(current._complete.complete_cluster[i]) : classes[i] === 3 && !instances[i] ? instanceColor(0) : classes[i] === 3 && $('completeColorMode').value === 'instances' ? rebarInstanceColor(instances[i], baseline ? 'internalRebar' : 'completeRebar') : hexColor(palette[classes[i]]), i * 3);
    const finalId = current._complete.complete_instance[i];
    const matches = filter === 'resolved' ? classes[i] === 3 && instances[i] > 0
      : filter === 'filtered' ? current._refinedClasses?.[i] === 3 && current._internalTypes?.[i] !== 5 && current._complete.complete_class[i] === 4
      : filter === 'merged' ? classes[i] === 3 && finalId > 0 && (mergedIds.has(finalId) || mergedIds.has(current._internalInstances[i]))
      : filter === 'bridged' ? classes[i] === 3 && finalId > 0 && (bridgeIds.has(finalId) || bridgeIds.has(current._internalInstances[i]))
      : filter === 'split' ? splitIds.has(finalId)
      : filter === 'separated' ? separatedClusters.has(current._complete.complete_cluster?.[i])
      : filter === 'hooks' ? hookClusters.has(current._complete.complete_cluster?.[i])
      : filter === 'final-rejected' ? finalRejectedClusters.has(current._complete.complete_cluster?.[i])
      : filter === 'all' || (filter === 'extended' ? instances[i] > 0 && !current._internalInstances[i] : filter === 'pending' ? classes[i] === 3 && !instances[i] : classes[i] === Number(filter));
    const explicitHardMask = filter === '4' || filter === 'filtered' || desiredTag === 'before' || desiredTag === 'noise';
    const semanticVisible = !useSemantic || (semanticVisibilityCustomized
      ? visibleSemanticCodes.has(semantic?.labels[i])
      : typeof semanticTagMatches !== 'function' || semanticTagMatches(semantic?.labels[i], semanticId));
    if (matches && hardMaskVisible(i, 'completeRebar', explicitHardMask) && semanticVisible && (instance === 'all' || instances[i] === Number(instance))) selected[size++] = i;
  }
  const resultColors = semantic ? resultDisplayColors(semantic, baseline ? 'internalRebar' : 'completeRebar') : null;
  if (resultColors) colors.set(resultColors);
  else if ($('completeColorMode').value === 'classes' && semantic) colors.set(semanticColors(semantic));
  else if ($('completeColorMode').value === 'score' && current._fusedSteelScores) colors.set(fusionScoreColors(current._fusedSteelScores));
  completeGeometry.setAttribute('color', new THREE.BufferAttribute(colors, 3));
  completeGeometry.setIndex(new THREE.BufferAttribute(selected.subarray(0, size), 1));
  completeGeometry.setDrawRange(0, size);
  clearCompleteAxes();
  if ($('completeAxes').checked && (filter === 'all' || filter === '3' || filter === 'resolved')) {
    const points = [], lineColors = [], origin = current.preview.origin;
    for (const segment of (baseline ? current.internalRebar.segments : current.completeRebar.segments)) {
      if (instance !== 'all' && segment.instanceId !== Number(instance)) continue;
      for (const point of [segment.startM, segment.endM]) { points.push(...point.map((v, axis) => v-origin[axis])); lineColors.push(...rebarInstanceColor(segment.instanceId, baseline ? 'internalRebar' : 'completeRebar')); }
    }
    const geometry = new THREE.BufferGeometry(); geometry.setAttribute('position', new THREE.Float32BufferAttribute(points, 3)); geometry.setAttribute('color', new THREE.Float32BufferAttribute(lineColors, 3));
    completeAxisLines = new THREE.LineSegments(geometry, new THREE.LineBasicMaterial({vertexColors:true})); completeRebarScene.add(completeAxisLines);
  }
  $('completeHint').textContent = `显示 ${fmt(size)} 个样本点。${current.completeRebar.enabled ? (current.completeRebar.clusters ? '按延伸前连通簇整体归并，弯钩随簇保留；多实例冲突簇完整保留为待定，未匹配簇整体归为噪音。' : '内外钢筋沿用同一实例编号；无法匹配的外部钢筋候选归为噪音。') : '缺少内部实例或双框，已保守保留外部钢筋。'} 遮挡处没有生成新点；轴线及长度仅表示轴向连接证据，不含弯钩曲线长度。`;
  if (current.completeRebar.designReview) $('completeHint').textContent = current.completeRebar.parameters?.extra_instance_penalty
    ? `显示 ${fmt(size)} 个样本点。按设计位置、长度及邻接关系竞争分配实例，同杆内外片段共用编号；夹具残留经几何复核后移除。未确定归属的候选单独保留，可切换第五步对照，或筛选跨夹具接续、本步过滤点。遮挡处不补点；轴线表示实测拟合段。`
    : `显示 ${fmt(size)} 个样本点。此历史结果使用较弱的设计复核约束；可重新运行第六步应用当前算法。遮挡处不补点；轴线表示实测拟合段。`;
  if (Number.isFinite(current.completeRebar.designReview?.earlyExtensionPoints)) $('completeHint').textContent = `显示 ${fmt(size)} 个样本点。先沿可靠内部钢筋的端部轴线接续外露点，再做设计关联和噪音清理。短外露段可继承内部编号；多个钢筋同时解释的点保留待定。可筛选跨夹具接续、本步过滤点。遮挡处不补点；外部轴线表示接续依据。`;
  if (Number.isFinite(current.completeRebar.designReview?.separatedExteriorClusters)) $('completeHint').textContent = `显示 ${fmt(size)} 个样本点。先分离同轴外露钢筋与横向夹具边缘，再接续内部实例并清理剩余分支。选择“粘连簇拆分对照”可比较处理前后，查看保留的钢筋和移除的边缘。原连通簇颜色用于追溯来源，不代表最终实例。遮挡处不补点。`;
  if (current.completeRebar.designReview?.hookClusters) $('completeHint').textContent = `显示 ${fmt(size)} 个样本点。弯钩核心单独锁定、只能整簇合并；弯钩实例的直段外端在夹具接触带按局部实测圆柱壳打磨。内外钢筋全部接续后，再按设计长度和同类钢筋参考点数过滤异常簇。可筛选“受保护弯曲外筋”和“最后整簇过滤”，对照第五步查看。`;
  if ($('completeColorMode').value === 'score' && current._fusedSteelScores) $('completeHint').textContent = `显示 ${fmt(size)} 个样本点，颜色沿用 03 的融合支持分数。点击点云可核对 03 → 05 → 06 的类别及高分保护状态；噪音也显示删除前分数。`;
  if (typeof applyCompleteTileAppearance === 'function' && applyCompleteTileAppearance()) {
    $('completeHint').textContent = $('completeHint').textContent.replace(`显示 ${fmt(size)} 个样本点。`, '当前使用按视野加载的高密度 Tiles LOD。');
  } else if (typeof completeTiles !== 'undefined' && completeTiles
    && typeof completeTilesSupported === 'function' && !completeTilesSupported()) {
    $('completeHint').textContent += ' 当前筛选依赖抽样阶段的附加属性，已自动回退到 30 万点预览。';
  }
  requestRender();
}

function rebuildSpatialInstancePalettes() {
  for (const step of ['internalRebar', 'completeRebar']) {
    const segments = current?.[step]?.segments || [];
    spatialInstancePalettes[step] = buildInstancePalette(segments.map(segment => ({
      id: segment.instanceId, paths: [[segment.startM, segment.endM]],
    })));
  }
  completeTileColorCache.clear();
}

function rebarInstanceColor(id, step) {
  const palette = spatialInstancePalettes[step === 'completeRebar' ? 'completeRebar' : 'internalRebar'];
  return palette.get(id) || instanceColor(id);
}

function instanceColor(id) {
  if (!id) return hexColor(internalTypeColors[4], '#94a3b8');
  const color = new THREE.Color().setHSL(((id * 0.61803398875) % 1 + 1) % 1, 0.72, 0.58);
  return [color.r, color.g, color.b];
}

function confidenceColor(value) {
  const t = THREE.MathUtils.clamp(value, 0, 1);
  const color = new THREE.Color().setHSL(0.02 + t * 0.31, 0.82, 0.52);
  return [color.r, color.g, color.b];
}

function internalColors(mode, types, instances, confidence, families = null) {
  const colors = new Float32Array(types.length * 3);
  for (let index = 0; index < types.length; index += 1) {
    let color;
    if (types[index] === 5) color = hexColor(internalTypeColors[5], '#ef476f');
    else if (types[index] === 4 || instances[index] === 0) color = hexColor(internalTypeColors[4], '#94a3b8');
    else if (mode === 'instances') color = rebarInstanceColor(instances[index], 'internalRebar');
    else if (mode === 'confidence') color = confidenceColor(confidence[index]);
    else if (mode === 'families' && families?.[index]) color = hexColor(internalFamilyColors[families[index]], '#94a3b8');
    else color = hexColor(internalTypeColors[types[index]], '#94a3b8');
    colors.set(color, index * 3);
  }
  return colors;
}

function updateInternalLegend() {
  const box = $('internalRebarLegend');
  box.replaceChildren();
  const mode = $('internalColorMode').value;
  box.hidden = mode === 'types';
  const entries = mode === 'score'
    ? [['低融合支持', '#ef4444'], ['中融合支持', '#eab308'], ['高融合支持（≥0.9 保护）', '#22c55e']]
    : mode === 'confidence'
    ? [['低拟合分数', '#ef4444'], ['中等拟合分数', '#eab308'], ['高拟合分数', '#22c55e']]
    : mode === 'instances'
      ? [['邻近钢筋优先异色', '#a78bfa'], ['同一实例颜色固定，远处可复用', '#38bdf8'], ['待定钢筋', internalTypeColors[4]]]
      : mode === 'families'
        ? Object.entries(internalFamilyNames()).map(([value, name]) => [name, internalFamilyColors[value] || '#94a3b8'])
      : [1, 2, 3, 4, 5].map((value) => [internalTypeNames[value], internalTypeColors[value]]);
  for (const [name, color] of entries) {
    const row = document.createElement('span');
    const swatch = document.createElement('i');
    swatch.className = 'swatch';
    swatch.style.background = color;
    row.append(swatch, name);
    box.append(row);
  }
}

function disposeInternalAxes() {
  if (!internalAxisGroup) return;
  internalRebarScene.remove(internalAxisGroup);
  internalAxisGroup.traverse((object) => {
    object.geometry?.dispose();
    object.material?.dispose();
  });
  internalAxisGroup = null;
}

function rebuildInternalAxes() {
  disposeInternalAxes();
  if (!$('internalAxes').checked || !current?.internalRebar?.segments?.length) return;
  const origin = current.preview?.origin;
  if (!Array.isArray(origin) || origin.length !== 3) return;
  const desired = semanticTagById(preferredSemanticTag);
  const typeFilter = semanticTagAvailable(stepSemanticData(current, 'internalRebar'), desired) ? desired.id : 'all';
  const familyFilter = $('internalFamilyFilter').value;
  const instanceFilter = $('internalInstanceFilter').value;
  const positions = [], colors = [];
  for (const segment of current.internalRebar.segments) {
    if (segment.type === 0) continue;
    if (!semanticTagMatches(({1:6,2:7,3:8})[segment.type], typeFilter)) continue;
    if (instanceFilter !== 'all' && segment.instanceId !== Number(instanceFilter)) continue;
    const family = current._internalInstanceById?.get(segment.instanceId)?.family;
    if (familyFilter !== 'all' && family !== Number(familyFilter)) continue;
    positions.push(
      segment.startM[0] - origin[0], segment.startM[1] - origin[1], segment.startM[2] - origin[2],
      segment.endM[0] - origin[0], segment.endM[1] - origin[1], segment.endM[2] - origin[2],
    );
    const color = segment.type === 4 || segment.instanceId === 0
      ? hexColor(internalTypeColors[4], '#94a3b8') : rebarInstanceColor(segment.instanceId, 'internalRebar');
    colors.push(...color, ...color);
  }
  if (!positions.length) return;
  const geometry = new THREE.BufferGeometry();
  geometry.setAttribute('position', new THREE.Float32BufferAttribute(positions, 3));
  geometry.setAttribute('color', new THREE.Float32BufferAttribute(colors, 3));
  internalAxisGroup = new THREE.LineSegments(geometry, new THREE.LineBasicMaterial({
    vertexColors: true, transparent: true, opacity: 0.95, depthTest: false,
  }));
  internalAxisGroup.renderOrder = 20;
  internalRebarScene.add(internalAxisGroup);
}

function applyInternalRebarAppearance() {
  if (!internalRebarGeometry || !current?._internalTypes) return;
  const data = stepSemanticData(current, 'internalRebar');
  const mode = $('internalColorMode').value;
  const unifiedColors = resultDisplayColors(data, 'internalRebar');
  const colors = unifiedColors || (mode === 'types' ? semanticColors(data)
    : mode === 'score' && current._fusedSteelScores ? fusionScoreColors(current._fusedSteelScores)
    : internalColors(mode, current._internalTypes, current._internalInstances, current._internalConfidence, current._internalFamilies));
  if (!unifiedColors && mode !== 'types' && mode !== 'score') {
    for (let i = 0; i < data.labels.length; i++) {
      if (current._internalTypes[i] === 0 || current._internalTypes[i] === 5) colors.set(semanticPalette[data.labels[i]], i*3);
    }
  }
  internalRebarGeometry.setAttribute('color', new THREE.BufferAttribute(colors, 3));
  const family = $('internalFamilyFilter').value;
  const instance = $('internalInstanceFilter').value;
  const selected = filterSemanticGeometry(internalRebarGeometry, 'internalRebar', index =>
    (family === 'all' || current._internalFamilies?.[index] === Number(family))
    && (instance === 'all' || current._internalInstances[index] === Number(instance)));
  const baselineStep = current.refinement?.mode === 'fusion-pass-through' ? '03' : '04';
  $('internalRebarHint').textContent = current.internalRebar?.denoising?.exteriorReviewEnabled
    ? `当前显示 ${fmt(selected)} 个预览点；内外钢筋均已做空间去噪，内部钢筋另外识别实例。${current.internalRebar?.denoising?.designBoundaryAppliesToAllSteel ? '钢筋结果的布外点全部移除；选择“全部钢筋”可检查，台面、夹具为独立对照类别。' : ''}夹具、台面沿用第 ${baselineStep} 步结果。噪音可在统一分类筛选中单独查看。`
    : `当前显示 ${fmt(selected)} 个预览点；夹具、台面和外部钢筋沿用第 ${baselineStep} 步结果。待定钢筋仍被保留；噪音可在统一分类筛选中单独查看。`;
  if (mode === 'score' && current._fusedSteelScores) $('internalRebarHint').textContent = `当前显示 ${fmt(selected)} 个预览点，颜色沿用 03 的融合支持分数。噪音也显示删除前分数；点击点云可核对类别变化和空间复核结果。几何拟合分数是另一个指标。`;
  updateInternalLegend();
  rebuildInternalAxes();
  requestRender();
}

function validCorners(corners, name) {
  if (corners == null) return null;
  if (!Array.isArray(corners) || corners.length !== 4 || corners.some((corner) =>
    !Array.isArray(corner) || corner.length < 2 || !Number.isFinite(Number(corner[0])) || !Number.isFinite(Number(corner[1])))) {
    throw new Error(`${name}必须包含 4 个有限 XY 角点`);
  }
  return corners.map((corner) => [Number(corner[0]), Number(corner[1])]);
}

function normalizedFrame(frame) {
  if (!frame?.detected) return null;
  const outer = validCorners(frame.outerCornersM ?? frame.cornersM, '外框');
  const inner = frame.innerDetected === false || frame.innerCornersM?.length === 0
    ? null : validCorners(frame.innerCornersM, '内框');
  const tablePlane = current?.preprocessing?.tableRemoval?.plane;
  const tableZ = Number(tablePlane?.heightM ?? tablePlane?.originM?.[2] ?? current?.projection?.tableRemoval?.origin?.[2]);
  const boundsZ = Number(current?.preview?.origin?.[2]) + Number(current?.preview?.bounds?.min?.[2]);
  const height = Number(frame.displayHeightM ?? ((Number.isFinite(tableZ) ? tableZ : boundsZ) + .002));
  if (!outer || !Number.isFinite(height)) return null;
  return { outer, inner, height };
}

function disposeFrameOverlays() {
  for (const { scene, group } of frameOverlays) {
    scene.remove(group);
    group.traverse((object) => {
      object.geometry?.dispose();
      object.material?.dispose();
    });
  }
  frameOverlays = [];
}

function disposeFloatingGeometryOverlays() {
  for (const { scene, group } of floatingGeometryOverlays) {
    scene.remove(group);
    group.traverse((object) => { object.geometry?.dispose(); object.material?.dispose(); });
  }
  floatingGeometryOverlays = [];
}

function finitePoint(point) {
  return Array.isArray(point) && point.length === 3 && point.every(value => Number.isFinite(Number(value)));
}

function scanPoint(point, origin) {
  return new THREE.Vector3(Number(point[0]) - origin[0], Number(point[1]) - origin[1], Number(point[2]) - origin[2]);
}

function normalizedSurface(surface, kind) {
  if (surface?.kind !== kind || surface?.closed !== true || !Array.isArray(surface.verticesM) || surface.verticesM.length < 4
    || surface.verticesM.some(point => !finitePoint(point)) || !Array.isArray(surface.triangles) || !surface.triangles.length
    || surface.triangles.some(triangle => !Array.isArray(triangle) || triangle.length !== 3 || triangle.some(index => !Number.isSafeInteger(index) || index < 0 || index >= surface.verticesM.length))) return null;
  return surface;
}

function normalizedOuterEnvelope(envelope) {
  if (envelope?.kind === 'triangulated-cloth') {
    const body = normalizedSurface(envelope, 'triangulated-cloth');
    return body && Number.isSafeInteger(envelope.upperVertexCount) && envelope.upperVertexCount >= 3 && envelope.upperVertexCount * 2 === envelope.verticesM.length ? envelope : null;
  }
  if (envelope?.kind !== 'composite-steel-envelope' || envelope.closed !== true || !normalizedSurface(envelope, 'composite-steel-envelope')) return null;
  if (envelope.body !== null && !normalizedOuterEnvelope(envelope.body)) return null;
  if (!Array.isArray(envelope.curveShells) || envelope.curveShells.some(shell => !normalizedSurface(shell, 'swept-curve')
    || !Array.isArray(shell.centerlineM) || shell.centerlineM.length < 2 || shell.centerlineM.some(point => !finitePoint(point)))) return null;
  return envelope;
}

function outerEnvelopeMesh(envelope, origin, color = 0x67e8f9) {
  const positions = envelope.verticesM.flatMap(point => scanPoint(point, origin).toArray());
  const geometry = new THREE.BufferGeometry();
  geometry.setAttribute('position', new THREE.Float32BufferAttribute(positions, 3));
  geometry.setIndex(envelope.triangles.flat());
  geometry.computeVertexNormals();
  const mesh = new THREE.Mesh(geometry, new THREE.MeshPhongMaterial({color, transparent: true, opacity: .2, side: THREE.DoubleSide, depthWrite: false, shininess: 20, flatShading: envelope.kind === 'triangulated-cloth'}));
  mesh.renderOrder = 12;
  return mesh;
}

function outerEnvelopeSections(envelope, origin) {
  const positions = [], axis = envelope.xyAxes?.[0] || [1, 0];
  const vertices = envelope.verticesM.map(point => scanPoint(point, origin));
  const projected = vertices.map(point => point.x * axis[0] + point.y * axis[1]);
  const [low, high] = projected.reduce(([min, max], value) => [Math.min(min, value), Math.max(max, value)], [Infinity, -Infinity]);
  // Actual intersections of three vertical cutting planes with the shell.
  // Shared triangle-edge endpoints join into closed section contours.
  for (const fraction of [.23, .5, .77]) {
    const plane = low + fraction * (high - low);
    for (const triangle of envelope.triangles) {
      const hits = [];
      for (let edge = 0; edge < 3; edge += 1) {
        const a = triangle[edge], b = triangle[(edge + 1) % 3];
        const da = projected[a] - plane, db = projected[b] - plane;
        if ((da <= 0 && db > 0) || (db <= 0 && da > 0)) hits.push(vertices[a].clone().lerp(vertices[b], da / (da - db)));
      }
      if (hits.length === 2) positions.push(...hits[0].toArray(), ...hits[1].toArray());
    }
  }
  const geometry = new THREE.BufferGeometry(); geometry.setAttribute('position', new THREE.Float32BufferAttribute(positions, 3));
  const lines = new THREE.LineSegments(geometry, new THREE.LineBasicMaterial({color:0x67e8f9, transparent:true, opacity:.62, depthTest:false}));
  lines.name = 'floatingPlanes'; lines.renderOrder = 15; return lines;
}

function rebuildFloatingGeometryOverlays() {
  disposeFloatingGeometryOverlays();
  const report = current?.preprocessing?.floatingZones, origin = current?.preview?.origin;
  if (!report || !finitePoint(origin)) return;
  const envelope = normalizedOuterEnvelope(report.outerEnvelope);
  const scenes = [floatingZonesScene, classScene, projectionScene, internalRebarScene];
  for (const scene of scenes) {
    const group = new THREE.Group();
    if (envelope) {
      // The point-cloud scenes otherwise contain no lights. Give the cloth
      // its own illumination so smooth normals reveal the web undulations.
      group.add(new THREE.HemisphereLight(0xd5f8ff, 0x26314b, 2));
      const light = new THREE.DirectionalLight(0xffffff, 1.5); light.position.set(1, -1, 2); group.add(light);
      const shells = new THREE.Group(); shells.name = 'floatingVolumes'; shells.visible = $('floatingVolumes').checked;
      const body = envelope.kind === 'composite-steel-envelope' ? envelope.body : envelope;
      if (body) shells.add(outerEnvelopeMesh(body, origin));
      for (const curve of envelope.kind === 'composite-steel-envelope' ? envelope.curveShells : []) {
        const curveMesh = outerEnvelopeMesh(curve, origin, 0x22b8cf); curveMesh.name = 'sweptCurveShell'; shells.add(curveMesh);
      }
      group.add(shells);
      const sections = outerEnvelopeSections(envelope, origin); sections.visible = $('floatingPlanes').checked; group.add(sections);
    }
    const lines = new THREE.Group(); lines.name = 'steelCenterlines';
    const positions = [];
    for (const segment of report.designSegments || []) if (finitePoint(segment?.startM) && finitePoint(segment?.endM)) positions.push(...scanPoint(segment.startM, origin).toArray(), ...scanPoint(segment.endM, origin).toArray());
    if (positions.length) { const geometry = new THREE.BufferGeometry(); geometry.setAttribute('position', new THREE.Float32BufferAttribute(positions, 3)); lines.add(new THREE.LineSegments(geometry, new THREE.LineBasicMaterial({color:0xa78bfa, transparent:true, opacity:.88, depthTest:false}))); }
    lines.visible = $('steelCenterlines').checked; group.add(lines); scene.add(group); floatingGeometryOverlays.push({scene, group});
  }
  const tolerance = envelope?.parameters;
  const bendHint = tolerance?.hookPartition === 'whole-straight-and-bend-cross-section'
    ? '弯钩排按完整平直段 + 弯钩作为一个整体分区，同排共用内外曲面。'
    : '主体按层铺平、边界直角折回，同排弯头共用内外曲面。';
  const bodyHint = tolerance?.heightRule === 'uniform-short-bar-top-plus-webs'
    ? `普通内部钢筋不分区，上表面统一为短筋最高上表面 ${fmtHeight(tolerance.shortBarTopSurfaceM)} + 阈值 ${fmtHeight(tolerance.upperSurfaceAllowanceM)} = ${fmtHeight(tolerance.uniformUpperHeightM)}；腹杆继续保留独立解析包络。`
    : '';
  const clearanceHint = tolerance ? ` ${tolerance.envelopeExpansionM ? `包络额外放宽 ${fmtHeight(tolerance.envelopeExpansionM)}。` : ''}当前余量：横向表面 ${fmtHeight(tolerance.lateralAllowanceM)}，配准 ${fmtHeight(tolerance.registrationAllowanceM)}，腹杆 ${fmtHeight(tolerance.webAllowanceM)}，弯筋表面 ${fmtHeight(tolerance.hookSurfaceAllowanceM)}；${bodyHint}${tolerance.hookProtection === 'shared-inner-outer-bend-cloth' ? bendHint : '弯筋使用三维扫掠曲面保护。'}` : '';
  $('floatingGeometryHint').textContent = envelope
    ? `青色连续外包络内包括层间空隙；审查域中包络外点标记为候选，${report.forbiddenRule?.action === 'step05-steel-boundary' ? '05 步以此包络为钢筋结果硬边界，不设实例、分数、分区或距离豁免。' : report.forbiddenRule?.action === 'step05-residual-veto' ? '05 步对无可靠支撑的布外待定残点执行禁飞区过滤。' : '是否删除由悬浮噪音复核决定。'}${clearanceHint}`
    : '该历史结果没有连续外包络；可显示设计中心线。请重新运行以查看精确禁飞边界。';
}

function makeDashedFrame(corners, height, origin, color) {
  const points = [...corners, corners[0]].map(([x, y]) =>
    new THREE.Vector3(x - origin[0], y - origin[1], height - origin[2]));
  const geometry = new THREE.BufferGeometry().setFromPoints(points);
  const perimeter = points.slice(1).reduce((sum, point, index) => sum + point.distanceTo(points[index]), 0);
  const line = new THREE.Line(geometry, new THREE.LineDashedMaterial({
    color, dashSize: Math.max(perimeter / 90, 0.004), gapSize: Math.max(perimeter / 140, 0.003),
    depthTest: false, transparent: true, opacity: 0.96,
  }));
  line.computeLineDistances();
  line.renderOrder = 10;
  return line;
}

function addFrameOverlay(scene, frameData, origin) {
  if (!frameData) return;
  const group = new THREE.Group();
  group.add(makeDashedFrame(frameData.outer, frameData.height, origin, 0xffffff));
  if (frameData.inner) group.add(makeDashedFrame(frameData.inner, frameData.height, origin, 0x57a6ff));
  group.visible = $('frameOverlay').checked;
  scene.add(group);
  frameOverlays.push({ scene, group });
}

function rebuildFrameOverlays() {
  disposeFrameOverlays();
  if (!current) return;
  const origin = current.preview?.origin;
  if (!Array.isArray(origin) || origin.length < 3 || origin.some((value) => !Number.isFinite(Number(value)))) return;
  const sharedFrame = normalizedFrame(current.preprocessing?.partition?.frame);
  const fusionFrame = sharedFrame || normalizedFrame(current.regions?.frame);
  const refinementFrame = sharedFrame || normalizedFrame(current.refinement?.frame) || fusionFrame;
  addFrameOverlay(partitionScene, sharedFrame, origin.map(Number));
  addFrameOverlay(fusionScene, fusionFrame, origin.map(Number));
  addFrameOverlay(refinementScene, refinementFrame, origin.map(Number));
  addFrameOverlay(internalRebarScene, refinementFrame, origin.map(Number));
  addFrameOverlay(completeRebarScene, refinementFrame, origin.map(Number));
}

function frameForCurrentStep() {
  const sharedFrame = normalizedFrame(current?.preprocessing?.partition?.frame);
  return ['internalRebar', 'completeRebar'].includes(rightScene)
    ? sharedFrame || normalizedFrame(current?.refinement?.frame) || normalizedFrame(current?.regions?.frame)
    : rightScene === 'refinement'
    ? sharedFrame || normalizedFrame(current?.refinement?.frame) || normalizedFrame(current?.regions?.frame)
    : rightScene === 'fusion' ? sharedFrame || normalizedFrame(current?.regions?.frame)
      : rightScene === 'partition' ? sharedFrame : null;
}

function updateFrameControls() {
  const frameData = frameForCurrentStep();
  $('frameControls').hidden = !frameData;
  $('innerFrameLegend').hidden = !frameData?.inner;
}

function updateRefinementLegend() {
  const box = $('refinementLegend');
  box.replaceChildren();
  const mode = $('refinementColorMode').value;
  box.hidden = mode !== 'zones';
  const names = current?.refinement?.zoneNames || defaultZoneNames;
  const entries = mode === 'regions'
    ? [0, 1, 2, 3, 4].map((value) => [defaultRegionNames[value], defaultRegionColors[value]])
    : mode === 'zones'
      ? [0, 1, 2, 3].map((value) => [names[String(value)] || defaultZoneNames[value], defaultZoneColors[value]])
      : [['台面', '#64748b'], ['夹具', '#f59e0b'], ['钢筋', '#2dd4bf']];
  for (const [name, color] of entries) {
    const row = document.createElement('span');
    const swatch = document.createElement('i');
    swatch.className = 'swatch';
    swatch.style.background = color;
    row.append(swatch, name);
    box.append(row);
  }
}

function applyRefinementAppearance() {
  if (!refinementGeometry || !current?._refinedClasses) return;
  const mode = $('refinementColorMode').value;
  const data = stepSemanticData(current, 'refinement');
  const colors = resultDisplayColors(data, 'refinement') || (mode === 'zones' ? zoneColors(current._refinedZones)
    : semanticColors(data));
  refinementGeometry.setAttribute('color', new THREE.BufferAttribute(colors, 3));
  const zone = $('refinementZoneFilter').value;
  filterSemanticGeometry(refinementGeometry, 'refinement', index =>
    (zone === 'all' || current._refinedZones[index] === Number(zone))
    && (!$('refinementChangedOnly').checked || current._refinedChanged[index] === 1));
  updateRefinementLegend();
  requestRender();
}

const fusionEvidenceBits = [
  [1, 'A 几何'], [2, 'B 投影'], [4, '轴线恢复'], [8, '区域或高度候选'],
];

function fusionEvidenceLabel(mask) {
  const labels = fusionEvidenceBits.filter(([bit]) => (mask & bit) !== 0).map(([, name]) => name);
  return labels.length ? labels.join(' + ') : '无明确支持证据';
}

function fusionScoreColors(scores, manifest = current) {
  const colors = new Float32Array(scores.length * 3);
  const protection = fusionProtectionThreshold(manifest) ?? .9;
  const low = fusionLowScoreThreshold(manifest);
  const palette = ['#ef4444', '#eab308', '#22c55e'].map(value => new THREE.Color(value).toArray());
  for (let index = 0; index < scores.length; index += 1) {
    colors.set(palette[scores[index] >= protection ? 2 : scores[index] > low ? 1 : 0], index * 3);
  }
  return colors;
}

function fusionProtectionThreshold(manifest = current) {
  const threshold = manifest?.fusion?.score?.protectionThreshold;
  return Number.isFinite(threshold) && threshold >= 0 && threshold <= 1 ? threshold : null;
}

function pointScoreTrace(manifest, index) {
  const score = manifest?._fusedSteelScores?.[index];
  const evidence = manifest?._fusedSteelEvidence?.[index];
  if (!Number.isFinite(score) || !Number.isInteger(evidence)) return null;
  const fused = manifest._fusedClasses?.[index];
  const internal = manifest._internalTypes?.[index] === 5 ? 4 : manifest._refinedClasses?.[index];
  const complete = manifest._complete?.complete_class?.[index];
  const threshold = fusionProtectionThreshold(manifest);
  const highScore = fused === 3 && threshold !== null && score >= threshold;
  const spatialReview = manifest.internalRebar?.denoising?.highScoreOverrideAllowed === true;
  const spatialOverride = highScore && spatialReview && internal === 4;
  const finalReview = manifest.completeRebar?.designReview?.finalClusterFilter;
  const finalDecision = finalReview?.highScoreOverrideAllowed === true
    ? finalReview.decisions?.find(d => d.clusterId === manifest._complete?.complete_cluster?.[index]) : null;
  const finalOverride = highScore && complete === 4 && !!finalDecision;
  return {score, evidence, fused, internal, complete,
    protected: highScore, spatialReview, spatialOverride, finalOverride, finalDecision,
    protectionViolation: highScore && !spatialOverride && (internal === 4 || (complete === 4 && !finalOverride))};
}

function fusionLowScoreThreshold(manifest = current) {
  const threshold = manifest?.fusion?.score?.lowScoreThreshold;
  return Number.isFinite(threshold) && threshold >= 0 && threshold <= 1 ? threshold : .5;
}

function isFusionPassThrough(manifest = current) {
  return manifest?.refinement?.mode === 'fusion-pass-through';
}

function projectionClassValueInvalid(value, manifest) {
  return value > 4 || (value === 0 && manifest?.projection?.pendingClass !== 0);
}

function defaultPreviewStep(manifest, available) {
  if (available.controlNet) return 'controlNet';
  if (available.cylinderDenoise) return 'cylinderDenoise';
  if (available.complete) return 'completeRebar';
  if (available.internalTypes) return 'internalRebar';
  if (available.refinedClasses && !isFusionPassThrough(manifest)) return 'refinement';
  if (available.fusedClasses) return 'fusion';
  if (available.projectionClasses) return 'projection';
  if (available.classes) return 'classification';
  if (available.partitionZones) return 'partition';
  if (available.sharedTableMask) return 'tableRemoval';
  return 'normal';
}

function updateFusionLegend() {
  const box = $('fusionLegend');
  box.replaceChildren();
  const mode = $('fusionColorMode').value;
  const entries = mode === 'regions'
    ? [0, 1, 2, 3, 4].map((value) => [current?.regions?.regionNames?.[String(value)] || defaultRegionNames[value], current?.regions?.colors?.[String(value)] || defaultRegionColors[value]])
    : mode === 'score'
      ? [['低支持', '#ef4444'], ['中支持', '#eab308'], ['高支持', '#22c55e']]
      : [['台面', '#64748b'], ['夹具', '#f59e0b'], ['钢筋', '#2dd4bf']];
  for (const [name, color] of entries) {
    const row = document.createElement('span');
    const swatch = document.createElement('i');
    swatch.className = 'swatch';
    swatch.style.background = color;
    row.append(swatch, name);
    box.append(row);
  }
}

function applyFusionAppearance() {
  if (!fusionGeometry || !current?._fusedClasses) return;
  $('pointInspector').hidden = true;
  const mode = $('fusionColorMode').value;
  const data = stepSemanticData(current, 'fusion');
  const colors = resultDisplayColors(data, 'fusion') || (mode === 'score' && current._fusedSteelScores
    ? fusionScoreColors(current._fusedSteelScores)
    : mode === 'regions'
      ? regionColors(current._fusedRegions, current.regions?.colors)
      : semanticColors(data));
  fusionGeometry.setAttribute('color', new THREE.BufferAttribute(colors, 3));
  const evidenceFilter = $('fusionScoreFilter').value;
  const threshold = fusionProtectionThreshold();
  const lowThreshold = fusionLowScoreThreshold();
  filterSemanticGeometry(fusionGeometry, 'fusion', index =>
    (!$('fusionRecoveredOnly').checked || current._fusedRecovered?.[index] === 1)
    && (evidenceFilter === 'all'
      || (evidenceFilter === 'both' && current._fusedSteelEvidence && (current._fusedSteelEvidence[index] & 3) === 3)
      || (evidenceFilter === 'high' && current._fusedSteelScores && threshold !== null && current._fusedSteelScores[index] >= threshold)
      || (evidenceFilter === 'low' && current._fusedSteelScores && current._fusedClasses[index] === 3 && current._fusedSteelScores[index] <= lowThreshold)));
  const scoreAvailable = Boolean(current._fusedSteelScores && current._fusedSteelEvidence);
  $('fusionHint').textContent = scoreAvailable
    ? `支持分数是融合规则值，不是校准概率。高分阈值 ${threshold === null ? '未声明' : threshold.toFixed(2)}，低分审查阈值 ${lowThreshold.toFixed(2)}。${current.internalRebar?.denoising?.highScoreOverrideAllowed ? '第 05 步允许充分空间证据推翻高分。' : ''}${current.fusion?.score?.branchRetentionPreserved ? '02A/02B 保留结果与评分证据分开；B 上下层高度规则计一路支持，共享分区不重复投票。' : ''}点击右侧点云可检查单点分数和证据。`
    : '该结果没有逐点支持分数；仍可按统一类别查看历史融合结果。';
  updateFusionLegend();
  requestRender();
}

function applyClassFilter() {
  if (!classGeometry || !current?._classes) return;
  const data = stepSemanticData(current, 'classification');
  classGeometry.setAttribute('color', new THREE.BufferAttribute(resultDisplayColors(data, 'classification') || semanticColors(data), 3));
  const filter = $('classFilter').value;
  filterSemanticGeometry(classGeometry, 'classification', index =>
    (!$('classRecoveredOnly').checked || current._recovered?.[index] === 1)
    && (filter === 'all' || filter === 'recovered' || (filter === 'excludeStatic' ? current._classes[index] !== 1 && current._classes[index] !== 2 : current._classes[index] === Number(filter))));
  requestRender();
}

function applyProjectionFilter() {
  if (!projectionGeometry || !current?._projectionClasses) return;
  const data = stepSemanticData(current, 'projection');
  projectionGeometry.setAttribute('color', new THREE.BufferAttribute(resultDisplayColors(data, 'projection') || semanticColors(data), 3));
  const layer = $('projectionLayerFilter').value;
  const classFilter = $('projectionClassFilter').value;
  filterSemanticGeometry(projectionGeometry, 'projection', index =>
    (layer === 'all' || current._projectionLayers[index] === Number(layer))
    && (classFilter === 'all' || current._projectionClasses[index] === Number(classFilter)));
  requestRender();
}

const projectionImageNotes = {
  binary: '俯视占用二值图：显示哪些投影像素包含点。',
  density: '俯视密度图：显示每个投影像素内的点密度。',
  height: '每个俯视像素的 Z 跨度（最高点减最低点）；结合密度和 Z 连续占用判断竖向面。',
  classes: '02B 投影分类图，颜色与三类点云图例一致。',
  layers: 'Z 高度分层图：用于检查峰值、层间边界和各层相对高度。',
  edges: '沿实测夹具面检查邻近薄边；黄色为本步改回夹具的点。仅贴近但不共面的钢筋不按此规则回收。',
  fixtures: '从夹具高度层的粗大俯视区域定位夹具，并结合连续侧壁确定高度范围。只剔除该范围内的点，不沿整根竖向投影删除上方钢筋。',
  side_0: 'XZ 侧视：使用 50% 重叠的深度切片查找腹杆，绿色为通过三维细长度检查的恢复候选，之后还会复核标高与夹具范围。',
  side_45: '45° 斜侧视：减少腹杆与夹具、相邻桁架的投影重叠；绿色为夹具范围复核前的恢复候选。',
  side_90: 'YZ 端视：补充沿 X 方向观察的腹杆证据；绿色为夹具范围复核前的恢复候选。',
  side_135: '135° 斜侧视：补充另一对角方向；绿色为夹具范围复核前的恢复候选。',
};

function setProjectionView(view) {
  projectionView = view;
  const imagePanel = $('projectionImagePanel');
  const isImage = rightScene === 'projection' && view !== '3d';
  $('semanticFilter').disabled = isImage;
  updateSemanticControls();
  if (isImage) $('semanticHint').textContent = '当前显示已生成的投影图像；切回“分类点云”后可使用统一分类筛选。';
  document.querySelectorAll('[data-projection-view]').forEach((button) => {
    button.classList.toggle('active', button.dataset.projectionView === view);
  });
  imagePanel.hidden = !isImage;
  $('view').style.visibility = isImage ? 'hidden' : '';
  document.querySelector('.divider').hidden = isImage || !compareSource;
  document.querySelector('.before').hidden = isImage || !compareSource;
  document.querySelector('.after').hidden = isImage;
  document.querySelector('.axis').hidden = isImage;
  if (isImage) {
    const image = current?.projection?.images?.[view];
    if (!image?.url) {
      imagePanel.hidden = true;
      $('view').style.visibility = '';
      setStatus(`当前结果缺少“${view}”投影图像。`, true);
      projectionView = '3d';
      setProjectionView('3d');
      return;
    }
    $('projectionImage').src = image.url;
    $('projectionImage').alt = image.title || projectionImageNotes[view] || '投影分类诊断图';
    $('projectionImageLink').href = image.url;
    const legend = view === 'classes'
      ? '<span class="imageLegend"><span><i class="swatch" style="background:#64748b"></i>台面</span><span><i class="swatch" style="background:#f59e0b"></i>夹具</span><span><i class="swatch" style="background:#2dd4bf"></i>钢筋</span></span>' : '';
    $('projectionImageCaption').innerHTML = `<strong>${image.title || '投影诊断图'}</strong>${projectionImageNotes[view] || ''}${legend}<div>点击图像打开原图</div>`;
  }
  requestRender();
}

function buildArrows() {
  disposeArrows();
  if (!$('arrows').checked || !current) return;
  const { _positions: positions, _normals: normals, _valid: valid } = current;
  const count = positions.length / 3;
  const stride = Math.max(1, Math.ceil(count / 2000));
  const length = Number($('length').value);
  const vertices = [], colors = [];
  const direction = new THREE.Vector3(), origin = new THREE.Vector3(), tip = new THREE.Vector3();
  const side = new THREE.Vector3(), headA = new THREE.Vector3(), headB = new THREE.Vector3();
  const segment = (a, b, color) => {
    vertices.push(a.x, a.y, a.z, b.x, b.y, b.z);
    colors.push(...color, ...color);
  };
  for (let index = 0; index < count; index += stride) {
    const offset = index * 3;
    origin.set(positions[offset], positions[offset + 1], positions[offset + 2]);
    direction.set(normals[offset], normals[offset + 1], normals[offset + 2]);
    const isValid = valid[index] && direction.lengthSq() > 1e-16;
    const arrowLength = isValid ? length : length * 0.35;
    if (isValid) direction.normalize(); else direction.set(0, 0, 1);
    tip.copy(origin).addScaledVector(direction, arrowLength);
    const reference = Math.abs(direction.z) > 0.9
      ? new THREE.Vector3(1, 0, 0) : new THREE.Vector3(0, 0, 1);
    side.crossVectors(direction, reference).normalize();
    headA.copy(tip).addScaledVector(direction, -arrowLength * 0.28).addScaledVector(side, arrowLength * 0.14);
    headB.copy(tip).addScaledVector(direction, -arrowLength * 0.28).addScaledVector(side, -arrowLength * 0.14);
    const color = isValid ? [0.56, 0.83, 1] : [1, 0, 0.65];
    segment(origin, tip, color); segment(tip, headA, color); segment(tip, headB, color);
  }
  const geometry = new THREE.BufferGeometry();
  geometry.setAttribute('position', new THREE.Float32BufferAttribute(vertices, 3));
  geometry.setAttribute('color', new THREE.Float32BufferAttribute(colors, 3));
  arrowLines = new THREE.LineSegments(geometry, new THREE.LineBasicMaterial({ vertexColors: true }));
  normalScene.add(arrowLines);
  requestRender();
}

function fit(view = 'oblique') {
  lastView = view;
  if (!current?.preview?.bounds) return;
  resizeRenderer();
  const sourceBounds = current.preview.bounds;
  const b = { min: [...sourceBounds.min], max: [...sourceBounds.max] };
  const singleInstance = (rightScene === 'controlNet' && $('controlNetUnitFilter').value !== 'all') || (rightScene === 'internalRebar' && $('internalInstanceFilter').value !== 'all') || (rightScene === 'completeRebar' && $('completeInstanceFilter').value !== 'all') || (rightScene === 'cylinderDenoise' && $('cylinderInstanceFilter').value !== 'all');
  const selectedGeometry = rightScene === 'controlNet' ? controlNetGeometry : rightScene === 'completeRebar' ? completeGeometry : rightScene === 'cylinderDenoise' ? cylinderDenoiseGeometry : internalRebarGeometry;
  if (['controlNet', 'internalRebar', 'completeRebar', 'cylinderDenoise'].includes(rightScene) && !compareSource && selectedGeometry?.index?.count) {
    b.min.fill(Infinity); b.max.fill(-Infinity);
    const positions = current._positions, selected = selectedGeometry.index.array;
    for (const index of selected) {
      for (let axis = 0; axis < 3; axis += 1) {
        const value = positions[index * 3 + axis];
        b.min[axis] = Math.min(b.min[axis], value);
        b.max[axis] = Math.max(b.max[axis], value);
      }
    }
  }
  if (rightScene === 'cylinderDenoise' && !compareSource && cylinderFitOverlay?.children.length) {
    // Include the full known-length model, even where the scan is occluded.
    const fittedBounds = new THREE.Box3().setFromObject(cylinderFitOverlay);
    if (!selectedGeometry?.index?.count) { b.min.fill(Infinity); b.max.fill(-Infinity); }
    for (let axis = 0; axis < 3; axis++) {
      b.min[axis] = Math.min(b.min[axis], fittedBounds.min.getComponent(axis));
      b.max[axis] = Math.max(b.max[axis], fittedBounds.max.getComponent(axis));
    }
  }
  if (rightScene === 'controlNet' && $('controlNetUnitFilter').value !== 'all' && controlNetOverlay?.children.length) {
    const bounds = new THREE.Box3().setFromObject(controlNetOverlay);
    if (!selectedGeometry?.index?.count) { b.min.fill(Infinity); b.max.fill(-Infinity); }
    for (let axis = 0; axis < 3; axis++) {
      b.min[axis] = Math.min(b.min[axis], bounds.min.getComponent(axis));
      b.max[axis] = Math.max(b.max[axis], bounds.max.getComponent(axis));
    }
  }
  if (rightScene === 'controlNet' && !compareSource && controlNetOverlay?.children.length) {
    const fittedBounds = new THREE.Box3().setFromObject(controlNetOverlay);
    if (!selectedGeometry?.index?.count) { b.min.fill(Infinity); b.max.fill(-Infinity); }
    for (let axis = 0; axis < 3; axis++) {
      b.min[axis] = Math.min(b.min[axis], fittedBounds.min.getComponent(axis));
      b.max[axis] = Math.max(b.max[axis], fittedBounds.max.getComponent(axis));
    }
  }
  const frameData = frameForCurrentStep();
  const origin = current.preview.origin || [0, 0, 0];
  if (frameData && $('frameOverlay').checked && !singleInstance) {
    for (const [x, y] of [...frameData.outer, ...(frameData.inner || [])]) {
      b.min[0] = Math.min(b.min[0], x - origin[0]); b.max[0] = Math.max(b.max[0], x - origin[0]);
      b.min[1] = Math.min(b.min[1], y - origin[1]); b.max[1] = Math.max(b.max[1], y - origin[1]);
    }
    b.min[2] = Math.min(b.min[2], frameData.height - origin[2]);
    b.max[2] = Math.max(b.max[2], frameData.height - origin[2]);
  }
  const dx = b.max[0] - b.min[0], dy = b.max[1] - b.min[1], dz = b.max[2] - b.min[2];
  const radius = Math.max(Math.hypot(dx, dy, dz) / 2, 0.01);
  controls.target.set((b.max[0] + b.min[0]) / 2, (b.max[1] + b.min[1]) / 2, (b.max[2] + b.min[2]) / 2);
  const vertical = THREE.MathUtils.degToRad(camera.fov / 2);
  const horizontal = Math.atan(Math.tan(vertical) * camera.aspect);
  const distance = radius / Math.sin(Math.min(vertical, horizontal)) * 1.12;
  const offset = view === 'top'
    ? new THREE.Vector3(0, 0, distance)
    : view === 'side'
      ? new THREE.Vector3(0, -distance, 0)
    : new THREE.Vector3(1, -1, 0.72).normalize().multiplyScalar(distance);
  camera.position.copy(controls.target).add(offset);
  camera.near = Math.max(0.0001, distance / 1000);
  camera.far = distance * 100;
  camera.updateProjectionMatrix();
  controls.update();
  requestRender();
}

function showStep(step) {
  if (step === 'tableRemoval' && !current?._sharedTableMask) {
    setStatus('该历史结果没有共享台面移除预览；请重新运行以生成 01B 数据。');
    return;
  }
  if (step === 'controlNet' && !current?._controlNet) {
    setStatus('该历史结果没有控制网拟合数据；请选择控制网实验重新运行。');
    return;
  }
  if (step === 'partition' && !current?._partitionZones) {
    setStatus('该历史结果没有共享钢筋分区预览；请重新运行以生成 01C 数据。');
    return;
  }
  if (step === 'floatingZones' && (!current?._sharedLayers || !current?._sharedFloatingNoise)) {
    setStatus('该历史结果没有共享钢筋分层或禁飞区预览；请重新运行以生成 01D 数据。');
    return;
  }
  if (step === 'classification' && !current?._classes) {
    setStatus('该历史结果只有第 1 步法向量；请重新运行至第 2 步以查看几何分类。');
    return;
  }
  if (step === 'projection' && !current?._projectionClasses) {
    setStatus('该历史结果没有投影分类；请用“02A + 02B 并行分类”重新运行。');
    return;
  }
  if (step === 'fusion' && !current?._fusedClasses) {
    setStatus('该历史结果没有评分融合数据；请重新运行至第 4 步。');
    return;
  }
  if (step === 'refinement' && !current?._refinedClasses) {
    setStatus('该历史结果没有边带与类别整理数据；请重新运行至第 5 步。');
    return;
  }
  if (step === 'internalRebar' && !current?._internalTypes) {
    setStatus('该历史结果没有内部钢筋分层与逐根编号；请重新运行至第 6 步。');
    return;
  }
  if (step === 'completeRebar' && !current?._complete) return;
  if (step === 'cylinderDenoise' && !current?._cylinderDenoise) { setStatus('该历史结果没有圆柱先验拟合去噪数据。'); return; }
  if (step === 'designPrior' && !current?._designPrior) { setStatus(current?.designPrior?.disabledReason || '该历史结果没有可显示的设计先验复核数据。'); return; }
  rightScene = step;
  $('pointInspector').hidden = true;
  updateResultColorControls();
  updateSemanticControls();
  applyCurrentSemanticFilter();
  document.querySelectorAll('[data-step]').forEach((button) => button.classList.toggle('active', button.dataset.step === step));
  document.querySelector('.after').textContent = step === 'normal'
    ? '01 · 法向量' : step === 'tableRemoval' ? '01B · 台面移除结果'
      : step === 'controlNet' ? `${controlNetStageLabel(current.controlNet)} · ${current.controlNet.mode === 'auto' ? '自动初始化' : '粗对齐初始化'}`
      : step === 'partition' ? '01C · 共享钢筋分区'
      : step === 'floatingZones' ? (current?.preprocessing?.floatingZones ? '01D · 钢筋分层与禁飞区' : '01D · 钢筋分层')
      : step === 'classification' ? '02A · 法向量几何分类'
      : step === 'projection' ? '02B · 投影图像分类'
        : step === 'fusion' ? '03 · 评分融合'
          : step === 'refinement' ? '04 · 边带与类别整理'
            : step === 'internalRebar' ? '05 · 内部钢筋分层与悬浮去噪' : step === 'completeRebar' ? '06 · 设计辅助实例整理与去噪' : step === 'cylinderDenoise' ? '07 · 圆柱先验拟合去噪' : step === 'designPrior' ? '08 · 设计先验复核' : '00 · 原始颜色 / 强度';
  document.querySelector('.before').textContent = step === 'controlNet'
    ? (controlNetInputStage(current.controlNet) === 'post-fusion' ? '03 · 融合结果' : '01B · 台面移除后的原始点')
    : '00 · 原始颜色 / 强度';
  $('compareLabel').textContent = step === 'controlNet'
    ? (controlNetInputStage(current.controlNet) === 'post-fusion' ? '并排对照 03 融合结果' : '并排对照台面移除结果')
    : '并排对照原始点云';
  $('semanticControls').hidden = false;
  if (step === 'controlNet') $('semanticControls').hidden = true;
  $('semanticFilters').hidden = step === 'cylinderDenoise';
  $('tableRemovalControls').hidden = step !== 'tableRemoval' || !current?._sharedTableMask;
  $('controlNetControls').hidden = step !== 'controlNet' || !current?._controlNet;
  $('partitionControls').hidden = step !== 'partition' || !current?._partitionZones;
  $('floatingZonesControls').hidden = step !== 'floatingZones' || !current?._sharedLayers;
  $('floatingGeometryControls').hidden = !['floatingZones', 'classification', 'projection', 'internalRebar'].includes(step)
    || !current?.preprocessing?.floatingZones;
  $('classificationControls').hidden = step !== 'classification' || !current?._classes;
  $('projectionControls').hidden = step !== 'projection' || !current?._projectionClasses;
  $('fusionControls').hidden = step !== 'fusion' || !current?._fusedClasses;
  $('refinementControls').hidden = step !== 'refinement' || !current?._refinedClasses;
  $('internalRebarControls').hidden = step !== 'internalRebar' || !current?._internalTypes;
  $('completeRebarControls').hidden = step !== 'completeRebar' || !current?._complete;
  $('cylinderDenoiseControls').hidden = step !== 'cylinderDenoise' || !current?._cylinderDenoise;
  $('designPriorControls').hidden = step !== 'designPrior' || !current?._designPrior;
  updateFrameControls();
  setProjectionView(step === 'projection' ? projectionView : '3d');
  if (step === 'partition' || step === 'floatingZones') fit('top');
}

function inspectFusionPoint(event) {
  const inspector = $('pointInspector');
  const target = rightScene === 'fusion' ? fusionPoints : rightScene === 'internalRebar' ? internalRebarPoints : rightScene === 'completeRebar' ? completePoints : null;
  if (!target || !current?._fusedSteelScores || !current?._fusedSteelEvidence
      || (rightScene === 'completeRebar' && typeof completeTiles !== 'undefined' && completeTilesSupported())) {
    inspector.hidden = true;
    return;
  }
  const rect = renderer.domElement.getBoundingClientRect();
  const start = compareSource ? rect.width / 2 : 0;
  const localX = event.clientX - rect.left;
  if (localX < start) { inspector.hidden = true; return; }
  const viewportWidth = rect.width - start;
  const pointer = new THREE.Vector2(((localX - start) / viewportWidth) * 2 - 1, -((event.clientY - rect.top) / rect.height) * 2 + 1);
  const raycaster = new THREE.Raycaster();
  raycaster.params.Points.threshold = Math.max(target.geometry?.boundingSphere?.radius / 180 || 0, 0.002);
  raycaster.setFromCamera(pointer, camera);
  const hit = raycaster.intersectObject(target, false)[0];
  if (!hit || !Number.isInteger(hit.index)) { inspector.hidden = true; return; }
  const index = hit.index;
  const trace = pointScoreTrace(current, index);
  if (!trace) { inspector.hidden = true; return; }
  const className = value => ({1:'台面', 2:'夹具', 3:'钢筋', 4:'噪音'})[value] || '未计算';
  const labels = current.fusion?.score?.evidenceNames;
  const evidence = labels ? fusionEvidenceBits.filter(([bit]) => trace.evidence & bit).map(([bit]) => labels[String(bit)]).filter(Boolean).join(' + ') : fusionEvidenceLabel(trace.evidence);
  inspector.style.whiteSpace = 'pre-line';
  inspector.textContent = `样本点 #${index} · 03 融合支持分数 ${trace.score.toFixed(3)}\n${evidence || '无明确支持证据'}\n03 ${className(trace.fused)} → 05 ${className(trace.internal)} → 06 ${className(trace.complete)}\n${trace.protectionViolation ? '异常：高分保护点被标为噪音' : trace.finalOverride ? `最后整簇复核：长度仅为设计的 ${(100*trace.finalDecision.lengthRatio).toFixed(1)}%，点数为参考的 ${(100*trace.finalDecision.pointCountRatio).toFixed(1)}%` : trace.spatialOverride ? '多视图与三维证据已推翻高分' : trace.protected ? (trace.spatialReview ? '高分：需充分空间证据才能删除' : '达到高分保护阈值') : '需结合实测结构检查'}；分数不是校准概率`;
  inspector.hidden = false;
}

function metrics(manifest) {
  const t = manifest.timings || {}, p = manifest.performance || {};
  const preprocessing = manifest.preprocessing;
  const tableRemoval = preprocessing?.tableRemoval;
  const partition = preprocessing?.partition;
  const floatingZones = preprocessing?.floatingZones;
  const classification = manifest.classification;
  const projection = manifest.projection;
  const fusion = manifest.fusion;
  const regions = manifest.regions;
  const refinement = manifest.refinement;
  const refinementPassThrough = isFusionPassThrough(manifest);
  const internalRebar = manifest.internalRebar;
  const controlNet = manifest.controlNet;
  const classificationS = classification?.timings?.classificationS ?? t.classificationS;
  const projectionS = t.projectionS ?? projection?.elapsedS;
  const fusionS = t.fusionS ?? fusion?.elapsedS;
  const regionsS = t.regionsS;
  const refinementS = t.refinementS ?? refinement?.elapsedS;
  const internalRebarS = t.internalRebarS ?? internalRebar?.elapsedS;
  const tableRemovalS = tableRemoval?.elapsedS ?? preprocessing?.timings?.tableRemovalS;
  const partitionS = partition?.elapsedS ?? preprocessing?.timings?.partitionS;
  const recovery = classification?.recovery;
  const recoveryS = classification?.timings?.recoveryS;
  const classificationRate = classificationS > 0 ? classification.pointCount / classificationS : NaN;
  const branchStats = [classification ? `02A ${fmt(classificationS, ' s')}` : '', projection ? `02B ${fmt(projectionS, ' s')}` : ''].filter(Boolean).join(' · ');
  const branchMode = projection ? (manifest.branchExecution?.mode === 'parallel' ? '并行' : '串行') : '分类';
  const preprocessingStats = [tableRemoval ? `台面移除 ${fmt(tableRemovalS, ' s')}` : '', partition ? `钢筋分区 ${fmt(partitionS, ' s')}` : '', floatingZones ? `分层/禁飞区 ${fmt(floatingZones.elapsedS, ' s')}` : ''].filter(Boolean).join(' · ');
  const finalStats = [fusion ? `评分融合 ${fmt(fusionS, ' s')}` : '', regions ? `区域归属 ${fmt(regionsS, ' s')}` : '', refinement && !refinementPassThrough ? `边带整理 ${fmt(refinementS, ' s')}` : '', internalRebar ? `内部钢筋 ${fmt(internalRebarS, ' s')}` : '', manifest.completeRebar ? `实例整理 ${fmt(manifest.completeRebar.elapsedS, ' s')}` : ''].filter(Boolean).join(' · ');
  const controlNetStats = controlNet ? ` · 控制网${controlNet.mode === 'auto' ? '自动初始化' : '粗对齐'} ${fmt(controlNet.elapsedS, ' s')}` : '';
  $('quickStats').textContent = `法向量 ${fmt(t.normalsS, ' s')}${preprocessingStats ? ` · ${preprocessingStats}` : ''}${controlNetStats}${branchStats ? ` · ${branchStats}` : ''}${Number.isFinite(t.classifiersWallS) ? ` · ${branchMode}墙钟 ${fmt(t.classifiersWallS, ' s')}` : ''}${finalStats ? ` · ${finalStats}` : ''} · 全流程 ${fmt(t.totalS, ' s')}`;
  const rows = [
    ['保存参数 k / workers', `${fmt(manifest.parameters?.k)} / ${fmt(manifest.parameters?.workers)}`],
    ['预览 / 全量点', `${fmt(manifest.preview?.pointCount)} / ${fmt(manifest.preview?.totalPointCount)}`],
    ['有效 / 无效法向量', `${fmt(manifest.validNormalCount)} / ${fmt(manifest.invalidNormalCount)}`],
    ['法向量吞吐量', fmt(p.pointsPerSecond, ' pts/s')],
    ['CPU 累计 / 进程峰值内存', `${fmt(p.cpuS, ' s')} / ${fmt(p.peakRssMB, ' MB')}`],
    ...[['读取', t.readS], ['KD 树', t.treeS], ['法向量', t.normalsS], ['持久化', t.persistS], ['预览', t.previewS], ['总计', t.totalS]].map(([name, value]) => [name, fmt(value, ' s')]),
  ];
  if (partition) {
    const counts = partition.counts || {};
    const frame = partition.frame || {};
    rows.splice(3, 0,
      ['01C 未定位 / 内框内部', `${fmt(counts.unlocated)} / ${fmt(counts.interior)}`],
      ['01C 夹具边带 / 外框外部', `${fmt(counts.band)} / ${fmt(counts.exterior)}`],
      ['01C 共享双框', frame.detected ? `已检出 · 外框 ${fmt((frame.outerCornersM || frame.cornersM)?.length)} 点 · 内框 ${fmt(frame.innerCornersM?.length)} 点` : '未检出'],
      ['01C 钢筋分区耗时', fmt(partitionS, ' s')],
    );
  }
  if (floatingZones) {
    const params = floatingZones.outerEnvelope?.parameters || floatingZones.params || {};
    const marginLabels = {lateralAllowanceM: '横向', registrationAllowanceM: '配准', webAllowanceM: '腹杆', hookSurfaceAllowanceM: '弯筋表面', in_plane_half_width_m: '平面内半宽', layer_half_height_m: '分层半高', end_halo_m: '端部光环'};
    const marginText = Object.entries(marginLabels).filter(([key]) => Number.isFinite(params[key]))
      .map(([key, name]) => `${name} ${fmtHeight(params[key])}`).join(' · ');
    rows.splice(3, 0,
      ['01D 钢筋层 / 连续外包络', `${fmt((preprocessing.layering?.layers || []).length)} / ${floatingZones.outerEnvelope?.closed ? `${fmt(floatingZones.outerEnvelope.verticesM?.length)} 顶点` : '未生成'}`],
      ['01D 审查范围 / 禁飞区候选', `${fmt(floatingZones.eligiblePointCount)} / ${fmt(floatingZones.forbiddenPointCount)}`],
      ['01D 包络裕量', marginText || '未报告单独裕量；以报告参数为准'],
      ['01D 设计中心线', Array.isArray(floatingZones.designSegments) && floatingZones.designSegments.length ? `${fmt(floatingZones.designSegments.length)} 段` : `跳过：${floatingZones.designAvailability?.reason || floatingZones.designReason || '设计数据不可用'}`],
    );
  }
  if (tableRemoval) {
    rows.splice(3, 0,
      ['01B 台面检测', tableRemoval.detected ? '已检出' : '未检出'],
      ['01B 移除 / 保留点', `${fmt(tableRemoval.removedPoints)} / ${fmt(tableRemoval.remainingPoints)}`],
      ['01B 台面移除耗时', fmt(tableRemovalS, ' s')],
    );
  }
  if (controlNet) {
    const controlStage = controlNetInputStage(controlNet) === 'post-fusion' ? '03X' : controlNetInputStage(controlNet) === 'post-layering' ? '01E' : '01B-X';
    rows.splice(3, 0,
      [`${controlStage} 初始化 / 配准`, `${controlNet.mode === 'auto' ? '自动' : '粗对齐'} / ${controlNet.registration.method}`],
      [`${controlStage} 支持 / 待定 / 局部离群 / 融合排除`, `${fmt(controlNet.counts.matched)} / ${fmt(controlNet.counts.pending)} / ${fmt(controlNet.counts.removed)} / ${fmt(controlNet.counts.excluded ?? 0)}`],
      [`${controlStage} 拟合单元 / 设计单元 / 物理母筋`, `${fmt(controlNet.counts.fittedUnits)} / ${fmt(controlNet.counts.designUnits)} / ${fmt(controlNet.counts.designBars)}`],
      ...(Number.isSafeInteger(controlNet.counts.extendedShortUnits) ? [[`${controlStage} 实测延长短筋`, `${fmt(controlNet.counts.extendedShortUnits)} 根`]] : []),
      [`${controlStage} 控制网耗时`, fmt(controlNet.elapsedS, ' s')],
    );
  }
  if (classification) {
    const counts = classification.counts || {};
    const threeClass = classification.classPolicy === 'table-rebar-fixture-remainder';
    const hasPending = classification.pendingClass === 0;
    const classificationRows = [
      ['分类版本', classification.version || '—'],
      threeClass && !hasPending ? ['台面', fmt(counts.table)] : ['待定候选 / 台面', `${fmt(counts.pending ?? counts.unknown)} / ${fmt(counts.table)}`],
      ['夹具 / 钢筋', `${fmt(counts.fixture)} / ${fmt(counts.rebar)}`],
      ['分类耗时 / 吞吐量', `${fmt(classificationS, ' s')} / ${fmt(classificationRate, ' pts/s')}`],
    ];
    if (recovery) {
      classificationRows.push(['全量本轮归还钢筋 / 恢复阶段耗时', `${fmt(recovery.recoveredPoints)} / ${fmt(recoveryS, ' s')}`]);
    }
    rows.splice(3, 0, ...classificationRows);
  }
  if (projection) {
    const counts = projection.counts || {};
    const projectionRate = projectionS > 0 ? projection.pointCount / projectionS : NaN;
    const layerHeights = (projection.layers || []).map((layer) => `${layer.name || `层 ${layer.id}${Number.isFinite(layer.heightM) ? ` · ${(layer.heightM * 1000).toFixed(1)} mm` : ''}`} ${fmtHeight(layer.heightM)}（距台面 ${fmtHeight(layer.relativeHeightM)}）`).join('；');
    const projectionTimingRows = Object.entries(projection.timings || {})
      .filter(([, value]) => Number.isFinite(value))
      .map(([name, value]) => [`02B · ${name}`, value < .01 ? fmt(value * 1000, ' ms') : fmt(value, ' s')]);
    rows.splice(3, 0,
      ['02B 投影版本', projection.version || '—'],
      [projection.pendingClass === 0 ? '02B 待定候选 / 台面 / 夹具 / 钢筋' : '02B 台面 / 夹具 / 钢筋', projection.pendingClass === 0
        ? `${fmt(counts.pending)} / ${fmt(counts.table)} / ${fmt(counts.fixture)} / ${fmt(counts.rebar)}`
        : `${fmt(counts.table)} / ${fmt(counts.fixture)} / ${fmt(counts.rebar)}`],
      ['02B 分支墙钟 / 吞吐量', `${fmt(projectionS, ' s')} / ${fmt(projectionRate, ' pts/s')}`],
      ['02B Z 层高度', layerHeights || '未检出层'],
      ...(projection.bottomHeight ? [['02B 底层标高保留', `${(projection.bottomHeight.lowM*1000).toFixed(1)}–${(projection.bottomHeight.highM*1000).toFixed(1)} mm / 补回 ${fmt(projection.bottomHeight.recoveredPoints)} 点`]] : []),
      ...(projection.topHeight ? [['02B 顶层标高保留', `${(projection.topHeight.lowM*1000).toFixed(1)}–${(projection.topHeight.highM*1000).toFixed(1)} mm / 补回 ${fmt(projection.topHeight.recoveredPoints)} 点`]] : []),
      ...(projection.fixtureFootprint ? [['02B 夹具范围剔除', `${fmt(projection.fixtureFootprint.reclaimedPoints)} 点`]] : []),
      ...(projection.multiview ? [
        ['02B 腹杆补充视角 / 重叠切片', `${projection.multiview.views.length} / ${projection.multiview.views.reduce((sum, view) => sum + view.sliceCount, 0)}`],
        ['02B 多视角恢复原始点', fmt(projection.multiview.recoveredPoints)],
        ['02B 其中弯头 / 接头恢复', fmt(projection.multiview.bendRecoveredPoints || 0)],
        ['02B 夹具贴边回收', fmt(projection.multiview.fixtureEdgePoints || 0)],
      ] : []),
      ...projectionTimingRows,
    );
  }
  if (fusion) {
    const counts = fusion.counts || {};
    const recovery = fusion.recovery || {};
    const agreement = fusion.agreement || {};
    const fusionTimingRows = Object.entries(fusion.timings || {})
      .filter(([, value]) => Number.isFinite(value))
      .map(([name, value]) => [`03 融合 · ${name}`, value < .01 ? fmt(value * 1000, ' ms') : fmt(value, ' s')]);
    rows.splice(3, 0,
      ['03 评分融合版本', fusion.version || '—'],
      ['03 台面 / 夹具 / 钢筋', `${fmt(counts.table)} / ${fmt(counts.fixture)} / ${fmt(counts.rebar)}`],
      ['03 几何恢复：投影 / 双分支', `${fmt(recovery.recoveredFromProjectionPoints)} / ${fmt(recovery.recoveredBothPoints)}`],
      ['02A/B 一致 / 分歧点', `${fmt(agreement.agreePoints)} / ${fmt(agreement.disagreePoints)}`],
      ...(fusion.score ? [
        ['03 高分阈值', Number.isFinite(fusion.score.protectionThreshold) ? fusion.score.protectionThreshold.toFixed(2) : '—'],
        ['03 低分审查阈值', fusionLowScoreThreshold(manifest).toFixed(2)],
        ['03 高分点', fmt(fusion.score.highConfidencePoints)],
      ] : []),
      ['03 评分融合耗时', fmt(fusionS, ' s')],
      ...fusionTimingRows,
    );
  }
  if (regions) {
    const counts = regions.counts || {};
    const frame = regions.frame || {};
    rows.splice(3, 0,
      ['03 区域版本', regions.version || '—'],
      ['区域：台面 / 内部 / 外露', `${fmt(counts.table)} / ${fmt(counts.interior)} / ${fmt(counts.exterior)}`],
      ['区域：夹具 / 未定位', `${fmt(counts.fixture)} / ${fmt(counts.unlocated)}`],
      ['区域使用双框', (partition?.frame || frame).detected ? '复用 01C 共享双框' : '历史结果未检出'],
      ['03 区域耗时', fmt(regionsS, ' s')],
    );
  }
  if (refinement && !refinementPassThrough) {
    const counts = refinement.counts || {};
    const regionCounts = refinement.regionCounts || {};
    const changes = refinement.changes || {};
    const frame = refinement.frame || {};
    const reasonNames = Object.values(refinement.reasonNames || {}).join(' / ');
    const refinementTimingRows = Object.entries(refinement.timings || {})
      .filter(([, value]) => Number.isFinite(value))
      .map(([name, value]) => [`04 边带整理 · ${name}`, value < .01 ? fmt(value * 1000, ' ms') : fmt(value, ' s')]);
    rows.splice(3, 0,
      ['04 边带整理版本', refinement.version || '—'],
      ['04 台面 / 夹具 / 钢筋', `${fmt(counts.table)} / ${fmt(counts.fixture)} / ${fmt(counts.rebar)}`],
      ['04 区域：台面 / 内部 / 外露', `${fmt(regionCounts.table)} / ${fmt(regionCounts.interior)} / ${fmt(regionCounts.exterior)}`],
      ['04 区域：夹具 / 未定位', `${fmt(regionCounts.fixture)} / ${fmt(regionCounts.unlocated)}`],
      ['04 类别变化总数', fmt(changes.totalChanged)],
      ['04 内部→钢筋 / 边带→夹具', `${fmt(changes.interiorToSteel)} / ${fmt(changes.bandToFixture)}`],
      ['04 边带→钢筋 / 外部→夹具 / 外部→钢筋', `${fmt(changes.bandToSteel)} / ${fmt(changes.exteriorToFixture)} / ${fmt(changes.exteriorToSteel)}`],
      ['04 类别整理原因', reasonNames || '—'],
      ['04 使用双框', (partition?.frame || frame).detected ? (partition?.frame ? '复用 01C 共享双框' : '历史结果自带双框') : '未检出'],
      ['04 边带整理耗时', fmt(refinementS, ' s')],
      ...refinementTimingRows,
    );
  }
  if (internalRebar) {
    const counts = internalRebar.counts || {};
    const layers = internalRebar.layers || {};
    const modelCounts = (internalRebar.instances || []).reduce((result, instance) => {
      const kind = instance.modelKind || '未标注';
      result[kind] = (result[kind] || 0) + 1;
      return result;
    }, {});
    const modelSummary = Object.entries(modelCounts).map(([kind, count]) => `${kind} ${fmt(count)}`).join(' / ');
    const timingRows = Object.entries(internalRebar.timings || {})
      .filter(([, value]) => Number.isFinite(value))
      .map(([name, value]) => [`05 内部钢筋 · ${name}`, value < .01 ? fmt(value * 1000, ' ms') : fmt(value, ' s')]);
    const tracks = internalRebar.tracks;
    const diameterSummary = diameterPriorSummary(internalRebar);
    const lengthSummary = observedLengthSummary(internalRebar);
    const ifcLengthSummary = ifcHorizontalLengthSummary(internalRebar);
    const trackRows = tracks ? [
      ['05 连续轨迹：合并 / 横向 / 端部延伸', `${fmt(tracks.mergedGroups)} / ${fmt(tracks.horizontalTracks)} / ${fmt(tracks.grownEnds)}`],
      diameterSummary ? ['05 直径', diameterSummary] : null,
      lengthSummary ? ['05 构件族可见跨距', `${lengthSummary}（点云内框观测）`] : null,
      ifcLengthSummary ? ['05 IFC 横筋设计长度', ifcLengthSummary] : null,
      Number.isFinite(tracks.anomalousLengthTracks) ? ['05 可见长度异常轨迹', fmt(tracks.anomalousLengthTracks)] : null,
    ].filter(Boolean) : [];
    rows.splice(3, 0,
      ['05 识别版本', internalRebar.version || '—'],
      ['05 处理范围点数', fmt(internalRebar.pointCount)],
      ['05 下层 / 上层 / 腹杆', `${fmt(counts.lower)} / ${fmt(counts.upper)} / ${fmt(counts.web)}`],
      ['05 待定钢筋点', fmt(counts.unassigned)],
      ['05 新增残余噪音', fmt(internalRebar.denoising?.removedPointCount ?? counts.noise ?? 0)],
      ...(internalRebar.denoising?.designVetoEnabled ? [
        ['05 禁飞区过滤 / 其中额外删除', `${fmt(internalRebar.denoising.designHardRemovedPointCount)} / ${fmt(internalRebar.denoising.additionalDesignRemovedPointCount)}`],
        [internalRebar.denoising.designBoundaryAppliesToAllSteel ? '05 布外残留钢筋（应为 0）' : '05 布外受保护点', fmt(internalRebar.denoising.designProtectedPointCount)],
      ] : []),
      ...(internalRebar.denoising?.confirmedSteelReviewExcluded ? [
        ['01D / 02 已排除噪音', fmt(internalRebar.denoising.earlierRemovedPointCount)],
        ['05 多视角残余候选', fmt(internalRebar.denoising.reviewedCandidatePointCount)],
        ['05 复核范围', internalRebar.denoising.designBoundaryAppliesToAllSteel
          ? '多视图检查残余点；包络边界检查全部钢筋，包括已确认实例'
          : '仅未分配且缺少支撑的残余点；已确认实例不复审'],
      ] : []),
      ...(internalRebar.denoising?.exteriorReviewEnabled ? [
        ['05 内部 / 外部噪音', `${fmt(internalRebar.denoising.interiorRemovedPointCount)} / ${fmt(internalRebar.denoising.exteriorRemovedPointCount)}`],
      ] : []),
      ...(internalRebar.denoising?.highScoreOverrideAllowed ? [
        ['05 去噪版本', internalRebar.denoising.version],
        ['05 侧视方向 / 切片厚度', `${fmt(internalRebar.denoising.parameters?.view_angles?.length)} / ${fmt((internalRebar.denoising.parameters?.slice_width ?? 0) * 1000, ' mm')}`],
        ['05 可靠实测支撑点', fmt(internalRebar.denoising.frozenObservedPointCount)],
        ['05 空间证据推翻高分', fmt(internalRebar.denoising.highScoreRemovedPointCount)],
        ['05 移除片段', fmt(internalRebar.denoising.removedComponentCount ?? 0)],
      ] : []),
      ...(internalRebar.denoising?.topViewReview?.enabled ? [
        ['05 俯视复核新增过滤', fmt(internalRebar.denoising.topViewReview.additionalRemovedPointCount)],
        ['05 局部圆杆表面保护', fmt(internalRebar.denoising.localRoundProtectedPointCount)],
        ['05 孤立证据不足而保留的高分点', fmt(internalRebar.denoising.highScoreIsolationBlockedPointCount)],
      ] : []),
      ...(Number.isFinite(internalRebar.denoising?.lowerBandReviewedPointCount) ? [
        ['05 下层高度带内审查 / 移除', `${fmt(internalRebar.denoising.lowerBandReviewedPointCount)} / ${fmt(internalRebar.denoising.lowerBandRemovedPointCount)}`],
        ['05 高分保护点', fmt(internalRebar.denoising.protectedCandidatePointCount)],
        ...((internalRebar.denoising.scoreDecisions || []).map(row => [
          `05 ${({low:'低分',medium:'中分',high:'高分'})[row.band] || `分数 ${Number(row.score).toFixed(2)}`}：候选 / 移除`,
          `${fmt(row.candidatePoints)} / ${fmt(row.removedPoints)}`])),
      ] : []),
      ['05 实例覆盖率', internalRebar.pointCount ? `${(100 * ((counts.lower + counts.upper + counts.web) / internalRebar.pointCount)).toFixed(2)}%` : '—'],
      ['05 补建实例 / 表面补点', `${fmt(internalRebar.diagnostics?.recovery?.newInstances)} / ${fmt(internalRebar.diagnostics?.completedSurfacePoints)}`],
      ['05 实例 / 拟合段', `${fmt(internalRebar.instanceCount)} / ${fmt(internalRebar.segmentCount)}`],
      ['05 下层 / 上层高度', `${fmtHeight(layers.lowerM)} / ${fmtHeight(layers.upperM)}`],
      ['05 模型', modelSummary || '—'],
      ...trackRows,
      ['05 内部钢筋耗时', fmt(internalRebarS, ' s')],
      ...timingRows,
    );
  }
  if (Number.isFinite(t.classifiersWallS)) rows.splice(3, 0, [`${projection ? '02A + 02B' : '02A'} 实际${branchMode}墙钟`, fmt(t.classifiersWallS, ' s')]);
  $('metrics').innerHTML = `<tbody>${rows.map(([name, value]) => `<tr><td>${name}</td><td>${value}</td></tr>`).join('')}</tbody>`;
}

async function loadManifest(manifest) {
  if (!manifest?.preview) return;
  const token = ++loadToken;
  try {
    const showLoadPhase = async (text) => {
      setStatus(text);
      if (typeof setTimeout === 'function') {
        await new Promise((resolve) => setTimeout(resolve, 0));
      }
    };
    const preview = manifest.preview;
    manifest = {...manifest, preview};
    const refinementPassThrough = isFusionPassThrough(manifest);
    const hasTableRemoval = Boolean(manifest.preprocessing?.tableRemoval && preview.sharedTableMaskUrl);
    const hasPartition = Boolean(hasTableRemoval && manifest.preprocessing?.partition?.frame && preview.partitionZonesUrl);
    const hasFloatingZones = Boolean(manifest.preprocessing?.layering && (manifest.preprocessing?.floatingZones || controlNetInputStage(manifest.controlNet) === 'post-layering')
      && preview.sharedLayersUrl && preview.sharedFloatingNoiseUrl);
    const hasProjection = Boolean(manifest.projection && preview.projectionClassesUrl && preview.projectionLayersUrl);
    const hasFusion = Boolean(manifest.fusion && manifest.regions && preview.fusedClassesUrl && preview.fusedRegionsUrl);
    const hasFusionScores = Boolean(hasFusion && preview.fusedSteelScoreUrl && preview.fusedSteelEvidenceUrl);
    const hasRefinement = Boolean(manifest.refinement && preview.refinedClassesUrl && preview.refinedRegionsUrl && (preview.refinedZonesUrl || hasPartition) && preview.refinedChangedUrl);
    const hasInternalRebar = Boolean(manifest.internalRebar && preview.internalTypesUrl && preview.internalInstancesUrl && preview.internalSegmentsUrl && preview.internalConfidenceUrl);
    const hasControlNet = Boolean(manifest.controlNet);
    const hasCylinderDenoise = Boolean(manifest.cylinderDenoise);
    const coreUrls = [
      preview.positionsUrl, preview.normalsUrl, preview.colorsUrl, preview.validUrl,
      hasTableRemoval ? preview.sharedTableMaskUrl : null,
      hasPartition ? preview.partitionZonesUrl : null,
      hasFloatingZones ? preview.sharedLayersUrl : null,
      hasFloatingZones ? preview.sharedFloatingNoiseUrl : null,
      preview.classesUrl || null, preview.recoveredUrl || null,
      hasProjection ? preview.projectionClassesUrl : null,
      hasProjection ? preview.projectionLayersUrl : null,
      hasFusion ? preview.fusedClassesUrl : null,
      hasFusion ? preview.fusedRegionsUrl : null,
      hasFusion && preview.fusedRecoveredUrl ? preview.fusedRecoveredUrl : null,
      hasFusionScores ? preview.fusedSteelScoreUrl : null,
      hasFusionScores ? preview.fusedSteelEvidenceUrl : null,
      hasRefinement ? preview.refinedClassesUrl : null,
      hasRefinement ? preview.refinedRegionsUrl : null,
      hasRefinement && preview.refinedZonesUrl ? preview.refinedZonesUrl : null,
      hasRefinement ? preview.refinedChangedUrl : null,
      hasInternalRebar ? preview.internalTypesUrl : null,
      hasInternalRebar ? preview.internalInstancesUrl : null,
      hasInternalRebar ? preview.internalSegmentsUrl : null,
      hasInternalRebar ? preview.internalConfidenceUrl : null,
    ];
    const completeFileCount = manifest.completeRebar ? 4 + Number(Boolean(manifest.completeRebar.clusters)) : 0;
    const designPriorFileCount = manifest.designPrior?.enabled ? 5 : 0;
    const cylinderFileCount = hasCylinderDenoise ? 2 : 0;
    const controlNetFileCount = hasControlNet ? 2 : 0;
    const totalFiles = coreUrls.filter(Boolean).length + completeFileCount + designPriorFileCount + cylinderFileCount + controlNetFileCount;
    let completedFiles = 0, downloadedBytes = 0;
    const trackedFetch = async (url) => {
      const bytes = await fetchBytes(url);
      completedFiles += 1;
      downloadedBytes += bytes.byteLength;
      if (token === loadToken) setStatus(`正在下载预览数据… ${completedFiles} / ${totalFiles} · ${(downloadedBytes / 1048576).toFixed(1)} MiB`);
      return bytes;
    };
    await showLoadPhase(`正在下载预览数据… 0 / ${totalFiles}`);
    const [complete, designPrior, controlNet, cylinderDenoise, coreBytes] = await Promise.all([
      loadCompletePreview(manifest, trackedFetch),
      loadDesignPriorPreview(manifest, trackedFetch),
      loadControlNetPreview(manifest, trackedFetch),
      loadCylinderDenoisePreview(manifest, trackedFetch),
      Promise.all(coreUrls.map((url) => url ? trackedFetch(url) : Promise.resolve(null))),
    ]);
    const [positionBytes, normalBytes, colorBytes, validBytes, tableMaskBytes, partitionZoneBytes, sharedLayerBytes, sharedFloatingNoiseBytes, classBytes, recoveredBytes, projectionClassBytes, projectionLayerBytes, fusedClassBytes, fusedRegionBytes, fusedRecoveredBytes, fusedScoreBytes, fusedEvidenceBytes, refinedClassBytes, refinedRegionBytes, refinedZoneBytes, refinedChangedBytes, internalTypeBytes, internalInstanceBytes, internalSegmentBytes, internalConfidenceBytes] = coreBytes;
    if (token !== loadToken) return;
    await showLoadPhase(`正在校验 ${fmt(preview.pointCount)} 个预览点…`);
    const positions = new Float32Array(positionBytes), normals = new Float32Array(normalBytes);
    const colors = new Uint8Array(colorBytes), valid = new Uint8Array(validBytes);
    const sharedTableMask = tableMaskBytes ? new Uint8Array(tableMaskBytes) : null;
    const partitionZones = partitionZoneBytes ? new Uint8Array(partitionZoneBytes) : null;
    const sharedLayers = sharedLayerBytes ? new Uint8Array(sharedLayerBytes) : null;
    const sharedFloatingNoise = sharedFloatingNoiseBytes ? new Uint8Array(sharedFloatingNoiseBytes) : null;
    const classes = classBytes ? new Uint8Array(classBytes) : null;
    const recovered = recoveredBytes ? new Uint8Array(recoveredBytes) : null;
    const projectionClasses = projectionClassBytes ? new Uint8Array(projectionClassBytes) : null;
    const projectionLayers = projectionLayerBytes ? new Uint8Array(projectionLayerBytes) : null;
    const fusedClasses = fusedClassBytes ? new Uint8Array(fusedClassBytes) : null;
    const fusedRegions = fusedRegionBytes ? new Uint8Array(fusedRegionBytes) : null;
    const fusedRecovered = fusedRecoveredBytes ? new Uint8Array(fusedRecoveredBytes) : null;
    const fusedSteelScores = fusedScoreBytes ? new Float32Array(fusedScoreBytes) : null;
    const fusedSteelEvidence = fusedEvidenceBytes ? new Uint8Array(fusedEvidenceBytes) : null;
    const refinedClasses = refinedClassBytes ? new Uint8Array(refinedClassBytes) : null;
    const refinedRegions = refinedRegionBytes ? new Uint8Array(refinedRegionBytes) : null;
    const refinedZones = refinedZoneBytes ? new Uint8Array(refinedZoneBytes) : (hasRefinement ? partitionZones : null);
    const refinedChanged = refinedChangedBytes ? new Uint8Array(refinedChangedBytes) : null;
    const internalTypes = internalTypeBytes ? new Uint8Array(internalTypeBytes) : null;
    const internalInstances = internalInstanceBytes ? new Uint32Array(internalInstanceBytes) : null;
    const internalSegments = internalSegmentBytes ? new Uint32Array(internalSegmentBytes) : null;
    const internalConfidence = internalConfidenceBytes ? new Float32Array(internalConfidenceBytes) : null;
    const count = preview.pointCount;
    if (positions.length !== count * 3 || normals.length !== count * 3 || colors.length !== count * 3 || valid.length !== count || (sharedTableMask && sharedTableMask.length !== count) || (partitionZones && partitionZones.length !== count) || (sharedLayers && sharedLayers.length !== count) || (sharedFloatingNoise && sharedFloatingNoise.length !== count) || (classes && classes.length !== count) || (recovered && recovered.length !== count) || (projectionClasses && projectionClasses.length !== count) || (projectionLayers && projectionLayers.length !== count) || (fusedClasses && fusedClasses.length !== count) || (fusedRegions && fusedRegions.length !== count) || (fusedRecovered && fusedRecovered.length !== count) || (fusedSteelScores && fusedSteelScores.length !== count) || (fusedSteelEvidence && fusedSteelEvidence.length !== count) || (refinedClasses && refinedClasses.length !== count) || (refinedRegions && refinedRegions.length !== count) || (refinedZones && refinedZones.length !== count) || (refinedChanged && refinedChanged.length !== count) || (internalTypes && internalTypes.length !== count) || (internalInstances && internalInstances.length !== count) || (internalSegments && internalSegments.length !== count) || (internalConfidence && internalConfidence.length !== count)) {
      throw new Error('预览数组长度与 Manifest 元数据不一致');
    }
    if (sharedTableMask?.some((value) => value > 1)) throw new Error('共享台面预览无效：掩码只能为 0 或 1');
    if (controlNet) {
      const postFusion = controlNetInputStage(manifest.controlNet) === 'post-fusion';
      if (!sharedTableMask || (postFusion && (!fusedClasses || !sharedLayers))) {
        throw new Error(postFusion ? '03 后控制网缺少融合类别或共享分层预览' : '控制网预览缺少共享台面移除掩码');
      }
      const invalidStageStatus = controlNet.status.some((value, index) =>
        !controlNetStageStatusValid(value, sharedTableMask[index], fusedClasses?.[index], postFusion));
      if (invalidStageStatus) throw new Error(postFusion
        ? '03 后控制网状态必须区分融合候选与融合排除点'
        : '早期控制网预览与共享台面移除掩码不一致');
    }
    if (partitionZones?.some((value) => value > 3)) throw new Error('共享钢筋分区预览无效：分区标签必须为 0 至 3');
    const declaredLayerIds = new Set((manifest.preprocessing?.layering?.layers || []).map(layer => Number(layer.id)).filter(Number.isFinite));
    if (sharedLayers?.some((value) => value !== 0 && declaredLayerIds.size && !declaredLayerIds.has(value))) {
      throw new Error('共享钢筋分层预览包含 Manifest 未声明的层编号');
    }
    if (sharedFloatingNoise?.some((value) => value > 1)) throw new Error('共享禁飞区预览无效：掩码只能为 0 或 1');
    if ((manifest.preprocessing?.layering || manifest.preprocessing?.floatingZones) && !hasFloatingZones) {
      throw new Error('钢筋分层与禁飞区 Manifest 缺少共享分层或禁飞区候选预览数据');
    }
    if (fusedSteelScores?.some((value) => !Number.isFinite(value) || value < 0 || value > 1)) throw new Error('融合支持分数无效：数值必须位于 0 至 1');
    if (fusedSteelEvidence?.some((value) => value > 15)) throw new Error('融合证据位掩码无效：仅支持 A、B、轴线恢复和共享分区归属');
    if (recovered && !classes) throw new Error('恢复掩码缺少对应的分类预览数据');
    if (designPrior && !complete) throw new Error('设计先验结果缺少整簇归并基线预览数据');
    if (cylinderDenoise && !complete) throw new Error('圆柱先验结果缺少第六步实例基线预览数据');
    if (recovered?.some((value, index) => value > 1 || (value === 1 && classes[index] !== 3))) {
      throw new Error('恢复掩码无效：只允许以 0/1 标记本轮归还为钢筋的预览点');
    }
    if (manifest.projection && !hasProjection) throw new Error('投影分类 Manifest 缺少预览分类或分层数据');
    if ((manifest.fusion || manifest.regions) && !hasFusion) throw new Error('融合 Manifest 缺少语义类别或区域预览数据');
    if (manifest.refinement && !hasRefinement) throw new Error('空间整理 Manifest 缺少类别、区域、分区或变化预览数据');
    if (manifest.internalRebar && !hasInternalRebar) throw new Error('内部钢筋 Manifest 缺少类型、实例、拟合段或置信度预览数据');
    if (manifest.refinement && !hasFusion) throw new Error('空间整理 Manifest 缺少作为变化基线的融合预览数据');
    if (projectionClasses?.some((value) => projectionClassValueInvalid(value, manifest))) {
      throw new Error(manifest.projection?.pendingClass === 0
        ? '投影分类预览无效：类别标签必须为 0 至 4'
        : '投影分类预览无效：类别标签必须为 1、2、3 或 4（噪音）');
    }
    const projectionLayerIds = new Set((manifest.projection?.layers || []).map((layer) => Number(layer.id)));
    if (projectionLayers?.some((value) => value !== 0 && !projectionLayerIds.has(value))) {
      throw new Error('投影分层预览包含 Manifest 未声明的层编号');
    }
    if (fusedClasses?.some((value) => value < 1 || value > 4)) {
      throw new Error('融合语义预览无效：类别标签必须为 1、2、3 或 4（噪音）');
    }
    if (fusedRegions?.some((value) => value > 4)) {
      throw new Error('融合区域预览无效：区域标签必须为 0 至 5');
    }
    if (fusedRecovered?.some((value, index) => value > 1 || (value === 1 && fusedClasses[index] !== 3))) {
      throw new Error('融合恢复掩码无效：只能标记最终类别为钢筋的点');
    }
    if (refinedClasses?.some((value) => value < 1 || value > 4)) {
      throw new Error('空间整理类别预览无效：类别标签必须为 1、2、3 或 4（噪音）');
    }
    if (refinedRegions?.some((value) => value > 4)) {
      throw new Error('空间整理区域预览无效：区域标签必须为 0 至 5');
    }
    if (refinedZones?.some((value) => value > 3)) {
      throw new Error('双框几何分区预览无效：分区标签必须为 0 至 3');
    }
    if (refinedChanged?.some((value, index) => value > 1 || value !== Number(refinedClasses[index] !== fusedClasses?.[index]))) {
      throw new Error('空间整理变化掩码无效：必须准确标记相对融合类别发生变化的点');
    }
    if (refinementPassThrough && (refinedClasses?.some((value, index) => value !== fusedClasses?.[index])
      || refinedRegions?.some((value, index) => value !== fusedRegions?.[index])
      || refinedZones?.some((value, index) => value !== partitionZones?.[index]))) {
      throw new Error('沿用融合结果的数据与第 03 步或共享分区不一致');
    }
    if (refinedRegions?.some((region, index) =>
      (refinedClasses[index] !== 4 && ((region === 0 && refinedClasses[index] !== 1)
      || (region === 3 && refinedClasses[index] !== 2)
      || ([1, 2, 4].includes(region) && refinedClasses[index] !== 3))))) {
      throw new Error('空间整理类别与区域不一致');
    }
    if (internalTypes?.some((value) => value > 5)) {
      throw new Error('内部钢筋类型预览无效：标签必须为 0 至 5');
    }
    if (internalConfidence?.some((value) => !Number.isFinite(value) || value < 0 || value > 1)) {
      throw new Error('内部钢筋置信度预览无效：数值必须位于 0 至 1');
    }
    if (internalTypes?.some((type, index) =>
      ((type === 0 || type === 4 || type === 5) && (internalInstances[index] !== 0 || internalSegments[index] !== 0))
      || (type >= 1 && type <= 3 && (internalInstances[index] === 0 || internalSegments[index] === 0)))) {
      throw new Error('内部钢筋预览标签不一致：非内部或实例待定点编号必须为 0，已分类钢筋必须有实例与拟合段编号');
    }
    if (manifest.internalRebar) {
      const instances = manifest.internalRebar.instances;
      const segments = manifest.internalRebar.segments;
      const expectedTypes = Object.fromEntries(Object.entries(internalTypeNames).filter(([id]) => id !== '5' || manifest.internalRebar.denoising).map(([id, name]) => [String(id), name]));
      const counts = manifest.internalRebar.counts || {};
      const countValues = ['lower', 'upper', 'web', 'unassigned'].map((name) => counts[name]);
      countValues.push(counts.noise ?? 0);
      if (!Array.isArray(instances) || !Array.isArray(segments)) {
        throw new Error('内部钢筋 Manifest 的实例和拟合段清单必须为数组');
      }
      if (Object.entries(expectedTypes).some(([id, name]) => id === '4'
        ? !['待分配', '实例待定', '钢筋（实例待定）', '待定钢筋'].includes(manifest.internalRebar.types?.[id])
        : id === '5' ? !['悬浮噪音', '噪音'].includes(manifest.internalRebar.types?.[id]) : manifest.internalRebar.types?.[id] !== name)) {
        throw new Error('内部钢筋 Manifest 的类型标签说明不完整或不一致');
      }
      if (!Number.isSafeInteger(manifest.internalRebar.pointCount) || manifest.internalRebar.pointCount < 0
        || countValues.some((value) => !Number.isSafeInteger(value) || value < 0)
        || countValues.reduce((sum, value) => sum + value, 0) !== manifest.internalRebar.pointCount) {
        throw new Error('内部钢筋 Manifest 的范围点数与类型计数不一致');
      }
      if (manifest.internalRebar.instanceCount !== instances.length || manifest.internalRebar.segmentCount !== segments.length) {
        throw new Error('内部钢筋 Manifest 的实例或拟合段计数与清单长度不一致');
      }
      const instanceIds = new Set();
      for (const instance of instances) {
        if (!Number.isSafeInteger(instance?.id) || instance.id <= 0 || instanceIds.has(instance.id)
          || ![1, 2, 3].includes(instance.type) || !Array.isArray(instance.segmentIds)
          || instance.segmentIds.length < 1 || new Set(instance.segmentIds).size !== instance.segmentIds.length
          || instance.segmentIds.some((id) => !Number.isSafeInteger(id) || id <= 0)
          || !Number.isSafeInteger(instance.pointCount) || instance.pointCount < 0
          || !Number.isFinite(instance.lengthM) || instance.lengthM < 0
          || typeof instance.modelKind !== 'string' || !instance.modelKind.trim()
          || ('family' in instance && ![1, 2, 3, 4].includes(instance.family))
          || ('diameterM' in instance && (!Number.isFinite(instance.diameterM) || instance.diameterM <= 0))
          || ('lengthAnomaly' in instance && typeof instance.lengthAnomaly !== 'boolean')) {
          throw new Error('内部钢筋实例清单包含无效或重复的编号/类型');
        }
        instanceIds.add(instance.id);
      }
      const segmentIds = new Set();
      for (const segment of segments) {
        const endpoints = [segment?.startM, segment?.endM];
        if (!Number.isSafeInteger(segment?.id) || segment.id <= 0 || segmentIds.has(segment.id)
          || !instanceIds.has(segment.instanceId)
          || ![1, 2, 3].includes(segment.type)
          || endpoints.some((point) => !Array.isArray(point) || point.length !== 3 || point.some((value) => !Number.isFinite(value)))
          || !Number.isFinite(segment.radiusM) || segment.radiusM <= 0
          || !Number.isSafeInteger(segment.pointCount) || segment.pointCount < 0) {
          throw new Error('内部钢筋拟合段清单包含无效编号、实例引用或几何参数');
        }
        segmentIds.add(segment.id);
      }
      const segmentOwner = new Map(segments.map((segment) => [segment.id, segment.instanceId]));
      const declaredSegmentIds = new Set(instances.flatMap((instance) => instance.segmentIds));
      if (declaredSegmentIds.size !== segments.length
        || segments.some((segment) => !declaredSegmentIds.has(segment.id))
        || instances.some((instance) => instance.segmentIds.some((id) => segmentOwner.get(id) !== instance.id))) {
        throw new Error('内部钢筋实例的拟合段清单与分段归属不一致');
      }
      if (internalInstances?.some((id) => id > 0 && !instanceIds.has(id))
        || internalSegments?.some((id) => id > 0 && !segmentIds.has(id))) {
        throw new Error('内部钢筋预览引用了 Manifest 未声明的实例或拟合段编号');
      }
    }
    if (manifest.designPrior) {
      const prior = manifest.designPrior;
      if (!['off','geometry','topology'].includes(prior.mode) || typeof prior.enabled !== 'boolean' || !prior.counts || !prior.inventory) throw new Error('设计先验 Manifest 缺少模式、统计或设计清单');
      if (prior.enabled && !designPrior) throw new Error('设计先验已启用但预览数据缺失');
    }
    const internalInstanceById = new Map((manifest.internalRebar?.instances || []).map((instance) => [instance.id, instance]));
    const internalFamilies = internalInstances ? new Uint8Array(internalInstances.length) : null;
    if (internalFamilies) {
      for (let index = 0; index < internalInstances.length; index += 1) {
        internalFamilies[index] = internalInstanceById.get(internalInstances[index])?.family || 0;
      }
    }
    await showLoadPhase('正在构建预览场景…');
    if (token !== loadToken) return;
    const nextRaw = new THREE.BufferGeometry(), nextNormal = new THREE.BufferGeometry();
    nextRaw.setAttribute('position', new THREE.BufferAttribute(positions, 3));
    nextRaw.setAttribute('color', new THREE.BufferAttribute(colors, 3, true));
    nextRaw.computeBoundingSphere();
    const previewBounds = nextRaw.boundingSphere.clone();
    nextNormal.setAttribute('position', new THREE.BufferAttribute(positions, 3));
    nextNormal.boundingSphere = previewBounds.clone();
    let nextTableRemoval = null;
    if (sharedTableMask) {
      nextTableRemoval = new THREE.BufferGeometry();
      nextTableRemoval.setAttribute('position', new THREE.BufferAttribute(positions, 3));
      nextTableRemoval.setAttribute('color', new THREE.BufferAttribute(colors, 3, true));
      nextTableRemoval.boundingSphere = previewBounds.clone();
    }
    let nextPartition = null;
    if (partitionZones) {
      nextPartition = new THREE.BufferGeometry();
      nextPartition.setAttribute('position', new THREE.BufferAttribute(positions, 3));
      nextPartition.setAttribute('color', new THREE.BufferAttribute(zoneColors(partitionZones), 3));
      nextPartition.boundingSphere = previewBounds.clone();
    }
    let nextFloatingZones = null;
    if (sharedLayers && sharedFloatingNoise) {
      nextFloatingZones = new THREE.BufferGeometry();
      nextFloatingZones.setAttribute('position', new THREE.BufferAttribute(positions, 3));
      nextFloatingZones.setAttribute('color', new THREE.BufferAttribute(floatingZoneColors(sharedLayers, sharedFloatingNoise), 3));
      nextFloatingZones.boundingSphere = previewBounds.clone();
    }
    let nextClass = null;
    if (classes) {
      nextClass = new THREE.BufferGeometry();
      nextClass.setAttribute('position', new THREE.BufferAttribute(positions, 3));
      nextClass.setAttribute('color', new THREE.BufferAttribute(classColors(classes), 3));
      nextClass.boundingSphere = previewBounds.clone();
    }
    let nextProjection = null;
    if (projectionClasses) {
      nextProjection = new THREE.BufferGeometry();
      nextProjection.setAttribute('position', new THREE.BufferAttribute(positions, 3));
      nextProjection.setAttribute('color', new THREE.BufferAttribute(classColors(projectionClasses), 3));
      nextProjection.boundingSphere = previewBounds.clone();
    }
    let nextFusion = null;
    if (fusedClasses) {
      nextFusion = new THREE.BufferGeometry();
      nextFusion.setAttribute('position', new THREE.BufferAttribute(positions, 3));
      nextFusion.setAttribute('color', new THREE.BufferAttribute(regionColors(fusedRegions, manifest.regions?.colors), 3));
      nextFusion.boundingSphere = previewBounds.clone();
    }
    let nextRefinement = null;
    if (refinedClasses) {
      nextRefinement = new THREE.BufferGeometry();
      nextRefinement.setAttribute('position', new THREE.BufferAttribute(positions, 3));
      nextRefinement.setAttribute('color', new THREE.BufferAttribute(regionColors(refinedRegions), 3));
      nextRefinement.boundingSphere = previewBounds.clone();
    }
    let nextInternalRebar = null;
    if (internalTypes) {
      nextInternalRebar = new THREE.BufferGeometry();
      nextInternalRebar.setAttribute('position', new THREE.BufferAttribute(positions, 3));
      nextInternalRebar.setAttribute('color', new THREE.BufferAttribute(internalColors('types', internalTypes, internalInstances, internalConfidence), 3));
      nextInternalRebar.boundingSphere = previewBounds.clone();
    }
    releasePreview();
    rawGeometry = nextRaw; normalGeometry = nextNormal; tableRemovalGeometry = nextTableRemoval; partitionGeometry = nextPartition; floatingZonesGeometry = nextFloatingZones; classGeometry = nextClass; projectionGeometry = nextProjection; fusionGeometry = nextFusion; refinementGeometry = nextRefinement; internalRebarGeometry = nextInternalRebar;
    rawPoints.geometry = rawGeometry; normalPoints.geometry = normalGeometry;
    tableRemovalPoints.geometry = tableRemovalGeometry || emptyClassGeometry;
    partitionPoints.geometry = partitionGeometry || emptyClassGeometry;
    floatingZonesPoints.geometry = floatingZonesGeometry || emptyClassGeometry;
    classPoints.geometry = classGeometry || emptyClassGeometry;
    projectionPoints.geometry = projectionGeometry || emptyClassGeometry;
    fusionPoints.geometry = fusionGeometry || emptyClassGeometry;
    refinementPoints.geometry = refinementGeometry || emptyClassGeometry;
    internalRebarPoints.geometry = internalRebarGeometry || emptyClassGeometry;
    current = { ...manifest, _complete: complete, _designPrior: designPrior, _controlNet: controlNet, _cylinderDenoise: cylinderDenoise, _positions: positions, _normals: normals, _valid: valid, _sharedTableMask: sharedTableMask, _partitionZones: partitionZones, _sharedLayers: sharedLayers, _sharedFloatingNoise: sharedFloatingNoise, _classes: classes, _recovered: recovered, _projectionClasses: projectionClasses, _projectionLayers: projectionLayers, _fusedClasses: fusedClasses, _fusedRegions: fusedRegions, _fusedRecovered: fusedRecovered, _fusedSteelScores: fusedSteelScores, _fusedSteelEvidence: fusedSteelEvidence, _refinedClasses: refinedClasses, _refinedRegions: refinedRegions, _refinedZones: refinedZones, _refinedChanged: refinedChanged, _internalTypes: internalTypes, _internalInstances: internalInstances, _internalSegments: internalSegments, _internalConfidence: internalConfidence, _internalFamilies: internalFamilies, _internalInstanceById: internalInstanceById };
    rebuildSpatialInstancePalettes();
    installCompletePreview();
    installControlNetPreview();
    installCylinderDenoisePreview();
    installDesignPriorPreview();
    if (hasPartition || hasRefinement || hasInternalRebar) $('frameOverlay').checked = true;
    rebuildFrameOverlays();
    rebuildFloatingGeometryOverlays();
    $('empty').hidden = true;
    $('sample').textContent = `样本 ${fmt(preview.pointCount)} / 全量 ${fmt(preview.totalPointCount)}`;
    $('source').textContent = `源文件：${manifest.source?.name || '未知'} · ${fmt(manifest.source?.pointCount)} 点`;
    metrics(manifest);
    $('showRemovedTable').checked = false;
    $('partitionZoneFilter').value = 'all';
    const floatingLayerOptions = [['all', '全部非台面点'], ['0', '未分层'], ...(manifest.preprocessing?.layering?.layers || []).map(layer => [String(layer.id), layer.name || `层 ${layer.id}${Number.isFinite(layer.heightM) ? ` · ${(layer.heightM * 1000).toFixed(1)} mm` : ''}`])];
    $('floatingLayerFilter').replaceChildren(...floatingLayerOptions.map(([value, label]) => new Option(label, value)));
    $('floatingLayerFilter').value = 'all';
    $('floatingForbiddenOnly').checked = false;
    $('tableRemovalStep').disabled = !sharedTableMask;
    $('controlNetStep').disabled = !controlNet;
    $('partitionStep').disabled = !partitionZones;
    $('floatingZonesStep').disabled = !(sharedLayers && sharedFloatingNoise);
    const layeringOnly = controlNetInputStage(manifest.controlNet) === 'post-layering';
    $('floatingZonesControls').querySelector('h2').textContent = layeringOnly ? '01D 钢筋分层' : '01D 钢筋分层与禁飞区';
    $('floatingForbiddenOnly').closest('label').hidden = layeringOnly;
    if (layeringOnly) $('floatingForbiddenOnly').checked = false;
    $('floatingHint').textContent = layeringOnly ? '分为底层钢筋、顶层钢筋、腹杆层；本轮停在禁飞区划分之前，以非台面点作为初始候选，后续阶段只使用尚未锁定的候选点。' : '分为底层钢筋、顶层钢筋、腹杆层；红色为禁飞区候选，05 步剔除包络外的全部钢筋点。';
    $('classificationStep').disabled = !classes;
    $('projectionStep').disabled = !projectionClasses;
    $('fusionStep').disabled = !fusedClasses;
    $('refinementStep').hidden = refinementPassThrough;
    $('refinementStep').disabled = !refinedClasses || refinementPassThrough;
    $('internalRebarStep').disabled = !internalTypes;
    $('cylinderDenoiseStep').disabled = !cylinderDenoise;
    $('classRecoveredControl').hidden = !recovered;
    $('fusionRecoveredControl').hidden = !fusedRecovered;
    if (!recovered) $('classRecoveredOnly').checked = false;
    if (!fusedRecovered) $('fusionRecoveredOnly').checked = false;
    const threeClass = manifest.classification?.classPolicy === 'table-rebar-fixture-remainder';
    const hasClassificationPending = manifest.classification?.pendingClass === 0;
    $('unknownLegend').hidden = threeClass && !hasClassificationPending;
    $('unknownLegendLabel').textContent = hasClassificationPending ? '区域保留的待定候选' : '待定（旧版结果）';
    const previousFilter = $('classFilter').value;
    const filters = [['all', '全部'], ['3', '仅钢筋'], ['4', '仅噪音'], ['1', '仅台面'], ['2', '仅夹具（含方管）']];
    if (recovered) filters.splice(2, 0, ['recovered', '仅本轮归还的钢筋']);
    if (hasClassificationPending) filters.push(['0', '仅待定候选']);
    else if (!threeClass) filters.push(['0', '仅待定（旧版）'], ['excludeStatic', '排除台面与夹具']);
    $('classFilter').replaceChildren(...filters.map(([value, label]) => new Option(label, value)));
    $('classFilter').value = filters.some(([value]) => value === previousFilter) ? previousFilter : 'all';
    $('classHint').textContent = manifest.classification?.retentionRestored
      ? '保留 02A 的几何分类、近邻钢筋、末端与腹杆恢复结果。区域保留继续生效；03 单独记录评分证据。'
      : hasClassificationPending
      ? `类别 0 是区域保留的待定候选，类别 3 仅表示本分支独立实测的钢筋。${recovered ? '可勾选仅本步恢复的钢筋作额外筛选。' : ''}`
      : threeClass
      ? `台面和钢筋以外统一归夹具；方管侧面、圆角、边缘属于同一类。${recovered ? '可勾选仅本步恢复的钢筋作额外筛选。' : ''}`
      : '旧版分类结果：待定点保留原有类别。筛选只改变显示。';
    const previousProjectionClass = $('projectionClassFilter').value;
    const projectionClassOptions = [['all', '全部'], ['1', '台面'], ['2', '夹具'], ['3', '钢筋'], ['4', '噪音']];
    $('projectionClassFilter').replaceChildren(...projectionClassOptions.map(([value, label]) => new Option(label, value)));
    $('projectionClassFilter').value = projectionClassOptions.some(([value]) => value === previousProjectionClass) ? previousProjectionClass : 'all';
    $('projectionHint').textContent = manifest.projection?.retentionRestored
      ? '保留 02B 的投影分类、腹杆恢复及上下层高度范围。高度保留计入 B 路支持；共享区域保留不重复计分。'
      : manifest.projection?.pendingClass === 0
      ? '类别 0 是高度保留的待定候选，类别 3 仅表示本分支独立实测的钢筋。'
      : '02B 是独立的投影图像分类结果；筛选不会合并或修改 02A 结果。';
    const previousLayer = $('projectionLayerFilter').value;
    const layerOptions = [['all', '全部高度'], ['0', '层间 / 未归层'], ...(manifest.projection?.layers || []).map((layer) => [String(layer.id), `${layer.name || `层 ${layer.id}${Number.isFinite(layer.heightM) ? ` · ${(layer.heightM * 1000).toFixed(1)} mm` : ''}`} · Z ${fmtHeight(layer.heightM)}`])];
    $('projectionLayerFilter').replaceChildren(...layerOptions.map(([value, label]) => new Option(label, value)));
    $('projectionLayerFilter').value = layerOptions.some(([value]) => value === previousLayer) ? previousLayer : 'all';
    $('layerSummary').replaceChildren(...(manifest.projection?.layers || []).map((layer) => {
      const row = document.createElement('span');
      row.innerHTML = `<b>${layer.name || `Z 层 ${layer.id}`}</b><i>Z ${fmtHeight(layer.lowM)} – ${fmtHeight(layer.highM)} · 峰值 ${fmtHeight(layer.heightM)} · 距台面 ${fmtHeight(layer.relativeHeightM)}</i>`;
      return row;
    }));
    document.querySelectorAll('[data-projection-view]').forEach((button) => {
      const view = button.dataset.projectionView;
      button.disabled = view !== '3d' && !manifest.projection?.images?.[view]?.url;
      if (view !== '3d' && manifest.projection?.images?.[view]?.title) button.title = manifest.projection.images[view].title;
    });
    const previousFusionClass = $('fusionClassFilter').value;
    const fusionClassOptions = [['all', '全部语义类别'], ['3', '仅钢筋（隐藏台面与夹具）']];
    if (fusedRecovered) fusionClassOptions.push(['recovered', '仅几何规则归还的钢筋']);
    $('fusionClassFilter').replaceChildren(...fusionClassOptions.map(([value, label]) => new Option(label, value)));
    $('fusionClassFilter').value = fusionClassOptions.some(([value]) => value === previousFusionClass) ? previousFusionClass : 'all';
    const previousFusionRegion = $('fusionRegionFilter').value;
    const regionNames = defaultRegionNames;
    const fusionRegionOptions = [['all', '全部区域'], ...[1, 2, 3, 4].map((value) => [String(value), `仅${regionNames[value]}`])];
    $('fusionRegionFilter').replaceChildren(...fusionRegionOptions.map(([value, label]) => new Option(label, value)));
    $('fusionRegionFilter').value = fusionRegionOptions.some(([value]) => value === previousFusionRegion) ? previousFusionRegion : 'all';
    const scoreAvailable = Boolean(fusedSteelScores && fusedSteelEvidence);
    $('internalScoreColorOption').disabled = !scoreAvailable;
    $('completeScoreColorOption').disabled = !scoreAvailable;
    if (!scoreAvailable && $('internalColorMode').value === 'score') $('internalColorMode').value = 'types';
    if (!scoreAvailable && $('completeColorMode').value === 'score') $('completeColorMode').value = 'instances';
    for (const option of $('fusionColorMode').options) if (option.value === 'score') option.disabled = !scoreAvailable;
    for (const option of $('fusionScoreFilter').options) option.disabled = option.value !== 'all' && !scoreAvailable;
    if (!scoreAvailable && $('fusionColorMode').value === 'score') $('fusionColorMode').value = 'classes';
    if (!scoreAvailable) $('fusionScoreFilter').value = 'all';
    $('fusionHint').textContent = scoreAvailable
      ? `支持分数是融合规则值，不是校准概率；点击右侧点云可查看证据来源。${fusedRecovered ? '可另外筛选本步恢复的钢筋。' : ''}`
      : '该历史结果没有逐点支持分数；仍可按统一类别查看融合结果。';
    updateFusionLegend();
    updateRefinementLegend();
    if (internalTypes) {
      const tracks = manifest.internalRebar.tracks;
      const familyNames = internalFamilyNames(manifest.internalRebar);
      const familyIds = [...new Set((manifest.internalRebar.instances || [])
        .map((instance) => instance.family).filter((family) => [1, 2, 3, 4].includes(family)))].sort();
      const hasTrackFamilies = Boolean(tracks && familyIds.length);
      $('internalFamilyControl').hidden = !hasTrackFamilies;
      $('internalFamilyColorOption').disabled = !hasTrackFamilies;
      if (!hasTrackFamilies && $('internalColorMode').value === 'families') $('internalColorMode').value = 'types';
      const previousFamily = $('internalFamilyFilter').value;
      const familyOptions = [['all', '全部构件族'], ...familyIds.map((family) => [String(family), `仅${familyNames[family] || `族 ${family}`}`])];
      $('internalFamilyFilter').replaceChildren(...familyOptions.map(([value, label]) => new Option(label, value)));
      $('internalFamilyFilter').value = familyOptions.some(([value]) => value === previousFamily) ? previousFamily : 'all';
      const previousInstance = $('internalInstanceFilter').value;
      const instanceOptions = [['all', '全部实例'], ...(manifest.internalRebar.instances || []).map((instance) => [
        String(instance.id),
        `#${instance.id} · ${instance.family ? `${familyNames[instance.family] || `族 ${instance.family}`} · ` : ''}${internalTypeNames[instance.type] || `类型 ${instance.type}`}${Number.isFinite(instance.diameterM) ? ` · Ø${fmtHeight(instance.diameterM)}` : ''} · ${fmt(instance.lengthM, ' m')}${instance.lengthAnomaly ? ' · 长度异常' : ''} · ${fmt(instance.pointCount)} 点`,
      ])];
      $('internalInstanceFilter').replaceChildren(...instanceOptions.map(([value, label]) => new Option(label, value)));
      $('internalInstanceFilter').value = instanceOptions.some(([value]) => value === previousInstance) ? previousInstance : 'all';
      const sampleScopeCount = internalTypes.reduce((sum, value) => sum + Number(value > 0), 0);
      const diameterSummary = diameterPriorSummary(manifest.internalRebar);
      const lengthSummary = observedLengthSummary(manifest.internalRebar);
      const ifcLengthSummary = ifcHorizontalLengthSummary(manifest.internalRebar);
      $('internalLayerSummary').replaceChildren(...[
        ['全量内部范围', `${fmt(manifest.internalRebar.pointCount)} 点`],
        ['预览内部范围', `${fmt(sampleScopeCount)} / ${fmt(preview.pointCount)} 样本点`],
        ['下层标高', fmtHeight(manifest.internalRebar.layers?.lowerM)],
        ['上层标高', fmtHeight(manifest.internalRebar.layers?.upperM)],
        ['实例 / 拟合段', `${fmt(manifest.internalRebar.instanceCount)} / ${fmt(manifest.internalRebar.segmentCount)}`],
        diameterSummary ? ['直径（来源见括号）', diameterSummary] : null,
        lengthSummary ? ['构件族代表跨距', `${lengthSummary}（点云内框观测）`] : null,
        ifcLengthSummary ? ['IFC 横筋设计长度', ifcLengthSummary] : null,
      ].filter(Boolean).map(([name, value]) => {
        const row = document.createElement('span');
        const label = document.createElement('b');
        const result = document.createElement('i');
        label.textContent = name;
        result.textContent = value;
        row.append(label, result);
        return row;
      }));
      $('sample').textContent = `样本 ${fmt(preview.pointCount)} / 全量 ${fmt(preview.totalPointCount)} · 内部范围样本 ${fmt(sampleScopeCount)}`;
      updateInternalLegend();
    }
    $('las').href = manifest.files?.lasUrl || '#';
    $('steelLas').href = manifest.files?.steelLasUrl || '#';
    $('steelLas').hidden = !manifest.files?.steelLasUrl;
    $('fixtureLas').href = manifest.files?.fixtureLasUrl || '#';
    $('fixtureLas').hidden = !manifest.files?.fixtureLasUrl;
    $('internalSteelLas').href = manifest.files?.internalSteelLasUrl || '#';
    $('internalSteelLas').hidden = !manifest.files?.internalSteelLasUrl;
    $('internalInstances').href = manifest.files?.internalInstancesUrl || '#';
    $('internalInstances').hidden = !manifest.files?.internalInstancesUrl;
    $('manifest').href = manifest.files?.manifestUrl || '#';
    $('downloads').hidden = false;
    applyNormalColors(); buildArrows();
    projectionView = '3d';
    showStep(defaultPreviewStep(manifest, {controlNet, cylinderDenoise, complete, internalTypes, refinedClasses, fusedClasses, projectionClasses, classes, partitionZones, sharedTableMask}));
    fit(rightScene === 'partition' ? 'top' : 'oblique');
    setStatus(`完成 · ${manifest.runId}`);
    window.pointcloudDebug = { current, renderer, rawScene, normalScene, tableRemovalScene, controlNetScene, partitionScene, classScene, projectionScene, fusionScene, refinementScene, internalRebarScene, completeRebarScene, designPriorScene, camera, loadManifest };
  } catch (error) {
    if (token !== loadToken) return;
    console.error(error);
    setStatus(error.message, true);
  }
}

async function loadRun(run) {
  if (!run?.runId) return;
  if (run.preview) return loadManifest(run);
  setStatus('正在读取运行信息…');
  const url = run.manifestUrl || `/runs/${encodeURIComponent(run.runId)}/manifest.json`;
  return loadManifest(await getJSON(url));
}

function controlNetHistoryModeLabel(run) {
  if (!['aligned', 'auto'].includes(run?.controlNetMode)) return '现有流程';
  const stage = run.controlNetInputStage === 'post-layering' ? '禁飞区前' : Number(run.throughStep) >= 4 ? '03X分层' : '台面后';
  return `控制网·${stage}·${run.controlNetMode === 'auto' ? '自动' : '粗对齐'}`;
}

function installRunHistory(runs) {
  const selected = requestedRun || current?.runId || '';
  const options = runs.slice(0, 40).map((run) => {
    const mode = controlNetHistoryModeLabel(run);
    const created = run.createdAt ? new Date(run.createdAt).toLocaleString('zh-CN', {hour12: false}) : run.runId;
    return new Option(`${mode} · ${created}`, run.runId);
  });
  if (!options.length) options.push(new Option('没有当前源文件的历史结果', ''));
  $('runHistory').replaceChildren(...options);
  $('runHistory').value = runs.some((run) => run.runId === selected) ? selected : options[0].value;
}

async function refreshRunHistory(force = false) {
  if (!force && Date.now() < historyRefreshAt) return;
  historyRefreshAt = Date.now() + 5000;
  try {
    const runs = await getJSON(`${api}/runs`);
    if (!Array.isArray(runs) || runs.some((run) => !run?.runId || !run.manifestUrl)) throw new Error('历史结果列表格式无效');
    installRunHistory(runs);
  } catch (error) {
    console.warn('无法读取运行历史：', error);
  }
}

async function status() {
  try {
    const state = await getJSON(`${api}/status`);
    const priorAvailable = state.priorAvailable === true;
    for (const option of $('controlNetMode').options) option.disabled = option.value !== 'off' && !priorAvailable;
    if (!priorAvailable && $('controlNetMode').value !== 'off') $('controlNetMode').value = 'off';
    updateControlNetRunMode();
    for (const option of $('priorMode').options) option.disabled = option.value !== 'off' && !priorAvailable;
    if (!priorAvailable && $('priorMode').value !== 'off') { $('priorMode').value = 'off'; if (['7', '8'].includes($('throughStep').value)) $('throughStep').value = '6'; }
    for (const option of $('throughStep').options) if (['7', '8'].includes(option.value)) option.disabled = !priorAvailable;
    const priorSummary = typeof state.priorSummary === 'string' ? state.priorSummary : state.priorSummary ? Object.entries(state.priorSummary).map(([key, value]) => `${key} ${value}`).join(' · ') : '';
    $('priorModeHint').textContent = priorAvailable ? `${priorSummary}；01D 将使用该模型划分禁飞区，上方开关仅控制第六步实例整理。` : (priorSummary || '当前源文件没有可用设计先验；仍可运行基线流程。');
    if (Number.isFinite(state.maxWorkers)) {
      $('workers').max = String(state.maxWorkers);
      if (!workerLimitInitialized) {
        $('workers').value = String(Math.min(Number($('workers').value), state.maxWorkers));
        workerLimitInitialized = true;
      }
    }
    $('source').textContent = `源文件：${state.sourceName || '未检测到'}${Number.isFinite(state.previewLimit) ? ` · 固定预览 ${fmt(state.previewLimit)} 点` : ''}${state.progress ? ` · ${state.progress.stage || ''} ${fmt(state.progress.completed)} / ${fmt(state.progress.total)}` : ''}`;
    if (requestedRun && current?.runId !== requestedRun) await loadRun({runId: requestedRun});
    const running = state.status === 'running';
    await refreshRunHistory();
    $('run').disabled = running;
    if (running) {
      suspendCompleteTilesForRun();
      setStatus(`处理中：${state.progress?.stage || '准备中'} · ${fmt(state.progress?.completed)} / ${fmt(state.progress?.total)}`);
      if (!poller) poller = setInterval(status, 1000);
    } else {
      completeTilesSuspendedForRun = false;
      if (poller) { clearInterval(poller); poller = null; }
      if (state.status === 'failed') setStatus(state.error || '处理失败', true);
      else if (state.status === 'complete' && !requestedRun && state.latest && current?.runId !== state.latest.runId) {
        await loadRun(state.latest);
      } else if (!current) setStatus('空闲 · 可开始新的全量处理');
    }
  } catch (error) {
    setStatus(`服务连接失败：${error.message}`, true);
  }
}

function runRequestPayload() {
  const controlNetMode = $('controlNetMode').value;
  const common = {k: Number($('k').value), workers: Number($('workers').value)};
  return controlNetMode === 'off'
    ? {...common, throughStep: Number($('throughStep').value), priorMode: $('priorMode').value, controlNetMode: 'off'}
    : {...common, throughStep: 2, priorMode: 'off', controlNetMode};
}

function updateControlNetRunMode() {
  const mode = $('controlNetMode').value;
  const experiment = mode !== 'off';
  $('run').textContent = experiment ? `↻ 运行台面后控制网（${mode === 'auto' ? '自动初始化' : '粗对齐'}）` : '↻ 重新运行';
  $('run').title = experiment ? '移除台面后开始控制网分阶段拟合，不执行分区、分层或分类' : '使用现有完整流程重新运行';
  $('controlNetModeHint').textContent = experiment
    ? `${mode === 'auto' ? '从台面移除后的扫描几何自动估计初始位姿' : '使用已有粗对齐位姿初始化'}；以非台面点作为初始候选，按直筋、短筋、腹杆依次拟合；确认点锁定，待定点保留。`
    : '控制网实验在台面移除后，以非台面点作为初始候选，按直筋、短筋、腹杆依次拟合；确认点锁定，待定点保留。';
}

$('run').addEventListener('click', async () => {
  try {
    $('run').disabled = true;
    const response = await fetch(`${api}/run`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(runRequestPayload()),
    });
    if (response.status === 409) throw new Error('已有任务正在运行');
    if (!response.ok) throw new Error(await response.text());
    suspendCompleteTilesForRun();
    requestedRun = null;
    historyRefreshAt = 0;
    const location = new URL(window.location.href); location.searchParams.delete('run');
    window.history.replaceState(null, '', location);
    setStatus('已提交，正在启动…');
    await status();
  } catch (error) {
    $('run').disabled = false;
    setStatus(error.message, true);
  }
});

$('controlNetMode').addEventListener('change', updateControlNetRunMode);
$('runHistory').addEventListener('change', async () => {
  const runId = $('runHistory').value;
  if (!runId || runId === current?.runId) return;
  requestedRun = runId;
  const location = new URL(window.location.href); location.searchParams.set('run', runId);
  window.history.replaceState(null, '', location);
  await loadRun({runId});
});

$('semanticFilter').addEventListener('change', () => {
  preferredSemanticTag = $('semanticFilter').value;
  semanticVisibilityCustomized = false;
  if (rightScene === 'completeRebar') { $('completeClassFilter').value = 'all'; $('completeInstanceFilter').value = 'all'; }
  for (const id of ['projectionLayerFilter', 'fusionScoreFilter', 'refinementZoneFilter', 'internalFamilyFilter', 'internalInstanceFilter']) $(id).value = 'all';
  for (const id of ['refinementChangedOnly', 'classRecoveredOnly', 'fusionRecoveredOnly']) $(id).checked = false;
  updateSemanticControls();
  applyCurrentSemanticFilter();
});

$('resultColorMode').addEventListener('change', () => {
  preferredResultColorMode = $('resultColorMode').value;
  updateResultColorControls();
  updateSemanticControls();
  applyCurrentSemanticFilter();
});

$('throughStep').addEventListener('change', () => { $('priorMode').value = ['7', '8'].includes($('throughStep').value) ? 'topology' : 'off'; });
$('priorMode').addEventListener('change', () => { if ($('priorMode').value === 'off') { if (['7', '8'].includes($('throughStep').value)) $('throughStep').value = '6'; } else $('throughStep').value = '8'; });
$('controlNetView').addEventListener('change', applyControlNetAppearance);
$('controlNetColorMode').addEventListener('change', applyControlNetAppearance);
$('controlNetLayerFilter').addEventListener('change', () => { rebuildControlNetUnitOptions(); applyControlNetAppearance(); if (!compareSource) fit(lastView); });
for (const id of ['controlNetFitOverlay', 'controlNetInitializationOverlay']) $(id).addEventListener('change', applyControlNetAppearance);
$('controlNetUnitFilter').addEventListener('change', () => { applyControlNetAppearance(); if (!compareSource) fit(lastView); });
$('controlNetShortReview').addEventListener('click', focusControlNetShort);
for (const [id, direction] of [['controlNetPrevious', -1], ['controlNetNext', 1]]) $(id).addEventListener('click', () => {
  const options = Array.from($('controlNetUnitFilter').options).slice(1);
  if (!options.length) return;
  const currentIndex = options.findIndex((option) => option.value === $('controlNetUnitFilter').value);
  const nextIndex = currentIndex < 0 ? (direction > 0 ? 0 : options.length - 1) : (currentIndex + direction + options.length) % options.length;
  $('controlNetUnitFilter').value = options[nextIndex].value;
  applyControlNetAppearance(); if (!compareSource) fit(lastView);
});
$('completeCompare').addEventListener('change', () => { rebuildCompleteInstanceOptions(); applyCompleteAppearance(); });
for (const id of ['completeColorMode', 'completeAxes']) $(id).addEventListener('change', applyCompleteAppearance);
for (const id of ['priorCompare', 'priorColorMode', 'priorFilter', 'priorLines']) $(id).addEventListener('change', applyDesignPriorAppearance);
$('cylinderCompare').addEventListener('change', applyCylinderDenoiseAppearance);
$('cylinderOverlay').addEventListener('change', applyCylinderDenoiseAppearance);
$('cylinderInstanceFilter').addEventListener('change', () => { applyCylinderDenoiseAppearance(); if (!compareSource) fit(lastView); });
for (const [id, direction] of [['cylinderPrevious', -1], ['cylinderNext', 1]]) $(id).addEventListener('click', () => {
  const options = Array.from($('cylinderInstanceFilter').options).slice(1);
  if (!options.length) return;
  const currentIndex = options.findIndex((option) => option.value === $('cylinderInstanceFilter').value);
  const nextIndex = currentIndex < 0 ? (direction > 0 ? 0 : options.length - 1) : (currentIndex + direction + options.length) % options.length;
  $('cylinderInstanceFilter').value = options[nextIndex].value;
  applyCylinderDenoiseAppearance(); if (!compareSource) fit(lastView);
});
$('completeClassFilter').addEventListener('change', () => { $('completeInstanceFilter').value = 'all'; applyCompleteAppearance(); });
$('completeInstanceFilter').addEventListener('change', () => { $('completeClassFilter').value = '3'; applyCompleteAppearance(); if (!compareSource) fit(lastView); });
$('mode').addEventListener('change', applyNormalColors);
$('arrows').addEventListener('change', buildArrows);
$('length').addEventListener('input', () => { $('lengthValue').textContent = Number($('length').value).toFixed(3); buildArrows(); });
$('size').addEventListener('input', () => {
  $('sizeValue').textContent = $('size').value;
  rawMaterial.size = normalMaterial.size = tableRemovalMaterial.size = controlNetMaterial.size = partitionMaterial.size = floatingZonesMaterial.size = classMaterial.size = projectionMaterial.size = fusionMaterial.size = refinementMaterial.size = internalRebarMaterial.size = completeMaterial.size = designPriorPoints.material.size = cylinderDenoisePoints.material.size = Number($('size').value);
  for (const record of completeTileRecords.values()) {
    for (const part of record.parts) {
      const materials = Array.isArray(part.object.material) ? part.object.material : [part.object.material];
      for (const material of materials) material.size = Number($('size').value);
    }
  }
  requestRender();
});
$('showRemovedTable').addEventListener('change', applyTableRemovalAppearance);
$('partitionZoneFilter').addEventListener('change', applyPartitionAppearance);
$('classFilter').addEventListener('change', applyClassFilter);
$('classRecoveredOnly').addEventListener('change', applyClassFilter);
$('fusionRecoveredOnly').addEventListener('change', applyFusionAppearance);
$('projectionClassFilter').addEventListener('change', applyProjectionFilter);
$('floatingLayerFilter').addEventListener('change', applyFloatingZonesAppearance);
$('floatingForbiddenOnly').addEventListener('change', applyFloatingZonesAppearance);
for (const [id, name] of [['floatingPlanes', 'floatingPlanes'], ['floatingVolumes', 'floatingVolumes'], ['steelCenterlines', 'steelCenterlines']]) {
  $(id).addEventListener('change', () => {
    for (const { group } of floatingGeometryOverlays) {
      const overlay = group.getObjectByName(name);
      if (overlay) overlay.visible = $(id).checked;
    }
    requestRender();
  });
}
$('projectionLayerFilter').addEventListener('change', applyProjectionFilter);
$('fusionColorMode').addEventListener('change', applyFusionAppearance);
$('fusionScoreFilter').addEventListener('change', applyFusionAppearance);
$('fusionClassFilter').addEventListener('change', () => {
  if ($('fusionClassFilter').value !== 'all' && $('fusionRegionFilter').value === '3') $('fusionRegionFilter').value = 'all';
  applyFusionAppearance();
});
$('fusionRegionFilter').addEventListener('change', () => {
  if ($('fusionRegionFilter').value === '3') $('fusionClassFilter').value = 'all';
  applyFusionAppearance();
});
$('refinementColorMode').addEventListener('change', applyRefinementAppearance);
$('refinementSubsetFilter').addEventListener('change', applyRefinementAppearance);
$('refinementChangedOnly').addEventListener('change', applyRefinementAppearance);
$('refinementZoneFilter').addEventListener('change', applyRefinementAppearance);
$('internalColorMode').addEventListener('change', applyInternalRebarAppearance);
$('internalTypeFilter').addEventListener('change', () => {
  const selected = current?._internalInstanceById?.get(Number($('internalInstanceFilter').value));
  if (selected && $('internalTypeFilter').value !== 'all' && selected.type !== Number($('internalTypeFilter').value)) {
    $('internalInstanceFilter').value = 'all';
  }
  applyInternalRebarAppearance();
});
$('internalFamilyFilter').addEventListener('change', () => {
  if ($('internalFamilyFilter').value !== 'all') {
    preferredSemanticTag = 'internal';
    updateSemanticControls();
  }
  const selected = current?._internalInstanceById?.get(Number($('internalInstanceFilter').value));
  if (selected && $('internalFamilyFilter').value !== 'all' && selected.family !== Number($('internalFamilyFilter').value)) {
    $('internalInstanceFilter').value = 'all';
  }
  applyInternalRebarAppearance();
});
$('internalInstanceFilter').addEventListener('change', () => {
  if ($('internalInstanceFilter').value !== 'all') {
    preferredSemanticTag = 'internal';
    updateSemanticControls();
  }
  const selected = current?._internalInstanceById?.get(Number($('internalInstanceFilter').value));
  if (selected) {
    if ($('internalTypeFilter').value !== 'all' && selected.type !== Number($('internalTypeFilter').value)) $('internalTypeFilter').value = 'all';
    if ($('internalFamilyFilter').value !== 'all' && selected.family !== Number($('internalFamilyFilter').value)) $('internalFamilyFilter').value = 'all';
  }
  applyInternalRebarAppearance();
  if (!compareSource) fit(lastView);
});
$('internalAxes').addEventListener('change', applyInternalRebarAppearance);
$('frameOverlay').addEventListener('change', () => {
  for (const { group } of frameOverlays) group.visible = $('frameOverlay').checked;
  requestRender();
});
$('compare').addEventListener('change', () => {
  compareSource = $('compare').checked;
  document.querySelector('.after').style.left = compareSource ? '' : '16px';
  fit(lastView);
  setProjectionView(projectionView);
});
document.querySelectorAll('[data-view]').forEach((button) => button.addEventListener('click', () => fit(button.dataset.view === 'fit' ? 'oblique' : button.dataset.view)));
document.querySelectorAll('[data-step]').forEach((button) => button.addEventListener('click', () => showStep(button.dataset.step)));
document.querySelectorAll('[data-projection-view]').forEach((button) => button.addEventListener('click', () => setProjectionView(button.dataset.projectionView)));
renderer.domElement.addEventListener('click', inspectFusionPoint);
$('png').addEventListener('click', () => {
  requestAnimationFrame(() => {
    render();
    const link = document.createElement('a');
    link.download = `pointcloud-${current?.runId || 'preview'}.png`;
    link.href = renderer.domElement.toDataURL('image/png');
    link.click();
  });
});

status();
requestRender();
