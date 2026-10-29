import { Vector3, type Box3 } from 'three'

// Shared with the inspection camera: viewer +X is right, and projected +Y is up.
export const REBAR_INSPECTION_TILT = Math.PI / 10
const screenUp = new Vector3(0, Math.sin(REBAR_INSPECTION_TILT), -Math.cos(REBAR_INSPECTION_TILT))
const LENGTH_RELATIVE_TOLERANCE = 0.05
const LENGTH_ABSOLUTE_TOLERANCE = 0.02 // metres
const POSITION_TOLERANCE = 0.02 // metres in the fixed inspection view

/** Short members first, then left-to-right rows from the top of the inspection view.
 * Use complete design bounds, including missing/review members, never observed support.
 * Length/row bands have a fixed anchor; an epsilon comparator would be non-transitive.
 */
export function orderRebarInspection<T extends { ifcGlobalId: string }>(
  bars: readonly T[],
  boundsForBar: (bar: T) => Box3 | null,
): T[] {
  const located: { bar: T; length: number; right: number; up: number; depth: number }[] = []
  const unlocated: T[] = []
  const compareId = (a: T, b: T) => a.ifcGlobalId < b.ifcGlobalId ? -1 : a.ifcGlobalId > b.ifcGlobalId ? 1 : 0
  for (const bar of bars) {
    const bounds = boundsForBar(bar)
    if (!bounds || bounds.isEmpty() || ![...bounds.min.toArray(), ...bounds.max.toArray()].every(Number.isFinite)) {
      unlocated.push(bar)
      continue
    }
    const center = bounds.getCenter(new Vector3())
    // Spatial span distinguishes short/long design members without depending on
    // scan coverage, vertex density, IDs, or the current camera's zoom/orbit.
    const length = bounds.getSize(new Vector3()).length()
    located.push({ bar, length, right: center.x, up: center.dot(screenUp), depth: center.y })
  }
  located.sort((a, b) => a.length - b.length || compareId(a.bar, b.bar))
  const ordered: T[] = []
  for (let first = 0; first < located.length;) {
    const anchor = located[first]!.length
    const tolerance = Math.max(LENGTH_ABSOLUTE_TOLERANCE, anchor * LENGTH_RELATIVE_TOLERANCE)
    let end = first + 1
    while (end < located.length && located[end]!.length - anchor <= tolerance) end++
    const group = located.slice(first, end).sort((a, b) => b.up - a.up || a.right - b.right || compareId(a.bar, b.bar))
    for (let rowStart = 0; rowStart < group.length;) {
      const rowTop = group[rowStart]!.up
      let rowEnd = rowStart + 1
      while (rowEnd < group.length && rowTop - group[rowEnd]!.up <= POSITION_TOLERANCE) rowEnd++
      const row = group.slice(rowStart, rowEnd).sort((a, b) => a.right - b.right || compareId(a.bar, b.bar))
      for (let columnStart = 0; columnStart < row.length;) {
        const columnLeft = row[columnStart]!.right
        let columnEnd = columnStart + 1
        while (columnEnd < row.length && row[columnEnd]!.right - columnLeft <= POSITION_TOLERANCE) columnEnd++
        // Stacked members can differ by submillimetres in X. Keep their vertical
        // reading order rather than letting that noise decide which layer is first.
        const column = row.slice(columnStart, columnEnd).sort((a, b) =>
          b.up - a.up || b.depth - a.depth || compareId(a.bar, b.bar),
        )
        ordered.push(...column.map(item => item.bar))
        columnStart = columnEnd
      }
      rowStart = rowEnd
    }
    first = end
  }
  // Retain every member even if its design geometry is unavailable.
  return [...ordered, ...unlocated.sort(compareId)]
}
