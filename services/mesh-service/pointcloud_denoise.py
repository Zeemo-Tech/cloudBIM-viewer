"""Production artifact publisher for the full, design-guided workbench algorithm."""
from copy import deepcopy
import json
import os
from pathlib import Path
import tempfile
import time

import laspy
import numpy as np
from threadpoolctl import threadpool_limits

from algorithms.pointcloud_normals import available_workers
from algorithms.preprocessed_las import PROVENANCE_RECORD_ID, PROVENANCE_USER_ID, provenance_vlr
from algorithms.pointcloud_segmentation import segment_points, VERSION as SEGMENT_VERSION
from algorithms.design_guided_instances import refine_instances, VERSION as GUIDED_VERSION
from algorithms.rebar_extension import ATTRIBUTES as COMPLETE_ATTRIBUTES
from pointcloud_step_pipeline import load_positions, source_stamp, source_hash, atomic_json
from rebar_poc import _publish_shared_artifact_permissions
from rebar_design_prior import prepare_snapshot
from rebar_design_inputs import resolve_design_inputs, VERSION as INPUT_VERSION

VERSION = 'denoise-v3-instance-map+' + SEGMENT_VERSION + '+' + INPUT_VERSION + '+' + GUIDED_VERSION
INSTANCE_CONTRACT = 'rebar-instance-map-v1'
INSTANCE_DIMENSION = 'cloudbim_instance_id'
PREVIEW_LIMIT = 500_000
MAX_SOURCE_POINTS = 20_000_000


def export_result(source, destination, context, colors):
    """Keep actual source records, in original scan coordinates, with original dimensions."""
    classes = np.asarray(context.complete_class)
    counts = np.bincount(classes, minlength=5)
    if len(counts) != 5 or int(counts[3]) == 0:
        raise ValueError('去噪后没有可用于偏差计算的钢筋点，请检查设计模型和配准')
    with laspy.open(source) as reader:
        header = deepcopy(reader.header)
        if INSTANCE_DIMENSION in header.point_format.dimension_names:
            raise ValueError('源 LAS 已包含 cloudbim_instance_id；请从原始点云重新执行去噪')
        header.add_extra_dim(laspy.ExtraBytesParams(
            name=INSTANCE_DIMENSION, type=np.uint32,
            description='CloudBIM rebar instance identity',
        ))
        matches = [vlr for vlr in header.vlrs
                   if vlr.user_id.rstrip('\x00') == PROVENANCE_USER_ID and vlr.record_id == PROVENANCE_RECORD_ID]
        if matches:
            if len(matches) != 1:
                raise ValueError('预处理 LAS 的 provenance VLR 数量非法')
            try:
                provenance = json.loads(bytes(matches[0].record_data).decode('ascii'))
            except (UnicodeDecodeError, json.JSONDecodeError, TypeError) as exc:
                raise ValueError('预处理 LAS 的 provenance VLR 无法解析') from exc
            parent_role = provenance.get('artifactRole')
            provenance.update(artifactRole='denoised-steel', parentArtifactRole=parent_role,
                              parentPointCount=len(classes), pointCount=int(counts[3]),
                              subsetSemantics='design-guided steel points (complete_class=3)',
                              derivedByAlgorithmVersion=VERSION)
            header.vlrs[:] = [vlr for vlr in header.vlrs
                              if not (vlr.user_id.rstrip('\x00') == PROVENANCE_USER_ID
                                      and vlr.record_id == PROVENANCE_RECORD_ID)]
            header.vlrs.append(provenance_vlr(provenance))
        with laspy.open(destination/'cleaned.las', mode='w', header=header, do_compress=False) as writer:
            offset = 0
            for chunk in reader.chunk_iterator(262144):
                stop = offset + len(chunk)
                retained = classes[offset:stop] == 3
                source_points = chunk[retained]
                output_points = laspy.ScaleAwarePointRecord.zeros(len(source_points), header=header)
                for dimension in chunk.point_format.dimension_names:
                    output_points[dimension] = source_points[dimension]
                output_points[INSTANCE_DIMENSION] = np.asarray(
                    context.complete_instance[offset:stop][retained], dtype=np.uint32,
                )
                writer.write_points(output_points)
                offset = stop
            if offset != len(classes):
                raise ValueError('导出期间源点云发生变化')
            if reader.evlrs:
                writer.write_evlrs(reader.evlrs)
    # A source-space local origin avoids float32 precision loss for survey coordinates.
    ids = np.linspace(0, len(classes)-1, min(len(classes), PREVIEW_LIMIT), dtype=np.int64)
    origin = (context.positions.min(axis=0) + context.positions.max(axis=0))/2
    vertices = np.empty(len(ids), dtype=[('x','<f4'),('y','<f4'),('z','<f4'),('red','u1'),('green','u1'),('blue','u1'),('label','u1'),('instance','<u4')])
    for axis, name in enumerate(('x','y','z')):
        vertices[name] = context.positions[ids, axis] - origin[axis]
    for axis, name in enumerate(('red','green','blue')):
        vertices[name] = colors[ids, axis]
    vertices['label'] = classes[ids]
    vertices['instance'] = context.complete_instance[ids]
    with (destination/'preview.ply').open('wb') as stream:
        stream.write(('ply\nformat binary_little_endian 1.0\nelement vertex %d\n' % len(ids) +
                      'property float x\nproperty float y\nproperty float z\nproperty uchar red\nproperty uchar green\nproperty uchar blue\nproperty uchar label\nproperty uint instance\nend_header\n').encode())
        vertices.tofile(stream)
    return {'pointsBefore': len(classes), 'pointsAfter': int(counts[3]),
            'counts': dict(zip(('unknown','table','fixture','steel','noise'), map(int, counts))),
            'previewPointCount': len(ids), 'previewOrigin': origin.tolist()}


def _instance_map(report):
    """Persist the complete design-bar directory and only explicit instance links."""
    inventory = report.get('designReview', {}).get('inventory')
    if not isinstance(inventory, dict):
        raise ValueError('去噪报告缺少设计钢筋目录')
    units = inventory.get('units')
    bars = inventory.get('bars')
    if not isinstance(units, list) or not isinstance(bars, list):
        raise ValueError('去噪报告中的设计钢筋目录无效')
    bar_rows = []
    for row in bars:
        if not isinstance(row, dict):
            raise ValueError('设计钢筋目录包含无效条目')
        design_id = row.get('designBarId')
        guid = row.get('ifcGlobalId')
        if not isinstance(design_id, str) or not design_id or not isinstance(guid, str) or not guid.strip():
            raise ValueError('设计钢筋缺少 designBarId 或 IFC GlobalId')
        bar_rows.append({
            'designBarId': design_id, 'ifcGlobalId': guid,
            'name': str(row.get('name') or ''),
            'unitIds': list(row.get('unitIds') or []),
            'coverage': str(row.get('coverage') or 'unresolved'),
        })
    if len({row['designBarId'] for row in bar_rows}) != len(bar_rows):
        raise ValueError('设计钢筋目录包含重复 designBarId')
    instance_rows = []
    for row in report.get('instances', []):
        if not isinstance(row, dict) or type(row.get('id')) is not int or row['id'] <= 0:
            raise ValueError('去噪报告包含无效实例 ID')
        instance_rows.append({
            'id': row['id'],
            'designBarId': row.get('designBarId') if isinstance(row.get('designBarId'), str) else None,
            'designUnitId': row.get('designUnitId') if isinstance(row.get('designUnitId'), str) else None,
            'reviewStatus': str(row.get('reviewStatus') or 'pending'),
            'pointCount': int(row.get('pointCount') or 0),
        })
    if len({row['id'] for row in instance_rows}) != len(instance_rows):
        raise ValueError('去噪报告包含重复实例 ID')
    return {
        'schema': INSTANCE_CONTRACT,
        'instances': instance_rows,
        'inventory': {
            'bars': bar_rows,
            'units': units,
        },
    }


def build_denoise(source, ifc, model, transform, destination, k=32):
    started = time.perf_counter()
    stamps = [source_stamp(p) for p in (source, ifc, model)]
    with source.open('rb') as stream:
        if stream.read(4) != b'LASF':
            raise ValueError('设计辅助去噪目前仅支持 LAS/LAZ 点云')
    with laspy.open(source) as reader:
        if not 3 <= reader.header.point_count <= MAX_SOURCE_POINTS:
            raise ValueError('点云去噪支持 3 至 2000 万个 LAS/LAZ 源点')
    destination.mkdir(parents=True, exist_ok=False)
    # Scratch arrays and intermediate workbench exports never become public artifacts.
    try:
        with tempfile.TemporaryDirectory(prefix='denoise-') as scratch:
            scratch = Path(scratch)
            digest = source_hash(source)
            config = scratch/'design.json'
            atomic_json(config, {'sourcePath': str(source), 'sourceSha256': digest,
                                 'ifcPath': str(ifc), 'modelPath': str(model), 'scanToBim': transform})
            snapshot = prepare_snapshot(config)
            inputs = resolve_design_inputs(snapshot, source, digest)
            if not inputs.inventory or not inputs.inventory.get('units'):
                raise ValueError('设计模型中没有可解析的钢筋构件，无法执行设计辅助去噪')
            progress = lambda stage, done, total: None
            positions, colors = load_positions(source, scratch, progress)
            workers = max(1, min(available_workers(), int(os.environ.get('REBAR_SPATIAL_WORKERS', '4'))))
            with threadpool_limits(limits=1):
                stages = segment_points(positions, scratch, k=k, workers=workers, through_step=6,
                                        source=source, design_inventory=inputs.inventory,
                                        dimension_priors=inputs.dimensions, progress=progress)
                arrays = {name: np.lib.format.open_memmap(scratch/f'{name}.npy', mode='w+', dtype=dtype, shape=(len(positions),))
                          for name, dtype in COMPLETE_ATTRIBUTES.items()}
                report = refine_instances(stages.context, stages.internal_rebar, inputs.inventory,
                                          mode='topology', workers=workers, output=arrays, progress=progress)
            result = export_result(source, destination, stages.context, colors)
            instance_map = _instance_map(report)
            atomic_json(destination/'instance-map.json', instance_map)
            instances_hash = source_hash(destination/'instance-map.json')
            result.update(algorithmVersion=VERSION, sourceSha256=digest, designFingerprint=snapshot['fingerprint'],
                          normalK=k, elapsedSeconds=time.perf_counter()-started,
                          instanceCount=len(report.get('instances', [])),
                          instanceContract=INSTANCE_CONTRACT, instancesSha256=instances_hash)
            if stamps != [source_stamp(p) for p in (source, ifc, model)]:
                raise ValueError('计算期间点云或设计模型发生变化，请重新计算')
            atomic_json(destination/'manifest.json', result)
            _publish_shared_artifact_permissions(destination)
            return result
    except BaseException:
        import shutil
        shutil.rmtree(destination, ignore_errors=True)
        raise
