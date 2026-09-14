import unittest

import numpy as np

from algorithms.rebar_v5 import GeometricV5Adapter
from algorithms.rebar_v5.contracts import FIXTURE, Params, ROLE_PLANAR
from algorithms.rebar_v5.features import multiscale
from algorithms.rebar_v5.hooks import recover_terminal_hooks
from rebar_validation import _rod


def smooth_u_hook(*, complete=True):
    """The physical torus/return-arm regression, with a 16 mm neighbour."""
    rng = np.random.default_rng(7301)
    body = _rod(rng, [-.4, 0, 0], [.12, 0, 0], .006, 1800, top_arcs=False)
    theta = rng.uniform(-np.pi/2, np.pi/2 if complete else 0.0, 1800)
    phi = rng.uniform(0, 2*np.pi, 1800)
    radial = np.column_stack((np.cos(theta), np.zeros(len(theta)), np.sin(theta)))
    arc = np.column_stack((.12+.04*np.cos(theta), np.zeros(len(theta)),
                           .04+.04*np.sin(theta)))
    arc += .006*(np.cos(phi)[:, None]*radial
                 + np.sin(phi)[:, None]*np.array([0., 1., 0.]))
    arc += rng.normal(0, .00015, arc.shape)
    arm = _rod(rng, [-.02, 0, .08], [.12, 0, .08], .006, 600, top_arcs=False)
    neighbour = _rod(rng, [-.2, .016, .08], [.3, .016, .08], .006, 1600,
                     top_arcs=False)
    points = np.vstack((body, arc, arm, neighbour))
    truth = np.repeat(np.arange(4), [len(body), len(arc), len(arm), len(neighbour)])
    return points, truth


def biased_parent():
    # The ordinary straight detector extends about 33 mm into the turn.  Hook
    # association must recover the physical endpoint instead of trusting it.
    return {
        "id": 1, "role": "planar", "layerId": 1, "radius": .006,
        "centerline": [[-.4, 0, 0], [.153, 0, 0]],
        "observedSegments": [{"points": [[-.4, 0, 0], [.153, 0, 0]]}],
        "inferredSegments": [], "pointCount": 100, "length": .553,
        "confidence": 1., "evidence": "observed", "direction": [1, 0, 0],
    }


def torus_hook(rng, anchor, outward, up):
    anchor, outward, up = (np.asarray(value, dtype=float) for value in (anchor, outward, up))
    normal = np.cross(outward, up)
    theta = rng.uniform(0., np.pi, 1400)
    phi = rng.uniform(0., 2*np.pi, len(theta))
    centreline = (anchor + (.04*np.sin(theta))[:, None]*outward
                  + (.04*(1.-np.cos(theta)))[:, None]*up)
    bend_radial = (np.sin(theta))[:, None]*outward-(np.cos(theta))[:, None]*up
    arc = centreline+.006*(np.cos(phi)[:, None]*bend_radial
                           + np.sin(phi)[:, None]*normal)
    arc += rng.normal(0., .00015, arc.shape)
    top = anchor+.08*up
    arm = _rod(rng, top, top-.14*outward, .006, 500, top_arcs=False)
    return np.vstack((arc, arm))


class TerminalHookTests(unittest.TestCase):
    def test_smooth_physical_u_hook_survives_the_full_pipeline(self):
        points, truth = smooth_u_hook()
        adapter = GeometricV5Adapter()
        result = adapter.analyze(points, adapter.normalize_parameters({"table_min_area": 1.}))
        try:
            attrs = adapter.project_points(points, result)
            stages = {stage["name"]: stage for stage in result.data["diagnostics"]["stages"]}
            self.assertEqual(stages["fixtures"]["confirmedPointCount"], 0)
            self.assertEqual(stages["terminal-hooks"]["attachedCount"], 1)
            self.assertLessEqual(stages["terminal-hooks"]["candidateCount"], 4)
            self.assertFalse(np.any(attrs.scene_class[truth == 1] == FIXTURE))
            self.assertFalse(np.any(attrs.fixture_kind[truth == 1]))
            for part in (0, 1, 2, 3):
                self.assertGreater(np.mean(attrs.rebar_class[truth == part] > 0), .90)

            def dominant(part):
                values = attrs.rebar_instance[truth == part]
                values = values[(values > 0) & (values < np.iinfo(np.uint32).max)]
                return int(np.bincount(values.astype(int)).argmax())

            hook_id = dominant(1)
            self.assertEqual(dominant(0), hook_id)
            self.assertEqual(dominant(2), hook_id)
            self.assertNotEqual(dominant(3), hook_id)
            self.assertTrue(np.all(attrs.rebar_role[np.isin(truth, (0, 1, 2))] == ROLE_PLANAR))
            self.assertEqual(result.data["intersections"], [])
            fitted = stages["terminal-hooks"]["fits"][0]
            self.assertEqual(fitted["parentOrdinal"], 0)
            self.assertAlmostEqual(fitted["radius"], .040, delta=.004)
            self.assertAlmostEqual(fitted["anchor"][0], .120, delta=.005)
            self.assertGreater(fitted["confidence"], .50)
            instance = next(item for item in result.data["instances"] if item["id"] == hook_id)
            lower_body = np.vstack([np.asarray(path["points"]) for path in instance["observedSegments"]
                                    if len(path["points"]) >= 2
                                    and np.max(np.abs(np.asarray(path["points"])[:, 2])) < .01
                                    and np.linalg.norm(np.asarray(path["points"])[-1]
                                                       - np.asarray(path["points"])[0]) > .10])
            self.assertLess(float(lower_body[:, 0].min()), -.35)
            self.assertLess(float(lower_body[:, 0].max()), .130)
        finally:
            adapter.close(result)

    def test_missing_half_arc_does_not_create_an_observed_chord(self):
        points, _ = smooth_u_hook(complete=False)
        p = Params()
        features = multiscale(points, points, p)
        parent = biased_parent()
        result, diagnostic = recover_terminal_hooks(points, features, p, [parent],
                                                    [{"id": 1, "height": 0.},
                                                     {"id": 2, "height": .080}])
        self.assertEqual(diagnostic["attachedCount"], 0)
        self.assertEqual(result[0]["observedSegments"], parent["observedSegments"])

    def test_both_parent_terminals_can_own_independent_observed_hooks(self):
        rng = np.random.default_rng(771)
        body = _rod(rng, [-.12, 0, 0], [.12, 0, 0], .006, 1200, top_arcs=False)
        left = torus_hook(rng, [-.12, 0, 0], [-1, 0, 0], [0, 0, -1])
        right = torus_hook(rng, [.12, 0, 0], [1, 0, 0], [0, 0, 1])
        points = np.vstack((body, left, right))
        p = Params()
        parent = biased_parent()
        parent["centerline"] = [[-.153, 0, 0], [.153, 0, 0]]
        parent["observedSegments"] = [{"points": parent["centerline"]}]
        found, diagnostic = recover_terminal_hooks(points, multiscale(points, points, p), p,
                                                   [parent], [])
        self.assertEqual(diagnostic["attachedCount"], 2)
        self.assertEqual({item["terminalOrdinal"] for item in diagnostic["fits"]}, {0, 1})
        trimmed = np.asarray(found[0]["observedSegments"][0]["points"])
        self.assertAlmostEqual(float(trimmed[:, 0].min()), -.12, delta=.005)
        self.assertAlmostEqual(float(trimmed[:, 0].max()), .12, delta=.005)
        observed = np.vstack([np.asarray(path["points"])
                              for path in found[0]["observedSegments"][1:]])
        self.assertLess(float(observed[:, 2].min()), -.075)
        self.assertGreater(float(observed[:, 2].max()), .075)


if __name__ == "__main__":
    unittest.main()
