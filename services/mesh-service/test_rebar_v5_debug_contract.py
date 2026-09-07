"""Debug masks, new point attributes and real PNTS transport stay consistent."""
import json
import struct
import tempfile
import unittest
from pathlib import Path

import numpy as np

from algorithms.rebar_base import RebarAnalysis, RebarPointAttributes, RebarAlgorithmError
from algorithms.rebar_v5 import GeometricV5Adapter
from algorithms.rebar_v5.contracts import Params
from algorithms.rebar_v5.pipeline import classify, export_sidecars
from rebar_tiles import rewrite_pnts
from test_rebar_v5_classification import _analysis, _instance
from test_rebar_v5_fixture_kinds import tube_and_clamp


class DebugContractTests(unittest.TestCase):
    def test_review_is_an_explicit_boolean_disabled_by_default(self):
        adapter = GeometricV5Adapter()
        field = adapter.descriptor['parameterSchema']['properties']['ownership_review_enabled']
        self.assertEqual(field, {'type':'boolean', 'default':False})
        self.assertFalse(Params.from_value().ownership_review_enabled)
        self.assertTrue(Params.from_value({'ownership_review_enabled':True}).ownership_review_enabled)
        for invalid in (0, 1, 'false', None):
            with self.assertRaises(ValueError):
                Params.from_value({'ownership_review_enabled':invalid})

    def test_debug_mask_cannot_be_overwritten_by_a_competing_cylinder(self):
        instance = _instance(1, [-.08,0,0], [.08,0,0], .004)
        instance['role'] = 'planar'
        analysis = _analysis([instance])
        analysis.data['algorithmDetails']['fixture']['surfaces'][0]['kind'] = 'plate'
        analysis.data['algorithmDetails']['parameters']['ownership_review_enabled'] = False
        attrs, _ = classify(np.array([[0,.004,0]]), analysis)
        self.assertEqual(attrs.scene_class.tolist(), [4])
        self.assertEqual(attrs.fixture_kind.tolist(), [2])
        self.assertEqual(attrs.rebar_role.tolist(), [0])
        analysis.data['algorithmDetails']['fixture']['surfaces'] = []
        attrs, _ = classify(np.array([[0,.004,0]]), analysis)
        self.assertEqual(attrs.scene_class.tolist(), [2])
        self.assertEqual(attrs.rebar_role.tolist(), [1])
        self.assertEqual(attrs.fixture_kind.tolist(), [0])

    def test_subtypes_survive_raw_sidecars_and_display_projection(self):
        points, _, split = tube_and_clamp()
        adapter = GeometricV5Adapter()
        analysis = adapter.analyze(points, adapter.normalize_parameters({'table_min_area':1.}))
        try:
            stages = analysis.data['diagnostics']['stages']
            review = next(item for item in stages if item['name']=='ownership-review')
            self.assertFalse(review['enabled'])
            self.assertEqual(review['maxPasses'], 0)
            self.assertTrue(any(item['name']=='raw-support-verification' for item in stages))
            with tempfile.TemporaryDirectory() as directory:
                summary = export_sidecars(directory, analysis)
                manifest = json.loads((Path(directory)/'labels/manifest.json').read_text())
                self.assertIn('fixture_kind', manifest['attributes'])
                self.assertIn('rebar_role', manifest['attributes'])
                self.assertEqual(sum(summary['fixtureKindCounts'].values()), summary['sceneClassCounts']['fixture'])
                attrs = adapter.project_points(points, analysis)
                attrs.validate(len(points))
                self.assertGreater((attrs.fixture_kind[:split]==1).mean(), .90)
                self.assertGreater((attrs.fixture_kind[split:]==2).mean(), .85)
        finally:
            adapter.close(analysis)

    def test_typed_pnts_attributes_round_trip_and_old_attrs_remain_optional(self):
        attrs = RebarPointAttributes(np.array([0,1],np.uint8),np.array([0,1],np.uint16),np.array([0,2],np.uint32),
            scene_class=np.array([4,2],np.uint8),fixture_kind=np.array([2,0],np.uint8),rebar_role=np.array([0,2],np.uint8))
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'tile.pnts'
            feature=json.dumps({'POINTS_LENGTH':2,'POSITION':{'byteOffset':0}}).encode()
            binary=np.array([[0,0,0],[1,0,0]],dtype='<f4').tobytes()
            path.write_bytes(b'pnts'+struct.pack('<6I',1,28+len(feature)+len(binary),len(feature),len(binary),0,0)+feature+binary)
            rewrite_pnts(path,lambda _:attrs)
            raw=path.read_bytes();_,_,ftj,ftb,btj,btb=struct.unpack_from('<6I',raw,4)
            offset=28+ftj+ftb
            batch=json.loads(raw[offset:offset+btj].decode().strip())
            for name, expected in (('FIXTURE_KIND',[2,0]),('REBAR_ROLE',[0,2])):
                self.assertEqual(batch[name]['componentType'],'UNSIGNED_BYTE')
                values=np.frombuffer(raw,dtype=np.uint8,count=2,offset=offset+btj+batch[name]['byteOffset'])
                self.assertEqual(values.tolist(),expected)
        RebarPointAttributes(attrs.rebar_class,attrs.rebar_direction,attrs.rebar_instance).validate(2)
        attrs.fixture_kind[1]=1
        with self.assertRaises(RebarAlgorithmError):attrs.validate(2)


if __name__=='__main__':unittest.main()
