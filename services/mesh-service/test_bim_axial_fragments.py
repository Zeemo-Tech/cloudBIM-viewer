"""Source-supported fragments may link across gaps, without filling those gaps."""
import unittest
from unittest.mock import patch

import numpy as np
from scipy.spatial.transform import Rotation
from threadpoolctl import threadpool_limits

from algorithms.bim_bent_bars import (_fit_ifc_axis, _grow_axis_support, _refine_fixed_axis_support,
                                     _section_task, recover_bent_bars)
from test_bim_bent_bars import data, inventory


def tube(spans):
    positions = np.concatenate([np.arange(lo, hi, .001) for lo, hi in spans])
    angles = np.linspace(0, 2*np.pi, 24, endpoint=False)
    normals = np.tile(np.c_[np.cos(angles), np.sin(angles)], (len(positions), 1))
    return np.repeat(positions, len(angles)), .004*normals, normals


def mask(along, intervals):
    result = np.zeros(len(along), bool)
    for lo, hi in intervals:
        result |= (along >= lo) & (along <= hi)
    return result


class AxialFragmentTests(unittest.TestCase):
    def test_short_fragment_claims_wait_for_later_body_fit_and_preserve_coarse_owners(self):
        ss = np.r_[np.linspace(.2425, .2495, 20), np.arange(.30, .70, .001)]
        angles = np.linspace(0, 2*np.pi, 24, endpoint=False)
        along = np.repeat(ss, len(angles))
        radial_normals = np.tile(np.c_[np.cos(angles), np.sin(angles)], (len(ss), 1))
        second, second_normals = data(offset=.06, bend=0)
        first = np.c_[along, .004*radial_normals]
        points = np.vstack((first, second))
        normals = np.vstack((np.c_[np.zeros(len(along)), radial_normals], second_normals))
        fragment = np.r_[along < .28, np.zeros(len(second), bool)]
        owner = np.zeros(len(points), np.uint32)
        status = np.full(len(points), 2, np.uint8)
        rows = [dict(id=i+1, designUnitId=str(i), designBarId=str(i), status='missing', pointCount=0)
                for i in range(2)]
        fitted = {}
        coarse_owner = []

        def fit(*args, **kwargs):
            if args[4]['source']['designUnitId'] == '1':
                self.assertFalse(owner[fragment].any(),
                                 'Early fragment claims changed the next body fitting input')
            result = _fit_ifc_axis(*args, **kwargs)
            self.assertIsNotNone(result[0])
            f = result[0]['parameters']
            fitted[args[4]['source']['designUnitId']] = np.array(f['transverseCoefficientsM'])
            return result

        def refine(*args, **kwargs):
            self.assertEqual(len(fitted), 2, 'Every body model must settle before fragment claims')
            if not coarse_owner:
                coarse_owner.append(owner.copy())
            return _refine_fixed_axis_support(*args, **kwargs)

        def central_sections(task):
            if task[5][1] < .03 and task[1] < .30:
                return task[0], task[1], []
            return _section_task(task)

        with threadpool_limits(limits=1), \
             patch('algorithms.bim_bent_bars._fit_ifc_axis', side_effect=fit), \
             patch('algorithms.bim_bent_bars._refine_fixed_axis_support', side_effect=refine), \
             patch('algorithms.bim_bent_bars._section_task', side_effect=central_sections):
            recover_bent_bars(points, normals, inventory([0, .06]), rows, status, owner,
                              {'objects': []}, {'objects': []})
        self.assertGreater(np.mean(owner[fragment] == 1), .95)
        retained = coarse_owner[0] > 0
        np.testing.assert_array_equal(owner[retained], coarse_owner[0][retained])
        for row in rows:
            np.testing.assert_array_equal(row['flexibleRegistration']['transverseCoefficientsM'],
                                          fitted[row['designUnitId']])
            self.assertEqual(row['pointCount'], np.count_nonzero(owner == row['id']))

    def test_short_fragment_is_not_lost_between_coarse_station_windows(self):
        # These 7 mm fragments sit across the ends of two 16 mm windows on a
        # 12 mm grid: neither clipped half spans the required 6 mm by itself.
        ss = np.r_[np.linspace(.2425, .2495, 20), np.arange(.30, .70, .001),
                   np.linspace(.7465, .7535, 20)]
        angles = np.linspace(0, 2*np.pi, 24, endpoint=False)
        along = np.repeat(ss, len(angles))
        normals = np.tile(np.c_[np.cos(angles), np.sin(angles)], (len(ss), 1))
        intervals, _, evidence = _grow_axis_support(
            along, .004*normals, normals, np.ones(len(along), bool),
            np.arange(.312, .69, .012), 1.2, .004, lambda s: True)
        for fragment in (along < .28, along > .72):
            self.assertGreater(np.mean(mask(along[fragment], intervals)), .95)
        self.assertFalse(mask(np.array([.275, .725]), intervals).any())
        self.assertFalse(mask(np.array([.24, .252, .744, .756]), intervals).any(),
                         'New short-fragment observations must stop at measured source bounds')
        self.assertGreater(evidence['adaptiveGrownSections'], 0)

    def test_refinement_only_revisits_windows_with_unclaimed_surface_points(self):
        along, radial, normals = tube([(.12, 1.08)])
        _, _, evidence = _grow_axis_support(along, radial, normals, np.ones(len(along), bool),
                                             np.arange(.12, 1.081, .012), 1.2, .004, lambda s: True)
        self.assertEqual(evidence['adaptiveWindowsEvaluated'], 0,
                         'Fully supported data should not pay for a second dense window sweep')

    def test_one_short_fragment_does_not_recheck_every_supported_body_midpoint(self):
        ss = np.r_[np.linspace(.0385, .0455, 20), np.arange(.10, 1.08, .001)]
        angles = np.linspace(0, 2*np.pi, 24, endpoint=False)
        along = np.repeat(ss, len(angles))
        normals = np.tile(np.c_[np.cos(angles), np.sin(angles)], (len(ss), 1))
        queried = []
        def allowed(s):
            queried.append(s)
            return True
        intervals, _, _ = _grow_axis_support(along, .004*normals, normals, np.ones(len(along), bool),
                                             np.arange(.108, 1.081, .012), 1.2, .004, allowed)
        self.assertGreater(np.mean(mask(along[along < .06], intervals)), .95)
        self.assertLess(len(queried), 30,
                        'The fragment incurred identity searches along the entire established body')

    def test_refined_fragment_cannot_jump_an_identity_barrier_between_coarse_stations(self):
        ss = np.r_[np.linspace(.2425, .2495, 20), np.arange(.30, .70, .001)]
        angles = np.linspace(0, 2*np.pi, 24, endpoint=False)
        along = np.repeat(ss, len(angles))
        normals = np.tile(np.c_[np.cos(angles), np.sin(angles)], (len(ss), 1))
        intervals, _, _ = _grow_axis_support(along, .004*normals, normals, np.ones(len(along), bool),
            np.arange(.312, .69, .012), 1.2, .004, lambda s: not .268 < s < .272)
        self.assertFalse(mask(along[along < .28], intervals).any())

    def test_links_short_fragments_in_both_directions_and_keeps_gaps_unobserved(self):
        spans = [(.18, .22), (.27, .31), (.36, .84), (.89, .93), (.98, 1.02)]
        along, radial, normals = tube(spans)
        intervals, _, evidence = _grow_axis_support(
            along, radial, normals, np.ones(len(along), bool), np.arange(.372, .83, .012),
            1.2, .004, lambda s: True)
        selected = mask(along, intervals)
        for lo, hi in spans:
            self.assertGreater(np.mean(selected[(along >= lo) & (along < hi)]), .95,
                               f'Valid fragment {lo}:{hi} was stranded on the fitted axis')
        self.assertFalse(mask(np.array([.245, .335, .865, .955]), intervals).any(),
                         'A connection is not observed support inside a missing scan gap')
        self.assertGreater(evidence['linkedFragmentSections'], 0)

    def test_gap_limit_and_identity_barrier_stop_connections(self):
        along, radial, normals = tube([(.12, .17), (.30, .34), (.40, .70), (.76, .80)])
        surface = np.ones(len(along), bool)
        for allowed, blocked in ((lambda s: True, along < .20),
                                 (lambda s: not .71 < s < .75, along > .75)):
            intervals, _, _ = _grow_axis_support(
                along, radial, normals, surface, np.arange(.42, .69, .012), 1.2, .004, allowed)
            self.assertFalse(mask(along[blocked], intervals).any())

    def test_plane_parallel_surface_and_invalid_normals_do_not_become_fragments(self):
        along, radial, normals = tube([(.36, .72), (.77, .82)])
        fragment = along > .76
        seeds = np.arange(.372, .71, .012)
        for kind in ('plane', 'parallel', 'normal'):
            rr, nn = radial.copy(), normals.copy()
            if kind == 'plane':
                rr[fragment] = [.004, 0]
                nn[fragment] = [1, 0]
            elif kind == 'parallel':
                rr[fragment] += [.035, 0]
            else:
                nn[fragment] = 0
            distance = np.linalg.norm(rr, axis=1)
            alignment = abs(np.sum(rr*nn, axis=1))/np.maximum(distance*np.linalg.norm(nn, axis=1), 1e-9)
            surface = (abs(distance-.004) <= .0014) & (alignment > .65)
            intervals, _, _ = _grow_axis_support(along, rr, nn, surface, seeds, 1.2, .004, lambda s: True)
            self.assertFalse(mask(along[fragment], intervals).any(), kind)

    def test_no_anchor_cannot_claim_a_free_standing_fragment(self):
        along, radial, normals = tube([(.36, .72)])
        intervals, _, _ = _grow_axis_support(along, radial, normals, np.ones(len(along), bool),
                                             np.empty(0), 1.2, .004, lambda s: True)
        self.assertEqual(intervals, [])

    def test_partial_view_fragment_requires_two_reliable_ends_and_correlated_normals(self):
        core_s, core_r, core_n = tube([(.36, .456), (.528, .72)])
        ss = np.r_[np.arange(.48, .504, .001), np.arange(.77, .81, .001)]
        angles = np.tile(np.linspace(-.8, .8, 24), len(ss))
        radial = .004*np.c_[np.cos(angles), np.sin(angles)]
        normals = np.c_[np.cos(.35*angles), np.sin(.35*angles)]
        along = np.r_[core_s, np.repeat(ss, 24)]
        rr, nn = np.vstack((core_r, radial)), np.vstack((core_n, normals))
        seeds = np.r_[np.arange(.372, .456, .012), np.arange(.54, .71, .012)]
        intervals, _, evidence = _grow_axis_support(along, rr, nn, np.ones(len(along), bool),
                                                   seeds, 1.2, .004, lambda s: True)
        self.assertGreater(np.mean(mask(along[(along >= .48) & (along < .504)], intervals)), .95)
        self.assertFalse(mask(along[along > .76], intervals).any(),
                         'Weaker evidence may not grow beyond the reliable outer anchor')
        self.assertGreater(evidence['bridgedFragmentSections'], 0)
        # Noisy plane normals can have the same spread, but do not follow the
        # observed radial variation; proximity and two endpoints are insufficient.
        rng = np.random.default_rng(28)
        idx = np.arange(len(core_s), len(along))
        rr[idx, 0] = .004
        random_angles = rng.normal(0, .18, len(idx))
        nn[idx] = np.c_[np.cos(random_angles), np.sin(random_angles)]
        distance = np.linalg.norm(rr, axis=1)
        surface = (abs(distance-.004) <= .0014) & (abs(np.sum(rr*nn, axis=1))/distance > .65)
        intervals, _, _ = _grow_axis_support(along, rr, nn, surface, seeds, 1.2, .004, lambda s: True)
        self.assertFalse(mask(along[(along >= .48) & (along < .504)], intervals).any())

    def test_thin_isolated_ring_is_not_a_linkable_axial_fragment(self):
        along, radial, normals = tube([(.36, .72), (.77, .771)])
        intervals, _, _ = _grow_axis_support(along, radial, normals, np.ones(len(along), bool),
                                             np.arange(.372, .71, .012), 1.2, .004, lambda s: True)
        self.assertFalse(mask(along[along > .76], intervals).any())

    def test_source_order_and_normal_sign_do_not_change_connections(self):
        along, radial, normals = tube([(.27, .31), (.36, .72), (.77, .81)])
        args = (np.arange(.372, .71, .012), 1.2, .004, lambda s: True)
        expected = _grow_axis_support(along, radial, normals, np.ones(len(along), bool), *args)
        order = np.random.default_rng(8).permutation(len(along))
        actual = _grow_axis_support(along[order], radial[order], -normals[order], np.ones(len(along), bool), *args)
        self.assertEqual(actual[0], expected[0])
        np.testing.assert_array_equal(actual[1], expected[1])

    def test_actual_recovery_links_detector_missed_fragments_in_rotated_coordinates(self):
        points, normals = data(bend=0)
        spans = [(.18, .22), (.27, .31), (.36, .84), (.89, .93), (.98, 1.02)]
        selected = mask(points[:, 0], spans)
        points, normals = points[selected], normals[selected]
        along = points[:, 0].copy()
        fragments = (along < .32) | (along > .88)
        excluded = (along < .22) & (np.arange(len(points)) % 5 == 0)
        protected = (along > .98) & (np.arange(len(points)) % 5 == 0)
        rotation = Rotation.from_rotvec([.4, -.6, .2]).as_matrix()
        translation = np.array([4., -2., 1.])
        points = points@rotation.T+translation
        normals = normals@rotation.T
        before = points.copy()
        inv = inventory([0])
        for key in ('startM', 'endM'):
            inv['units'][0][key] = (rotation@inv['units'][0][key]+translation).tolist()
        owner = np.where(protected, 2, 0).astype(np.uint32)
        status = np.where(protected, 1, 2).astype(np.uint8)
        rows = [dict(id=1, designUnitId='0', designBarId='0', status='missing', pointCount=0)]

        def central_sections(task):
            if task[1] < .36 or task[1] > .84:
                return task[0], task[1], []
            return _section_task(task)

        with threadpool_limits(limits=1), patch('algorithms.bim_bent_bars._section_task', side_effect=central_sections):
            recover_bent_bars(points, normals, inv, rows, status, owner, {'objects': []}, {'objects': []},
                              excluded=excluded)
        self.assertGreater(np.mean(owner[fragments & ~excluded & ~protected] == 1), .95)
        self.assertFalse(owner[excluded].any())
        self.assertTrue(np.all(owner[protected] == 2))
        self.assertFalse(mask(np.array([.245, .335, .865, .955]), rows[0]['observedSupportIntervalsM']).any())
        np.testing.assert_array_equal(points, before)


if __name__ == '__main__':
    unittest.main()
