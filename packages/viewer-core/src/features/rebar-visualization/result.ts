import type { RebarSegmentationResult } from '@/api/backend-rebar'

/** Both saved V5 revisions share the theoretical-intersection analysis contract. */
export function isRebarV5Result(value: RebarSegmentationResult | null | undefined): value is RebarSegmentationResult {
  return value?.algorithm.id === 'geometric-v5' &&
    (value.algorithm.version === '1' || value.algorithm.version === '2') &&
    value.analysisSchema === 'rebar-analysis-v2' &&
    value.visualization?.schema === 'rebar-visualization-v3'
}
