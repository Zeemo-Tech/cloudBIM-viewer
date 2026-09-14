"""Three semantic steel layers and source-aligned floating-noise candidates.

Cloth membership never changes classifier inputs or votes. Step 05 enforces
the cloth boundary on all steel, independently of the observed residual review.
"""
import time
import numpy as np
from . import multiview_floating_noise
from .design_floating_zones import build_floating_zones, classify_floating_zones
from .spatial_keys import unique_integer_rows

ATTRIBUTES = {'shared_layer': 'u1', 'shared_floating_noise': 'u1'}


def _structural_bands(design_layers):
    """Merge crossing-bar elevations, then separate bottom and top assemblies."""
    ordered = sorted(design_layers, key=lambda item: item['heightM'])
    if not ordered:
        return []
    gaps = np.array([b['lowM']-a['highM'] for a, b in zip(ordered, ordered[1:])])
    split = int(np.argmax(gaps))+1 if len(gaps) and gaps.max() > .006 else len(ordered)
    groups = [ordered[:split]] + ([ordered[split:]] if split < len(ordered) else [])
    return [dict(height=float(np.mean([item['heightM'] for item in group])),
                 low=min(item['lowM'] for item in group), high=max(item['highM'] for item in group))
            for group in groups]


def prepare_floating_scene(context, inventory=None, *, output, progress=None):
    started = time.perf_counter()
    progress = progress or (lambda *args: None)
    progress('01D：钢筋分层与设计禁飞区', 0, len(context.positions))
    report = build_floating_zones(inventory)
    eligible = ~np.asarray(context.shared_table_mask, bool) & np.isin(context.partition_zone, [1, 3])
    cloth_eligible = ~np.asarray(context.shared_table_mask, bool)
    _, forbidden = classify_floating_zones(context.positions, report, eligible_mask=cloth_eligible)
    layers = np.zeros(len(context.positions), np.uint8)
    # Without a usable design, measured horizontal bands still move before A/B.
    # Occupied voxels prevent repeated scanner echoes manufacturing layer peaks.
    from .internal_rebar import _height_bands, InternalRebarParameters
    scope = np.flatnonzero(eligible & (context.partition_zone == 1))
    bands, histogram = [], {}
    if len(scope):
        points = context.positions[scope]
        origin = points.min(axis=0)
        _, representatives = unique_integer_rows(np.floor((points-origin)/.003).astype(np.int64), return_index=True)
        points = points[representatives]
        normals = context.normals[scope[representatives]]
        valid = context.normal_valid[scope[representatives]].astype(bool)
        # Horizontal cylindrical surfaces include side and top arcs; exclude
        # flat horizontal faces, which alone cannot establish a steel layer.
        evidence = valid & (np.abs(normals[:, 2]) < .9)
        axes = np.tile([1., 0., 0.], (len(points), 1))
        bands, histogram = _height_bands(points, axes, evidence.astype(float), InternalRebarParameters())
    design_bands = sorted((b for b in report.get('layers', []) if b['id'] > 0), key=lambda b: b['heightM'])
    if report.get('enabled') and design_bands:
        bands = _structural_bands(design_bands)
    z = context.positions[:, 2]
    if len(bands) == 2:
        layers[eligible & (z > bands[0]['high']) & (z < bands[1]['low'])] = 3
    for i, band in enumerate(bands, 1):
        selected = eligible & (z >= band['low']) & (z <= band['high'])
        layers[selected] = i
    layers[~eligible] = 0
    output['shared_layer'][:] = layers
    output['shared_floating_noise'][:] = forbidden
    for name, values in output.items():
        values.flags.writeable = False
        setattr(context, name, values)
    report.update(eligiblePointCount=int(cloth_eligible.sum()), forbiddenPointCount=int(forbidden.sum()),
                  elapsedS=time.perf_counter()-started,
                  removedBeforeComputation=False,
                  eligibility='all non-table source rows; final steel scope applied in step 05')
    semantic_layers = [{'id': 1, 'name': '底层钢筋'}, {'id': 2, 'name': '顶层钢筋'}, {'id': 3, 'name': '腹杆层'}]
    for layer, band in zip(semantic_layers, bands):
        layer.update(heightM=band['height'], lowM=band['low'], highM=band['high'])
    if len(bands) == 2:
        semantic_layers[2].update(lowM=bands[0]['high'], highM=bands[1]['low'])
    layering = {'source': 'design-and-measured' if report.get('enabled') else 'measured',
                'bands': bands, 'layers': semantic_layers, 'heightHistogram': histogram,
                'counts': {str(i): int(n) for i, n in enumerate(np.bincount(layers, minlength=4))},
                'elapsedS': time.perf_counter()-started,
                'policy': 'bottom steel, top steel and intervening web candidates; no clipping or classifier votes'}
    context.scene_cache.update(layering=layering, floatingZones=report)
    return layering, report


def review_floating_noise(points, *, hard_mask=None, observed_review=True, **kwargs):
    """Apply the cloth boundary to every steel row supplied by step 05.

    ``hard_mask`` is the historical argument name for design review candidates.
    """
    hard = np.zeros(len(points), bool) if hard_mask is None else np.asarray(hard_mask, bool)
    review = np.asarray(kwargs['review_mask'], bool)
    if hard.shape != (len(points),) or review.shape != (len(points),):
        raise ValueError('masks must correspond to source rows')
    if observed_review:
        removed, report = multiview_floating_noise.multiview_noise_mask(points, **kwargs)
        removed &= review
    else:
        removed = np.zeros(len(points), bool)
        report = {'version': 'shared-design-floating-v1', 'candidatePointCount': int(review.sum()),
                  'reviewedCandidatePointCount': 0, 'views': [], 'components': []}
    design_removed = hard.copy() if observed_review else np.zeros(len(points), bool)
    additional = design_removed & ~removed
    removed |= design_removed
    report.update(sharedAlgorithm=True, observedReviewEnabled=observed_review,
                  designVetoEnabled=observed_review, designVetoStage='05',
                  designVetoScope='all classified steel; no instance or score exceptions',
                  designCandidatePointCount=int(hard.sum()),
                  designConfirmedRemovedPointCount=int(np.count_nonzero(hard & removed)),
                  designProtectedPointCount=int(np.count_nonzero(hard & ~removed)),
                  designHardRemovedPointCount=int(design_removed.sum()),
                  additionalDesignRemovedPointCount=int(additional.sum()),
                  removedPointCount=int(removed.sum()))
    scores = kwargs.get('steel_scores')
    if scores is not None:
        report['highScoreRemovedPointCount'] = int(np.count_nonzero(removed & (np.asarray(scores) >= .9-1e-6)))
    return removed, report


def denoise_branch(context, labels, *, cache, stage, workers=1, progress=None):
    """Report candidates without changing independent A/B labels or evidence."""
    hard = np.asarray(context.shared_floating_noise, bool)
    _, report = review_floating_noise(context.positions, hard_mask=hard,
        review_mask=labels == 3, observed_review=False)
    report.update(stage=stage, noiseClass=4,
                  policy='independent classifier output unchanged; floating review deferred to step 05')
    return report
