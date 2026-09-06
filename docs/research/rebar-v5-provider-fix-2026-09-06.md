# V5 default and provider runtime fix, 2026-09-06

V5 is now the default at the user's explicit request for manual review. This is
not a claim of acceptance: the first frozen holdout still failed, and the real
900 s / 2 GiB performance targets remain unverified.

## Failure evidence

The managed mesh-service log for the asset 5 request ending at 10:33:25 shows:

```text
POST /assets/5/rebar-segmentation?force=true -> 502 (about 5m4s)
rebar_api.compute_rebar -> rebar_poc._compute_rebar_artifact
  -> pipeline.analyze -> refine_fixture_support -> SpatialStore.query
ValueError: spatial neighbourhood exceeds V5 memory budget; reduce feature radii
POST /rebar/compute -> 500
```

The fixture refinement stage queried a complete coalesced face region. A long
or dense face, even without coalescing, can exceed the neighborhood point limit.
The generic provider response then hid the spatial-budget cause. This was an
explicit algorithm resource guard, not evidence of an HTTP timeout or an OOM.

`test_rebar_v5_fixture_refinement.py` reproduced the same exception with 2,400
raw plane records and a neighborhood limit of 800, before the fix. This small
reproducer uses the real disk-backed store and raw refinement call chain.

## Minimal repair

- Keep successful whole-region queries and the existing face fitter unchanged.
- On typed `SpatialBudgetExceeded`, visit intersecting spatial cores in stable
  order and refit complete raw support with a halo derived from the existing
  face length, grid, cylinder-width rejection and tube-companion rules.
- Every local query still obeys the same limit. Do not sample, increase the cap,
  shrink physical support, relax merge conditions, or return unverified proposal
  faces. A physical halo that itself exceeds the limit remains an explicit error.
- Export optional half-open `coreBounds` as support-count provenance; halo
  observations are fitting context, not owned evidence. Retain only cells
  supported by valid core observations and recount each face's raw
  `supportCount`. These bounds must not clip the cells' projection footprints:
  storage boundaries otherwise create false gaps inside observed cells. The
  unchanged final mask combines finite occupied cells with an idempotent OR.
- Return sanitized HTTP 422 `resource_limit_exceeded` through Python and Go,
  with a readable panel message instead of `provider_failed` for this condition.
- Prefer V5 for new computations, including when viewing a saved V3/V4 result.
  Saved results retain their original schema; their algorithm-specific parameters
  are not copied into V5. No stored latest pointer was replaced by this repair.

## Verification

Code and regression checks:

```bash
cd services/mesh-service
../../.cloudbim/mesh-venv/bin/python -m unittest \
  test_rebar_v5_fixture_refinement.py test_rebar_v5_scene.py test_rebar_v5_foundation.py
../../.cloudbim/mesh-venv/bin/python -m unittest discover -p 'test_rebar*.py'
```

- Focused tests: 28 passed, including the formerly failing dense face, unchanged
  under-budget output, holes, core ownership/counts, reader-chunk determinism,
  noise exclusion, true halo overflow, and half-density square-tube coverage.
  A dense projection lattice additionally verifies that storage boundaries do
  not cut observed cells; this caught 7,469 false negatives in the first draft.
- Full Python rebar tests: 205 passed. The pre-existing optional SQLite loader
  warning is still present; this suite completes successfully without it.
- `go test ./...` in `backend/`: passed, including default and error propagation.
- `npm run build`: passed, retaining the existing bundle-size warning.
- Real Vue panel with mocked HTTP responses: eight cases passed at widths 1100
  and 390 for no saved result and saved V3/V4/V5 results. V5 is selected, the
  original artifact is emitted for viewing, and a compute error renders the
  resource-budget message. This is not authenticated full-asset browser QA.

Development synthetic checks:

```bash
cd services/mesh-service
../../.cloudbim/mesh-venv/bin/python rebar_validation.py \
  --algorithm geometric-v5 --v5-suite \
  --output ../../.cloudbim/rebar-v5-validation/development-provider-fix-02.json
```

All three development cases passed their unchanged gates. Frozen source and
evaluator fingerprint:
`924dac6daf932e27c8d462f461a7d63172cf30706c1985b7883116b27aa03ac1`.
No held-out scenario was rerun or inspected for this runtime repair.

## Running stack

The managed stack was stopped and started with `scripts/cloudbim-dev.sh` after
the final algorithm edits. Frontend 5173 and backend health 8090 both returned
HTTP 200; mesh service and PostgreSQL report healthy. Live mesh OpenAPI confirms
`geometric-v5` as the request default, and the deployed pipeline, spatial store
and API file hashes match the workspace. The six fixture regressions also pass
inside the rebuilt mesh container. The container excludes test modules from its
image, so the test file was passed on stdin to its Python interpreter. Optional
PyMeshLab plugin loader warnings remain; they did not prevent these V5 tests.

Real validation boundary: the failing full real input has not been recomputed
with this revision. No new real artifact, ROI review or performance acceptance is
claimed. Restarting the stack updates code, not stored segmentation results;
manual review must request a new calculation, using the panel's recompute action
when an older artifact is cached.

Two subagents were used: Sol/high for read-only planning and correctness review,
and Terra/medium for isolated default/API/error-path implementation. Root owned
the spatial repair, integrated the changes and ran final checks.
