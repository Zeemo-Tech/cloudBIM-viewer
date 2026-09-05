import unittest
from unittest.mock import patch
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

    def test_dominated_precise_stripe_cannot_erase_its_parent(self):
        parent = candidate()
        parent.update(id=1, role='planar', length=1.,
                      observedSegments=[{'points': [[0., 0, 0], [1., 0, 0]]}])
        stripe = candidate()
        stripe.update(id=2, role='planar', radius=.0095, length=.96,
                      observedSegments=[{'points': [[.02, 0, 0], [.98, 0, 0]]}])
        observations = surface(np.linspace(0., 1., 251))
        observations[:, 1:] *= .0095/.008
        for candidates in ([parent, stripe], [stripe, parent]):
            for preserve in (False, True):
                kept, diagnostics = verify_raw_instances(Runtime(observations), candidates, self.p,
                                                          preserve_association=preserve)
                physical = [item for item in kept if not item.get('_associationOnly')]
                self.assertEqual([item['id'] for item in physical], [1])
                self.assertEqual(diagnostics['verifiedCount'], 1)

    def test_failed_owner_axis_reverification_does_not_suppress_unique_tail(self):
        parent=candidate()
        parent.update(id=1,role='planar',length=1.,observedSegments=[{'points':[[0,0,0],[1,0,0]]}])
        tail=candidate()
        tail.update(id=2,role='planar',length=.35,observedSegments=[{'points':[[-.25,0,0],[.1,0,0]]}])
        with patch('algorithms.rebar_v5.verification._append_transferred_support',return_value=np.empty(0,np.uint64)):
            kept,_=verify_raw_instances(Runtime(surface(np.linspace(-.25,1.,251))),[parent,tail],self.p)
        self.assertEqual([item['id'] for item in kept],[1,2])

    def test_surface_stripe_transfers_continuous_tail_to_parent_in_both_orders(self):
        parent = candidate()
        parent.update(id=1, role='planar', length=1., centerline=[[0., 0, 0], [1., 0, 0]],
                      observedSegments=[{'points': [[0., 0, 0], [1., 0, 0]]}])
        stripe = candidate()
        stripe.update(id=2, role='planar', length=.35, centerline=[[-.25, 0, 0], [.1, 0, 0]],
                      observedSegments=[{'points': [[-.25, 0, 0], [.1, 0, 0]]}])
        observations = surface(np.linspace(-.25, 1., 251))
        for candidates in ([parent, stripe], [stripe, parent]):
            kept, _ = verify_raw_instances(Runtime(observations), candidates, self.p)
            self.assertEqual(len(kept), 1)
            self.assertEqual(kept[0]['id'], 1)
            points = np.vstack([path['points'] for path in kept[0]['observedSegments']])
            self.assertAlmostEqual(points[:, 0].min(), -.25)
            self.assertEqual(kept[0]['rawSupportCount'], len(observations))
            self.assertTrue(np.allclose(points[:, 1:], 0))

    def test_surface_stripe_tail_gap_remains_inferred(self):
        parent = candidate()
        parent.update(id=1, role='planar', length=1., observedSegments=[{'points': [[0., 0, 0], [1., 0, 0]]}])
        stripe = candidate()
        stripe.update(id=2, role='planar', length=.4, observedSegments=[{'points': [[-.3, 0, 0], [.1, 0, 0]]}])
        observations = surface(np.r_[np.linspace(-.3, -.2, 30), np.linspace(-.05, 1., 211)])
        kept, _ = verify_raw_instances(Runtime(observations), [parent, stripe], self.p)
        self.assertEqual(len(kept), 1)
        self.assertAlmostEqual(min(p['points'][0][0] for p in kept[0]['observedSegments']), -.3)
        for path in kept[0]['observedSegments']:
            ends = np.asarray(path['points'])[:, 0]
            self.assertFalse(ends.min() < -.1 < ends.max())
        self.assertTrue(any(np.min(np.asarray(p['points'])[:, 0]) < -.1 < np.max(np.asarray(p['points'])[:, 0])
                            for p in kept[0]['inferredSegments']))

    def test_biased_stripe_tail_counts_all_reverified_parent_surface_records(self):
        parent = candidate()
        parent.update(id=1, role='planar', length=1.,
                      observedSegments=[{'points': [[0., 0, 0], [1., 0, 0]]}])
        stripe = candidate()
        stripe.update(id=2, role='planar', radius=.004, length=.35,
                      observedSegments=[{'points': [[-.25, 0, .004], [.1, 0, .004]]}])
        observations = np.asarray([[x, .008*np.cos(a), .008*np.sin(a)]
                                   for x in np.linspace(-.25, 1., 251)
                                   for a in np.linspace(0, 2*np.pi, 12, endpoint=False)])
        for candidates in ([parent, stripe], [stripe, parent]):
            kept, _ = verify_raw_instances(Runtime(observations), candidates, self.p)
            self.assertEqual(len(kept), 1)
            points = np.vstack([path['points'] for path in kept[0]['observedSegments']])
            self.assertAlmostEqual(points[:, 0].min(), -.25)
            self.assertTrue(np.allclose(points[:, 1:], 0))
            self.assertEqual(kept[0]['rawSupportCount'], len(observations))

    def test_three_level_tail_support_reaches_root_before_next_candidate(self):
        from itertools import permutations
        items=[]
        for ident, lo, hi in ((1, .15, 1.), (2, -.1, .4), (3, -.3, .05)):
            item=candidate()
            item.update(id=ident,role='planar',length=hi-lo,
                        observedSegments=[{'points':[[lo,0,0],[hi,0,0]]}])
            items.append(item)
        observations=surface(np.linspace(-.3,1.,261))
        for ordered in permutations(items):
            for preserve in (False, True):
                kept,diagnostic=verify_raw_instances(Runtime(observations),list(ordered),self.p,
                                                     preserve_association=preserve)
                physical=[item for item in kept if not item.get('_associationOnly')]
                self.assertEqual([item['id'] for item in physical],[1])
                self.assertEqual(physical[0]['rawSupportCount'],len(observations))
                self.assertAlmostEqual(min(path['points'][0][0] for path in physical[0]['observedSegments']),-.3)
                self.assertEqual(diagnostic['verifiedCount'],1)

    def test_root_extension_does_not_absorb_a_later_tail_on_another_axis(self):
        from itertools import permutations
        items=[]
        for ident, lo, hi, y in ((1,.15,1.,0.), (2,-.1,.4,0.), (3,-.3,-.15,.012)):
            item=candidate()
            item.update(id=ident,role='planar',length=hi-lo,
                        observedSegments=[{'points':[[lo,y,0],[hi,y,0]]}])
            items.append(item)
        observations=np.vstack((surface(np.linspace(-.1,1.,221)),
                                surface(np.linspace(-.3,-.15,31))+[0,.012,0]))
        for ordered in permutations(items):
            kept,_=verify_raw_instances(Runtime(observations),list(ordered),self.p)
            self.assertEqual(sorted(item['id'] for item in kept),[1,3])
            leaf=next(item for item in kept if item['id']==3)
            self.assertTrue(np.allclose(np.vstack([path['points'] for path in leaf['observedSegments']])[:,1],.012))

    def test_hook_local_arm_suppresses_biased_stripe_without_changing_curve(self):
        parent = candidate()
        parent.update(id=1, role='planar', length=2., observedSegments=[{'points': [[0., 0, 0], [1., 0, 0], [1., 1., 0]]}])
        stripe = candidate()
        stripe.update(id=2, role='planar', radius=.004, length=.6,
                      observedSegments=[{'points': [[.2, 0, .004], [.8, 0, .004]]}])
        straight = surface(np.linspace(0., 1., 151))
        turn = surface(np.linspace(0., 1., 151))[:, [1, 0, 2]]+[1., 0, 0]
        for candidates in ([parent, stripe], [stripe, parent]):
            kept, _ = verify_raw_instances(Runtime(np.vstack((straight, turn))), candidates, self.p)
            self.assertEqual(len(kept), 1)
            self.assertEqual(kept[0]['id'], 1)
            paths = np.vstack([p['points'] for p in kept[0]['observedSegments']])
            self.assertTrue(np.allclose(paths[:, 2], 0))
            self.assertGreater(paths[:, 1].max(), .99)

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

    def test_duplicate_owner_selection_is_independent_of_candidate_order(self):
        precise = candidate(); precise["id"] = 10
        broad = candidate(); broad["id"] = 20; broad["radius"] = .010
        points = surface(np.linspace(-.2, .2, 24))
        signatures = []
        for candidates in ((precise, broad), (broad, precise)):
            kept, _ = verify_raw_instances(Runtime(points), candidates, self.p, preserve_association=True)
            retained = [item for item in kept if not item.get("_associationOnly")]
            self.assertEqual(len(retained), 1)
            signatures.append((retained[0]["id"], retained[0]["rawSupportCount"]))
        self.assertEqual(signatures[0], signatures[1])

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
