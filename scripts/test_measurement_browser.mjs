import assert from 'node:assert/strict'
import { readFile, mkdir, writeFile } from 'node:fs/promises'
import { createHmac } from 'node:crypto'
import { pathToFileURL } from 'node:url'
if (!process.env.CLOUDBIM_MEASUREMENT_E2E) {
  console.log('SKIP: set CLOUDBIM_MEASUREMENT_E2E=1 with the managed local stack running.'); process.exit(0)
}
const { chromium } = await import(pathToFileURL(process.env.CLOUDBIM_PLAYWRIGHT_MODULE || '/home/hong/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright/index.mjs'))
const output=process.env.CLOUDBIM_MEASUREMENT_EVIDENCE || '.cloudbim/measurement-20261030/browser'
await mkdir(output,{recursive:true})
const secret=(await readFile('.env','utf8')).match(/^JWT_SECRET=(.*)$/m)?.[1]?.trim().replace(/^['"]|['"]$/g,'')
assert.ok(secret)
const b64=v=>Buffer.from(JSON.stringify(v)).toString('base64url'), now=Math.floor(Date.now()/1000)
const unsigned=`${b64({alg:'HS256',typ:'JWT'})}.${b64({sub:'1',username:'demo',iat:now,exp:now+3600})}`
const token=`${unsigned}.${createHmac('sha256',secret).update(unsigned).digest('base64url')}`
const browser=await chromium.launch({headless:true,executablePath:process.env.CLOUDBIM_CHROMIUM || '/usr/bin/google-chrome',args:['--no-sandbox','--use-gl=angle','--use-angle=swiftshader','--enable-unsafe-swiftshader']})
const page=await browser.newPage({viewport:{width:1920,height:1080},deviceScaleFactor:1.25})
const errors=[],blockedWrites=[],saved=[],checks=[],pending=[]
const check=value=>{checks.push(value);console.log(JSON.stringify(value))}
page.on('pageerror',e=>errors.push(e.message))
page.on('response',response=>{
  if(response.request().method()==='POST' && /\/assets\/\d+\/measurements$/.test(new URL(response.url()).pathname))pending.push((async()=>{
    assert.ok(response.ok(),`measurement HTTP ${response.status()}`)
    const body=await response.json();assert.ok(body.data?.id);saved.push({...body.data,url:response.url()})
  })())
})
const settle=async()=>{await page.evaluate(()=>new Promise(r=>requestAnimationFrame(()=>requestAnimationFrame(r))));await Promise.all(pending)}
const expectSave=async(action)=>{
  const count=saved.length
  const response=page.waitForResponse(r=>r.request().method()==='POST' && /\/assets\/\d+\/measurements$/.test(new URL(r.url()).pathname))
  await action();await response;await settle();assert.equal(saved.length,count+1);return saved.at(-1)
}
const hideBadges=async()=>{while(await page.locator('.measurement-badge__close').count())await page.locator('.measurement-badge__close').first().click();await settle()}
const tool=async value=>{await page.getByLabel('测量方式',{exact:true}).selectOption(value);await settle()}
const inputPoint=async (point,save=true)=>{
  const details=page.locator('.measurement-assist__coordinates')
  if(await details.getAttribute('open') === null)await details.locator('summary').click()
  for(let i=0;i<3;i++)await page.getByRole('spinbutton',{name:`坐标 ${'XYZ'[i]}，米`,exact:true}).fill(String(point[i]))
  if(save)return expectSave(()=>page.getByRole('button',{name:'确认坐标',exact:true}).click())
  await page.getByRole('button',{name:'确认坐标',exact:true}).click();await settle()
}
async function openSurface(surface){
  const url=surface==='alignment'?'http://127.0.0.1:5173/alignment?projectId=3&bimAssetId=13&pointcloudAssetId=14&step=1':`http://127.0.0.1:5173/preview/asset?projectId=3&assetId=${surface==='bim'?13:14}&previewType=${surface}`
  await page.goto(url);await page.waitForSelector('.viewport > canvas, .unified-viewer-viewport > canvas')
  await page.waitForFunction(()=>window.__measureScenes.some(s=>{let n=0;s.traverse(o=>{if((o.isMesh||o.isPoints)&&o.geometry?.attributes.position?.count>10000)n++});return n>0}),null,{timeout:60000})
  if(surface==='alignment')await page.waitForFunction(()=>[...document.querySelectorAll('button')].some(b=>b.textContent.trim().startsWith('下一步')&&!b.disabled),null,{timeout:60000})
  if(await page.getByRole('button',{name:'展开测量工具',exact:true}).count())await page.getByRole('button',{name:'展开测量工具',exact:true}).click()
  await page.getByRole('button',{name:'几何测量',exact:true}).click();await page.waitForSelector('.measurement-assist__panel')
  await page.evaluate(()=>{
    window.__measureLive=()=>{
      const scene=window.__measureScenes.find(s=>{let n=0;s.traverse(o=>{if((o.isMesh||o.isPoints)&&o.geometry?.attributes.position?.count>10000)n++});return n>0})
      return {scene,camera:window.__measureCameras.get(scene),canvas:document.querySelector('.viewport > canvas, .unified-viewer-viewport > canvas')}
    }
  })
  await settle()
}
try{
  await page.context().addCookies([{name:'cloudbim_session',value:token,url:'http://127.0.0.1:5173',httpOnly:true,sameSite:'Lax'}])
  await page.route('**/*',route=>{
    const request=route.request(),path=new URL(request.url()).pathname
    if(!['GET','HEAD','OPTIONS'].includes(request.method()) && !(request.method()==='POST' && /\/assets\/(13|14)\/measurements$/.test(path))){blockedWrites.push(path);return route.abort()}
    return route.continue()
  })
  await page.addInitScript(()=>{
    window.__THREE_DEVTOOLS__=new EventTarget();window.__measureScenes=[];window.__measureCameras=new Map()
    window.__THREE_DEVTOOLS__.addEventListener('observe',e=>{
      if(e.detail.isScene)window.__measureScenes.push(e.detail)
      if(e.detail.isWebGLRenderer){const render=e.detail.render.bind(e.detail);e.detail.render=(s,c)=>{window.__measureCameras.set(s,c);return render(s,c)}}
    })
  })
  for(const surface of ['bim','pointcloud','alignment']){
    await openSurface(surface)
    await tool('pick');const exact=await inputPoint([.13,.27,.41]);assert.equal(exact.payload.measurement.type,'locate')
    check({surface,case:'coordinate-input-saves',sources:exact.payload.measurement.sources.map(s=>s.kind)})
    await tool('angle');await inputPoint([1,0,0],false);await inputPoint([0,0,0],false);const angle=await inputPoint([0,2,0])
    assert.ok(Math.abs(angle.payload.measurement.value-90)<1e-8)
    check({surface,case:'three-point-angle',degrees:angle.payload.measurement.value})
    await page.reload();await page.waitForSelector('.measurement-badge')
    await page.waitForFunction(()=>document.body.innerText.includes('90.00°'))
    await settle()
    assert.equal(await page.locator('.el-message--error').count(),0,await page.locator('.el-message--error').allTextContents())
    check({surface,case:'backend-reload',angleVisible:true})
    await openSurface(surface)
    await tool('pick')
    const canvas=page.locator('.viewport > canvas, .unified-viewer-viewport > canvas'), rect=await canvas.boundingBox()
    const count=saved.length
    await page.mouse.move(rect.x+rect.width*.55,rect.y+rect.height*.45);await page.mouse.down();await page.mouse.move(rect.x+rect.width*.55+50,rect.y+rect.height*.45+25,{steps:8});await page.mouse.up();await settle()
    assert.equal(saved.length,count);check({surface,case:'drag-does-not-measure'})
    await page.screenshot({path:`${output}/${surface}-measurements.png`})
    if(surface!=='pointcloud')continue
    await hideBadges()
    await settle()
    // First validate a real loaded point, then use controlled geometry only in this browser's scene.
    const real=await page.evaluate(async()=>{
      const {pickMeasurementPoint}=await import('/packages/viewer-core/src/viewer/measurement/picking.ts')
      const {defaultMeasurementState}=await import('/packages/viewer-core/src/viewer/measurement/types.ts')
      const {scene,camera,canvas}=window.__measureLive();const r=canvas.getBoundingClientRect()
      let cloud;scene.traverse(o=>{if(!cloud&&o.isPoints&&o.geometry.attributes.position.count>10000)cloud=o})
      if(!cloud)throw Error('No real pointcloud geometry')
      window.__measureCloudParent=cloud.parent;const target={object:cloud,kind:'pointcloud',assetId:14}
      for(let j=0;j<cloud.geometry.attributes.position.count;j+=Math.max(1,Math.floor(cloud.geometry.attributes.position.count/3000))){
        const p=camera.position.clone().fromBufferAttribute(cloud.geometry.attributes.position,j).applyMatrix4(cloud.matrixWorld).project(camera)
        const x=(p.x+1)*r.width/2,y=(1-p.y)*r.height/2
        if(p.z< -1||p.z>1||x<400||x>r.width-120||y<90||y>r.height-160)continue
        const hit=pickMeasurementPoint({camera,width:r.width,height:r.height,targets:[target],contains:()=>true,settings:defaultMeasurementState().settings},x,y)
        if(hit && document.elementFromPoint(r.x+x,r.y+y)===canvas)return{x:r.x+x,y:r.y+y,index:hit.source.vertexIndex}
      }
      throw Error('No unobscured real point candidate')
    })
    await page.getByLabel('取点来源',{exact:true}).selectOption('pointcloud')
    const actual=await expectSave(()=>page.mouse.click(real.x,real.y))
    assert.equal(actual.payload.measurement.sources[0].kind,'pointcloud');assert.ok(Number.isInteger(actual.payload.measurement.sources[0].vertexIndex))
    check({surface,case:'real-point-pick',vertexIndex:actual.payload.measurement.sources[0].vertexIndex,dataLevel:actual.payload.measurement.dataLevel})
    async function fixture(kind){
      return page.evaluate(async kind=>{
        const THREE=await import('/node_modules/three/build/three.module.js')
        const {scene,camera,canvas}=window.__measureLive();const parent=window.__measureCloudParent
        window.__measureFixture?.removeFromParent();parent.updateWorldMatrix(true,false)
        const positions=[]
        if(kind==='cylinder')for(let a=0;a<64;a++)for(let j=0;j<21;j++)positions.push(.15*Math.cos(a*Math.PI/32),-.3+.6*j/20,.15*Math.sin(a*Math.PI/32))
        else if(kind==='corner')for(let i=0;i<=24;i++)for(let j=0;j<=24;j++){const a=.3*i/24,b=.3*j/24;positions.push(a,b,0,a,0,b,0,a,b)}
        else for(let i=-20;i<=20;i++)for(let j=-20;j<=20;j++)positions.push(i*.015,j*.015,0)
        const geometry=new THREE.BufferGeometry().setAttribute('position',new THREE.Float32BufferAttribute(positions,3))
        const object=new THREE.Points(geometry,new THREE.PointsMaterial({color:0x6fbcce,size:4,sizeAttenuation:false}));object.name='Measurement browser fixture'
        const origin=camera.position.clone().addScaledVector(camera.getWorldDirection(new THREE.Vector3()),2)
        const world=new THREE.Matrix4().compose(origin,camera.quaternion,new THREE.Vector3(1,1,1))
        object.matrixAutoUpdate=false;object.matrix.copy(parent.matrixWorld.clone().invert().multiply(world));parent.add(object);object.updateWorldMatrix(true,false)
        window.__measureFixture=object
        const seed=kind==='corner'?new THREE.Vector3(.15,.15,0):kind==='cylinder'?new THREE.Vector3(0,0,.15):new THREE.Vector3()
        const p=seed.applyMatrix4(world).project(camera),r=canvas.getBoundingClientRect()
        return {x:r.x+(p.x+1)*r.width/2,y:r.y+(1-p.y)*r.height/2}
      },kind)
    }
    const radius=page.getByRole('spinbutton',{name:'邻域半径，米',exact:true})
    const tolerance=page.getByRole('spinbutton',{name:'拟合容差，毫米',exact:true})
    await radius.fill('.6');await radius.blur();await tolerance.fill('1');await tolerance.blur()
    for(const kind of ['plane','corner','cylinder']){
      await hideBadges();await tool(kind);const hit=await fixture(kind);await settle();await page.mouse.move(hit.x,hit.y);await settle()
      const result=await expectSave(()=>page.mouse.click(hit.x,hit.y));assert.equal(result.payload.measurement.geometry.kind,kind)
      if(kind==='cylinder')assert.ok(Math.abs(result.payload.measurement.geometry.radius-.15)<.002)
      const lens=await page.locator('.measurement-assist__lens canvas, canvas.measurement-assist__lens, .measurement-assist__feedback canvas').evaluate(c=>{
        const d=c.getContext('2d').getImageData(0,0,c.width,c.height).data;let n=0;for(let i=3;i<d.length;i+=4)if(d[i]>0)n++;return{width:c.width,height:c.height,nonempty:n}
      })
      assert.ok(lens.nonempty>100);check({surface,case:`fixture-${kind}`,rmse:result.payload.measurement.geometry.rmse,radius:result.payload.measurement.geometry.radius,lens})
      await page.screenshot({path:`${output}/pointcloud-${kind}.png`})
      if(kind==='corner'){
        await hideBadges();await tool('frame');await page.getByLabel('捕捉方式',{exact:true}).selectOption('corner')
        const frame=await expectSave(()=>page.mouse.click(hit.x,hit.y))
        const {x,y,z}=frame.payload.measurement.frame
        const dot=(a,b)=>a.reduce((s,v,i)=>s+v*b[i],0)
        assert.ok(Math.abs(dot(x,z))<1e-8&&Math.abs(dot(x,y))<1e-8&&Math.abs(dot(y,z))<1e-8)
        check({surface,case:'corner-local-frame',axes:[x,y,z]})
        await page.getByRole('button',{name:'清除局部坐标系',exact:true}).click()
        await page.getByLabel('捕捉方式',{exact:true}).selectOption('point')
      }
    }
    await page.setViewportSize({width:1280,height:800});await settle()
    const bounds=await page.locator('.measurement-assist__panel').boundingBox();assert.ok(bounds.x>=0&&bounds.y>=0&&bounds.x+bounds.width<=1280&&bounds.y+bounds.height<=800)
    check({surface,case:'narrow-layout',bounds});await page.screenshot({path:`${output}/pointcloud-narrow.png`});await page.setViewportSize({width:1920,height:1080})
    await page.reload();await page.waitForFunction(()=>document.body.innerText.includes('0.3000 m'))
    await page.waitForFunction(()=>window.__measureScenes.some(s=>s.getObjectByName('Saved measurement geometry')?.children.some(o=>o.geometry?.type==='CylinderGeometry')))
    assert.equal(await page.locator('.el-message--error').count(),0)
    check({surface,case:'fitted-cylinder-reload',diameterM:.3,geometryRestored:true})
  }
  assert.deepEqual(errors,[]);assert.deepEqual(blockedWrites,[])
}catch(error){
  await page.screenshot({path:`${output}/failure.png`}).catch(()=>{})
  await writeFile(`${output}/failure.json`,JSON.stringify({error:String(error),url:page.url(),errors,blockedWrites,checks},null,2));throw error
}finally{
  await Promise.allSettled(pending)
  const cleanup=[]
  for(const record of saved){const u=new URL(record.url);u.pathname=`/measurements/${record.id}`;const response=await page.request.delete(u.toString());cleanup.push({id:record.id,status:response.status()})}
  await writeFile(`${output}/evidence.json`,JSON.stringify({checks,errors,blockedWrites,saved:saved.map(r=>({id:r.id,kind:r.kind,payload:r.payload})),cleanup},null,2))
  await browser.close();assert.ok(cleanup.every(r=>r.status>=200&&r.status<300),'test measurement cleanup')
}
