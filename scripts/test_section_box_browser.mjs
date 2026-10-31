import assert from 'node:assert/strict'
import { readFile, mkdir, writeFile } from 'node:fs/promises'
import { createHmac } from 'node:crypto'
import { pathToFileURL } from 'node:url'

// Opt-in local WebGL acceptance. All application writes are blocked in this
// isolated browser; the historical scan, alignment and reports stay untouched.
if (!process.env.CLOUDBIM_SECTION_E2E) {
  console.log('SKIP: set CLOUDBIM_SECTION_E2E=1 with the managed local stack running.')
  process.exit(0)
}
const { chromium } = await import(pathToFileURL(process.env.CLOUDBIM_PLAYWRIGHT_MODULE || '/home/hong/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright/index.mjs'))
const output = process.env.CLOUDBIM_SECTION_EVIDENCE || '.cloudbim/assessment-20261030-viewer-parity/browser'
const surface = process.env.CLOUDBIM_SECTION_SURFACE
assert.ok(!surface || ['alignment', 'bim', 'pointcloud'].includes(surface), 'unknown section test surface')
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
const page = await browser.newPage({ viewport: { width: 1920, height: 1080 }, deviceScaleFactor: Number(process.env.CLOUDBIM_SECTION_DPR || 1) })
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
    window.__gizmoScenes = []; window.__renderedScenes = new Map()
    window.__THREE_DEVTOOLS__.addEventListener('observe', e => { if (e.detail.isScene) window.__gizmoScenes.push(e.detail); if(e.detail.isWebGLRenderer) { const original=e.detail.render.bind(e.detail); e.detail.render=(scene,camera)=>{ window.__renderedScenes.set(scene,camera); return original(scene,camera) } } })
  })
  const frames = () => page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))))
  const install = async () => page.evaluate(() => {
    window.__section = () => {
      const scene = window.__gizmoScenes.find(s => s.children.some(c => c.name === 'Section box'))
      const group = scene?.children.find(c => c.name === 'Section box')
      const camera = window.__renderedScenes.get(scene)
      const canvas = document.querySelector('.viewport > canvas, .unified-viewer-viewport > canvas')
      if (!scene || !group || !camera || !canvas) throw new Error('Missing live section box / camera / canvas')
      return { scene, group, camera, canvas }
    }
    window.__sectionSnapshot = () => {
      const {scene,group,camera}=window.__section(); scene.updateMatrixWorld(true); camera.updateMatrixWorld(true)
      const content=[]; const planes=[]
      for(const root of scene.children) {
        if(root===group || root.isTransformControlsRoot || root.isBox3Helper || root.name.includes('grid')) continue
        root.traverse(o=>{ if(o.isMesh || o.isPoints) { content.push([o.uuid,o.matrixWorld.toArray()]); if(o.material?.clippingPlanes?.length) planes.push(o.material.clippingPlanes.map(p=>[...p.normal.toArray(),p.constant])) } })
      }
      return {rotation:group.quaternion.toArray(),faces:group.children.filter(c=>c.isGroup).map(c=>c.position.toArray()),camera:camera.matrixWorld.toArray(),content,planes,visible:group.visible}
    }
    window.__sectionHit = kind => {
      const {group,camera,canvas}=window.__section(); const rect=canvas.getBoundingClientRect()
      const faceGroups=group.children.filter(c=>c.isGroup)
      const hover=(x,y)=>{
        canvas.dispatchEvent(new PointerEvent('pointermove',{bubbles:true,clientX:x,clientY:y,buttons:0,pointerId:1,pointerType:'mouse',isPrimary:true}))
        const face=faceGroups.findIndex(g=>g.children[0].material.opacity>.05)
        const edge=group.children.some(o=>o.isLine2 && o.material.linewidth===3.2)
        if((kind==='face' && face>=0)||(kind==='edge' && face<0 && edge)) return {x,y,face}
      }
      const candidates=[]
      for(const face of faceGroups) {
        const p=face.position.clone().applyMatrix4(group.matrixWorld).project(camera)
        for(const dx of [0,-15,15,-30,30])for(const dy of [0,-15,15])candidates.push([rect.x+(p.x+1)*rect.width/2+dx,rect.y+(1-p.y)*rect.height/2+dy])
      }
      for(const line of group.children.filter(o=>o.isLine2)) {
        const attr=line.geometry.attributes.instanceStart, end=line.geometry.attributes.instanceEnd
        if(!attr)continue
        for(const t of [.25,.5,.75]) {
          const a=group.position.clone().fromBufferAttribute(attr,0),b=a.clone().fromBufferAttribute(end,0)
          const p=a.lerp(b,t).applyMatrix4(group.matrixWorld).project(camera)
          candidates.push([rect.x+(p.x+1)*rect.width/2,rect.y+(1-p.y)*rect.height/2])
        }
      }
      for(const [x,y]of candidates) { if(x<=rect.x||x>=rect.right||y<=rect.y||y>=rect.bottom)continue; const h=hover(x,y); if(h)return h }
      throw new Error(`No ${kind} hit on live section box`)
    }
  })
  const compareContent=(a,b)=> {const m=new Map(a.content);for(const [id,mat]of b.content)if(m.has(id))assert.deepEqual(mat,m.get(id),'source world matrix changed')}
  if (!surface || surface === 'alignment') {
  await page.goto(process.env.CLOUDBIM_SECTION_URL || 'http://127.0.0.1:5173/alignment?projectId=3&bimAssetId=13&pointcloudAssetId=14&step=1')
  await page.waitForFunction(() => window.__gizmoScenes.some(s => s.children.some(c => c.isTransformControlsRoot && c.controls.object && c.controls.enabled)), null, { timeout: 60000 })
  await page.waitForFunction(() => [...document.querySelectorAll('button')].some(b => b.textContent.trim().startsWith('下一步') && !b.disabled), null, { timeout: 60000 })
  await page.getByRole('button',{name:'重置视角',exact:true}).click()
  const canvasBounds=await page.locator('.viewport > canvas').boundingBox()
  await page.mouse.move(canvasBounds.x+90,canvasBounds.y+320);await page.mouse.down();await page.mouse.move(canvasBounds.x+250,canvasBounds.y+130,{steps:12});await page.mouse.up()
  await page.getByRole('button',{name:'开启剖切',exact:true}).click()
  await frames(); await install()
  await page.screenshot({path:`${output}/initial-section.png`})
  for(const projection of ['perspective','orthographic']) {
    if(projection==='orthographic'){await page.getByRole('button',{name:'当前透视，切换正交',exact:true}).click();await frames()}
    for(const kind of ['face','edge']) {
      const hit=await page.evaluate(k=>window.__sectionHit(k),kind)
      const before=await page.evaluate(()=>window.__sectionSnapshot())
      await page.mouse.move(hit.x,hit.y); await page.mouse.down()
      // On face pickers use the direction toward the projected model centre to shrink.
      const delta=await page.evaluate(h=>{
        const {group,camera,canvas}=window.__section();const r=canvas.getBoundingClientRect()
        const faces=group.children.filter(c=>c.isGroup)
        if(h.face<0)return{x:28,y:19}
        const axis=Math.floor(h.face/2),side=h.face%2
        const a=faces[h.face].position.clone(), b=a.clone();b.setComponent(axis,b.getComponent(axis)+(side?-1:1)*.2)
        a.applyMatrix4(group.matrixWorld).project(camera);b.applyMatrix4(group.matrixWorld).project(camera)
        const x=(b.x-a.x)*r.width,y=-(b.y-a.y)*r.height,n=Math.hypot(x,y)
        return{x:x/n*32,y:y/n*32}
      },hit)
      await page.mouse.move(hit.x+delta.x,hit.y+delta.y,{steps:5});await frames()
      const during=await page.evaluate(()=>window.__sectionSnapshot())
      assert.deepEqual(during.camera,before.camera); compareContent(before,during)
      assert.notDeepEqual(kind==='face'?during.faces:during.rotation,kind==='face'?before.faces:before.rotation)
      assert.ok(during.planes.length>0 && during.planes.every(p=>p.length===6))
      await page.screenshot({path:`${output}/alignment-${projection}-${kind}.png`})
      await page.keyboard.press('Escape');await page.mouse.up();await frames()
      const after=await page.evaluate(()=>window.__sectionSnapshot())
      assert.deepEqual(after.faces,before.faces);assert.deepEqual(after.rotation,before.rotation)
      checks.push({surface:'alignment',projection,kind,cameraUnchanged:true,sourceUnchanged:true,exactRollback:true,clippedMaterials:during.planes.length})
    }
  }
  // Commit a rotation, then restore through the actual reset control.
  const edge=await page.evaluate(()=>window.__sectionHit('edge'))
  await page.mouse.move(edge.x,edge.y);await page.mouse.down();await page.mouse.move(edge.x+26,edge.y+18,{steps:4});await page.mouse.up()
  await page.getByRole('button',{name:'重新贴合模型',exact:true}).click();await frames()
  assert.deepEqual((await page.evaluate(()=>window.__sectionSnapshot())).rotation,[0,0,0,1])
  for(const [width,height]of [[2560,1440],[1280,800]]) {
    await page.setViewportSize({width,height});await frames()
    const layout=await page.evaluate(()=>({overflow:document.documentElement.scrollWidth>innerWidth,feedback:document.querySelector('.section-box-feedback')?.getBoundingClientRect().toJSON(),icon:document.querySelector('.view-toolbar .viewport-tool-glyph')?.getBoundingClientRect().toJSON()}))
    assert.equal(layout.overflow,false);assert.equal(layout.icon.width,18)
    await page.screenshot({path:`${output}/alignment-${width}.png`});checks.push({width,height,...layout})
  }
  await page.getByRole('button',{name:'关闭剖切',exact:true}).click();await frames()
  assert.equal((await page.evaluate(()=>window.__sectionSnapshot())).visible,false)
  }
  for(const [previewType,assetId]of [['bim',13],['pointcloud',14]]) {
    if(surface && surface !== previewType)continue
    await page.setViewportSize({width:1920,height:1080})
    await page.goto(`http://127.0.0.1:5173/preview/asset?projectId=3&assetId=${assetId}&previewType=${previewType}`)
    await page.waitForSelector('.unified-viewer-viewport canvas')
    if(previewType==='bim')await page.getByRole('switch',{name:'启用剖切',exact:true}).locator('..').click()
    else await page.getByRole('button',{name:'剖切',exact:true}).click()
    await page.waitForFunction(()=>window.__gizmoScenes.some(s=>s.children.some(g=>g.name==='Section box'&&g.visible)),null,{timeout:60000})
    await install();await frames()
    const before=await page.evaluate(()=>window.__sectionSnapshot()), hit=await page.evaluate(()=>window.__sectionHit('edge'))
    await page.mouse.move(hit.x,hit.y);await page.mouse.down();await page.mouse.move(hit.x+25,hit.y+18,{steps:5});await frames()
    const during=await page.evaluate(()=>window.__sectionSnapshot())
    assert.notDeepEqual(during.rotation,before.rotation);assert.deepEqual(during.camera,before.camera);compareContent(before,during)
    await page.keyboard.press('Escape');await page.mouse.up();await frames()
    assert.deepEqual((await page.evaluate(()=>window.__sectionSnapshot())).rotation,before.rotation)
    if(previewType==='pointcloud') {
      const layout=await page.evaluate(()=>({status:document.querySelector('.section-box-status').getBoundingClientRect().toJSON(),toolbar:document.querySelector('.pointcloud-viewport-toolbar').getBoundingClientRect().toJSON()}))
      assert.ok(layout.status.top >= layout.toolbar.bottom+7,'section feedback must clear the display toolbar')
      checks.push({surface:previewType,layout})
    }
    await page.screenshot({path:`${output}/${previewType}-preview.png`})
    await page.getByRole('button',{name:'重新贴合模型',exact:true}).click()
    checks.push({surface:previewType,enabled:true,rotation:true,exactRollback:true,clippedMaterials:during.planes.length})
  }
  assert.deepEqual(errors,[]);assert.deepEqual(blockedWrites,[])
  await writeFile(`${output}/evidence${surface?`-${surface}`:''}.json`,JSON.stringify({checks,errors,blockedWrites},null,2))
  console.log(JSON.stringify({passed:checks.length,errors,blockedWrites,output}))
} catch(error) {
  await page.screenshot({path:`${output}/failure.png`}).catch(()=>{})
  await writeFile(`${output}/failure.json`,JSON.stringify({message:String(error),url:page.url(),checks,errors,blockedWrites},null,2))
  throw error
} finally { await browser.close() }
