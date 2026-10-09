import type { RebarProfileSample } from '@cloudbim/viewer-core'

/** Preserve equal geometric scale. Never connect a missing observation or separate design units. */
export function projectRebarProfile(samples: RebarProfileSample[], axes: [number, number]) {
  const valid = (point: number[] | null): point is [number, number, number] => Boolean(point?.length === 3 && point.every(Number.isFinite))
  const points = samples.flatMap(s => [s.designCenterM, s.observedCenterM]).filter(valid)
  if (!points.length) return null
  const [horizontal, vertical] = axes
  const xmin = Math.min(...points.map(p => p[horizontal]!)), xmax = Math.max(...points.map(p => p[horizontal]!))
  const ymin = Math.min(...points.map(p => p[vertical]!)), ymax = Math.max(...points.map(p => p[vertical]!))
  const spanX = Math.max(xmax - xmin, .001), spanY = Math.max(ymax - ymin, .001)
  const scale = Math.min(500 / spanX, 130 / spanY)
  const x = (p: number[]) => 300 + (p[horizontal]! - (xmin + xmax) / 2) * scale
  const y = (p: number[]) => 93 - (p[vertical]! - (ymin + ymax) / 2) * scale
  function paths(observed: boolean) {
    const result: string[] = []
    let path = '', previousUnit = ''
    for (const sample of samples) {
      const point = observed ? sample.observedCenterM : sample.designCenterM
      if (!valid(point) || sample.designUnitId !== previousUnit) {
        if (path) result.push(path)
        path = ''
      }
      if (valid(point)) path += `${path ? 'L' : 'M'}${x(point).toFixed(2)},${y(point).toFixed(2)} `
      previousUnit = sample.designUnitId
    }
    if (path) result.push(path)
    return result
  }
  return {
    designPaths: paths(false), observedPaths: paths(true),
    observedPoints: samples.filter(s => valid(s.observedCenterM)).map(s => ({ x: x(s.observedCenterM!), y: y(s.observedCenterM!) })),
    horizontalSpanMm: 500 / scale * 1000, verticalSpanMm: 130 / scale * 1000,
  }
}
