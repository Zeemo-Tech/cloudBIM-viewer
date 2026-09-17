import assert from 'node:assert/strict';
import { pathToFileURL } from 'node:url';

const { chromium } = await import(pathToFileURL(process.env.CLOUDBIM_PLAYWRIGHT_MODULE || '/home/hong/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright/index.mjs'));
const browser = await chromium.launch({ headless: true, executablePath: process.env.CLOUDBIM_CHROMIUM || '/home/hong/.cache/ms-playwright/chromium-1228/chrome-linux64/chrome', args: ['--no-sandbox', '--use-gl=angle', '--use-angle=swiftshader', '--enable-unsafe-swiftshader'] });
const page = await browser.newPage({ viewport: { width: 1600, height: 1000 } });
const errors = [];
page.on('pageerror', error => errors.push(error.message));
try {
  await page.goto(process.env.CLOUDBIM_POINTCLOUD_URL || 'http://127.0.0.1:8766/?run=20260914T120236-67f3aadd');
  await page.waitForFunction(() => window.pointcloudDebug?.current?.controlNet && document.querySelector('#controlNetHint')?.textContent.includes('样本点'), null, { timeout: 60000 });
  const before = await page.evaluate(() => {
    const { renderer, controlNetScene, camera } = window.pointcloudDebug;
    renderer.render(controlNetScene, camera);
    return { calls: renderer.info.render.calls, points: renderer.info.render.points, triangles: renderer.info.render.triangles, camera: camera.position.toArray() };
  });
  assert.ok(before.points > 100000 && before.triangles > 100000, 'Use a dense control-net result with scan points and fitted tubes visible');
  const canvas = page.locator('#view canvas');
  const box = await canvas.boundingBox();
  await page.mouse.move(box.x + box.width * .45, box.y + box.height * .5);
  await page.mouse.down();
  await page.mouse.move(box.x + box.width * .65, box.y + box.height * .55, { steps: 12 });
  await page.mouse.up();
  const after = await page.evaluate(() => {
    const { renderer, controlNetScene, camera } = window.pointcloudDebug;
    renderer.render(controlNetScene, camera);
    return { calls: renderer.info.render.calls, points: renderer.info.render.points, triangles: renderer.info.render.triangles, camera: camera.position.toArray() };
  });
  console.log(JSON.stringify({ before, after, errors }));
  assert.notDeepEqual(after.camera, before.camera, 'Real pointer dragging must move the camera');
  assert.equal(after.points, before.points, 'Dragging must preserve the displayed scan points');
  assert.equal(after.triangles, before.triangles, 'Dragging must preserve fitted tube detail');
  assert.ok(after.calls < 1200, `${after.calls} draw calls per drag frame; control-net tube segments must be batched`);
  assert.deepEqual(errors, []);
} finally {
  await browser.close();
}
