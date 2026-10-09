"""Publish one immutable, full-scene classified tileset from a no-BIM run.

Run labels retain source record order. A rigid source-to-analysis matrix is
validated against EVERY source row before any labels are attached to tiles.
This never creates separate table/fixture/steel assets or modifies source tiles.
"""
from __future__ import annotations
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import tempfile

import laspy
import numpy as np
from scipy.spatial import cKDTree

from algorithms.rebar_base import RebarPointAttributes
from artifact_permissions import publish_shared_artifact_permissions
from pointcloud_tile_sidecar import referenced_pnts, _coordinate_tolerance
from rebar_tiles import rewrite_pnts

SCHEMA = 'pointcloud-segmentation-preview-v1'


def publish_preview(source: Path, source_tiles: Path, run: Path, destination: Path, source_to_analysis=None):
    source, source_tiles, run, destination = map(Path, (source, source_tiles, run, destination))
    if destination.exists():
        raise FileExistsError('preview destination must be new')
    manifest = json.loads((run / 'manifest.json').read_text())
    internal = manifest.get('internalRebar') or {}
    if (not manifest.get('completed') or manifest.get('priorMode') != 'off'
            or internal.get('diagnostics', {}).get('scopeMode') != 'all-steel'):
        raise ValueError('a completed, full-scene model-free instance run is required')
    transform = np.asarray(source_to_analysis if source_to_analysis is not None else np.eye(4), float)
    if (transform.shape != (4, 4) or not np.isfinite(transform).all()
            or not np.allclose(transform[3], [0, 0, 0, 1])
            or not np.allclose(transform[:3, :3] @ transform[:3, :3].T, np.eye(3), atol=1e-8)
            or not np.isclose(np.linalg.det(transform[:3, :3]), 1)):
        raise ValueError('source-to-analysis transform must be rigid')
    positions = np.load(run / 'positions.npy', mmap_mode='r')
    classes = np.load(run / 'refined_class.npy', mmap_mode='r')
    types = np.load(run / 'internal_type.npy', mmap_mode='r')
    instances = np.load(run / 'internal_instance.npy', mmap_mode='r')
    count = len(positions)
    if any(v.shape != (count,) for v in (classes, types, instances)) or manifest['source']['pointCount'] != count:
        raise ValueError('source label count mismatch')
    with source.open('rb') as stream:
        digest = hashlib.file_digest(stream, 'sha256').hexdigest()
    # The source may be an unrotated upload; comparison uses record identity,
    # never an unverified nearest-neighbour transfer between different scans.
    raw_positions = np.empty((count, 3), np.float64)
    offset, max_error = 0, 0.
    with laspy.open(source) as reader:
        if reader.header.point_count != count:
            raise ValueError('source point count mismatch')
        for chunk in reader.chunk_iterator(262144):
            end = offset + len(chunk)
            xyz = np.column_stack((chunk.x, chunk.y, chunk.z))
            raw_positions[offset:end] = xyz
            error = np.linalg.norm(xyz @ transform[:3, :3].T + transform[:3, 3] - positions[offset:end], axis=1)
            max_error = max(max_error, float(error.max(initial=0)))
            offset = end
    if max_error > 2e-5:
        raise ValueError(f'run coordinates/record order do not match the uploaded source: {max_error:g} m')
    labels = np.asarray(classes).copy()
    labels[types == 5] = 4
    tree = cKDTree(raw_positions)
    conflicts = 0

    def project(points):
        nonlocal conflicts
        distance, nearest = tree.query(points, k=2, workers=1)
        tolerance = _coordinate_tolerance(points)
        if np.any(distance[:, 0] > tolerance):
            raise ValueError('tile coordinates do not match source')
        ids = nearest[:, 0]
        cls, inst = labels[ids].copy(), np.asarray(instances[ids], np.uint32).copy()
        ambiguous = distance[:, 1] - distance[:, 0] <= max(tolerance * .01, 1e-10)
        # Coincident source rows with conflicting labels cannot claim a unique
        # owner. Retain the point, explicitly unassigned, instead of guessing.
        conflict = ambiguous & ((labels[ids] != labels[nearest[:, 1]]) | (instances[ids] != instances[nearest[:, 1]]))
        inst[conflict] = 0
        cls[conflict & (labels[ids] != labels[nearest[:, 1]])] = 0
        conflicts += int(conflict.sum())
        return RebarPointAttributes(cls.astype(np.uint8), np.zeros(len(ids), np.uint16), inst)

    plane = manifest.get('preprocessing', {}).get('tableRemoval', {}).get('plane')
    source_plane = None
    if plane is not None:
        normal = np.array([-plane['slopes'][0], -plane['slopes'][1], 1.])
        normal /= np.linalg.norm(normal)
        origin = (np.asarray(plane['origin']) - transform[:3, 3]) @ transform[:3, :3]
        source_plane = {'origin': origin.tolist(), 'normal': (normal @ transform[:3, :3]).tolist(),
                        'clearanceM': float(plane['clearanceM'])}
    destination.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix='.segmentation-preview-', dir=destination.parent))
    try:
        shutil.copytree(source_tiles, staging / 'tiles')
        tile_points = 0
        for tile, _ in referenced_pnts(staging / 'tiles'):
            tile_points += rewrite_pnts(tile, project)
        report = {'schema': SCHEMA, 'algorithmVersion': manifest['algorithmVersion'], 'runId': manifest['runId'],
                  'sourceSha256': digest, 'sourcePointCount': count, 'plane': source_plane,
                  'sourceToAnalysisRowMajor': transform.tolist(), 'coordinateMatchMaxErrorM': max_error,
                  'instanceCount': internal['instanceCount'], 'assignedPointCount': int(np.count_nonzero(instances)),
                  'instanceQuality': {'wholeBarValidated': False,
                                      'unit': 'local-cylinder-track',
                                      'shortCandidateCount': sum(i['lengthM'] < .2 for i in internal.get('instances', []))},
                  'classCounts': {str(i): int(np.count_nonzero(labels == i)) for i in range(5)},
                  'tilesPointCount': tile_points, 'ambiguousTilePointCount': conflicts,
                  'displayContract': 'one source-coordinate tileset; per-point colors; no asset split',
                  'instanceScope': internal['diagnostics']['scope'], 'designModelUsed': False}
        (staging / 'manifest.json').write_text(json.dumps(report, ensure_ascii=False, indent=2))
        publish_shared_artifact_permissions(staging)
        staging.rename(destination)
        return report
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        raise


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--source-tiles', type=Path, required=True)
    parser.add_argument('--run', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--leveling', type=Path, help='JSON with sourceToLeveledRowMajor, identity when omitted')
    args = parser.parse_args()
    transform = json.loads(args.leveling.read_text())['sourceToLeveledRowMajor'] if args.leveling else None
    result = publish_preview(args.source, args.source_tiles, args.run, args.output, transform)
    print(json.dumps(result, ensure_ascii=False))
