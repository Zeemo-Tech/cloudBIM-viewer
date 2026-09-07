import unittest
from unittest.mock import patch
import numpy as np

from algorithms.rebar_v5.contracts import Params, REBAR, TABLE, FIXTURE
from algorithms.rebar_v5 import GeometricV5Adapter
from algorithms.rebar_v5.features import multiscale
from algorithms.rebar_v5.scene import _accepted_fixture_faces, detect_fixtures, detect_table, fixture_mask, refine_fixture_faces, table_mask
from rebar_validation import make_truth_scene, _with_density


def plane(x, y, z=0.0):
    xx, yy = np.meshgrid(x, y)
    return np.column_stack((xx.ravel(), yy.ravel(), np.full(xx.size, z)))


def planar_features(points, normal=(0, 0, 1)):
    return {"surface_normal": np.tile(normal, (len(points), 1)), "surface_planarity": np.ones(len(points)), "surface_valid": np.ones(len(points), dtype=np.uint8)}


class SceneTests(unittest.TestCase):
    def setUp(self):
        self.p = Params.from_value({"table_grid_size": .05, "table_min_area": .10, "fixture_grid_cell": .01, "fixture_min_width": .02, "fixture_min_length": .03, "fixture_min_points": 10})

    def test_lowest_qualifying_plane_beats_larger_upper_plane(self):
        lower = plane(np.arange(-.25, .26, .05), np.arange(-.25, .26, .05), 0)
        upper = plane(np.arange(-.45, .46, .05), np.arange(-.45, .46, .05), .15)
        points = np.vstack((lower, upper))
        model = detect_table(points, planar_features(points), self.p)
        self.assertIsNotNone(model)
        self.assertLess(abs(model["origin"][2]), .01)

    def test_table_mask_preserves_observed_hole(self):
        base = plane(np.arange(-.3, .31, .05), np.arange(-.3, .31, .05))
        points = base[np.linalg.norm(base[:, :2], axis=1) > .12]
        model = detect_table(points, planar_features(points), self.p)
        self.assertIsNotNone(model)
        masked = table_mask(np.array([[.2, .2, 0], [0, 0, 0]]), model, self.p)
        self.assertEqual(masked.tolist(), [True, False])

    def test_grid_of_rods_is_not_a_table(self):
        # Horizontal feature normals alone are insufficient when observed area
        # is below the true occupied-cell table threshold.
        rods = np.vstack([np.column_stack((np.linspace(-.5, .5, 30), np.full(30, y), np.zeros(30))) for y in (-.12, 0, .12)])
        self.assertIsNone(detect_table(rods, planar_features(rods), self.p))

    def test_fixture_face_is_selected_but_cylinder_is_not(self):
        face = plane(np.arange(-.10, .11, .01), np.arange(-.06, .07, .01), .08)
        fixtures = detect_fixtures(face, planar_features(face), self.p)
        self.assertTrue(fixtures)
        self.assertTrue(fixture_mask(np.array([[0, 0, .08], [0, 0, .10]]), fixtures, self.p)[0])
        theta = np.linspace(0, 2*np.pi, 120, endpoint=False)
        cylinder = np.column_stack((.02*np.cos(theta), .02*np.sin(theta), np.linspace(0, .12, len(theta))))
        cylinder_features = planar_features(cylinder)
        cylinder_features["surface_planarity"][:] = .1
        self.assertEqual(detect_fixtures(cylinder, cylinder_features, self.p), [])

    def test_fixture_mask_uses_the_returned_face_coordinate_frame(self):
        face = plane(np.arange(1.0, 1.15, .01), np.arange(2.0, 2.10, .01), .08)
        fixtures = detect_fixtures(face, planar_features(face), self.p)
        self.assertTrue(fixtures)
        self.assertTrue(fixture_mask(np.array([[1.07, 2.05, .08]]), fixtures, self.p)[0])

    def test_far_perpendicular_faces_are_not_a_single_square_tube(self):
        first = plane(np.arange(-.1, .11, .01), np.arange(-.05, .06, .01), .08)
        second = np.column_stack((np.full(len(first), 3.0), first[:, 0], first[:, 1]))
        points = np.vstack((first, second))
        features = planar_features(points)
        features["surface_normal"][len(first):] = (1, 0, 0)
        fixtures = detect_fixtures(points, features, self.p)
        self.assertTrue(fixtures)
        self.assertTrue(all(item["kind"] == "plate" for item in fixtures))

    def test_duplicate_suppression_preserves_close_parallel_distinct_faces(self):
        def face(z):
            return {"origin": [0, 0, z], "normal": [0, 0, 1], "axes": [[1, 0, 0], [0, 1, 0]],
                    "halfExtent": [.06, .02], "distance": self.p.fixture_surface_distance,
                    "supportCount": 40, "coverage": 1., "confidence": 1., "kind": "plate",
                    "occupiedCells": [[0, 0]], "gridSize": self.p.fixture_grid_cell}
        support = np.vstack([plane(np.arange(-.05,.06,.005),np.arange(-.015,.016,.005),z) for z in (0,.02)])
        faces = _accepted_fixture_faces([face(0), face(.02)], support, self.p)
        self.assertEqual(len(faces), 2)

    def test_raw_refinement_fits_each_exact_canonical_normal_once(self):
        proposals = [{"normal": [0, 0, 1]}, {"normal": [0, 0, -1]}, {"normal": [1, 0, 0]}]
        with patch("algorithms.rebar_v5.scene._fixture_face", return_value=[]) as fitted:
            refine_fixture_faces(np.empty((0, 3)), proposals, self.p)
        self.assertEqual(fitted.call_count, 2)

    def test_separated_small_plates_remain_separate_patches(self):
        first = plane(np.arange(-.08, .09, .01), np.arange(-.04, .05, .01), .08)
        second = first + np.array([.5, 0, 0])
        fixtures = detect_fixtures(np.vstack((first, second)), planar_features(np.vstack((first, second))), self.p)
        self.assertGreaterEqual(len(fixtures), 2)
        self.assertGreater(np.linalg.norm(np.asarray(fixtures[0]["origin"])-np.asarray(fixtures[-1]["origin"])), .3)

    def test_noisy_tilted_table_is_fitted_by_bounded_ransac(self):
        base = plane(np.arange(-.3, .31, .04), np.arange(-.3, .31, .04))
        angle = np.deg2rad(7)
        rotation = np.array([[1, 0, 0], [0, np.cos(angle), -np.sin(angle)], [0, np.sin(angle), np.cos(angle)]])
        points = base @ rotation.T
        points[:, 2] += np.random.default_rng(4).normal(0, .0007, len(points))
        normal = (np.array([0, 0, 1.]) @ rotation.T)
        model = detect_table(points, planar_features(points, normal), self.p)
        self.assertIsNotNone(model)
        self.assertGreater(model["normal"][2], .98)

    def test_physical_truth_table_and_fixture_masks_do_not_swallow_steel(self):
        truth = make_truth_scene()
        p = Params.from_value()
        features = multiscale(truth.points, truth.points, p)
        model = detect_table(truth.points, features, p)
        self.assertIsNotNone(model)
        table = table_mask(truth.points, model, p)
        fixtures = fixture_mask(truth.points, detect_fixtures(truth.points, features, p, model), p)
        steel = truth.scene == REBAR
        self.assertGreaterEqual(table[truth.scene == TABLE].mean(), .99)
        self.assertEqual(int(table[steel].sum()), 0)
        self.assertEqual(int(fixtures[steel].sum()), 0)
        self.assertGreater(int(fixtures[truth.scene == FIXTURE].sum()), 0)

    def test_half_density_square_tube_companions_mask_only_observed_faces(self):
        truth = _with_density(make_truth_scene(seed=20260905, top_arcs=True), .5, 20260905)
        p = Params.from_value()
        features = multiscale(truth.points, truth.points, p)
        table = detect_table(truth.points, features, p)
        fixtures = detect_fixtures(truth.points, features, p, table)
        masked = fixture_mask(truth.points, fixtures, p)
        square = truth.case_labels == "fixture_square_tube"
        self.assertGreaterEqual(masked[square].mean(), .99)
        self.assertEqual(int(masked[truth.scene == REBAR].sum()), 0)

    def test_pipeline_refines_half_density_fixture_faces_from_raw_support(self):
        truth = _with_density(make_truth_scene(seed=20260905, top_arcs=True), .5, 20260905)
        adapter = GeometricV5Adapter()
        analysis = adapter.analyze(truth.points, adapter.normalize_parameters({}))
        try:
            p = Params.from_value()
            fixtures = analysis.data["algorithmDetails"]["fixture"]["surfaces"]
            masked = fixture_mask(truth.points, fixtures, p)
            square = truth.case_labels == "fixture_square_tube"
            attrs = adapter.project_points(truth.points, analysis)
            self.assertGreaterEqual(masked[square].mean(), .98)
            self.assertEqual(int(masked[truth.scene == REBAR].sum()), 0)
            self.assertEqual(int(np.count_nonzero(square & (attrs.scene_class == REBAR))), 0)
            self.assertLessEqual(int(np.count_nonzero(square & (attrs.scene_class == 0))), 12)
        finally:
            adapter.close(analysis)


if __name__ == "__main__":
    unittest.main()
