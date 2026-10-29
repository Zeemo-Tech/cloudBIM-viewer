import assert from 'node:assert/strict'
import { readFile, mkdir, writeFile } from 'node:fs/promises'
import { createHmac } from 'node:crypto'
import { pathToFileURL } from 'node:url'

// Opt-in local WebGL acceptance. All application writes are blocked in this
// isolated browser; the historical scan, alignment and reports stay untouched.
if (!process.env.CLOUDBIM_GIZMO_E2E) {
  console.log('SKIP: set CLOUDBIM_GIZMO_E2E=1 with the managed local stack running.')
  process.exit(0)
}
const { chromium } = await import(pathToFileURL(process.env.CLOUDBIM_PLAYWRIGHT_MODULE || '/home/hong/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright/index.mjs'))
const output = process.env.CLOUDBIM_GIZMO_EVIDENCE || '.cloudbim/assessment-20261028-gizmo'
await mkdir(output, { recursive: true })
const env = await readFile('.env', 'utf8')
const secret = env.match(/^JWT_SECRET=(.*)$/m)?.[1]?.trim().replace(/^['"]|['"]$/g, '')
assert.ok(secret, 'local development JWT_SECRET is required; it is never logged')
const now = Math.floor(Date.now() / 1000)
const b64 = v => Buffer.from(JSON.stringify(v)).toString('base64url')
const unsigned = `${b64({ alg: 'HS256', typ: 'JWT' })}.${b64({ sub: '1', username: 'demo', iat: now, exp: now + 1800 })}`
const token = `${unsigned}.${createHmac('sha256', secret).update(unsigned).digest('base64url')}`
const browser = await chromium.launch({ headless: true, executablePath: process.env.CLOUDBIM_CHROMIUM || '/usr/bin/google-chrome',
  args: ['--no-sandbox', '--use-gl=angle', '--use-angle=swiftshader', '--enable-unsafe-swiftshader'] })
const page = await browser.newPage({ viewport: { width: 1920, height: 1080 }, deviceScaleFactor: Number(process.env.CLOUDBIM_GIZMO_DPR || 1) })
const errors = [], blockedWrites = [], checks = []
page.on('pageerror', e => errors.push(e.message))
try {
  await page.context().addCookies([{ name: 'cloudbim_session', value: token, url: 'http://127.0.0.1:5173', httpOnly: true, sameSite: 'Lax' }])
  await page.route('**/*', route => {
    if (!['GET', 'HEAD', 'OPTIONS'].includes(route.request().method())) {
      blockedWrites.push(new URL(route.request().url()).pathname)
      return route.abort()
    }
    return route.continue()
  })
  await page.addInitScript(() => {
    window.__THREE_DEVTOOLS__ = new EventTarget()
    window.__gizmoScenes = []
    window.__THREE_DEVTOOLS__.addEventListener('observe', e => { if (e.detail.isScene) window.__gizmoScenes.push(e.detail) })
  })
  await page.goto(process.env.CLOUDBIM_GIZMO_URL || 'http://127.0.0.1:5173/alignment?projectId=3&bimAssetId=13&pointcloudAssetId=14&step=1')
  await page.waitForFunction(() => window.__gizmoScenes.some(s => s.children.some(c => c.isTransformControlsRoot && c.controls.object && c.controls.enabled)), null, { timeout: 60000 })
  await page.waitForFunction(() => [...document.querySelectorAll('button')].some(b => b.textContent.trim().startsWith('下一步') && !b.disabled), null, { timeout: 60000 })
  await page.evaluate(() => {
    window.__gizmo = () => {
      const scene = window.__gizmoScenes.find(s => s.children.some(c => c.isTransformControlsRoot && c.controls.object))
      const controllers = scene.children.filter(c => c.isTransformControlsRoot).map(c => c.controls)
      const translate = controllers.find(c => c.mode === 'translate'), rotate = controllers.find(c => c.mode === 'rotate')
      return { scene, translate, rotate, target: translate.object, camera: translate.camera, canvas: translate.domElement }
    }
    window.__gizmoSnapshot = () => {
      const { scene, target, camera } = window.__gizmo()
      scene.updateMatrixWorld(true)
      const stationary = []
      scene.traverse(o => { if (o.isPoints) stationary.push({ id: o.uuid, matrix: [...o.matrixWorld.elements] }) })
      return { position: target.position.toArray(), quaternion: target.quaternion.toArray(), scale: target.scale.toArray(),
        matrix: target.matrixWorld.toArray(), camera: camera.matrixWorld.toArray(), stationary }
    }
    window.__gizmoMeasure = () => {
      const { scene, target, camera, translate, rotate, canvas } = window.__gizmo()
      scene.updateMatrixWorld(true); camera.updateMatrixWorld(true)
      const rect = canvas.getBoundingClientRect()
      const center = target.position.clone().setFromMatrixPosition(target.matrixWorld).project(camera)
      let radius = 0
      for (const controller of [translate, rotate]) {
        const gizmo = controller.getHelper().children.find(child => child.gizmo).gizmo[controller.mode]
        gizmo.traverseVisible(object => {
          const positions = object.geometry?.attributes.position
          if (!positions) return
          for (let i = 0; i < positions.count; i++) {
            const p = target.position.clone().fromBufferAttribute(positions, i).applyMatrix4(object.matrixWorld).project(camera)
            radius = Math.max(radius, Math.hypot((p.x - center.x) * rect.width / 2, (p.y - center.y) * rect.height / 2))
          }
        })
      }
      return { diameter: radius * 2, width: rect.width, height: rect.height,
        pivotVisible: Math.abs(center.x) < 1 && Math.abs(center.y) < 1 && Math.abs(center.z) < 1,
        projection: camera.isPerspectiveCamera ? 'perspective' : 'orthographic',
        cameraDistance: camera.position.distanceTo(target.position.clone().setFromMatrixPosition(target.matrixWorld)),
        zoom: camera.zoom, dpr: devicePixelRatio }
    }
    window.__gizmoHit = (want, axis) => {
      const { scene, translate, rotate, target, camera, canvas } = window.__gizmo()
      scene.updateMatrixWorld(true)
      const rect = canvas.getBoundingClientRect()
      const center = target.position.clone().setFromMatrixPosition(target.matrixWorld).project(camera)
      const cx = (center.x + 1) * rect.width / 2, cy = (1 - center.y) * rect.height / 2
      for (let y = Math.max(4, cy - 240); y < Math.min(rect.height - 4, cy + 240); y += 3) {
        for (let x = Math.max(4, cx - 240); x < Math.min(rect.width - 4, cx + 240); x += 3) {
          const pointer = { x: x / rect.width * 2 - 1, y: 1 - y / rect.height * 2, button: 0 }
          translate.pointerHover(pointer); rotate.pointerHover(pointer)
          const match = want === 'rotate' ? !translate.axis && rotate.axis === 'Y'
            : translate.axis === axis && (want !== 'overlap' || rotate.axis === 'Y')
          if (match) {
            translate.axis = null; rotate.axis = null
            return { x: rect.x + x, y: rect.y + y }
          }
        }
      }
      throw new Error(`no ${want} ${axis} picker in the real scene`)
    }
  })
  const initial = await page.evaluate(() => window.__gizmoSnapshot())
  const frames = () => page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))))
  const measure = async name => {
    await frames()
    const metrics = await page.evaluate(() => window.__gizmoMeasure())
    assert.ok(metrics.diameter > 0 && metrics.diameter <= Math.min(280, Math.min(metrics.width, metrics.height) * .30) + .5,
      `${name}: visible diameter ${metrics.diameter} exceeds the CSS-pixel cap`)
    assert.ok(metrics.pivotVisible, `${name}: the manipulator must be on screen to validate its visible size`)
    checks.push({ name, ...metrics })
    return metrics
  }
  const moveToPivot = async () => {
    const point = await page.evaluate(() => {
      const { target, camera, canvas } = window.__gizmo()
      const rect = canvas.getBoundingClientRect()
      const p = target.position.clone().setFromMatrixPosition(target.matrixWorld).project(camera)
      return { x: rect.x + (p.x + 1) * rect.width / 2, y: rect.y + (1 - p.y) * rect.height / 2 }
    })
    await page.mouse.move(point.x, point.y)
  }
  await measure('default-1920')
  for (const [name, want, axis] of [['horizontal-overlap', 'overlap', 'X'], ['plane', 'translate', 'XZ'], ['yaw', 'rotate', 'Y']]) {
    if (name === 'plane') {
      // Front view hides its edge-on XY engineering plane. Orbit through the
      // normal camera input first so all three plane squares can be picked.
      const box = await page.locator('.viewport > canvas').boundingBox()
      await page.mouse.move(box.x + 120, box.y + 120)
      await page.mouse.down()
      await page.mouse.move(box.x + 280, box.y + 260, { steps: 12 })
      await page.mouse.up()
    }
    const hit = await page.evaluate(([want, axis]) => window.__gizmoHit(want, axis), [want, axis])
    const before = await page.evaluate(() => window.__gizmoSnapshot())
    await page.mouse.move(hit.x, hit.y)
    await page.mouse.down()
    await page.mouse.move(hit.x + 32, hit.y + 12, { steps: 5 })
    const during = await page.evaluate(() => ({ snapshot: window.__gizmoSnapshot(), text: document.querySelector('.transform-handle-feedback')?.textContent,
      dragging: [window.__gizmo().translate.dragging, window.__gizmo().rotate.dragging],
      saveDisabled: [...document.querySelectorAll('button')].find(b => b.textContent.trim() === '保存粗配准')?.disabled,
      nextDisabled: [...document.querySelectorAll('button')].find(b => b.textContent.trim().startsWith('下一步'))?.disabled }))
    assert.deepEqual(during.snapshot.camera, before.camera)
    assert.deepEqual(during.snapshot.scale, before.scale)
    assert.deepEqual(during.dragging, want === 'rotate' ? [false, true] : [true, false])
    if (want === 'rotate') {
      assert.deepEqual(during.snapshot.position, before.position)
      assert.notDeepEqual(during.snapshot.quaternion, before.quaternion)
    } else {
      assert.deepEqual(during.snapshot.quaternion, before.quaternion)
      assert.notDeepEqual(during.snapshot.position, before.position)
    }
    assert.ok(during.text.includes('Esc'))
    assert.equal(during.saveDisabled, true, 'cannot save an incomplete gesture')
    assert.equal(during.nextDisabled, true, 'cannot analyze an incomplete gesture')
    await page.screenshot({ path: `${output}/${name}-1920.png` })
    await page.keyboard.press('Escape')
    await page.mouse.up()
    const after = await page.evaluate(() => window.__gizmoSnapshot())
    assert.deepEqual(after.matrix, before.matrix)
    const original = new Map(before.stationary.map(p => [p.id, p.matrix]))
    for (const p of after.stationary) if (original.has(p.id)) assert.deepEqual(p.matrix, original.get(p.id))
    checks.push({ name, hit, feedback: during.text, cameraUnchanged: true, cancelledExactly: true })
  }
  const sizes = [{ width: 2560, height: 1440 }, { width: 1707, height: 960 },
    { width: 1512, height: 982 }, { width: 1440, height: 900 }, { width: 1280, height: 800 }]
  for (const size of sizes) {
    await page.setViewportSize(size)
    await measure(`resize-${size.width}`)
    const hit = await page.evaluate(() => window.__gizmoHit('translate', 'X'))
    await page.mouse.move(hit.x, hit.y)
    await page.screenshot({ path: `${output}/layout-${size.width}.png` })
    assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth))
  }
  const beforeSizing = await page.evaluate(() => window.__gizmoSnapshot())
  // Native wheel input exercises OrbitControls and the page's per-frame sizing.
  for (const [name, delta] of [['wheel-far', 1800], ['wheel-close', -3200]]) {
    await moveToPivot()
    await page.mouse.wheel(0, delta)
    await measure(name)
  }
  const beforeManual = await page.evaluate(() => window.__gizmoSnapshot())
  await page.keyboard.down('Control')
  for (let i = 0; i < 15; i++) await page.mouse.wheel(0, -100)
  await page.keyboard.up('Control')
  await measure('manual-maximum')
  assert.ok((await page.locator('.registration-handle-hint').allTextContents()).some(text => text.includes('200%')))
  assert.deepEqual((await page.evaluate(() => window.__gizmoSnapshot())).camera, beforeManual.camera)
  await page.getByRole('button', { name: '恢复默认手柄大小', exact: true }).click()
  assert.ok((await page.locator('.registration-handle-hint').allTextContents()).some(text => text.includes('100%')))
  await measure('reset-size')
  await page.screenshot({ path: `${output}/zoom-close-1280.png` })
  // Restore the view and navigation pivot together; never reset the model pose.
  await page.getByRole('button', { name: '重置视角', exact: true }).click()
  await frames()
  await page.getByRole('button', { name: '当前透视，切换正交', exact: true }).click()
  const orthoDefault = await measure('orthographic-default')
  await moveToPivot()
  await page.mouse.wheel(0, -2000)
  await page.waitForFunction(zoom => window.__gizmo().camera.zoom !== zoom, orthoDefault.zoom, { timeout: 10000 })
  await measure('orthographic-close')
  await page.screenshot({ path: `${output}/orthographic-close-1280.png` })
  const orthoHit = await page.evaluate(() => window.__gizmoHit('translate', 'X'))
  await page.mouse.move(orthoHit.x, orthoHit.y)
  await page.mouse.down()
  await page.mouse.move(orthoHit.x + 20, orthoHit.y + 10, { steps: 4 })
  assert.equal(await page.evaluate(() => window.__gizmo().translate.dragging), true)
  await page.keyboard.press('Escape')
  await page.mouse.up()
  checks.push({ name: 'orthographic-close-picking', cancelledExactly: true })
  await page.getByRole('button', { name: '当前正交，切换透视', exact: true }).click()
  await page.getByRole('button', { name: '重置视角', exact: true }).click()
  await frames()
  assert.deepEqual((await page.evaluate(() => window.__gizmoSnapshot())).matrix, beforeSizing.matrix)
  assert.equal(await page.getByRole('button', { name: /^下一步/ }).isDisabled(), false)
  // Also finish a normal drag: mouseup must commit once, release the camera,
  // and block downstream analysis until the user explicitly saves alignment.
  const hit = await page.evaluate(() => window.__gizmoHit('translate', 'X'))
  const beforeCommit = await page.evaluate(() => window.__gizmoSnapshot())
  await page.mouse.move(hit.x, hit.y)
  await page.mouse.down()
  await page.mouse.move(hit.x + 24, hit.y, { steps: 4 })
  const pending = await page.evaluate(() => window.__gizmoSnapshot())
  await page.mouse.up()
  const committed = await page.evaluate(() => ({ snapshot: window.__gizmoSnapshot(),
    dragging: [window.__gizmo().translate.dragging, window.__gizmo().rotate.dragging],
    dirty: [...document.querySelectorAll('button')].find(b => b.textContent.trim().startsWith('下一步'))?.disabled }))
  assert.deepEqual(committed.snapshot.matrix, pending.matrix)
  assert.deepEqual(committed.snapshot.quaternion, beforeCommit.quaternion)
  assert.notDeepEqual(committed.snapshot.position, beforeCommit.position)
  assert.deepEqual(committed.dragging, [false, false])
  assert.equal(committed.dirty, true)
  checks.push({ name: 'mouseup-commit', exclusive: true, dirty: true })
  assert.deepEqual(errors, [])
  assert.deepEqual(blockedWrites, [])
  const result = { checks, errors, blockedWrites, initialPointObjects: initial.stationary.length,
    screenshots: ['horizontal-overlap-1920.png', 'plane-1920.png', 'yaw-1920.png',
      ...sizes.map(size => `layout-${size.width}.png`), 'zoom-close-1280.png', 'orthographic-close-1280.png'] }
  await writeFile(`${output}/browser-checks.json`, JSON.stringify(result, null, 2))
  console.log(JSON.stringify(result, null, 2))
} catch (error) {
  await page.screenshot({ path: `${output}/failure.png` }).catch(() => {})
  await writeFile(`${output}/failure.json`, JSON.stringify({ error: String(error), errors, blockedWrites, text: (await page.locator('body').innerText()).slice(0, 1800) }, null, 2))
  throw error
} finally {
  await browser.close()
}
