import json
from pathlib import Path
import tempfile
import unittest

import laspy
import numpy as np
import trimesh

from analysis_mesh.artifact import build_artifact
from analysis_mesh.contracts import Component, ComponentMeshStream, MeshPart
from rebar_comparison import compute_instance_comparison, refresh_rebar_comparison
from rebar_metrics import measure_bar
from rebar_scan_surface import ObservedRebarSurface


IDENTITY = np.eye(4).T.ravel().tolist()
SEGMENTS = [('u', np.array([0., 0., -.1]), np.array([0., 0., .1]))]
VERTICES = np.array([[-.004, 0., 0.], [.004, 0., 0.], [0., .004, 0.]])
NORMALS = np.array([[-1., 0., 0.], [1., 0., 0.], [0., 1., 0.]])


def cylinder(shift=-.006, angles=None, radius=.004, tilt=0.):
    if angles is None:
        angles = np.linspace(0, 2 * np.pi, 128, endpoint=False)
    z, a = np.meshgrid(np.linspace(-.1, .1, 201), angles, indexing='ij')
    return np.column_stack((radius*np.cos(a.ravel()) + shift + tilt*z.ravel(),
                            radius*np.sin(a.ravel()), z.ravel()))


def surface(points, segments=SEGMENTS, unit_points=None):
    if unit_points is None:
        unit_points = {'u': points}
    result = measure_bar(np.empty(0), np.empty((0, 3)), segments, points,
                         unit_points=unit_points, radii={s[0]: .004 for s in segments})
    return ObservedRebarSurface(segments, unit_points, result['longitudinalProfile']), result


class ObservedSurfaceTests(unittest.TestCase):
    def test_translation_matches_same_side_even_beyond_diameter_and_k_one(self):
        for shift in (-.006, -.012, .006):
            with self.subTest(shift=shift):
                model, report = surface(cylinder(shift))
                for k in (1, 32, 64):
                    values, selected, known = model.match(VERTICES, NORMALS, k=k)
                    np.testing.assert_allclose(values, [-shift, shift, 0.], atol=1e-8)
                    self.assertTrue(known.all())
                    self.assertTrue((selected >= 0).all())
                np.testing.assert_allclose(report['bending']['maxCentrelineDepartureM'], abs(shift), atol=1e-8)
                self.assertLess(report['bending']['residualBowM'], 1e-8)
                self.assertLess(report['crossSection']['maxAbsRadiusDeltaM'], 1e-8)
                np.testing.assert_allclose(report['longitudinalProfile'][0]['offsetVectorM'], [shift, 0, 0], atol=1e-8)

    def test_half_scan_never_fabricates_back_face(self):
        model, report = surface(cylinder(angles=np.linspace(-np.pi/2, np.pi/2, 129)))
        values, _, known = model.match(VERTICES, NORMALS, k=1)
        self.assertTrue(np.isnan(values[0]))
        self.assertFalse(known[0])
        self.assertAlmostEqual(values[1], -.006)
        self.assertGreater(report['crossSection']['supportedSectionCount'], 0)

    def test_tiny_arc_and_missing_topology_are_unknown(self):
        for points, segments in ((cylinder(angles=np.linspace(0, np.pi/2, 129)), SEGMENTS),
                                 (cylinder(), []), (np.array([[-.002, 0, 0]]), SEGMENTS)):
            model, _ = surface(points, segments=segments)
            values, selected, known = model.match(VERTICES, NORMALS)
            self.assertTrue(np.isnan(values).all())
            self.assertTrue((selected == -1).all())
            self.assertFalse(known.any())

    def test_distance_cap_uses_full_displacement_not_signed_projection(self):
        model, _ = surface(cylinder(-.012))
        values, _, known = model.match(VERTICES, NORMALS, max_search_distance=.005)
        self.assertTrue(np.isnan(values).all())
        self.assertFalse(known.any())

    def test_radius_change_is_separate_from_translation(self):
        model, report = surface(cylinder(radius=.0045))
        values, _, _ = model.match(VERTICES, NORMALS)
        np.testing.assert_allclose(values, [.0065, -.0055, .0005], atol=1e-8)
        self.assertAlmostEqual(report['crossSection']['maxAbsRadiusDeltaM'], .0005)

    def test_rotated_large_world_coordinates_preserve_millimetres(self):
        rotation = trimesh.transformations.rotation_matrix(.71, [1., 2., 3.])[:3, :3]
        translation = np.array([1e6, -2e6, 3e6])
        points = cylinder() @ rotation.T + translation
        segments = [('u', start @ rotation.T + translation, end @ rotation.T + translation)
                    for _, start, end in SEGMENTS]
        model, _ = surface(points, segments)
        values, _, _ = model.match(VERTICES @ rotation.T + translation, NORMALS @ rotation.T)
        np.testing.assert_allclose(values, [.006, -.006, 0], atol=2e-8)

    def test_ninety_degree_setting_still_rejects_opposite_normals(self):
        model, _ = surface(cylinder(angles=np.linspace(-np.pi/2, np.pi/2, 129)))
        # With 90 degrees, perpendicular samples are allowed by the public
        # setting; use a narrower right-facing sector in the supported model.
        unit = model.units[0]
        keep = unit['normals'][:, 0] > .5
        for key in ['points', 'normals', 'stations']:
            unit[key] = unit[key][keep]
        values, _, _ = model.match(VERTICES[:1], NORMALS[:1], max_angle_deg=90.)
        self.assertTrue(np.isnan(values).all())

    def test_axial_gap_is_not_interpolated_as_observation(self):
        points = cylinder()
        points = points[np.abs(points[:, 2]) > .04]
        model, _ = surface(points)
        values, _, _ = model.match(VERTICES, NORMALS)
        self.assertTrue(np.isnan(values).all())

    def test_tilt_and_noisy_uneven_sampling_keep_centerline_and_sign(self):
        points = cylinder(tilt=.03)
        points = np.concatenate((points, points[points[:, 0] > -.004]))
        points += np.random.default_rng(42).normal(0, .00002, points.shape)
        model, report = surface(points)
        values, _, _ = model.match(VERTICES, NORMALS)
        np.testing.assert_allclose(values[:2], [.006, -.006], atol=.0001)
        self.assertLess(report['bending']['residualBowM'], .0001)

    def test_rapid_center_motion_cannot_claim_precise_section_centers(self):
        angles = np.linspace(0, 2*np.pi, 128, endpoint=False)
        z, a = np.meshgrid(np.linspace(-.1, .1, 1601), angles, indexing='ij')
        center = lambda station: .003 * np.sin(2*np.pi*32*(station+.1)/.2)
        points = np.column_stack((center(z.ravel())+.004*np.cos(a.ravel()), .004*np.sin(a.ravel()), z.ravel()))
        points += np.random.default_rng(44).normal(0, .00002, points.shape)
        model, report = surface(points)
        supported = [r for r in report['longitudinalProfile'] if r['observedCenterM'] is not None]
        for row in supported:
            self.assertLess(abs(row['observedCenterM'][0] - center(row['stationM'] - .1)), .0005)
        self.assertTrue(any(r['quality'] == 'unstable-section' for r in report['longitudinalProfile']))

    def test_bent_bar_uses_local_unit_and_never_borrows_other_limb(self):
        first = cylinder()
        # Second limb along X, extending from the Z limb's design endpoint.
        second = cylinder(.009)[:, [2, 1, 0]] + np.array([.1, 0, .1])
        segments = SEGMENTS + [('v', np.array([0., 0, .1]), np.array([.2, 0, .1]))]
        points = np.concatenate((first, second))
        model, _ = surface(points, segments, {'u': first, 'v': second})
        vertices = np.array([[-.004, 0, 0], [.1, 0, .104]])
        normals = np.array([[-1., 0, 0], [0., 0, 1.]])
        values, _, _ = model.match(vertices, normals)
        np.testing.assert_allclose(values, [.006, .009], atol=1e-8)
        missing, _ = surface(points, segments, {'v': second})
        self.assertTrue(np.isnan(missing.match(vertices, normals)[0][0]))

    def test_production_split_mesh_reports_signed_offsets_and_recolor_preserves_evidence(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            mesh = trimesh.creation.cylinder(radius=.004, height=.2, sections=32).subdivide()
            mesh.invert()
            build_artifact(ComponentMeshStream([Component('A', [MeshPart('part', 'part', mesh, np.eye(4), 'source')])], {}),
                           root / 'analysis', {'id': 'test'}, face_cap=50)
            mapping = {'schema': 'rebar-instance-map-v1', 'inventory': {
                'bars': [{'designBarId': 'A', 'ifcGlobalId': 'A'}],
                'units': [{'designBarId': 'A', 'designUnitId': 'u', 'diameterM': .008,
                           'startM': [0, 0, -.1], 'endM': [0, 0, .1]}]},
                'instances': [{'id': 1, 'designBarId': 'A', 'designUnitId': 'u', 'reviewStatus': 'matched'}]}
            (root / 'map.json').write_text(json.dumps(mapping))
            las = laspy.create(point_format=3, file_version='1.2')
            las.header.scales = np.full(3, 1e-7)
            las.add_extra_dim(laspy.ExtraBytesParams(name='cloudbim_instance_id', type=np.uint32))
            points = cylinder()
            las.x, las.y, las.z = points.T
            las['cloudbim_instance_id'] = np.ones(len(points), np.uint32)
            las.write(root / 'scan.las')
            result = compute_instance_comparison(str(root / 'scan.las'), str(root / 'analysis'), str(root / 'map.json'),
                IDENTITY, voxel_size=.001, downsample_enabled=False, max_histogram_distance=.1,
                histogram_bins=20, tolerance=.005, normal_constraint_enabled=True, normal_fallback_mode='unknown', knn_k=1)
            xyz = np.asarray(result['mesh'].vertices)
            for vertex, expected in zip(VERTICES[:2], [.006, -.006]):
                at = np.all(np.isclose(xyz, vertex, atol=1e-8), axis=1)
                self.assertTrue(at.any())
                np.testing.assert_allclose(result['distances'][at], expected, atol=1e-7)
            report = result['rebarComparison']
            row = report['bars'][0]
            self.assertEqual(row['constraint']['fallbackKnownCount'], 0)
            self.assertGreater(row['scanSurface']['supportedPointCount'], 0)
            self.assertAlmostEqual(row['measurement']['bending']['maxCentrelineDepartureM'], .006, places=6)
            refreshed = refresh_rebar_comparison(report, result['distances'], .02, 20, .003)
            self.assertEqual(refreshed['bars'][0]['measurement'], row['measurement'])
            json.dumps(report, allow_nan=False)


if __name__ == '__main__':
    unittest.main()
