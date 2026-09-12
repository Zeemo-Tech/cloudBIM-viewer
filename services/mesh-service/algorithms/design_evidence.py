"""Pure, conservative evidence decisions for design-guided candidates.

``metrics`` contains measurements, never a fitted-model confidence.  A metric
may be a scalar or ``{"value": scalar, "sources": [source_id, ...]}``.  The
optional ``source_ids`` mapping is also accepted (for example,
``{"cylinder": [12, 13], "plane": [88]}``).  Evidence labels are deduplicated
source identifiers so repeated classifier votes cannot look independent.

Retry history is deliberately plain data: each record is a mapping with an
``attempt`` integer, a ``source_ids`` iterable and a ``support_signature``.
The signature must change when an attempt acquired new measured support.  This
module does not fit geometry, mutate candidates, or turn support into a
probability.
"""
from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

from .design_evidence_contract import Candidate, Evidence, Reason, RobustnessPolicy, State

_MIN_VALID_NORMAL_FRACTION = .25
_PLANE_NORMAL_FRACTION = .50
_PLANE_COHERENCE = .90


def _value(metrics: Mapping[str, Any], name: str, default: Any = None) -> Any:
    value = metrics.get(name, default)
    return value.get("value", default) if isinstance(value, Mapping) else value


def _labels(metrics: Mapping[str, Any], *names: str) -> tuple[str, ...]:
    labels: set[str] = set()
    by_name = metrics.get("source_ids", {})
    if not isinstance(by_name, Mapping):
        by_name = {}
    for name in names:
        value = metrics.get(name)
        raw = value.get("sources", ()) if isinstance(value, Mapping) else ()
        raw = by_name.get(name, raw)
        if raw is None:
            continue
        if isinstance(raw, (str, bytes)) or not isinstance(raw, Iterable):
            raw = (raw,)
        labels.update(str(item) for item in raw if item is not None)
    return tuple(sorted(labels))


def _finite(value: Any) -> bool:
    try:
        return value is not None and float(value) == float(value) and abs(float(value)) != float("inf")
    except (TypeError, ValueError):
        return False


def evaluate_evidence(metrics: Mapping[str, Any], *, policy: RobustnessPolicy | None = None) -> Evidence:
    """Classify measured local support without using design rank or probability.

    Missing or invalid normals are reported as missing evidence.  They never
    become a cylinder rejection.  A plane veto requires measured plane support,
    coherent valid normals, and a plane residual which beats the cylinder by
    ``policy.plane_advantage``.
    """
    p = policy or RobustnessPolicy()
    if bool(_value(metrics, "hard_excluded", False)):
        return Evidence(State.EXCLUDED_BOUNDARY, Reason.HARD_BOUNDARY,
                        negative=_labels(metrics, "hard_excluded"))

    positive: set[str] = set()
    negative: set[str] = set()
    missing: set[str] = set()
    count = _value(metrics, "point_count", 0) or 0
    cells = _value(metrics, "occupied_cells", 0) or 0
    bins = _value(metrics, "occupied_bins", 0) or 0
    span = _value(metrics, "span_m", 0) or 0
    cyl_error = _value(metrics, "cylinder_error_m")
    radial = _value(metrics, "radial_alignment")
    normals = _value(metrics, "normal_valid_fraction")
    arc = _value(metrics, "arc_degrees", 0) or 0

    support_ok = (count > 0 and cells >= p.min_occupied_cells and bins >= p.min_occupied_bins and
                  span >= p.minimum_span)
    normals_ok = _finite(normals) and float(normals) >= _MIN_VALID_NORMAL_FRACTION
    radial_ok = _finite(radial) and float(radial) >= p.min_radial_alignment
    cylinder_ok = (support_ok and normals_ok and _finite(cyl_error) and
                   float(cyl_error) <= p.max_surface_error and radial_ok and
                   float(arc) >= p.min_arc_degrees)
    if cylinder_ok:
        positive.update(_labels(metrics, "cylinder", "point_count", "occupied_cells", "occupied_bins", "span_m",
                                "cylinder_error_m", "radial_alignment", "arc_degrees"))
        # A useful report still names measured support when callers supplied no
        # row identifiers; this is a source label, not a second vote.
        if not positive:
            positive.add("measured-cylinder")
    elif count <= 0 or cells <= 0:
        missing.update(_labels(metrics, "support", "point_count", "occupied_cells"))
        missing.add("measured-support")

    if bool(_value(metrics,"missing_endpoints",False)):missing.add("unobserved-endpoints")
    if float(arc)<p.min_arc_degrees:missing.add("visible-arc")
    if not support_ok:missing.add("axial-or-spatial-support")
    if not normals_ok:
        missing.update(_labels(metrics, "normals", "normal_valid_fraction"))
        missing.add("normals")

    # A partial surface remains valid if the directly measured radial evidence
    # is strong.  Only short arcs are provisional, not rejected.
    plane_error = _value(metrics, "plane_error_m")
    coherence = _value(metrics, "plane_coherence")
    fixture_fraction = _value(metrics, "fixture_fraction", 0) or 0
    # Fixture classification can be unknown.  The measured planar fit itself
    # is sufficient only when its aligned, valid normals make it unambiguous.
    plane_measured = (_finite(plane_error) and _finite(coherence) and
                      float(coherence) >= _PLANE_COHERENCE and _finite(normals) and
                      float(normals) >= _PLANE_NORMAL_FRACTION)
    plane_wins = (plane_measured and _finite(cyl_error) and
                  float(plane_error) <= float(cyl_error) * p.plane_advantage)
    if plane_wins:
        negative.update(_labels(metrics, "plane", "plane_error_m", "plane_coherence", "fixture_fraction"))
        if not negative:
            negative.add("measured-plane")
        return Evidence(State.REJECTED_OBSERVED, Reason.PLANE_WINS,
                        tuple(sorted(positive)), tuple(sorted(negative)), tuple(sorted(missing)), 0.0)

    # Explicit measured mismatches are observed conflicts, rather than merely
    # a failure to validate a design candidate.
    conflict = ((_finite(_value(metrics, "offset_m")) and abs(float(_value(metrics, "offset_m"))) > p.max_offset) or
                (_finite(_value(metrics, "angle_degrees")) and abs(float(_value(metrics, "angle_degrees"))) > p.max_angle_degrees) or
                (_finite(_value(metrics, "diameter_error_m")) and abs(float(_value(metrics, "diameter_error_m"))) > p.max_surface_error))
    if conflict:
        negative.update(_labels(metrics, "offset_m", "angle_degrees", "diameter_error_m"))
        if not negative:
            negative.add("measured-geometry-conflict")
        return Evidence(State.REJECTED_OBSERVED, Reason.GEOMETRY_CONFLICT,
                        tuple(sorted(positive)), tuple(sorted(negative)), tuple(sorted(missing)), 0.0)

    if cylinder_ok:
        state = State.CONFIRMED if float(arc) >= p.confirmed_arc_degrees else State.PROVISIONAL
        reason = Reason.CYLINDER_SUPPORT if state == State.CONFIRMED else Reason.PARTIAL_ARC
        # A bounded ordering score based solely on measured fit quality.  It
        # deliberately has no confidence/probability interpretation.
        surface_quality = max(0., min(1., 1. - float(cyl_error) / p.max_surface_error))
        radial_quality = max(0., min(1., (float(radial) - p.min_radial_alignment) /
                                      (1. - p.min_radial_alignment)))
        occupancy_quality = min(1., ((float(cells) / p.min_occupied_cells) +
                                     (float(bins) / p.min_occupied_bins) +
                                     (float(span) / p.minimum_span)) / 3.)
        score = .55 * surface_quality + .30 * radial_quality + .15 * occupancy_quality
        return Evidence(state, reason, tuple(sorted(positive)), tuple(sorted(negative)), tuple(sorted(missing)), score)

    if bool(_value(metrics, "design_only", False)):
        # A design prior may justify trying to acquire observations, never
        # acceptance or a positive evidence vote by itself.
        missing.update(_labels(metrics, "design_only"))
        missing.add("measured-support")
    reason = Reason.MISSING_NORMALS if "normals" in missing else Reason.SPARSE_SUPPORT
    return Evidence(State.INSUFFICIENT, reason, tuple(sorted(positive)), tuple(sorted(negative)), tuple(sorted(missing)), 0.0)


def _field(item: Any, name: str, default: Any = None) -> Any:
    return item.get(name, default) if isinstance(item, Mapping) else getattr(item, name, default)


def _source_set(item: Any) -> set[str]:
    rows = _field(item,"rows",None)
    if rows is not None:
        return {str(value) for value in rows}
    raw = _field(item, "source_ids", None)
    if raw is None:
        raw = _field(item, "provenance", {})
        raw = raw.get("source_ids") if isinstance(raw, Mapping) else None
    if raw is None:
        metrics = _field(item, "metrics", {})
        raw = metrics.get("source_ids") if isinstance(metrics, Mapping) else None
        if isinstance(raw, Mapping):
            flattened = []
            for values in raw.values():
                flattened.extend((values,) if isinstance(values, (str, bytes)) or not isinstance(values, Iterable) else values)
            raw = tuple(flattened)
    # Candidate rows are stable observed source identifiers when a caller did
    # not retain separate provenance IDs.  Do not test numpy arrays for truth.
    if raw is None:
        raw = _field(item, "rows", ())
    if isinstance(raw, (str, bytes)) or not isinstance(raw, Iterable):
        raw = (raw,) if raw is not None else ()
    return {str(value) for value in raw if value is not None}


def retry_decision(candidate: Candidate | Mapping[str, Any], history: Iterable[Mapping[str, Any]], *,
                   anchors: Iterable[Candidate | Mapping[str, Any]] = (),
                   policy: RobustnessPolicy | None = None) -> dict[str, Any]:
    """Return a bounded, independently-supported retry instruction.

    Anchors are usable only when their evidence is ``CONFIRMED`` and their
    source IDs do not overlap this candidate's IDs.  Provisional candidates,
    including design-only hypotheses, cannot anchor a retry.
    """
    p = policy or RobustnessPolicy()
    records = list(history)
    current_attempt = int(_field(candidate, "attempt", 0) or 0)
    highest_attempt = max([current_attempt, *(int(record.get("attempt", 0) or 0) for record in records)], default=0)
    if highest_attempt >= p.max_retries:
        return {"retry": False, "action": "stop", "reason": "max_retries"}

    candidate_sources = _source_set(candidate)
    signature = _field(candidate, "support_signature", None)
    if signature is None:
        provenance = _field(candidate, "provenance", {})
        signature = provenance.get("support_signature") if isinstance(provenance, Mapping) else None
    prior_sources = set().union(*(_source_set(record) for record in records)) if records else set()
    prior_signatures = {record.get("support_signature") for record in records if record.get("support_signature") is not None}
    if records and (not candidate_sources - prior_sources or (signature is not None and signature in prior_signatures)):
        return {"retry": False, "action": "stop", "reason": "no_new_support"}

    candidate_id = str(_field(candidate, "unit_id", _field(candidate, "id", "")))
    independent = []
    for anchor in anchors:
        anchor_id = str(_field(anchor, "unit_id", _field(anchor, "id", "")))
        evidence = _field(anchor, "evidence")
        state = _field(evidence, "state") if evidence is not None else None
        if anchor_id == candidate_id or state != State.CONFIRMED or _source_set(anchor) & candidate_sources:
            continue
        independent.append(anchor_id)
    # The first retry is always local acquisition.  Neighbour comparison is a
    # bounded second attempt and cannot substitute for direct support.
    if highest_attempt == 0:
        return {"retry": True, "action": "adjust_support_or_normals", "reason": "fresh_support_required"}
    if independent:
        return {"retry": True, "action": "compare_neighbors", "reason": "independent_confirmed_anchor",
                "anchors": tuple(sorted(independent))}
    return {"retry": True, "action": "adjust_support_or_normals", "reason": "fresh_support_required"}
