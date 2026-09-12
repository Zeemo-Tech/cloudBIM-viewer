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

from pointcloud_denoise import _instance_map, export_result
from pointcloud_preprocess import build_preprocess
from algorithms.preprocessed_las import PROVENANCE_RECORD_ID, PROVENANCE_USER_ID
from pointcloud_denoise_api import create_denoise_router, DenoiseRequest


class DenoiseTests(unittest.TestCase):
    def test_instance_map_keeps_complete_physical_bar_directory(self):
        report = {
            'instances': [
                {'id': 7, 'designBarId': 'bar-a', 'designUnitId': 'u-a', 'reviewStatus': 'matched', 'pointCount': 20},
                {'id': 8, 'designBarId': None, 'reviewStatus': 'pending', 'pointCount': 3},
            ],
            'designReview': {'inventory': {
                'bars': [
                    {'designBarId': 'bar-a', 'ifcGlobalId': 'A', 'name': 'A', 'unitIds': ['u-a'], 'coverage': 'complete'},
                    {'designBarId': 'bar-missing', 'ifcGlobalId': 'M', 'name': 'M', 'unitIds': ['u-m'], 'coverage': 'complete'},
                    {'designBarId': 'bar-unresolved', 'ifcGlobalId': 'U', 'name': 'U', 'unitIds': [], 'coverage': 'unresolved'},
                ],
                'units': [
                    {'designBarId': 'bar-a', 'designUnitId': 'u-a', 'kind': 'straight'},
                    {'designBarId': 'bar-missing', 'designUnitId': 'u-m', 'kind': 'straight'},
                ],
            }},
        }
        value = _instance_map(report)
        self.assertEqual(value['schema'], 'rebar-instance-map-v1')
        self.assertEqual([bar['designBarId'] for bar in value['inventory']['bars']], ['bar-a', 'bar-missing', 'bar-unresolved'])
        self.assertEqual(value['instances'][0]['designUnitId'], 'u-a')
        self.assertEqual(value['instances'][1]['designBarId'], None)

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
