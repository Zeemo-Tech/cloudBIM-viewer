import unittest

from algorithms.design_evidence import evaluate_evidence, retry_decision
from algorithms.design_evidence_contract import Candidate, Evidence, Reason, State
import numpy as np


def measured(**extra):
    base = dict(point_count=60, occupied_cells=16, occupied_bins=4, span_m=.024,
                cylinder_error_m=.0007, radial_alignment=.86, normal_valid_fraction=.8,
                arc_degrees=110, source_ids={"cylinder_error_m": ["row-1", "row-1"], "radial_alignment": ["row-2"]})
    base.update(extra)
    return base


class DesignEvidenceTests(unittest.TestCase):
    def test_real_competing_measured_plane_wins(self):
        result = evaluate_evidence(measured(plane_error_m=.0003, plane_coherence=.9, fixture_fraction=.7,
                                            source_ids={"plane_error_m": ["plane-row"]}))
        self.assertEqual((result.state, result.reason), (State.REJECTED_OBSERVED, Reason.PLANE_WINS))
        self.assertIn("plane-row", result.negative)

    def test_sparse_invalid_normals_are_missing_not_negative(self):
        result = evaluate_evidence(measured(occupied_cells=2, normal_valid_fraction=0, arc_degrees=20))
        self.assertEqual(result.state, State.INSUFFICIENT)
        self.assertEqual(result.reason, Reason.MISSING_NORMALS)
        self.assertIn("normals", result.missing)
        self.assertFalse(result.negative)

    def test_positive_measured_partial_cylinder_and_duplicate_source_vote(self):
        result = evaluate_evidence(measured(arc_degrees=180))
        self.assertEqual(result.state, State.CONFIRMED)
        self.assertEqual(result.positive.count("row-1"), 1)

    def test_unsupported_design_is_not_accepted_or_positive_evidence(self):
        result = evaluate_evidence(dict(design_only=True))
        self.assertEqual(result.state, State.INSUFFICIENT)
        self.assertFalse(result.accepted)
        self.assertFalse(result.anchor_eligible)
        self.assertFalse(result.positive)
        self.assertIn("measured-support", result.missing)

    def test_under_minimum_arc_and_invalid_normals_cannot_confirm(self):
        self.assertEqual(evaluate_evidence(measured(arc_degrees=19)).state, State.INSUFFICIENT)
        result = evaluate_evidence(measured(normal_valid_fraction=0, arc_degrees=180))
        self.assertEqual(result.state, State.INSUFFICIENT)
        self.assertIn("normals", result.missing)

    def test_score_is_bounded_and_rewards_better_measured_residual(self):
        better = evaluate_evidence(measured(cylinder_error_m=.0002, radial_alignment=.95))
        worse = evaluate_evidence(measured(cylinder_error_m=.0012, radial_alignment=.73))
        self.assertLessEqual(better.score, 1.)
        self.assertGreaterEqual(worse.score, 0.)
        self.assertGreater(better.score, worse.score)

    def test_self_and_provisional_anchors_are_rejected(self):
        candidate = Candidate("candidate", np.empty((0,)), {}, {}, provenance={"source_ids": ["a"]})
        self_anchor = Candidate("candidate", np.empty((0,)), {}, {}, evidence=Evidence(State.CONFIRMED, Reason.CYLINDER_SUPPORT), provenance={"source_ids": ["b"]})
        overlapping = Candidate("other", np.empty((0,)), {}, {}, evidence=Evidence(State.CONFIRMED, Reason.CYLINDER_SUPPORT), provenance={"source_ids": ["a"]})
        provisional = Candidate("third", np.empty((0,)), {}, {}, evidence=Evidence(State.PROVISIONAL, Reason.PARTIAL_ARC), provenance={"source_ids": ["b"]})
        decision = retry_decision(candidate, [], anchors=[self_anchor, overlapping, provisional])
        self.assertEqual(decision["action"], "adjust_support_or_normals")

    def test_max_retries_and_no_added_observations_stop(self):
        candidate = Candidate("candidate", np.empty((0,)), {}, {}, attempt=2, provenance={"source_ids": ["a"], "support_signature": "same"})
        self.assertEqual(retry_decision(candidate, [])["reason"], "max_retries")
        candidate.attempt = 1
        self.assertEqual(retry_decision(candidate, [{"attempt": 0, "source_ids": ["a"], "support_signature": "same"}])["reason"], "no_new_support")

    def test_row_ids_are_independent_anchor_sources_and_neighbors_are_second_retry(self):
        candidate = Candidate("candidate", np.array([1, 2]), {}, {}, attempt=1)
        anchor = Candidate("anchor", np.array([3, 4]), {}, {}, evidence=Evidence(State.CONFIRMED, Reason.CYLINDER_SUPPORT))
        decision = retry_decision(candidate, [{"attempt": 0, "source_ids": ["old"]}], anchors=[anchor])
        self.assertEqual(decision["action"], "compare_neighbors")
        first = Candidate("first", np.array([1]), {}, {})
        self.assertEqual(retry_decision(first, [], anchors=[anchor])["action"], "adjust_support_or_normals")


if __name__ == "__main__":
    unittest.main()
