# Design-prior v3 visual evidence

The `bad-run-*` files are selected copies of the initial bad-run comparison. They show the
baseline, the prior topology result, and run `20260910T083213-20a8d960` with
the old IFC/config.  Use `bad-run-render-manifest.json` for run identity and
the deterministic point-selection rules.

The images make no whole-scene ground-truth or accuracy claim. Cyan marks source rows
recovered from baseline non-rebar; red marks source rows filtered from baseline
rebar.

The `final-*` files use baseline `20260910T093242-18ab92b8`, the defective old
run `20260910T083213-20a8d960`, and corrected-IFC result
`20260910T093321-cedef2ba`. Final comparisons omit design lines so reference
geometry cannot hide measured gaps. The three merge details show all ROI
points in XY, XZ, and YZ; blue/orange are original instances, green is their
shared result ID, and gray is other retained steel. No points are filled in.

See `final-render-manifest.json` for all 45 images and sampling provenance;
the full generated set is in `.cloudbim/design-prior/review-v3/final/visuals/`.
See the parent [review](../../pointcloud-design-prior-linking-review-2026-09-10.md)
for the root agent's visual assessment, scope limits, and quantitative results.
