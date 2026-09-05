# Rebar V5 validation

`services/mesh-service/rebar_validation.py` is the fixed, synthetic physical-truth
acceptance harness for `geometric-v5`. It measures an algorithm's raw-source
projection and its finite centreline intersections separately. It does not turn
an intersection into a point classification or treat algorithm output as human
truth.

Run the parameter scenarios while iterating on the algorithm:

```bash
cd services/mesh-service
../../.cloudbim/mesh-venv/bin/python rebar_validation.py \
  --algorithm geometric-v5 --v5-suite \
  --output ../../.cloudbim/rebar-v5-validation/parameter-suite.json
```

The suite freezes three parameter scenarios before analysis: the default
upper-half circumference, full circumference, and upper-half at 50% deterministic
point density. It also declares two holdout scenarios with seed `20261017` (top
full density and full circumference at half density). Execute them only after
parameters are frozen, using `--include-holdout`:

```bash
../../.cloudbim/mesh-venv/bin/python rebar_validation.py \
  --algorithm geometric-v5 --v5-suite --include-holdout \
  --output ../../.cloudbim/rebar-v5-validation/final-suite.json
```

An optional parameter object is accepted either inline or as a file via
`--parameters-json`. For every run the report records the normalized object and
a SHA-256 fingerprint of `rebar_validation.py` plus the V5 implementation files
*before* calling `analyze`. The conventional output directory is
`.cloudbim/rebar-v5-validation/`; it is an ignored review artifact, not a source
of truth.

The main semantic precision, recall, scene confusion matrix, per-case recall,
and steel deletion rates include every physical truth observation. In particular,
the deliberately hard `declared_tiny_crossing` observations are included in
semantic recall. `truthID=0` has no physical instance identity and is reported as
`instanceTruth.truthIdZeroNoInstanceTruthCount`; it is excluded only from IoU
instance matching so it cannot be conflated with an arbitrary predicted ID.

Each executed scenario has explicit checks. Passing requires all of these fixed
thresholds; changing synthetic geometry or reducing a threshold is not an
acceptable way to make the suite pass.

| Metric | Requirement |
| --- | ---: |
| Base-bar family precision | >= 0.99 |
| Base-bar family recall | >= 0.98 |
| Hook family recall | >= 0.90 |
| Web/inclined family recall | >= 0.90 |
| True steel classified as table | <= 0.01 |
| True steel classified as fixture | <= 0.01 |
| Instance IoU >= 0.5 precision | >= 0.90 |
| Instance IoU >= 0.5 recall | >= 0.90 |
| Hook predicted-parent consistency | >= 0.95 |

The command emits `passed` per scenario and for the suite. Current algorithm
work is still in progress: do not infer a pass from this document, and do not
publish a final acceptance result until the holdout command has been run against
the frozen parameters and source fingerprint.

The old single-case CLI remains available for compatibility:

```bash
../../.cloudbim/mesh-venv/bin/python rebar_validation.py \
  --algorithm geometric-v5 --seed 20260905 --full-circle
```
