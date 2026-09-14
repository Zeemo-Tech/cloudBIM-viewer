import type { RebarPointVisibilityCategory, RebarVisualizationMetadata } from '@/api/backend-rebar'

export type RebarVisualizationMode = 'rebar-class' | 'rebar-direction' | 'rebar-instance'
export type Rgb = [number, number, number]

export const V3_COLORS = {
  clutter: '#334155', table: '#94a3b8', noise: '#d946ef', rebar: '#ef4444',
  directionA: '#22d3ee', directionB: '#f97316', intersection: '#facc15',
} as const

const AMBIGUITY_COLOR = '#a855f7'
const INACTIVE_COLOR = '#525252'
export const REBAR_SEMANTIC_FALLBACK_COLORS = {
  fixtureUnknown: '#10b981', fixtureSquareTube: '#60a5fa', fixturePlate: '#fbbf24', fixtureBolt: '#f472b6',
  rebarUnresolved: '#a855f7', rebarPlanar: '#2dd4bf', rebarWeb: '#fb923c',
} as const
type RebarPoint = { sceneClass: number; flags: number; direction: number; instance: number; fixtureKind?: number; rebarRole?: number }

export const REBAR_POINT_VISIBILITY_CATEGORIES: RebarPointVisibilityCategory[] = [
  'unknown', 'table', 'noise', 'fixtureUnknown', 'fixtureSquareTube', 'fixturePlate', 'fixtureBolt',
  'rebarUnresolved', 'rebarPlanar', 'rebarWeb',
]

export function rebarPointVisibilityCategory(point: Pick<RebarPoint, 'sceneClass' | 'fixtureKind' | 'rebarRole'>): RebarPointVisibilityCategory {
  if (point.sceneClass === 1) return 'table'
  if (point.sceneClass === 3) return 'noise'
  if (point.sceneClass === 4) return point.fixtureKind === 1 ? 'fixtureSquareTube' : point.fixtureKind === 2 ? 'fixturePlate' : point.fixtureKind === 3 ? 'fixtureBolt' : 'fixtureUnknown'
  if (point.sceneClass === 2) return point.rebarRole === 1 ? 'rebarPlanar' : point.rebarRole === 2 ? 'rebarWeb' : 'rebarUnresolved'
  return 'unknown'
}

/** Attributes are additive in V5; missing subtype values use the broad legacy bucket. */
export function isRebarPointVisible(point: Pick<RebarPoint, 'sceneClass' | 'fixtureKind' | 'rebarRole'>, visibility: Partial<Record<RebarPointVisibilityCategory, boolean>> | undefined): boolean {
  return visibility?.[rebarPointVisibilityCategory(point)] !== false
}

export function visibleRebarPointIndices(
  count: number,
  pointAt: (index: number) => Pick<RebarPoint, 'sceneClass' | 'fixtureKind' | 'rebarRole'>,
  visibility: Partial<Record<RebarPointVisibilityCategory, boolean>> | undefined,
): number[] | null {
  if (!Object.values(visibility ?? {}).some((value) => value === false)) return null
  let filtered: number[] | null = null
  for (let index = 0; index < count; index += 1) {
    if (isRebarPointVisible(pointAt(index), visibility)) {
      filtered?.push(index)
    } else if (!filtered) {
      filtered = Array.from({ length: index }, (_, previous) => previous)
    }
  }
  return filtered
}

const hex = /^#[0-9a-f]{6}$/i
export function validateVisualization(value: unknown): RebarVisualizationMetadata | null {
  if (!value || typeof value !== 'object') return null
  const v = value as Partial<RebarVisualizationMetadata>
  if (!['rebar-visualization-v1', 'rebar-visualization-v2', 'rebar-visualization-v3'].includes(v.schema ?? '') || v.defaultMode !== 'rebar-class' ||
    v.instanceStrategy !== 'golden-angle-v1' || !v.colors || typeof v.colors !== 'object') return null
  const requiredColors = v.schema === 'rebar-visualization-v3'
    ? ['unknown', 'table', 'rebar', 'noise', 'fixture', 'directionA', 'directionB']
    : Object.keys(V3_COLORS)
  for (const key of requiredColors) if (!hex.test((v.colors as Record<string, string>)[key] ?? '')) return null
  if (!v.attributes || !v.values) return null
  if (v.schema === 'rebar-visualization-v3') {
    const scenes = v.values.sceneClass as Record<string, unknown> | undefined
    if (!scenes || scenes.unknown !== 0 || scenes.table !== 1 || scenes.rebar !== 2 || scenes.noise !== 3 || scenes.fixture !== 4) return null
    if (v.values.rebarClass && (v.values.rebarClass as Record<string, unknown>).nonRebar !== 0) return null
    for (const color of Object.values(v.colors)) if (!hex.test(color)) return null
  } else if (v.schema === 'rebar-visualization-v2') {
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

export function rebarSemanticColor(visualization: unknown, category: keyof typeof REBAR_SEMANTIC_FALLBACK_COLORS): string {
  const colors = validateVisualization(visualization)?.colors
  const colorKey = ({ fixtureUnknown: 'fixture', fixtureSquareTube: 'squareTube', fixturePlate: 'plate', fixtureBolt: 'bolt', rebarUnresolved: 'unresolved', rebarPlanar: 'planar', rebarWeb: 'web' } as const)[category]
  return colors?.[colorKey] ?? REBAR_SEMANTIC_FALLBACK_COLORS[category]
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
  sceneClass?: number; flags?: number; direction?: number; instance?: number; fixtureKind?: number; rebarRole?: number
}): Rgb | null {
  const metadata = validateVisualization(visualization)
  if (!metadata || point.sceneClass === undefined || point.flags === undefined) return null
  return v3ColorWithMetadata(mode, metadata, {
    sceneClass: point.sceneClass, flags: point.flags,
    direction: point.direction ?? 0, instance: point.instance ?? 0, fixtureKind: point.fixtureKind, rebarRole: point.rebarRole,
  })
}

export function createRebarColorizer(metadata: RebarVisualizationMetadata, instancePalette?: ReadonlyMap<number, Rgb>) {
  const colorForInstance = (id: number) => instancePalette?.get(id) ?? instanceColor(id)
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
  const fixtureColors = (['fixtureUnknown','fixtureSquareTube','fixturePlate','fixtureBolt'] as const)
    .map((category) => hexRgb(rebarSemanticColor(metadata, category)))
  const roleColors = (['rebarUnresolved','rebarPlanar','rebarWeb'] as const)
    .map((category) => hexRgb(rebarSemanticColor(metadata, category)))
  const hasRoleContract = metadata.schema === 'rebar-visualization-v3' && Boolean(metadata.values.rebarRole)
  const directionColor = (id: number): Rgb => {
    if (id === 1) return directionA
    if (id === 2) return directionB
    return instanceColor(id)
  }

  let sceneColors: Map<number, Rgb> | null = null
  if (metadata.schema === 'rebar-visualization-v2' || metadata.schema === 'rebar-visualization-v3') {
    const names = metadata.values.sceneClass as Record<string, number>
    sceneColors = new Map()
    for (const name in names) {
      const id = names[name]
      sceneColors.set(id, hexRgb(colors[name] ?? (id === 4 ? '#10b981' : V3_COLORS.clutter)))
    }
  }

  return (mode: RebarVisualizationMode, point: RebarPoint): Rgb => {
    if (sceneColors) {
      // V5 does not assign intersection points. Bit 1 is instance ambiguity;
      // bit 0 is deliberately ignored because it is never emitted by V5.
      const fixtureColor = point.sceneClass === 4
        ? fixtureColors[point.fixtureKind ?? 0] ?? fixtureColors[0]
        : null
      const scene = fixtureColor ?? sceneColors.get(point.sceneClass) ?? clutter
      if (point.sceneClass !== 2) return mode === 'rebar-class' ? scene : inactive
      if (metadata.schema === 'rebar-visualization-v2' && ((point.flags & 1) !== 0 || point.direction === 65535)) return intersection
      const ambiguous = metadata.schema === 'rebar-visualization-v3'
        ? (point.flags & 2) !== 0 || point.instance === 0
        : (point.flags & 2) !== 0 || point.instance === 0xffffffff
      if (mode === 'rebar-class' && hasRoleContract) {
        return roleColors[point.rebarRole ?? 0] ?? roleColors[0]!
      }
      if (mode === 'rebar-instance') {
        if (ambiguous) return ambiguity
        if (point.instance > 0) return colorForInstance(point.instance)
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
    if (point.instance) return colorForInstance(point.instance)
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
  if (metadata.schema === 'rebar-visualization-v3') {
    const scenes = metadata.values.sceneClass as Record<string, number>
    const labels: Record<string, string> = { unknown: '未知', table: '台面', rebar: '钢筋', noise: '噪声', fixture: '夹具／围挡' }
    const entries = Object.entries(scenes).sort((a, b) => a[1] - b[1]).map(([key, id]) => ({
      label: labels[key] ?? key,
      color: hexRgb(metadata.colors[key] ?? V3_COLORS.clutter),
    }))
    if (mode === 'rebar-class') {
      const kinds = metadata.values.fixtureKind as Record<string, number> | undefined
      const roles = metadata.values.rebarRole as Record<string, number> | undefined
      if (!kinds && !roles) return entries
      const fixtureIndex = entries.findIndex((entry) => entry.label === '夹具／围挡')
      const fixtures = Object.entries(kinds ?? {}).sort((a, b) => a[1] - b[1]).map(([key]) => ({
        label: ({ unknown: '夹具·未细分', squareTube: '夹具·方管', plate: '夹具·夹持板', bolt: '夹具·螺栓' } as Record<string, string>)[key] ?? key,
        color: hexRgb(rebarSemanticColor(metadata, key === 'squareTube' ? 'fixtureSquareTube' : key === 'plate' ? 'fixturePlate' : key === 'bolt' ? 'fixtureBolt' : 'fixtureUnknown')),
      }))
      const roleEntries = Object.entries(roles ?? {}).sort((a, b) => a[1] - b[1]).map(([key]) => ({
        label: ({ unresolved: '钢筋·待判定', planar: '钢筋·平面筋', web: '钢筋·斜腹杆' } as Record<string, string>)[key] ?? key,
        color: hexRgb(rebarSemanticColor(metadata, key === 'planar' ? 'rebarPlanar' : key === 'web' ? 'rebarWeb' : 'rebarUnresolved')),
      }))
      const sceneEntries = fixtureIndex < 0 ? [...entries, ...fixtures] : [...entries.slice(0, fixtureIndex), ...fixtures, ...entries.slice(fixtureIndex + 1)]
      const rebarIndex = sceneEntries.findIndex((entry) => entry.label === '钢筋')
      return rebarIndex < 0 ? [...sceneEntries, ...roleEntries] : [...sceneEntries.slice(0, rebarIndex), ...roleEntries, ...sceneEntries.slice(rebarIndex + 1)]
    }
    if (mode === 'rebar-direction') return [item('方向 A', 'directionA'), item('方向 B', 'directionB'), { label: '非钢筋', color: hexRgb(INACTIVE_COLOR) }]
    return [item(`单根实例（${summary?.instanceCount ?? 0}）`, 'rebar'), { label: '归属待确认', color: hexRgb(metadata.colors.ambiguity ?? AMBIGUITY_COLOR) }, { label: '非钢筋', color: hexRgb(INACTIVE_COLOR) }]
  }
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
