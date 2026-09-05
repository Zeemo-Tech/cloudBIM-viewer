import unittest
import numpy as np
from rebar_bim_matching import match_bim_instances
from algorithms.rebar_v4_geometry import build_segment_index, project_top2


def instance(identifier, start, end, y=0):
    points = [[start, y, 0.04], [end, y, 0.04]]
    return dict(
        id=identifier,
        centerline=points,
        observedSegments=[{"points": points}],
        radius=0.004,
        directionId=1,
        evidence="observed",
        inferredSegments=[],
    )


def design(y=0):
    return dict(id=f"bar-{y}", points=[[0, y, 0.04], [1, y, 0.04]], radius=0.004)


class BimMatchingTests(unittest.TestCase):
    def test_design_only_never_creates_scan_instances(self):
        instances = []
        result = match_bim_instances(instances, {"bars": [design()]})
        self.assertEqual(instances, [])
        self.assertEqual(result["matchedCount"], 0)

    def test_unique_fragments_share_id_but_gap_never_receives_observed_labels(self):
        instances = [instance(1, 0, 0.4), instance(2, 0.6, 1)]
        result = match_bim_instances(instances, {"bars": [design()]})
        self.assertEqual(len(instances), 1)
        self.assertEqual(result["inferredGapCount"], 1)
        self.assertEqual(instances[0]["inferredSegments"][0]["source"], "bim")
        index = build_segment_index(instances)
        attributes = project_top2(
            np.array([[0.2, 0, 0.04], [0.5, 0, 0.04], [0.8, 0, 0.04]]), index
        )
        np.testing.assert_array_equal(attributes.best_id, [1, 0, 1])

    def test_20mm_uncertainty_cannot_pick_one_of_16mm_spaced_design_bars(self):
        instances = [instance(1, 0, 1, 0.001)]
        result = match_bim_instances(
            instances,
            {"bars": [design(0), design(0.016)]},
            registration_uncertainty=0.02,
        )
        self.assertEqual(result["matchedCount"], 0)
        self.assertEqual(result["ambiguousCount"], 1)
        self.assertNotIn("designId", instances[0])

    def test_overlapping_competitors_do_not_collapse_to_one_physical_bar(self):
        instances = [instance(1, 0, 1), instance(2, 0.1, 0.9, 0.003)]
        result = match_bim_instances(instances, {"bars": [design()]})
        self.assertEqual(len(instances), 2)
        self.assertEqual(result["matchedCount"], 0)
        self.assertEqual(result["ambiguousCount"], 2)

    def test_competitor_just_outside_match_gate_still_prevents_false_certainty(self):
        instances = [instance(1, 0, 1, 0.05)]
        result = match_bim_instances(instances, {"bars": [design(0), design(0.112)]})
        self.assertEqual(result["matchedCount"], 0)
        self.assertEqual(result["ambiguousCount"], 1)

    def test_reversed_fragment_and_20mm_registration_offset(self):
        instances = [instance(1, 0.4, 0, 0.02), instance(2, 1, 0.6, 0.02)]
        result = match_bim_instances(instances, {"bars": [design()]})
        self.assertEqual(result["mergedFragmentCount"], 1)
        bridge = np.array(instances[0]["inferredSegments"][0]["points"])
        np.testing.assert_allclose(bridge[:, 1], 0.02)
        self.assertGreater(bridge[-1, 0], bridge[0, 0])

    def test_large_offset_or_wrong_direction_is_rejected(self):
        instances = [instance(1, 0, 1, 0.1)]
        self.assertEqual(
            match_bim_instances(instances, {"bars": [design()]})["matchedCount"], 0
        )
        instances = [instance(1, 0, 1)]
        prior = {
            "bars": [
                dict(
                    id="cross",
                    points=[[0.5, -0.5, 0.04], [0.5, 0.5, 0.04]],
                    radius=0.004,
                )
            ]
        }
        self.assertEqual(match_bim_instances(instances, prior)["matchedCount"], 0)


if __name__ == "__main__":
    unittest.main()
