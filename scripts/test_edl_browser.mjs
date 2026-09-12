import assert from 'node:assert/strict'
import { spawn } from 'node:child_process'
import { createHmac } from 'node:crypto'
import { mkdtemp, readFile, rm } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import { join } from 'node:path'

const chromePort = 9323
const profileDir = await mkdtemp(join(tmpdir(), 'cloudbim-edl-test-'))
const chrome = spawn('google-chrome', [
  '--headless=new',
  '--no-sandbox',
  '--disable-gpu-sandbox',
  `--remote-debugging-port=${chromePort}`,
  '--remote-allow-origins=*',
  `--user-data-dir=${profileDir}`,
  'about:blank',
], { stdio: 'ignore' })

async function waitForChrome() {
  const deadline = Date.now() + 10_000
  while (Date.now() < deadline) {
    try {
      const response = await fetch(`http://127.0.0.1:${chromePort}/json/version`)
      if (response.ok) return
    } catch {}
    await new Promise((resolve) => setTimeout(resolve, 100))
  }
  throw new Error('Chrome DevTools did not become ready')
}

async function openPage() {
  const response = await fetch(`http://127.0.0.1:${chromePort}/json/new`, { method: 'PUT' })
  return response.json()
}

function connectCdp(url) {
  const socket = new WebSocket(url)
  let nextId = 0
  const pending = new Map()
  socket.addEventListener('message', (event) => {
    const message = JSON.parse(event.data)
    const handler = pending.get(message.id)
    if (!handler) return
    pending.delete(message.id)
    if (message.error) handler.reject(new Error(message.error.message))
    else handler.resolve(message.result)
  })
  const ready = new Promise((resolve, reject) => {
    socket.addEventListener('open', resolve, { once: true })
    socket.addEventListener('error', reject, { once: true })
  })
  return {
    async call(method, params = {}) {
      await ready
      const id = ++nextId
      const result = new Promise((resolve, reject) => pending.set(id, { resolve, reject }))
      socket.send(JSON.stringify({ id, method, params }))
      return result
    },
    close() {
      socket.close()
    },
  }
}

function base64Url(value) {
  return Buffer.from(value).toString('base64url')
}

async function developmentSessionToken() {
  let secret = 'cloudbim-dev-secret'
  try {
    const envFile = await readFile(new URL('../.env', import.meta.url), 'utf8')
    const configured = envFile.match(/^JWT_SECRET=(.*)$/m)?.[1]?.trim().replace(/^['"]|['"]$/g, '')
    if (configured) secret = configured
  } catch {}
  const now = Math.floor(Date.now() / 1000)
  const unsigned = `${base64Url(JSON.stringify({ alg: 'HS256', typ: 'JWT' }))}.${base64Url(JSON.stringify({
    sub: '1',
    username: 'demo',
    iat: now,
    exp: now + 3_600,
  }))}`
  const signature = createHmac('sha256', secret).update(unsigned).digest('base64url')
  return `${unsigned}.${signature}`
}

const pixelProbe = `
  async () => {
    const THREE = await import('/node_modules/.vite/deps/three.js')
    const { PointCloudEdlPipeline } = await import('/src/components/preview/edlPipeline.ts')
    const canvas = document.createElement('canvas')
    document.body.replaceChildren(canvas)
    const renderer = new THREE.WebGLRenderer({ canvas, preserveDrawingBuffer: true })
    renderer.setPixelRatio(1)
    renderer.setSize(256, 256, false)
    renderer.setClearColor(0x0b1020, 1)
    renderer.localClippingEnabled = true
    renderer.toneMapping = THREE.ACESFilmicToneMapping
    renderer.toneMappingExposure = 1
    renderer.outputColorSpace = THREE.SRGBColorSpace

    const scene = new THREE.Scene()
    scene.background = new THREE.Color(0x0b1020)
    const camera = new THREE.PerspectiveCamera(50, 1, 0.01, 100)
    camera.position.set(0, 0, 4)
    camera.lookAt(0, 0, 0)
    camera.updateMatrixWorld(true)

    const positions = []
    for (let y = -8; y <= 8; y += 1) {
      for (let x = -8; x <= 8; x += 1) {
        positions.push(x * 0.09, y * 0.09, 0)
      }
    }
    const geometry = new THREE.BufferGeometry()
    geometry.setAttribute('position', new THREE.Float32BufferAttribute(positions, 3))
    const material = new THREE.PointsMaterial({ color: 0x86898d, size: 5, sizeAttenuation: false })
    material.fog = false
    material.toneMapped = false
    material.depthWrite = true
    material.depthTest = true
    material.onBeforeCompile = (shader) => {
      shader.fragmentShader = shader.fragmentShader.replace(
        '#include <clipping_planes_fragment>',
        '#include <clipping_planes_fragment>\\nif (length(gl_PointCoord - vec2(0.5)) > 0.5) discard;',
      )
    }
    const points = new THREE.Points(geometry, material)
    scene.add(points)

    const sample = () => {
      const copy = document.createElement('canvas')
      copy.width = canvas.width
      copy.height = canvas.height
      const context = copy.getContext('2d')
      context.drawImage(canvas, 0, 0)
      const pixels = context.getImageData(0, 0, copy.width, copy.height).data
      const background = [pixels[0], pixels[1], pixels[2]]
      let visiblePixels = 0
      for (let index = 0; index < pixels.length; index += 4) {
        const difference = Math.abs(pixels[index] - background[0])
          + Math.abs(pixels[index + 1] - background[1])
          + Math.abs(pixels[index + 2] - background[2])
        if (difference > 24) visiblePixels += 1
      }
      return { background, visiblePixels }
    }

    renderer.render(scene, camera)
    const direct = sample()
    const pipeline = new PointCloudEdlPipeline(renderer, { enabled: true })
    pipeline.render(scene, camera)
    const edl = sample()
    pipeline.dispose()
    renderer.dispose()
    return { direct, edl }
  }
`

try {
  await waitForChrome()
  const page = await openPage()
  const cdp = connectCdp(page.webSocketDebuggerUrl)
  try {
    await cdp.call('Page.enable')
    await cdp.call('Network.enable')
    await cdp.call('Page.navigate', { url: 'http://127.0.0.1:5173/' })
    await new Promise((resolve) => setTimeout(resolve, 1_000))
    const evaluation = await cdp.call('Runtime.evaluate', {
      expression: `(${pixelProbe})()`,
      awaitPromise: true,
      returnByValue: true,
    })
    if (evaluation.exceptionDetails) {
      throw new Error(evaluation.exceptionDetails.exception?.description || evaluation.exceptionDetails.text)
    }
    const result = evaluation.result.value
    assert.ok(result.direct.visiblePixels > 1_000, 'direct rendering must show the point fixture')
    assert.ok(
      result.edl.visiblePixels > 1_000,
      `EDL rendering must preserve visible points; got ${JSON.stringify(result)}`,
    )
    console.log('EDL fixture rendering preserves visible point-cloud pixels.')

    const token = await developmentSessionToken()
    const cookie = await cdp.call('Network.setCookie', {
      name: 'cloudbim_session',
      value: token,
      url: 'http://127.0.0.1:5173/',
      httpOnly: true,
      sameSite: 'Lax',
    })
    assert.equal(cookie.success, true, 'development session cookie must be accepted')
    await cdp.call('Page.navigate', {
      url: 'http://127.0.0.1:5173/preview/asset?projectId=2&previewType=pointcloud&assetId=5&displayName=YB-1mesh2.0.las',
    })
    const loadDeadline = Date.now() + 30_000
    while (Date.now() < loadDeadline) {
      const body = await cdp.call('Runtime.evaluate', {
        expression: 'document.body.innerText',
        returnByValue: true,
      })
      if (body.result.value?.includes('点云已加载')) break
      await new Promise((resolve) => setTimeout(resolve, 250))
    }
    // The preview renders directly while tiles refine, then enables EDL after
    // the initial load settles. Exercise the settled frame where the regression occurs.
    await new Promise((resolve) => setTimeout(resolve, 8_000))
    const preview = await cdp.call('Runtime.evaluate', {
      expression: `(() => {
        const canvas = document.querySelector('.unified-viewer-viewport canvas')
        const enhancement = [...document.querySelectorAll('button')]
          .find((button) => button.innerText === '显示增强')
        if (!canvas) return { visiblePixels: 0, enhancementEnabled: null, body: document.body.innerText }
        const copy = document.createElement('canvas')
        copy.width = canvas.width
        copy.height = canvas.height
        const context = copy.getContext('2d')
        context.drawImage(canvas, 0, 0)
        const pixels = context.getImageData(0, 0, copy.width, copy.height).data
        const background = [pixels[0], pixels[1], pixels[2]]
        let visiblePixels = 0
        let visibleLuminance = 0
        let darkVisiblePixels = 0
        for (let index = 0; index < pixels.length; index += 4) {
          const difference = Math.abs(pixels[index] - background[0])
            + Math.abs(pixels[index + 1] - background[1])
            + Math.abs(pixels[index + 2] - background[2])
          if (difference > 12) {
            const luminance = 0.2126 * pixels[index]
              + 0.7152 * pixels[index + 1]
              + 0.0722 * pixels[index + 2]
            visiblePixels += 1
            visibleLuminance += luminance
            if (luminance < 32) darkVisiblePixels += 1
          }
        }
        return {
          visiblePixels,
          meanVisibleLuminance: visiblePixels ? visibleLuminance / visiblePixels : 0,
          darkVisibleRatio: visiblePixels ? darkVisiblePixels / visiblePixels : 1,
          background,
          enhancementEnabled: enhancement?.getAttribute('aria-pressed'),
          loaded: document.body.innerText.includes('点云已加载'),
          body: document.body.innerText.slice(0, 500),
          storedEdl: localStorage.getItem('cloudbim.viewer.edlEnabled'),
        }
      })()`,
      returnByValue: true,
    })
    console.log('Point-cloud frame metrics:', JSON.stringify(preview.result.value))
    assert.equal(
      preview.result.value.loaded,
      true,
      `asset 5 must finish loading; got ${JSON.stringify(preview.result.value)}`,
    )
    assert.ok(
      preview.result.value.visiblePixels > 200,
      `point-cloud preview must remain visible when EDL is requested; got ${JSON.stringify(preview.result.value)}`,
    )
    assert.ok(
      preview.result.value.meanVisibleLuminance > 24,
      `point-cloud preview must not render as an all-black mass; got ${JSON.stringify(preview.result.value)}`,
    )
    assert.ok(
      ['true', 'false'].includes(preview.result.value.enhancementEnabled),
      `the enhancement control must reflect the active render path; got ${JSON.stringify(preview.result.value)}`,
    )
    console.log('Point-cloud asset preview remains visible and preserves usable brightness.')

    await cdp.call('Page.reload')
    const reloadDeadline = Date.now() + 30_000
    let reloadVerified = false
    while (Date.now() < reloadDeadline) {
      const state = await cdp.call('Runtime.evaluate', {
        expression: `(() => ({
          loaded: document.body.innerText.includes('点云已加载'),
          enhancementEnabled: [...document.querySelectorAll('button')]
            .find((button) => button.innerText === '显示增强')?.getAttribute('aria-pressed'),
        }))()`,
        returnByValue: true,
      })
      if (state.result.value?.loaded) {
        reloadVerified = true
        break
      }
      await new Promise((resolve) => setTimeout(resolve, 250))
    }
    assert.equal(reloadVerified, true, 'asset 5 must finish loading after reload')
  } finally {
    cdp.close()
  }
} finally {
  chrome.kill('SIGTERM')
  await new Promise((resolve) => chrome.once('exit', resolve))
  for (let attempt = 0; attempt < 20; attempt += 1) {
    try {
      await rm(profileDir, { recursive: true, force: true })
      break
    } catch (error) {
      if (attempt === 19) throw error
      await new Promise((resolve) => setTimeout(resolve, 50))
    }
  }
}
