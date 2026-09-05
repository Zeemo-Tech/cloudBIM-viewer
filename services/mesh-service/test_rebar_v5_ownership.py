import unittest
from copy import deepcopy

import numpy as np

from algorithms.rebar_v5.contracts import Params
from algorithms.rebar_v5.ownership import merge_fragments
from algorithms.rebar_v5 import GeometricV5Adapter
from rebar_validation import REBAR, make_truth_scene


def fragment(points, *, role="planar", radius=.008, support=40):
    points = np.asarray(points, dtype=float)
    return {
        "id": 99, "role": role, "radius": radius, "confidence": .8,
        "length": float(np.sum(np.linalg.norm(np.diff(points, axis=0), axis=1))),
        "centerline": points.tolist(), "observedSegments": [{"points": points.tolist()}],
        "inferredSegments": [], "rawSupportCount": support,
        "_rawSupportIndices": np.arange(support, dtype=np.uint64),
    }


class OwnershipTests(unittest.TestCase):
    def setUp(self):
        self.p = Params.from_value({"min_primitive_votes": 5, "min_primitive_length": .03,
                                    "min_instance_length": .03})

    def test_overlapping_collinear_fragments_become_one_instance(self):
        first = fragment([[-.20, 0, 0], [.08, 0, 0]])
        duplicate = fragment([[-.05, .001, 0], [.20, .001, 0]], support=55)
        merged = merge_fragments([first, duplicate], self.p)
        self.assertEqual(len(merged), 1)
        self.assertEqual(merged[0]["rawSupportCount"], 55)
        self.assertEqual(len(merged[0]["observedSegments"]), 2)
        self.assertAlmostEqual(merged[0]["length"], .4, places=5)
        line = np.asarray(merged[0]["centerline"])
        self.assertTrue(np.allclose(line[0], [-.2, 0, 0]))
        self.assertTrue(np.allclose(line[-1], [.2, 0, 0]))

    def test_curve_vertices_are_never_replaced_by_a_chord(self):
        curve = fragment([[0, 0, 0], [.03, .01, 0], [.05, .04, 0]])
        overlap = fragment([[.02, .006, 0], [.04, .02, 0], [.055, .055, 0]])
        merged = merge_fragments([curve, overlap], self.p)
        self.assertEqual(len(merged), 1)
        output = np.vstack([np.asarray(path["points"]) for path in merged[0]["observedSegments"]])
        for point in np.vstack((curve["observedSegments"][0]["points"], overlap["observedSegments"][0]["points"])):
            self.assertLessEqual(np.min(np.linalg.norm(output-np.asarray(point), axis=1)), .001)

    def test_16mm_parallel_pair_is_never_merged(self):
        first = fragment([[-.2, -.008, 0], [.2, -.008, 0]], radius=.004)
        second = fragment([[-.2, .008, 0], [.2, .008, 0]], radius=.004)
        self.assertEqual(len(merge_fragments([first, second], self.p)), 2)

    def test_roles_do_not_merge_at_a_contact(self):
        planar = fragment([[-.2, 0, 0], [0, 0, 0]], role="planar")
        web = fragment([[0, 0, 0], [.2, 0, 0]], role="web")
        self.assertEqual(len(merge_fragments([planar, web], self.p)), 2)

    def test_hook_segments_share_one_parent_even_with_coarse_turn(self):
        stem = fragment([[-.16, 0, 0], [0, 0, 0]])
        hook = fragment([[.001, 0, 0], [.11, .11, 0]])
        merged = merge_fragments([stem, hook], self.p)
        self.assertEqual(len(merged), 1)
        self.assertEqual(len(merged[0]["observedSegments"]), 2)
        line = np.asarray(merged[0]["centerline"])
        self.assertFalse(np.allclose(line[0], line[-1]))
        self.assertTrue(np.allclose(line[0], [-.16, 0, 0]) or np.allclose(line[-1], [-.16, 0, 0]))
        self.assertTrue(np.allclose(line[0], [.11, .11, 0]) or np.allclose(line[-1], [.11, .11, 0]))

    def test_association_gap_stays_inferred_and_is_not_observed(self):
        left = fragment([[-.20, 0, 0], [-.025, 0, 0]])
        right = fragment([[.025, 0, 0], [.20, 0, 0]])
        merged = merge_fragments([left, right], self.p)
        self.assertEqual(len(merged), 1)
        self.assertEqual(len(merged[0]["observedSegments"]), 2)
        bridges = [x for x in merged[0]["inferredSegments"] if x.get("source") == "unique-fragment-association"]
        self.assertEqual(len(bridges), 1)
        self.assertGreater(np.linalg.norm(np.subtract(*bridges[0]["points"])), .04)

    def test_fragment_permutation_has_identical_geometry(self):
        left = fragment([[-.20, 0, 0], [-.03, 0, 0]], support=40)
        right = fragment([[.03, 0, 0], [.20, 0, 0]], support=40)
        duplicate = fragment([[-.19, .001, 0], [.02, .001, 0]], support=40)
        hook = fragment([[.201, 0, 0], [.28, .08, 0]], support=40)
        for index, item in enumerate((left, right, duplicate, hook), 1):
            item["id"] = index
            item["_rawSupportIndices"] = np.arange((index-1)*40, index*40, dtype=np.uint64)
        def signature(items):
            merged = merge_fragments(items, self.p)
            self.assertEqual(len(merged), 1)
            result = merged[0]
            return (result["rawSupportCount"], result["length"], result["centerline"],
                    result["observedSegments"], result["inferredSegments"])
        expected = signature([left, right, duplicate, hook])
        self.assertEqual(expected, signature([hook, duplicate, right, left]))

    def test_association_only_attaches_one_parent_but_never_emits_its_geometry(self):
        left = fragment([[-.2, 0, 0], [0, 0, 0]], support=40)
        right = fragment([[.10, 0, 0], [.2, 0, 0]], support=40)
        bridge = fragment([[-.1, 0, 0], [.1, 0, 0]], support=80)
        bridge["_associationOnly"] = True
        bridge["_rawSupportIndices"] = np.arange(80, dtype=np.uint64)
        left["_rawSupportIndices"] = np.arange(40, dtype=np.uint64)
        right["_rawSupportIndices"] = np.arange(40, 80, dtype=np.uint64)
        merged = merge_fragments([left, right, bridge], self.p)
        self.assertEqual(len(merged), 2)
        self.assertNotIn("_associationOnly", merged[0])
        self.assertNotIn("_rawSupportIndices", merged[0])
        output = np.vstack([np.asarray(path["points"]) for path in merged[0]["observedSegments"]])
        self.assertFalse(any(np.allclose(point, [-.1, 0, 0]) for point in output))

    def test_association_only_stripe_cannot_join_distinct_parallel_axes(self):
        """A low-unique stripe may overlap both rods but cannot become their union path."""
        lower = fragment([[-.2, -.008, 0], [.2, -.008, 0]], radius=.004, support=40)
        upper = fragment([[-.2, .008, 0], [.2, .008, 0]], radius=.004, support=40)
        stripe = fragment([[-.2, -.008, 0], [.2, -.008, 0], [.2, .008, 0], [-.2, .008, 0]],
                          radius=.004, support=80)
        stripe["_associationOnly"] = True
        merged = merge_fragments([lower, upper, stripe], self.p)
        self.assertEqual(len(merged), 2)

    def test_unflagged_low_unique_bridge_cannot_collapse_saved_16mm_pair(self):
        """Saved 8/10/17 topology: removing the flag must still retain both rods."""
        lower = fragment([[-1.5, -.550, .026], [1.5, -.550, .026]], radius=.004, support=900)
        upper = fragment([[-1.5, -.534, .026], [1.5, -.534, .026]], radius=.004, support=900)
        bridge = fragment([[-1.1086, -.550, .026], [-1.0472, -.550, .026]], radius=.004, support=39)
        bridge["observedSegments"] = [
            {"points": [[-1.1086, -.550, .026], [-1.0472, -.550, .026]]},
            {"points": [[-1.2513, -.534, .026], [-1.2127, -.534, .026]]},
        ]
        bridge["centerline"] = [[-1.1086, -.550, .026], [-1.0472, -.550, .026],
                                [-1.2127, -.534, .026], [-1.2513, -.534, .026]]
        bridge["inferredSegments"] = [{"points": [[-1.0472, -.550, .026], [-1.2127, -.534, .026]],
                                        "source": "raw-support-gap"}]
        lower["_rawSupportIndices"] = np.arange(900, dtype=np.uint64)
        upper["_rawSupportIndices"] = np.arange(900, 1800, dtype=np.uint64)
        bridge["_rawSupportIndices"] = np.r_[np.arange(18, dtype=np.uint64),
                                               np.arange(900, 921, dtype=np.uint64)]
        before = deepcopy(bridge)
        for ratio in (.06, .30):
            bridge["rawSupportUniqueRatio"] = ratio
            outputs = merge_fragments([lower, bridge, upper], self.p)
            self.assertGreaterEqual(len(outputs), 2)
            parents = []
            for item in outputs:
                for path in item["observedSegments"]:
                    points = np.asarray(path["points"])
                    if np.linalg.norm(points[-1]-points[0]) > 2.9:
                        parents.append((round(float(points[0, 1]), 3), item["rawSupportCount"]))
            self.assertEqual(sorted(parents), [(-.55, 900), (-.534, 900)])
            self.assertTrue(all(len(item["observedSegments"]) == 1 for item in outputs))
            self.assertTrue(all(not item["inferredSegments"] for item in outputs))
        self.assertEqual(bridge["centerline"], before["centerline"])
        self.assertEqual(bridge["observedSegments"], before["observedSegments"])
        self.assertEqual(bridge["inferredSegments"], before["inferredSegments"])
        self.assertTrue(np.array_equal(bridge["_rawSupportIndices"], before["_rawSupportIndices"]))

    def test_association_only_stripe_cannot_join_split_measured_near_rod(self):
        lower = fragment([[-.2, -.008, 0], [.2, -.008, 0]], radius=.004, support=40)
        lower["observedSegments"] = [
            {"points": [[-.2, -.008, 0], [0, -.008, 0]]},
            {"points": [[0, -.008, 0], [.2, -.008, 0]]},
        ]
        upper = fragment([[-.2, .008, 0], [.2, .008, 0]], radius=.004, support=40)
        stripe = fragment([[-.2, -.008, 0], [.2, -.008, 0], [.2, .008, 0], [-.2, .008, 0]],
                          radius=.004, support=80)
        stripe["_associationOnly"] = True
        for items in ((lower, stripe, upper), (upper, stripe, lower)):
            self.assertEqual(len(merge_fragments(list(items), self.p)), 2)

    def test_association_only_endpoint_cannot_bypass_support_gate(self):
        lower = fragment([[-.2, -.008, 0], [0, -.008, 0]], radius=.004, support=40)
        upper = fragment([[.044, .008, 0], [.2, .008, 0]], radius=.004, support=40)
        bridge = fragment([[.002, -.008, 0], [.042, .008, 0]], radius=.004, support=20)
        lower["_rawSupportIndices"] = np.arange(40, dtype=np.uint64)
        upper["_rawSupportIndices"] = np.arange(40, 80, dtype=np.uint64)
        bridge["_rawSupportIndices"] = np.arange(80, 100, dtype=np.uint64)
        bridge["_associationOnly"] = True
        merged = merge_fragments([lower, bridge, upper], self.p)
        self.assertEqual(len(merged), 2)
        self.assertFalse(any(np.allclose(point, [.002, -.008, 0])
                             for item in merged for path in item["observedSegments"]
                             for point in path["points"]))

    def test_transitive_overlap_cannot_join_distinct_parallel_axes_in_any_order(self):
        lower = fragment([[-.2, -.008, 0], [.2, -.008, 0]], radius=.004, support=40)
        upper = fragment([[-.2, .008, 0], [.2, .008, 0]], radius=.004, support=40)
        stripe = fragment([[-.2, -.008, 0], [.2, -.008, 0], [.2, .008, 0], [-.2, .008, 0]],
                          radius=.004, support=80)
        stripe["_associationOnly"] = True
        for items in ((lower, stripe, upper), (upper, stripe, lower)):
            with self.subTest(order=[item["centerline"][0][1] for item in items]):
                merged = merge_fragments(list(items), self.p)
                self.assertEqual(len(merged), 2)
                self.assertTrue(any(np.isclose(path["points"][0][1], -.008)
                                    for item in merged for path in item["observedSegments"]))
                self.assertTrue(any(np.isclose(path["points"][0][1], .008)
                                    for item in merged for path in item["observedSegments"]))

    def test_merged_raw_support_count_is_the_exact_source_index_union(self):
        first = fragment([[-.2, 0, 0], [.08, 0, 0]], support=40)
        second = fragment([[-.05, .001, 0], [.2, .001, 0]], support=55)
        second["_rawSupportIndices"] = np.arange(20, 75, dtype=np.uint64)
        merged = merge_fragments([first, second], self.p)
        self.assertEqual(len(merged), 1)
        self.assertEqual(merged[0]["rawSupportCount"], 75)

    def test_singleton_cleans_private_evidence_and_drops_association_only(self):
        item = fragment([[0, 0, 0], [.2, 0, 0]])
        item["_rawSupportIndices"] = np.arange(4, dtype=np.uint64)
        output = merge_fragments([item], self.p)
        self.assertEqual(len(output), 1)
        self.assertNotIn("_rawSupportIndices", output[0])
        item["_associationOnly"] = True
        self.assertEqual(merge_fragments([item], self.p), [])

    def test_physical_hook_has_one_dominant_parent_after_ownership(self):
        """Use the full raw cloud: hook labels must not remain fragment IDs."""
        truth = make_truth_scene(seed=20260905, top_arcs=True)
        adapter = GeometricV5Adapter()
        analysis = adapter.analyze(truth.points, adapter.normalize_parameters({"detection_point_limit": 100000}))
        try:
            attrs = adapter.project_points(truth.points, analysis)
            hook = (truth.scene == REBAR) & (truth.instance == truth.hook_instance)
            ids = attrs.rebar_instance[hook]
            ids = ids[(ids > 0) & (ids < np.iinfo(np.uint32).max)]
            self.assertGreaterEqual(len(ids), .95*np.count_nonzero(hook))
            self.assertGreater(np.max(np.bincount(ids.astype(int)))/len(ids), .90)
            # The saved regression was not visible in aggregate metrics: both
            # 16 mm rods could be absorbed by one otherwise plausible model.
            close_ids = []
            for truth_id in (3, 4):
                assigned = attrs.rebar_instance[(truth.scene == REBAR) & (truth.instance == truth_id)]
                assigned = assigned[(assigned > 0) & (assigned < np.iinfo(np.uint32).max)]
                self.assertGreaterEqual(len(assigned), 850)
                close_ids.append(int(np.bincount(assigned.astype(int)).argmax()))
            self.assertNotEqual(*close_ids)
            baseline = attrs.rebar_instance[(truth.scene == REBAR) & (truth.instance == 1)]
            self.assertTrue(np.all((baseline > 0) & (baseline < np.iinfo(np.uint32).max)))
            self.assertEqual(len(np.unique(baseline)), 1, 'baseline tail stripe retained a duplicate instance')
        finally:
            adapter.close(analysis)


if __name__ == "__main__":
    unittest.main()
