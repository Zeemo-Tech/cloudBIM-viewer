import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from types import SimpleNamespace

import laspy
import numpy as np

from algorithms.rebar_base import RebarInputContext, RebarAlgorithmError
from algorithms.rebar_geometric_v6 import GeometricV6Adapter, source_attributes
from algorithms.rebar_v5.spatial import SpatialBudgetExceeded
from rebar_poc import compute_rebar_artifact
from test_rebar_tiles import _write, _read


def points():
    x, y = np.meshgrid(np.linspace(0, .6, 40), np.linspace(0, .3, 20))
    table = np.column_stack((x.ravel(), y.ravel(), np.zeros(x.size)))
    t, angle = np.meshgrid(np.linspace(0, .6, 100), np.linspace(0, 2*np.pi, 12, endpoint=False))
    rod = np.column_stack((t.ravel(), .15 + .004*np.cos(angle.ravel()), .03 + .004*np.sin(angle.ravel())))
    return np.vstack((table, rod))


class SharedProductionTests(unittest.TestCase):
    def test_explicit_dimension_snapshot_is_forwarded_to_shared_stages(self):
        marker = {"available": True, "families": [{"diameterM": .008}]}
        source = np.array([[0., 0., 0.], [1., 0., 0.], [2., 0., 0.]])
        context = RebarInputContext(source, lambda: iter([(np.arange(3, dtype=np.uint64), source)]),
                                    source_path="/storage/assets/scan/source.las", dimension_priors=marker)
        with patch("algorithms.rebar_geometric_v6.segment_points", side_effect=RuntimeError("probe")) as segment:
            with self.assertRaisesRegex(RuntimeError, "probe"):
                GeometricV6Adapter().analyze_source(context, {})
        self.assertIs(segment.call_args.kwargs["dimension_priors"], marker)

    def test_floating_noise_maps_to_production_noise_and_exterior_steel_is_retained(self):
        context = SimpleNamespace(refined_class=np.array([1,2,3,3,3], np.uint8),
            internal_type=np.array([0,0,5,4,0], np.uint8),
            internal_instance=np.zeros(5, np.uint32), internal_confidence=np.zeros(5, np.float32),
            fused_steel_score=np.array([0,0,1,.65,1], np.float32),
            fused_steel_evidence=np.array([0,0,0,1,3], np.uint8))
        runtime = SimpleNamespace(stages=SimpleNamespace(context=context),
            roles=np.zeros(1, np.uint8), directions=np.zeros(1, np.uint16))
        attrs = source_attributes(runtime, slice(None))
        np.testing.assert_array_equal(attrs.scene_class, [1,4,3,2,2])
        np.testing.assert_array_equal(attrs.rebar_flags, [0,0,0,2,2])
        np.testing.assert_allclose(attrs.class_confidence, [0,0,0,.65,1])
        np.testing.assert_array_equal(attrs.instance_confidence, 0)
        np.testing.assert_array_equal(context.refined_class, [1,2,3,3,3])

    def test_full_source_ignores_bootstrap_and_preserves_record_indices_and_labels(self):
        source = points(); ids = np.arange(len(source), dtype=np.uint64)*2
        context = RebarInputContext(source[:3], lambda: iter([(ids[:800], source[:800]), (ids[800:], source[800:])]))
        adapter = GeometricV6Adapter(); analysis = adapter.analyze_source(context, {})
        scratch = analysis.resources.path
        try:
            self.assertEqual(analysis.data['diagnostics']['sourcePointCount'], len(source))
            expected = analysis.resources.stages.context
            self.assertIsNone(analysis.resources.stages.complete_rebar)
            self.assertEqual(analysis.data['diagnostics']['throughStep'], 6)
            attrs = adapter.project_points(source, analysis)
            np.testing.assert_array_equal(attrs.scene_class, np.where(expected.internal_type == 5, 3, np.array([0,1,4,2,3],np.uint8)[expected.refined_class]))
            np.testing.assert_array_equal(attrs.rebar_instance, expected.internal_instance)
            self.assertEqual(adapter.project_points(np.array([[10.,10.,10.]]),analysis).scene_class[0], 0)
            with tempfile.TemporaryDirectory() as output:
                summary = adapter.export_sidecars(output, analysis)
                self.assertEqual(sum(summary['sceneClassCounts'].values()), len(source))
                manifest = json.loads((Path(output)/'labels/manifest.json').read_text())
                with np.load(Path(output)/'labels'/manifest['chunks'][0]['path']) as data:
                    np.testing.assert_array_equal(data['source_index'], ids)
                    np.testing.assert_array_equal(data['scene_class'], attrs.scene_class)
                    np.testing.assert_array_equal(data['fused_steel_score'], expected.fused_steel_score)
                    np.testing.assert_array_equal(data['fused_steel_evidence'], expected.fused_steel_evidence)
                self.assertEqual(manifest['schema'], 'rebar-raw-labels-v3')
        finally:
            adapter.close(analysis)
        self.assertFalse(scratch.exists())

    def test_physical_las_and_tiles_publish_without_modifying_source(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary); cloud=root/'source.las'; xyz=points()
            las=laspy.create(point_format=0);las.header.scales=np.array([.000001]*3)
            las.x,las.y,las.z=xyz.T;las.write(cloud)
            original=hashlib.sha256(cloud.read_bytes()).hexdigest()
            tiles=root/'source-tiles';tiles.mkdir();(tiles/'tileset.json').write_text('{}')
            prior_ifc=root/'current.ifc';prior_ifc.write_text('server-selected BIM')
            shown=np.column_stack((las.x,las.y,las.z)).astype('<f4')
            _write(tiles/'cloud.pnts',{'POINTS_LENGTH':len(shown),'POSITION':{'byteOffset':0}},shown.tobytes())
            kwargs=dict(point_cloud_path=str(cloud),point_cloud_format='las',source_tileset_path=str(tiles),
                output_directory=str(root/'artifact'),artifact_version='v6-test',algorithm='geometric-v6',
                input_options={'maxInputPoints':3},parameters={},storage_root=str(root),
                bim_prior={'ifc_path':str(prior_ifc),'scan_to_bim':[1,0,0,0,0,1,0,0,0,0,1,0,0,0,0,1]})
            marker={'available':True,'families':[{'diameterM':.008}],'provenance':{'selection':'explicit'}}
            with patch('algorithms.rebar_dimension_priors.load_dimension_priors',return_value=marker) as loader:
                result=compute_rebar_artifact(**kwargs)
            loader.assert_called_once_with(ifc_path=str(prior_ifc.resolve()))
            self.assertEqual(result['algorithm'],{'id':'geometric-v6','version':'14'})
            self.assertEqual(result['summary']['diagnostics']['dimensionPriors'],marker)
            self.assertEqual(result['summary']['rawSource']['finitePointCount'],len(xyz))
            self.assertEqual(result['summary']['display']['totalPointCount'],len(xyz))
            self.assertEqual(hashlib.sha256(cloud.read_bytes()).hexdigest(),original)
            self.assertTrue((root/'artifact/features/manifest.json').is_file())
            _, _, batch, binary = _read(root/'artifact/tiles/cloud.pnts')
            with np.load(root/'artifact/labels/000000.npz') as labels:
                np.testing.assert_array_equal(np.frombuffer(binary,dtype='u1',count=len(xyz),offset=batch['SCENE_CLASS']['byteOffset']), labels['scene_class'])
                np.testing.assert_array_equal(np.frombuffer(binary,dtype='<u4',count=len(xyz),offset=batch['REBAR_INSTANCE']['byteOffset']), labels['rebar_instance'])
            with self.assertRaisesRegex(ValueError,'immutable'):
                compute_rebar_artifact(**kwargs)

    def test_parameters_and_source_budget_fail_explicitly(self):
        adapter=GeometricV6Adapter()
        for params in ({'normal_k':True},{'normal_k':2},{'normal_k':3.5},{'noise_radius':.02},{'display_transfer_distance':float('nan')}):
            with self.assertRaises(RebarAlgorithmError):adapter.normalize_parameters(params)
        with patch('algorithms.rebar_geometric_v6.MAX_SOURCE_POINTS', 3):
            with self.assertRaises(SpatialBudgetExceeded):adapter.analyze(points(),{})


if __name__=='__main__':unittest.main()
