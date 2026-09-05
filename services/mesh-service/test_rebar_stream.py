from __future__ import annotations
import json
import tempfile
import unittest
from pathlib import Path
import numpy as np
import laspy
from algorithms.rebar_base import RebarInputContext, RebarAnalysis, RebarPointAttributes
from rebar_stream import iter_source_chunks, write_raw_labels


class StreamingTests(unittest.TestCase):
    def test_las_chunks_keep_source_indices_and_can_restart(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'source.las'
            cloud=laspy.LasData(laspy.LasHeader(point_format=3,version='1.2'))
            cloud.x=np.arange(11)*.01; cloud.y=np.zeros(11); cloud.z=np.zeros(11); cloud.write(path)
            chunks=list(iter_source_chunks(str(path),chunk_size=4))
            self.assertEqual([len(p) for _,p in chunks],[4,4,3])
            np.testing.assert_array_equal(np.concatenate([i for i,_ in chunks]),np.arange(11))
            for (i,p),(again,q) in zip(chunks,iter_source_chunks(str(path),chunk_size=4)):
                np.testing.assert_array_equal(i,again); np.testing.assert_array_equal(p,q)

    def test_raw_labels_are_indexed_not_display_tile_counts(self):
        class Algorithm:
            def project_points(self, points, analysis):
                return RebarPointAttributes(np.array([1,0],np.uint8),np.array([3,0],np.uint16),
                    np.array([17,0],np.uint32),np.array([2,4],np.uint8),np.array([0,0],np.uint8))
        context=RebarInputContext(np.zeros((2,3)),lambda:iter([(np.array([2,8],np.uint64),np.zeros((2,3)))]))
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'labels'
            summary=write_raw_labels(path,context,Algorithm(),RebarAnalysis({}))
            self.assertEqual(summary['finitePointCount'],2)
            self.assertEqual(summary['sceneClassCounts']['fixture_formwork'],1)
            manifest=json.loads((path/'manifest.json').read_text())
            with np.load(path/manifest['chunks'][0]['path'],allow_pickle=False) as labels:
                np.testing.assert_array_equal(labels['source_index'],[2,8])
                self.assertEqual(labels['rebar_instance'].dtype,np.dtype('<u4'))
                self.assertEqual(labels['rebar_instance'].tolist(),[17,0])

    def test_ambiguity_memberships_round_trip_and_invalid_rows_rejected(self):
        class Algorithm:
            candidates = {'point_indices':np.array([1]), 'offsets':np.array([0,2]), 'instance_ids':np.array([3,8])}
            def project_points(self, points, analysis):
                return RebarPointAttributes(np.ones(2,np.uint8),np.ones(2,np.uint16),
                    np.array([3,0xffffffff],np.uint32),np.full(2,2,np.uint8),np.array([1,2],np.uint8))
            def project_candidates(self, points, analysis):
                return self.candidates
        algorithm=Algorithm()
        context=RebarInputContext(np.zeros((2,3)),lambda:iter([(np.array([12,18],np.uint64),np.zeros((2,3)))]))
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'labels'
            result=write_raw_labels(path,context,algorithm,RebarAnalysis({}))
            self.assertEqual(result['ambiguousPointCount'],1)
            with np.load(path/'000000.npz',allow_pickle=False) as labels:
                np.testing.assert_array_equal(labels['candidate_instance_ids'],[3,8])
                np.testing.assert_array_equal(labels['source_index'][labels['candidate_point_indices']],[18])
            algorithm.candidates={**algorithm.candidates,'point_indices':np.array([0])}
            with self.assertRaisesRegex(ValueError,'match ambiguous'):
                write_raw_labels(Path(folder)/'invalid',context,algorithm,RebarAnalysis({}))

if __name__=='__main__': unittest.main()
