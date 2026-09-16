import json
import tempfile
import unittest
from pathlib import Path

import laspy
import numpy as np
import trimesh

from analysis_mesh.artifact import build_artifact
from analysis_mesh.contracts import Component, ComponentMeshStream, MeshPart
from rebar_workbench import catalog, load_inputs, run_bar


class WorkbenchTests(unittest.TestCase):
    def _fixture(self, root, points=None, instance_id=1):
        mesh = trimesh.creation.cylinder(radius=.004, height=.2, sections=32).subdivide(); mesh.invert()
        build_artifact(ComponentMeshStream([Component('A', [MeshPart('part', 'part', mesh, np.eye(4), 'source')])], {}), root/'analysis', {'id':'test'}, face_cap=5000)
        angles = np.linspace(0, 2*np.pi, 96, endpoint=False); z, a = np.meshgrid(np.linspace(-.1,.1,80), angles, indexing='ij')
        points = points if points is not None else np.c_[.004*np.cos(a.ravel())-.006, .004*np.sin(a.ravel()), z.ravel()]
        las = laspy.create(point_format=3, file_version='1.2'); las.header.scales=np.full(3,1e-7); las.add_extra_dim(laspy.ExtraBytesParams(name='cloudbim_instance_id',type=np.uint32)); las.x,las.y,las.z=points.T; las['cloudbim_instance_id']=np.full(len(points),instance_id,np.uint32); las.write(root/'scan.las')
        mapping={'schema':'rebar-instance-map-v1','inventory':{'bars':[{'designBarId':'A','ifcGlobalId':'A','name':'bar'}],'units':[{'designBarId':'A','designUnitId':'u','diameterM':.008,'startM':[0,0,-.1],'endM':[0,0,.1]}]},'instances':[{'id':1,'designBarId':'A','designUnitId':'u','reviewStatus':'matched'}]}; (root/'map.json').write_text(json.dumps(mapping))
        config={'scan_path':str(root/'scan.las'),'analysis_mesh_path':str(root/'analysis'),'instance_map_path':str(root/'map.json'),'alignment_matrix':np.eye(4).T.ravel().tolist(),'parameters':{'normal_max_angle_deg':30,'knn_k':1,'max_search_distance':.2}}
        (root/'inputs.json').write_text(json.dumps(config)); return load_inputs(root/'inputs.json')

    def test_translated_cylinder_has_full_indices_and_normals(self):
        with tempfile.TemporaryDirectory() as d:
            result=run_bar(self._fixture(Path(d)), 'A', {})
            self.assertEqual(result['schema'], 'scan-bim-workbench-v1'); self.assertEqual(len(result['scan']['instanceIds']), len(result['scan']['unitIds']))
            self.assertEqual(len(result['mesh']['positions'])//3, len(result['distances']))
            known=[i for i,x in enumerate(result['matchedScanIndices']) if x>=0]
            self.assertTrue(known); self.assertTrue(all(result['reasons'][i]=='matched' for i in known)); self.assertTrue(all(0 <= result['matchedScanIndices'][i] < len(result['scan']['instanceIds']) for i in known))
            self.assertGreater(result['stats']['knownCount'],0)

    def test_partial_and_invalid_input_are_truthful(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d); angles=np.linspace(-np.pi/2,np.pi/2,96); z,a=np.meshgrid(np.linspace(-.1,.1,80),angles,indexing='ij'); points=np.c_[.004*np.cos(a.ravel())-.006,.004*np.sin(a.ravel()),z.ravel()]; loaded=self._fixture(root,points)
            self.assertEqual(catalog(loaded)['bars'][0]['status'], 'matched')
            result=run_bar(loaded,'A',{})
            self.assertGreater(result['stats']['knownCount'],0); self.assertGreater(result['stats']['unknownCount'],0)
            self.assertTrue(all(result['scan']['supported']))
            self.assertTrue(any(abs(x) > 0 for x in result['scan']['normals']))
            self.assertEqual(len(result['scan']['normals']), len(result['scan']['positions']))
            with self.assertRaisesRegex(Exception, 'normalMaxAngleDeg'):
                run_bar(loaded,'A',{'normalMaxAngleDeg':91})
            (root/'bad.json').write_text('{}')
            with self.assertRaisesRegex(Exception, 'missing required'):
                load_inputs(root/'bad.json')

    def test_declared_bar_with_no_owned_points_is_missing(self):
        with tempfile.TemporaryDirectory() as d:
            result=run_bar(self._fixture(Path(d), instance_id=0), 'A', {})
            self.assertEqual(result['status'], 'missing')
            self.assertEqual(result['stats']['knownCount'], 0)
            self.assertTrue(set(result['reasons']) <= {'bar-not-matched'})

    def test_match_trace_preserves_production_geometry_and_distance(self):
        from rebar_comparison import compute_instance_comparison
        with tempfile.TemporaryDirectory() as d:
            loaded=self._fixture(Path(d)); result=run_bar(loaded,'A',{})
            production=compute_instance_comparison(loaded['scan_path'],loaded['analysis_mesh_path'],loaded['instance_map_path'],loaded['alignment_matrix'],voxel_size=.002,downsample_enabled=False,max_histogram_distance=.02,histogram_bins=60,tolerance=.005,normal_constraint_enabled=True,knn_k=1)
            distances=np.array([np.nan if v is None else v for v in result['distances']],dtype=np.float32)
            np.testing.assert_array_equal(distances,production['distances'])
            points=np.array(result['scan']['positions']).reshape(-1,3)
            vertices=np.array(result['mesh']['positions']).reshape(-1,3)
            normals=np.array(result['vertexEvidence']['transverseNormals'])
            for i,q in enumerate(result['matchedScanIndices']):
                if q<0:continue
                self.assertTrue(result['scan']['supported'][q])
                self.assertAlmostEqual(float((points[q]-vertices[i])@normals[i]),distances[i],places=7)
            self.assertTrue(all(row['fitEvidence']['reason'] for row in result['profile']))

    def test_window_experiment_is_recorded_and_defaults_unchanged(self):
        with tempfile.TemporaryDirectory() as d:
            loaded=self._fixture(Path(d)); baseline=run_bar(loaded,'A',{}); experiment=run_bar(loaded,'A',{'windowScale':2})
            self.assertEqual(experiment['profile'][0]['windowM'],baseline['profile'][0]['windowM']*2)
            self.assertEqual(baseline['parameters']['windowScale'],1)
            for params in [{'knnK':1.5},{'knnK':True},{'windowScale':0},{'minArcCoverageDeg':float('nan')}]:
                with self.assertRaises(Exception):run_bar(loaded,'A',params)
