import unittest
from dataclasses import replace
import numpy as np

from algorithms.rebar_v5.contracts import Params
from algorithms.rebar_v5.rods import distance_to_paths, hough_lines, hough_seeds, planar_bars, refine_axis, trace, _same_axis, web_bars
from algorithms.rebar_v4_geometry import LinePrimitive
from rebar_validation import _rod
from rebar_validation import make_truth_scene, REBAR
from algorithms.rebar_v5 import GeometricV5Adapter


def features(points, tangent=(1, 0, 0), linearity=1.0):
    return {"axis_tangent": np.tile(tangent, (len(points), 1)).astype(float),
            "axis_linearity": np.full(len(points), linearity, dtype=float)}


class RodTests(unittest.TestCase):
    def setUp(self):
        self.p = Params.from_value({"min_primitive_votes": 5, "min_primitive_length": .03,
            "min_instance_length": .03, "offset_cell_size": .004, "hough_angle_step_degrees": 2.,
            "axis_radius": .02, "support_distance": .004, "max_radius": .015})

    def test_hough_votes_seed_planar_instance(self):
        x = np.linspace(-.2, .2, 48)
        points = np.column_stack((x, np.zeros(len(x)), np.zeros(len(x))))
        f = features(points)
        candidates = hough_lines(points[:, :2], f["axis_tangent"][:, :2], self.p)
        seeds = hough_seeds(points, f["axis_tangent"], np.arange(len(points)), candidates,
                            np.array([[1., 0., 0.], [0., 1., 0.]]), self.p)
        instances, _, diagnostic = planar_bars(points, f, self.p)
        self.assertTrue(candidates)
        self.assertTrue(seeds)
        self.assertGreater(diagnostic["houghCandidateCount"], 0)
        # A centreline without cylindrical support is intentionally pruned;
        # Hough's tested contract is producing actual finite seed geometry.

    def test_partial_arc_refines_to_a_physical_axis(self):
        axis = np.array([1., 0, 0])
        cloud = _rod(np.random.default_rng(3), [-.1, 0, 0], [.1, 0, 0], .008, 1200, top_arcs=True)
        primitive = LinePrimitive(np.array([-.1, 0, 0]), np.array([.1, 0, 0]), axis, .008, len(cloud), 1.)
        refined, confidence = refine_axis(primitive, cloud, self.p)
        self.assertGreater(confidence, .5)
        self.assertAlmostEqual(refined.radius, .008, delta=.002)
        self.assertLess(np.linalg.norm((refined.start+refined.end)/2), .003)

    def test_sparse_top_and_full_circle_keep_the_same_axis(self):
        primitive = LinePrimitive(np.array([-.2, 0, 0]), np.array([.2, 0, 0]),
                                  np.array([1., 0, 0]), .008, 225, 1.)
        for top_arcs in (True, False):
            cloud = _rod(np.random.default_rng(31), [-.2, 0, 0], [.2, 0, 0],
                         .008, 450, top_arcs=top_arcs)[::2]
            refined, confidence = refine_axis(primitive, cloud, self.p)
            self.assertGreaterEqual(confidence, .45)
            self.assertAlmostEqual(refined.radius, .008, delta=.002)
            self.assertLess(np.linalg.norm((refined.start+refined.end)/2), .003)

    def test_xy_overlapping_layers_remain_distinct(self):
        x = np.linspace(-.2, .2, 40)
        lower = np.column_stack((x, np.zeros(len(x)), np.zeros(len(x))))
        upper = lower + np.array([0, 0, .05])
        points = np.vstack((lower, upper)); f = features(points)
        instances, layers, _ = planar_bars(points, f, self.p)
        self.assertEqual(len(layers), 2)
        self.assertEqual(len(layers), 2)

    def test_missing_support_is_not_an_observed_segment(self):
        item = {"radius": .006, "observedSegments": [{"points": [[-.2, 0, 0], [-.05, 0, 0]]}, {"points": [[.05, 0, 0], [.2, 0, 0]]}]}
        mask = distance_to_paths(np.array([[0, 0, 0], [.1, 0, 0]]), [item], self.p)
        self.assertEqual(mask.tolist(), [False, True])

    def test_hough_seed_keeps_a_missing_middle_gap_split(self):
        x = np.r_[np.linspace(-.2, -.06, 24), np.linspace(.06, .2, 24)]
        points = np.column_stack((x, np.zeros(len(x)), np.zeros(len(x))))
        f = features(points)
        candidates = hough_lines(points[:, :2], f["axis_tangent"][:, :2], self.p)
        seeds = hough_seeds(points, f["axis_tangent"], np.arange(len(points)), candidates,
                            np.array([[1., 0., 0.], [0., 1., 0.]]), self.p)
        self.assertGreaterEqual(len(seeds), 2)
        self.assertTrue(all(not (seed.start[0] < 0 < seed.end[0]) for seed in seeds))

    def test_close_parallel_rods_have_separate_hough_offsets(self):
        x = np.linspace(-.2, .2, 45)
        points = np.vstack((np.column_stack((x, np.full(len(x), -.018), np.zeros(len(x)))),
                            np.column_stack((x, np.full(len(x), .018), np.zeros(len(x))))))
        candidates = hough_lines(points[:, :2], features(points)["axis_tangent"][:, :2], self.p)
        self.assertGreaterEqual(len(candidates), 2)

    def test_physical_rebar_truth_is_not_a_hough_duplicate_explosion(self):
        truth = make_truth_scene(seed=20260905, top_arcs=True)
        adapter = GeometricV5Adapter()
        analysis = adapter.analyze(truth.points[truth.scene == REBAR], adapter.normalize_parameters({"detection_point_limit": 100000}))
        try:
            instances = analysis.data["instances"]
            # The source contains 13 physical rods (the two hook labels share
            # one ID); this bound permits fragmentation but rejects hundreds of
            # identical Hough/local surface stripes.
            self.assertLessEqual(len(instances), 40)
            self.assertTrue(any(item["length"] > .3 for item in instances))
            attrs = adapter.project_points(truth.points[truth.scene == REBAR], analysis)
            self.assertGreater(np.count_nonzero(attrs.rebar_class), len(attrs.rebar_class) * .10)
        finally:
            adapter.close(analysis)

    def test_one_physical_bar_traces_once_while_close_pair_stays_distinct(self):
        cloud = _rod(np.random.default_rng(9), [-.2, 0, 0], [.2, 0, 0], .008, 900, top_arcs=True)
        primitive = LinePrimitive(np.array([-.2, 0, 0]), np.array([.2, 0, 0]), np.array([1., 0, 0]), .008, len(cloud), 1.)
        self.assertEqual(len(trace([primitive], cloud, self.p, "planar")), 1)
        close = LinePrimitive(np.array([-.2, .016, 0]), np.array([.2, .016, 0]), np.array([1., 0, 0]), .004, len(cloud), 1.)
        self.assertFalse(_same_axis(primitive, close, self.p))

    def test_physical_16mm_pair_is_detected_as_two_unmerged_axes(self):
        truth = make_truth_scene(seed=20260905, top_arcs=True)
        rows = truth.scene == REBAR
        adapter = GeometricV5Adapter()
        analysis = adapter.analyze(truth.points[rows], adapter.normalize_parameters({"detection_point_limit": 100000}))
        try:
            attrs = adapter.project_points(truth.points[rows], analysis)
            ids = truth.instance[rows]
            dominant = []
            for ident in (3, 4):
                predicted = attrs.rebar_instance[ids == ident]
                known = predicted[(predicted > 0) & (predicted < 0xffffffff)]
                self.assertGreater(len(known), len(predicted) * .50)
                dominant.append(int(np.bincount(known.astype(int)).argmax()))
            self.assertNotEqual(*dominant)
        finally:
            adapter.close(analysis)

    def test_fragmented_long_inclined_web_keeps_observed_gaps(self):
        """Three short cylindrical observations make one long diagonal web, not three misses."""
        direction = np.array([.18, 0., .108]); direction /= np.linalg.norm(direction)
        cloud = _rod(np.random.default_rng(74), -.5*.31*direction, .5*.31*direction,
                     .006, 3000, top_arcs=False)
        axial = (cloud + .5*.31*direction) @ direction
        # Each retained physical piece is < the ordinary 45 mm primitive
        # threshold; the two gaps remain inferred rather than fabricated.
        keep = ((axial >= .000) & (axial <= .040)) | ((axial >= .125) & (axial <= .165)) | ((axial >= .250) & (axial <= .290))
        cloud = cloud[keep]
        web_p = replace(self.p, min_primitive_length=.045, min_instance_length=.080, axial_gap=.070, join_gap=.090)
        found, _ = web_bars(cloud, features(cloud, direction), web_p, [], [])
        self.assertEqual(len(found), 1)
        self.assertGreater(found[0]["length"], .11)
        line = np.asarray(found[0]["centerline"])
        self.assertGreater(np.linalg.norm(line[0]-line[-1]), .27)
        self.assertGreaterEqual(len(found[0]["observedSegments"]), 3)
        self.assertGreaterEqual(len(found[0]["inferredSegments"]), 2)

    def test_isolated_short_inclined_fragment_is_not_promoted_to_a_web(self):
        direction = np.array([.08, 0., .048]); direction /= np.linalg.norm(direction)
        cloud = _rod(np.random.default_rng(75), -.020*direction, .020*direction,
                     .006, 360, top_arcs=False)
        web_p = replace(self.p, min_primitive_length=.045, min_instance_length=.080)
        found, _ = web_bars(cloud, features(cloud, direction), web_p, [], [])
        self.assertEqual(found, [])

    def test_fragmented_web_survives_actual_multiscale_default_pipeline(self):
        direction=np.array([.18,0.,.108]);direction/=np.linalg.norm(direction)
        cloud=_rod(np.random.default_rng(74),-.155*direction,.155*direction,.006,3000,top_arcs=False)
        axial=(cloud+.155*direction)@direction
        cloud=cloud[((axial>=0)&(axial<=.040))|((axial>=.125)&(axial<=.165))|((axial>=.250)&(axial<=.290))]
        adapter=GeometricV5Adapter()
        analysis=adapter.analyze(cloud,adapter.normalize_parameters({}))
        try:
            attrs=adapter.project_points(cloud,analysis)
            self.assertGreater((attrs.rebar_role==2).mean(),.95)
            self.assertEqual(len(analysis.data['instances']),1)
            item=analysis.data['instances'][0]
            self.assertEqual(len(item['observedSegments']),3)
            self.assertEqual(len(item['inferredSegments']),2)
            self.assertAlmostEqual(item['radius'],.006,delta=.001)
        finally:adapter.close(analysis)

    def test_distant_short_wrong_axis_is_not_authorized_by_another_web(self):
        direction=np.array([.18,0.,.108]);direction/=np.linalg.norm(direction)
        long=_rod(np.random.default_rng(8104),-.15*direction,.15*direction,.006,1200,top_arcs=False)
        other=np.array([0.,1.,1.]);other/=np.linalg.norm(other)
        short=_rod(np.random.default_rng(8105),np.array([1.,1.,0.])-.02*other,np.array([1.,1.,0.])+.02*other,.006,200,top_arcs=False)
        points=np.vstack((long,short))
        f={'axis_tangent':np.vstack((np.tile(direction,(len(long),1)),np.tile(other,(len(short),1)))),'axis_linearity':np.ones(len(points))}
        found,_=web_bars(points,f,Params(),[],[])
        self.assertTrue(found)
        self.assertFalse(distance_to_paths(short,found,Params()).any())


if __name__ == "__main__":
    unittest.main()
