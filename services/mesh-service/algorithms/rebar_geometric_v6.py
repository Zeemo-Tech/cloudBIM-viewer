"""Production adapter for the shared Step 05 segmentation and floating denoising."""
import hashlib
import json
import logging
import os
from pathlib import Path
import tempfile

import numpy as np
from threadpoolctl import threadpool_limits

from .pointcloud_normals import available_workers
from .pointcloud_segmentation import segment_points, VERSION as PIPELINE_VERSION
from .rebar_base import RebarAlgorithm, RebarAlgorithmError, RebarAnalysis, RebarInputContext, RebarPointAttributes
from .rebar_v5.contracts import VISUALIZATION
from .rebar_v5.spatial import SpatialBudgetExceeded

VERSION = '12'
CHUNK_SIZE = 262144
MAX_SOURCE_POINTS = 20_000_000
SCENE_LOOKUP = np.array([0, 1, 4, 2, 3], np.uint8)


class Runtime:
    def __init__(self):
        self.temp = tempfile.TemporaryDirectory(prefix='rebar-v6-')
        self.path = Path(self.temp.name)
        self.stages = None
        self.points = None
        self.source_indices = None

    def close(self):
        self.stages = None
        self.points = None
        self.source_indices = None
        self.temp.cleanup()


def instance_records(report):
    segments = {}
    for segment in report['segments']:
        segments.setdefault(segment['instanceId'], []).append(segment)
    result = []
    for item in report['instances']:
        observed = [{'points': [s['startM'], s['endM']]} for s in segments.get(item['id'], [])]
        result.append({**item, 'centerline': [], 'observedSegments': observed,
                       'radius': item['diameterM'] / 2,
                       'role': 'web' if item['type'] == 3 else 'planar' if item['type'] in (1, 2) else 'unresolved',
                       'layerId': item['type'], 'evidence': 'step05 observed internal cylinders'})
    return result


def source_attributes(runtime, rows):
    context = runtime.stages.context
    classes = context.refined_class[rows].copy()
    classes[context.internal_type[rows] == 5] = 4
    ids = context.internal_instance[rows].astype(np.uint32)
    steel = classes == 3
    scene = SCENE_LOOKUP[classes]
    count = len(classes)
    fit_confidence = context.internal_confidence[rows].astype(np.float32)
    # The stage reports geometric assignment scores, not calibrated probabilities.
    fit_confidence = np.clip(fit_confidence, 0, 1)
    fused_score = getattr(context, 'fused_steel_score', None)
    fused_score = (np.zeros(count, np.float32) if fused_score is None else
                   np.clip(np.asarray(fused_score[rows], np.float32), 0, 1))
    class_confidence = np.where(steel, np.maximum(fit_confidence, fused_score), 0).astype(np.float32)
    role = runtime.roles[ids]
    directions = runtime.directions[ids]
    attrs = RebarPointAttributes(steel.astype(np.uint8), directions, ids, scene,
        np.where(steel & (ids == 0), 2, 0).astype(np.uint8),
        class_confidence, np.where(ids > 0, fit_confidence, 0).astype(np.float32),
        np.zeros(count, np.uint8), role)
    attrs.validate(count)
    return attrs


class GeometricV6Adapter(RebarAlgorithm):
    @property
    def descriptor(self):
        return {'id': 'geometric-v6', 'version': VERSION, 'name': '钢筋分层与悬浮点去噪（当前生产算法）',
            'analysisSchema': 'rebar-analysis-v2',
            'capabilities': {'class': True, 'direction': True, 'instance': True, 'confidence': True,
                'sceneClass': True, 'rebarFlags': True, 'rawLabels': True, 'features': True,
                'intersections': False, 'fixtureKind': False, 'rebarRole': True, 'bimPrior': True},
            'inputOptionSchema': {'type': 'object', 'additionalProperties': False,
                'properties': {'maxInputPoints': {'type': 'integer', 'default': 200000, 'minimum': 3, 'maximum': 200000,
                    'description': 'Bootstrap only; the segmentation processes all finite source points'}}},
            'parameterSchema': {'type': 'object', 'additionalProperties': False, 'properties': {
                'normal_k': {'type': 'integer', 'default': 32, 'minimum': 3, 'maximum': 128, 'title': '法向量近邻数'},
                'display_transfer_distance': {'type': 'number', 'default': .003, 'minimum': .000001, 'maximum': .01,
                    'unit': 'm', 'title': '显示点映射容差'}}},
            'visualization': VISUALIZATION}

    def normalize_parameters(self, raw):
        raw = dict(raw or {})
        properties = self.descriptor['parameterSchema']['properties']
        if set(raw) - set(properties):
            raise RebarAlgorithmError('unknown V6 parameters: ' + ', '.join(sorted(set(raw) - set(properties))))
        result = {}
        for name, prop in properties.items():
            value = raw.get(name, prop['default'])
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not np.isfinite(value) or not prop['minimum'] <= value <= prop['maximum']:
                raise RebarAlgorithmError(f'invalid {name}')
            if prop['type'] == 'integer' and int(value) != value:
                raise RebarAlgorithmError(f'{name} must be an integer')
            result[name] = int(value) if prop['type'] == 'integer' else float(value)
        return result

    def analyze(self, sample, parameters):
        points = np.asarray(sample, np.float64)
        if points.ndim != 2 or points.shape[1:] != (3,):
            raise RebarAlgorithmError('sample must contain XYZ records')
        valid = np.isfinite(points).all(axis=1)
        return self.analyze_source(RebarInputContext(points[valid], lambda: iter([(np.flatnonzero(valid).astype(np.uint64), points[valid])])), parameters)

    def analyze_source(self, context, parameters):
        if context.bim_prior is not None:
            raise RebarAlgorithmError('V6 does not accept spatial BIM priors')
        parameters = self.normalize_parameters(parameters)
        runtime = Runtime()
        try:
            count = 0
            fingerprint = hashlib.sha256()
            previous = -1
            with (runtime.path/'xyz.bin').open('wb') as xyz, (runtime.path/'indices.bin').open('wb') as indices:
                for ids, points in context.iter_chunks():
                    ids, points = np.asarray(ids, '<u8'), np.asarray(points, '<f8')
                    if points.shape != (len(ids), 3) or not np.isfinite(points).all():
                        raise RebarAlgorithmError('source chunks must contain finite indexed XYZ')
                    if not len(ids):
                        continue
                    if int(ids[0]) <= previous or np.any(ids[1:] <= ids[:-1]):
                        raise RebarAlgorithmError('source indices must be strictly increasing')
                    previous = int(ids[-1])
                    count += len(ids)
                    if count > MAX_SOURCE_POINTS:
                        raise SpatialBudgetExceeded('V6 full-source point budget exceeded')
                    points.tofile(xyz); ids.tofile(indices)
                    fingerprint.update(ids.tobytes()); fingerprint.update(points.tobytes())
            if count < 3:
                raise RebarAlgorithmError('at least three finite source points are required')
            runtime.points = np.memmap(runtime.path/'xyz.bin', dtype='<f8', mode='r', shape=(count, 3))
            runtime.source_indices = np.memmap(runtime.path/'indices.bin', dtype='<u8', mode='r', shape=(count,))
            runtime.fingerprint = fingerprint.hexdigest()
            runtime.workers = max(1, min(available_workers(), int(os.environ.get('REBAR_SPATIAL_WORKERS', '4'))))
            def progress(stage, done, total):
                if done == 0:
                    logging.getLogger(__name__).info('V6 %s', stage)
            with threadpool_limits(limits=1):
                runtime.stages = segment_points(runtime.points, runtime.path, k=parameters['normal_k'],
                    workers=runtime.workers, source=context.source_path, through_step=6, progress=progress,
                    dimension_priors=context.dimension_priors)
            report = runtime.stages.internal_rebar
            instances = instance_records(report)
            maximum = max([0] + [i['id'] for i in instances])
            runtime.roles = np.zeros(maximum + 1, np.uint8)
            runtime.directions = np.zeros(maximum + 1, np.uint16)
            for item in instances:
                runtime.roles[item['id']] = {'unresolved': 0, 'planar': 1, 'web': 2}[item['role']]
                parts = item['observedSegments']
                if parts:
                    delta = np.diff(np.array(parts[0]['points']), axis=0)[0]
                    runtime.directions[item['id']] = int(np.argmax(np.abs(delta))) + 1
                    item['directionId'] = int(runtime.directions[item['id']])
            return RebarAnalysis({'instances': instances, 'intersections': [], 'connections': [],
                'diagnostics': {'pipelineVersion': PIPELINE_VERSION, 'throughStep': 6, 'sourcePointCount': count,
                    'timings': runtime.stages.timing, 'counts': report['counts'],
                    'dimensionPriors': runtime.stages.context.dimension_priors,
                    'classificationPolicy': 'shared stages through UI Step 05; inner and exterior steel receive score-aware multiview and fixture-density review'},
                'algorithmDetails': {'parameters': parameters, 'internalRebar': report}}, runtime)
        except Exception:
            runtime.close()
            raise

    def project_points(self, points_xyz, analysis):
        points = np.asarray(points_xyz, np.float64)
        if points.ndim != 2 or points.shape[1:] != (3,) or not np.isfinite(points).all():
            raise RebarAlgorithmError('display points must contain finite XYZ')
        runtime = analysis.resources
        distance, nearest = runtime.stages.context.tree.query(points, workers=runtime.workers)
        attrs = source_attributes(runtime, nearest)
        outside = distance > analysis.data['algorithmDetails']['parameters']['display_transfer_distance']
        # Tiles get a bounded transfer of measured labels; no renewed classification.
        values = {name: value.copy() for name, value in vars(attrs).items()}
        for value in values.values():
            value[outside] = 0
        return RebarPointAttributes(**values)

    def export_sidecars(self, directory, analysis):
        directory = Path(directory)
        runtime = analysis.resources
        context = runtime.stages.context
        folders = {name: directory/name for name in ('features', 'labels')}
        for folder in folders.values():
            folder.mkdir()
        chunks = {name: [] for name in folders}
        descriptions = {}
        counts = np.zeros(5, np.int64); roles = np.zeros(3, np.int64); ambiguous = 0
        for ordinal, start in enumerate(range(0, len(runtime.points), CHUNK_SIZE)):
            rows = slice(start, min(start + CHUNK_SIZE, len(runtime.points)))
            ids = runtime.source_indices[rows]
            attrs = source_attributes(runtime, rows)
            fused_score = getattr(context, 'fused_steel_score', None)
            fused_evidence = getattr(context, 'fused_steel_evidence', None)
            payloads = {
                'features': {'source_index': ids, 'surface_normal': context.normals[rows],
                    'surface_valid': context.normal_valid[rows], 'surface_curvature': context.curvature[rows],
                    'surface_radius': context.neighbor_radius[rows]},
                'labels': {'source_index': ids, **vars(attrs),
                    'complete_cluster': np.zeros(len(ids), np.uint32), 'complete_segment': context.internal_segment[rows],
                    'internal_type': context.internal_type[rows],
                    'fused_steel_score': (np.zeros(len(ids), np.float32) if fused_score is None else
                                          np.asarray(fused_score[rows], np.float32)),
                    'fused_steel_evidence': (np.zeros(len(ids), np.uint8) if fused_evidence is None else
                                             np.asarray(fused_evidence[rows], np.uint8))},
            }
            for kind, payload in payloads.items():
                path = folders[kind]/f'{ordinal:06d}.npz'
                np.savez_compressed(path, **payload)
                chunks[kind].append({'path': path.name, 'count': len(ids), 'firstSourceIndex': int(ids[0]),
                    'lastSourceIndex': int(ids[-1]), 'sha256': hashlib.sha256(path.read_bytes()).hexdigest()})
                descriptions[kind] = {k: {'dtype': str(v.dtype), 'shape': list(v.shape[1:])} for k, v in payload.items()}
            counts += np.bincount(attrs.scene_class, minlength=5)
            roles += np.bincount(attrs.rebar_role[attrs.scene_class == 2], minlength=3)
            ambiguous += int(np.count_nonzero(attrs.rebar_flags & 2))
        summary = {'sourceFingerprint': runtime.fingerprint, 'indexSpace': 'source-reader-record',
            'finitePointCount': len(runtime.points), 'ambiguousPointCount': ambiguous,
            'sceneClassCounts': dict(zip(('unknown','table','rebar','noise','fixture'), map(int, counts))),
            'fixtureKindCounts': {'unknown': int(counts[4]), 'squareTube': 0, 'plate': 0, 'bolt': 0},
            'rebarRoleCounts': dict(zip(('unresolved','planar','web'), map(int, roles)))}
        for kind, folder in folders.items():
            manifest = {**summary, 'schema': 'rebar-features-v1' if kind == 'features' else 'rebar-raw-labels-v3',
                'algorithm': {'id':'geometric-v6', 'version': VERSION}, 'pipelineVersion': PIPELINE_VERSION,
                'attributes': descriptions[kind], 'chunks': chunks[kind]}
            (folder/'manifest.json').write_text(json.dumps(manifest, indent=2))
        return summary

    def close(self, analysis):
        if analysis.resources is not None:
            analysis.resources.close()
