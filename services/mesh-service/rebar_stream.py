"""Bounded raw-point I/O and index-addressable classification artifacts."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Iterator

import numpy as np

from algorithms.rebar_base import RebarAlgorithm, RebarAnalysis, RebarInputContext

CHUNK_SIZE = 100_000
SCENE_NAMES = ('clutter', 'table', 'rebar', 'statisticalNoise', 'fixture_formwork')


def iter_source_chunks(path: str, file_format: str | None = None,
                       chunk_size: int = CHUNK_SIZE) -> Iterator[tuple[np.ndarray, np.ndarray]]:
    """Source paths must be confined by the artifact boundary before this call."""
    fmt = (file_format or Path(path).suffix.lstrip('.')).lower()
    if fmt in ('las', 'laz'):
        import laspy
        offset = 0
        with laspy.open(path) as source:
            for records in source.chunk_iterator(chunk_size):
                xyz = np.column_stack((records.x, records.y, records.z)).astype(np.float64)
                indices = np.arange(offset, offset + len(xyz), dtype=np.uint64)
                offset += len(xyz)
                finite = np.isfinite(xyz).all(axis=1)
                yield indices[finite], xyz[finite]
        return
    # Open3D's PLY/PCD decoder is materializing, as in the legacy reader. The
    # processing interface remains bounded, and the limitation is reported.
    import open3d as o3d
    cloud = o3d.io.read_point_cloud(str(path), format=fmt)
    points = np.asarray(cloud.points)
    for start in range(0, len(points), chunk_size):
        xyz = np.asarray(points[start:start + chunk_size], dtype=np.float64)
        indices = np.arange(start, start + len(xyz), dtype=np.uint64)
        finite = np.isfinite(xyz).all(axis=1)
        yield indices[finite], xyz[finite]


def write_raw_labels(directory: Path, context: RebarInputContext,
                     algorithm: RebarAlgorithm, analysis: RebarAnalysis) -> dict:
    directory.mkdir()
    counts = np.zeros(5, dtype=np.int64)
    total = ambiguous = 0
    instance_counts: dict[int, int] = {}
    chunks = []
    for indices, xyz in context.iter_chunks():
        if not len(xyz):
            continue
        attrs = algorithm.project_points(xyz, analysis)
        attrs.validate(len(xyz))
        scene = attrs.scene_class if attrs.scene_class is not None else (attrs.rebar_class > 0).astype(np.uint8) * 2
        flags = attrs.rebar_flags if attrs.rebar_flags is not None else np.zeros(len(xyz), np.uint8)
        if np.any(scene > 4):
            raise ValueError('unrecognized scene class in raw labels')
        payload = dict(source_index=indices.astype('<u8'), scene_class=scene,
                       rebar_class=attrs.rebar_class, rebar_direction=attrs.rebar_direction.astype('<u2'),
                       rebar_instance=attrs.rebar_instance.astype('<u4'), rebar_flags=flags)
        candidate_method = getattr(algorithm, 'project_candidates', None)
        if np.any(flags & 2):
            if candidate_method is None:
                raise ValueError('ambiguous labels require candidate instance memberships')
            candidates = candidate_method(xyz, analysis)
            if not isinstance(candidates, dict) or set(candidates) != {'point_indices', 'offsets', 'instance_ids'}:
                raise ValueError('invalid ambiguity CSR fields')
            rows, offsets, ids = (np.asarray(candidates[k]) for k in ('point_indices','offsets','instance_ids'))
            if any(v.ndim != 1 or v.dtype.kind not in 'iu' for v in (rows, offsets, ids)):
                raise ValueError('ambiguity CSR must contain integer vectors')
            if not np.array_equal(rows, np.flatnonzero(flags & 2)):
                raise ValueError('candidate rows must match ambiguous source records')
            if len(offsets) != len(rows)+1 or offsets[0] != 0 or offsets[-1] != len(ids) or np.any(np.diff(offsets.astype(np.int64)) < 2):
                raise ValueError('invalid ambiguity CSR offsets')
            if np.any(ids <= 0) or np.any(ids >= 0xffffffff):
                raise ValueError('candidate memberships require physical instance IDs')
            for start, end in zip(offsets[:-1], offsets[1:]):
                if len(np.unique(ids[int(start):int(end)])) != int(end-start):
                    raise ValueError('duplicate candidate instance ID')
            payload.update(candidate_point_indices=rows.astype('<u4'), candidate_offsets=offsets.astype('<u8'), candidate_instance_ids=ids.astype('<u4'))
        name = f'{len(chunks):06d}.npz'
        path = directory / name
        np.savez_compressed(path, **payload)
        chunks.append(dict(path=name, count=len(xyz), firstSourceIndex=int(indices[0]),
                           lastSourceIndex=int(indices[-1]), sha256=hashlib.sha256(path.read_bytes()).hexdigest()))
        counts += np.bincount(scene, minlength=5)
        total += len(xyz)
        ambiguous += int(np.count_nonzero(flags & 2))
        known = attrs.rebar_instance[(scene == 2) & (attrs.rebar_instance > 0) & (attrs.rebar_instance < 0xffffffff)]
        identifiers, support = np.unique(known, return_counts=True)
        for identifier, count in zip(identifiers, support):
            instance_counts[int(identifier)] = instance_counts.get(int(identifier), 0) + int(count)
    manifest = dict(schema='rebar-raw-labels-v1', indexSpace='source-reader-record',
                    finitePointCount=total, ambiguousPointCount=ambiguous,
                    instanceCount=len(instance_counts), instancePointCounts=instance_counts,
                    sceneClassCounts=dict(zip(SCENE_NAMES, map(int, counts))), chunks=chunks,
                    attributes={'source_index':'uint64', 'scene_class':'uint8', 'rebar_class':'uint8',
                                'rebar_direction':'uint16', 'rebar_instance':'uint32', 'rebar_flags':'uint8'},
                    candidates={'encoding':'sparse-csr', 'point_indices':'uint32 local row in source_index',
                                'offsets':'uint64', 'instance_ids':'uint32'})
    (directory / 'manifest.json').write_text(json.dumps(manifest, indent=2), encoding='utf-8')
    return {key:value for key,value in manifest.items() if key not in ('chunks', 'attributes')}
