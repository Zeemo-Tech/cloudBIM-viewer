import assert from 'node:assert/strict'
import { readFile, mkdir, writeFile } from 'node:fs/promises'
import { createHmac } from 'node:crypto'
import { pathToFileURL } from 'node:url'

if (!process.env.CLOUDBIM_INSPECTION_E2E) {
  console.log('SKIP: set CLOUDBIM_INSPECTION_E2E=1 with the managed local stack running.')
  process.exit(0)
}
const { chromium } = await import(pathToFileURL(process.env.CLOUDBIM_PLAYWRIGHT_MODULE || '/home/hong/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright/index.mjs'))
const output = process.env.CLOUDBIM_INSPECTION_EVIDENCE || '.cloudbim/inspection-upright-20261029'
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
  await page.getByRole('button', { name: '自动巡检', exact: true }).waitFor({ timeout: 60000 })
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
    window.__selected = () => document.querySelector('.rebar-inspection-status')?.dataset.memberId ?? ''
    window.__snapshot = () => {
      const { scene, camera, mesh, scan } = window.__inspection(); scene.updateMatrixWorld(true)
      const matrices = []
      scene.getObjectByName('Calibration content').traverse(o => {
        if ((o.isMesh || o.isPoints) && !o.userData.__inspectionForeground) matrices.push([o.uuid, o.matrixWorld.toArray()])
      })
      if (scan) matrices.push([scan.uuid, scan.matrixWorld.toArray()])
      const state = o => o ? ({ opacity: o.material.opacity, depthWrite: o.material.depthWrite, transparent: o.material.transparent,
        vertexColors: o.material.vertexColors, color: o.material.color?.toArray(),
        positionCount: o.geometry.getAttribute('position').count, indexCount: o.geometry.index?.count ?? 0,
        foreground: o.children.filter(c => c.userData.__inspectionForeground).map(c => ({ opacity: c.material.opacity, depthWrite: c.material.depthWrite,
          vertexColors: c.material.vertexColors, color: c.material.color?.toArray(), count: c.geometry.index.count })) }) : null
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
    window.__frameReadout = () => {
      const { mesh, camera, canvas } = window.__inspection(), T = window.__T
      const bar = window.__bars.find(b => b.ifcGlobalId === window.__selected())
      camera.updateMatrixWorld(true); mesh.updateWorldMatrix(true, false)
      const rect = canvas.getBoundingClientRect(), positions = mesh.geometry.getAttribute('position'), projected = [], worldBounds = new T.Box3()
      for (let i = bar.vertexStart; i < bar.vertexStart + bar.vertexCount; i++) {
        const point = new T.Vector3().fromBufferAttribute(positions, i).applyMatrix4(mesh.matrixWorld)
        worldBounds.expandByPoint(point); projected.push(point.project(camera))
      }
      const mean = projected.reduce((sum, p) => sum.add(p), new T.Vector3()).divideScalar(projected.length)
      let xx = 0, xy = 0, yy = 0
      for (const p of projected) {
        const dx = (p.x - mean.x) * rect.width, dy = (p.y - mean.y) * rect.height
        xx += dx * dx; xy += dx * dy; yy += dy * dy
      }
      const theta = Math.abs(Math.atan2(2 * xy, xx - yy) * 90 / Math.PI)
      const center = worldBounds.getCenter(new T.Vector3())
      const viewCenter = camera.position.clone().addScaledVector(camera.getWorldDirection(new T.Vector3()), camera.position.distanceTo(center))
      const above = viewCenter.clone().add(new T.Vector3(0, .01, 0)).project(camera), origin = viewCenter.project(camera)
      return { id: bar.ifcGlobalId, horizontalAngleDegrees: theta, verticalAngleDegrees: 90 - theta,
        cameraUp: camera.up.toArray(), worldUpScreenDx: above.x - origin.x, worldUpScreenDy: above.y - origin.y,
        elevation: -camera.getWorldDirection(new T.Vector3()).y, worldSize: worldBounds.getSize(new T.Vector3()).toArray(),
        allVerticesVisible: projected.every(p => Math.abs(p.x) < 1 && Math.abs(p.y) < 1 && Math.abs(p.z) < 1),
        widthFraction: (Math.max(...projected.map(p => p.x)) - Math.min(...projected.map(p => p.x))) / 2,
        heightFraction: (Math.max(...projected.map(p => p.y)) - Math.min(...projected.map(p => p.y))) / 2 }
    }
    window.__hit = (skip = '', overlap = false, wanted = '') => {
      const { mesh, camera, canvas, scene } = window.__inspection(), T = window.__T
      scene.updateMatrixWorld(true); camera.updateMatrixWorld(true)
      const rect = canvas.getBoundingClientRect(), p = new T.Vector3(), ray = new T.Raycaster()
      const positions = mesh.geometry.getAttribute('position'), indices = mesh.geometry.index
      const identity = hit => window.__bars.find(b => hit.face.a >= b.vertexStart && hit.face.a < b.vertexStart + b.vertexCount)?.ifcGlobalId
      for (const bar of window.__bars) {
        if (bar.ifcGlobalId === skip || (wanted && bar.ifcGlobalId !== wanted)) continue
        const step = Math.max(1, Math.floor(bar.vertexCount / 30))
        for (let i = bar.vertexStart; i < bar.vertexStart + bar.vertexCount; i += step) {
          p.fromBufferAttribute(positions, i).applyMatrix4(mesh.matrixWorld).project(camera)
          if (p.z < -1 || p.z > 1 || Math.abs(p.x) > .85 || Math.abs(p.y) > .85) continue
          // MouseEvent coordinates are integer CSS pixels. Cast the exact ray
          // that the real input will use, not an unstable projected mesh edge.
          const x = Math.round(rect.left + (p.x + 1) * rect.width / 2), y = Math.round(rect.top + (1 - p.y) * rect.height / 2)
          if (document.elementFromPoint(x, y) !== canvas) continue
          ray.setFromCamera(new T.Vector2((x - rect.left) / rect.width * 2 - 1, 1 - (y - rect.top) / rect.height * 2), camera)
          const side = mesh.material.side
          mesh.material.side = T.DoubleSide
          const hits = ray.intersectObject(mesh, false)
          mesh.material.side = side
          const ids = [...new Set(hits.map(identity).filter(Boolean))]
          if (!ids.length || ids[0] === skip || (wanted && ids[0] !== wanted) || (overlap && ids.length < 2)) continue
          return { x, y, ids }
        }
      }
      throw Error(`No real member hit (overlap=${overlap})`)
    }

  }, bars)
  const assertLevel = frame => {
    assert.deepEqual(frame.cameraUp, [0, 1, 0])
    assert.ok(Math.abs(frame.worldUpScreenDx) < 1e-8 && frame.worldUpScreenDy > 0, 'engineering vertical must stay upright')
    assert.equal(frame.allVerticesVisible, true)
  }
  const baseline = await page.evaluate(() => window.__snapshot())
  const originalData = await page.evaluate(() => window.__fingerprints())
  assert.deepEqual(await page.locator('.c2m-deviation-workspace > section').evaluateAll(nodes => nodes.map(n => n.getAttribute('aria-label'))), ['偏差计算', '配色调整'])
  const entry = await page.evaluate(() => window.__hit())
  await page.mouse.click(entry.x, entry.y)
  assert.equal(await page.evaluate(() => window.__selected()), '')
  await page.mouse.dblclick(entry.x, entry.y)
  await page.waitForFunction(id => window.__selected() === id, entry.ids[0])
  await page.waitForTimeout(900)
  checks.push({ name: 'direct-double-click', selected: entry.ids[0], noModeSwitch: true })
  const manualFrame = await page.evaluate(() => window.__frameReadout())
  assert.ok(manualFrame.horizontalAngleDegrees < 3, `manual long-axis angle: ${manualFrame.horizontalAngleDegrees}`)
  assertLevel(manualFrame)
  const focused = await page.evaluate(() => window.__snapshot())
  for (const material of [focused.scan, ...focused.scan.foreground]) {
    assert.equal(material.vertexColors, false)
    assert.equal(material.color[0], material.color[1]); assert.equal(material.color[1], material.color[2])
  }
  assert.equal(focused.model.vertexColors, true); assert.equal(focused.model.foreground[0].vertexColors, true)
  checks.push({ name: 'manual-horizontal-frame-and-gray-scan', ...manualFrame, scanGray: true, deviationPaletteRetained: true })
  await page.screenshot({ path: `${output}/inspection-horizontal-manual-1920.png` })
  // Use real OrbitControls gestures so its target stays synchronized with the camera.
  const orbit = await page.evaluate(() => {
    const { camera, canvas } = window.__inspection(), T = window.__T
    const elevation = Math.asin(-camera.getWorldDirection(new T.Vector3()).y)
    const rect = canvas.getBoundingClientRect()
    return { x: rect.left + rect.width * .9, y: rect.top + rect.height * .9,
      dy: -(elevation - .05) * rect.height / (2 * Math.PI) }
  })
  await page.mouse.move(orbit.x, orbit.y); await page.mouse.down()
  await page.mouse.move(orbit.x, orbit.y + orbit.dy, { steps: 12 }); await page.mouse.up()
  await page.waitForTimeout(200)
  const lowElevation = await page.evaluate(() => Math.asin(-window.__inspection().camera.getWorldDirection(new window.__T.Vector3()).y))
  assert.ok(Math.abs(lowElevation) < .2, `actual orbit reached low angle: ${lowElevation}`)
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
  await page.waitForTimeout(200)
  const target = await page.evaluate(() => window.__hit(window.__selected()))
  const beforeClick = await page.evaluate(() => window.__snapshot())
  const start = performance.now()
  await page.mouse.click(target.x, target.y)
  await page.waitForFunction(id => window.__selected() === id, target.ids[0], { timeout: 4000 })
  timings.push({ name: 'low-angle-click-to-observed-selection', ms: +(performance.now() - start).toFixed(1) })
  const afterClick = await page.evaluate(() => window.__snapshot())
  const cameraDelta = Math.max(...afterClick.camera.map((value, i) => Math.abs(value - beforeClick.camera[i])))
  assert.ok(cameraDelta < 1e-10, `manual selection moved the inspection viewpoint: ${cameraDelta}`)
  assert.equal(afterClick.id, target.ids[0])
  checks.push({ name: 'low-angle-manual-pick', selected: afterClick.id, cameraUnchanged: true })
  const overlap = await page.evaluate(() => window.__hit('', true))
  await page.mouse.click(overlap.x, overlap.y)
  assert.equal(await page.evaluate(() => window.__selected()), overlap.ids[0])
  await page.keyboard.down('Alt'); await page.mouse.click(overlap.x, overlap.y); await page.keyboard.up('Alt')
  assert.equal(await page.evaluate(() => window.__selected()), overlap.ids[1])
  checks.push({ name: 'overlapping-bars', candidates: overlap.ids.length, secondSelected: true })
  await page.screenshot({ path: `${output}/inspection-low-angle-1920.png` })
  await page.keyboard.press('Escape')
  assert.equal(await page.evaluate(() => window.__selected()), '')
  checks.push({ name: 'escape-exit', passed: true })
  await page.setViewportSize({ width: 2560, height: 1440 })
  await page.waitForTimeout(250)
  assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth))
  await page.getByRole('button', { name: '自动巡检', exact: true }).click()
  await page.waitForTimeout(1000)
  const first = await page.evaluate(() => window.__selected())
  const autoFirstFrame = await page.evaluate(() => window.__frameReadout())
  await page.waitForFunction(id => window.__selected() && window.__selected() !== id, first, { timeout: 6500 })
  await page.waitForTimeout(900)
  const autoNextFrame = await page.evaluate(() => window.__frameReadout())
  for (const frame of [autoFirstFrame, autoNextFrame]) {
    assert.ok(frame.worldSize[1] > Math.max(frame.worldSize[0], frame.worldSize[2]), 'use real vertical stirrups for this regression')
    assert.ok(frame.verticalAngleDegrees < 3, `stirrup vertical angle: ${frame.verticalAngleDegrees}`)
    assert.ok(Math.abs(frame.elevation) < 1e-8, 'stirrup uses a front elevation')
    assertLevel(frame)
  }
  await page.screenshot({ path: `${output}/inspection-autoplay-2560.png` })
  checks.push({ name: 'autoplay-advances-and-keeps-stirrups-upright', first: autoFirstFrame, next: autoNextFrame })
  await page.getByRole('button', { name: '停止自动巡检', exact: true }).click()
  assert.equal(await page.evaluate(() => window.__selected()), '')
  const direct = await page.evaluate(id => window.__hit('', false, id), autoNextFrame.id)
  await page.mouse.dblclick(direct.x, direct.y)
  await page.waitForFunction(id => window.__selected() === id, direct.ids[0])
  await page.waitForTimeout(900)
  const stirrupManual = await page.evaluate(() => window.__frameReadout())
  assertLevel(stirrupManual)
  assert.ok(stirrupManual.verticalAngleDegrees < 3)
  assert.ok(Math.abs(stirrupManual.elevation) < 1e-8)
  checks.push({ name: 'manual-stirrup-upright', ...stirrupManual })
  await page.screenshot({ path: `${output}/inspection-stirrup-manual-2560.png` })
  const canvas = await page.locator('.viewport > canvas').boundingBox()
  await page.mouse.dblclick(canvas.x + 24, canvas.y + 160)
  assert.equal(await page.evaluate(() => window.__selected()), '')
  checks.push({ name: 'blank-double-click-exit', passed: true })
  const restored = await page.evaluate(() => window.__snapshot())
  assert.equal(restored.model.opacity, baseline.model.opacity)
  assert.equal(restored.model.depthWrite, baseline.model.depthWrite)
  assert.equal(restored.model.foreground.length, 0)
  assert.equal(restored.scan.foreground.length, 0)
  assert.equal(restored.scan.vertexColors, baseline.scan.vertexColors)
  assert.deepEqual(restored.scan.color, baseline.scan.color)
  assert.deepEqual(await page.evaluate(() => window.__fingerprints()), originalData, 'geometry, ownership, distances and original colors must restore byte-for-byte')
  const matrices = new Map(baseline.matrices)
  for (const [id, matrix] of restored.matrices) if (matrices.has(id)) assert.deepEqual(matrix, matrices.get(id))
  checks.push({ name: 'exit-restoration', opacityAndDepthRestored: true, existingMatricesUnchanged: matrices.size, geometryOwnershipDistancesColorsUnchanged: true })
  for (const [step, label] of [[1, '坐标配准'], [2, '分类去噪'], [4, '报告']]) {
    await page.getByRole('button', { name: `第 ${step} 步：${label}`, exact: true }).click()
    await page.waitForTimeout(step === 4 ? 2000 : 300)
    if (step === 4) {
      assert.equal(await page.locator('.report-reader-toolbar strong').first().textContent(), '100%')
      checks.push({ name: 'report-default-zoom', percent: 100 })
    }
    await page.screenshot({ path: `${output}/step-${step}-2560.png` })
  }
  await page.setViewportSize({ width: 1280, height: 800 })
  await page.getByRole('button', { name: '第 3 步：偏差对比', exact: true }).click()
  await page.waitForTimeout(500)
  assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth))
  await page.screenshot({ path: `${output}/comparison-1280.png` })
  assert.deepEqual(errors, []); assert.deepEqual(blockedWrites, [])
  const result = { bars: bars.length, checks, timings, errors, blockedWrites, baseline, restored, originalData }
  await writeFile(`${output}/browser-checks.json`, JSON.stringify(result, null, 2))
  console.log(JSON.stringify({ bars: bars.length, checks, timings, errors, blockedWrites }, null, 2))
} catch (error) {
  await writeFile(`${output}/page-errors.json`, JSON.stringify({ errors, blockedWrites, checks, timings }, null, 2))
  await page.screenshot({ path: `${output}/failure.png` }).catch(() => {})
  await writeFile(`${output}/failure.txt`, String(error.stack))
  throw error
} finally { await browser.close() }
