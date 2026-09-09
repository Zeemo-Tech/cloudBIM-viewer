import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np

from algorithms.rebar_v5.chunks import ChunkCache, DerivedCache, memory_budget


class MemoryCacheTests(unittest.TestCase):
    def test_resident_columns_share_arrays_without_io(self):
        with tempfile.TemporaryDirectory() as directory:
            cache = ChunkCache(1024)
            path = Path(directory)/'a.npz'
            values = np.arange(100, dtype=np.uint64)
            cache.save(path, source_index=values)
            with patch('algorithms.rebar_v5.chunks.np.load', side_effect=AssertionError('disk read')):
                self.assertIs(cache.read(path)['source_index'], values)
                self.assertIs(cache.read(path)['source_index'], values)
            self.assertFalse(path.exists())
            self.assertEqual(cache.stats['diskWrites'], 0)
            cache.clear()
            self.assertEqual(cache.stats['residentBytes'], 0)

    def test_budget_spills_lru_and_preserves_replacement_and_uint64(self):
        with tempfile.TemporaryDirectory() as directory:
            cache = ChunkCache(16)
            a, b = (Path(directory)/name for name in ('a.npz','b.npz'))
            expected = np.array([2**63+1, 2**64-1], np.uint64)
            cache.save(a, source_index=expected)
            cache.save(b, value=np.zeros(2, np.uint64))
            self.assertTrue(a.exists())
            np.testing.assert_array_equal(cache.read(a)['source_index'], expected)
            cache.save(a, source_index=expected[::-1].copy())
            cache.read(b)
            np.testing.assert_array_equal(cache.read(a)['source_index'], expected[::-1])
            self.assertLessEqual(cache.stats['peakResidentBytes'], 16)

    def test_zero_or_oversized_budget_uses_lossless_disk_path(self):
        for budget in (0, 4):
            with tempfile.TemporaryDirectory() as directory:
                cache=ChunkCache(budget);path=Path(directory)/'a.npz'
                data=np.arange(8)
                cache.save(path, label=data)
                self.assertTrue(path.exists())
                np.testing.assert_array_equal(cache.read(path)['label'], data)
                self.assertEqual(cache.stats['residentBytes'], 0)
                self.assertEqual(cache.stats['spilledBytes'], data.nbytes)

    def test_budget_obeys_host_and_container_headroom(self):
        def contents(path):
            return {'/proc/meminfo': 'MemAvailable: 12582912 kB\n', '/proc/self/cgroup':'0::/\n',
                    '/sys/fs/cgroup/memory.max': str(4*1024**3),
                    '/sys/fs/cgroup/memory.current': str(1024**3)}[str(path)]
        with patch.dict('os.environ', {'REBAR_MEMORY_CACHE_MB':'4096'}), patch.object(Path, 'read_text', contents):
            self.assertEqual(memory_budget(), 512*1024**2)

    def test_budget_resolves_nested_scope_and_ancestor_limit(self):
        def contents(path):
            values={'/proc/meminfo':'MemAvailable: 33554432 kB\n',
                    '/proc/self/cgroup':'0::/user.slice/benchmark.scope\n',
                    '/sys/fs/cgroup/user.slice/benchmark.scope/memory.max':str(8*1024**3),
                    '/sys/fs/cgroup/user.slice/benchmark.scope/memory.current':str(1024**3),
                    '/sys/fs/cgroup/user.slice/memory.max':str(6*1024**3),
                    '/sys/fs/cgroup/user.slice/memory.current':str(3*1024**3)}
            if str(path) not in values:raise FileNotFoundError(path)
            return values[str(path)]
        with patch.dict('os.environ', {'REBAR_MEMORY_CACHE_MB':'4096'}), patch.object(Path,'read_text',contents):
            self.assertEqual(memory_budget(),512*1024**2)

    def test_derived_indexes_have_a_separate_bounded_lru(self):
        cache=DerivedCache(8)
        cache.save('a','A',4);cache.save('b','B',4)
        self.assertEqual(cache.get('a'),'A')
        cache.save('c','C',4)
        self.assertIsNone(cache.get('b'))
        self.assertEqual(cache.get('a'),'A')
        cache.save('large','large',9)
        self.assertIsNone(cache.get('large'))
        self.assertLessEqual(cache.stats['peakResidentBytes'],8)

    def test_display_queries_share_indexes_and_ownership_changes_invalidate_them(self):
        from algorithms.rebar_base import RebarAnalysis
        from algorithms.rebar_v5 import pipeline
        from algorithms.rebar_v5.contracts import Params
        runtime=pipeline.Runtime(Params(),cache_bytes=1024*1024)
        try:
            points=[]
            for index in range(2):
                lo=np.array([index*runtime.p.block_size,0.,0.]);hi=lo+runtime.p.block_size
                xyz=lo+np.array([[.01,.01,.01],[.02,.01,.01],[.03,.01,.01]])
                points.append(xyz[:1]);ids=np.arange(index*3,index*3+3,dtype=np.uint64)
                feature=runtime.path/f'features-{index}.npz';label=runtime.path/f'labels-{index}.npz'
                runtime.feature_chunks.append(feature);runtime.feature_bounds.append((lo,hi))
                runtime.chunks.save(feature,xyz=xyz,source_index=ids)
                labels={k:np.zeros(3,dtype) for k,dtype in {
                    'rebar_class':np.uint8,'rebar_direction':np.uint16,'rebar_instance':np.uint32,
                    'scene_class':np.uint8,'rebar_flags':np.uint8,'class_confidence':np.float32,
                    'instance_confidence':np.float32,'fixture_kind':np.uint8,'rebar_role':np.uint8}.items()}
                runtime.chunks.save(label,source_index=ids,**labels,candidate_point_indices=np.empty(0,np.uint32),
                                   candidate_offsets=np.array([0],np.uint64),candidate_instance_ids=np.empty(0,np.uint32))
                runtime.label_chunks[index]=label
            runtime.store.count=6
            analysis=RebarAnalysis({'instances':[]},resources=runtime)
            with patch.object(pipeline,'cKDTree',wraps=pipeline.cKDTree) as tree:
                first,_=pipeline.transfer_labels(points[0],analysis)
                pipeline.transfer_labels(points[1],analysis)
                again,_=pipeline.transfer_labels(points[0],analysis)
                self.assertEqual(tree.call_count,2)
                np.testing.assert_array_equal(first.scene_class,again.scene_class)
                pipeline.finalize_raw_ownership(analysis)
                pipeline.transfer_labels(points[0],analysis)
                self.assertEqual(tree.call_count,3)
            self.assertEqual(runtime.chunks.stats['diskReads'],0)
            self.assertEqual(runtime.chunks.stats['diskWrites'],0)
        finally:runtime.close()


if __name__ == '__main__':
    unittest.main()
