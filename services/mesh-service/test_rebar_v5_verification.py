import unittest
import numpy as np

from algorithms.rebar_v5.contracts import Params
from algorithms.rebar_v5.verification import verify_raw_instances


RECORD = np.dtype([("source_index", "<u8"), ("xyz", "<f8", (3,))])


class Store:
    def __init__(self, points, indices=None, duplicate_queries=False):
        self.records = np.empty(len(points), RECORD)
        self.records["source_index"] = np.arange(len(points), dtype=np.uint64) if indices is None else indices
        self.records["xyz"] = points
        self.duplicate_queries = duplicate_queries
    def query(self, lo, hi):
        rows = self.records[np.all((self.records["xyz"] >= lo) & (self.records["xyz"] < hi), axis=1)]
        return np.concatenate((rows, rows)) if self.duplicate_queries else rows


class Runtime:
    def __init__(self, points, **kwargs): self.store = Store(points, **kwargs)
    def masks(self, records): return np.zeros(len(records), dtype=bool)


def candidate():
    return {"id": 7, "radius": .008, "centerline": [[-.2, 0, 0], [.2, 0, 0]],
            "observedSegments": [{"points": [[-.2, 0, 0], [.2, 0, 0]]}], "inferredSegments": []}


def surface(x):
    return np.asarray([[value, .008*np.cos(angle), .008*np.sin(angle)] for value in x for angle in np.linspace(0, 2*np.pi, 6, endpoint=False)])


class VerificationTests(unittest.TestCase):
    def setUp(self): self.p = Params.from_value({"min_primitive_votes": 4, "min_primitive_length": .02, "axial_gap": .05, "block_size": .1})

    def test_absent_raw_evidence_discards_candidate(self):
        items, diagnostics = verify_raw_instances(Runtime(np.empty((0, 3))), [candidate()], self.p)
        self.assertEqual(items, []); self.assertEqual(diagnostics["discardedCount"], 1)

    def test_raw_gap_splits_observed_and_marks_inferred_bridge(self):
        x = np.r_[np.linspace(-.2, -.07, 12), np.linspace(.07, .2, 12)]
        items, _ = verify_raw_instances(Runtime(surface(x)), [candidate()], self.p)
        self.assertEqual(len(items), 1); self.assertEqual(len(items[0]["observedSegments"]), 2)
        self.assertTrue(any(path["source"] == "raw-support-gap" for path in items[0]["inferredSegments"]))

    def test_sparse_raw_support_is_kept_even_if_not_a_detection_sample(self):
        x = np.linspace(-.2, .2, 16)
        items, _ = verify_raw_instances(Runtime(surface(x)), [candidate()], self.p)
        self.assertEqual(len(items), 1); self.assertEqual(items[0]["rawSupportCount"], len(surface(x)))

    def test_overlapping_query_pieces_do_not_double_count_source_indices(self):
        x = np.linspace(-.2, .2, 20)
        points = surface(x)
        items, _ = verify_raw_instances(Runtime(points, duplicate_queries=True), [candidate()], self.p)
        self.assertEqual(items[0]["rawSupportCount"], len(points))

    def test_long_segment_extends_only_to_actual_raw_surface_ends(self):
        item = candidate(); item["observedSegments"] = [{"points": [[-.16, 0, 0], [.16, 0, 0]]}]
        items, _ = verify_raw_instances(Runtime(surface(np.linspace(-.19, .19, 24))), [item], self.p)
        endpoints = np.asarray(items[0]["observedSegments"][0]["points"])
        self.assertLessEqual(endpoints[0, 0], -.18); self.assertGreaterEqual(endpoints[1, 0], .18)
        self.assertLessEqual(items[0]["rawSupportCoverage"], 1)

    def test_axis_interior_points_are_not_surface_evidence(self):
        x = np.linspace(-.2, .2, 24)
        items, _ = verify_raw_instances(Runtime(np.column_stack((x, np.zeros(len(x)), np.zeros(len(x))))), [candidate()], self.p)
        self.assertEqual(items, [])

    def test_polyline_segments_are_verified_independently(self):
        first = surface(np.linspace(-.2, 0, 14))
        second = surface(np.linspace(0, .2, 14))[:, [1, 0, 2]]
        item = candidate(); item["observedSegments"] = [{"points": [[-.2, 0, 0], [0, 0, 0], [0, .2, 0]]}]
        items, _ = verify_raw_instances(Runtime(np.vstack((first, second))), [item], self.p)
        self.assertEqual(len(items), 1)
        self.assertGreaterEqual(len(items[0]["observedSegments"]), 2)

    def test_exact_duplicate_raw_support_keeps_one_owner(self):
        items, diagnostics = verify_raw_instances(Runtime(surface(np.linspace(-.2, .2, 24))),
                                                   [candidate(), candidate()], self.p)
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["rawSupportUniqueRatio"], 1.0)
        self.assertEqual(diagnostics["discardedCount"], 1)

    def test_short_observed_terminal_of_supported_parent_is_retained(self):
        item=candidate()
        item['observedSegments']=[{'points':[[-.1,0,0],[0,0,0],[0,.018,0]]}]
        first=surface(np.linspace(-.1,0,25))
        tail=surface(np.linspace(.002,.016,10))[:,[1,0,2]]
        kept,_=verify_raw_instances(Runtime(np.vstack((first,tail))),[item],self.p)
        self.assertTrue(any(np.asarray(path['points'])[-1,1]>.012 for path in kept[0]['observedSegments']))

    def test_dense_support_does_not_bridge_a_separate_low_cluster(self):
        p=Params.from_value({'min_primitive_votes':4,'axial_gap':.07})
        item=candidate();item['observedSegments']=[{'points':[[.095,0,0],[.42,0,0]]}]
        observations=surface(np.r_[np.linspace(.039,.055,12),np.linspace(.098,.419,200)])
        kept,_=verify_raw_instances(Runtime(observations),[item],p)
        self.assertGreaterEqual(np.asarray(kept[0]['observedSegments'][0]['points'])[0,0],.097)


if __name__ == "__main__": unittest.main()
