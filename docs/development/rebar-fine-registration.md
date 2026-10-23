# Segmented-rebar rigid fine registration

`POST /alignments/bim/fine` accepts `method: auto | rebar | icp`. Auto uses fresh classified steel when available. A stale or invalid classification is rejected; a missing classification retains the legacy ICP route. Explicit `rebar` requires a current valid classification. The rebar route does not require a remeshed target. A failed rebar solve never retries raw-scene ICP.

## Geometry and weighting

`algorithms/rebar_registration.py` reads original rows from `cleaned.las`, retaining their instance IDs. For each physical bar it uses its longest declared straight design unit of at least 450 mm. Independent circle fits use 12 mm axial windows on evenly spaced stations, >=160 degrees of observed arc, radius/residual/uncertainty checks, and a 4 mm inner-window consistency check. No inferred control-line samples or unobserved endpoints become observations.

A candidate needs >=8 measured sections, >=400 mm supported span, >=35% design span, >=25% station coverage, straightness RMS <=6 mm and direction difference <=8 degrees. Its base weight is

`min(4,(diameter/6mm)^2) * 1/(1+(straightnessRMS/2mm)^2) * sectionCoverage * spanFraction`.

One physical bar receives one normalized weight, regardless of its point count. Per-bar 6 mm Huber weights reduce the effect of isolated installation offsets. The six-DOF solve fits measured centers to fixed design lines; axial sliding is not invented from unobserved endpoints. At least two populated direction families, full Jacobian rank, correction <=3 degrees /75 mm at the reference center and leave-one-bar-out stability (<=5 mm /0.3 degree) are required. No scale or local deformation is fitted.

Within each direction family, spatially ordered whole bars at indices 2,7,... are reserved. Only the remainder enters the global solve. Both weighted and equal-bar RMSE and within10mm bar fractions are evaluated on training and held-out bars; requested regression ratios apply to both. Hard geometry/stability failures cannot be overridden. Same-scan holdout is an internal consistency check, not independent field truth. Selection does not guarantee every diameter appears in the holdout.

## Coordinate contract

All lengths are metres. Saved `T` is a proper rigid scan-to-BIM/GLB Y-up transform encoded column-major. Denoise inventory is in the scan frame defined by its saved `T0`; the solver requires `T == T0`. Fixed design lines are `T0 * designScan`; measured circle centers are initially `T0 * measuredScan`. The correction `C` moves observations only, and the saved result is **`Tnew = C @ T0`**. Raw LAS coordinates and original IFC geometry are not rewritten. Report engineering coordinates use `(X,-Z,Y)` after `Tnew`.

The result reports translation of the reference center (`diagnostics.fit.pivotBimM`), not just the difference between transform translation columns. Diagnostics retain both full matrices, correction, per-bar weights, source hashes, measured sections, split IDs and deletion sensitivity. Tests use asymmetric rotations/translations and reject reflection, scaling and unconstrained parallel-bar geometry.

## Freshness and preservation

Backend verifies current source/model/IFC/alignment fingerprints and classification artifact hashes before computation and again before saving. Complete alignment and derivative snapshots must still match. Saving archives the previous complete DB alignment and the candidate under `scanAsset/alignment-history/`; alignment saving itself does not remove classification or C2M files. The existing C2M rerun endpoint may clean up unreferenced old C2M files. This trial therefore archived the original distance/mesh files with verified hashes before requesting a rerun, under the assessment's `historical-c2m/`. The audit file records an attempted application, while the API and DB establish whether it committed.

A new global transform makes prior denoise and C2M inputs stale. The frontend cancels pending preview loads, clears instance geometry and blocks deviation analysis until classification is rerun. Operational sequence:

1. Saved coarse alignment and preliminary classification.
2. Rebar-weighted fine registration.
3. New denoise/segmentation generation using the new saved transform.
4. New instance C2M generation bound to the new segmentation version.
5. Report exported from those fresh versions.

TRIAL-YB-20261022 evidence is versioned separately in `.cloudbim/assessment-20261023-fine-registration/`. The unchanged historical v03 report is the coarse baseline; v04 is generated from the rerun results.
