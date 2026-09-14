"""Whole-cluster statistical rejection, only after all Step 06 ownership merges."""
import numpy as np
from scipy.spatial import cKDTree

from .design_prior_refinement import _candidates, PriorParameters
from .rebar_extension import exterior_clusters, ExtensionParameters


def filter_final_clusters(context, out, inventory, associations, protected, cluster_records,
                          *, review_unassigned=True):
    units = inventory['units']
    unit_centers = np.array([(np.asarray(u['startM'])+u['endM'])/2 for u in units])
    unit_axes = np.array([u['direction'] for u in units])
    unit_lengths = np.array([u['lengthM'] for u in units])
    tree = cKDTree(unit_centers)
    rows = np.flatnonzero(out['complete_class'] == 3)
    owners = out['complete_instance'][rows]
    order = np.argsort(owners, kind='stable')
    values, starts = np.unique(owners[order], return_index=True)
    populations = []
    for owner, group in zip(values, np.split(rows[order], starts[1:])):
        if owner:
            populations.append((int(owner), group))
        elif review_unassigned:
            group = group[~protected[group]]
            if not len(group):
                continue
            labels = exterior_clusters(context.positions[group], ExtensionParameters())
            order = np.argsort(labels, kind='stable')
            _, starts = np.unique(labels[order], return_index=True)
            populations.extend((0, part) for part in np.split(group[order], starts[1:]))
    measured = []
    for owner, group in populations:
        if len(group) < 3:
            continue  # The final tiny-island pass handles these with distance evidence.
        xyz = context.positions[group]; center = xyz.mean(0)
        _, basis = np.linalg.eigh((xyz-center).T @ (xyz-center))
        axis = basis[:, -1]; projected = (xyz-center) @ axis
        length = float(np.ptp(projected))
        choices = associations.get(owner, set())
        matched = next(iter(choices)) if len(choices) == 1 else None
        summary = dict(center=center, axis=axis, length=length, radius=.004, strong=True)
        ranks = _candidates(summary, units, unit_centers, unit_axes, unit_lengths, tree, PriorParameters())
        plausible = [u for cost, u in ranks if cost <= min(2., ranks[0][0]+.12)] if ranks else []
        # A nearby explicitly designed short bar defeats a length-deficit claim
        # against a long bar with an equally plausible geometric match.
        candidate = matched if matched is not None else min(plausible, key=lambda u:units[u]['lengthM']) if plausible else None
        measured.append(dict(owner=owner, rows=group, length=length, pointCount=len(group), axis=axis,
                             matched=matched, candidate=candidate, protected=bool(protected[group].any())))
    references = [m for m in measured if m['matched'] is not None and
                  units[m['matched']].get('coverage') == 'complete' and
                  .65 <= m['length']/units[m['matched']]['lengthM'] <= 1.3]
    report = dict(phase='final_after_all_merges', expectedClusterCount=len(units),
        observedInstancesBefore=int(np.count_nonzero(values)), removedInstanceCount=0,
        removedComponentCount=0, removedPointCount=0, highScoreRemovedPointCount=0,
        highScoreOverrideAllowed=True, maximumLengthRatio=.35, maximumPointCountRatio=.35,
        referencePolicy='same design slot winner, otherwise >=3 nearby comparable matched bars',
        decisions=[], reviewedComponentCount=len(measured),
        reviewUnassigned=review_unassigned,
        unassignedPolicy=('clustered_for_diagnostics' if review_unassigned
                          else 'superseded_by_final_unassigned_disposition'))
    scores = getattr(context, 'fused_steel_score', None)
    for m in measured:
        u = m['candidate']
        if u is None or m['protected'] or units[u].get('coverage') != 'complete':
            continue
        unit = units[u]; expected_length = unit['lengthM']
        length_ratio = m['length']/expected_length
        if length_ratio >= report['maximumLengthRatio']:
            continue
        peers = [r for r in references if r is not m and r['matched'] == u]
        reference_kind = 'same_design_slot'
        if not peers:
            peers = [r for r in references if r is not m and
                     units[r['matched']]['kind'] == unit['kind'] and
                     abs(r['axis'] @ m['axis']) >= .94 and
                     .7 <= units[r['matched']]['diameterM']/unit['diameterM'] <= 1.4 and
                     np.linalg.norm(unit_centers[r['matched']]-unit_centers[u]) < 1.]
            if len(peers) < 3:
                continue
            peers = sorted(peers, key=lambda r:np.linalg.norm(unit_centers[r['matched']]-unit_centers[u]))[:8]
            reference_kind = 'neighboring_matched_bars'
        expected_points = float(np.median([r['pointCount']/r['length']*expected_length for r in peers]))
        point_ratio = m['pointCount']/max(expected_points, 1.)
        if point_ratio >= report['maximumPointCountRatio']:
            continue
        selected = m['rows']
        cluster_id = max((c['id'] for c in cluster_records), default=0)+1
        decision = dict(clusterId=cluster_id, sourceInstanceId=m['owner'],
            designUnitId=unit['designUnitId'], pointCount=len(selected),
            observedLengthM=m['length'], designLengthM=expected_length, lengthRatio=length_ratio,
            expectedPointCount=expected_points, pointCountRatio=point_ratio,
            referenceKind=reference_kind, referenceInstanceIds=[r['owner'] for r in peers],
            highScorePointCount=0 if scores is None else int(np.count_nonzero(scores[selected] >= .9)),
            reason='final_cluster_length_and_point_count_deficit')
        cluster_records.append(dict(id=cluster_id, pointCount=len(selected), category='rejected-final-cluster',
                                    sourceClusterIds=np.unique(out['complete_cluster'][selected]).tolist()))
        out['complete_cluster'][selected] = cluster_id
        out['complete_class'][selected] = 4
        for name in ('complete_instance', 'complete_segment', 'complete_confidence'):
            out[name][selected] = 0
        report['removedInstanceCount'] += int(m['owner'] > 0)
        report['removedComponentCount'] += 1
        report['removedPointCount'] += len(selected)
        report['highScoreRemovedPointCount'] += decision['highScorePointCount']
        report['decisions'].append(decision)
    report['observedInstancesAfter'] = len(np.unique(out['complete_instance'][out['complete_instance'] > 0]))
    return report
