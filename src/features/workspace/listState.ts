/** Tab-local list preferences. Never stores auth, asset contents or file handles. */
export type ListFilters = Record<string, string | [Date, Date] | null>
export interface ListState<F extends ListFilters> { filters: F; page: number; pageSize: number }

export function decodeListState<F extends ListFilters>(raw: string | null, defaults: F, sizes: readonly number[]): ListState<F> {
  const state = { filters: { ...defaults }, page: 1, pageSize: sizes[0]! }
  try {
    const saved = JSON.parse(raw || 'null')
    if (!saved || typeof saved !== 'object') return state
    for (const key of Object.keys(defaults) as Array<keyof F>) {
      const value = saved.filters?.[key]
      if (typeof defaults[key] === 'string' && typeof value === 'string' && value.length <= 1024) {
        state.filters[key] = value as F[typeof key]
      } else if (key === 'dateRange' && Array.isArray(value) && value.length === 2) {
        const dates = value.map((item) => typeof item === 'string' ? new Date(item) : new Date(NaN))
        if (dates.every((date) => Number.isFinite(date.getTime())) && dates[0]!.getTime() <= dates[1]!.getTime()) {
          state.filters[key] = dates as F[typeof key]
        }
      }
    }
    if (sizes.includes(saved.pageSize)) state.pageSize = saved.pageSize
    if (Number.isSafeInteger(saved.page) && saved.page > 0 && saved.page <= 100000) state.page = saved.page
  } catch { /* Corrupt/old preferences must never prevent opening the page. */ }
  return state
}

export function readListState<F extends ListFilters>(key: string, defaults: F, sizes: readonly number[]) {
  let raw: string | null = null
  try { raw = sessionStorage.getItem(key) } catch { /* Private/disabled storage uses defaults. */ }
  return decodeListState(raw, defaults, sizes)
}

export function writeListState<F extends ListFilters>(key: string, state: ListState<F>) {
  try { sessionStorage.setItem(key, JSON.stringify(state)) } catch { /* Storage is optional. */ }
}
