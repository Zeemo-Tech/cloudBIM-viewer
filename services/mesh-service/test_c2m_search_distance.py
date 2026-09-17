import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import numpy as np
from scipy.spatial import cKDTree
from pydantic import ValidationError
from rebar_deviation import constrained_nearest
from rebar_solid import RebarSolid
from main import C2MParams, C2MRequest, _c2m_compute_quick
import test_rebar_comparison as fixtures
import trimesh

class SearchDistanceTests(unittest.TestCase):
    def test_parameter_boundaries(self):
        self.assertEqual(C2MParams().max_search_distance, .2)
        for value in [0, -1, .00001, .201, float('nan'), float('inf')]:
            with self.assertRaises(ValidationError): C2MParams(max_search_distance=value)
        for value in [.0001, .02, .2]:
            self.assertEqual(C2MParams(max_search_distance=value).max_search_distance, value)

    def test_distance_caps_full_candidate_line_including_fallback(self):
        mesh=trimesh.creation.cylinder(radius=.01,height=.2,sections=32)
        solid=RebarSolid([mesh]);p=np.array([[.01,0,0]]);t=np.array([[0.,0,1]]);n=np.array([[1.,0,0]])
        for fallback in ['unknown','nearest']:
            for point,cap,allowed in [([.016,0,0],.005,False),([.015,0,0],.005,True),
                                      ([.014,0,.004],.005,False),([.014,0,.002],.005,True),
                                      ([-.015,0,0],.2,False),([.006,0,0],.003,False),([.006,0,0],.005,True)]:
                with self.subTest(fallback=fallback,point=point,cap=cap):
                    scan=np.array([point]);d,ix,_,_=constrained_nearest(cKDTree(scan),scan,p,t,n,k=1,max_angle_deg=45,
                        half_space_only=False,fallback_mode=fallback,solid=solid,max_search_distance=cap)
                    self.assertEqual(bool(np.isfinite(d[0])),allowed)
                    if not allowed:self.assertEqual(ix[0],-1)

    def test_service_applies_custom_cap_and_publishes_effective_value(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory); fixtures.RebarComparisonTests()._fixture(root)
            fingerprint=root/'fingerprint.ply'; fixtures.component('F',0).parts[0].mesh.export(fingerprint)
            with patch('main.C2M_OUTPUT_DIR',str(root/'outputs')):
                results=[]
                for cap in [.02,.08]:
                    result=_c2m_compute_quick(C2MRequest(scan_path=str(root/'scan.las'),mesh_path=str(fingerprint),
                        analysis_mesh_path=str(root/'analysis'),instance_map_path=str(root/'instance-map.json'),
                        alignment_matrix=fixtures.IDENTITY,params=C2MParams(downsample_enabled=False,max_search_distance=cap)))
                    self.assertIsInstance(result,dict)
                    self.assertEqual(result['diagnostics']['rebarComparison']['effective']['maxSearchDistance'],cap)
                    values=np.fromfile(result['distancesPath'],dtype='<f4')
                    self.assertTrue(np.all(np.abs(values[np.isfinite(values)])<=cap))
                    results.append(np.isfinite(values).sum())
                self.assertEqual(results[0],0);self.assertGreater(results[1],0)

    def test_legacy_input_does_not_silently_ignore_custom_cap(self):
        with self.assertRaises(ValidationError):
            C2MRequest(scan_path='scan',mesh_path='mesh',alignment_matrix=fixtures.IDENTITY,params=C2MParams(max_search_distance=.01))

if __name__=='__main__':unittest.main()
