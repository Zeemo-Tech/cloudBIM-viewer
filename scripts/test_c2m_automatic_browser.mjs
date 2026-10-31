import assert from 'node:assert/strict'
import { readFile, mkdir, writeFile } from 'node:fs/promises'
import { createHmac, createHash } from 'node:crypto'
import { pathToFileURL } from 'node:url'

// Opt-in integration check: reruns denoise and computes one real C2M
// result. It does not delete a saved result to manufacture a missing state.
const verifyOnly = process.env.CLOUDBIM_C2M_AUTO_VERIFY_ONLY === '1'
if (!process.env.CLOUDBIM_C2M_AUTO_E2E && !verifyOnly) {
  console.log('SKIP: set CLOUDBIM_C2M_AUTO_E2E=1 to permit one local denoise/C2M run.')
  process.exit(0)
}
const { chromium } = await import(pathToFileURL(process.env.CLOUDBIM_PLAYWRIGHT_MODULE || '/home/hong/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright/index.mjs'))
const output = process.env.CLOUDBIM_C2M_AUTO_EVIDENCE || '.cloudbim/c2m-automatic-20261029'
await mkdir(output, { recursive: true })
const env = await readFile('.env', 'utf8')
const secret = env.match(/^JWT_SECRET=(.*)$/m)?.[1]?.trim().replace(/^['"]|['"]$/g, '')
assert.ok(secret, 'local development session secret is required; never logged')
const now = Math.floor(Date.now() / 1000), b64 = v => Buffer.from(JSON.stringify(v)).toString('base64url')
const unsigned = `${b64({ alg: 'HS256', typ: 'JWT' })}.${b64({ sub: '1', username: 'demo', iat: now, exp: now + 3600 })}`
const token = `${unsigned}.${createHmac('sha256', secret).update(unsigned).digest('base64url')}`
const browser = await chromium.launch({ headless: true, executablePath: process.env.CLOUDBIM_CHROMIUM || '/usr/bin/google-chrome',
  args: ['--no-sandbox', '--use-gl=angle', '--use-angle=swiftshader', '--enable-unsafe-swiftshader'] })
const page = await browser.newPage({ viewport: { width: 1920, height: 1080 } })
await page.context().addCookies([{ name: 'cloudbim_session', value: token, url: 'http://127.0.0.1:5173', httpOnly: true, sameSite: 'Lax' }])
const base = 'http://127.0.0.1:5173', query = 'modelScanFileId=14&modelBimFileId=13'
const endpoint = name => `${base}/alignments/bim${name}?${query}`
const api = page.context().request
const get = async name => { const r = await api.get(endpoint(name)); assert.ok(r.ok(), `${name}: ${r.status()}`); return (await r.json()).data }
const hash = bytes => createHash('sha256').update(bytes).digest('hex')
const distanceHash = async version => {
  const r = await api.get(`${endpoint('/c2m/distances')}&resultVersion=${version}`)
  assert.ok(r.ok(), `distances: ${r.status()}`)
  return hash(await r.body())
}
const stable = value => Array.isArray(value) ? value.map(stable) : value && typeof value === 'object'
  ? Object.fromEntries(Object.entries(value).filter(([key]) => !['timings', 'timingSeconds', 'instanceMapHash'].includes(key)).map(([key, v]) => [key, stable(v)])) : value
const errors = [], blockedWrites = [], requests = [], timings = []
page.on('pageerror', e => errors.push(e.message))
let injectMissing = false, injectedMissingCount = 0, denoiseFinished = false
const requestStart = new Map()
await page.route('**/*', async route => {
  const request = route.request(), path = new URL(request.url()).pathname
  if (request.method() === 'GET' && path === '/alignments/bim/c2m/latest' && injectMissing) {
    injectMissing = false; injectedMissingCount++
    return route.fulfill({ status: 404, contentType: 'application/json', body: JSON.stringify({ code: 404, msg: 'No comparison result (integration test)', data: null }) })
  }
  if (!['GET', 'HEAD', 'OPTIONS'].includes(request.method())) {
    if (request.method() === 'POST' && ['/alignments/bim/denoise', '/alignments/bim/c2m'].includes(path)) {
      requests.push({ path, body: request.postDataJSON() }); requestStart.set(request, performance.now())
    } else { blockedWrites.push(path); return route.abort() }
  }
  return route.continue()
})
page.on('response', response => {
  const request = response.request(), start = requestStart.get(request)
  if (start === undefined) return
  const path = new URL(request.url()).pathname
  timings.push({ path, httpSeconds: +(performance.now() - start).toFixed(3) / 1000, status: response.status() })
  if (path === '/alignments/bim/denoise' && response.ok()) { denoiseFinished = true; injectMissing = true }
})
try {
  const saved = verifyOnly ? JSON.parse(await readFile(`${output}/before.json`, 'utf8')) : null
  const [alignmentBefore, denoiseBefore, resultBefore] = saved ? [saved.alignment, saved.denoise, saved.result]
    : await Promise.all([get(''), get('/denoise/latest'), get('/c2m/latest')])
  assert.equal(denoiseBefore.fresh, true); assert.equal(resultBefore.fresh, true)
  const distancesBefore = saved?.distancesSha256 ?? await distanceHash(resultBefore.resultVersion)
  await writeFile(`${output}/before.json`, JSON.stringify({ alignment: alignmentBefore, denoise: denoiseBefore, result: resultBefore, distancesSha256: distancesBefore }, null, 2))
  await page.goto(`${base}/alignment?projectId=3&bimAssetId=13&pointcloudAssetId=14&step=2`)
  await page.getByText('偏差结果已就绪', { exact: true }).waitFor({ timeout: 60000 })
  let result
  if (verifyOnly) {
    const recorded = JSON.parse(await readFile(`${output}/failure.json`, 'utf8'))
    assert.deepEqual(recorded.errors, []); assert.deepEqual(recorded.blockedWrites, [])
    requests.push(...recorded.requests); timings.push(...recorded.timings)
    injectedMissingCount = recorded.injectedMissingCount; denoiseFinished = true
    result = await get('/c2m/latest')
  } else {
    assert.equal(requests.length, 0, 'fresh stored comparison is reused on entering denoise step')
    const computed = page.waitForResponse(r => r.request().method() === 'POST' && new URL(r.url()).pathname === '/alignments/bim/c2m', { timeout: 180000 })
    console.log('Fresh result reused; starting real denoise → automatic comparison replay.')
    await page.getByRole('button', { name: '重新分类与去噪', exact: true }).click()
    const response = await computed
    assert.ok(response.ok(), `real comparison response: ${response.status()}`)
    result = (await response.json()).data
    console.log('Automatic comparison returned 200; checking lineage and saved artifacts.')
  }
  assert.equal(denoiseFinished, true); assert.equal(injectedMissingCount, 1)
  await page.getByText('偏差结果已就绪', { exact: true }).waitFor({ timeout: 60000 })
  const [alignmentAfter, denoiseAfter, resultAfter] = await Promise.all([get(''), get('/denoise/latest'), get('/c2m/latest')])
  assert.deepEqual(alignmentAfter, alignmentBefore, 'saved alignment remains unchanged')
  assert.notEqual(denoiseAfter.version, denoiseBefore.version, 'each denoise run publishes a new immutable version')
  assert.equal(resultAfter.diagnostics.rebarComparison.instanceMapHash, denoiseAfter.result.instancesSha256)
  assert.equal(resultAfter.diagnostics.rebarComparison.inspection.provenance.instanceMapHash, denoiseAfter.result.instancesSha256)
  for (const [name, field] of [['instance-map.json', 'instancesSha256'], ['control-net.json', 'controlNetSha256']]) {
    for (const denoise of [denoiseBefore, denoiseAfter]) {
      // The HTTP artifact route deliberately serves only the current version.
      // Older denoise directories remain immutable on the managed local stack.
      const root = process.env.CLOUDBIM_DENOISE_EVIDENCE_ROOT || 'backend/data/assets/760731a0111181486425f189/denoise/13'
      assert.equal(hash(await readFile(`${root}/${denoise.version}/${name}`)), denoise.result[field], `${name} hash matches its version`)
      if (denoise === denoiseAfter) {
        const r = await api.get(`${endpoint(`/denoise/artifacts/${name}`)}&version=${denoise.version}`)
        assert.ok(r.ok(), `current denoise artifact: ${name}`)
        assert.equal(hash(await r.body()), denoise.result[field])
      }
    }
  }
  assert.equal(resultAfter.resultVersion, result.resultVersion)
  assert.equal(resultAfter.fresh, true)
  assert.equal(await distanceHash(resultAfter.resultVersion), distancesBefore, 'full distance binary equals the saved baseline')
  const historical = await api.get(`${base}/alignments/bim/c2m/reports/${resultBefore.resultVersion}/json`)
  assert.ok(historical.ok(), 'original historical report stays accessible')
  assert.deepEqual((await historical.json()).comparison, resultBefore.diagnostics.rebarComparison, 'original report is immutable, including its timings and lineage')
  assert.deepEqual(stable(resultAfter.diagnostics.rebarComparison), stable(resultBefore.diagnostics.rebarComparison), 'all per-bar report data equals baseline, excluding timings and separately verified instance-map lineage')
  assert.deepEqual(requests.map(r => r.path), ['/alignments/bim/denoise', '/alignments/bim/c2m'])
  assert.equal(requests[1].body.denoiseVersion, denoiseAfter.version)
  assert.equal(requests[1].body.downsampleEnabled, false)
  assert.equal(requests[1].body.normalConstraintEnabled, true)
  await page.getByRole('button', { name: '第 3 步：偏差对比', exact: true }).click()
  await page.getByRole('button', { name: '自动巡检', exact: true }).waitFor({ timeout: 60000 })
  await page.waitForFunction(() => !document.querySelector('button[aria-label="自动巡检"]')?.disabled, null, { timeout: 60000 })
  await page.screenshot({ path: `${output}/automatic-comparison-1920.png` })
  await page.reload()
  await page.getByRole('button', { name: '自动巡检', exact: true }).waitFor({ timeout: 60000 })
  await page.waitForFunction(() => !document.querySelector('button[aria-label="自动巡检"]')?.disabled, null, { timeout: 60000 })
  assert.equal(requests.length, 2, 'reload reuses fresh result without another compute')
  assert.deepEqual(blockedWrites, []); assert.deepEqual(errors, [])
  const evidence = { verificationMode: verifyOnly ? 'recorded-run-postconditions' : 'full-replay', input: { scanId: 14, bimId: 13 }, injectedMissingCount,
    injection: 'one latest-result GET after real denoise returns; no stored result is deleted',
    requests, timings, serverTimings: resultAfter.diagnostics.rebarComparison.timings,
    denoiseBefore: denoiseBefore.version, denoiseAfter: denoiseAfter.version,
    beforeVersion: resultBefore.resultVersion, afterVersion: resultAfter.resultVersion, distancesSha256: distancesBefore,
    savedAlignmentUnchanged: true, oldAndNewInstanceAndControlEvidenceHashesVerified: true, fullDistancesAndPerBarReportsEqual: true,
    originalHistoryAccessible: true, freshResultReusedAtEntryAndReload: true, errors, blockedWrites }
  await writeFile(`${output}/after.json`, JSON.stringify({ alignment: alignmentAfter, denoise: denoiseAfter, result: resultAfter }, null, 2))
  await writeFile(`${output}/integration-checks.json`, JSON.stringify(evidence, null, 2))
  console.log(JSON.stringify(evidence, null, 2))
} catch (error) {
  await writeFile(`${output}/failure.json`, JSON.stringify({ error: String(error.stack), requests, timings, errors, blockedWrites, injectedMissingCount }, null, 2))
  await page.screenshot({ path: `${output}/failure.png` }).catch(() => {})
  throw error
} finally { await browser.close() }
