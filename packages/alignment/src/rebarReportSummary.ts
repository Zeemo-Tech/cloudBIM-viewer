import type { RebarComparisonBar } from '@cloudbim/viewer-core'
import type { reportDiameterStatistics } from './rebarReportFigure'

type Diameter = ReturnType<typeof reportDiameterStatistics>
const nonnegative = (value: unknown): value is number => typeof value === 'number' && Number.isFinite(value) && value >= 0

/** Member counts, not vertex counts. Surface qualification follows the existing
 * inspection P95 rule; diameter uses the worst valid measured section, never
 * the signed mean (opposite errors must not cancel). Missing/review members
 * remain unassessed even if an older payload contains numerical measurements. */
export function summarizeReportMembers(bars: RebarComparisonBar[], diameters: ReadonlyMap<string, Diameter>, surfaceToleranceMm: number, diameterToleranceMm: number | null | undefined) {
  const surfaceLimit = nonnegative(surfaceToleranceMm) ? surfaceToleranceMm : null
  const diameterLimit = nonnegative(diameterToleranceMm) ? diameterToleranceMm : null
  const result = {
    total: bars.length, matched: 0, missing: 0, review: 0,
    surfaceEligible: 0, surfacePass: 0, surfaceFail: 0, surfaceUnknown: 0,
    diameterMeasured: 0, diameterEligible: 0, diameterPass: 0, diameterFail: 0, diameterUnknown: 0,
    validSections: 0, bothEligible: 0, bothPass: 0,
    surfacePassRatio: null as number | null, diameterPassRatio: null as number | null,
    diameterToleranceMm: diameterLimit,
  }
  for (const bar of bars) {
    result[bar.status]++
    const p95 = bar.stats?.p95Abs
    const surfaceEligible = bar.status === 'matched' && bar.knownCount > 0 && nonnegative(p95) && surfaceLimit !== null
    const surfacePass = surfaceEligible && p95 * 1000 <= surfaceLimit
    if (surfaceEligible) { result.surfaceEligible++; surfacePass ? result.surfacePass++ : result.surfaceFail++ }
    const diameter = diameters.get(bar.ifcGlobalId)
    const measured = bar.status === 'matched' && Boolean(diameter && diameter.count > 0 && nonnegative(diameter.maxAbsDeltaMm))
    if (measured) { result.diameterMeasured++; result.validSections += diameter!.count }
    const diameterEligible = measured && diameterLimit !== null
    const diameterPass = diameterEligible && diameter!.maxAbsDeltaMm! <= diameterLimit
    if (diameterEligible) { result.diameterEligible++; diameterPass ? result.diameterPass++ : result.diameterFail++ }
    if (surfaceEligible && diameterEligible) { result.bothEligible++; if (surfacePass && diameterPass) result.bothPass++ }
  }
  result.surfaceUnknown = result.total - result.surfaceEligible
  result.diameterUnknown = result.total - result.diameterEligible
  result.surfacePassRatio = result.surfaceEligible ? result.surfacePass / result.surfaceEligible : null
  result.diameterPassRatio = result.diameterEligible ? result.diameterPass / result.diameterEligible : null
  return result
}
