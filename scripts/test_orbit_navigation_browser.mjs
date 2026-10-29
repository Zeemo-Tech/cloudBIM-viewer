import assert from 'node:assert/strict'
import { readFile, mkdir, writeFile } from 'node:fs/promises'
import { createHmac } from 'node:crypto'
import { pathToFileURL } from 'node:url'

// Read-only local acceptance; run with the two managed development stacks.
if (!process.env.CLOUDBIM_NAVIGATION_E2E) {
  console.log('SKIP: set CLOUDBIM_NAVIGATION_E2E=1 with CloudBIM and tunnel dev stacks running.')
  process.exit(0)
}
const { chromium } = await import(pathToFileURL(process.env.CLOUDBIM_PLAYWRIGHT_MODULE || '/home/hong/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright/index.mjs'))
const output = '.cloudbim/assessment-20261029-navigation'
await mkdir(output, { recursive: true })
const env = await readFile('.env', 'utf8')
const secret = env.match(/^JWT_SECRET=(.*)$/m)?.[1]?.trim().replace(/^['"]|['"]$/g, '')
assert.ok(secret)
const now = Math.floor(Date.now() / 1000), b64 = value => Buffer.from(JSON.stringify(value)).toString('base64url')
const unsigned = `${b64({ alg: 'HS256', typ: 'JWT' })}.${b64({ sub: '1', username: 'demo', iat: now, exp: now + 1800 })}`
const token = `${unsigned}.${createHmac('sha256', secret).update(unsigned).digest('base64url')}`
const browser = await chromium.launch({ headless: true, executablePath: '/usr/bin/google-chrome',
  args: ['--no-sandbox', '--use-gl=angle', '--use-angle=swiftshader', '--enable-unsafe-swiftshader'] })
const errors = [], checks = []
try {
  const page = await browser.newPage({ viewport: { width: 1600, height: 1000 } })
  page.on('pageerror', error => errors.push(error.message))
  await page.context().addCookies([{ name: 'cloudbim_session', value: token, url: 'http://127.0.0.1:5173', httpOnly: true, sameSite: 'Lax' }])
  await page.route('**/*', route => ['GET', 'HEAD', 'OPTIONS'].includes(route.request().method()) ? route.continue() : route.abort())
  await page.addInitScript(() => {
    window.__THREE_DEVTOOLS__ = new EventTarget(); window.__navigationScenes = []
    window.__THREE_DEVTOOLS__.addEventListener('observe', event => { if (event.detail.isScene) window.__navigationScenes.push(event.detail) })
  })
  await page.goto('http://127.0.0.1:5173/alignment?projectId=3&bimAssetId=13&pointcloudAssetId=14&step=1')
  await page.waitForFunction(() => window.__navigationScenes.some(scene => scene.children.some(c => c.isTransformControlsRoot && c.controls.object)), null, { timeout: 60000 })
  await page.waitForFunction(() => [...document.querySelectorAll('button')].some(b => b.textContent.trim().startsWith('下一步') && !b.disabled), null, { timeout: 60000 })
  await page.evaluate(() => {
    window.__navScene = window.__navigationScenes.find(scene => scene.children.some(c => c.isTransformControlsRoot && c.controls.object))
    window.__navCamera = () => window.__navScene.children.find(c => c.isTransformControlsRoot).controls.camera
    window.__navMatrices = () => {
      window.__navScene.updateMatrixWorld(true)
      const values = []
      window.__navScene.getObjectByName('Calibration content').traverse(o => { if (o.isMesh || o.isPoints) values.push([o.uuid, o.matrixWorld.toArray()]) })
      return values
    }
    window.__navBefore = window.__navMatrices()
  })
  const canvas = page.locator('.viewport > canvas')
  const rect = await canvas.boundingBox()
  assert.ok(rect)
  for (const projection of ['perspective', 'orthographic']) {
    if (projection === 'orthographic') {
      // Use the visible projection control, preserving the application path.
      await page.getByRole('button', { name: '当前透视，切换正交', exact: true }).click()
    }
    await canvas.evaluate(canvas => {
      const r = canvas.getBoundingClientRect()
      for (let i = 0; i < 1000; i++) canvas.dispatchEvent(new WheelEvent('wheel', { deltaY: -100, clientX: r.left + r.width / 2, clientY: r.top + r.height / 2, bubbles: true, cancelable: true }))
    })
    const before = await page.evaluate(() => window.__navCamera().position.toArray())
    await page.mouse.move(rect.x + rect.width * .4, rect.y + rect.height * .4)
    await page.mouse.down({ button: 'right' })
    await page.mouse.move(rect.x + rect.width * .4 + 100, rect.y + rect.height * .4, { steps: 8 })
    await page.mouse.up({ button: 'right' })
    const afterPanState = await page.evaluate(() => ({ position: window.__navCamera().position.toArray(), zoom: window.__navCamera().zoom }))
    const afterPan = afterPanState.position
    for (let i = 0; i < 10; i++) await page.mouse.wheel(0, 100)
    await page.waitForTimeout(200)
    const afterZoom = await page.evaluate(() => ({ position: window.__navCamera().position.toArray(), zoom: window.__navCamera().zoom }))
    const distance = (a, b) => Math.hypot(...a.map((v, i) => v - b[i]))
    const pan = distance(before, afterPan), zoomOut = distance(afterPan, afterZoom.position)
    assert.ok(pan > .001, `real ${projection} pan failed: ${pan}`)
    assert.ok(projection === 'orthographic' ? afterZoom.zoom < afterPanState.zoom : zoomOut > .001, `real zoom-out failed: ${zoomOut}`)
    checks.push({ app: 'CloudBIM actual alignment', projection, pan, zoomOut, zoom: afterZoom.zoom })
    await page.getByRole('button', { name: '重置视角', exact: true }).click()
    if (projection === 'orthographic') assert.equal(await page.evaluate(() => window.__navCamera().zoom), 1, 'orthographic reset must restore a visible full view')
  }
  const matrices = await page.evaluate(() => ({ before: window.__navBefore, after: window.__navMatrices() }))
  const after = new Map(matrices.after)
  for (const [id, matrix] of matrices.before) if (after.has(id)) assert.deepEqual(after.get(id), matrix)
  checks.push({ unchangedGeometryMatrices: matrices.before.length })
  await page.screenshot({ path: `${output}/cloudbim-reset.png` })

  // Tunnel's production shared factory, exercised with native browser input and
  // a WebGL scene. This fixture needs no saved task or application data writes.
  const tunnel = await browser.newPage({ viewport: { width: 1200, height: 800 } })
  tunnel.on('pageerror', error => errors.push(error.message))
  await tunnel.goto('http://127.0.0.1:8848')
  await tunnel.evaluate(async () => {
    const T = await import('/node_modules/three/build/three.module.js')
    const { createOrbitControls } = await import('/src/views/data/components/viewer/viewport/projectionCameras.ts')
    const scene = new T.Scene(), camera = new T.PerspectiveCamera(50, 1.5, .01, 5000)
    scene.background = new T.Color('#f1f5f9')
    camera.position.set(8, 6, 12)
    const renderer = new T.WebGLRenderer({ antialias: true }); renderer.setSize(1200, 800)
    document.body.replaceChildren(renderer.domElement); document.body.style.margin = '0'
    const mesh = new T.Mesh(new T.BoxGeometry(7, 3, 2), new T.MeshNormalMaterial())
    scene.add(mesh)
    const controls = createOrbitControls(camera, renderer.domElement)
    window.__tunnelNavigation = { camera, controls, renderer, scene }
    renderer.setAnimationLoop(() => { controls.update(); renderer.render(scene, camera) })
  })
  await tunnel.locator('canvas').evaluate(canvas => {
    for (let i = 0; i < 1000; i++) canvas.dispatchEvent(new WheelEvent('wheel', { deltaY: -100, clientX: 600, clientY: 400, bubbles: true, cancelable: true }))
  })
  const before = await tunnel.evaluate(() => window.__tunnelNavigation.camera.position.toArray())
  await tunnel.mouse.move(400, 350); await tunnel.mouse.down({ button: 'right' })
  await tunnel.mouse.move(600, 350, { steps: 10 }); await tunnel.mouse.up({ button: 'right' })
  await tunnel.waitForTimeout(800)
  const panned = await tunnel.evaluate(() => window.__tunnelNavigation.camera.position.toArray())
  for (let i = 0; i < 10; i++) await tunnel.mouse.wheel(0, 100)
  await tunnel.waitForTimeout(200)
  const final = await tunnel.evaluate(() => ({ position: window.__tunnelNavigation.camera.position.toArray(), radius: window.__tunnelNavigation.controls.getDistance() }))
  const distance = (a, b) => Math.hypot(...a.map((v, i) => v - b[i]))
  assert.ok(distance(before, panned) > .001)
  assert.ok(distance(panned, final.position) > .001)
  checks.push({ app: 'Tunnel shared factory WebGL fixture', pan: distance(before, panned), zoomOut: distance(panned, final.position), radius: final.radius })
  await tunnel.evaluate(() => { const { camera, controls } = window.__tunnelNavigation; camera.position.set(8, 6, 12); controls.target.set(0, 0, 0); controls.update() })
  await tunnel.screenshot({ path: `${output}/tunnel-shared-preview.png` })
  assert.deepEqual(errors, [])
  await writeFile(`${output}/results.json`, JSON.stringify({ checks, errors }, null, 2))
  console.log(JSON.stringify({ checks, errors }, null, 2))
} finally {
  await browser.close()
}
