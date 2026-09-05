import type { RebarVisualizationMetadata } from '@/api/backend-rebar'

export type RebarVisualizationMode = 'rebar-class' | 'rebar-direction' | 'rebar-instance'
export type Rgb = [number, number, number]

export const V3_COLORS = {
  clutter: '#334155', table: '#94a3b8', noise: '#d946ef', rebar: '#ef4444',
  directionA: '#22d3ee', directionB: '#f97316', intersection: '#facc15',
} as const

const AMBIGUITY_COLOR = '#a855f7'
const INACTIVE_COLOR = '#525252'
type RebarPoint = { sceneClass: number; flags: number; direction: number; instance: number }

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

export function createRebarColorizer(metadata: RebarVisualizationMetadata) {
  const colors = metadata.colors
  const clutter = hexRgb(colors.clutter ?? V3_COLORS.clutter)
  const table = hexRgb(colors.table ?? V3_COLORS.table)
  const noise = hexRgb(colors.noise ?? V3_COLORS.noise)
  const rebar = hexRgb(colors.rebar ?? V3_COLORS.rebar)
  const directionA = hexRgb(colors.directionA ?? V3_COLORS.directionA)
  const directionB = hexRgb(colors.directionB ?? V3_COLORS.directionB)
  const intersection = hexRgb(colors.intersection ?? V3_COLORS.intersection)
  const ambiguity = hexRgb(colors.ambiguity ?? AMBIGUITY_COLOR)
  const inactive = hexRgb(INACTIVE_COLOR)
  const directionColor = (id: number): Rgb => {
    if (id === 1) return directionA
    if (id === 2) return directionB
    return instanceColor(id)
  }

  let sceneColors: Map<number, Rgb> | null = null
  if (metadata.schema === 'rebar-visualization-v2') {
    const names = metadata.values.sceneClass as Record<string, number>
    sceneColors = new Map()
    for (const name in names) {
      const id = names[name]
      sceneColors.set(id, hexRgb(colors[name] ?? (id === 4 ? '#10b981' : V3_COLORS.clutter)))
    }
  }

  return (mode: RebarVisualizationMode, point: RebarPoint): Rgb => {
    if (sceneColors) {
      // V2: bit 1 is a crossing and wins if both bits are present; bit 2 only
      // accents uncertain ownership in instance mode, preserving category/direction meaning.
      const scene = sceneColors.get(point.sceneClass) ?? clutter
      if (point.sceneClass !== 2) return mode === 'rebar-class' ? scene : inactive
      if ((point.flags & 1) !== 0 || point.direction === 65535) return intersection
      const ambiguous = (point.flags & 2) !== 0 || point.instance === 0xffffffff
      if (mode === 'rebar-instance') {
        if (ambiguous) return ambiguity
        if (point.instance > 0) return instanceColor(point.instance)
      }
      if (mode === 'rebar-direction' && point.direction > 0 && point.direction !== 65535) return directionColor(point.direction)
      return rebar
    }

    const isRebar = point.sceneClass === 2
    const isIntersection = isRebar && ((point.flags & 1) !== 0 || point.direction === 65535 || point.instance === 0xffffffff)
    const scene = point.sceneClass === 1 ? table : point.sceneClass === 3 ? noise : point.sceneClass === 2 ? rebar : clutter
    if (mode !== 'rebar-class' && !isRebar) return inactive
    if (isIntersection) return intersection
    if (mode === 'rebar-class') return point.direction === 1 ? directionA : point.direction === 2 ? directionB : scene
    if (mode === 'rebar-direction') return point.direction === 1 ? directionA : point.direction === 2 ? directionB : rebar
    if (point.instance) return instanceColor(point.instance)
    return scene
  }
}

export function v3ColorWithMetadata(mode: RebarVisualizationMode, metadata: RebarVisualizationMetadata, point: RebarPoint): Rgb {
  return createRebarColorizer(metadata)(mode, point)
}

export function legendItems(mode: RebarVisualizationMode, visualization: unknown, summary?: { instanceCount?: number; directionCount?: number }): Array<{ label: string; color: Rgb }> {
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
    if (mode === 'rebar-class') return [...entries, item('交点', 'intersection')]
    if (mode === 'rebar-direction') {
      const directionCount = summary?.directionCount === undefined
        ? 2
        : Math.max(0, Math.floor(summary.directionCount))
      const directions = Array.from({ length: directionCount }, (_, index) => {
        const id = index + 1
        const label = id === 1 ? '方向 A' : id === 2 ? '方向 B' : `方向 ${id}`
        const color = id === 1 ? hexRgb(c.directionA) : id === 2 ? hexRgb(c.directionB) : instanceColor(id)
        return { label, color }
      })
      return [...directions, item('交点', 'intersection'), { label: '非钢筋', color: hexRgb(INACTIVE_COLOR) }]
    }
    return [
      item(`单根实例（${summary?.instanceCount ?? 0}）`, 'rebar'),
      item('交点', 'intersection'),
      { label: '归属待确认', color: hexRgb(metadata.colors.ambiguity ?? AMBIGUITY_COLOR) },
      { label: '非钢筋', color: hexRgb(INACTIVE_COLOR) },
    ]
  }
  if (mode === 'rebar-instance') return [item(`实例（${summary?.instanceCount ?? 0}，golden-angle-v1）`, 'rebar'), item('交叉', 'intersection'), { label: '非钢筋', color: hexRgb(INACTIVE_COLOR) }]
  if (mode === 'rebar-direction') return [item('方向 A', 'directionA'), item('方向 B', 'directionB'), item('交叉', 'intersection'), { label: '非钢筋', color: hexRgb(INACTIVE_COLOR) }]
  return [item('台面', 'table'), item('杂物', 'clutter'), item('噪声', 'noise'), item('方向 A', 'directionA'), item('方向 B', 'directionB'), item('交叉', 'intersection')]
}
