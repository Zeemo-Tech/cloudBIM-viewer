import unittest
from types import SimpleNamespace
import numpy as np
from algorithms.rebar_fragment_terminals import review_fragment_terminals


def cylinder(a, b, radius=.003, rings=36):
    return np.array([[x, radius*np.cos(q), radius*np.sin(q)] for x in np.linspace(a, b, rings)
                     for q in np.linspace(0, 2*np.pi, 12, endpoint=False)])


def plate(x):
    return np.array([[x, y, z] for y in np.linspace(-.008, .008, 7) for z in np.linspace(-.008, .008, 7)])


def scene(with_witnesses=True):
    # Pre-merge groups contain only class-3 retained rows.  Class-2 fixture
    # witnesses are separate source rows, never an input group member.
    left_clean, right_clean = cylinder(0, .42), cylinder(.58, 1)
    left, right = np.vstack((left_clean, plate(.44))), np.vstack((right_clean, plate(.56)))
    witnesses = np.vstack((plate(.443), plate(.557))) if with_witnesses else np.empty((0, 3))
    points = np.vstack((left, right, witnesses))
    refined = np.r_[np.full(len(left)+len(right), 3, np.uint8), np.full(len(witnesses), 2, np.uint8)]
    groups = [dict(rows=np.arange(len(left), dtype=np.int64), origin='internal', sourceId=7, kind='straight', diameterM=.006),
              dict(rows=np.arange(len(left), len(left)+len(right), dtype=np.int64), origin='exterior', sourceId=8, kind='short', diameterM=.006)]
    return SimpleNamespace(positions=points, refined_class=refined), groups, len(left_clean), len(left), len(right_clean), len(right)


class FragmentTerminalTests(unittest.TestCase):
    def test_separate_groups_peel_both_gap_faces_without_design_clipping(self):
        ctx, groups, left_clean, left_n, right_clean, right_n = scene()
        removed, labels, report = review_fragment_terminals(ctx, groups, {'units': []})
        self.assertGreaterEqual(report['removedPointCount'], 60)
        self.assertTrue(np.all(~removed[:left_clean]))
        self.assertTrue(np.all(~removed[left_n:left_n+right_clean]))
        self.assertTrue(np.all(labels[:left_n] > 0))
        self.assertTrue(np.all(labels[left_n:left_n+right_n] > 0))
        self.assertEqual({x['origin'] for x in report['fragments']}, {'internal', 'exterior'})

    def test_clean_groups_are_labeled_without_fixture_witnesses(self):
        ctx, groups, _, left_n, _, right_n = scene(False)
        removed, labels, report = review_fragment_terminals(ctx, groups, {'units': []})
        self.assertFalse(np.any(removed))
        self.assertTrue(np.all(labels[:left_n+right_n] > 0))
        self.assertEqual(report['removedPointCount'], 0)

    def test_sparse_web_and_curved_groups_are_skipped(self):
        ctx, groups, *_ = scene(); groups[0]['kind'] = 'web'; groups[1]['kind'] = 'curved'
        removed, labels, report = review_fragment_terminals(ctx, groups, {'units': []})
        self.assertFalse(np.any(removed)); self.assertFalse(np.any(labels))
        self.assertEqual(report['sources'][0]['skippedReason'], 'web_or_curved_or_sparse')

    def test_unmatched_external_group_uses_spatial_design_radius(self):
        ctx,groups,left_clean,left_n,right_clean,_=scene()
        groups[1].pop('diameterM');groups[1].pop('kind')
        inv={'units':[dict(designUnitId='design-a',startM=[0,0,0],endM=[1,0,0],diameterM=.006,kind='straight')]}
        removed,_,report=review_fragment_terminals(ctx,groups,inv)
        self.assertGreater(sum(d['pointCount'] for d in report['decisions'] if d['origin']=='exterior'),0)
        self.assertFalse(np.any(removed[left_n:left_n+right_clean]))
        self.assertFalse(np.any(removed[ctx.refined_class==2]))
    def test_one_sided_observation_remains_a_cylinder(self):
        ctx,groups,left_clean,left_n,right_clean,_=scene()
        for group in groups:
            rows=group['rows'];group['rows']=rows[ctx.positions[rows,2]>=-1e-9]
        removed,_,_=review_fragment_terminals(ctx,groups,{'units':[]})
        self.assertFalse(np.any(removed[:left_clean]));self.assertFalse(np.any(removed[left_n:left_n+right_clean]))
    def test_density_gaps_do_not_split_sparse_sampling_rings(self):
        from algorithms.rebar_fragment_terminals import _fragments
        p=cylinder(0,.5,rings=26)
        _,_,parts,cuts,_=_fragments(p)
        self.assertEqual(len(cuts),0);self.assertEqual(len(np.unique(parts)),1)
    def test_permutation_is_deterministic_in_source_coordinates(self):
        ctx, groups, *_ = scene(); first, _, _ = review_fragment_terminals(ctx, groups, {'units': []})
        order = np.random.default_rng(22).permutation(len(ctx.positions)); inverse = np.empty_like(order); inverse[order] = np.arange(len(order))
        shuffled = SimpleNamespace(positions=ctx.positions[order], refined_class=ctx.refined_class[order])
        new_groups = [dict(g, rows=inverse[g['rows']]) for g in groups]
        second, _, _ = review_fragment_terminals(shuffled, new_groups, {'units': []})
        self.assertTrue(np.array_equal(first, second[inverse]))


if __name__ == '__main__': unittest.main()
