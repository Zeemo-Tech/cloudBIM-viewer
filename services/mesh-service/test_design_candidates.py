import unittest
from types import SimpleNamespace
import numpy as np

from algorithms.design_candidates import generate_candidates
from test_internal_rebar import cylinder


def context(points, normals, *, hard=None):
    n = len(points)
    return SimpleNamespace(positions=points, normals=normals, normal_valid=np.ones(n, np.uint8),
        shared_table_mask=np.zeros(n, np.uint8), shared_floating_noise=np.zeros(n, np.uint8) if hard is None else hard,
        refined_class=np.zeros(n, np.uint8), shared_layer=np.ones(n, np.uint8))


def unit(kind='straight', start=(0, 0, 0), end=(.12, 0, 0), diameter=.012):
    return {'units': [dict(designBarId='bar', designUnitId='bar/unit0', kind=kind, startM=start, endM=end,
                           diameterM=diameter, coverage='complete')]}


class DesignCandidateTests(unittest.TestCase):
    def test_fixed_radius_and_observed_span_for_upper_half_cylinder(self):
        points, normals, _ = cylinder((0, 0, .02), (.12, 0, .02), radius=.003, along=30, around=16)
        keep = normals[:, 2] > 0
        result, report = generate_candidates(context(points[keep], normals[keep]), unit(start=(0,0,.02), end=(.12,0,.02),diameter=.006))
        self.assertTrue(result); candidate = result[0]
        self.assertEqual(candidate.model['radius'], .003)
        self.assertGreater(candidate.metrics['arc_degrees'], 20)
        self.assertLess(candidate.metrics['cylinder_error_m'], .001)
        self.assertLessEqual(candidate.model['high'] - candidate.model['low'], .121)
        self.assertEqual(report['candidateCount'], 1)

    def test_plane_box_metrics_compete_with_cylinder(self):
        x, y = np.meshgrid(np.linspace(0, .12, 20), np.linspace(-.01, .01, 10))
        points = np.c_[x.ravel(), y.ravel(), np.zeros(x.size)]
        result, _ = generate_candidates(context(points, np.tile([0., 0., 1.], (len(points), 1))), unit())
        self.assertTrue(result)
        self.assertLess(result[0].metrics['plane_error_m'], result[0].metrics['cylinder_error_m'])

    def test_square_tube_faces_are_not_a_round_rod(self):
        from algorithms.design_evidence import evaluate_evidence
        x,t=np.meshgrid(np.linspace(0,.12,60),np.linspace(-.003,.003,15));cloud=[];normals=[]
        for sign in [-1,1]:
            cloud += [np.c_[x.ravel(),t.ravel(),np.full(x.size,sign*.003)],np.c_[x.ravel(),np.full(x.size,sign*.003),t.ravel()]]
            normals += [np.tile([0,0,sign],(x.size,1)),np.tile([0,sign,0],(x.size,1))]
        candidates,_=generate_candidates(context(np.vstack(cloud),np.vstack(normals)),unit(diameter=.006))
        self.assertTrue(candidates)
        self.assertTrue(all(not evaluate_evidence(c.metrics).accepted for c in candidates))

    def test_hard_mask_rows_never_enter_support(self):
        points, normals, _ = cylinder((0,0,0), (.12,0,0), radius=.006)
        hard = np.ones(len(points), np.uint8)
        result, report = generate_candidates(context(points, normals, hard=hard), unit())
        self.assertEqual(result, [])
        self.assertEqual(report['eligiblePointCount'], 0)

    def test_partial_hard_neighborhood_does_not_veto_eligible_support(self):
        points, normals, _ = cylinder((0,0,0), (.12,0,0), radius=.006)
        hard = np.zeros(len(points), np.uint8); hard[::5] = 1
        result, _ = generate_candidates(context(points, normals, hard=hard), unit())
        self.assertTrue(result)
        self.assertEqual(result[0].metrics['hard_excluded'], 0)
        self.assertGreater(result[0].metrics['excluded_nearby_count'], 0)

    def test_short_pool_permits_local_xy_translation(self):
        points, normals, _ = cylinder((0,.085,0), (.12,.085,0), radius=.006)
        result, _ = generate_candidates(context(points, normals), unit('short'))
        self.assertTrue(result)
        self.assertGreater(result[0].metrics['design_position_offset_m'], .04)
        from algorithms.design_evidence import evaluate_evidence
        self.assertTrue(evaluate_evidence(result[0].metrics).accepted)

    def test_all_units_are_processed_beyond_per_unit_candidate_cap(self):
        clouds = [cylinder((0, i*.04, 0), (.12, i*.04, 0), radius=.006) for i in range(9)]
        points = np.vstack([item[0] for item in clouds]); normals = np.vstack([item[1] for item in clouds])
        inv = {'units': [dict(designBarId=str(i), designUnitId=f'u{i}', kind='straight',
                              startM=(0, i*.04, 0), endM=(.12, i*.04, 0), diameterM=.012)
                         for i in range(9)]}
        result, _ = generate_candidates(context(points, normals), inv)
        self.assertEqual({item.unit_id for item in result}, {f'u{i}' for i in range(9)})

    def test_coordinate_sampling_is_invariant_to_source_order(self):
        points, normals, _ = cylinder((0,0,0), (.12,0,0), radius=.006)
        first, _ = generate_candidates(context(points, normals), unit())
        order = np.random.default_rng(4).permutation(len(points))
        second, _ = generate_candidates(context(points[order], normals[order]), unit())
        np.testing.assert_allclose(first[0].model['center'], second[0].model['center'], atol=1.e-6)
        np.testing.assert_allclose(first[0].metrics['cylinder_error_m'], second[0].metrics['cylinder_error_m'], atol=1.e-9)

    def test_absent_design_observations_never_make_a_candidate(self):
        points = np.array([[2., 2., 2.], [2.01, 2., 2.], [2., 2.01, 2.]])
        result, report = generate_candidates(context(points, np.zeros_like(points)), unit())
        self.assertEqual(result, [])
        self.assertTrue(all(a['accepted'] is False for a in report['attempts']))


if __name__ == '__main__':
    unittest.main()
