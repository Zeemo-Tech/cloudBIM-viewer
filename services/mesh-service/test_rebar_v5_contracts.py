import copy
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np

from algorithms.rebar_base import RebarInputContext
from algorithms.rebar_v5.contracts import Params
from algorithms.rebar_v5.pipeline import prepare
from algorithms.rebar_v5.intersections import compute_intersections
from algorithms.rebar_v5.projection import project_surface_top2
from algorithms.rebar_v4_geometry import build_segment_index
from rebar_poc import _check_decoded_budget, PointCloudInputError


class V5ContractTests(unittest.TestCase):
    def test_format_comments_and_conflicting_counts_cannot_bypass_memory_budget(self):
        with tempfile.TemporaryDirectory() as directory:
            for fmt,header in (
                ('ply','ply\ncomment element vertex 1\nelement vertex 3000000\nend_header\n'),
                ('pcd','# POINTS 1\nPOINTS 3000000\nDATA ascii\n'),
                ('ply','ply\nelement vertex 4\nelement vertex 5\nend_header\n'),
                ('pcd','POINTS 4\nPOINTS 5\nDATA ascii\n'),
            ):
                path=Path(directory)/('input.'+fmt);path.write_text(header)
                with self.subTest(format=fmt,header=header):
                    with self.assertRaises(PointCloudInputError):_check_decoded_budget(path,fmt)

    def test_candidate_and_feature_records_are_invariant_to_reader_and_core_splits(self):
        rng=np.random.default_rng(8)
        points=np.column_stack((rng.uniform(.075,.125,160),rng.uniform(-.02,.02,160),np.zeros(160)))
        points=np.vstack((points,points[0],[[np.nan,0,0]]))
        results=[]
        for reader_size,core_limit in ((17,500),(63,48)):
            p=Params.from_value({'block_size':.1,'block_point_limit':core_limit,'neighbourhood_point_limit':2000})
            context=RebarInputContext(points[:3],lambda:iter((np.arange(i,min(i+reader_size,len(points)),dtype=np.uint64),points[i:i+reader_size]) for i in range(0,len(points),reader_size)))
            runtime,det,features,_=prepare(context,p)
            try:
                raw=[]
                for path in runtime.feature_chunks:
                    with runtime.chunks.read(path) as saved:raw.append({k:saved[k] for k in saved.files})
                combined={k:np.concatenate([x[k] for x in raw]) for k in raw[0]}
                order=np.argsort(combined['source_index']);combined={k:v[order] for k,v in combined.items()}
                results.append((det,features,combined))
            finally:runtime.close()
        for i in (0,1,2):
            a,b=results[0][i],results[1][i]
            if isinstance(a,dict):
                for key in a:np.testing.assert_allclose(a[key],b[key],atol=1e-6,err_msg=key)
            else:np.testing.assert_array_equal(a,b)
        np.testing.assert_array_equal(results[0][2]['source_index'],np.arange(161,dtype=np.uint64))

    def test_surface_competition_does_not_prefer_a_larger_tube(self):
        entries=[{'id':i,'directionId':1,'radius':r+.002,'observedSegments':[{'points':[[0,0,0],[1,0,0]]}]} for i,r in ((1,.004),(2,.008))]
        result=project_surface_top2(np.array([[.5,.004,0],[.5,.008,0]]),build_segment_index(entries),.002)
        self.assertEqual(result.best_id.tolist(),[1,2])

    def test_surface_projection_exposes_best_cylinder_radial_normal(self):
        entries=[{'id':1,'directionId':1,'radius':.006,'observedSegments':[{'points':[[-.08,0,0],[.08,0,0]]}]}]
        result=project_surface_top2(np.array([[-.04,.004,0]]),build_segment_index(entries),.002)
        np.testing.assert_allclose(result.best_normal, [[0,1,0]], atol=1e-12)

    def test_surface_projection_normal_survives_expansion_and_same_instance_piece_change(self):
        entries=[{'id':1,'directionId':1,'radius':.006,'observedSegments':[
            {'points':[[.45,.001,0],[.55,.001,0]]},
            {'points':[[.496,-.5,0],[.496,.5,0]]},
        ]}]
        index=build_segment_index(entries)
        for neighbours in (1, 20):
            result=project_surface_top2(np.array([[.5,.004,0]]),index,.002,neighbours)
            np.testing.assert_allclose(result.best_normal, [[1,0,0]], atol=1e-12)
            self.assertEqual(result.best_id.tolist(), [1])
        empty=project_surface_top2(np.empty((0,3)),index,.002)
        self.assertEqual(empty.best_normal.shape,(0,3))

    def test_all_intersection_edge_cases_and_point_arrays_are_immutable(self):
        a={'id':1,'observedSegments':[{'points':[[0,0,0],[1,0,0]]}]}
        labels={name:np.array([0,1,2,3,4],dtype=dtype) for name,dtype in (('scene',np.uint8),('instance',np.uint32),('direction',np.uint16))}
        before={k:v.copy() for k,v in labels.items()}
        for name,line,observed,expected in (
            ('exact',[[.5,-1,0],[.5,1,0]],True,1),
            ('tolerated',[[.5,-1,.001],[.5,1,.001]],True,1),
            ('too_far',[[.5,-1,.00101],[.5,1,.00101]],True,0),
            ('different_layer',[[.5,-1,.05],[.5,1,.05]],True,0),
            ('parallel',[[0,.0001,0],[1,.0001,0]],True,0),
            ('extension_only',[[1.2,-1,0],[1.2,1,0]],True,0),
            ('inferred',[[.5,-1,0],[.5,1,0]],False,0),
        ):
            b={'id':2,'centerline':line,'observedSegments':[{'points':line}] if observed else [],'inferredSegments':[] if observed else [{'points':line}]}
            instances=[copy.deepcopy(a),b];prior=copy.deepcopy(instances)
            with self.subTest(case=name):
                self.assertEqual(len(compute_intersections(instances)),expected)
                self.assertEqual(instances,prior)
                for key in labels:np.testing.assert_array_equal(labels[key],before[key])


if __name__=='__main__':unittest.main()
