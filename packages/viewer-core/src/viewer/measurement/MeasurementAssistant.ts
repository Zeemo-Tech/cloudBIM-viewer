import * as THREE from 'three'
import { fitLocalPlane, fitEdgeOrCorner, fitCylinder } from './geometry'
import { measurementPresentation } from './evidence'
import { pickMeasurementPoint, sampleMeasurementNeighbourhood, planeAtMeshHit, worldUnitsPerPixel, type MeasurementTarget, type PickedMeasurementPoint, type PickContext } from './picking'
import { defaultMeasurementState, type XYZ, type MeasurementAssistState, type MeasurementSettings, type MeasurementTool,
  type MeasurementEvidence, type MeasurementSource, type PlaneGeometry, type CylinderGeometry, type FittedGeometry, type MeasurementFrame } from './types'

type PointRecord = { id?: string; x: number; y: number; z: number; measurement?: MeasurementEvidence }
type DistanceRecord = { id?: string; start: PointRecord; end: PointRecord; distance: number; heightDifference: number; measurement?: MeasurementEvidence }
type RecordValue = PointRecord | DistanceRecord
interface Options {
  element: () => HTMLElement | null
  camera: () => THREE.Camera | null
  scene: () => THREE.Scene | null
  orbit: () => { target: THREE.Vector3; update: () => void } | null
  targets: () => MeasurementTarget[]
  contains: (p: THREE.Vector3) => boolean
  references: () => PointRecord[]
  anchor: () => THREE.Vector3 | null
  onState: (state: MeasurementAssistState) => void
  onRecord: (kind: 'point' | 'distance', record: RecordValue) => void
  onCoordinates: (point: THREE.Vector3) => void
  onUndo: () => void
  requestRender: () => void
  contextKey?: () => string
}
const vector = (p: XYZ) => new THREE.Vector3(...p)
const tuple = (p: THREE.Vector3) => p.toArray() as XYZ
const key = (p: THREE.Vector3) => p.toArray().map(v => v.toFixed(8)).join(',')
const clone = <T>(value: T): T => JSON.parse(JSON.stringify(value))
const uuid = () => globalThis.crypto?.randomUUID?.() ?? `measurement-${Date.now()}-${Math.random().toString(16).slice(2)}`
function dispose(group: THREE.Group) {
  group.traverse(o => {
    const mesh = o as THREE.Mesh
    mesh.geometry?.dispose()
    if (Array.isArray(mesh.material)) mesh.material.forEach(m => m.dispose()); else mesh.material?.dispose()
  })
  group.clear()
}

/** Owns the measurement session, never the model pose or registration. */
export class MeasurementAssistant {
  state = defaultMeasurementState()
  private group = new THREE.Group()
  private saved = new THREE.Group()
  private lastSaved = ''
  private raw: PickedMeasurementPoint | null = null
  private picked: PickedMeasurementPoint[] = []
  private sourceByPoint = new Map<string, MeasurementSource>()
  private plane: PlaneGeometry | null = null
  private cylinder: CylinderGeometry | null = null
  private frame: MeasurementFrame | null = null
  private lastHover = 0
  private mode = 'none'
  private view: { position: THREE.Vector3; up: THREE.Vector3; target: THREE.Vector3; zoom: number } | null = null
  constructor(private options: Options) {
    this.group.name = 'Measurement assistance'
    this.saved.name = 'Saved measurement geometry'
    this.group.userData.measurementHelper = this.saved.userData.measurementHelper = true
  }
  private publish() {
    this.state.hasPlane = !!this.plane; this.state.hasFrame = !!this.frame
    this.state.pickedCount = this.picked.length
    this.state.canUndo = this.picked.length > 0 || !!this.options.anchor()
    this.state.canRestoreView = !!this.view
    this.options.onState({ ...this.state, settings: { ...this.state.settings } })
    this.options.requestRender()
  }
  refresh() { this.publish() }
  setMode(mode: string) {
    if (this.mode === mode) return
    this.mode = mode; this.state.active = mode !== 'none'; this.state.advanced = mode === 'geometry'
    this.reset(); this.state.hover = null; this.sourceByPoint.clear(); this.publish()
  }
  setSettings(patch: Partial<MeasurementSettings>) {
    Object.assign(this.state.settings, patch)
    this.state.settings.radius = THREE.MathUtils.clamp(Number(this.state.settings.radius) || .15, .005, 10)
    this.state.settings.tolerance = THREE.MathUtils.clamp(Number(this.state.settings.tolerance) || .005, .00005, this.state.settings.radius * .2)
    this.state.preview = null; this.raw = null; dispose(this.group)
    if (patch.sourceFilter !== undefined) { this.picked = []; this.plane = null; this.cylinder = null }
    this.state.error = ''; this.publish()
  }
  setTool(tool: MeasurementTool) { this.state.tool = tool; this.reset() }
  reset() {
    this.picked = []; this.plane = null; this.cylinder = null; this.raw = null
    this.state.preview = null; this.state.error = ''; this.state.hover = null
    this.state.status = this.instruction(); dispose(this.group); this.publish()
  }
  private instruction() {
    if (!this.state.advanced) return '点击确认 · 拖动调整视角 · Alt 暂停吸附 · Esc 退出'
    const text: Record<MeasurementTool, string> = {
      pick: '点击保存坐标', plane: '点击表面提取并保存局部平面', 'point-plane': '先点击参考平面，再点击待测点，可连续测量',
      'plane-plane': '依次点击两张近似平行的平面', edge: '靠近两面交线，预览后点击保存', corner: '靠近三面交会处，预览后点击保存',
      angle: '依次点击起点、顶点、终点', 'plane-angle': '依次点击两张平面，测量较小夹角',
      'line-angle': '依次点击第一条线两端、第二条线两端，测量较小夹角',
      cylinder: '点击圆柱表面，调整邻域覆盖足够圆弧和长度', frame: '点击平面建立局部坐标系；角点吸附可作为原点',
      'cylinder-distance': '依次点击两根圆柱，测量无限轴线的最短距离',
    }
    return text[this.state.tool]
  }
  undo() {
    if (!this.state.advanced) this.options.onUndo()
    else {
      this.picked.pop()
      if (!this.picked.length) { this.plane = null; this.cylinder = null }
      this.state.status = this.instruction(); this.state.error = ''; dispose(this.group)
      if (this.plane) this.draw(this.plane)
      this.drawSelection()
    }
    this.publish()
  }
  clearFrame() { this.frame = null; this.state.status = '已恢复世界坐标轴'; this.publish() }
  flip() {
    if (!this.plane) return
    this.plane.normal = tuple(vector(this.plane.normal).negate())
    if (this.state.preview?.kind === 'plane') this.state.preview = clone(this.plane)
    dispose(this.group); this.draw(this.plane); this.publish()
  }
  faceOn() {
    const camera = this.options.camera(), orbit = this.options.orbit()
    if (!camera || !orbit || !this.plane) return
    if (!this.view) this.view = { position: camera.position.clone(), up: camera.up.clone(), target: orbit.target.clone(), zoom: (camera as THREE.PerspectiveCamera).zoom }
    const distance = Math.max(camera.position.distanceTo(orbit.target), this.plane.radius * 3)
    const normal = vector(this.plane.normal)
    // Keep Y-up through OrbitControls. Avoid an exact pole singularity.
    if (Math.abs(normal.y) > .999999) normal.z += 1e-5
    orbit.target.copy(vector(this.plane.origin)); camera.position.copy(orbit.target).addScaledVector(normal.normalize(), distance)
    camera.up.set(0, 1, 0); camera.lookAt(orbit.target); orbit.update(); this.publish()
  }
  restoreView() {
    const camera = this.options.camera(), orbit = this.options.orbit()
    if (!camera || !orbit || !this.view) return
    camera.position.copy(this.view.position); camera.up.copy(this.view.up); orbit.target.copy(this.view.target)
    if ('zoom' in camera) { (camera as THREE.PerspectiveCamera).zoom = this.view.zoom; (camera as THREE.PerspectiveCamera).updateProjectionMatrix() }
    orbit.update(); this.view = null; this.publish()
  }
  private context(): PickContext | null {
    const element = this.options.element(), camera = this.options.camera()
    if (!element || !camera) return null
    const rect = element.getBoundingClientRect()
    return { camera, width: rect.width, height: rect.height, targets: this.options.targets(), contains: this.options.contains, settings: this.state.settings }
  }
  private fit(picked: PickedMeasurementPoint, tool: MeasurementTool): FittedGeometry | null {
    const context = this.context()
    if (!context) return null
    const options = { radius: this.state.settings.radius, tolerance: this.state.settings.tolerance,
      facing: tuple(context.camera.position.clone().sub(picked.point)) }
    if (['plane', 'point-plane', 'plane-plane', 'plane-angle', 'frame'].includes(tool)) {
      const face = planeAtMeshHit(picked, options.radius)
      if (face) { if (vector(face.normal).dot(vector(options.facing)) < 0) face.normal = tuple(vector(face.normal).negate()); return face }
    }
    const sample = sampleMeasurementNeighbourhood(context, picked, options.radius)
    if (sample.error) { this.state.error = sample.error; return null }
    const result = tool === 'edge' || tool === 'corner' ? fitEdgeOrCorner(sample.points, tuple(picked.point), options, tool)
      : tool === 'cylinder' || tool === 'cylinder-distance' ? fitCylinder(sample.points, tuple(picked.point), options)
        : fitLocalPlane(sample.points, tuple(picked.point), options)
    if (!result.ok) { this.state.error = result.reason; return null }
    return result.value
  }
  pick(event: Pick<PointerEvent, 'clientX' | 'clientY' | 'altKey'>): THREE.Vector3 | null {
    if (!this.state.active) return null
    const context = this.context(), element = this.options.element()
    if (!context || !element) return null
    const rect = element.getBoundingClientRect(), x = event.clientX - rect.left, y = event.clientY - rect.top
    this.state.error = ''
    const picked = pickMeasurementPoint(context, x, y)
    this.raw = picked
    if (!picked) { this.state.hover = null; this.state.preview = null; dispose(this.group); if (this.plane) this.draw(this.plane); this.publish(); return null }
    let point = picked.point.clone(), label = picked.label
    const tool = this.state.advanced ? this.state.tool : 'pick'
    let fitTool: MeasurementTool | null = null
    if (!event.altKey && (this.state.settings.snap === 'edge' || this.state.settings.snap === 'corner')) fitTool = this.state.settings.snap
    if (this.state.advanced && !['pick', 'angle', 'line-angle'].includes(tool) && !(tool === 'point-plane' && this.plane)
      && !(tool === 'frame' && fitTool === 'corner')) fitTool = tool
    if (fitTool) {
      const geometry = this.fit(picked, fitTool)
      this.state.preview = geometry
      if (geometry?.kind === 'edge' || geometry?.kind === 'corner') { point = vector(geometry.position); label = geometry.kind === 'edge' ? '拟合边缘点' : '拟合角点' }
      else if (geometry?.kind === 'plane') label = geometry.algorithm === 'mesh-face-normal-v1' ? '模型面法向' : '拟合平面'
      else if (geometry?.kind === 'cylinder') label = '拟合圆柱'
      if (!geometry && (tool === 'edge' || tool === 'corner' || this.state.settings.snap === 'edge' || this.state.settings.snap === 'corner')) {
        this.raw = null; this.state.hover = null; dispose(this.group); this.publish(); return null
      }
    } else this.state.preview = null
    if (!fitTool && !event.altKey) {
      const candidates = this.options.references().map(p => ({ point: new THREE.Vector3(p.x, p.y, p.z), label: '已有测量点', source: p.measurement?.sources[0] }))
      if (this.state.settings.snap === 'vertex' && picked.hit.face && (picked.object as THREE.Mesh).isMesh) {
        const vertices = [picked.hit.face.a, picked.hit.face.b, picked.hit.face.c].map(i => new THREE.Vector3().fromBufferAttribute(picked.object.geometry.getAttribute('position'), i).applyMatrix4(picked.object.matrixWorld))
        const modelSource = (position: THREE.Vector3, vertexIndex?: number): MeasurementSource => ({ ...clone(picked.source), vertexIndex,
          localPoint: tuple(position.clone().applyMatrix4(picked.object.matrixWorld.clone().invert())) })
        const indices = [picked.hit.face.a, picked.hit.face.b, picked.hit.face.c]
        for (let i = 0; i < 3; i++) {
          const midpoint = vertices[i]!.clone().lerp(vertices[(i + 1) % 3]!, .5)
          candidates.push({ point: vertices[i]!, label: '模型顶点', source: modelSource(vertices[i]!,indices[i]) })
          candidates.push({ point: midpoint, label: '模型边中点', source: modelSource(midpoint) })
        }
      }
      let best = 18
      const surfacePoint = point.clone()
      for (const candidate of candidates) {
        if (!context.contains(candidate.point) || (context.settings.sourceFilter !== 'all' && candidate.source?.kind !== context.settings.sourceFilter)) continue
        // A hidden/back-surface endpoint must not steal a foreground click.
        if (candidate.point.distanceTo(surfacePoint) > worldUnitsPerPixel(context.camera, surfacePoint, context.height) * 24) continue
        const screen = candidate.point.clone().project(context.camera)
        if (screen.z < -1 || screen.z > 1) continue
        const distance = Math.hypot((screen.x + 1) * rect.width / 2 - x, (1 - screen.y) * rect.height / 2 - y)
        if (distance <= best) {
          best = distance; point = candidate.point.clone(); label = candidate.label
          if (candidate.source) picked.source = clone(candidate.source)
        }
      }
    }
    const anchor = this.options.anchor() ?? (this.picked[0]?.point ?? null)
    if (!event.altKey && this.state.settings.axis !== 'free' && anchor && (!this.state.advanced || ['pick','angle','line-angle'].includes(tool))) {
      const axisName = this.state.settings.axis
      const axis = this.frame ? vector(this.frame[axisName]) : new THREE.Vector3(axisName === 'x' ? 1 : 0, axisName === 'y' ? 1 : 0, axisName === 'z' ? 1 : 0)
      point = anchor.clone().addScaledVector(axis, point.clone().sub(anchor).dot(axis)); label = `${axisName.toUpperCase()} 轴约束点`
    }
    picked.point = point
    this.raw = picked
    this.sourceByPoint.set(key(point), clone(picked.source))
    const displayPoint = this.frame ? this.toLocal(point) : tuple(point)
    this.state.hover = { x, y, position: displayPoint, label, sourceLabel: this.frame ? '局部坐标 · m' : '世界坐标 · Y 向上 · m' }
    dispose(this.group)
    if (this.plane) this.draw(this.plane)
    if (this.cylinder) this.draw(this.cylinder)
    if (this.state.preview) this.draw(this.state.preview)
    this.drawPoint(point, 0x73a2f3)
    this.drawSelection(point)
    if (tool === 'point-plane' && this.plane) this.drawLine(point, this.projectToPlane(point, this.plane).foot)
    this.publish()
    return point
  }
  hover(event: PointerEvent) {
    if (event.buttons || !this.state.active) return
    const now = performance.now()
    if (now - this.lastHover < (this.state.tool.startsWith('cylinder') ? 240 : 90)) return null
    this.lastHover = now; return this.pick(event)
  }
  leave() { this.state.hover = null; this.publish() }
  private toLocal(point: THREE.Vector3): XYZ {
    if (!this.frame) return tuple(point)
    const delta = point.clone().sub(vector(this.frame.origin))
    return [delta.dot(vector(this.frame.x)), delta.dot(vector(this.frame.y)), delta.dot(vector(this.frame.z))]
  }
  coordinates(payload: { point: XYZ; relative: boolean }) {
    if (!payload.point.every(Number.isFinite)) return
    let point = vector(payload.point)
    if (this.frame) point = vector(this.frame.x).multiplyScalar(payload.point[0]).addScaledVector(vector(this.frame.y), payload.point[1]).addScaledVector(vector(this.frame.z), payload.point[2])
    if (payload.relative) {
      const anchor = this.options.anchor() ?? this.picked.at(-1)?.point ?? (this.frame ? vector(this.frame.origin) : null)
      if (!anchor) { this.state.error = '先选一个基准点，再输入增量'; this.publish(); return }
      point.add(anchor)
    } else if (this.frame) point.add(vector(this.frame.origin))
    const source: MeasurementSource = { kind: 'construction', objectName: '坐标输入', localPoint: tuple(point), worldMatrix: new THREE.Matrix4().toArray(), representation: 'explicit-coordinate' }
    this.sourceByPoint.set(key(point), source)
    if (!this.state.advanced) this.options.onCoordinates(point)
    else if (['pick','angle','line-angle'].includes(this.state.tool)) {
      this.raw = { point, source, object: null as unknown as THREE.Mesh, hit: null as unknown as THREE.Intersection, label: '坐标输入' }
      this.commitCurrent()
    } else this.state.error = '几何拟合需要点击表面；坐标输入适用于定位、测距和三点角度'
    this.publish()
  }
  private evidence(type: MeasurementEvidence['type'], anchors: THREE.Vector3[], geometry?: FittedGeometry): MeasurementEvidence {
    const sources = anchors.map(p => this.sourceByPoint.get(key(p))).filter((p): p is MeasurementSource => !!p).map(clone)
    return { version: 1, type, sources, anchors: anchors.map(tuple), geometry: geometry ? clone(geometry) : undefined,
      frame: this.frame ? clone(this.frame) : undefined, settings: { ...this.state.settings }, capturedAt: new Date().toISOString(), contextKey: this.options.contextKey?.(),
      dataLevel: sources.some(s => s.kind === 'pointcloud') ? '点云显示采样快照' : sources.some(s => s.kind === 'construction') ? '构造坐标' : '模型表面' }
  }
  annotate<T extends object>(record: T, type: 'locate' | 'distance' | 'area'): T {
    const r = record as Record<string, any>
    const points = type === 'locate' ? [r] : type === 'distance' ? [r.start, r.end] : r.points
    return { ...record, measurement: this.evidence(type, points.map((p: PointRecord) => new THREE.Vector3(p.x, p.y, p.z)), this.state.preview ?? undefined) }
  }
  commit(event: PointerEvent) {
    this.pick(event)
    if (!this.state.advanced) return false
    this.commitCurrent(); return true
  }
  private commitCurrent() {
    const raw = this.raw
    if (!raw) { this.state.error ||= '此处没有可靠候选，请重新选择表面'; this.publish(); return }
    const point = raw.point.clone(), tool = this.state.tool
    const g = this.state.preview
    if (tool === 'angle' || tool === 'line-angle') {
      this.picked.push({ ...raw, point })
      if (this.picked.length === (tool === 'angle' ? 3 : 4)) {
        const [a,b,c,d] = this.picked.map(p => p.point)
        const u = a!.clone().sub(b!), v = tool === 'angle' ? c!.clone().sub(b!) : c!.clone().sub(d!)
        if (u.length() < 1e-7 || v.length() < 1e-7) { this.picked.pop(); this.state.error = '角度的两条边不能为零长度'; this.publish(); return }
        const evidence = this.evidence(tool, this.picked.map(p => p.point)); evidence.value = THREE.MathUtils.radToDeg(u.angleTo(v)); evidence.unit = 'deg'
        if (tool === 'line-angle') evidence.value = Math.min(evidence.value, 180-evidence.value)
        this.savePoint(b!, evidence); this.picked = []
      } else this.state.status = tool === 'line-angle' ? `已选 ${this.picked.length} / 4 点，请继续点选` : this.picked.length === 1 ? '请点击角度顶点' : '请点击角度终点'
    } else if (tool === 'cylinder-distance') {
      if (!g || g.kind !== 'cylinder') { this.state.error ||= '无法得到可靠圆柱，请调整邻域'; this.publish(); return }
      if (!this.cylinder) { this.cylinder = clone(g); this.picked = [{...raw,point}]; this.state.status = '已选第一根圆柱，请点击第二根圆柱' }
      else {
        const c1=vector(this.cylinder.center), c2=vector(g.center), u=vector(this.cylinder.axis), v=vector(g.axis), w=c2.clone().sub(c1)
        const b=u.dot(v), denominator=1-b*b
        const start=denominator < 1e-6 ? c1.clone().addScaledVector(u,w.dot(u)) : c1.clone().addScaledVector(u,(w.dot(u)-b*w.dot(v))/denominator)
        const end=denominator < 1e-6 ? c2 : c2.clone().addScaledVector(v,(b*w.dot(u)-w.dot(v))/denominator)
        const evidence=this.evidence(tool,[this.picked[0]!.point,point],g)
        evidence.referenceCylinder=clone(this.cylinder); evidence.value=start.distanceTo(end); evidence.unit='m'
        this.saveDistance(start,end,evidence); this.cylinder=null; this.picked=[]; this.state.status='圆柱轴距已记录，可继续测量'
      }
    } else if (tool === 'point-plane' && this.plane) {
      const { signed, foot } = this.projectToPlane(point, this.plane)
      const evidence = this.evidence(tool, [this.picked[0]!.point, point], this.plane)
      evidence.referencePlane = clone(this.plane); evidence.value = signed; evidence.unit = 'm'
      this.saveDistance(point, foot, evidence); this.state.status = '参考面已锁定，继续点击待测点；重选参考面请点重新开始'
    } else if (['point-plane','plane-plane','plane-angle'].includes(tool)) {
      if (!g || g.kind !== 'plane') { this.state.error ||= '无法得到可靠平面，请调整邻域或点击另一位置'; this.publish(); return }
      if (!this.plane) { this.plane = clone(g); this.picked = [{ ...raw, point }]; this.state.status = tool === 'point-plane' ? '参考面已锁定，请点击待测点' : '已选第一平面，请点击第二平面' }
      else {
        const n1 = vector(this.plane.normal), n2 = vector(g.normal)
        const angle = THREE.MathUtils.radToDeg(Math.acos(THREE.MathUtils.clamp(Math.abs(n1.dot(n2)), 0, 1)))
        const evidence = this.evidence(tool, [this.picked[0]!.point, point], g); evidence.referencePlane = clone(this.plane)
        if (tool === 'plane-angle') { evidence.value = angle; evidence.unit = 'deg'; this.savePoint(point, evidence) }
        else {
          if (angle > 5) { this.state.error = `两平面夹角 ${angle.toFixed(1)}°，请使用面面夹角或点到面测量`; this.publish(); return }
          const origin = vector(this.plane.origin)
          const distance = vector(g.origin).sub(origin).dot(n2) / n1.dot(n2)
          evidence.value = distance; evidence.unit = 'm'; this.saveDistance(origin, origin.clone().addScaledVector(n1, distance), evidence)
        }
        this.picked = []; this.plane = null
      }
    } else if (tool === 'pick') this.savePoint(point, this.evidence('locate', [point]))
    else {
      if (!g) { this.state.error ||= '无法拟合，请调整搜索半径或容差'; this.publish(); return }
      if (g.kind === 'plane') this.plane = clone(g)
      if (tool === 'frame') {
        const plane = g.kind === 'plane' ? g : g.kind === 'corner' ? g.planes[0] : null
        if (!plane) { this.state.error = '请点击平面或可靠角点'; this.publish(); return }
        this.plane = clone(plane)
        const z = vector(plane.normal)
        const x = g.kind === 'corner'
          ? z.clone().cross(vector(g.planes[1]!.normal)).normalize()
          : new THREE.Vector3(Math.abs(z.x) < .8 ? 1 : 0, Math.abs(z.x) < .8 ? 0 : 1, 0).projectOnPlane(z).normalize()
        if (x.lengthSq() < .9) { this.state.error = '角点无法形成稳定的局部坐标轴'; this.publish(); return }
        const y = z.clone().cross(x).normalize()
        this.frame = { origin: tuple(point), x: tuple(x), y: tuple(y), z: tuple(z) }
      }
      const evidence = this.evidence(tool, [point], g)
      this.savePoint(g.kind === 'cylinder' ? vector(g.center) : point, evidence)
    }
    this.state.canUndo = this.picked.length > 0; this.publish()
  }
  private savePoint(point: THREE.Vector3, measurement: MeasurementEvidence) {
    this.options.onRecord('point', { id: uuid(), x: point.x, y: point.y, z: point.z, measurement })
    this.state.status = `${measurementPresentation(measurement)?.title ?? '测量'}已记录，可继续点选`
  }
  private saveDistance(start: THREE.Vector3, end: THREE.Vector3, measurement: MeasurementEvidence) {
    this.options.onRecord('distance', { id: uuid(), start: { x:start.x,y:start.y,z:start.z }, end: {x:end.x,y:end.y,z:end.z},
      distance: start.distanceTo(end), heightDifference: Math.abs(end.y-start.y), measurement })
  }
  private projectToPlane(point: THREE.Vector3, plane: PlaneGeometry) {
    const n = vector(plane.normal), signed = point.clone().sub(vector(plane.origin)).dot(n)
    return { signed, foot: point.clone().addScaledVector(n, -signed) }
  }
  private ensureGroups() {
    const scene = this.options.scene()
    if (scene && this.group.parent !== scene) scene.add(this.group)
    if (scene && this.saved.parent !== scene) scene.add(this.saved)
  }
  private drawPoint(point: THREE.Vector3, color: number, group = this.group) {
    this.ensureGroups()
    const camera = this.options.camera(), height = this.options.element()?.clientHeight ?? 600
    const radius = camera ? worldUnitsPerPixel(camera, point, height) * 4 : .005
    const marker = new THREE.Mesh(new THREE.SphereGeometry(Math.max(radius, .0001), 8, 6), new THREE.MeshBasicMaterial({ color, depthTest: false, depthWrite: false }))
    marker.position.copy(point); marker.renderOrder = 10004; group.add(marker)
  }
  private drawLine(a: THREE.Vector3, b: THREE.Vector3, group = this.group) {
    const line = new THREE.Line(new THREE.BufferGeometry().setFromPoints([a,b]), new THREE.LineBasicMaterial({color: 0x6fbcce, depthTest: false, depthWrite: false}))
    line.renderOrder = 10003; group.add(line)
  }
  private drawSelection(hover?: THREE.Vector3) {
    if (!['angle','line-angle'].includes(this.state.tool)) return
    const points = this.picked.map(p => p.point)
    points.forEach(p => this.drawPoint(p, 0xffc070))
    if (hover) points.push(hover)
    for (let i=1;i<points.length;i++) {
      if (this.state.tool === 'line-angle' && i % 2 === 0) continue
      this.drawLine(points[i-1]!,points[i]!)
    }
  }
  private draw(g: FittedGeometry, group = this.group) {
    this.ensureGroups()
    if ('support' in g && g.support.length) {
      const support = new THREE.Points(new THREE.BufferGeometry().setFromPoints(g.support.map(vector)),
        new THREE.PointsMaterial({ color: 0x80d2ca, size: 3, sizeAttenuation: false, depthWrite: false }))
      group.add(support)
    }
    if (g.kind === 'plane') {
      const disc = new THREE.Mesh(new THREE.CircleGeometry(g.radius, 48), new THREE.MeshBasicMaterial({ color: 0x4e66cc, transparent: true, opacity: .16, side: THREE.DoubleSide, depthWrite: false }))
      disc.position.copy(vector(g.origin)); disc.quaternion.setFromUnitVectors(new THREE.Vector3(0,0,1),vector(g.normal)); group.add(disc)
      const arrow = new THREE.ArrowHelper(vector(g.normal), vector(g.origin), g.radius * 1.25, 0x73a2f3, g.radius *.2, g.radius*.1)
      group.add(arrow)
    } else if (g.kind === 'cylinder') {
      const cylinder = new THREE.Mesh(new THREE.CylinderGeometry(g.radius,g.radius,g.length,32,1,true), new THREE.MeshBasicMaterial({color:0x6fbcce,wireframe:true,transparent:true,opacity:.45,depthWrite:false}))
      cylinder.position.copy(vector(g.center)); cylinder.quaternion.setFromUnitVectors(new THREE.Vector3(0,1,0),vector(g.axis)); group.add(cylinder)
      this.drawLine(vector(g.center).addScaledVector(vector(g.axis),-g.length/2),vector(g.center).addScaledVector(vector(g.axis),g.length/2),group)
    } else {
      g.planes.forEach(p => this.draw(p,group)); this.drawPoint(vector(g.position),g.kind==='corner'?0xb99df1:0x73a2f3,group)
      if (g.direction) this.drawLine(vector(g.position).addScaledVector(vector(g.direction),-.15),vector(g.position).addScaledVector(vector(g.direction),.15),group)
    }
  }
  syncSaved(records: Array<{ id?: string; measurement?: MeasurementEvidence }>) {
    const evidence = records.filter(r => r.measurement && !['distance','locate','area'].includes(r.measurement.type))
    const signature = evidence.map(r => `${r.id}:${JSON.stringify(r.measurement?.anchors)}`).join(';')
    if (signature === this.lastSaved) return
    this.lastSaved = signature; dispose(this.saved); this.ensureGroups()
    for (const record of evidence) {
      const e = record.measurement!
      if (e.referencePlane) this.draw(e.referencePlane,this.saved)
      if (e.referenceCylinder) this.draw(e.referenceCylinder,this.saved)
      if (e.geometry && e.type !== 'point-plane') this.draw(e.geometry,this.saved)
      if (e.type === 'angle') { for (let i=1;i<e.anchors.length;i++) this.drawLine(vector(e.anchors[i-1]!),vector(e.anchors[i]!),this.saved) }
      if (e.type === 'line-angle') { this.drawLine(vector(e.anchors[0]!),vector(e.anchors[1]!),this.saved); this.drawLine(vector(e.anchors[2]!),vector(e.anchors[3]!),this.saved) }
      if (e.frame && e.type === 'frame') for (const [axis,color] of [[e.frame.x,0xe07179],[e.frame.y,0x6fbcce],[e.frame.z,0x73a2f3]] as const) {
        this.saved.add(new THREE.ArrowHelper(vector(axis),vector(e.frame.origin), e.settings.radius*1.5,color))
      }
    }
  }
  dispose() { dispose(this.group); dispose(this.saved); this.group.removeFromParent(); this.saved.removeFromParent(); this.sourceByPoint.clear(); this.view = null; this.lastSaved = ''; this.frame = null; this.reset() }
}
