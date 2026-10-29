import assert from 'node:assert/strict'
import { readFile, mkdir, writeFile } from 'node:fs/promises'
import { createHmac } from 'node:crypto'
import { pathToFileURL } from 'node:url'

if (!process.env.CLOUDBIM_INSPECTION_E2E) {
  console.log('SKIP: set CLOUDBIM_INSPECTION_E2E=1 with the managed local stack running.')
  process.exit(0)
}
const { chromium } = await import(pathToFileURL(process.env.CLOUDBIM_PLAYWRIGHT_MODULE || '/home/hong/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright/index.mjs'))
const output = process.env.CLOUDBIM_INSPECTION_EVIDENCE || '.cloudbim/assessment-20261029-inspection'
await mkdir(output, { recursive: true })
const env = await readFile('.env', 'utf8')
const secret = env.match(/^JWT_SECRET=(.*)$/m)?.[1]?.trim().replace(/^['"]|['"]$/g, '')
assert.ok(secret, 'local development session secret is required; never logged')
const now = Math.floor(Date.now() / 1000), b64 = v => Buffer.from(JSON.stringify(v)).toString('base64url')
const unsigned = `${b64({ alg: 'HS256', typ: 'JWT' })}.${b64({ sub: '1', username: 'demo', iat: now, exp: now + 1800 })}`
const token = `${unsigned}.${createHmac('sha256', secret).update(unsigned).digest('base64url')}`
const browser = await chromium.launch({ headless: true, executablePath: process.env.CLOUDBIM_CHROMIUM || '/usr/bin/google-chrome',
  args: ['--no-sandbox', '--use-gl=angle', '--use-angle=swiftshader', '--enable-unsafe-swiftshader'] })
const page = await browser.newPage({ viewport: { width: 1920, height: 1080 } })
const errors = [], blockedWrites = [], checks = [], timings = []
let bars
page.on('pageerror', e => errors.push(e.message))
page.on('response', async response => {
  if (!response.url().includes('c2m') || !response.headers()['content-type']?.includes('json')) return
  try { const body = await response.json(); if (body.data?.diagnostics?.rebarComparison?.bars) bars = body.data.diagnostics.rebarComparison.bars } catch {}
})
try {
  await page.context().addCookies([{ name: 'cloudbim_session', value: token, url: 'http://127.0.0.1:5173', httpOnly: true, sameSite: 'Lax' }])
  await page.route('**/*', route => {
    if (!['GET', 'HEAD', 'OPTIONS'].includes(route.request().method())) {
      blockedWrites.push(new URL(route.request().url()).pathname); return route.abort()
    }
    return route.continue()
  })
  await page.addInitScript(() => {
    window.__THREE_DEVTOOLS__ = new EventTarget(); window.__inspectionScenes = []
    window.__THREE_DEVTOOLS__.addEventListener('observe', e => { if (e.detail.isScene) window.__inspectionScenes.push(e.detail) })
  })
  await page.goto(process.env.CLOUDBIM_INSPECTION_URL || 'http://127.0.0.1:5173/alignment?projectId=3&bimAssetId=13&pointcloudAssetId=14&step=3')
  await page.getByRole('button', { name: '下一根钢筋', exact: true }).waitFor({ timeout: 60000 })
  await page.waitForFunction(() => window.__inspectionScenes.some(s => s.getObjectByName('c2m-colored-result')) && !document.body.innerText.includes('正在加载偏差'), null, { timeout: 60000 })
  assert.ok(bars?.length, 'read the real stored comparison identities')
  await page.evaluate(async bars => {
    window.__bars = bars
    window.__T = await import('/node_modules/three/build/three.module.js')
    window.__inspection = () => {
      const scene = window.__inspectionScenes.find(s => s.getObjectByName('c2m-colored-result'))
      const camera = scene.children.find(c => c.isTransformControlsRoot).controls.camera
      const mesh = scene.getObjectByName('c2m-colored-result').children.find(c => c.isMesh && !c.userData.__inspectionForeground)
      let scan; scene.traverse(o => { if (o.isPoints && o.geometry.getAttribute('instance') && !o.userData.__inspectionForeground) scan = o })
      return { scene, camera, mesh, scan, canvas: document.querySelector('.viewport > canvas') }
    }
    window.__selected = () => document.querySelector('.rebar-comparison-details > span')?.textContent.replace(/^IFC：\s*/, '').trim() ?? ''
    window.__snapshot = () => {
      const { scene, camera, mesh, scan } = window.__inspection(); scene.updateMatrixWorld(true)
      const matrices = []
      scene.getObjectByName('Calibration content').traverse(o => {
        if ((o.isMesh || o.isPoints) && !o.userData.__inspectionForeground) matrices.push([o.uuid, o.matrixWorld.toArray()])
      })
      if (scan) matrices.push([scan.uuid, scan.matrixWorld.toArray()])
      const state = o => o ? ({ opacity: o.material.opacity, depthWrite: o.material.depthWrite, transparent: o.material.transparent,
        positionCount: o.geometry.getAttribute('position').count, indexCount: o.geometry.index?.count ?? 0,
        foreground: o.children.filter(c => c.userData.__inspectionForeground).map(c => ({ opacity: c.material.opacity, depthWrite: c.material.depthWrite, count: c.geometry.index.count })) }) : null
      return { id: window.__selected(), camera: camera.matrixWorld.toArray(), matrices, model: state(mesh), scan: state(scan) }
    }
    window.__fingerprints = () => {
      const { mesh, scan } = window.__inspection()
      const hash = attribute => {
        if (!attribute) return null
        const bytes = new Uint8Array(attribute.array.buffer, attribute.array.byteOffset, attribute.array.byteLength)
        let value = 2166136261
        for (const byte of bytes) value = Math.imul(value ^ byte, 16777619)
        return `${bytes.length}:${value >>> 0}`
      }
      return [mesh, scan].map(object => Object.fromEntries(['position', 'normal', 'instance', 'label', 'distance', 'color']
        .map(name => [name, hash(object.geometry.getAttribute(name))])))
    }
    window.__hit = (skip = '', overlap = false) => {
      const { mesh, camera, canvas, scene } = window.__inspection(), T = window.__T
      scene.updateMatrixWorld(true); camera.updateMatrixWorld(true)
      const rect = canvas.getBoundingClientRect(), p = new T.Vector3(), ray = new T.Raycaster()
      const positions = mesh.geometry.getAttribute('position'), indices = mesh.geometry.index
      const identity = hit => window.__bars.find(b => hit.face.a >= b.vertexStart && hit.face.a < b.vertexStart + b.vertexCount)?.ifcGlobalId
      for (const bar of window.__bars) {
        if (bar.ifcGlobalId === skip) continue
        const step = Math.max(1, Math.floor(bar.vertexCount / 30))
        for (let i = bar.vertexStart; i < bar.vertexStart + bar.vertexCount; i += step) {
          p.fromBufferAttribute(positions, i).applyMatrix4(mesh.matrixWorld).project(camera)
          if (p.z < -1 || p.z > 1 || Math.abs(p.x) > .85 || Math.abs(p.y) > .85) continue
          ray.setFromCamera(new T.Vector2(p.x, p.y), camera)
          const hits = ray.intersectObject(mesh, false)
          const ids = [...new Set(hits.map(identity).filter(Boolean))]
          if (!ids.length || ids[0] === skip || (overlap && ids.length < 2)) continue
          return { x: rect.left + (p.x + 1) * rect.width / 2, y: rect.top + (1 - p.y) * rect.height / 2, ids }
        }
      }
      throw Error(`No real member hit (overlap=${overlap})`)
    }
    window.__lowAngle = () => {
      const { mesh, camera, scene } = window.__inspection(), T = window.__T
      scene.updateMatrixWorld(true)
      const box = new T.Box3().setFromObject(mesh), center = box.getCenter(new T.Vector3()), size = box.getSize(new T.Vector3())
      camera.position.copy(center).add(new T.Vector3(size.x * .12, size.y * .08, size.z * .42))
      camera.up.set(0, 1, 0); camera.lookAt(center.clone().add(new T.Vector3(0, 0, -size.z * .25))); camera.updateMatrixWorld(true)
    }
  }, bars)
  const baseline = await page.evaluate(() => window.__snapshot())
  const originalData = await page.evaluate(() => window.__fingerprints())
  await page.getByRole('button', { name: '下一根钢筋', exact: true }).click()
  await page.locator('#rebar-context-transparency').waitFor()
  await page.waitForTimeout(1000)
  await page.getByRole('button', { name: '手动选择', exact: true }).click()
  await page.evaluate(() => window.__lowAngle())
  const initial = await page.evaluate(() => window.__snapshot())
  assert.ok(Math.abs(initial.model.opacity - .2) < 1e-10)
  assert.equal(initial.model.depthWrite, false)
  assert.equal(initial.model.foreground[0].opacity, 1)
  const initialBar = bars.find(bar => bar.ifcGlobalId === initial.id)
  if (initialBar.instanceIds.length) assert.ok(initial.scan.foreground[0].count > 0)
  else assert.equal(initial.scan.foreground[0].count, 0, 'missing members must not invent scan evidence')
  const hit = await page.evaluate(() => window.__hit(window.__selected()))
  await page.mouse.move(hit.x, hit.y); await page.mouse.down()
  const down = await page.evaluate(() => window.__snapshot())
  assert.equal(down.id, initial.id, 'pointerdown must not select another bar')
  await page.mouse.move(hit.x + 80, hit.y + 35, { steps: 8 })
  const during = await page.evaluate(() => window.__snapshot())
  assert.equal(during.id, initial.id)
  assert.notDeepEqual(during.camera, initial.camera, 'orbit continues while pressing over a bar')
  await page.mouse.move(hit.x, hit.y, { steps: 8 }); await page.mouse.up()
  assert.equal(await page.evaluate(() => window.__selected()), initial.id, 'round-trip drag remains navigation')
  checks.push({ name: 'drag-over-member', selectedUnchangedOnPressAndRelease: true, orbitMoved: true })
  await page.evaluate(() => window.__lowAngle())
  const target = await page.evaluate(() => window.__hit(window.__selected()))
  const beforeClick = await page.evaluate(() => window.__snapshot())
  const start = performance.now()
  await page.mouse.click(target.x, target.y)
  await page.waitForFunction(id => window.__selected() === id, target.ids[0])
  timings.push({ name: 'low-angle-click-to-observed-selection', ms: +(performance.now() - start).toFixed(1) })
  const afterClick = await page.evaluate(() => window.__snapshot())
  assert.deepEqual(afterClick.camera, beforeClick.camera, 'manual selection retains the inspection viewpoint')
  assert.equal(afterClick.id, target.ids[0])
  checks.push({ name: 'low-angle-manual-pick', selected: afterClick.id, cameraUnchanged: true })
  const overlap = await page.evaluate(() => window.__hit('', true))
  await page.mouse.click(overlap.x, overlap.y)
  await page.keyboard.down('Alt'); await page.mouse.click(overlap.x, overlap.y); await page.keyboard.up('Alt')
  assert.equal(await page.evaluate(() => window.__selected()), overlap.ids[1])
  checks.push({ name: 'overlapping-bars', candidates: overlap.ids.length, secondSelected: true })
  await page.screenshot({ path: `${output}/inspection-low-angle-1920.png` })
  // Exercise the real, keyboard-accessible native range input.
  await page.locator('#rebar-context-transparency').focus(); await page.keyboard.press('ArrowRight')
  const adjusted = await page.evaluate(() => window.__snapshot())
  assert.ok(Math.abs(adjusted.model.opacity - .15) < 1e-10)
  assert.equal(adjusted.model.foreground[0].opacity, 1)
  checks.push({ name: 'context-transparency', value: 85, selectedOpaque: true })
  await page.setViewportSize({ width: 2560, height: 1440 })
  await page.waitForTimeout(250)
  assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth))
  await page.screenshot({ path: `${output}/inspection-low-angle-2560.png` })
  await page.getByRole('button', { name: '定位当前钢筋', exact: true }).click()
  await page.waitForTimeout(900)
  await page.screenshot({ path: `${output}/inspection-locate-2560.png` })
  const located = await page.evaluate(() => window.__snapshot())
  assert.notDeepEqual(located.camera, adjusted.camera)
  await page.getByRole('button', { name: '退出巡检模式', exact: true }).click()
  const restored = await page.evaluate(() => window.__snapshot())
  assert.equal(restored.model.opacity, baseline.model.opacity)
  assert.equal(restored.model.depthWrite, baseline.model.depthWrite)
  assert.equal(restored.model.foreground.length, 0)
  assert.equal(restored.scan.foreground.length, 0)
  assert.deepEqual(await page.evaluate(() => window.__fingerprints()), originalData, 'geometry, ownership, distances and original colors must restore byte-for-byte')
  const matrices = new Map(baseline.matrices)
  for (const [id, matrix] of restored.matrices) if (matrices.has(id)) assert.deepEqual(matrix, matrices.get(id))
  checks.push({ name: 'exit-restoration', opacityAndDepthRestored: true, existingMatricesUnchanged: matrices.size, geometryOwnershipDistancesColorsUnchanged: true })
  assert.deepEqual(errors, []); assert.deepEqual(blockedWrites, [])
  const result = { bars: bars.length, checks, timings, errors, blockedWrites, baseline, restored, originalData }
  await writeFile(`${output}/browser-checks.json`, JSON.stringify(result, null, 2))
  console.log(JSON.stringify({ bars: bars.length, checks, timings, errors, blockedWrites }, null, 2))
} catch (error) {
  await page.screenshot({ path: `${output}/failure.png` }).catch(() => {})
  await writeFile(`${output}/failure.txt`, String(error.stack))
  throw error
} finally { await browser.close() }
