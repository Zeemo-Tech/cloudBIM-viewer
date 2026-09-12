import unittest
from types import SimpleNamespace
import numpy as np
from scipy.spatial import cKDTree

from algorithms.rebar_terminal_cleanup import clean_terminals


def cylinder(a=0., b=1., radius=.003, n=48):
    t = np.linspace(a, b, n); angle = np.linspace(0, 2*np.pi, 10, endpoint=False)
    return np.array([[x, radius*np.cos(q), radius*np.sin(q)] for x in t for q in angle])


def case(tip=False, second=False, diameter=.006):
    rod = cylinder(); fixture = np.empty((0, 3))
    if tip:
        fixture = np.array([[1.012, y, z] for y in np.linspace(-.006, .006, 6) for z in np.linspace(-.006, .006, 6)])
        rod = np.vstack((rod, fixture))
    if second: rod = np.vstack((rod, cylinder(.96, 1.04, .003)))
    n = len(rod); classes = np.full(n, 3, np.uint8); owners = np.ones(n, np.uint32)
    if second: owners[-len(cylinder(.96,1.04,.003)):] = 2
    refined = np.r_[np.full(n-len(fixture), 3, np.uint8), np.full(len(fixture), 2, np.uint8)]
    # Fixture rows also appear as retained glue in complete rows, as happens after Step 06.
    ctx = SimpleNamespace(positions=rod, tree=cKDTree(rod), refined_class=refined, complete_class=classes,
        complete_instance=owners, complete_segment=owners.copy(), complete_confidence=np.ones(n, np.float32))
    inv = {'units':[{'designUnitId':'u1','diameterM':diameter}], 'bars':[]}
    report = {'instances':[{'id':1,'designUnitId':'u1','pointCount':int(np.sum(owners==1))}], 'segments':[{'id':1,'pointCount':int(np.sum(owners==1))}]}
    return ctx, report, inv, fixture


class TerminalCleanupTests(unittest.TestCase):
    def test_clean_six_and_eight_mm_rods_unchanged(self):
        for diameter in (.006, .008):
            ctx, report, inv, _ = case(diameter=diameter)
            result = clean_terminals(ctx, report, inv)['terminalCleanup']
            self.assertEqual(result['removedPointCount'], 0)
            self.assertTrue(np.all(ctx.complete_class == 3))

    def test_fixture_tip_is_removed_and_bookkept(self):
        ctx, report, inv, fixture = case(tip=True)
        report.update(instanceCount=1, rebarPoints=len(ctx.positions),
                      counts={'table': 0, 'fixture': 0, 'rebar': len(ctx.positions), 'noise': 0},
                      designReview={'filteredPoints': 7})
        report['instances'][0]['rebarPoints'] = len(ctx.positions)
        report['segments'][0]['rebarPoints'] = len(ctx.positions)
        output = {}; rebuilt = clean_terminals(ctx, report, inv, output=output); result = rebuilt['terminalCleanup']
        self.assertGreaterEqual(result['removedPointCount'], 20)
        removed = output['terminal_removed'].astype(bool)
        self.assertTrue(np.all(output['terminal_previous_instance'][removed] == 1))
        self.assertTrue(np.all(ctx.complete_instance[removed] == 0))
        self.assertEqual(rebuilt['instances'][0]['pointCount'], len(ctx.positions)-result['removedPointCount'])
        self.assertEqual(rebuilt['instanceCount'], 1)
        self.assertEqual(rebuilt['rebarPoints'], rebuilt['counts']['rebar'])
        self.assertEqual(rebuilt['designReview']['filteredPoints'], 7+result['removedPointCount'])

    def test_sparse_and_half_length_rods_are_preserved(self):
        ctx, report, inv, _ = case(); ctx.positions = ctx.positions[:60]; ctx.tree = cKDTree(ctx.positions)
        for name in ('refined_class','complete_class','complete_instance','complete_segment','complete_confidence'): setattr(ctx, name, getattr(ctx,name)[:60])
        result = clean_terminals(ctx, report, inv)['terminalCleanup']
        self.assertEqual(result['removedPointCount'], 0)

    def test_midbody_is_immutable_and_crossing_is_protected(self):
        ctx, report, inv, fixture = case(tip=True, second=True)
        result = clean_terminals(ctx, report, inv)['terminalCleanup']
        self.assertEqual(result['removedPointCount'], 0)
        self.assertTrue(np.all(ctx.complete_class[:100] == 3))

    def test_curved_exterior_hook_is_not_polished(self):
        ctx, report, inv, fixture = case(tip=True)
        ctx.complete_cluster = np.full(len(ctx.positions), 9, np.uint32)
        report['clusters'] = [{'id': 9, 'category': 'curved-exterior'}]
        output = {}
        result = clean_terminals(ctx, report, inv, output=output)['terminalCleanup']
        self.assertTrue(np.all(ctx.complete_class[:-len(fixture)] == 3))
        self.assertTrue(np.all(output['terminal_removed'][:-len(fixture)] == 0))

    def test_polyline_ordinary_bar_is_eligible_without_measured_curved_cluster(self):
        ctx, report, inv, fixture = case(tip=True)
        report['instances'][0]['designBarId'] = 'ordinary-polyline'
        inv['bars'] = [{'designBarId': 'ordinary-polyline', 'points': [[0,0,0], [.5,0,0], [1,0,0]]}]
        result = clean_terminals(ctx, report, inv)['terminalCleanup']
        self.assertGreater(result['removedPointCount'], 0)

    def test_web_unit_is_skipped(self):
        ctx, report, inv, fixture = case(tip=True)
        inv['units'][0]['kind'] = 'web'
        result = clean_terminals(ctx, report, inv)['terminalCleanup']
        self.assertEqual(result['removedPointCount'], 0)

    def test_mixed_straight_and_curved_owner_keeps_only_curved_rows_locked(self):
        ctx, report, inv, fixture = case(tip=True)
        ctx.complete_cluster = np.zeros(len(ctx.positions), np.uint32)
        ctx.complete_cluster[-10:] = 9
        report['clusters'] = [{'id': 9, 'category': 'curved-exterior'}]
        result = clean_terminals(ctx, report, inv)['terminalCleanup']
        self.assertGreater(result['removedPointCount'], 0)
        self.assertTrue(np.all(ctx.complete_class[-10:] == 3))

    def test_round_endcap_inside_radius_is_preserved(self):
        ctx, report, inv, _ = case()
        angles = np.linspace(0, 2*np.pi, 16, endpoint=False)
        cap = np.c_[np.full(len(angles), 1.012), .002*np.cos(angles), .002*np.sin(angles)]
        for name, values in [('positions', cap), ('refined_class', np.full(len(cap), 2, np.uint8)),
                             ('complete_class', np.full(len(cap), 3, np.uint8)),
                             ('complete_instance', np.ones(len(cap), np.uint32)),
                             ('complete_segment', np.ones(len(cap), np.uint32)),
                             ('complete_confidence', np.ones(len(cap), np.float32))]:
            setattr(ctx, name, np.concatenate((getattr(ctx, name), values), axis=0))
        ctx.tree = cKDTree(ctx.positions)
        result = clean_terminals(ctx, report, inv)['terminalCleanup']
        self.assertEqual(result['removedPointCount'], 0)
        self.assertTrue(np.all(ctx.complete_class[-len(cap):] == 3))

    def test_gently_bent_terminal_uses_local_collar_not_global_axis(self):
        ctx, report, inv, fixture = case(tip=True)
        # A 12 mm gradual bow puts the last 40 mm noticeably off a global PCA
        # cylinder, while its adjacent collar still has a coherent local fit.
        x = ctx.positions[:, 0]
        ctx.positions[:, 1] += .012*x*x
        ctx.tree = cKDTree(ctx.positions)
        output = {}
        clean_terminals(ctx, report, inv, output=output)
        self.assertTrue(np.all(ctx.complete_class[:-len(fixture)] == 3))
        self.assertTrue(np.all(output['terminal_removed'][:-len(fixture)] == 0))

    def test_design_unsupported_instance_bypasses(self):
        ctx, report, inv, fixture = case(tip=True); report['instances'][0]['designUnitId'] = 'missing'
        result = clean_terminals(ctx, report, inv)['terminalCleanup']
        self.assertEqual(result['removedPointCount'], 0)


if __name__ == '__main__': unittest.main()
