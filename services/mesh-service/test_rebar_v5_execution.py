"""Exact broad-phase candidates and bounded ordered spatial execution."""
import os
import threading
import unittest
from unittest.mock import patch

import numpy as np

from algorithms.spatial_keys import integer_pair_membership
from algorithms.rebar_v5.candidates import PointBoundsIndex
from algorithms.rebar_v5.contracts import Params
from algorithms.rebar_v5.scene import fixture_rows, fixture_mask
from algorithms.rebar_v5.scheduling import ordered_work, worker_count
from algorithms.rebar_v5 import pipeline
from algorithms.rebar_base import RebarAnalysis
from test_rebar_v5_classification import _face


def square(value):
    return value * value


class CandidateIndexTests(unittest.TestCase):
    def test_index_and_zero_budget_match_exhaustive_boxes_with_nonfinite_points(self):
        rng = np.random.default_rng(716)
        points = np.vstack((rng.normal(size=(4000, 3)), [[np.nan,0,0], [np.inf,0,0], [-np.inf,0,0]]))
        for budget in (0, 64*1024**2):
            index = PointBoundsIndex(points, budget)
            for _ in range(30):
                lower = rng.uniform(-2, 1, 3); upper = lower + rng.uniform(0, 2, 3)
                expected = np.flatnonzero(np.all((points >= lower) & (points <= upper), axis=1))
                np.testing.assert_array_equal(index.query(lower, upper), expected)
        self.assertIsNone(PointBoundsIndex(points, 1).orders)

    def test_exact_face_edges_and_holes_preserve_source_order(self):
        p = Params(); rng = np.random.default_rng(714)
        for origin in (0., 1e8):
            face = _face();face['origin'][0] += origin
            points = rng.uniform([-.3,-.2,-.005], [.3,.2,.005], (5000,3))
            points[:,0] += origin
            edge=np.array([origin,0,p.fixture_surface_distance])
            points=np.vstack((points,edge,np.nextafter(edge,np.inf),np.nextafter(edge,-np.inf)))
            expected=np.flatnonzero(fixture_mask(points,[face],p))
            np.testing.assert_array_equal(fixture_rows(points,face,p,point_index=PointBoundsIndex(points)),expected)

    def test_integer_pairs_match_tuple_equality_without_overflow_or_rounding(self):
        rng=np.random.default_rng(714)
        for dtype in (np.int64,np.uint64):
            limit=np.iinfo(dtype)
            known=np.concatenate((rng.integers(0,100,(100,2)).astype(dtype),
                                  np.array([[limit.min,0],[limit.max,1]],dtype=dtype)))
            keys=np.concatenate((known,known[::-1],np.zeros((10,2),dtype=dtype)))
            expected=np.array([tuple(row) in {tuple(r) for r in known} for row in keys])
            np.testing.assert_array_equal(integer_pair_membership(keys,known),expected)
        known=np.array([[1.5,2.],[3.,4.]])
        np.testing.assert_array_equal(integer_pair_membership(np.array([[1,2],[3,4]]),known),[False,True])
        known=np.array([[-2**31,2**31-1],[-1,0],[0,-1]],np.int64)
        keys=np.vstack((known,[[2**31-1,-2**31],[0,0]]))
        np.testing.assert_array_equal(integer_pair_membership(keys,known),[True,True,True,False,False])


class OrderedExecutionTests(unittest.TestCase):
    def test_boundary_workers_never_access_lru_and_match_lazy_updates(self):
        p=Params();runtime=pipeline.Runtime(p,cache_bytes=32*1024)
        rng=np.random.default_rng(931)
        points=rng.uniform([0,0,-.02],[.49,.02,.02],(1200,3))
        source=np.arange(len(points),dtype=np.uint64)+np.uint64(2**53+19)
        runtime.store.build([(source,points)])
        for key,(_,count) in runtime.store.cells.items():
            runtime.noise_maps[key]=np.zeros(count,np.uint8)
            runtime.suspect_maps[key]=np.zeros(count,np.uint8)
        for ordinal,(_,lo,hi) in enumerate(runtime.store.cores()):
            records=runtime.store.query(lo,hi)
            path=runtime.path/f'features-{ordinal:06d}.npz'
            runtime.chunks.save(path,xyz=records['xyz'],source_index=records['source_index'],
                noise=np.zeros(len(records),np.uint8),surface_valid=np.zeros(len(records),np.uint8),
                axis_valid=np.zeros(len(records),np.uint8))
            runtime.feature_chunks.append(path);runtime.feature_bounds.append((lo,hi))
        analysis=RebarAnalysis({'algorithmDetails':{'plane':{},'fixture':{'surfaces':[],'bolts':[]}}},resources=runtime)
        def features(support,query,_p):
            return {'surface_valid':np.full(len(query),len(support)%251,np.uint8),
                    'axis_valid':np.full(len(query),len(support)%239,np.uint8)}
        try:
            with (patch.object(pipeline,'table_mask',side_effect=lambda xyz,*_:xyz[:,2]<-.005),
                  patch.object(pipeline,'fixture_mask',side_effect=lambda xyz,*_:xyz[:,2]>.015),
                  patch.object(pipeline,'bolt_mask',side_effect=lambda xyz,*_:np.zeros(len(xyz),bool)),
                  patch.object(pipeline,'multiscale',side_effect=features)):
                expected=[pipeline.stage_features(i,runtime.chunks.read(path),analysis)
                          for i,path in enumerate(runtime.feature_chunks)]
                runtime.updated_features.clear();runtime.update_chunks.clear()
                # Force the production size gate without allocating a large test cloud.
                runtime.store.count=250_000
                owner=threading.get_ident();read=runtime.chunks.read;save=runtime.chunks.save
                def on_owner(function):
                    def call(*args,**kwargs):
                        self.assertEqual(threading.get_ident(),owner)
                        return function(*args,**kwargs)
                    return call
                with (patch.object(pipeline,'worker_count',return_value=2),
                      patch.object(runtime.chunks,'read',side_effect=on_owner(read)),
                      patch.object(runtime.chunks,'save',side_effect=on_owner(save))):
                    pipeline._prime_boundary_updates(analysis)
                    for i,path in enumerate(runtime.feature_chunks):
                        actual=pipeline.stage_features(i,runtime.chunks.read(path),analysis)
                        for key in actual:np.testing.assert_array_equal(actual[key],expected[i][key])
                self.assertEqual(len(runtime.updated_features),len(runtime.feature_chunks))
                self.assertGreater(runtime.chunks.stats['diskWrites'],0)
        finally:
            runtime.close()

    def test_backpressure_and_source_order(self):
        consumed=[]
        def jobs():
            for i in range(15):
                consumed.append(i)
                yield i
        outputs=ordered_work(square,jobs(),3)
        self.assertEqual(next(outputs),0)
        self.assertEqual(len(consumed),3)
        self.assertEqual(list(outputs),[i*i for i in range(1,15)])

    def test_single_worker_and_worker_error(self):
        self.assertEqual(list(ordered_work(square,range(8),1)),[i*i for i in range(8)])
        def fail(i):
            if i==2:raise ValueError('task failed')
            return i
        with self.assertRaisesRegex(ValueError,'task failed'):
            list(ordered_work(fail,range(20),3))

    def test_spawned_work_matches_serial(self):
        self.assertEqual(list(ordered_work(square,range(8),2,processes=True)),[i*i for i in range(8)])

    def test_unknown_low_memory_and_explicit_serial_fall_back(self):
        for available in (None,0,512*1024**2):
            with patch('algorithms.rebar_v5.scheduling.available_memory',return_value=available):
                self.assertEqual(worker_count(Params()),1)
        with patch.dict(os.environ,REBAR_SPATIAL_WORKERS='1'):
            self.assertEqual(worker_count(Params()),1)
        with patch.dict(os.environ,REBAR_SPATIAL_WORKERS='999'):
            self.assertLessEqual(worker_count(Params()),4)
        self.assertEqual(worker_count(Params(),cache_reserve=64*1024**3),1)


if __name__=='__main__':unittest.main()
