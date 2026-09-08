import * as THREE from 'three';
import { OrbitControls } from 'three/addons/controls/OrbitControls.js';

const $ = (id) => document.getElementById(id);
const api = '/api';
let current = null, rawGeometry = null, normalGeometry = null, classGeometry = null, projectionGeometry = null, fusionGeometry = null, refinementGeometry = null, internalRebarGeometry = null, arrowLines = null;
let frameOverlays = [];
let internalAxisGroup = null;
let poller = null, loadToken = 0, frame = 0, rightScene = 'normal';
let canvasWidth = 0, canvasHeight = 0;
let workerLimitInitialized = false;
let compareSource = true, lastView = 'oblique', projectionView = '3d';

let renderer;
try {
  renderer = new THREE.WebGLRenderer({ antialias: true, preserveDrawingBuffer: true });
  renderer.setClearColor(0x0b1020);
  renderer.setPixelRatio(Math.min(devicePixelRatio, 2));
  $('view').append(renderer.domElement);
} catch (error) {
  $('empty').hidden = false;
  $('empty').innerHTML = `<div><strong>无法初始化 WebGL</strong>${error.message}</div>`;
  throw error;
}

const rawScene = new THREE.Scene();
const normalScene = new THREE.Scene();
const classScene = new THREE.Scene();
const projectionScene = new THREE.Scene();
const fusionScene = new THREE.Scene();
const refinementScene = new THREE.Scene();
const internalRebarScene = new THREE.Scene();
const camera = new THREE.PerspectiveCamera(50, 1, 0.001, 1e7);
camera.up.set(0, 0, 1);
const controls = new OrbitControls(camera, renderer.domElement);
controls.enableDamping = true;
controls.dampingFactor = 0.08;
const rawMaterial = new THREE.PointsMaterial({ size: 2, sizeAttenuation: false, vertexColors: true });
const normalMaterial = new THREE.PointsMaterial({ size: 2, sizeAttenuation: false, vertexColors: true });
const classMaterial = new THREE.PointsMaterial({ size: 2, sizeAttenuation: false, vertexColors: true });
const projectionMaterial = new THREE.PointsMaterial({ size: 2, sizeAttenuation: false, vertexColors: true });
const fusionMaterial = new THREE.PointsMaterial({ size: 2, sizeAttenuation: false, vertexColors: true });
const refinementMaterial = new THREE.PointsMaterial({ size: 2, sizeAttenuation: false, vertexColors: true });
const internalRebarMaterial = new THREE.PointsMaterial({ size: 2, sizeAttenuation: false, vertexColors: true });
const rawPoints = new THREE.Points(new THREE.BufferGeometry(), rawMaterial);
const normalPoints = new THREE.Points(new THREE.BufferGeometry(), normalMaterial);
const emptyClassGeometry = new THREE.BufferGeometry();
const classPoints = new THREE.Points(emptyClassGeometry, classMaterial);
const projectionPoints = new THREE.Points(emptyClassGeometry, projectionMaterial);
const fusionPoints = new THREE.Points(emptyClassGeometry, fusionMaterial);
const refinementPoints = new THREE.Points(emptyClassGeometry, refinementMaterial);
const internalRebarPoints = new THREE.Points(emptyClassGeometry, internalRebarMaterial);
rawScene.add(rawPoints);
normalScene.add(normalPoints);
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
  renderer.setScissorTest(true);
  if (compareSource) {
    renderer.setViewport(0, 0, half, canvasHeight);
    renderer.setScissor(0, 0, half, canvasHeight);
    renderer.render(rawScene, camera);
  }
  renderer.setViewport(half, 0, canvasWidth - half, canvasHeight);
  renderer.setScissor(half, 0, canvasWidth - half, canvasHeight);
  const scene = rightScene === 'normal' ? normalScene
    : rightScene === 'classification' ? classScene
      : rightScene === 'projection' ? projectionScene
        : rightScene === 'fusion' ? fusionScene
          : rightScene === 'refinement' ? refinementScene
            : rightScene === 'internalRebar' ? internalRebarScene : rawScene;
  renderer.render(scene, camera);
  renderer.setScissorTest(false);
  if (controls.update()) requestRender();
}

controls.addEventListener('change', requestRender);
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

function disposeArrows() {
  if (!arrowLines) return;
  normalScene.remove(arrowLines);
  arrowLines.geometry.dispose();
  arrowLines.material.dispose();
  arrowLines = null;
}

function releasePreview() {
  disposeArrows();
  disposeInternalAxes();
  rawGeometry?.dispose();
  normalGeometry?.dispose();
  classGeometry?.dispose();
  projectionGeometry?.dispose();
  fusionGeometry?.dispose();
  refinementGeometry?.dispose();
  internalRebarGeometry?.dispose();
  rawGeometry = null;
  normalGeometry = null;
  classGeometry = null;
  projectionGeometry = null;
  fusionGeometry = null;
  refinementGeometry = null;
  internalRebarGeometry = null;
  classPoints.geometry = emptyClassGeometry;
  projectionPoints.geometry = emptyClassGeometry;
  fusionPoints.geometry = emptyClassGeometry;
  refinementPoints.geometry = emptyClassGeometry;
  internalRebarPoints.geometry = emptyClassGeometry;
  disposeFrameOverlays();
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

const defaultRegionNames = { 0: '台面', 1: '内部钢筋', 2: '外露钢筋', 3: '夹具', 4: '未定位钢筋' };
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

const internalTypeNames = { 0: '非内部钢筋', 1: '下层钢筋', 2: '上层钢筋', 3: '腹杆', 4: '实例待定' };
const internalTypeColors = { 1: '#38bdf8', 2: '#fb7185', 3: '#facc15', 4: '#94a3b8' };

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

function internalColors(mode, types, instances, confidence) {
  const colors = new Float32Array(types.length * 3);
  for (let index = 0; index < types.length; index += 1) {
    let color;
    if (types[index] === 4 || instances[index] === 0) color = hexColor(internalTypeColors[4], '#94a3b8');
    else if (mode === 'instances') color = instanceColor(instances[index]);
    else if (mode === 'confidence') color = confidenceColor(confidence[index]);
    else color = hexColor(internalTypeColors[types[index]], '#94a3b8');
    colors.set(color, index * 3);
  }
  return colors;
}

function updateInternalLegend() {
  const box = $('internalRebarLegend');
  box.replaceChildren();
  const mode = $('internalColorMode').value;
  const entries = mode === 'confidence'
    ? [['低拟合分数', '#ef4444'], ['中等拟合分数', '#eab308'], ['高拟合分数', '#22c55e']]
    : mode === 'instances'
      ? [['不同颜色', '#a78bfa'], ['每种颜色代表一个钢筋实例', '#38bdf8'], ['实例待定', internalTypeColors[4]]]
      : [1, 2, 3, 4].map((value) => [internalTypeNames[value], internalTypeColors[value]]);
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
  const typeFilter = $('internalTypeFilter').value;
  const instanceFilter = $('internalInstanceFilter').value;
  const positions = [], colors = [];
  for (const segment of current.internalRebar.segments) {
    if (segment.type === 0) continue;
    if (typeFilter !== 'all' && segment.type !== Number(typeFilter)) continue;
    if (instanceFilter !== 'all' && segment.instanceId !== Number(instanceFilter)) continue;
    positions.push(
      segment.startM[0] - origin[0], segment.startM[1] - origin[1], segment.startM[2] - origin[2],
      segment.endM[0] - origin[0], segment.endM[1] - origin[1], segment.endM[2] - origin[2],
    );
    const color = segment.type === 4 || segment.instanceId === 0
      ? hexColor(internalTypeColors[4], '#94a3b8') : instanceColor(segment.instanceId);
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
  if (!internalRebarGeometry || !current?._internalTypes || !current?._internalInstances || !current?._internalConfidence) return;
  const types = current._internalTypes;
  const instances = current._internalInstances;
  internalRebarGeometry.setAttribute('color', new THREE.BufferAttribute(
    internalColors($('internalColorMode').value, types, instances, current._internalConfidence), 3,
  ));
  const typeFilter = $('internalTypeFilter').value;
  const instanceFilter = $('internalInstanceFilter').value;
  const filtered = new Uint32Array(types.length);
  let selected = 0;
  for (let index = 0; index < types.length; index += 1) {
    const matchesType = types[index] > 0 && (typeFilter === 'all' || types[index] === Number(typeFilter));
    const matchesInstance = instanceFilter === 'all'
      || (instances[index] > 0 && instances[index] === Number(instanceFilter));
    if (matchesType && matchesInstance) filtered[selected++] = index;
  }
  internalRebarGeometry.setIndex(new THREE.BufferAttribute(filtered.subarray(0, selected), 1));
  internalRebarGeometry.setDrawRange(0, selected);
  $('internalRebarHint').textContent = `当前显示 ${fmt(selected)} / ${fmt(types.length)} 个全局样本点；仅显示内部钢筋；灰色点仍属于钢筋，尚未确定单根归属。拟合分数是几何质量指标，不代表校准概率。`;
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
  const tableZ = current?.projection?.tableRemoval?.origin?.[2];
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
  const fusionFrame = normalizedFrame(current.regions?.frame);
  const refinementFrame = normalizedFrame(current.refinement?.frame);
  addFrameOverlay(fusionScene, fusionFrame, origin.map(Number));
  addFrameOverlay(refinementScene, refinementFrame || fusionFrame, origin.map(Number));
  addFrameOverlay(internalRebarScene, refinementFrame || fusionFrame, origin.map(Number));
}

function frameForCurrentStep() {
  return rightScene === 'internalRebar'
    ? normalizedFrame(current?.refinement?.frame) || normalizedFrame(current?.regions?.frame)
    : rightScene === 'refinement'
    ? normalizedFrame(current?.refinement?.frame) || normalizedFrame(current?.regions?.frame)
    : rightScene === 'fusion' ? normalizedFrame(current?.regions?.frame) : null;
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
  if (!refinementGeometry || !current?._refinedClasses || !current?._refinedRegions || !current?._refinedZones || !current?._refinedChanged) return;
  const classes = current._refinedClasses;
  const regions = current._refinedRegions;
  const zones = current._refinedZones;
  const changed = current._refinedChanged;
  const mode = $('refinementColorMode').value;
  const colors = mode === 'regions' ? regionColors(regions)
    : mode === 'zones' ? zoneColors(zones) : classColors(classes);
  refinementGeometry.setAttribute('color', new THREE.BufferAttribute(colors, 3));
  const subset = $('refinementSubsetFilter').value;
  const zone = $('refinementZoneFilter').value;
  if (subset === 'all' && zone === 'all') {
    refinementGeometry.setIndex(null);
    refinementGeometry.setDrawRange(0, classes.length);
  } else {
    const filtered = new Uint32Array(classes.length);
    let selected = 0;
    for (let index = 0; index < classes.length; index += 1) {
      const matchesSubset = subset === 'all'
        || (subset === 'steel' && classes[index] === 3)
        || (subset === 'fixture' && classes[index] === 2)
        || (subset === 'changed' && changed[index] === 1);
      const matchesZone = zone === 'all' || zones[index] === Number(zone);
      if (matchesSubset && matchesZone) filtered[selected++] = index;
    }
    refinementGeometry.setIndex(new THREE.BufferAttribute(filtered.subarray(0, selected), 1));
    refinementGeometry.setDrawRange(0, selected);
  }
  updateRefinementLegend();
  requestRender();
}

function updateFusionLegend() {
  const box = $('fusionLegend');
  box.replaceChildren();
  const useRegions = $('fusionColorMode').value === 'regions';
  const entries = useRegions
    ? [0, 1, 2, 3, 4].map((value) => [current?.regions?.regionNames?.[String(value)] || defaultRegionNames[value], current?.regions?.colors?.[String(value)] || defaultRegionColors[value]])
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
  if (!fusionGeometry || !current?._fusedClasses || !current?._fusedRegions) return;
  const classes = current._fusedClasses;
  const regions = current._fusedRegions;
  const recovered = current._fusedRecovered;
  const classFilter = $('fusionClassFilter').value;
  const regionFilter = $('fusionRegionFilter').value;
  const colors = $('fusionColorMode').value === 'regions'
    ? regionColors(regions, current.regions?.colors)
    : classColors(classes);
  fusionGeometry.setAttribute('color', new THREE.BufferAttribute(colors, 3));
  if (classFilter === 'all' && regionFilter === 'all') {
    fusionGeometry.setIndex(null);
    fusionGeometry.setDrawRange(0, classes.length);
  } else {
    const filtered = new Uint32Array(classes.length);
    let selected = 0;
    for (let index = 0; index < classes.length; index += 1) {
      const matchesClass = classFilter === 'all'
        || (classFilter === 'recovered' ? recovered?.[index] === 1 : classes[index] === Number(classFilter));
      const matchesRegion = regionFilter === 'all' || regions[index] === Number(regionFilter);
      if (matchesClass && matchesRegion) filtered[selected++] = index;
    }
    fusionGeometry.setIndex(new THREE.BufferAttribute(filtered.subarray(0, selected), 1));
    fusionGeometry.setDrawRange(0, selected);
  }
  updateFusionLegend();
  requestRender();
}

function applyClassFilter() {
  if (!classGeometry || !current?._classes) return;
  const classes = current._classes;
  const recovered = current._recovered;
  const filter = $('classFilter').value;
  const count = classes.length;
  if (filter === 'all') {
    classGeometry.setIndex(null);
    classGeometry.setDrawRange(0, count);
    requestRender();
    return;
  }
  const filtered = new Uint32Array(count);
  let selected = 0;
  for (let index = 0; index < count; index += 1) {
    const value = classes[index];
    const matches = filter === 'recovered'
      ? recovered?.[index] === 1
      : filter === 'excludeStatic' ? value !== 1 && value !== 2 : value === Number(filter);
    if (matches) filtered[selected++] = index;
  }
  classGeometry.setIndex(new THREE.BufferAttribute(filtered.subarray(0, selected), 1));
  classGeometry.setDrawRange(0, selected);
  requestRender();
}

function applyProjectionFilter() {
  if (!projectionGeometry || !current?._projectionClasses || !current?._projectionLayers) return;
  const classes = current._projectionClasses;
  const layers = current._projectionLayers;
  const classFilter = $('projectionClassFilter').value;
  const layerFilter = $('projectionLayerFilter').value;
  const count = classes.length;
  if (classFilter === 'all' && layerFilter === 'all') {
    projectionGeometry.setIndex(null);
    projectionGeometry.setDrawRange(0, count);
    requestRender();
    return;
  }
  const filtered = new Uint32Array(count);
  let selected = 0;
  for (let index = 0; index < count; index += 1) {
    const matchesClass = classFilter === 'all' || classes[index] === Number(classFilter);
    const matchesLayer = layerFilter === 'all' || layers[index] === Number(layerFilter);
    if (matchesClass && matchesLayer) filtered[selected++] = index;
  }
  projectionGeometry.setIndex(new THREE.BufferAttribute(filtered.subarray(0, selected), 1));
  projectionGeometry.setDrawRange(0, selected);
  requestRender();
}

const projectionImageNotes = {
  binary: '俯视占用二值图：显示哪些投影像素包含点。',
  density: '俯视密度图：显示每个投影像素内的点密度。',
  height: '每个俯视像素的 Z 跨度（最高点减最低点）；结合密度和 Z 连续占用判断竖向面。',
  classes: '02B 投影分类图，颜色与三类点云图例一致。',
  layers: 'Z 高度分层图：用于检查峰值、层间边界和各层相对高度。',
};

function setProjectionView(view) {
  projectionView = view;
  const imagePanel = $('projectionImagePanel');
  const isImage = rightScene === 'projection' && view !== '3d';
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
  const singleInstance = rightScene === 'internalRebar' && $('internalInstanceFilter').value !== 'all';
  if (rightScene === 'internalRebar' && !compareSource && internalRebarGeometry?.index?.count) {
    b.min.fill(Infinity); b.max.fill(-Infinity);
    const positions = current._positions, selected = internalRebarGeometry.index.array;
    for (const index of selected) {
      for (let axis = 0; axis < 3; axis += 1) {
        const value = positions[index * 3 + axis];
        b.min[axis] = Math.min(b.min[axis], value);
        b.max[axis] = Math.max(b.max[axis], value);
      }
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
  if (step === 'classification' && !current?._classes) {
    setStatus('该历史结果只有第 1 步法向量；请重新运行至第 2 步以查看几何分类。');
    return;
  }
  if (step === 'projection' && !current?._projectionClasses) {
    setStatus('该历史结果没有投影分类；请用“02A + 02B 并行分类”重新运行。');
    return;
  }
  if (step === 'fusion' && !current?._fusedClasses) {
    setStatus('该历史结果没有融合与区域分类；请重新运行至第 4 步。');
    return;
  }
  if (step === 'refinement' && !current?._refinedClasses) {
    setStatus('该历史结果没有双框空间整理；请重新运行至第 5 步。');
    return;
  }
  if (step === 'internalRebar' && !current?._internalTypes) {
    setStatus('该历史结果没有内部钢筋分层与逐根编号；请重新运行至第 6 步。');
    return;
  }
  rightScene = step;
  document.querySelectorAll('[data-step]').forEach((button) => button.classList.toggle('active', button.dataset.step === step));
  document.querySelector('.after').textContent = step === 'normal'
    ? '01 · 法向量' : step === 'classification' ? '02A · 法向量几何分类'
      : step === 'projection' ? '02B · 投影图像分类'
        : step === 'fusion' ? '03 · 融合与区域分类'
          : step === 'refinement' ? '04 · 双框约束与类别整理'
            : step === 'internalRebar' ? '05 · 内部钢筋分层与逐根编号' : '00 · 原始颜色 / 强度';
  $('classificationControls').hidden = step !== 'classification' || !current?._classes;
  $('projectionControls').hidden = step !== 'projection' || !current?._projectionClasses;
  $('fusionControls').hidden = step !== 'fusion' || !current?._fusedClasses;
  $('refinementControls').hidden = step !== 'refinement' || !current?._refinedClasses;
  $('internalRebarControls').hidden = step !== 'internalRebar' || !current?._internalTypes;
  updateFrameControls();
  setProjectionView(step === 'projection' ? projectionView : '3d');
}

function metrics(manifest) {
  const t = manifest.timings || {}, p = manifest.performance || {};
  const classification = manifest.classification;
  const projection = manifest.projection;
  const fusion = manifest.fusion;
  const regions = manifest.regions;
  const refinement = manifest.refinement;
  const internalRebar = manifest.internalRebar;
  const classificationS = classification?.timings?.classificationS ?? t.classificationS;
  const projectionS = t.projectionS ?? projection?.elapsedS;
  const fusionS = t.fusionS ?? fusion?.elapsedS;
  const regionsS = t.regionsS;
  const refinementS = t.refinementS ?? refinement?.elapsedS;
  const internalRebarS = t.internalRebarS ?? internalRebar?.elapsedS;
  const recovery = classification?.recovery;
  const recoveryS = classification?.timings?.recoveryS;
  const classificationRate = classificationS > 0 ? classification.pointCount / classificationS : NaN;
  const branchStats = [classification ? `02A ${fmt(classificationS, ' s')}` : '', projection ? `02B ${fmt(projectionS, ' s')}` : ''].filter(Boolean).join(' · ');
  const branchMode = projection ? (manifest.branchExecution?.mode === 'parallel' ? '并行' : '串行') : '分类';
  const finalStats = [fusion ? `融合 ${fmt(fusionS, ' s')}` : '', regions ? `区域 ${fmt(regionsS, ' s')}` : '', refinement ? `空间整理 ${fmt(refinementS, ' s')}` : '', internalRebar ? `内部钢筋 ${fmt(internalRebarS, ' s')}` : ''].filter(Boolean).join(' · ');
  $('quickStats').textContent = `法向量 ${fmt(t.normalsS, ' s')}${branchStats ? ` · ${branchStats}` : ''}${Number.isFinite(t.classifiersWallS) ? ` · ${branchMode}墙钟 ${fmt(t.classifiersWallS, ' s')}` : ''}${finalStats ? ` · ${finalStats}` : ''} · 全流程 ${fmt(t.totalS, ' s')}`;
  const rows = [
    ['保存参数 k / workers', `${fmt(manifest.parameters?.k)} / ${fmt(manifest.parameters?.workers)}`],
    ['预览 / 全量点', `${fmt(manifest.preview?.pointCount)} / ${fmt(manifest.preview?.totalPointCount)}`],
    ['有效 / 无效法向量', `${fmt(manifest.validNormalCount)} / ${fmt(manifest.invalidNormalCount)}`],
    ['法向量吞吐量', fmt(p.pointsPerSecond, ' pts/s')],
    ['CPU 累计 / 进程峰值内存', `${fmt(p.cpuS, ' s')} / ${fmt(p.peakRssMB, ' MB')}`],
    ...[['读取', t.readS], ['KD 树', t.treeS], ['法向量', t.normalsS], ['持久化', t.persistS], ['预览', t.previewS], ['总计', t.totalS]].map(([name, value]) => [name, fmt(value, ' s')]),
  ];
  if (classification) {
    const counts = classification.counts || {};
    const threeClass = classification.classPolicy === 'table-rebar-fixture-remainder';
    const classificationRows = [
      ['分类版本', classification.version || '—'],
      threeClass ? ['台面', fmt(counts.table)] : ['待定 / 台面', `${fmt(counts.unknown)} / ${fmt(counts.table)}`],
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
    const layerHeights = (projection.layers || []).map((layer) => `${layer.name || `层 ${layer.id}`} ${fmtHeight(layer.heightM)}（距台面 ${fmtHeight(layer.relativeHeightM)}）`).join('；');
    const projectionTimingRows = Object.entries(projection.timings || {})
      .filter(([, value]) => Number.isFinite(value))
      .map(([name, value]) => [`02B · ${name}`, value < .01 ? fmt(value * 1000, ' ms') : fmt(value, ' s')]);
    rows.splice(3, 0,
      ['02B 投影版本', projection.version || '—'],
      ['02B 台面 / 夹具 / 钢筋', `${fmt(counts.table)} / ${fmt(counts.fixture)} / ${fmt(counts.rebar)}`],
      ['02B 分支墙钟 / 吞吐量', `${fmt(projectionS, ' s')} / ${fmt(projectionRate, ' pts/s')}`],
      ['02B Z 层高度', layerHeights || '未检出层'],
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
      ['03 融合版本', fusion.version || '—'],
      ['03 台面 / 夹具 / 钢筋', `${fmt(counts.table)} / ${fmt(counts.fixture)} / ${fmt(counts.rebar)}`],
      ['03 几何恢复：投影 / 双分支', `${fmt(recovery.recoveredFromProjectionPoints)} / ${fmt(recovery.recoveredBothPoints)}`],
      ['02A/B 一致 / 分歧点', `${fmt(agreement.agreePoints)} / ${fmt(agreement.disagreePoints)}`],
      ['03 融合耗时', fmt(fusionS, ' s')],
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
      ['夹具围框', frame.detected ? `已检出${Array.isArray(frame.cornersM) ? ` · ${fmt(frame.cornersM.length)} 角点` : ''}` : '未检出'],
      ['03 区域耗时', fmt(regionsS, ' s')],
    );
  }
  if (refinement) {
    const counts = refinement.counts || {};
    const regionCounts = refinement.regionCounts || {};
    const changes = refinement.changes || {};
    const frame = refinement.frame || {};
    const reasonNames = Object.values(refinement.reasonNames || {}).join(' / ');
    const refinementTimingRows = Object.entries(refinement.timings || {})
      .filter(([, value]) => Number.isFinite(value))
      .map(([name, value]) => [`04 空间整理 · ${name}`, value < .01 ? fmt(value * 1000, ' ms') : fmt(value, ' s')]);
    rows.splice(3, 0,
      ['04 整理版本', refinement.version || '—'],
      ['04 台面 / 夹具 / 钢筋', `${fmt(counts.table)} / ${fmt(counts.fixture)} / ${fmt(counts.rebar)}`],
      ['04 区域：台面 / 内部 / 外露', `${fmt(regionCounts.table)} / ${fmt(regionCounts.interior)} / ${fmt(regionCounts.exterior)}`],
      ['04 区域：夹具 / 未定位', `${fmt(regionCounts.fixture)} / ${fmt(regionCounts.unlocated)}`],
      ['04 类别变化总数', fmt(changes.totalChanged)],
      ['04 内部→钢筋 / 边带→夹具', `${fmt(changes.interiorToSteel)} / ${fmt(changes.bandToFixture)}`],
      ['04 边带→钢筋 / 外部→夹具 / 外部→钢筋', `${fmt(changes.bandToSteel)} / ${fmt(changes.exteriorToFixture)} / ${fmt(changes.exteriorToSteel)}`],
      ['04 类别整理原因', reasonNames || '—'],
      ['双框检测', frame.detected ? `已检出 · 外框 ${fmt((frame.outerCornersM || frame.cornersM)?.length)} 点 · 内框 ${fmt(frame.innerCornersM?.length)} 点` : '未检出'],
      ['04 空间整理耗时', fmt(refinementS, ' s')],
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
    rows.splice(3, 0,
      ['05 识别版本', internalRebar.version || '—'],
      ['05 处理范围点数', fmt(internalRebar.pointCount)],
      ['05 下层 / 上层 / 腹杆', `${fmt(counts.lower)} / ${fmt(counts.upper)} / ${fmt(counts.web)}`],
      ['05 实例待定点', fmt(counts.unassigned)],
      ['05 实例覆盖率', internalRebar.pointCount ? `${(100 * (1 - counts.unassigned / internalRebar.pointCount)).toFixed(2)}%` : '—'],
      ['05 补建实例 / 表面补点', `${fmt(internalRebar.diagnostics?.recovery?.newInstances)} / ${fmt(internalRebar.diagnostics?.completedSurfacePoints)}`],
      ['05 实例 / 拟合段', `${fmt(internalRebar.instanceCount)} / ${fmt(internalRebar.segmentCount)}`],
      ['05 下层 / 上层高度', `${fmtHeight(layers.lowerM)} / ${fmtHeight(layers.upperM)}`],
      ['05 模型', modelSummary || '—'],
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
  setStatus('正在读取预览二进制数据…');
  try {
    const preview = manifest.preview;
    const hasProjection = Boolean(manifest.projection && preview.projectionClassesUrl && preview.projectionLayersUrl);
    const hasFusion = Boolean(manifest.fusion && manifest.regions && preview.fusedClassesUrl && preview.fusedRegionsUrl);
    const hasRefinement = Boolean(manifest.refinement && preview.refinedClassesUrl && preview.refinedRegionsUrl && preview.refinedZonesUrl && preview.refinedChangedUrl);
    const hasInternalRebar = Boolean(manifest.internalRebar && preview.internalTypesUrl && preview.internalInstancesUrl && preview.internalSegmentsUrl && preview.internalConfidenceUrl);
    const [positionBytes, normalBytes, colorBytes, validBytes, classBytes, recoveredBytes, projectionClassBytes, projectionLayerBytes, fusedClassBytes, fusedRegionBytes, fusedRecoveredBytes, refinedClassBytes, refinedRegionBytes, refinedZoneBytes, refinedChangedBytes, internalTypeBytes, internalInstanceBytes, internalSegmentBytes, internalConfidenceBytes] = await Promise.all([
      fetchBytes(preview.positionsUrl), fetchBytes(preview.normalsUrl),
      fetchBytes(preview.colorsUrl), fetchBytes(preview.validUrl),
      preview.classesUrl ? fetchBytes(preview.classesUrl) : Promise.resolve(null),
      preview.recoveredUrl ? fetchBytes(preview.recoveredUrl) : Promise.resolve(null),
      hasProjection ? fetchBytes(preview.projectionClassesUrl) : Promise.resolve(null),
      hasProjection ? fetchBytes(preview.projectionLayersUrl) : Promise.resolve(null),
      hasFusion ? fetchBytes(preview.fusedClassesUrl) : Promise.resolve(null),
      hasFusion ? fetchBytes(preview.fusedRegionsUrl) : Promise.resolve(null),
      hasFusion && preview.fusedRecoveredUrl ? fetchBytes(preview.fusedRecoveredUrl) : Promise.resolve(null),
      hasRefinement ? fetchBytes(preview.refinedClassesUrl) : Promise.resolve(null),
      hasRefinement ? fetchBytes(preview.refinedRegionsUrl) : Promise.resolve(null),
      hasRefinement ? fetchBytes(preview.refinedZonesUrl) : Promise.resolve(null),
      hasRefinement ? fetchBytes(preview.refinedChangedUrl) : Promise.resolve(null),
      hasInternalRebar ? fetchBytes(preview.internalTypesUrl) : Promise.resolve(null),
      hasInternalRebar ? fetchBytes(preview.internalInstancesUrl) : Promise.resolve(null),
      hasInternalRebar ? fetchBytes(preview.internalSegmentsUrl) : Promise.resolve(null),
      hasInternalRebar ? fetchBytes(preview.internalConfidenceUrl) : Promise.resolve(null),
    ]);
    if (token !== loadToken) return;
    const positions = new Float32Array(positionBytes), normals = new Float32Array(normalBytes);
    const colors = new Uint8Array(colorBytes), valid = new Uint8Array(validBytes);
    const classes = classBytes ? new Uint8Array(classBytes) : null;
    const recovered = recoveredBytes ? new Uint8Array(recoveredBytes) : null;
    const projectionClasses = projectionClassBytes ? new Uint8Array(projectionClassBytes) : null;
    const projectionLayers = projectionLayerBytes ? new Uint8Array(projectionLayerBytes) : null;
    const fusedClasses = fusedClassBytes ? new Uint8Array(fusedClassBytes) : null;
    const fusedRegions = fusedRegionBytes ? new Uint8Array(fusedRegionBytes) : null;
    const fusedRecovered = fusedRecoveredBytes ? new Uint8Array(fusedRecoveredBytes) : null;
    const refinedClasses = refinedClassBytes ? new Uint8Array(refinedClassBytes) : null;
    const refinedRegions = refinedRegionBytes ? new Uint8Array(refinedRegionBytes) : null;
    const refinedZones = refinedZoneBytes ? new Uint8Array(refinedZoneBytes) : null;
    const refinedChanged = refinedChangedBytes ? new Uint8Array(refinedChangedBytes) : null;
    const internalTypes = internalTypeBytes ? new Uint8Array(internalTypeBytes) : null;
    const internalInstances = internalInstanceBytes ? new Uint32Array(internalInstanceBytes) : null;
    const internalSegments = internalSegmentBytes ? new Uint32Array(internalSegmentBytes) : null;
    const internalConfidence = internalConfidenceBytes ? new Float32Array(internalConfidenceBytes) : null;
    const count = preview.pointCount;
    if (positions.length !== count * 3 || normals.length !== count * 3 || colors.length !== count * 3 || valid.length !== count || (classes && classes.length !== count) || (recovered && recovered.length !== count) || (projectionClasses && projectionClasses.length !== count) || (projectionLayers && projectionLayers.length !== count) || (fusedClasses && fusedClasses.length !== count) || (fusedRegions && fusedRegions.length !== count) || (fusedRecovered && fusedRecovered.length !== count) || (refinedClasses && refinedClasses.length !== count) || (refinedRegions && refinedRegions.length !== count) || (refinedZones && refinedZones.length !== count) || (refinedChanged && refinedChanged.length !== count) || (internalTypes && internalTypes.length !== count) || (internalInstances && internalInstances.length !== count) || (internalSegments && internalSegments.length !== count) || (internalConfidence && internalConfidence.length !== count)) {
      throw new Error('预览数组长度与 Manifest 元数据不一致');
    }
    if (recovered && !classes) throw new Error('恢复掩码缺少对应的分类预览数据');
    if (recovered?.some((value, index) => value > 1 || (value === 1 && classes[index] !== 3))) {
      throw new Error('恢复掩码无效：只允许以 0/1 标记本轮归还为钢筋的预览点');
    }
    if (manifest.projection && !hasProjection) throw new Error('投影分类 Manifest 缺少预览分类或分层数据');
    if ((manifest.fusion || manifest.regions) && !hasFusion) throw new Error('融合 Manifest 缺少语义类别或区域预览数据');
    if (manifest.refinement && !hasRefinement) throw new Error('空间整理 Manifest 缺少类别、区域、分区或变化预览数据');
    if (manifest.internalRebar && !hasInternalRebar) throw new Error('内部钢筋 Manifest 缺少类型、实例、拟合段或置信度预览数据');
    if (manifest.refinement && !hasFusion) throw new Error('空间整理 Manifest 缺少作为变化基线的融合预览数据');
    if (projectionClasses?.some((value) => value < 1 || value > 3)) {
      throw new Error('投影分类预览无效：类别标签必须为 1、2 或 3');
    }
    const projectionLayerIds = new Set((manifest.projection?.layers || []).map((layer) => Number(layer.id)));
    if (projectionLayers?.some((value) => value !== 0 && !projectionLayerIds.has(value))) {
      throw new Error('投影分层预览包含 Manifest 未声明的层编号');
    }
    if (fusedClasses?.some((value) => value < 1 || value > 3)) {
      throw new Error('融合语义预览无效：类别标签必须为 1、2 或 3');
    }
    if (fusedRegions?.some((value) => value > 4)) {
      throw new Error('融合区域预览无效：区域标签必须为 0 至 4');
    }
    if (fusedRecovered?.some((value, index) => value > 1 || (value === 1 && fusedClasses[index] !== 3))) {
      throw new Error('融合恢复掩码无效：只能标记最终类别为钢筋的点');
    }
    if (refinedClasses?.some((value) => value < 1 || value > 3)) {
      throw new Error('空间整理类别预览无效：类别标签必须为 1、2 或 3');
    }
    if (refinedRegions?.some((value) => value > 4)) {
      throw new Error('空间整理区域预览无效：区域标签必须为 0 至 4');
    }
    if (refinedZones?.some((value) => value > 3)) {
      throw new Error('双框几何分区预览无效：分区标签必须为 0 至 3');
    }
    if (refinedChanged?.some((value, index) => value > 1 || value !== Number(refinedClasses[index] !== fusedClasses?.[index]))) {
      throw new Error('空间整理变化掩码无效：必须准确标记相对融合类别发生变化的点');
    }
    if (refinedRegions?.some((region, index) =>
      (region === 0 && refinedClasses[index] !== 1)
      || (region === 3 && refinedClasses[index] !== 2)
      || ([1, 2, 4].includes(region) && refinedClasses[index] !== 3))) {
      throw new Error('空间整理类别与区域不一致');
    }
    if (internalTypes?.some((value) => value > 4)) {
      throw new Error('内部钢筋类型预览无效：标签必须为 0 至 4');
    }
    if (internalConfidence?.some((value) => !Number.isFinite(value) || value < 0 || value > 1)) {
      throw new Error('内部钢筋置信度预览无效：数值必须位于 0 至 1');
    }
    if (internalTypes?.some((type, index) =>
      ((type === 0 || type === 4) && (internalInstances[index] !== 0 || internalSegments[index] !== 0))
      || (type >= 1 && type <= 3 && (internalInstances[index] === 0 || internalSegments[index] === 0)))) {
      throw new Error('内部钢筋预览标签不一致：非内部或实例待定点编号必须为 0，已分类钢筋必须有实例与拟合段编号');
    }
    if (manifest.internalRebar) {
      const instances = manifest.internalRebar.instances;
      const segments = manifest.internalRebar.segments;
      const expectedTypes = Object.fromEntries(Object.entries(internalTypeNames).map(([id, name]) => [String(id), name]));
      const counts = manifest.internalRebar.counts || {};
      const countValues = ['lower', 'upper', 'web', 'unassigned'].map((name) => counts[name]);
      if (!Array.isArray(instances) || !Array.isArray(segments)) {
        throw new Error('内部钢筋 Manifest 的实例和拟合段清单必须为数组');
      }
      if (Object.entries(expectedTypes).some(([id, name]) => id === '4'
        ? !['待分配', '实例待定', '钢筋（实例待定）'].includes(manifest.internalRebar.types?.[id])
        : manifest.internalRebar.types?.[id] !== name)) {
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
          || typeof instance.modelKind !== 'string' || !instance.modelKind.trim()) {
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
    const nextRaw = new THREE.BufferGeometry(), nextNormal = new THREE.BufferGeometry();
    nextRaw.setAttribute('position', new THREE.BufferAttribute(positions, 3));
    nextRaw.setAttribute('color', new THREE.BufferAttribute(colors, 3, true));
    nextRaw.computeBoundingSphere();
    nextNormal.setAttribute('position', new THREE.BufferAttribute(positions, 3));
    nextNormal.computeBoundingSphere();
    let nextClass = null;
    if (classes) {
      nextClass = new THREE.BufferGeometry();
      nextClass.setAttribute('position', new THREE.BufferAttribute(positions, 3));
      nextClass.setAttribute('color', new THREE.BufferAttribute(classColors(classes), 3));
      nextClass.computeBoundingSphere();
    }
    let nextProjection = null;
    if (projectionClasses) {
      nextProjection = new THREE.BufferGeometry();
      nextProjection.setAttribute('position', new THREE.BufferAttribute(positions, 3));
      nextProjection.setAttribute('color', new THREE.BufferAttribute(classColors(projectionClasses), 3));
      nextProjection.computeBoundingSphere();
    }
    let nextFusion = null;
    if (fusedClasses) {
      nextFusion = new THREE.BufferGeometry();
      nextFusion.setAttribute('position', new THREE.BufferAttribute(positions, 3));
      nextFusion.setAttribute('color', new THREE.BufferAttribute(regionColors(fusedRegions, manifest.regions?.colors), 3));
      nextFusion.computeBoundingSphere();
    }
    let nextRefinement = null;
    if (refinedClasses) {
      nextRefinement = new THREE.BufferGeometry();
      nextRefinement.setAttribute('position', new THREE.BufferAttribute(positions, 3));
      nextRefinement.setAttribute('color', new THREE.BufferAttribute(regionColors(refinedRegions), 3));
      nextRefinement.computeBoundingSphere();
    }
    let nextInternalRebar = null;
    if (internalTypes) {
      nextInternalRebar = new THREE.BufferGeometry();
      nextInternalRebar.setAttribute('position', new THREE.BufferAttribute(positions, 3));
      nextInternalRebar.setAttribute('color', new THREE.BufferAttribute(internalColors('types', internalTypes, internalInstances, internalConfidence), 3));
      nextInternalRebar.computeBoundingSphere();
    }
    releasePreview();
    rawGeometry = nextRaw; normalGeometry = nextNormal; classGeometry = nextClass; projectionGeometry = nextProjection; fusionGeometry = nextFusion; refinementGeometry = nextRefinement; internalRebarGeometry = nextInternalRebar;
    rawPoints.geometry = rawGeometry; normalPoints.geometry = normalGeometry;
    classPoints.geometry = classGeometry || emptyClassGeometry;
    projectionPoints.geometry = projectionGeometry || emptyClassGeometry;
    fusionPoints.geometry = fusionGeometry || emptyClassGeometry;
    refinementPoints.geometry = refinementGeometry || emptyClassGeometry;
    internalRebarPoints.geometry = internalRebarGeometry || emptyClassGeometry;
    current = { ...manifest, _positions: positions, _normals: normals, _valid: valid, _classes: classes, _recovered: recovered, _projectionClasses: projectionClasses, _projectionLayers: projectionLayers, _fusedClasses: fusedClasses, _fusedRegions: fusedRegions, _fusedRecovered: fusedRecovered, _refinedClasses: refinedClasses, _refinedRegions: refinedRegions, _refinedZones: refinedZones, _refinedChanged: refinedChanged, _internalTypes: internalTypes, _internalInstances: internalInstances, _internalSegments: internalSegments, _internalConfidence: internalConfidence };
    if (hasRefinement || hasInternalRebar) $('frameOverlay').checked = true;
    rebuildFrameOverlays();
    if (Array.from($('history').options).some((option) => option.value === manifest.runId)) {
      $('history').value = manifest.runId;
    }
    $('empty').hidden = true;
    $('sample').textContent = `样本 ${fmt(preview.pointCount)} / 全量 ${fmt(preview.totalPointCount)}`;
    $('source').textContent = `源文件：${manifest.source?.name || '未知'} · ${fmt(manifest.source?.pointCount)} 点`;
    metrics(manifest);
    $('classificationStep').disabled = !classes;
    $('projectionStep').disabled = !projectionClasses;
    $('fusionStep').disabled = !fusedClasses;
    $('refinementStep').disabled = !refinedClasses;
    $('internalRebarStep').disabled = !internalTypes;
    const threeClass = manifest.classification?.classPolicy === 'table-rebar-fixture-remainder';
    $('unknownLegend').hidden = threeClass;
    const previousFilter = $('classFilter').value;
    const filters = [['all', '全部'], ['3', '仅钢筋'], ['1', '仅台面'], ['2', '仅夹具（含方管）']];
    if (recovered) filters.splice(2, 0, ['recovered', '仅本轮归还的钢筋']);
    if (!threeClass) filters.push(['0', '仅待定（旧版）'], ['excludeStatic', '排除台面与夹具']);
    $('classFilter').replaceChildren(...filters.map(([value, label]) => new Option(label, value)));
    $('classFilter').value = filters.some(([value]) => value === previousFilter) ? previousFilter : 'all';
    $('classHint').textContent = threeClass
      ? `台面和钢筋以外统一归夹具；方管侧面、圆角、边缘属于同一类。${recovered ? '“本轮归还”是钢筋的预览子集，沿用钢筋绿色。' : ''}`
      : '旧版分类结果：待定点保留原有类别。筛选只改变显示。';
    const previousProjectionClass = $('projectionClassFilter').value;
    $('projectionClassFilter').value = ['all', '1', '2', '3'].includes(previousProjectionClass) ? previousProjectionClass : 'all';
    const previousLayer = $('projectionLayerFilter').value;
    const layerOptions = [['all', '全部高度'], ['0', '层间 / 未归层'], ...(manifest.projection?.layers || []).map((layer) => [String(layer.id), `${layer.name || `层 ${layer.id}`} · Z ${fmtHeight(layer.heightM)}`])];
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
    const regionNames = { ...defaultRegionNames, ...(manifest.regions?.regionNames || {}) };
    const fusionRegionOptions = [['all', '全部区域'], ...[1, 2, 3, 4].map((value) => [String(value), `仅${regionNames[value]}`])];
    $('fusionRegionFilter').replaceChildren(...fusionRegionOptions.map(([value, label]) => new Option(label, value)));
    $('fusionRegionFilter').value = fusionRegionOptions.some(([value]) => value === previousFusionRegion) ? previousFusionRegion : 'all';
    $('fusionHint').textContent = `02A 与 02B 的分歧由实体夹具面和钢筋轴线证据判断。${fusedRecovered ? '“几何规则归还”包含 02B 本轮补回的交点，以及融合时从投影误分中恢复的钢筋。' : ''}`;
    updateFusionLegend();
    updateRefinementLegend();
    if (internalTypes) {
      const previousInstance = $('internalInstanceFilter').value;
      const instanceOptions = [['all', '全部实例'], ...(manifest.internalRebar.instances || []).map((instance) => [
        String(instance.id),
        `#${instance.id} · ${internalTypeNames[instance.type] || `类型 ${instance.type}`} · ${fmt(instance.pointCount)} 点 · ${fmt(instance.lengthM, ' m')}`,
      ])];
      $('internalInstanceFilter').replaceChildren(...instanceOptions.map(([value, label]) => new Option(label, value)));
      $('internalInstanceFilter').value = instanceOptions.some(([value]) => value === previousInstance) ? previousInstance : 'all';
      const sampleScopeCount = internalTypes.reduce((sum, value) => sum + Number(value > 0), 0);
      $('internalLayerSummary').replaceChildren(...[
        ['全量内部范围', `${fmt(manifest.internalRebar.pointCount)} 点`],
        ['预览内部范围', `${fmt(sampleScopeCount)} / ${fmt(preview.pointCount)} 样本点`],
        ['下层标高', fmtHeight(manifest.internalRebar.layers?.lowerM)],
        ['上层标高', fmtHeight(manifest.internalRebar.layers?.upperM)],
        ['实例 / 拟合段', `${fmt(manifest.internalRebar.instanceCount)} / ${fmt(manifest.internalRebar.segmentCount)}`],
      ].map(([name, value]) => {
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
    if (internalTypes) { showStep('internalRebar'); applyInternalRebarAppearance(); }
    else if (refinedClasses) { showStep('refinement'); applyRefinementAppearance(); }
    else if (fusedClasses) { showStep('fusion'); applyFusionAppearance(); }
    else if (projectionClasses) { showStep('projection'); applyProjectionFilter(); }
    else if (classes) { showStep('classification'); applyClassFilter(); }
    else showStep('normal');
    fit();
    setStatus(`完成 · ${manifest.runId}`);
    window.pointcloudDebug = { current, renderer, rawScene, normalScene, classScene, projectionScene, fusionScene, refinementScene, internalRebarScene, camera, loadManifest };
  } catch (error) {
    if (token !== loadToken) return;
    console.error(error);
    setStatus(error.message, true);
  }
}

async function history(selectLatest = true) {
  try {
    const runs = await getJSON(`${api}/runs`);
    const box = $('history'), previous = box.value;
    box.innerHTML = '';
    if (!runs.length) { box.innerHTML = '<option>尚无历史运行</option>'; return; }
    for (const manifest of runs) {
      const option = document.createElement('option');
      option.value = manifest.runId;
      option.textContent = `${manifest.runId} · ${new Date(manifest.createdAt).toLocaleString()}`;
      option._manifest = manifest;
      box.append(option);
    }
    box.value = runs.some((run) => run.runId === previous) ? previous : (current?.runId || runs[0].runId);
    if (selectLatest) await loadManifest(box.selectedOptions[0]._manifest);
  } catch (error) {
    setStatus(`历史记录读取失败：${error.message}`, true);
  }
}

async function status() {
  try {
    const state = await getJSON(`${api}/status`);
    if (Number.isFinite(state.maxWorkers)) {
      $('workers').max = String(state.maxWorkers);
      if (!workerLimitInitialized) {
        $('workers').value = String(Math.min(Number($('workers').value), state.maxWorkers));
        workerLimitInitialized = true;
      }
    }
    $('source').textContent = `源文件：${state.sourceName || '未检测到'}${state.progress ? ` · ${state.progress.stage || ''} ${fmt(state.progress.completed)} / ${fmt(state.progress.total)}` : ''}`;
    const running = state.status === 'running';
    $('run').disabled = running;
    if (running) {
      setStatus(`处理中：${state.progress?.stage || '准备中'} · ${fmt(state.progress?.completed)} / ${fmt(state.progress?.total)}`);
      if (!poller) poller = setInterval(status, 1000);
    } else {
      if (poller) { clearInterval(poller); poller = null; }
      if (state.status === 'failed') setStatus(state.error || '处理失败', true);
      else if (state.status === 'complete' && state.latest && current?.runId !== state.latest.runId) {
        await history(false); await loadManifest(state.latest);
      } else if (!current) setStatus('空闲 · 可开始新的全量处理');
    }
  } catch (error) {
    setStatus(`服务连接失败：${error.message}`, true);
  }
}

$('run').addEventListener('click', async () => {
  try {
    $('run').disabled = true;
    const response = await fetch(`${api}/run`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ k: Number($('k').value), workers: Number($('workers').value), throughStep: Number($('throughStep').value) }),
    });
    if (response.status === 409) throw new Error('已有任务正在运行');
    if (!response.ok) throw new Error(await response.text());
    setStatus('已提交，正在启动…');
    await status();
  } catch (error) {
    $('run').disabled = false;
    setStatus(error.message, true);
  }
});

$('history').addEventListener('change', (event) => loadManifest(event.target.selectedOptions[0]._manifest));
$('mode').addEventListener('change', applyNormalColors);
$('arrows').addEventListener('change', buildArrows);
$('length').addEventListener('input', () => { $('lengthValue').textContent = Number($('length').value).toFixed(3); buildArrows(); });
$('size').addEventListener('input', () => { $('sizeValue').textContent = $('size').value; rawMaterial.size = normalMaterial.size = classMaterial.size = projectionMaterial.size = fusionMaterial.size = refinementMaterial.size = internalRebarMaterial.size = Number($('size').value); requestRender(); });
$('classFilter').addEventListener('change', applyClassFilter);
$('projectionClassFilter').addEventListener('change', applyProjectionFilter);
$('projectionLayerFilter').addEventListener('change', applyProjectionFilter);
$('fusionColorMode').addEventListener('change', applyFusionAppearance);
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
$('refinementZoneFilter').addEventListener('change', applyRefinementAppearance);
$('internalColorMode').addEventListener('change', applyInternalRebarAppearance);
$('internalTypeFilter').addEventListener('change', () => {
  if ($('internalTypeFilter').value !== 'all') $('internalInstanceFilter').value = 'all';
  applyInternalRebarAppearance();
});
$('internalInstanceFilter').addEventListener('change', () => {
  if ($('internalInstanceFilter').value !== 'all') $('internalTypeFilter').value = 'all';
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
$('png').addEventListener('click', () => {
  requestRender();
  requestAnimationFrame(() => {
    const link = document.createElement('a');
    link.download = `pointcloud-${current?.runId || 'preview'}.png`;
    link.href = renderer.domElement.toDataURL('image/png');
    link.click();
  });
});

status();
history(false);
requestRender();
