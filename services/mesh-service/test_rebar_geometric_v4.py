from __future__ import annotations

import unittest
from unittest.mock import patch

import numpy as np

from algorithms.rebar_base import RebarAnalysis
from algorithms.rebar_geometric_v4 import (
    AMBIGUOUS_INSTANCE,
    FLAG_AMBIGUOUS,
    FLAG_CROSSING,
    GeometricV4Adapter,
    _Params,
    SCENE_FIXTURE,
    SCENE_REBAR,
    SCENE_TABLE,
)
from algorithms.rebar_v4_geometry import (
    LinePrimitive,
    LocalFeatures,
    PlaneModel,
    trace_primitive_graph,
)
from algorithms.rebar_v4_postprocess import refine_v4_evidence
from rebar_validation import run


def _analysis(entries, *, surfaces=()):
    return RebarAnalysis(
        {
            "algorithmDetails": {
                "projection": {"instances": entries},
                "plane": None,
                "fixture": {"surfaces": list(surfaces)},
                "parameters": {
                    "ambiguity_margin": 0.16,
                    "crossing_angle_degrees": 40.0,
                    "table_distance": 0.004,
                },
            }
        }
    )


def _entry(identifier, points, *, radius=0.012, direction=1):
    return {
        "id": identifier,
        "directionId": direction,
        "centerline": points,
        "observedSegments": [{"points": points}],
        "radius": radius,
        "evidence": "observed",
    }


class GeometricV4ProtocolTests(unittest.TestCase):
    def setUp(self):
        self.algorithm = GeometricV4Adapter()

    def test_descriptor_exposes_fixture_and_legacy_palette(self):
        descriptor = self.algorithm.descriptor
        self.assertIn("experimental", descriptor["name"])
        self.assertEqual(descriptor["version"], "5")
        self.assertTrue(descriptor["capabilities"]["rawLabels"])
        self.assertTrue(descriptor["capabilities"]["bimPrior"])
        self.assertFalse(descriptor["capabilities"]["confidence"])
        values = descriptor["visualization"]["values"]
        self.assertEqual(values["sceneClass"]["fixture_formwork"], SCENE_FIXTURE)
        for key in ("directionA", "directionB", "intersection"):
            self.assertIn(key, descriptor["visualization"]["colors"])
        self.assertEqual(
            descriptor["parameterSchema"]["properties"]["fixture_min_width"]["unit"],
            "m",
        )

    def test_ambiguity_sentinel_and_candidate_csr_match_raw_attributes(self):
        entries = [
            _entry(1, [[0, 0, 0.04], [1, 0, 0.04]], direction=1),
            _entry(2, [[0, 0.01, 0.04], [1, 0.01, 0.04]], direction=1),
        ]
        fixture = {
            "origin": [0.75, 0.005, 0.04],
            "normal": [0, 0, 1],
            "axes": [[1, 0, 0], [0, 1, 0]],
            "halfExtent": [0.1, 0.02],
            "distance": 0.002,
        }
        points = np.array([[0.25, 0.005, 0.04], [0.75, 0.005, 0.04]])
        analysis = _analysis(entries, surfaces=[fixture])
        attributes = self.algorithm.project_points(points, analysis)
        self.assertEqual(int(attributes.rebar_instance[0]), int(AMBIGUOUS_INSTANCE))
        self.assertEqual(int(attributes.rebar_flags[0]), FLAG_AMBIGUOUS)
        self.assertEqual(int(attributes.scene_class[1]), SCENE_FIXTURE)
        candidates = self.algorithm.project_candidates(points, analysis)
        np.testing.assert_array_equal(candidates["point_indices"], [0])
        np.testing.assert_array_equal(candidates["offsets"], [0, 2])
        np.testing.assert_array_equal(candidates["instance_ids"], [1, 2])

    def test_decisive_crossing_keeps_physical_instance(self):
        entries = [
            _entry(1, [[0, 0, 0.04], [1, 0, 0.04]], direction=1),
            _entry(2, [[0.5, -1, 0.04], [0.5, 1, 0.04]], direction=2),
        ]
        point = np.array([[0.508, 0.001, 0.04]])
        attributes = self.algorithm.project_points(point, _analysis(entries))
        self.assertEqual(int(attributes.scene_class[0]), SCENE_REBAR)
        self.assertEqual(int(attributes.rebar_instance[0]), 1)
        self.assertEqual(int(attributes.rebar_flags[0]), FLAG_CROSSING)

    def test_table_and_fixture_points_have_no_rebar_labels_or_flags(self):
        entries = [_entry(1, [[0, 0, 0], [1, 0, 0]], direction=1)]
        fixture = {
            "origin": [0.5, 0, 0], "normal": [0, 0, 1],
            "axes": [[1, 0, 0], [0, 1, 0]], "halfExtent": [0.1, 0.1],
            "distance": 0.01,
        }
        analysis = _analysis(entries, surfaces=[fixture])
        analysis.data["algorithmDetails"]["plane"] = {
            "origin": [0, 0, 0], "normal": [0, 0, 1],
            "axes": [[1, 0, 0], [0, 1, 0]], "hull": [[-2, -2], [2, -2], [2, 2]],
        }
        attrs = self.algorithm.project_points(np.array([[0.1, 0, 0], [0.5, 0, 0]]), analysis)
        for row in (0, 1):
            self.assertEqual(int(attrs.rebar_class[row]), 0)
            self.assertEqual(int(attrs.rebar_direction[row]), 0)
            self.assertEqual(int(attrs.rebar_instance[row]), 0)
            self.assertEqual(int(attrs.rebar_flags[row]), 0)
        self.assertEqual(int(attrs.scene_class[0]), SCENE_TABLE)
        self.assertEqual(int(attrs.scene_class[1]), SCENE_FIXTURE)


    # The pure post-processing seam remains public enough for deterministic
    # geometry tests while adapter tests above cover its output contract.
    @staticmethod
    def primitive(a, b, *, votes=12, score=1.0):
        a, b = np.asarray(a, float), np.asarray(b, float)
        return LinePrimitive(a, b, (b - a) / np.linalg.norm(b - a), 0.004, votes, score)

    def test_postprocess_preserves_initial_fixture_evidence_without_support(self):
        retained = {"origin": [0, 0, 0.04], "normal": [0, 0, 1], "axes": [[1, 0, 0], [0, 1, 0]], "halfExtent": [0.20, 0.02], "distance": 0.006, "supportCount": 20, "coverage": 0.75}
        edge = {**retained, "halfExtent": [0.20, 0.004]}
        result = refine_v4_evidence([retained, edge], [], np.empty((0, 3)), max_radius=0.012)
        self.assertEqual(result.fixture_surfaces, (retained, edge))
        self.assertEqual(result.diagnostics["fixtureCompleted"], 0)

    def test_sparse_3d_diagonal_candidate_requires_observed_support(self):
        # Deliberately diagonal only in the table plane: recovery must not be
        # coupled to the world's Z axis.
        diagonal = self.primitive([0, 0, 0.02], [0.08, 0.08, 0.02], votes=6, score=0.95)
        support = np.linspace(diagonal.start, diagonal.end, 8)
        accepted = refine_v4_evidence([], [], support, max_radius=0.012, recovery_candidates=[diagonal])
        self.assertEqual(accepted.primitives, (diagonal,))
        rejected = refine_v4_evidence([], [], support[:3], max_radius=0.012, recovery_candidates=[diagonal])
        self.assertEqual(rejected.primitives, ())
        self.assertEqual(rejected.diagnostics["diagonalRecoveryRejectedUnsupported"], 1)

    def test_hook_join_requires_unique_locally_supported_continuation(self):
        first = self.primitive([0, 0, 0], [0.10, 0, 0])
        second = self.primitive([0.125, 0.01, 0], [0.20, 0.06, 0])
        supported = np.vstack((np.linspace(first.start, first.end, 8), np.linspace(second.start, second.end, 8)))
        unique = refine_v4_evidence([], [first, second], supported, max_radius=0.012)
        self.assertEqual(unique.diagnostics["hookJoinCandidates"], 1)
        self.assertEqual(unique.diagnostics["hookJoinAccepted"], 1)
        trace_options = dict(join_gap=0.09, observed_join_gap=0.04, maximum_turn_degrees=32, minimum_instance_length=0.08)
        self.assertEqual(
            len(trace_primitive_graph(list(unique.primitives), join_overrides=unique.hook_join_overrides, **trace_options)),
            1,
        )
        competing = self.primitive([0.125, -0.01, 0], [0.20, -0.06, 0])
        blocked = refine_v4_evidence([], [first, second, competing], supported, max_radius=0.012)
        self.assertEqual(blocked.diagnostics["hookJoinAccepted"], 0)
        self.assertEqual(
            len(trace_primitive_graph(list(blocked.primitives), join_overrides=blocked.hook_join_overrides, **trace_options)),
            3,
        )
        crossing = self.primitive([0.10, -0.05, 0], [0.10, 0.05, 0])
        blocked_crossing = refine_v4_evidence([], [first, second, crossing], supported, max_radius=0.012)
        self.assertEqual(blocked_crossing.diagnostics["hookJoinAccepted"], 0)
        self.assertEqual(
            len(trace_primitive_graph(list(blocked_crossing.primitives), join_overrides=blocked_crossing.hook_join_overrides, **trace_options)),
            3,
        )

    def test_hook_join_allows_a_unique_three_fragment_chain(self):
        first = self.primitive([0, 0, 0], [0.10, 0, 0])
        middle = self.primitive([0.12, 0.01, 0], [0.18, 0.07, 0])
        last = self.primitive([0.19, 0.095, 0], [0.19, 0.18, 0])
        support = np.vstack(
            [np.linspace(item.start, item.end, 8) for item in (first, middle, last)]
        )
        refined = refine_v4_evidence([], [first, middle, last], support, max_radius=0.012)
        self.assertEqual(refined.diagnostics["hookJoinCandidates"], 2)
        self.assertEqual(refined.diagnostics["hookJoinAccepted"], 2)
        traced = trace_primitive_graph(
            list(refined.primitives),
            join_gap=0.09,
            observed_join_gap=0.04,
            maximum_turn_degrees=32,
            minimum_instance_length=0.08,
            join_overrides=refined.hook_join_overrides,
        )
        self.assertEqual(len(traced), 1)

    @staticmethod
    def _features(points, tangent=(1, 0, 0), *, linearity=0.9, planarity=0.05):
        points = np.asarray(points, float)
        count = len(points)
        return LocalFeatures(
            points, np.arange(count), np.tile(tangent, (count, 1)),
            np.full(count, linearity), np.full(count, planarity),
            np.tile([0, 0, 1], (count, 1)), np.full(count, 12, np.int32),
        )

    @staticmethod
    def _off_table_plane():
        return PlaneModel(
            np.zeros(3), np.array([0, 0, 1.0]), np.eye(3)[:2],
            np.arange(3), np.array([[-2, -2], [2, -2], [2, 2], [-2, 2]]), 0.0,
        )

    def test_adapter_recovers_supported_sparse_3d_diagonal_candidate(self):
        diagonal = self.primitive([0, 0, 0.03], [0.10, 0.10, 0.03], votes=6, score=0.95)
        points = np.linspace(diagonal.start, diagonal.end, 8)
        features = self._features(points, diagonal.tangent)
        with patch("algorithms.rebar_geometric_v4.local_features", return_value=features), patch(
            "algorithms.rebar_geometric_v4._detect_fixture_surfaces", return_value=[]
        ), patch("algorithms.rebar_geometric_v4.line_primitives", side_effect=[[], [diagonal]]):
            result = self.algorithm._analyze_points(
                points, _Params(), self._off_table_plane()
            )
        self.assertEqual(len(result.data["instances"]), 1)
        self.assertEqual(result.data["diagnostics"]["postprocess"]["diagonalRecoveryAccepted"], 1)

    def test_adapter_deduplicates_recovery_against_existing_primitive(self):
        diagonal = self.primitive([0, 0, 0.03], [0.10, 0, 0.13], votes=8, score=0.95)
        points = np.linspace(diagonal.start, diagonal.end, 8)
        features = self._features(points, diagonal.tangent)
        with patch("algorithms.rebar_geometric_v4.local_features", return_value=features), patch(
            "algorithms.rebar_geometric_v4._detect_fixture_surfaces", return_value=[]
        ), patch("algorithms.rebar_geometric_v4.line_primitives", side_effect=[[diagonal], [diagonal]]):
            result = self.algorithm._analyze_points(points, _Params(), self._off_table_plane())
        self.assertEqual(len(result.data["instances"]), 1)
        self.assertEqual(result.data["diagnostics"]["postprocess"]["diagonalRecoveryRejectedDuplicate"], 1)

    def test_adapter_hook_override_joins_only_unique_supported_continuation(self):
        first = self.primitive([0, 0, 0.03], [0.10, 0, 0.03])
        second = self.primitive([0.125, 0.01, 0.03], [0.20, 0.06, 0.03])
        points = np.vstack((np.linspace(first.start, first.end, 8), np.linspace(second.start, second.end, 8)))
        features = self._features(points)
        with patch("algorithms.rebar_geometric_v4.local_features", return_value=features), patch(
            "algorithms.rebar_geometric_v4._detect_fixture_surfaces", return_value=[]
        ), patch("algorithms.rebar_geometric_v4.line_primitives", side_effect=[[first, second], []]):
            result = self.algorithm._analyze_points(points, _Params(), self._off_table_plane())
        self.assertEqual(len(result.data["instances"]), 1)

    def test_adapter_fixture_completion_suppresses_edge_primitive_not_adjacent_cylinder(self):
        surface = {"origin": [0.1, 0, 0.03], "normal": [0, 0, 1], "axes": [[1, 0, 0], [0, 1, 0]], "halfExtent": [0.11, 0.02], "distance": 0.006, "supportCount": 30, "coverage": 0.9}
        edge = self.primitive([0, 0.032, 0.03], [0.20, 0.032, 0.03])
        adjacent = self.primitive([0, 0.070, 0.03], [0.20, 0.070, 0.03])
        plane_support = np.array(
            [[x, y, .03] for x in np.linspace(0, .20, 4) for y in (.022, .027, .032)]
        )
        points = np.vstack((plane_support, np.linspace(adjacent.start, adjacent.end, 8)))
        features = self._features(points, linearity=0.1, planarity=0.9)
        with patch("algorithms.rebar_geometric_v4.local_features", return_value=features), patch(
            "algorithms.rebar_geometric_v4._detect_fixture_surfaces", return_value=[surface]
        ), patch("algorithms.rebar_geometric_v4.line_primitives", side_effect=[[edge, adjacent], []]):
            result = self.algorithm._analyze_points(points, _Params(), self._off_table_plane())
        self.assertEqual(len(result.data["instances"]), 1)
        self.assertEqual(result.data["diagnostics"]["postprocess"]["fixtureCompleted"], 1)

    def test_all_table_source_returns_an_empty_valid_analysis(self):
        xy = np.stack(
            np.meshgrid(np.linspace(-1, 1, 20), np.linspace(-1, 1, 20)), axis=-1
        ).reshape(-1, 2)
        sample = np.c_[xy, np.zeros(len(xy))]

        class Context:
            bim_prior = None

            def __init__(self, points):
                self.sample = points

            def iter_chunks(self):
                yield np.arange(len(self.sample), dtype=np.uint32), self.sample

        result = self.algorithm.analyze_source(
            Context(sample), self.algorithm.normalize_parameters({})
        )
        self.assertEqual(result.data["instances"], [])
        self.assertEqual(result.data["diagnostics"]["instanceCount"], 0)
        self.assertEqual(result.data["diagnostics"]["sourceCoveragePointCount"], 0)

    def test_dense_same_instance_pieces_do_not_hide_competing_membership(self):
        first = _entry(1, [[-0.001, 0, 0], [0.001, 0, 0]])
        first["observedSegments"] *= 25
        second = _entry(2, [[-0.01, 0.001, 0], [0.01, 0.001, 0]])
        result = self.algorithm.project_points(
            np.array([[0, 0.0005, 0]]), _analysis([first, second])
        )
        self.assertEqual(int(result.rebar_instance[0]), int(AMBIGUOUS_INSTANCE))
        self.assertEqual(int(result.rebar_flags[0]), FLAG_AMBIGUOUS)


class GeometricV4AcceptanceTests(unittest.TestCase):
    def test_thin_non_touching_bars_at_eight_millimetre_spacing_stay_separate(self):
        rng = np.random.default_rng(17)
        table = np.c_[
            rng.uniform(-1, 1, 6000),
            rng.uniform(-1, 1, 6000),
            rng.normal(0, 0.0007, 6000),
        ]
        bars = []
        for y in (-0.008, 0.0, 0.008):
            x = rng.uniform(-0.75, 0.75, 900)
            phi = rng.uniform(0, np.pi, 900)
            bars.append(
                np.c_[x, y + 0.0025 * np.cos(phi), 0.025 + 0.0025 * np.sin(phi)]
            )
        points = np.vstack([table, *bars])
        algorithm = GeometricV4Adapter()
        analysis = algorithm.analyze(points, algorithm.normalize_parameters({}))
        attrs = algorithm.project_points(points, analysis)
        winners = []
        for i in range(3):
            ids = attrs.rebar_instance[6000 + i * 900 : 6900 + i * 900]
            physical = ids[(ids > 0) & (ids < AMBIGUOUS_INSTANCE)]
            identifiers, counts = np.unique(physical, return_counts=True)
            winners.append(int(identifiers[np.argmax(counts)]))
            self.assertGreater(np.max(counts) / 900, 0.98)
        self.assertEqual(len(set(winners)), 3)
        self.assertEqual(len(analysis.data["instances"]), 3)

    def test_empty_source_sample_has_explicit_input_error(self):
        class Context:
            sample = np.empty((0, 3))
            bim_prior = None

        algorithm = GeometricV4Adapter()
        with self.assertRaisesRegex(ValueError, "at least three finite XYZ"):
            algorithm.analyze_source(Context(), algorithm.normalize_parameters({}))

    def test_independent_physical_scenes_across_noise_and_surface_coverage(self):
        for seed, top_arcs in (
            (20260905, True),
            (20260906, True),
            (20260907, True),
            (20260905, False),
        ):
            with self.subTest(seed=seed, top_arcs=top_arcs):
                report = run("geometric-v4", seed, top_arcs)
                self.assertGreater(report["semantic"]["precision"], 0.995)
                self.assertGreater(report["semantic"]["recall"], 0.98)
                self.assertEqual(report["semantic"]["fixtureLeakage"], 0)
                self.assertEqual(report["instances"]["iou50Recall"], 1)
                self.assertGreater(report["instances"]["iou50Precision"], 0.92)
                self.assertEqual(report["instances"]["merged"], 0)
                self.assertGreater(report["hookRecall"], 0.97)
                self.assertGreater(report["hookParentConsistency"], 0.97)
                if seed == 20260905 and top_arcs:
                    self.assertEqual(report["instances"]["predicted"], 13)
                    self.assertEqual(report["hookParentConsistency"], 1)

    def test_parallel_endpoints_stay_separate_and_overlap_is_not_inferred(self):
        def primitive(a, b):
            a, b = np.array(a, dtype=float), np.array(b, dtype=float)
            return LinePrimitive(a, b, (b - a) / np.linalg.norm(b - a), 0.004, 100, 1.0)

        options = dict(
            join_gap=0.09,
            observed_join_gap=0.04,
            maximum_turn_degrees=32,
            minimum_instance_length=0.08,
        )
        parallel = trace_primitive_graph(
            [
                primitive([0, 0, 0], [1, 0, 0]),
                primitive([1, 0.016, 0], [2, 0.016, 0]),
            ],
            **options,
        )
        self.assertEqual(len(parallel), 2)
        overlap = trace_primitive_graph(
            [
                primitive([0, 0, 0], [1, 0, 0]),
                primitive([0.95, 0, 0], [2, 0, 0]),
            ],
            **options,
        )
        self.assertEqual(len(overlap), 1)
        self.assertEqual(len(overlap[0].observed_segments), 2)
        self.assertEqual(len(overlap[0].inferred_segments), 0)


if __name__ == "__main__":
    unittest.main()
