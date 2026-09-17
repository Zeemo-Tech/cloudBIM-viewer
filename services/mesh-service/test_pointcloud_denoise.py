from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import patch
import json

import laspy
import numpy as np
from fastapi import HTTPException
from pydantic import ValidationError

from pointcloud_denoise import (_apply_control_result, _control_envelope, _instance_map,
                                build_denoise, export_result, CONTROL_CONTRACT,
                                CONTROL_VERSION, VERSION)
from pointcloud_preprocess import build_preprocess
from algorithms.preprocessed_las import PROVENANCE_RECORD_ID, PROVENANCE_USER_ID
from algorithms.rebar_control_net import fit_control_net
from pointcloud_denoise_api import create_denoise_router, DenoiseRequest
from test_rebar_control_net import inventory as control_inventory, tube


class DenoiseTests(unittest.TestCase):
    def test_instance_map_keeps_complete_physical_bar_directory(self):
        inventory = {
            'bars': [
                {'designBarId': 'bar-a', 'ifcGlobalId': 'A', 'name': 'A', 'unitIds': ['u-a', 'u-b'], 'coverage': 'complete'},
                {'designBarId': 'bar-pending', 'ifcGlobalId': 'P', 'name': 'P', 'unitIds': ['u-p'], 'coverage': 'complete'},
                {'designBarId': 'bar-unresolved', 'ifcGlobalId': 'U', 'name': 'U', 'unitIds': [], 'coverage': 'unresolved'},
            ],
            'units': [
                {'designBarId': 'bar-a', 'designUnitId': 'u-a', 'kind': 'straight'},
                {'designBarId': 'bar-a', 'designUnitId': 'u-b', 'kind': 'short'},
                {'designBarId': 'bar-pending', 'designUnitId': 'u-p', 'kind': 'web'},
            ],
            'relations': [{'from': 'u-a', 'to': 'u-b', 'kind': 'next'}],
        }
        report = {
            'instances': [
                {'id': 1, 'designBarId': 'bar-a', 'designUnitId': 'u-a', 'status': 'fitted', 'pointCount': 20},
                {'id': 2, 'designBarId': 'bar-a', 'designUnitId': 'u-b', 'status': 'missing', 'pointCount': 0},
                {'id': 3, 'designBarId': 'bar-pending', 'designUnitId': 'u-p', 'status': 'pending', 'pointCount': 0},
            ],
            'version': CONTROL_VERSION,
        }
        envelope = _control_envelope(report)
        value = _instance_map(report, inventory, envelope)
        self.assertEqual(value['schema'], 'rebar-instance-map-v1')
        self.assertEqual(value['inventory'], inventory)
        self.assertEqual([bar['designBarId'] for bar in value['inventory']['bars']], ['bar-a', 'bar-pending', 'bar-unresolved'])
        self.assertEqual(value['instances'][0]['designUnitId'], 'u-a')
        self.assertEqual([row['reviewStatus'] for row in value['instances']], ['matched', 'missing', 'ambiguous'])
        self.assertIs(value['controlNet'], envelope)

    def test_control_status_maps_to_production_preview_classes_without_recomputing_owners(self):
        context = SimpleNamespace(positions=np.zeros((5, 3)))
        report = {'instances': [{'id': value, 'status': 'fitted' if value == 9 else 'missing'}
                                for value in range(1, 10)]}
        status = np.arange(5, dtype=np.uint8)
        owner = np.array([0, 9, 0, 0, 0], dtype=np.uint32)
        classes, installed_owner = _apply_control_result(
            context, report, {'control_status': status, 'control_instance': owner})
        np.testing.assert_array_equal(classes, [1, 3, 0, 4, 2])
        self.assertIs(context.complete_instance, owner)
        self.assertIs(installed_owner, owner)

    def test_build_uses_post_table_control_fit_and_publishes_identical_envelope(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source, ifc, model, destination = root/'source.las', root/'design.ifc', root/'model.glb', root/'result'
            cloud = laspy.LasData(laspy.LasHeader(point_format=3, version='1.2'))
            cloud.x, cloud.y, cloud.z = [0, .1, .2, .3], [0, 0, 0, 0], [0, .01, .02, .03]
            cloud.write(source); ifc.write_text('ifc'); model.write_bytes(b'glb')
            transform = np.eye(4).ravel(order='F').tolist()
            inventory = {'bars': [{'designBarId': 'b', 'ifcGlobalId': 'G', 'unitIds': ['u'], 'coverage': 'complete'}],
                         'units': [{'designBarId': 'b', 'designUnitId': 'u'}], 'relations': []}
            snapshot = {'fingerprint': 'snapshot', 'modelInfo': {'scanToBim': transform}, 'inventory': inventory}
            inputs = SimpleNamespace(inventory=inventory, dimensions={'available': True}, report={})
            positions = np.c_[cloud.x, cloud.y, cloud.z]
            context = SimpleNamespace(positions=positions, normals=np.ones((4, 3)),
                                      shared_table_mask=np.array([1, 0, 0, 0], np.uint8))
            stages = SimpleNamespace(context=context)
            fit_report = {'version': CONTROL_VERSION,
                          'instances': [{'id': 1, 'designBarId': 'b', 'designUnitId': 'u',
                                         'status': 'fitted', 'pointCount': 1}],
                          'counts': {'fittedUnits': 1}}
            fit_arrays = {'control_status': np.array([0, 1, 2, 3], np.uint8),
                          'control_instance': np.array([0, 1, 0, 0], np.uint32)}

            def prepare(path):
                config = json.loads(path.read_text())
                self.assertEqual(config['scanToBim'], transform)
                return snapshot

            def exported(_source, _destination, value, _colors):
                np.testing.assert_array_equal(value.complete_class, [1, 3, 0, 4])
                np.testing.assert_array_equal(value.complete_instance, fit_arrays['control_instance'])
                return {'pointsBefore': 4, 'pointsAfter': 1, 'counts': {}}

            with patch('pointcloud_denoise.prepare_snapshot', side_effect=prepare), \
                 patch('pointcloud_denoise.resolve_design_inputs', return_value=inputs), \
                 patch('pointcloud_denoise.load_positions', return_value=(positions, np.zeros((4, 3), np.uint8))), \
                 patch('pointcloud_denoise.segment_points', return_value=stages) as segment, \
                 patch('pointcloud_denoise.fit_control_net', return_value=(fit_report, fit_arrays)) as fit, \
                 patch('pointcloud_denoise.export_result', side_effect=exported), \
                 patch('pointcloud_denoise.available_workers', return_value=2), \
                 patch('pointcloud_denoise._publish_shared_artifact_permissions'):
                result = build_denoise(source, ifc, model, transform, destination, k=16)

            self.assertTrue(VERSION.startswith('denoise-v4-control-net+'))
            self.assertEqual(segment.call_args.kwargs['through_step'], 2)
            self.assertIs(segment.call_args.kwargs['stop_after_table'], True)
            self.assertEqual(fit.call_args.kwargs['mode'], 'aligned')
            self.assertIs(fit.call_args.args[0], positions)
            self.assertIs(fit.call_args.args[1], context.shared_table_mask)
            control = json.loads((destination/'control-net.json').read_text())
            mapping = json.loads((destination/'instance-map.json').read_text())
            self.assertEqual(control, mapping['controlNet'])
            self.assertEqual(control['schema'], CONTROL_CONTRACT)
            self.assertEqual(control['coordinateFrame'], 'scan')
            self.assertEqual(result['controlNetAlgorithmVersion'], CONTROL_VERSION)
            self.assertEqual(result['controlNetSha256'], __import__('hashlib').sha256((destination/'control-net.json').read_bytes()).hexdigest())
            self.assertEqual(result['matchedInstanceCount'], 1)

    def test_export_keeps_final_steel_source_records_and_attributes(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root/'source.las'
            cloud = laspy.LasData(laspy.LasHeader(point_format=3, version='1.2'))
            cloud.header.scales = [.001]*3
            cloud.header.offsets = [1000000., 2000000., 0.]
            cloud.x = np.arange(7)*.007 + 1000000
            cloud.y = np.ones(7)*2000000
            cloud.z = np.arange(7)*.003
            cloud.intensity = np.arange(7) + 200
            cloud.red = np.arange(7) + 800
            cloud.write(source)
            original = source.read_bytes()
            context = SimpleNamespace(complete_class=np.array([1,2,3,4,3,0,4],np.uint8),
                complete_instance=np.array([0,0,7,0,301,0,0],np.uint32),
                positions=np.c_[cloud.x,cloud.y,cloud.z])
            result = export_result(source,root,context,np.ones((7,3),np.uint8)*127)
            cleaned = laspy.read(root/'cleaned.las')
            for dimension in cloud.point_format.dimension_names:
                np.testing.assert_array_equal(cleaned[dimension], cloud[dimension][[2,4]])
            np.testing.assert_array_equal(cleaned['cloudbim_instance_id'], [7, 301])
            self.assertEqual(source.read_bytes(),original)
            self.assertEqual(result['pointsAfter'],2)
            self.assertEqual(result['counts']['noise'],2)
            self.assertEqual(sum(result['counts'].values()),7)
            data=(root/'preview.ply').read_bytes().split(b'end_header\n',1)[1]
            preview=np.frombuffer(data,dtype=[('xyz','<f4',3),('rgb','u1',3),('label','u1'),('instance','<u4')])
            np.testing.assert_allclose(preview['xyz']+result['previewOrigin'],context.positions,atol=1e-7,rtol=0)
            np.testing.assert_array_equal(preview['label'],context.complete_class)
            np.testing.assert_array_equal(preview['instance'],context.complete_instance)

    def test_small_synthetic_control_fit_exports_real_owner_ids_and_preserves_source(self):
        points, normals = tube([0, 0, .03], [1, 0, .03], along=64, around=16)
        inventory = control_inventory([([0, 0, .03], [1, 0, .03])])
        inventory['bars'][0]['ifcGlobalId'] = 'synthetic-guid'
        original_points = points.copy()
        report, arrays = fit_control_net(
            points, np.zeros(len(points), bool), inventory,
            mode='aligned', normals=normals, workers=1, curve_workers=1)
        context = SimpleNamespace(positions=points, normals=normals,
                                  shared_table_mask=np.zeros(len(points), np.uint8))
        _apply_control_result(context, report, arrays)
        self.assertEqual(report['instances'][0]['status'], 'fitted')
        self.assertGreater(np.count_nonzero(context.complete_instance == 1), .9*len(points))
        self.assertEqual(context.complete_instance.dtype, np.uint32)
        np.testing.assert_array_equal(points, original_points)

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root/'source.las'
            cloud = laspy.LasData(laspy.LasHeader(point_format=3, version='1.2'))
            cloud.header.scales = [.0001]*3
            cloud.x, cloud.y, cloud.z = points.T
            cloud.intensity = np.arange(len(points), dtype=np.uint16)
            cloud.write(source)
            original = source.read_bytes()
            result = export_result(source, root, context, np.zeros((len(points), 3), np.uint8))
            cleaned = laspy.read(root/'cleaned.las')
            selected = context.complete_class == 3
            np.testing.assert_array_equal(cleaned.intensity, cloud.intensity[selected])
            np.testing.assert_array_equal(cleaned["cloudbim_instance_id"], context.complete_instance[selected])
            self.assertEqual(result['pointsAfter'], int(np.count_nonzero(selected)))
            self.assertEqual(source.read_bytes(), original)
            envelope = _control_envelope(report)
            mapping = _instance_map(report, inventory, envelope)
            self.assertEqual(mapping['instances'][0]['reviewStatus'], 'matched')
            json.dumps(mapping, allow_nan=False)

    def test_empty_retained_cloud_is_not_published(self):
        context=SimpleNamespace(complete_class=np.array([1,2,4]))
        with self.assertRaisesRegex(ValueError,'没有可用于偏差计算'):
            export_result(None,None,context,None)

    def test_export_relabels_preprocessed_vlr_for_steel_subset(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / 'source.las'
            cloud = laspy.LasData(laspy.LasHeader(point_format=3, version='1.2'))
            x, y = np.meshgrid(np.linspace(0, .38, 20), np.linspace(0, .38, 20))
            table = np.column_stack((x.ravel(), y.ravel(), np.zeros(x.size)))
            upper = np.column_stack((np.linspace(.04, .34, 48), np.full(48, .19), np.full(48, .05)))
            xyz = np.concatenate((table, upper))
            cloud.x, cloud.y, cloud.z = xyz.T
            cloud.write(source)
            build_preprocess(source, root / 'preprocessed')
            cleaned_path = root / 'preprocessed' / 'cleaned.las'
            cleaned = laspy.read(cleaned_path)
            count = len(cleaned.points)
            context = SimpleNamespace(complete_class=np.where(np.arange(count) % 2, 3, 4).astype(np.uint8),
                complete_instance=np.ones(count, np.uint32), positions=np.c_[cleaned.x,cleaned.y,cleaned.z])
            export_result(cleaned_path, root, context, np.ones((count,3),np.uint8))
            result = laspy.read(root / 'cleaned.las')
            vlr = next(v for v in result.header.vlrs if v.user_id.rstrip('\x00') == PROVENANCE_USER_ID
                       and v.record_id == PROVENANCE_RECORD_ID)
            value = json.loads(bytes(vlr.record_data).decode('ascii'))
            self.assertEqual(value['artifactRole'], 'denoised-steel')
            self.assertEqual(value['parentArtifactRole'], 'cleaned')
            self.assertEqual(value['parentPointCount'], count)
            self.assertEqual(value['pointCount'], len(result.points))

    def test_http_rejects_traversal_and_nonrigid_alignment_before_computation(self):
        router=create_denoise_router()
        route=next(route for route in router.routes if route.path.endswith('/compute'))
        self.assertEqual([p.name for p in route.dependant.body_params], ['request'])
        self.assertEqual(route.dependant.query_params, [])
        with tempfile.TemporaryDirectory() as tmp, patch.dict('os.environ',{'ANALYSIS_MESH_STORAGE_ROOT':tmp}):
            root=Path(tmp)
            for name in ('source.las','design.ifc','model.glb'):(root/name).touch()
            request=dict(sourcePath=str(root/'source.las'),ifcPath=str(root/'design.ifc'),modelPath=str(root/'model.glb'),outputPath=str(root/'output'),transform=np.eye(4).ravel(order='F').tolist())
            with patch('pointcloud_denoise_api.build_denoise') as build:
                for bad in ({'outputPath':str(root/'../escape')},{'sourcePath':'/etc/passwd'},{'normalK':True},{'transform':[0.]*16}):
                    with self.assertRaises((HTTPException, ValidationError)):
                        route.endpoint(DenoiseRequest(**{**request,**bad}))
                build.assert_not_called()
                build.return_value={'ok':True}
                self.assertEqual(route.endpoint(DenoiseRequest(**request)), {'ok': True})
                self.assertEqual(build.call_args.args[3],request['transform'])


if __name__=='__main__':unittest.main()
