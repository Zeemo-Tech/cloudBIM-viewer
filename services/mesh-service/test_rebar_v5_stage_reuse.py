"""Boundary PCA reuses masks from the complete source-indexed halo."""
from dataclasses import asdict
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import patch

import numpy as np
from algorithms.rebar_v5.chunks import ChunkCache

from algorithms.rebar_base import RebarAnalysis
from algorithms.rebar_v5.contracts import Params
from algorithms.rebar_v5.pipeline import stage_features
from algorithms.rebar_v5.spatial import RECORD, SpatialStore


class Arrays(dict):
    @property
    def files(self):
        return list(self)


class StageMaskReuseTests(unittest.TestCase):
    def test_global_cell_pruning_uses_bounded_batches_and_half_open_boxes(self):
        with tempfile.TemporaryDirectory() as directory:
            p = Params()
            store = SpatialStore(directory, p)
            store.cells = {(index, 0, 0): (None, 0) for index in range(10_000)}
            original = np.asarray
            sizes = []
            def observed(value, *args, **kwargs):
                if isinstance(value, list) and value and isinstance(value[0], tuple):
                    sizes.append(len(value))
                return original(value, *args, **kwargs)
            with patch('algorithms.rebar_v5.spatial.np.asarray', side_effect=observed):
                keys = list(store._intersecting_cells(np.array([4095*p.block_size, 0, 0]),
                                                       np.array([4097*p.block_size, 1, 1])))
            self.assertEqual(keys, [(4095, 0, 0), (4096, 0, 0)])
            self.assertEqual(sizes, [4096, 4096, 1808])

    def run_stage(self, table, fixture):
        p = Params()
        support = np.empty(5, RECORD)
        support['source_index'] = [4, 17, 2**53+41, 2**53+102, 2**63+900]
        support['xyz'] = [[-.01, 0, 0], [0, 0, 0], [.01, 0, 0], [.02, 0, 0], [.03, 0, 0]]
        core_rows = [1, 3]
        raw = Arrays(source_index=support['source_index'][core_rows],
                     xyz=support['xyz'][core_rows], noise=np.zeros(2, np.uint8),
                     surface_valid=np.ones(2, np.uint8), axis_valid=np.ones(2, np.uint8))
        def mask(values, _models, _p, selection):
            return selection[np.searchsorted(support['xyz'][:, 0], values[:, 0])]
        with tempfile.TemporaryDirectory() as directory:
            runtime = SimpleNamespace(chunks=ChunkCache(1024*1024), p=p, updated_features=set(), update_chunks={}, path=Path(directory),
                                      feature_bounds=[(np.zeros(3), np.ones(3))],
                                      store=SimpleNamespace(query=lambda *_: support),
                                      masks=lambda records: np.zeros(len(records), bool))
            analysis = RebarAnalysis({'algorithmDetails': {'parameters': asdict(p), 'plane': {},
                                     'fixture': {'surfaces': [], 'bolts': []}}}, resources=runtime)
            with (patch('algorithms.rebar_v5.pipeline.table_mask', side_effect=lambda *args: mask(*args, table)) as tm,
                  patch('algorithms.rebar_v5.pipeline.fixture_mask', side_effect=lambda *args: mask(*args, fixture)) as fm,
                  patch('algorithms.rebar_v5.pipeline.bolt_mask', side_effect=lambda points, *_: np.zeros(len(points), bool)) as bm,
                  patch('algorithms.rebar_v5.pipeline.multiscale', side_effect=lambda support, query, p: {
                      'surface_valid': np.full(len(query), 7, np.uint8), 'axis_valid': np.full(len(query), 8, np.uint8)
                  }) as pca):
                actual = stage_features(0, raw, analysis)
                self.assertIn(0, runtime.updated_features)
                calls = (tm.call_count, fm.call_count, bm.call_count, pca.call_count)
                # Cached updates survive another ownership pass without masks/PCA.
                cached = stage_features(0, raw, analysis)
                for name in actual:
                    np.testing.assert_array_equal(cached[name], actual[name])
                self.assertEqual(calls, (tm.call_count, fm.call_count, bm.call_count, pca.call_count))
        return actual, calls

    def test_core_masks_are_selected_from_halo_by_source_identity(self):
        result, calls = self.run_stage(np.array([True, False, False, False, False]),
                                      np.array([False, False, True, False, False]))
        self.assertEqual(calls[:3], (1, 1, 1))
        self.assertEqual(calls[3], 2)
        np.testing.assert_array_equal(result['surface_valid'], [7, 7])

    def test_fully_excluded_core_needs_no_fixture_masks_or_boundary_pca(self):
        result, calls = self.run_stage(np.ones(5, bool), np.zeros(5, bool))
        self.assertEqual(calls, (1, 0, 0, 0))
        np.testing.assert_array_equal(result['surface_valid'], [1, 1])


if __name__ == '__main__':
    unittest.main()
