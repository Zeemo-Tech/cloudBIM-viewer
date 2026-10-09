import type { C2MStats } from '../api/backend-c2m'

export type C2MRangeMode = 'auto' | 'full' | 'manual'

export function niceC2MRangeMm(value: number) {
  if (!Number.isFinite(value) || value <= 0) return 30
  const scale = 10 ** Math.floor(Math.log10(value))
  const factor = [1, 1.25, 1.5, 2, 2.5, 3, 4, 5, 6, 8, 10].find(n => n * scale >= value) ?? 10
  return Math.max(1, factor * scale)
}

// Compute once when distances arrive; range/tolerance changes must not sort millions of vertices.
export function summarizeC2MRange(distances: Float32Array) {
  const values = distances.filter(Number.isFinite).map(Math.abs).sort()
  if (!values.length) return null
  const quantile = (fraction: number) => {
    const position = (values.length - 1) * fraction
    const lower = Math.floor(position)
    return values[lower] + (values[Math.ceil(position)] - values[lower]) * (position - lower)
  }
  const q1 = quantile(0.25)
  const q3 = quantile(0.75)
  const p98Abs = values[Math.max(0, Math.ceil(values.length * 0.98) - 1)]
  // A percentile alone follows the tail once outliers exceed its exclusion rate.
  // The upper Tukey fence instead follows the middle half of absolute deviations.
  // Cap by P98 so broad, tail-free distributions still use most of the color scale.
  const coreExtentAbs = Math.min(p98Abs, q3 + 1.5 * (q3 - q1))
  return { coreExtentAbs, p98Abs, maxAbs: values[values.length - 1] }
}

export function resolveC2MRangeMm(
  mode: C2MRangeMode,
  manualMm: number,
  toleranceMm: number,
  summary: ReturnType<typeof summarizeC2MRange>,
  stats?: C2MStats | null,
) {
  // Expand the display to accommodate engineering tolerance, never change the tolerance itself.
  const minimum = Math.max(1, toleranceMm * 1.25)
  if (mode === 'manual') return Math.max(minimum, manualMm)
  const maxAbs = stats ? Math.max(Math.abs(stats.min), Math.abs(stats.max)) : 0
  const extent = mode === 'full' ? summary?.maxAbs ?? maxAbs
    : summary?.coreExtentAbs ?? stats?.p95Abs ?? maxAbs
  return niceC2MRangeMm(Math.max(minimum, extent * 1000, 0.1))
}
