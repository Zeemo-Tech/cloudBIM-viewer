import assert from 'node:assert/strict'
import test from 'node:test'
// @ts-ignore Node source runner.
import { projectRebarProfile } from './rebarProjection.ts'
import type { RebarProfileSample } from '@cloudbim/viewer-core'

test('projections preserve geometric scale and gaps across missing or separate units', () => {
  const samples: RebarProfileSample[] = [
    { designUnitId: 'a', stationM: 0, designCenterM: [0,0,0], observedCenterM: [0,.01,0], transverseOffsetM: .01 },
    { designUnitId: 'a', stationM: 1, designCenterM: [1,0,0], observedCenterM: null, transverseOffsetM: null },
    { designUnitId: 'a', stationM: 2, designCenterM: [2,0,0], observedCenterM: [2,.01,0], transverseOffsetM: .01 },
    { designUnitId: 'b', stationM: 0, designCenterM: [2,1,0], observedCenterM: [2,1.01,0], transverseOffsetM: .01 },
  ]
  const plot = projectRebarProfile(samples, [0,1])!
  assert.equal(plot.designPaths.length, 2)
  assert.equal(plot.observedPaths.length, 3)
  assert.equal(plot.observedPoints.length, 3)
  assert.ok(Math.abs(plot.horizontalSpanMm/plot.verticalSpanMm-500/130)<1e-9)
  assert.equal(projectRebarProfile([], [0,2]),null)
})
