"""Broad-phase pruning must preserve the original precise point ownership."""
from copy import deepcopy
import unittest
from unittest.mock import patch

import numpy as np

from algorithms.rebar_v5.contracts import Params
from algorithms.rebar_v5.scene import fixture_candidates, fixture_mask
from algorithms.rebar_v5.pipeline import classify
from algorithms.rebar_v5.bolts import bolt_mask
from test_rebar_v5_classification import _analysis, _face, _instance
from test_rebar_v5_bolt_performance import model as bolt_model


class SpatialPruningTests(unittest.TestCase):
    def test_classification_prunes_remote_bolts_without_changing_attributes(self):
        points = np.random.default_rng(42).uniform(-.04, .04, (2000, 3))
        points = np.vstack((points, [.031, .001, .012]))
        models = [bolt_model(origin=(index*2., 0., 0.)) for index in range(30)]
        analysis = _analysis([])
        analysis.data['algorithmDetails']['fixture']['bolts'] = models
        for review in (False, True):
            analysis.data['algorithmDetails']['parameters']['ownership_review_enabled'] = review
            with patch('algorithms.rebar_v5.pipeline.bolt_mask', wraps=bolt_mask) as masked:
                actual, candidates = classify(points, analysis)
                self.assertEqual(masked.call_count, 1)
            with patch('algorithms.rebar_v5.pipeline.bolt_candidates', return_value=models):
                expected, expected_candidates = classify(points, analysis)
            for name, values in vars(actual).items():
                np.testing.assert_array_equal(values, getattr(expected, name), err_msg=name)
            for name, values in candidates.items():
                np.testing.assert_array_equal(values, expected_candidates[name], err_msg=name)

    def test_rotated_faces_and_boundary_points_match_exhaustive_masks(self):
        p = Params()
        angle = .63
        rotation = np.array([[np.cos(angle), -np.sin(angle), 0],
                             [np.sin(angle), np.cos(angle), 0], [0, 0, 1]])
        near = _face()
        near['axes'] = (np.asarray(near['axes']) @ rotation.T).tolist()
        far = deepcopy(near)
        far['origin'] = [20, 30, 0]
        rng = np.random.default_rng(63)
        cloud = rng.uniform(-.4, .4, (3000, 3))
        cloud[:, 2] *= .01
        # Include the exact face thickness limit and a nonfinite query.
        cloud = np.vstack((cloud, [-.1, 0, p.fixture_surface_distance], [np.nan, 0, 0]))
        expected = fixture_mask(cloud, [near], p) | fixture_mask(cloud, [far], p)
        np.testing.assert_array_equal(fixture_mask(cloud, [near, far], p), expected)
        self.assertEqual(fixture_candidates(cloud, [near, far], p), [near])
        self.assertTrue(expected[-2])
        self.assertFalse(expected[-1])
        self.assertEqual(fixture_candidates(np.empty((0, 3)), [near], p), [])
        self.assertEqual(fixture_candidates(np.full((2, 3), np.nan), [near], p), [])

    def test_pruned_and_exhaustive_classification_are_identical(self):
        rng = np.random.default_rng(57)
        points = rng.uniform([-.25, -.12, -.01], [.25, .12, .01], (4000, 3))
        faces = []
        for ordinal in range(60):
            face = _face()
            face['origin'][0] += ordinal*2
            face['kind'] = ['plate', 'square-tube-face', 'fixture-unknown'][ordinal % 3]
            faces.append(face)
        bar = _instance(1, [-.2, 0, 0], [.2, 0, 0], .006)
        bar['role'] = 'web'
        analysis = _analysis([bar], faces)
        for review in (False, True):
            analysis.data['algorithmDetails']['parameters']['ownership_review_enabled'] = review
            with patch('algorithms.rebar_v5.pipeline.fixture_mask', wraps=fixture_mask) as masked:
                actual, candidates = classify(points, analysis)
                self.assertEqual(masked.call_count, 1)
            with patch('algorithms.rebar_v5.pipeline.fixture_candidates', side_effect=lambda points, faces, p: faces):
                expected, expected_candidates = classify(points, analysis)
            for name, values in vars(actual).items():
                np.testing.assert_array_equal(values, getattr(expected, name), err_msg=name)
            for name, values in candidates.items():
                np.testing.assert_array_equal(values, expected_candidates[name], err_msg=name)


if __name__ == '__main__':
    unittest.main()
