import unittest
import numpy as np

from algorithms.rebar_shape_templates import build_unit_shape_templates


def inventory(points, units):
    return {'bars': [{'designBarId': 'bar', 'points': points}], 'units': units}


class RebarShapeTemplateTests(unittest.TestCase):
    def test_reversed_unit_orders_both_tails_through_their_straight_joins(self):
        points = [[0, 1, 0], [0, 0, 0], [3, 0, 0], [3, 0, 1]]
        unit = {'designBarId': 'bar', 'designUnitId': 'u', 'startM': [3, 0, 0], 'endM': [0, 0, 0]}
        template = build_unit_shape_templates(inventory(points, [unit]))['u']
        np.testing.assert_allclose(template['startTailM'][-1], [0, 0, 0], atol=1e-8)
        np.testing.assert_allclose(template['endTailM'][0], [3, 0, 0], atol=1e-8)
        self.assertAlmostEqual(template['shapeLengthM'], 5.)

    def test_hook_and_main_retain_the_full_known_path_length(self):
        points = [[0, 1, 0], [0, 0, 0], [3, 0, 0], [3, 0, 1]]
        unit = {'designBarId': 'bar', 'designUnitId': 'main', 'startM': [0, 0, 0], 'endM': [3, 0, 0]}
        template = build_unit_shape_templates(inventory(points, [unit]))['main']
        self.assertAlmostEqual(template['straightLengthM'], 3.)
        self.assertAlmostEqual(template['shapeLengthM'], 5.)
        self.assertEqual(template['localCenterlineM'][1], [0., 0., 0.])
        self.assertEqual(template['localCenterlineM'][-2], [3., 0., 0.])
        self.assertTrue(template['hasCurves'])

    def test_straight_bar_has_no_tails_or_curves(self):
        unit = {'designBarId': 'bar', 'designUnitId': 'line', 'startM': [0, 0, 0], 'endM': [2, 0, 0]}
        template = build_unit_shape_templates(inventory([[0, 0, 0], [2, 0, 0]], [unit]))['line']
        self.assertEqual(template['startTailM'], [])
        self.assertEqual(template['endTailM'], [])
        self.assertFalse(template['hasCurves'])
        self.assertAlmostEqual(template['shapeLengthM'], 2.)

    def test_two_web_units_split_the_connector_and_cover_parent_once(self):
        points = [[0, 0, 0], [1, 0, 0], [1, .5, 0], [1, 1, 0], [1, 1, 1], [1, 1, 2]]
        units = [
            {'designBarId': 'bar', 'designUnitId': 'a', 'startM': [0, 0, 0], 'endM': [1, 0, 0]},
            {'designBarId': 'bar', 'designUnitId': 'b', 'startM': [1, 1, 1], 'endM': [1, 1, 2]},
        ]
        values = build_unit_shape_templates(inventory(points, units))
        self.assertAlmostEqual(values['a']['shapeLengthM'] + values['b']['shapeLengthM'], 4.)
        self.assertAlmostEqual(values['a']['endTailM'][0][0], 1., places=7) # join is preserved
        self.assertAlmostEqual(values['a']['shapeLengthM'], values['b']['shapeLengthM'])

    def test_canonical_template_is_rigid_transform_invariant(self):
        points = np.array([[0, 1, 0], [0, 0, 0], [2, 0, 0], [2, 0, 1]], float)
        unit = {'designBarId': 'bar', 'designUnitId': 'u', 'startM': [0, 0, 0], 'endM': [2, 0, 0]}
        baseline = build_unit_shape_templates(inventory(points.tolist(), [unit]))['u']
        rotation = np.array([[0, -1, 0], [1, 0, 0], [0, 0, 1]], float)
        shift = np.array([4, -3, 7.])
        moved = (points @ rotation.T + shift).tolist()
        transformed = {**unit, 'startM': (np.array(unit['startM']) @ rotation.T + shift).tolist(),
                       'endM': (np.array(unit['endM']) @ rotation.T + shift).tolist()}
        candidate = build_unit_shape_templates(inventory(moved, [transformed]))['u']
        for key in ('localCenterlineM', 'startTailM', 'endTailM'):
            np.testing.assert_allclose(candidate[key], baseline[key], atol=1e-8)


if __name__ == '__main__':
    unittest.main()
