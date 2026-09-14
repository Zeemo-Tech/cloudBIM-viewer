"""Versioned, source-bound design inventory for the optional workbench experiment.

Physical bars and matching units are deliberately different identities. A mapped
solid is a physical occurrence; a folded bar can own many straight matching units.
No geometry from this module is ever substituted for observed scan coordinates.
"""
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import subprocess
import time
import uuid

import numpy as np
from scipy.spatial import cKDTree

from rebar_bim import (_matrix, _walk_items, _scaled_transform, _extruded_points,
                       _swept_disk_points, _basis_for_glb,
                       _extruded_curve_primitives, _swept_disk_primitives)
from algorithms.rebar_design_curves import transform_primitives

VERSION = "design-inventory-v4"
SCHEMA = "rebar-design-snapshot-v1"
MAX_BARS = 20_000
MAX_UNITS = 20_000


def digest_file(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def _hash(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()).hexdigest()


def _write(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name('.' + path.name + '-' + uuid.uuid4().hex)
    try:
        temp.write_text(json.dumps(value, ensure_ascii=False, allow_nan=False))
        os.replace(temp, path)
    finally:
        temp.unlink(missing_ok=True)


def _line_runs(points, minimum_length=.025):
    """Keep straight runs; sampled bend transitions are not counted as web bars."""
    points = np.asarray(points, float)
    if len(points) < 2:
        return []
    points = points[np.r_[True, np.linalg.norm(np.diff(points, axis=0), axis=1) > 1e-8]]
    if len(points) < 2:
        return []
    delta = np.diff(points, axis=0)
    lengths = np.linalg.norm(delta, axis=1)
    axes = delta / lengths[:, None]
    runs, start = [], 0
    for end in range(1, len(delta) + 1):
        if end == len(delta) or np.dot(axes[start], axes[end]) < np.cos(np.deg2rad(3)):
            if np.linalg.norm(points[end] - points[start]) >= minimum_length:
                runs.append((points[start].tolist(), points[end].tolist()))
            start = end
    return runs


def _brep_body(item, transform, unit):
    """Conservative circular straight body of a tessellated solid, possibly hooked.

    Fit only the central body for non-straight shapes. Folded webs fail the radial
    residual gate and remain explicitly unresolved, rather than becoming a chord.
    """
    vertices = [p.Coordinates for f in item.Outer.CfsFaces for b in f.Bounds for p in b.Bound.Polygon]
    xyz = np.unique(np.asarray(vertices, float), axis=0) * unit
    xyz = (transform @ np.c_[xyz, np.ones(len(xyz))].T).T[:, :3]
    if len(xyz) < 12:
        return None, xyz
    center = xyz.mean(axis=0)
    _, _, axes = np.linalg.svd(xyz - center, full_matrices=False)
    axis = axes[0]
    along = (xyz - center) @ axis
    span = np.ptp(along)
    if span < .05:
        return None, xyz
    for partial in (False, True):
        selected = xyz if not partial else xyz[(along > along.min() + .2*span) & (along < along.max() - .2*span)]
        if len(selected) < 12:
            continue
        c = selected.mean(axis=0)
        _, _, basis = np.linalg.svd(selected-c, full_matrices=False)
        uv = (selected-c) @ basis[1:].T
        # Algebraic circle fit is stable on the full circular design section.
        fit, _, rank, _ = np.linalg.lstsq(np.c_[2*uv, np.ones(len(uv))], np.sum(uv*uv, axis=1), rcond=None)
        radius_sq = fit[2] + np.dot(fit[:2], fit[:2])
        if rank < 3 or radius_sq <= 0:
            continue
        radius = float(np.sqrt(radius_sq))
        errors = np.abs(np.linalg.norm(uv-fit[:2], axis=1)-radius)
        if not .0015 <= radius <= .015 or np.quantile(errors, .9) > min(.0008, radius*.2):
            continue
        c += fit[:2] @ basis[1:]
        t = (selected-c) @ basis[0]
        if np.ptp(t) < 10*radius:
            continue
        points = np.array([c+t.min()*basis[0], c+t.max()*basis[0]])
        return (points, radius, 'partial' if partial else 'complete'), xyz
    return None, xyz


def extract_model(ifc_path, model_path=None):
    """Extract in GLB coordinates, independent of the scan/alignment cache."""
    import ifcopenshell
    import ifcopenshell.util.unit
    import ifcopenshell.util.placement

    model = ifcopenshell.open(str(ifc_path))
    unit = float(ifcopenshell.util.unit.calculate_unit_scale(model))
    angle_scale = float(ifcopenshell.util.unit.calculate_unit_scale(model, 'PLANEANGLEUNIT'))
    raw, anchors, diagnostics = [], [], {'unsupportedProducts': 0, 'truncated': False}
    for product in model.by_type('IfcProduct'):
        if not getattr(product, 'Representation', None):
            continue
        # The sample exports circular reinforcing geometry as IfcPlate. Do not
        # infer fixture/rebar semantics solely from that IFC class.
        if product.is_a() not in ('IfcReinforcingBar', 'IfcPlate', 'IfcBuildingElementProxy', 'IfcMember'):
            continue
        try:
            placement = _scaled_transform(ifcopenshell.util.placement.get_local_placement(product.ObjectPlacement), unit)
        except Exception:
            diagnostics['unsupportedProducts'] += 1
            continue
        representations = list(product.Representation.Representations)
        bodies = [r for r in representations if getattr(r, 'RepresentationIdentifier', '') == 'Body']
        for ordinal, rep in enumerate(bodies or representations):
            for item, transform, occurrence in _walk_items(rep.Items, placement, unit, f'representation{ordinal}'):
                if len(raw) >= MAX_BARS:
                    diagnostics['truncated'] = True
                    break
                extracted, coverage, xyz = None, 'complete', None
                if item.is_a('IfcExtrudedAreaSolid'):
                    extracted = _extruded_points(item, transform, unit)
                elif item.is_a('IfcSweptDiskSolid'):
                    extracted = _swept_disk_points(item, transform, unit, angle_scale)
                elif item.is_a('IfcFacetedBrep'):
                    body, xyz = _brep_body(item, transform, unit)
                    if body is not None:
                        extracted, coverage = body[:2], body[2]
                    else:
                        coverage = 'unresolved'
                if extracted is None and xyz is None:
                    continue
                points, radius = extracted if extracted is not None else (None, None)
                primitives = (_extruded_curve_primitives(item, transform, unit)
                              if item.is_a('IfcExtrudedAreaSolid') else
                              _swept_disk_primitives(item, transform, unit, angle_scale)
                              if item.is_a('IfcSweptDiskSolid') else [])
                identity = f'{product.GlobalId}:{occurrence}'
                entry = {'designBarId': identity, 'ifcGlobalId': str(product.GlobalId),
                         'name': str(product.Name or ''), 'productType': product.is_a(),
                         'source': 'ifc-brep-body' if item.is_a('IfcFacetedBrep') else 'ifc-analytic',
                         'coverage': coverage, 'radiusM': radius,
                         'points': points.tolist() if points is not None else [],
                         'boundsM': [xyz.min(axis=0).tolist(), xyz.max(axis=0).tolist()] if xyz is not None else None}
                if primitives:
                    entry['curvePrimitives'] = primitives
                raw.append(entry)
                if points is not None:
                    anchors.append((str(product.GlobalId), points, radius))
    basis = _basis_for_glb(anchors, Path(model_path) if model_path else None, diagnostics)
    if basis is None:
        raise ValueError('设计 IFC 与 GLB 坐标约定校验失败')
    for bar in raw:
        for field in ('points', 'boundsM'):
            if bar.get(field):
                p = np.asarray(bar[field])
                bar[field] = ((basis @ np.c_[p, np.ones(len(p))].T).T[:, :3]).tolist()
        if bar.get('curvePrimitives'):
            bar['curvePrimitives'] = transform_primitives(
                bar['curvePrimitives'], basis[:3, :3], basis[:3, 3])
    return {'version': VERSION, 'bars': raw, 'diagnostics': diagnostics, 'ifcUnitScale': unit}


def _relation(a, b):
    """Directed local geometric relation. XY crossing is not a physical weld."""
    p, q = np.asarray(a['startM']), np.asarray(b['startM'])
    u, v = np.asarray(a['endM'])-p, np.asarray(b['endM'])-q
    ua, va = u/np.linalg.norm(u), v/np.linalg.norm(v)
    parallel = abs(float(ua@va)) > .94
    if parallel:
        if np.dot(ua, va) < 0:
            q = np.asarray(b['endM']); v = -v
        # Relative transverse displacement encodes layer and lane ordering.
        delta = (q+v/2)-(p+u/2)
        transverse = delta-ua*(delta@ua)
        return {'kind': 'parallel', 'offsetM': transverse.tolist(), 'distanceM': float(np.linalg.norm(transverse))}
    if abs(ua[2]) < .2 and abs(va[2]) < .2:
        matrix = np.column_stack((u[:2], -v[:2]))
        if abs(np.linalg.det(matrix)) > 1e-9:
            t, s = np.linalg.solve(matrix, q[:2]-p[:2])
            if -.01 <= t <= 1.01 and -.01 <= s <= 1.01:
                dz = float((q+s*v-p-t*u)[2])
                return {'kind': 'crossing', 'heightDifferenceM': dz,
                        'order': 'above' if dz > .002 else 'below' if dz < -.002 else 'uncertain'}
    return None


def inventory_from_bars(bars, scan_to_bim, *, provenance=None):
    """Also serves future analytic IFC and synthetic validation without meshing."""
    inverse = np.linalg.inv(_matrix(scan_to_bim))
    physical, units, relations = [], [], []
    for source in bars:
        bar = deepcopy(source)
        points = np.asarray(bar.get('points', []), float)
        bar['unitIds'] = []
        if bar.get('curvePrimitives'):
            try:
                bar['curvePrimitives'] = transform_primitives(
                    bar['curvePrimitives'], inverse[:3, :3], inverse[:3, 3])
            except ValueError:
                # Invalid or historical metadata must not poison the sampled
                # centerline fallback or be mistaken for exact geometry.
                bar.pop('curvePrimitives', None)
        if points.size:
            if points.ndim != 2 or points.shape[1] != 3 or not np.isfinite(points).all():
                raise ValueError('Invalid design centerline')
            points = (inverse @ np.c_[points, np.ones(len(points))].T).T[:, :3]
            bar['points'] = points.tolist()
            bar['boundsM'] = [points.min(axis=0).tolist(), points.max(axis=0).tolist()]
            runs = _line_runs(points)
            # A short hook leg belongs to its long physical bar. It is not a
            # standalone web diagonal just because its direction is inclined.
            run_lengths = [float(np.linalg.norm(np.asarray(b)-a)) for a,b in runs]
            longest = max(run_lengths, default=0.)
            hooked = longest > 1. and sum(length > longest*.2 for length in run_lengths) == 1 and len(runs)>1
            bar['excludedHookRunCount'] = 0
            for ordinal, (start, end) in enumerate(runs):
                delta = np.asarray(end)-start
                length = float(np.linalg.norm(delta))
                if hooked and length < longest*.2:
                    bar['excludedHookRunCount'] += 1
                    continue
                direction = delta/length
                kind = 'web' if abs(direction[2]) > .2 else 'short' if length <= .5 else 'straight'
                uid = f"{bar['designBarId']}/unit{ordinal}"
                units.append({'designBarId': bar['designBarId'], 'designUnitId': uid,
                              'ordinal': ordinal, 'startM': start, 'endM': end,
                              'direction': direction.tolist(), 'lengthM': length,
                              'diameterM': float(bar['radiusM'])*2, 'kind': kind,
                              'coverage': bar.get('coverage', 'complete'), 'source': bar.get('source', 'ifc-analytic'),
                              'confidence': .8 if bar.get('coverage') == 'partial' else 1.0})
                if bar['unitIds']:
                    relations.append({'from': bar['unitIds'][-1], 'to': uid, 'kind': 'next',
                                      'meaning': 'same parent, distinct straight units; never merge across this edge'})
                bar['unitIds'].append(uid)
            if not runs:
                bar['coverage'] = 'unresolved'
        elif bar.get('boundsM'):
            lo, hi = np.asarray(bar['boundsM'])
            corners = np.array(np.meshgrid(*zip(lo, hi))).T.reshape(-1, 3)
            transformed = (inverse @ np.c_[corners, np.ones(8)].T).T[:, :3]
            bar['boundsM'] = [transformed.min(axis=0).tolist(), transformed.max(axis=0).tolist()]
        physical.append(bar)
        if len(units) > MAX_UNITS:
            raise ValueError('Design matching unit budget exceeded')
    horizontal = [u for u in units if u['kind'] != 'web']
    heights = sorted(((float((np.asarray(u['startM'])[2]+u['endM'][2])/2), u) for u in horizontal), key=lambda item: item[0])
    layer_heights = []
    for height, item in heights:
        if not layer_heights or height-layer_heights[-1] > .005:
            layer_heights.append(height)
        item['layerId'] = len(layer_heights)
    for item in units:
        item.setdefault('layerId', 0)
    if units:
        centers = np.array([(np.asarray(u['startM'])+u['endM'])/2 for u in units])
        tree = cKDTree(centers)
        # Inventory is small and cached; candidate graph remains O(M*k).
        _, near = tree.query(centers, k=min(33, len(units)))
        near = np.asarray(near).reshape(len(units), -1)
        for i, neighbors in enumerate(near):
            for j in neighbors:
                if j == i:
                    continue
                relation = _relation(units[i], units[int(j)])
                if relation is not None:
                    relations.append({'from': units[i]['designUnitId'], 'to': units[int(j)]['designUnitId'], **relation})
    coverage = {'physicalBarCount': len(physical), 'matchingUnitCount': len(units),
                'webPhysicalBarCount': sum(any(u['kind'] == 'web' and u['designBarId'] == b['designBarId'] for u in units) for b in physical),
                'webStraightUnitCount': sum(u['kind'] == 'web' for u in units),
                'shortUnitCount': sum(u['kind'] == 'short' for u in units),
                'completeBars': sum(b.get('coverage') == 'complete' for b in physical),
                'partialBars': sum(b.get('coverage') == 'partial' for b in physical),
                'unresolvedBars': sum(b.get('coverage') == 'unresolved' for b in physical),
                'countPolicy': 'Observed straight-web instances are compared only to parsed web units, never physical parents.',
                'fixtureSource': 'measured point-cloud inner/outer frame; IFC entity type is not fixture evidence'}
    return {'version': VERSION, 'bars': physical, 'units': units, 'relations': relations,
            'layerHeightsM': layer_heights, 'coverage': coverage, 'provenance': provenance or {}}


def _resolve_active_asset(config):
    """Optional local workbench binding, read-only against the managed stack.

    This is server configuration, never a browser argument. Follow the scan's
    current linked BIM and its saved alignment rather than a stale upload path.
    """
    binding=config.get('databaseBinding')
    if not binding: return config
    scan_id=binding.get('scanAssetId')
    if type(scan_id) is not int or scan_id<=0: raise ValueError('Invalid scan asset binding')
    query=f"""SELECT json_build_object('scanDir',s.dir,'modelDir',b.dir,'modelName',b.source_name,
        'bimAssetId',b.id,'uploadDir',u.dir,'alignmentId',a.id,'scanToBim',a.matrix_json::json)
        FROM db_assets s JOIN db_assets b ON b.id=s.linked_bim_id
        JOIN db_alignments a ON a.scan_id=s.id AND a.bim_id=b.id
        JOIN LATERAL (SELECT dir FROM db_uploads WHERE asset_id=b.id ORDER BY created_at DESC LIMIT 1) u ON true
        WHERE s.id={scan_id} AND s.status='ready' AND b.status='ready'"""
    command=['docker','exec','-i',binding.get('container','cloudbim-postgres-1'),'sh','-c',
             'psql -X -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d "$POSTGRES_DB" -At']
    process=subprocess.run(command,input=query+';',text=True,capture_output=True,timeout=10)
    if process.returncode or not process.stdout.strip():
        raise ValueError('无法读取点云当前关联的 BIM 和粗配准；未回退到旧模型')
    row=json.loads(process.stdout)
    data_root=Path(binding['dataRoot']).resolve()
    def local(value):
        return (data_root/Path(value).relative_to(binding.get('containerDataRoot','/app/data'))).resolve()
    if Path(config['sourcePath']).resolve().parent != local(row['scanDir']):
        raise ValueError('数据库绑定与配置源点云不一致')
    return {**config,'ifcPath':str(local(row['uploadDir'])/'source'),'modelPath':str(local(row['modelDir'])/'model.glb'),
            'modelName':row['modelName'],'bimAssetId':row['bimAssetId'],'scanToBim':row['scanToBim'],
            'alignmentProvenance':{'scanAssetId':scan_id,'bimAssetId':row['bimAssetId'],'alignmentId':row['alignmentId'],
                                   'source':'current linked BIM and saved db_alignments'}}


def prepare_snapshot(config_path, cache_directory=None):
    """Server-owned config only. Model parsing and aligned inventory cache separately."""
    started = time.perf_counter()
    path = Path(config_path).resolve()
    config = _resolve_active_asset(json.loads(path.read_text()))
    def resolved(key, required=True):
        value = config.get(key)
        if not value:
            if required:
                raise ValueError(f'Missing design configuration: {key}')
            return None
        p = Path(value)
        return (path.parent/p).resolve(strict=True) if not p.is_absolute() else p.resolve(strict=True)
    source = resolved('sourcePath')
    ifc = resolved('ifcPath')
    glb = resolved('modelPath', False)
    transform = config['scanToBim']
    _matrix(transform)
    source_hash = config.get('sourceSha256')
    if not isinstance(source_hash, str) or len(source_hash) != 64:
        raise ValueError('先验配置必须绑定源点云 SHA-256')
    cache = Path(cache_directory or path.parent/'design-prior-cache')
    model_key = _hash([VERSION, digest_file(ifc), digest_file(glb) if glb else None])
    model_cache = cache/f'model-{model_key}.json'
    model_hit = model_cache.is_file()
    if model_hit:
        model = json.loads(model_cache.read_text())
    else:
        model = extract_model(ifc, glb)
        _write(model_cache, model)
    geometry_done = time.perf_counter()
    aligned_key = _hash([model_key, transform])
    aligned_cache = cache/f'aligned-{aligned_key}.json'
    aligned_hit = aligned_cache.is_file()
    if aligned_hit:
        inventory = json.loads(aligned_cache.read_text())
    else:
        inventory = inventory_from_bars(model['bars'], transform,
            provenance={'modelFingerprint': model_key, 'alignmentFingerprint': aligned_key,
                        'ifcUnitScale': model['ifcUnitScale'], 'diagnostics': model['diagnostics']})
        _write(aligned_cache, inventory)
    return {'schema': SCHEMA, 'sourcePath': str(source), 'sourceSha256': source_hash,
            'fingerprint': _hash([aligned_key, source_hash]), 'inventory': inventory,
            'modelInfo':{'name':config.get('modelName',ifc.name),'bimAssetId':config.get('bimAssetId'),
                         'modelFingerprint':model_key,'alignment':config.get('alignmentProvenance'),
                         'scanToBim':transform,
                         'binding':'current-linked-bim' if config.get('databaseBinding') else 'fixed-server-config'},
            'preparation': {'modelCacheHit': model_hit, 'alignmentCacheHit': aligned_hit,
                            'modelS': geometry_done-started, 'totalS': time.perf_counter()-started}}


def validate_snapshot(snapshot, source_path, source_sha256):
    if snapshot.get('schema') != SCHEMA:
        raise ValueError('Unsupported design snapshot')
    if Path(snapshot['sourcePath']).resolve() != Path(source_path).resolve() or snapshot['sourceSha256'] != source_sha256:
        raise ValueError('设计先验快照与本次源点云不匹配；请重新配置')
    inventory = snapshot['inventory']
    ids = [u['designUnitId'] for u in inventory['units']]
    if len(ids) > MAX_UNITS or len(set(ids)) != len(ids):
        raise ValueError('Invalid design unit identities')
    for unit in inventory['units']:
        xyz = np.asarray([unit['startM'], unit['endM']], float)
        if xyz.shape != (2, 3) or not np.isfinite(xyz).all() or np.linalg.norm(xyz[1]-xyz[0]) < 1e-6:
            raise ValueError('Invalid design unit geometry')
    return inventory
