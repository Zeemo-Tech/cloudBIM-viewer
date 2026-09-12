// Run the actual workbench fetch/validation path without a WebGL/browser surface.
// Pass an HTTP manifest URL; this checks the served artifact, not a synthetic copy.
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import vm from 'node:vm';
import path from 'node:path';

const manifestUrl = process.argv[2];
const local = manifestUrl && !/^https?:/.test(manifestUrl);
const readBytes = async url => {
  if (!local) { const response=await fetch(new URL(url,manifestUrl));assert.ok(response.ok);return response.arrayBuffer(); }
  const relative=url.split('?')[0].split('/').slice(3).join('/');
  const bytes=await readFile(path.join(path.dirname(manifestUrl),relative));return bytes.buffer.slice(bytes.byteOffset,bytes.byteOffset+bytes.byteLength);
};
assert.ok(manifestUrl, 'Usage: node scripts/test_pointcloud_manifest_loading.mjs <manifest URL>');
const previewLimit = Number(process.argv[3] || 300_000);
assert.ok(Number.isSafeInteger(previewLimit) && previewLimit > 0, 'Preview limit must be a positive integer');
const source = await readFile(new URL('./pointcloud-debug/viewer.js', import.meta.url), 'utf8');
const context = vm.createContext({
  loadToken: 0, current: null, PREVIEW_RENDER_LIMIT: previewLimit, setStatus() {}, fmt: value => String(value),
  fetchBytes: readBytes,
});
const extract = (start, end) => source.slice(source.indexOf(start), source.indexOf(end, source.indexOf(start)));
vm.runInContext(extract('const internalTypeNames =', '\n'), context);
vm.runInContext(extract('function fusionProtectionThreshold(', 'function updateFusionLegend('), context);
vm.runInContext(extract('async function loadCompletePreview(', 'function clearCompleteAxes('), context);
vm.runInContext(extract('async function loadDesignPriorPreview(', 'function installDesignPriorPreview('), context);
const validation = extract('async function loadManifest(', '    const nextRaw = new THREE.BufferGeometry()');
assert.ok(validation.includes('恢复掩码无效'), 'Must exercise the production recovery-mask guard');
assert.ok(validation.includes('projectionClassValueInvalid'), 'Must exercise the production pending-class guard');
vm.runInContext(validation + '\nreturn {pointCount: count};\n} catch (error) { throw error; }\n}', context);
const manifest = local ? JSON.parse(await readFile(manifestUrl,'utf8')) : await (await fetch(manifestUrl)).json();
if (local) context.PREVIEW_RENDER_LIMIT=manifest.preview.pointCount;
const result = await context.loadManifest(manifest);
assert.equal(result.pointCount, Math.min(manifest.preview.pointCount, context.PREVIEW_RENDER_LIMIT));
console.log(`Workbench manifest fetch and all pre-render validations passed: ${manifest.runId}, ${result.pointCount} preview points.`);
if (manifest.terminalPreview) {
  const detail={...manifest,preview:manifest.terminalPreview,tiles:undefined};
  const checked=await context.loadManifest(detail);
  assert.equal(checked.pointCount,detail.preview.pointCount,'Terminal detail must fit without browser downsampling');
  const removed=new Uint8Array(await readBytes(detail.preview.terminal_removedUrl));
  assert.equal(removed.reduce((a,b)=>a+b,0),manifest.terminalCleanup.removedPointCount,'Detail must contain every peeled source point');
  console.log(`Terminal detail validated: ${checked.pointCount} observed points, all ${manifest.terminalCleanup.removedPointCount} peeled points included.`);
}
