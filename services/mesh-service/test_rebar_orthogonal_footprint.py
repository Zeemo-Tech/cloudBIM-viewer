import json, os, unittest
import numpy as np
from algorithms.rebar_orthogonal_footprint import build_orthogonal_footprint
from algorithms.rebar_curve_envelope import split_curve_bars

def bar(name, points, radius=.01): return {"designBarId": name, "points": points, "radiusM": radius, "coverage": "complete"}
def cell(report, x, y):
    xs, ys, active = np.array(report["gridXM"]), np.array(report["gridYM"]), np.array(report["activeCells"])
    local = (np.array([x, y])-np.array(report["xyOriginM"])) @ np.array(report["xyAxes"]).T
    xi, yi = np.searchsorted(xs, local[0], side="right")-1, np.searchsorted(ys, local[1], side="right")-1
    return bool(0 <= xi < active.shape[1] and 0 <= yi < active.shape[0] and active[yi, xi])

class OrthogonalFootprintTests(unittest.TestCase):
    def test_l_concavity_is_not_convexified_and_surface_boundary_is_exact(self):
        report = build_orthogonal_footprint({"bars": [bar("x", [[0,0,0],[3,0,0]]), bar("y", [[0,0,0],[0,3,0]])]})
        self.assertTrue(cell(report, .5, .01)); self.assertFalse(cell(report, 2., 2.))
        self.assertTrue(cell(report, 1., .014))  # 10 mm radius + 5 mm clearance
        self.assertFalse(cell(report, 1., .016))

    def test_rotation_translation_invariance(self):
        base = {"bars": [bar("a", [[0,0,0],[2,0,0]]), bar("b", [[0,0,0],[0,1,0]])]}
        rotation = np.array([[0,-1],[1,0]]); shift = np.array([4.,-2.])
        transformed = {"bars": [bar(x["designBarId"], [np.r_[np.array(p[:2])@rotation.T+shift,p[2]].tolist() for p in x["points"]]) for x in base["bars"]]}
        one, two = build_orthogonal_footprint(base), build_orthogonal_footprint(transformed)
        self.assertTrue(cell(one, .5, .01)); self.assertTrue(cell(two, *(.5*np.array([1,0])@rotation.T+shift)))

    def test_non_decimal_boundary_and_endpoint_total_allowance(self):
        report = build_orthogonal_footprint({"bars": [bar("e", [[.0037,.0019,0],[1.0037,.0019,0]], .007)]})
        axes, origin, xs = np.array(report["xyAxes"]), np.array(report["xyOriginM"]), np.array(report["gridXM"])
        local_start = (np.array([.0037,.0019])-origin) @ axes.T
        # Along this axis the end must reach exactly 12 mm, not 24 mm (radius
        # plus clearance is already part of the total endpoint budget).
        self.assertTrue(np.any(np.isclose(xs, local_start[0]-.012, atol=1e-9)))
        self.assertTrue(cell(report, .5037, .0019+.0119)); self.assertFalse(cell(report, .5037, .0019+.0121))

    def test_cached_body_grid_is_bounded_when_available(self):
        path = os.path.join(os.path.dirname(__file__), "..", "..", ".cloudbim", "design-prior", "design-prior-cache", "aligned-9a9511965ab611edf7fd0a021bb84f40ce98a16417fd151bbdafb085ffe63d37.json")
        if not os.path.exists(path): self.skipTest("cache unavailable")
        with open(path) as handle: cached = json.load(handle)
        body, _ = split_curve_bars(cached)
        report = build_orthogonal_footprint(body)
        self.assertLessEqual(np.array(report["activeCells"]).size, 60000); self.assertGreater(np.array(report["activeCells"]).sum(), 0)

if __name__ == "__main__": unittest.main()
