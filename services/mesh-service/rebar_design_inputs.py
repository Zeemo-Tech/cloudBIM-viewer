"""One validated design snapshot for every workbench stage.

Model dimensions/counts are input data. Workbench assumptions and numerical
tolerances are separate policy; resolving a model must not silently retune them.
No source-path association or second IFC lookup is allowed at this boundary.
"""
from collections import defaultdict
from copy import deepcopy
from dataclasses import dataclass
import math

from rebar_design_prior import validate_snapshot

VERSION = 'workbench-design-inputs-v1'


@dataclass(frozen=True)
class DesignInputs:
    inventory: dict | None
    dimensions: dict
    report: dict


def resolve_design_inputs(snapshot, source_path, source_sha256):
    """Validate binding, then derive dimensions from the same aligned inventory.

    A missing/unresolved design is explicitly unavailable, never permission to
    fall back to the historical model associated with the scan's asset path.
    Length families count matching straight units, not folded physical parents.
    """
    report = {
        'version': VERSION, 'enabled': snapshot is not None,
        'engineeringAssumptions': {
            'coordinateUnit': 'm', 'verticalAxis': 'Z', 'horizontalWorkbench': True,
            'frameInteriorPolicy': 'non-table interior belongs to steel in this fixture setup',
        },
        'consumers': {
            'diameters': '05 observed-cylinder regularization; 06 design-unit association and cylinder models',
            'alignedCenterlines': '01D layers and envelope; 06 instance matching',
            'counts': '06 physical-parent and straight-unit matching diagnostics',
        },
        'algorithmPolicy': {
            'classifierThresholds': 'existing fixed metric and geometric thresholds; not model dimensions',
            'envelopeAllowances': 'existing FloatingZoneParameters; not extracted geometry',
            'radiusSearchBounds': 'existing InternalRebarParameters; not automatically expanded',
            'countSemantics': 'physical parents and matching straight units are distinct',
        },
    }
    dimensions = {'available': False, 'reason': 'no-design-snapshot',
                  'diametersM': [], 'horizontalLengthsM': [], 'families': [],
                  'counts': {}, 'provenance': None}
    if snapshot is None:
        report.update(design={'available': False, 'reason': dimensions['reason']})
        return DesignInputs(None, dimensions, report)

    inventory = deepcopy(validate_snapshot(snapshot, source_path, source_sha256))
    bars, units = inventory['bars'], inventory['units']
    parents = {bar['designBarId']: bar for bar in bars}
    if len(parents) != len(bars):
        raise ValueError('Duplicate physical design bar identities')
    families = defaultdict(list)
    for unit in units:
        parent = parents.get(unit['designBarId'])
        if parent is None:
            raise ValueError('Design unit has no physical parent')
        diameter, length = float(unit['diameterM']), float(unit['lengthM'])
        if not math.isfinite(diameter) or diameter <= 0 or not math.isfinite(length) or length <= 0:
            raise ValueError('Design unit dimensions must be finite positive metres')
        radius = parent.get('radiusM')
        if radius is None or not math.isclose(diameter, 2 * float(radius), rel_tol=1e-6, abs_tol=1e-9):
            raise ValueError('Design unit diameter differs from its physical parent')
        geometric_length = math.dist(unit['startM'], unit['endM'])
        if not math.isclose(length, geometric_length, rel_tol=1e-6, abs_tol=1e-9):
            raise ValueError('Design unit length differs from its aligned centerline')
        if unit['kind'] not in ('straight', 'short', 'web'):
            raise ValueError('Unsupported design unit kind')
        key = (round(diameter, 9), round(length, 9), unit['kind'])
        families[key].append(unit['designUnitId'])

    records = [{'diameterM': d, 'lengthM': length, 'kind': kind,
                'shape': 'straight', 'orientation': 'inclined' if kind == 'web' else 'horizontal',
                'lengthSemantics': 'designMatchingUnit', 'count': len(ids),
                'designUnitIds': ids}
               for (d, length, kind), ids in sorted(families.items())]
    diameters = sorted({r['diameterM'] for r in records})
    counts = {
        'physicalBarCount': len(bars), 'matchingUnitCount': len(units),
        'webPhysicalBarCount': len({u['designBarId'] for u in units if u['kind'] == 'web'}),
        'webStraightUnitCount': sum(u['kind'] == 'web' for u in units),
        'shortUnitCount': sum(u['kind'] == 'short' for u in units),
        'unresolvedBars': sum(b.get('coverage') == 'unresolved' for b in bars),
    }
    # Existing consumers read coverage. Reject conflicting counts rather than
    # reporting one inventory while the matching stage silently uses another.
    for name, value in counts.items():
        if name in inventory.get('coverage', {}) and inventory['coverage'][name] != value:
            raise ValueError(f'Design coverage disagrees with inventory: {name}')
    inventory.setdefault('coverage', {}).update(counts)
    provenance = {
        'selection': 'validated-workbench-snapshot',
        'sourceSha256': source_sha256, 'snapshotFingerprint': snapshot.get('fingerprint'),
        'modelInfo': deepcopy(snapshot.get('modelInfo', {})),
        'inventoryProvenance': deepcopy(inventory.get('provenance', {})),
    }
    dimensions.update(available=bool(diameters),
                      reason=None if diameters else 'no-resolved-design-dimensions',
                      diametersM=diameters,
                      horizontalLengthsM=sorted({r['lengthM'] for r in records if r['kind'] != 'web'}),
                      families=records, counts=counts, provenance=provenance)
    report.update(design={
        'available': bool(diameters), 'diametersM': diameters,
        'horizontalLengthsM': dimensions['horizontalLengthsM'], 'counts': counts,
        'provenance': provenance,
        'geometrySource': 'same aligned inventory for layers, envelope and matching',
    })
    return DesignInputs(inventory, dimensions, report)
