from __future__ import annotations

import unittest

import numpy as np

from algorithms.rebar_base import RebarAnalysis
from algorithms.rebar_geometric_v4 import (
    AMBIGUOUS_INSTANCE,
    FLAG_AMBIGUOUS,
    FLAG_CROSSING,
    GeometricV4Adapter,
    SCENE_FIXTURE,
    SCENE_REBAR,
)
from algorithms.rebar_v4_geometry import LinePrimitive, trace_primitive_graph
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
