import { BufferAttribute, Color, type BufferGeometry } from 'three'
import { PLYLoader } from 'three/examples/jsm/loaders/PLYLoader.js'
import { buildInstancePalette } from '../../features/rebar-visualization/instancePalette.js'

export const DENOISE_CLASSES = [
  { id: 3, key: 'steel', name: '钢筋', color: '#2dd4bf' },
  { id: 2, key: 'fixture', name: '夹具', color: '#f59e0b' },
  { id: 1, key: 'table', name: '台面残留', color: '#64748b' },
  { id: 4, key: 'noise', name: '噪声', color: '#ef476f' },
  { id: 0, key: 'unknown', name: '未分类', color: '#94a3b8' },
] as const

export type DenoiseColorMode = 'classes' | 'cleaned'
const previewColors = new WeakMap<BufferGeometry, Partial<Record<DenoiseColorMode, Float32Array>>>()

/** Existing production PLYs contain points/IDs but no centerline manifest.
 * Fit a display-only axis per whole instance so cached results also benefit
 * from spatial coloring. This does not change point positions or ownership.
 */
function previewInstancePalette(geometry: BufferGeometry) {
  const positions = geometry.getAttribute('position')
  const labels = geometry.getAttribute('label')
  const ids = geometry.getAttribute('instance')
  const stats = new Map<number, {
    count: number; sum: number[]; products: number[]
    center: number[]; axis: number[]; min: number; max: number
  }>()
  for (let i = 0; i < positions.count; i++) {
    const id = ids.getX(i)
    if (labels.getX(i) !== 3 || !Number.isInteger(id) || id <= 0) continue
    const x = positions.getX(i), y = positions.getY(i), z = positions.getZ(i)
    if (![x, y, z].every(Number.isFinite)) continue
    let item = stats.get(id)
    if (!item) {
      item = { count: 0, sum: [0, 0, 0], products: [0, 0, 0, 0, 0, 0],
        center: [], axis: [], min: Infinity, max: -Infinity }
      stats.set(id, item)
    }
    item.count++
    item.sum[0] += x; item.sum[1] += y; item.sum[2] += z
    item.products[0] += x*x; item.products[1] += x*y; item.products[2] += x*z
    item.products[3] += y*y; item.products[4] += y*z; item.products[5] += z*z
  }
  for (const item of stats.values()) {
    item.center = item.sum.map(value => value / item.count)
    const [x, y, z] = item.center, p = item.products, n = item.count
    const xx = p[0]/n-x*x, xy = p[1]/n-x*y, xz = p[2]/n-x*z
    const yy = p[3]/n-y*y, yz = p[4]/n-y*z, zz = p[5]/n-z*z
    const largest = [xx, yy, zz].indexOf(Math.max(xx, yy, zz))
    let axis: number[] = [0, 1, 2].map(index => index === largest ? 1 : 0)
    for (let iteration = 0; iteration < 16; iteration++) {
      const [a, b, c] = axis
      const next = [xx*a+xy*b+xz*c, xy*a+yy*b+yz*c, xz*a+yz*b+zz*c]
      const length = Math.hypot(...next)
      if (length < 1e-15) break
      axis = next.map(value => value / length)
    }
    item.axis = axis
  }
  for (let i = 0; i < positions.count; i++) {
    if (labels.getX(i) !== 3) continue
    const item = stats.get(ids.getX(i))
    if (!item) continue
    const t = (positions.getX(i)-item.center[0])*item.axis[0]
      + (positions.getY(i)-item.center[1])*item.axis[1]
      + (positions.getZ(i)-item.center[2])*item.axis[2]
    if (!Number.isFinite(t)) continue
    item.min = Math.min(item.min, t); item.max = Math.max(item.max, t)
  }
  return buildInstancePalette([...stats].map(([id, item]) => ({
    id, paths: [[item.center.map((v, axis) => v + item.min * item.axis[axis]),
      item.center.map((v, axis) => v + item.max * item.axis[axis])]],
  })))
}

/** Render final ownership, including the same bar's separated visible fragments. */
export function parseDenoisePreview(buffer: ArrayBuffer, mode: 'classes' | 'cleaned') {
  const loader = new PLYLoader()
  loader.setCustomPropertyNameMapping({ label: ['label'], instance: ['instance'] })
  const geometry = loader.parse(buffer)
  const labels = geometry.getAttribute('label')
  if (!labels || labels.count !== geometry.getAttribute('position').count) {
    geometry.dispose()
    throw new Error('去噪预览缺少分类信息，请重新分类和去噪')
  }
  for (let i = 0; i < labels.count; i++) {
    const label = labels.getX(i)
    if (!Number.isInteger(label) || label < 0 || label >= DENOISE_CLASSES.length) {
      geometry.dispose()
      throw new Error('去噪预览缺少分类信息，请重新分类和去噪')
    }
  }
  try {
    applyDenoisePreviewAppearance(geometry, mode, mode === 'cleaned' ? [3] : DENOISE_CLASSES.map(item => item.id))
    return geometry
  } catch (error) {
    geometry.dispose()
    throw error
  }
}

function buildPreviewColors(geometry: BufferGeometry, mode: DenoiseColorMode) {
  const labels = geometry.getAttribute('label')
  const instances = geometry.getAttribute('instance')
  if (mode === 'cleaned') {
    if (!instances || instances.count !== labels.count) {
      throw new Error('此去噪结果尚未包含钢筋实例信息，请重新分类和去噪')
    }
    for (let i = 0; i < labels.count; i++) {
      if (labels.getX(i) === 3 && (!Number.isInteger(instances.getX(i)) || instances.getX(i) < 0)) {
        throw new Error('此去噪结果尚未包含钢筋实例信息，请重新分类和去噪')
      }
    }
  }
  const palette = new Map(DENOISE_CLASSES.map(item => [Number(item.id), new Color(item.color)]))
  const spatialColors = mode === 'cleaned' ? previewInstancePalette(geometry) : null
  const instanceColors = new Map<number, Color>()
  const colors = new Float32Array(labels.count * 3)
  for (let i = 0; i < labels.count; i++) {
    const label = labels.getX(i)
    let color = palette.get(label) ?? palette.get(0)!
    if (label === 3) {
      if (mode === 'cleaned') {
        const instance = instances.getX(i)
        color = palette.get(0)!
        if (instance > 0) {
          let assigned = instanceColors.get(instance)
          if (!assigned) {
            const rgb = spatialColors?.get(instance)
            assigned = rgb ? new Color(...rgb)
              : new Color().setHSL(((instance * 0.61803398875) % 1 + 1) % 1, 0.72, 0.58)
            instanceColors.set(instance, assigned)
          }
          color = assigned
        }
      }
    }
    color.toArray(colors, i * 3)
  }
  return colors
}

/** Filter only the preview; preserve the full geometry and stable instance colors. */
export function applyDenoisePreviewAppearance(
  geometry: BufferGeometry,
  mode: DenoiseColorMode,
  visibleClasses: readonly number[],
  instanceIds?: readonly number[],
  options: { dimUnselected?: boolean; highlightInstanceIds?: readonly number[]; highlightColor?: string } = {},
) {
  const cached = previewColors.get(geometry) ?? {}
  const colors = cached[mode] ?? buildPreviewColors(geometry, mode)
  cached[mode] = colors
  previewColors.set(geometry, cached)
  const labels = geometry.getAttribute('label')
  const visible = new Set(visibleClasses)
  const instances = geometry.getAttribute('instance')
  const selected = instanceIds ? new Set(instanceIds) : null
  const highlighted = options.highlightInstanceIds ? new Set(options.highlightInstanceIds) : null
  const dimUnselected = Boolean(options.dimUnselected && selected)
  // Keep GPU buffers stable while the user flips switches repeatedly.
  let index = geometry.getIndex()
  if (!index && (selected && !dimUnselected || mode === 'cleaned' || DENOISE_CLASSES.some(item => !visible.has(item.id)))) {
    index = new BufferAttribute(new Uint32Array(labels.count), 1)
    geometry.setIndex(index)
  }
  let count = 0
  for (let i = 0; i < labels.count; i++) {
    const label = labels.getX(i)
    const isSelected = !selected || Boolean(instances && selected.has(instances.getX(i)))
    if (visible.has(label) && (isSelected || dimUnselected || highlighted)) {
      index?.setX(count, i)
      count++
    }
  }
  const attribute = geometry.getAttribute('color')
  if (attribute) {
    attribute.array.set(colors)
    if (dimUnselected && instances) {
      const dim = new Color('#8b97a8')
      for (let i = 0; i < labels.count; i++) {
        if (labels.getX(i) !== 3 || selected?.has(instances.getX(i))) continue
        dim.toArray(attribute.array as any, i * 3)
      }
    }
    if (highlighted && instances) {
      const highlight = new Color(options.highlightColor ?? '#ffe082')
      for (let i = 0; i < labels.count; i++) {
        if (labels.getX(i) === 3 && highlighted.has(instances.getX(i))) highlight.toArray(attribute.array as any, i * 3)
      }
    }
    attribute.needsUpdate = true
  } else {
    const nextColors = colors.slice()
    if (dimUnselected && instances) {
      const dim = new Color('#8b97a8')
      for (let i = 0; i < labels.count; i++) {
        if (labels.getX(i) !== 3 || selected?.has(instances.getX(i))) continue
        dim.toArray(nextColors, i * 3)
      }
    }
    if (highlighted && instances) {
      const highlight = new Color(options.highlightColor ?? '#ffe082')
      for (let i = 0; i < labels.count; i++) {
        if (labels.getX(i) === 3 && highlighted.has(instances.getX(i))) highlight.toArray(nextColors, i * 3)
      }
    }
    geometry.setAttribute('color', new BufferAttribute(nextColors, 3))
  }
  if (index) index.needsUpdate = true
  geometry.setDrawRange(0, count)
  return count
}
