# V5 recovery, 2026-09-06

This is an implementation and evidence record, not an acceptance certificate.

## Preserved baseline

- Recovery checkpoint: `e23206c`. It preserves all 80 previously changed/new
  files, including earlier C2M, remesh and V4 work. It does not attribute those
  changes to this recovery or certify the unfinished V5 implementation.
- The original pre-V5 baseline remains in `.cloudbim/v5-baseline/`, with HEAD
  `77a774b4a72df62c65f304352c182b6178f35860` and 31 recorded dirty files.
  At recovery start, 28 still matched their baseline hashes; the other three
  contained the subsequent work already described by the handoff.
- All 12 V5 source files matched
  `.cloudbim/v5-handoff/implementation-fingerprint.json` at recovery start.
- The stack was stopped. No real scan was started during diagnosis.

## Reproduced causes

1. Replaying `v5_topdiag05b.json`'s `merge.beforeRaw` against the recovery
   checkpoint takes about 0.16 s and incorrectly groups ordinary candidates 8
   and 10. The direct overlap pass includes association-only candidate 17 and
   accepts edges 8-17 and 10-17. This bypasses the later support overlap gate;
   candidate 17 shares only 18/39 and 21/39 points with the two rods.
2. The same merged group reports 900 raw support records because ownership takes
   the maximum member count. The correct accounting operation is the union of
   original source indices, including distinct records at identical coordinates.
3. Hook stripe candidate 26 shares all 98 support records with hook candidate
   15, but an aggregate residual/order tie heuristic retains it. Tail candidate
   11 has 42 observations that fit candidate 16's cylinder (median radial
   residual about 6.6e-9 m), extending 0.172 m beyond its current endpoint.
   Deleting that candidate without transferring observed tail support loses data.
4. Confirmed fixture points reenter detection by design during mask release.
   The final bar confidence used only axial linearity/direction, omitting the
   already available cylinder surface residual. A linearly textured square face
   can therefore defeat its correctly detected finite planar surface.
   A second, earlier loss was reproduced in the actual half-density pipeline:
   the representative-point fit covered only 511/706 tube records, whereas a
   standalone fit on raw observations covered 700. Raw-store refinement now
   refits proposed finite faces without widening the surface or gap thresholds.
   The focused pipeline diagnostic covers 694/706, with 12 unknown and zero
   tube records assigned to steel; this is not a full-suite acceptance result.

## Repair boundaries

- Association-only candidates may attach to one established physical parent;
  they cannot join two ordinary instances or export inferred stripe geometry.
- Raw-support accounting uses source-index unions, not maxima or coordinate
  deduplication. Final confirmed and candidate support counts are separate.
- Local surface stripes are checked against finite observed parent edges.
  Transferred tails are re-verified on the parent cylinder using the existing
  raw gap, length and vote checks; unsupported gaps remain inferred.
  Transfers are applied immediately in an immutable rank order: the saved
  23 <- 15 <- 11 chain previously hid the short parent before its newly measured
  extent became visible to the later tail. Every descendant must still pass the
  unchanged finite overlap and radial checks against the updated physical root.
- Final planar/cylinder competition includes the existing cylinder surface
  residual and valid local normal agreement with both the face normal and the
  winning cylinder radial normal, not unconditional fixture priority. Taking
  the maximum complete face evidence is order-independent.
- The streaming comparison tool keeps uint64 identities exact, includes sparse
  boundary feature updates, and does not load a full-source coordinate array.

## Release and validation rules

Fresh requests and the frontend default to the pre-V5 `geometric-v3` until V5
passes acceptance. Explicit V5 requests and persisted V5 results remain usable;
older artifacts retain their own visualization schema and point semantics.

All original numeric acceptance thresholds and synthetic geometry are retained.
The existing evaluator's merged-instance count now has an independent zero-merge
veto, so aggregate IoU cannot waive a physical identity merge. Held-out seed
20261017 must not be used for repair decisions. Real-scan performance and ROI
review follow a successful frozen synthetic run.

## IFC evidence

The user has authorized investigating design information, superseding the old
request's pure-point-cloud restriction as a product direction. The current V5
implementation still advertises `bimPrior: false`; no IFC-assisted V5 result is
claimed by this recovery.

The existing extractor was run with the project Python environment on:

- IFC: `backend/data/uploads/419278dd32c37508da3af8bd/source` (extensionless).
- GLB and metadata: `backend/data/assets/45d9b4149bf0870e75ccce38/`.

It produced 50 IFC rods, all with an 8 mm design diameter, and 6 explicitly
identified GLB fallback candidates. The IFC unit scale is 0.001 and the checked
IFC-to-GLB basis is x,z,-y. The identity transform used for this extraction
inventory does not establish scan registration or local design correspondence.

A future optional V5 path can reuse the owner-scoped BIM selection, immutable
input fingerprints and saved registration already used by V4. Use design radii
as bounded candidate/refinement priors, with unique spatial correspondence and
raw observed cylinder support. Missing or ambiguous correspondence must preserve
point-cloud inference; design geometry must not create measured segments or
override observed ownership. The available model has no local diameter variety,
so it cannot resolve these four bugs by choosing between different design sizes.

## Status

Implementation and integrated validation are in progress. V5 is not released.

Current recovery checks:

- Full Python rebar regression: 198 tests passed on recovery-04, recorded in
  `.cloudbim/rebar-v5-validation/unit-tests-recovery-04.log`.
- `go test ./...`: passed. `npm run build`: passed, with the existing bundle-size
  warning. Nine visualization Node tests and six source-comparison tests passed.
- Real Three.js module/browser fixture: passed. The mounted real Vue panel also
  passed eight browser cases (new, V3, V4, V5 at widths 1100 and 390), preserving
  old results and gating intersection controls to V5. These are synthetic UI
  checks, not an authenticated full-asset workflow.
- Development suite recovery-04: all three parameter cases passed every fixed
  gate, including zero merged physical instances. This is not held-out or real
  accuracy. Its implementation/evaluator fingerprint is
  `d90e4a88062659ec6ce892b8204e40fb43909510087b342a6d90042e9e3dd33a`.

| Parameter case | Steel P | Steel R (base family) | Instance IoU50 P/R | Hook parent | Merges |
| --- | ---: | ---: | --- | ---: | ---: |
| top | .999655 | 1 | 1 / 1 | 1 | 0 |
| full | .999657 | 1 | 1 / 1 | 1 | 0 |
| top, half density | 1 | 1 | 1 / 1 | 1 | 0 |

Recovery-02 passed aggregate gates but still retained the known left-tail stripe.
The explicit identity audit caught it; recovery-03 removes it without changing
any tolerance. All three development cases now have 13/13 matched physical
instances, no spurious instances, no splits and no merges. The baseline tail and
16 mm pair have direct full-pipeline regressions, in addition to the aggregate
gates. A three-level transfer regression covers all six candidate permutations
and both association modes; a distinct-axis tail is required to remain separate.
No recovery holdout or real benchmark has run yet.
