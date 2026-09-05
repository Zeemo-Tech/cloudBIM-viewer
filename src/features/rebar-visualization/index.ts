import type { RebarVisualizationMetadata } from '@/api/backend-rebar'

export type RebarVisualizationMode = 'rebar-class' | 'rebar-direction' | 'rebar-instance'
export type Rgb = [number, number, number]

export const V3_COLORS = {
  clutter: '#334155', table: '#94a3b8', noise: '#d946ef', rebar: '#ef4444',
  directionA: '#22d3ee', directionB: '#f97316', intersection: '#facc15',
} as const

const hex = /^#[0-9a-f]{6}$/i
export function validateVisualization(value: unknown): RebarVisualizationMetadata | null {
  if (!value || typeof value !== 'object') return null
  const v = value as Partial<RebarVisualizationMetadata>
  if (!['rebar-visualization-v1', 'rebar-visualization-v2'].includes(v.schema ?? '') || v.defaultMode !== 'rebar-class' ||
    v.instanceStrategy !== 'golden-angle-v1' || !v.colors || typeof v.colors !== 'object') return null
  for (const key of Object.keys(V3_COLORS)) if (!hex.test((v.colors as Record<string, string>)[key] ?? '')) return null
  if (!v.attributes || !v.values) return null
  if (v.schema === 'rebar-visualization-v2') {
    const scenes = v.values.sceneClass as Record<string, unknown> | undefined
    if (!scenes || scenes.table !== 1 || scenes.rebar !== 2 || (scenes.fixture_formwork ?? scenes.fixture) !== 4) return null
    for (const color of Object.values(v.colors)) if (!hex.test(color)) return null
  }
  return v as RebarVisualizationMetadata
}

export function hexRgb(value: string): Rgb {
  const n = Number.parseInt(value.slice(1), 16)
  return [((n >> 16) & 255) / 255, ((n >> 8) & 255) / 255, (n & 255) / 255]
}

export function instanceColor(id: number): Rgb {
  const hue = (((id - 1) * 137.508 + 15) % 360 + 360) % 360
  const lightness = id % 2 ? 0.52 : 0.64
  const saturation = 0.75
  const c = (1 - Math.abs(2 * lightness - 1)) * saturation
  const x = c * (1 - Math.abs((hue / 60) % 2 - 1))
  const m = lightness - c / 2
  const [r, g, b] = hue < 60 ? [c, x, 0] : hue < 120 ? [x, c, 0] : hue < 180 ? [0, c, x] : hue < 240 ? [0, x, c] : hue < 300 ? [x, 0, c] : [c, 0, x]
  return [r + m, g + m, b + m]
}

export function v3Color(mode: RebarVisualizationMode, visualization: unknown, point: {
  sceneClass?: number; flags?: number; direction?: number; instance?: number
}): Rgb | null {
  const metadata = validateVisualization(visualization)
  if (!metadata || point.sceneClass === undefined || point.flags === undefined) return null
  return v3ColorWithMetadata(mode, metadata, {
    sceneClass: point.sceneClass, flags: point.flags,
    direction: point.direction ?? 0, instance: point.instance ?? 0,
  })
}

export function v3ColorWithMetadata(mode: RebarVisualizationMode, metadata: RebarVisualizationMetadata, point: {
  sceneClass: number; flags: number; direction: number; instance: number
}): Rgb {
  const colors = { ...V3_COLORS, ...metadata.colors }
  const named = (name: keyof typeof V3_COLORS) => hexRgb(colors[name])
  if (metadata.schema === 'rebar-visualization-v2') {
    const names = metadata.values.sceneClass as Record<string, number>
    const key = Object.keys(names).find((name) => names[name] === point.sceneClass) ?? 'clutter'
    const scene = hexRgb(metadata.colors[key] ?? (point.sceneClass === 4 ? '#10b981' : colors.clutter))
    const hasInstance = point.instance > 0 && point.instance !== 0xffffffff
    if (mode === 'rebar-instance' && hasInstance) return instanceColor(point.instance)
    if (point.sceneClass !== 2) return scene
    if ((point.flags & 2) !== 0 || point.instance === 0xffffffff) return named('intersection')
    if (mode === 'rebar-direction' && point.direction > 0 && point.direction !== 65535) return instanceColor(point.direction)
    return named('rebar')
  }
  const intersection = (point.flags & 1) !== 0 || point.direction === 65535 || point.instance === 0xffffffff
  const scene = point.sceneClass === 1 ? named('table') : point.sceneClass === 3 ? named('noise') : point.sceneClass === 2 ? named('rebar') : named('clutter')
  if (intersection) return named('intersection')
  if (mode === 'rebar-class') return point.direction === 1 ? named('directionA') : point.direction === 2 ? named('directionB') : scene
  if (mode === 'rebar-direction') return point.direction === 1 ? named('directionA') : point.direction === 2 ? named('directionB') : point.sceneClass === 2 ? named('rebar') : scene.map((v) => v * 0.42) as Rgb
  if (point.instance && point.instance !== 0xffffffff) return instanceColor(point.instance)
  return scene
}

export function legendItems(mode: RebarVisualizationMode, visualization: unknown, summary?: { instanceCount?: number }): Array<{ label: string; color: Rgb }> {
  const metadata = validateVisualization(visualization)
  if (!metadata) return []
  const c = { ...V3_COLORS, ...metadata.colors }
  const item = (label: string, color: keyof typeof V3_COLORS) => ({ label, color: hexRgb(c[color]) })
  if (metadata.schema === 'rebar-visualization-v2') {
    const scenes = metadata.values.sceneClass as Record<string, number>
    const labels: Record<string, string> = { clutter: '其他', table: '台面', rebar: '钢筋', noise: '噪声', statisticalNoise: '噪声', fixture: '夹具／围挡', fixture_formwork: '夹具／围挡' }
    const seen = new Set<number>()
    const entries = Object.entries(scenes).filter(([, id]) => {
      if (typeof id !== 'number' || seen.has(id)) return false
      seen.add(id); return true
    }).sort((a, b) => a[1] - b[1]).map(([key, id]) => ({ label: labels[key] ?? key, color: hexRgb(metadata.colors[key] ?? (id === 4 ? '#10b981' : c.clutter)) }))
    if (mode === 'rebar-instance') entries.push(item(`单根实例（${summary?.instanceCount ?? 0}）`, 'rebar'))
    if (mode === 'rebar-direction') entries.push(item('三维方向（不同颜色）', 'directionA'))
    entries.push(item('归属待确认', 'intersection'))
    return entries
  }
  if (mode === 'rebar-instance') return [item(`实例（${summary?.instanceCount ?? 0}，golden-angle-v1）`, 'rebar'), item('交叉', 'intersection'), item('台面', 'table'), item('杂物', 'clutter'), item('噪声', 'noise')]
  if (mode === 'rebar-direction') return [item('方向 A', 'directionA'), item('方向 B', 'directionB'), item('交叉', 'intersection'), item('非钢筋', 'clutter')]
  return [item('台面', 'table'), item('杂物', 'clutter'), item('噪声', 'noise'), item('方向 A', 'directionA'), item('方向 B', 'directionB'), item('交叉', 'intersection')]
}
