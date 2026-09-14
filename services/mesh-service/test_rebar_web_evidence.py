"""Adversarial synthetic coverage for web-cylinder evidence."""
import unittest

import numpy as np

from algorithms.rebar_control_net import fit_control_net
from test_rebar_control_net import inventory


_START = np.array([0., 0., .02])
_END = np.array([.24, .11, .23])


def _frame(start=_START, end=_END):
    direction = np.asarray(end, float) - np.asarray(start, float)
    direction /= np.linalg.norm(direction)
    helper = np.eye(3)[np.argmin(np.abs(direction))]
    u = np.cross(direction, helper)
    u /= np.linalg.norm(u)
    return u, np.cross(direction, u)


def _arc_tube(*, center_uv=(0., 0.), angle_start=0., angle_width=2*np.pi,
              along=84, around=18, tangent_normals=False):
    """Sample an oblique tube arc, optionally with deliberately tangential normals."""
    u, v = _frame()
    station, angle = np.meshgrid(
        np.linspace(0., 1., along),
        np.linspace(angle_start, angle_start + angle_width, around,
                    endpoint=angle_width < 2*np.pi),
        indexing="ij")
    center = (_START + station.ravel()[:, None]*(_END-_START)
              + center_uv[0]*u + center_uv[1]*v)
    radial = (np.cos(angle.ravel())[:, None]*u
              + np.sin(angle.ravel())[:, None]*v)
    if tangent_normals:
        normals = (-np.sin(angle.ravel())[:, None]*u
                   + np.cos(angle.ravel())[:, None]*v)
    else:
        normals = radial
    return center + .004*radial, normals


def _web_inventory():
    return inventory([(_START, _END)], kinds=["web"])


def _fit(points, normals):
    return fit_control_net(
        points, np.zeros(len(points), bool), _web_inventory(), normals=normals)


def _partial_web_with_tangential_clutter():
    # A real scan often exposes only one quadrant of an oblique web. A nearby
    # tangential surface trace has enough radial-only votes to resemble a
    # second parallel cylinder, while its normals disprove that interpretation.
    web, web_normals = _arc_tube(
        angle_start=.4, angle_width=np.pi/2)
    clutter, clutter_normals = _arc_tube(
        center_uv=(.012, 0.), angle_start=2.2,
        angle_width=np.deg2rad(10), around=24, tangent_normals=True)
    return web, clutter, np.vstack((web_normals, clutter_normals))


class RebarWebEvidenceTests(unittest.TestCase):
    def test_partial_web_wins_over_tangential_clutter(self):
        web, clutter, normals = _partial_web_with_tangential_clutter()
        report, attrs = _fit(np.vstack((web, clutter)), normals)

        self.assertEqual(report["counts"]["fittedUnits"], 1)
        self.assertEqual(report["instances"][0]["status"], "fitted")
        np.testing.assert_allclose(
            np.asarray(report["instances"][0]["centerlineM"]),
            np.asarray([_START, _END]), atol=.001)
        self.assertGreater(np.mean(attrs["control_instance"][:len(web)] == 1), .95)
        self.assertLess(np.mean(attrs["control_instance"][len(web):] != 0), .05)

    def test_web_evidence_is_invariant_to_normal_sign(self):
        web, clutter, normals = _partial_web_with_tangential_clutter()
        points = np.vstack((web, clutter))
        signs = np.where(np.arange(len(normals)) % 3, 1., -1.)[:, None]

        report, attrs = _fit(points, normals)
        flipped_report, flipped_attrs = _fit(points, normals*signs)

        self.assertEqual(report["instances"][0]["status"], "fitted")
        self.assertEqual(flipped_report["instances"][0]["status"], "fitted")
        np.testing.assert_allclose(
            report["instances"][0]["centerlineM"],
            flipped_report["instances"][0]["centerlineM"], atol=1e-12)
        np.testing.assert_array_equal(
            attrs["control_instance"], flipped_attrs["control_instance"])

    def test_missing_normals_do_not_force_an_identity(self):
        web, clutter, _ = _partial_web_with_tangential_clutter()
        report, attrs = _fit(np.vstack((web, clutter)), None)

        self.assertEqual(report["counts"]["fittedUnits"], 0)
        self.assertEqual(report["instances"][0]["status"], "pending")
        self.assertEqual(report["instances"][0]["reason"],
                         "ambiguous-parallel-support")
        self.assertFalse(np.any(attrs["control_instance"]))

    def test_two_real_parallel_webs_remain_ambiguous(self):
        left, left_normals = _arc_tube(center_uv=(-.012, 0.), around=16)
        right, right_normals = _arc_tube(center_uv=(.012, 0.), around=16)
        points = np.vstack((left, right))
        normals = np.vstack((left_normals, right_normals))

        for supplied_normals in (normals, None):
            with self.subTest(normals=supplied_normals is not None):
                report, attrs = _fit(points, supplied_normals)
                self.assertEqual(report["counts"]["fittedUnits"], 0)
                self.assertEqual(report["instances"][0]["status"], "pending")
                self.assertEqual(report["instances"][0]["reason"],
                                 "ambiguous-parallel-support")
                self.assertFalse(np.any(attrs["control_instance"]))

    def test_planar_fixture_cannot_become_a_web_cylinder(self):
        u, v = _frame()
        station, width = np.meshgrid(
            np.linspace(0., 1., 84), np.linspace(-.001, .001, 13),
            indexing="ij")
        points = (_START + station.ravel()[:, None]*(_END-_START)
                  + .004*u + width.ravel()[:, None]*v)
        normals = np.tile(u, (len(points), 1))

        for supplied_normals in (normals, None):
            with self.subTest(normals=supplied_normals is not None):
                report, attrs = _fit(points, supplied_normals)
                self.assertEqual(report["counts"]["fittedUnits"], 0)
                self.assertEqual(report["instances"][0]["status"], "pending")
                self.assertFalse(np.any(attrs["control_instance"]))


if __name__ == "__main__":
    unittest.main()
