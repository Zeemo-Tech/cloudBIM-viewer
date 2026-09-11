import unittest
import numpy as np

from algorithms.multiview_floating_noise import multiview_noise_mask, observed_cylinder_support, observed_cylinder_continuation


def rod(start, end, radius=.004, step=.0015):
    start, end = np.array(start, float), np.array(end, float)
    axis = end-start
    length = np.linalg.norm(axis)
    axis /= length
    a = np.cross(axis, np.eye(3)[np.argmin(np.abs(axis))]); a /= np.linalg.norm(a)
    b = np.cross(axis, a)
    t, theta = np.meshgrid(np.linspace(0, length, max(3, int(length/step)+1)),
                          np.linspace(0, 2*np.pi, 24, endpoint=False))
    normals = np.cos(theta.ravel())[:, None]*a+np.sin(theta.ravel())[:, None]*b
    return start+t.ravel()[:, None]*axis+radius*normals, normals


def ball(center, radius=.002, count=200):
    k = np.arange(count)+.5
    z = 1-2*k/count
    theta = np.pi*(1+np.sqrt(5))*k
    nn = np.column_stack((np.sqrt(1-z*z)*np.cos(theta), np.sqrt(1-z*z)*np.sin(theta), z))
    return np.array(center)+radius*nn, nn


class MultiviewNoiseTests(unittest.TestCase):
    def run_scene(self, extra, extra_normals, score=1., rotate=0., review=None):
        points, normals = rod([0, 0, 0], [.18, 0, 0])
        size = len(points)
        points = np.vstack((points, extra)); normals = np.vstack((normals, extra_normals))
        angle = np.deg2rad(rotate)
        r = np.array([[np.cos(angle), -np.sin(angle), 0], [np.sin(angle), np.cos(angle), 0], [0, 0, 1]])
        points = points@r.T; normals = normals@r.T
        scores = np.full(len(points), score)
        removed, report = multiview_noise_mask(points, normals=normals, steel_scores=scores,
            review_mask=np.ones(len(points), bool) if review is None else review)
        self.assertFalse(removed[:size].any(), 'real rod must survive')
        return removed[size:], report

    def test_dense_floating_ball_above_rod_overrules_high_score(self):
        p, n = ball([.08, 0, .025])
        for angle in (0., 23., 71.):
            removed, report = self.run_scene(p, n, rotate=angle)
            self.assertTrue(removed.all())
            self.assertEqual(report['highScoreRemovedPointCount'], len(p))

    def test_same_height_ball_hidden_by_some_projections(self):
        p, n = ball([.08, .025, 0])
        for angle in (0., 23., 71.):
            removed, _ = self.run_scene(p, n, rotate=angle)
            self.assertTrue(removed.all())

    def test_floating_plate_is_not_a_rod_because_it_is_dense_or_long(self):
        x, y = np.meshgrid(np.arange(.03, .10, .0015), np.arange(.025, .070, .0015))
        p = np.column_stack((x.ravel(), y.ravel(), np.full(x.size, .035)))
        removed, _ = self.run_scene(p, np.tile([0, 0, 1.], (len(p), 1)))
        self.assertTrue(removed.all())

    def test_long_thin_and_large_flat_islands_have_no_size_immunity(self):
        for length, width in ((.15, .010), (.40, .06)):
            x, y = np.meshgrid(np.arange(0, length, .002), np.arange(.06, .06+width, .002))
            p = np.column_stack((x.ravel(), y.ravel(), np.full(x.size, .035)))
            removed, _ = self.run_scene(p, np.tile([0, 0, 1.], (len(p), 1)))
            self.assertTrue(removed.all())

    def test_unobserved_gap_pair_cannot_support_itself(self):
        p = np.array([[.30, .004, 0], [.301, .004, 0]])
        removed, _ = self.run_scene(p, np.tile([0, 1, 0.], (2, 1)))
        self.assertTrue(removed.all())

    def test_fitted_gap_high_score_pair_does_not_become_observed_support(self):
        left, ln = rod([0, 0, 0], [.05, 0, 0])
        right, rn = rod([.15, 0, 0], [.20, 0, 0])
        points = np.vstack((left, right, [[.10, .004, 0], [.101, .004, 0]]))
        normals = np.vstack((ln, rn, [[0, 1, 0], [0, 1, 0]]))
        observed = observed_cylinder_support(points, np.ones(len(points), int), np.ones(len(points)),
            [dict(id=1, startM=[0, 0, 0], endM=[.20, 0, 0])])
        self.assertTrue(observed[:-2].all())
        self.assertFalse(observed[-2:].any())
        for workers in (1, 4):
            removed, report = multiview_noise_mask(points, normals=normals, steel_scores=np.ones(len(points)),
                review_mask=np.ones(len(points), bool), observed_support_mask=observed, workers=workers)
            self.assertFalse(removed[:-2].any())
            self.assertTrue(removed[-2:].all())
            self.assertEqual(report['highScoreRemovedPointCount'], 2)

    def test_short_round_fragment_and_occlusion_gap_survive(self):
        for start, end in [([.19, 0, 0], [.197, 0, 0]), ([.3, .1, .02], [.308, .1, .02])]:
            p, n = rod(start, end)
            removed, _ = self.run_scene(p, n, score=.65)
            self.assertFalse(removed.any())

    def test_bent_hook_survives(self):
        first, nn = rod([.20, 0, 0], [.23, 0, 0])
        second, mm = rod([.23, 0, 0], [.23, 0, .025])
        removed, _ = self.run_scene(np.vstack((first, second)), np.vstack((nn, mm)))
        self.assertFalse(removed.any())

    def test_exterior_rows_supply_context_but_cannot_be_removed(self):
        p, n = ball([.08, 0, .025])
        size = len(rod([0, 0, 0], [.18, 0, 0])[0])
        review = np.r_[np.ones(size, bool), np.zeros(len(p), bool)]
        removed, _ = self.run_scene(p, n, review=review)
        self.assertFalse(removed.any())

    def test_no_evidence_is_not_permission_to_delete_everything(self):
        p, n = ball([0, 0, 0])
        removed, report = multiview_noise_mask(p, normals=n, review_mask=np.ones(len(p), bool))
        self.assertFalse(removed.any())
        self.assertIn('skippedReason', report)


def rounded_fixture_lip(span=.010):
    """A misclassified strip on a rounded plate edge, with measured fixture context."""
    x, theta = np.meshgrid(np.arange(0, span+.0001, .0005), np.linspace(0, np.pi/2, 13))
    nn = np.column_stack((np.zeros(x.size), np.sin(theta.ravel()), np.cos(theta.ravel())))
    points = np.column_stack((x.ravel(), np.zeros(x.size), np.full(x.size, .030)))+.004*nn
    xx = np.r_[np.arange(-.03, -.0004, .0005), np.arange(span+.0005, span+.031, .0005)]
    x, theta = np.meshgrid(xx, np.linspace(0, np.pi/2, 13))
    fn = np.column_stack((np.zeros(x.size), np.sin(theta.ravel()), np.cos(theta.ravel())))
    fixture = np.column_stack((x.ravel(), np.zeros(x.size), np.full(x.size, .030)))+.004*fn
    x, y = np.meshgrid(np.arange(-.03, span+.031, .001), np.arange(-.03, 0, .001))
    top = np.column_stack((x.ravel(), y.ravel(), np.full(x.size, .034)))
    x, z = np.meshgrid(np.arange(-.03, span+.031, .001), np.arange(0, .030, .001))
    side = np.column_stack((x.ravel(), np.full(x.size, .004), z.ravel()))
    return points, nn, np.vstack((fixture, top, side)), np.vstack((fn,
        np.tile([0, 0, 1.], (len(top), 1)), np.tile([0, 1., 0], (len(side), 1))))


class FixtureDensityTests(unittest.TestCase):
    def test_density_cannot_trim_a_measured_rod_where_it_enters_a_fixture(self):
        # Deliberately ambiguous curved fixture context: the end strip has
        # strong fixture votes but continues the frozen cylinder surface.
        points, normals, fixture, fn = rounded_fixture_lip(.0045)
        anchor, an = rod([-.03, 0, .030], [-.005, 0, .030])
        xyz = np.vstack((anchor, points)); nn = np.vstack((an, normals))
        ids = np.r_[np.ones(len(anchor), int), np.zeros(len(points), int)]
        observed = ids > 0
        segments = [dict(id=1, startM=[-.03, 0, .030], endM=[-.005, 0, .030], radiusM=.004)]
        continuation = observed_cylinder_continuation(xyz, nn, ids, segments, observed)
        self.assertTrue(continuation[len(anchor):].all())
        for score in (.25, 1.):
            without_continuation, _ = multiview_noise_mask(xyz, normals=nn,
                review_mask=np.ones(len(xyz), bool), observed_support_mask=observed,
                steel_scores=np.full(len(xyz), score), fixture_points=fixture, fixture_normals=fn)
            self.assertTrue(without_continuation[len(anchor):].all(), 'reproduce density trimming a real rod end')
            removed, report = multiview_noise_mask(xyz, normals=nn, review_mask=np.ones(len(xyz), bool),
                observed_support_mask=observed, observed_continuation_mask=continuation,
                steel_scores=np.full(len(xyz), score), fixture_points=fixture, fixture_normals=fn)
            self.assertFalse(removed.any())

    def test_fitted_empty_gap_and_off_surface_echo_are_not_continuation(self):
        anchor, an = rod([0, 0, 0], [.04, 0, 0])
        points = np.vstack((anchor, [[.1, .004, 0], [.046, .008, 0]]))
        nn = np.vstack((an, [[0, 1, 0], [0, 1, 0]]))
        ids = np.r_[np.ones(len(anchor), int), [0, 0]]
        observed = ids > 0
        segments = [dict(id=1, startM=[0, 0, 0], endM=[.2, 0, 0], radiusM=.004)]
        continuation = observed_cylinder_continuation(points, nn, ids, segments, observed)
        self.assertFalse(continuation[-2:].any())

    def test_rounded_fixture_lip_loses_shape_immunity_but_score_raises_threshold(self):
        points, normals, fixture, fn = rounded_fixture_lip()
        baseline, _ = multiview_noise_mask(points, normals=normals, review_mask=np.ones(len(points), bool))
        self.assertFalse(baseline.any(), 'the original round-shape protection reproduces the leak')
        for score, should_remove in ((.25, True), (.65, True), (1., False)):
            removed, report = multiview_noise_mask(points, normals=normals,
                review_mask=np.ones(len(points), bool), steel_scores=np.full(len(points), score),
                fixture_points=fixture, fixture_normals=fn)
            self.assertEqual(bool(removed.all()), should_remove)
            if not should_remove:
                self.assertFalse(removed.any())
                self.assertEqual(report['blockedRemovalPointCount'], len(points))

    def test_sufficient_fixture_evidence_can_override_high_scores(self):
        points, normals, fixture, fn = rounded_fixture_lip(.0045)
        for workers, sign in ((1, 1), (4, -1)):
            removed, report = multiview_noise_mask(points, normals=sign*normals,
                review_mask=np.ones(len(points), bool), steel_scores=np.ones(len(points)),
                fixture_points=fixture, fixture_normals=sign*fn, workers=workers)
            self.assertTrue(removed.all())
            self.assertEqual(report['densityReview']['highScoreRemovedPointCount'], len(points))

    def test_low_score_real_rod_near_dense_fixture_survives(self):
        _, _, fixture, fn = rounded_fixture_lip()
        points, normals = rod([-.02, -.008, .041], [.03, -.008, .041], radius=.004)
        for score in (.25, 1.):
            removed, _ = multiview_noise_mask(points, normals=normals,
                review_mask=np.ones(len(points), bool), steel_scores=np.full(len(points), score),
                fixture_points=fixture, fixture_normals=fn)
            self.assertFalse(removed.any(), 'density and score alone cannot delete an exposed rod')

    def test_observed_continuation_is_preserved_and_review_mask_is_respected(self):
        points, normals, fixture, fn = rounded_fixture_lip(.0045)
        for observed, review in ((np.ones(len(points), bool), np.ones(len(points), bool)),
                                 (None, np.zeros(len(points), bool))):
            removed, _ = multiview_noise_mask(points, normals=normals, review_mask=review,
                observed_support_mask=observed, fixture_points=fixture, fixture_normals=fn)
            self.assertFalse(removed.any())
    def test_fixture_without_valid_normals_cannot_overrule_a_round_fragment(self):
        points, normals, fixture, fn = rounded_fixture_lip()
        for missing in (None, np.zeros_like(fn), np.full_like(fn, np.nan)):
            removed, _ = multiview_noise_mask(points, normals=normals,
                review_mask=np.ones(len(points), bool), fixture_points=fixture, fixture_normals=missing)
            self.assertFalse(removed.any())


if __name__ == '__main__':
    unittest.main()
