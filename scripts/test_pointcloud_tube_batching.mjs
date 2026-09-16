import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';
import * as THREE from 'three';

const source = readFileSync(new URL('./pointcloud-debug/viewer.js', import.meta.url), 'utf8');
const start = source.indexOf('function createControlNetOverlay(');
const end = source.indexOf('\nfunction rebuildControlNetOverlay(', start);
const create = new Function('THREE', `${source.slice(start, end)}\nreturn createControlNetOverlay;`)(THREE);

test('dense fitted centerlines batch all segments without changing cylinder surfaces or bounds', () => {
  const origin = [9, 19, 29];
  const centerline = Array.from({ length: 401 }, (_, i) => [9 + i / 1000, 19 + Math.sin(i / 10) / 10, 29 + i / 2000]);
  centerline.splice(15, 0, [...centerline[14]]); // Ignore the same degenerate segment as before.
  const report = { inventory: { units: [] }, instances: [{ id: 1, status: 'fitted', diameterM: .012, centerlineM: centerline }] };
  const group = create(report, origin, { colorMode: 'parents' });
  const tubes = [];
  group.traverse(object => { if (object.isMesh) tubes.push(object); });
  assert.equal(tubes.length, 1, 'Hundreds of samples must use one tube batch');
  const batch = tubes[0];
  assert.equal(batch.count, 400);
  const bounds = batch.boundingBox;
  let instance = 0;
  for (let i = 1; i < centerline.length; i++) {
    const a = new THREE.Vector3(...centerline[i - 1]).sub(new THREE.Vector3(...origin));
    const b = new THREE.Vector3(...centerline[i]).sub(new THREE.Vector3(...origin));
    const direction = b.clone().sub(a), length = direction.length();
    if (length <= 1e-7) continue;
    const original = new THREE.CylinderGeometry(.006, .006, length, 12, 1, true);
    const originalTransform = new THREE.Matrix4().compose(a.clone().add(b).multiplyScalar(.5), new THREE.Quaternion().setFromUnitVectors(new THREE.Vector3(0, 1, 0), direction.normalize()), new THREE.Vector3(1, 1, 1));
    const matrix = new THREE.Matrix4();
    batch.getMatrixAt(instance++, matrix);
    assert.deepEqual(batch.geometry.index.array, original.index.array, 'Triangle topology remains identical');
    for (let vertex = 0; vertex < original.attributes.position.count; vertex++) {
      const expected = new THREE.Vector3().fromBufferAttribute(original.attributes.position, vertex).applyMatrix4(originalTransform);
      const actual = new THREE.Vector3().fromBufferAttribute(batch.geometry.attributes.position, vertex).applyMatrix4(matrix);
      assert.ok(expected.distanceTo(actual) < 1e-6, 'Every transformed vertex preserves the previous cylinder surface');
      assert.ok(bounds.clone().expandByScalar(1e-6).containsPoint(actual), 'Culling and fit-to-view bounds include every segment');
    }
    original.dispose();
  }
  assert.equal(instance, batch.count);
  assert.equal(batch.material.opacity, .16);
  assert.equal(batch.material.side, THREE.DoubleSide);
  group.traverse(object => { if (object.isInstancedMesh) object.dispose(); object.geometry?.dispose(); object.material?.dispose(); });
});

test('zero-length fitted paths do not allocate empty tube batches', () => {
  const group = create({ inventory: { units: [] }, instances: [{ id: 1, status: 'fitted', diameterM: .012, centerlineM: [[1, 2, 3], [1, 2, 3]] }] }, [0, 0, 0]);
  group.traverse(object => { assert.ok(!object.isMesh); object.geometry?.dispose(); object.material?.dispose(); });
});
