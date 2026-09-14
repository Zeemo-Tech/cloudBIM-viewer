import unittest

import numpy as np

from algorithms.fixture_regions import classify_regions


def synthetic_frame(angle_degrees=27.0, include_frame=True):
    rng = np.random.default_rng(5)
    angle = np.deg2rad(angle_degrees)
    axes = np.array([[np.cos(angle), np.sin(angle)],
                     [-np.sin(angle), np.cos(angle)]])

    clouds, labels, rail_clouds = [], [], []
    if include_frame:
        for u in (np.arange(-1.0, -.919, .004), np.arange(.92, 1.001, .004)):
            uu, vv = np.meshgrid(u, np.arange(-.5, .501, .004), indexing="ij")
            rail_clouds.append(np.column_stack((uu.ravel(), vv.ravel())))
        for v in (np.arange(-.5, -.419, .004), np.arange(.42, .501, .004)):
            uu, vv = np.meshgrid(np.arange(-1.0, 1.001, .004), v, indexing="ij")
            rail_clouds.append(np.column_stack((uu.ravel(), vv.ravel())))
    else:
        uu, vv = np.meshgrid(np.arange(-1.0, -.919, .004),
                             np.arange(-.5, .501, .004), indexing="ij")
        rail_clouds.append(np.column_stack((uu.ravel(), vv.ravel())))
    rails = np.vstack(rail_clouds)
    clouds.append(np.column_stack((rails @ axes, np.full(len(rails), .04))))
    labels.append(np.full(len(rails), 2, np.uint8))

    # A long, thin fixture-labelled interior web must not become a boundary.
    u = np.arange(-.8, .801, .004)
    web = np.column_stack((u, np.full(len(u), .12)))
    clouds.append(np.column_stack((web @ axes, np.full(len(web), .07))))
    labels.append(np.full(len(web), 2, np.uint8))

    interior = rng.uniform([-.85, -.35], [.85, .35], (500, 2))
    on_rail = np.array([[-.98, 0.0], [.98, .1], [0.0, -.48], [.2, .48]])
    exterior = np.array([[-1.04, 0.0], [1.04, .1], [0.0, -.54], [.2, .54]])
    steel = np.vstack((interior, on_rail, exterior))
    clouds.append(np.column_stack((steel @ axes, np.full(len(steel), .10))))
    labels.append(np.full(len(steel), 3, np.uint8))
    table = rng.uniform([-1.2, -.7], [1.2, .7], (200, 2))
    clouds.append(np.column_stack((table @ axes, np.zeros(len(table)))))
    labels.append(np.ones(len(table), np.uint8))
    positions, classes = np.vstack(clouds), np.concatenate(labels)

    pixel = .004
    low = positions[:, :2].min(axis=0) - .02
    ij = np.floor((positions[:, :2] - low) / pixel).astype(np.int32)
    nx, ny = ij.max(axis=0) + 2
    source_to_pixel = ij[:, 1] * nx + ij[:, 0]
    # The projection stage's width evidence contains the substantial rails,
    # while the deliberately thin internal web is absent.
    wide = np.zeros((ny, nx), bool)
    rail_ij = ij[:len(rails)]
    wide[rail_ij[:, 1], rail_ij[:, 0]] = True
    report = {"gridShape": [int(ny), int(nx)], "pixelSizeM": pixel,
              "xyOriginM": low.tolist()}
    cache = {"source_to_pixel": source_to_pixel, "wide": wide}
    return positions, classes, report, cache, len(rails) + len(web), len(interior)


class FixtureRegionTests(unittest.TestCase):
    def test_rotated_outer_rail_enclosure_and_source_order_mapping(self):
        positions, classes, projection, cache, steel_start, inside_count = synthetic_frame()
        report, result_cache, regions = classify_regions(
            positions, classes, projection, cache, workers=2)
        self.assertTrue(report["frame"]["detected"])
        self.assertEqual(regions.dtype, np.uint8)
        self.assertTrue(np.all(regions[classes == 1] == 0))
        self.assertTrue(np.all(regions[classes == 2] == 3))
        steel_regions = regions[steel_start:steel_start + inside_count + 8]
        self.assertTrue(np.all(steel_regions[:inside_count + 4] == 1))
        self.assertTrue(np.all(steel_regions[-4:] == 2))
        self.assertGreater(result_cache["inside"].sum(), 1)

        corners = np.asarray(report["frame"]["cornersM"])
        angle = np.deg2rad(27.0)
        axes = np.array([[np.cos(angle), np.sin(angle)],
                         [-np.sin(angle), np.cos(angle)]])
        local = corners @ axes.T
        np.testing.assert_allclose(local.min(axis=0), [-1.0, -.5], atol=.035)
        np.testing.assert_allclose(local.max(axis=0), [1.0, .5], atol=.035)
        inner = np.asarray(report['frame']['innerCornersM']) @ axes.T
        np.testing.assert_allclose(inner.min(axis=0), [-.92, -.42], atol=.035)
        np.testing.assert_allclose(inner.max(axis=0), [.92, .42], atol=.035)
        self.assertTrue(report['frame']['innerDetected'])
        self.assertEqual(result_cache['frame_inner_bounds_local'].shape, (4,))

    def test_missing_frame_does_not_force_inside_region(self):
        positions, classes, projection, cache, _, _ = synthetic_frame(include_frame=False)
        report, result_cache, regions = classify_regions(positions, classes, projection, cache)
        self.assertFalse(report["frame"]["detected"])
        self.assertTrue(np.all(regions[classes == 3] == 4))
        self.assertTrue(np.all(regions[classes == 2] == 3))
        self.assertEqual(int(result_cache["inside"].sum()), 0)


if __name__ == "__main__":
    unittest.main()
