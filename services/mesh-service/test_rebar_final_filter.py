import unittest
from types import SimpleNamespace
import numpy as np

from algorithms.rebar_final_filter import filter_final_clusters
from algorithms.design_guided_instances import refine_instances
from test_design_guided_instances import scene, inventory


class FinalFilterTests(unittest.TestCase):
    def case(self, short_points=40, short_length=.1, protected=False, merged=False):
        main = np.c_[np.linspace(0, 1, 1000), np.zeros(1000), np.zeros(1000)]
        short = np.c_[np.linspace(.3, .3+short_length, short_points), np.full(short_points, .02), np.zeros(short_points)]
        ctx = SimpleNamespace(positions=np.vstack([main, short]), fused_steel_score=np.ones(1000+short_points))
        out = dict(complete_class=np.full(1000+short_points, 3, np.uint8),
                   complete_instance=np.r_[np.ones(1000), np.full(short_points, 1 if merged else 2)].astype(np.uint32),
                   complete_segment=np.r_[np.ones(1000), np.full(short_points, 2)].astype(np.uint32),
                   complete_confidence=np.ones(1000+short_points), complete_cluster=np.zeros(1000+short_points, np.uint32))
        locked = np.r_[np.zeros(1000, bool), np.full(short_points, protected)]
        clusters = []
        r = filter_final_clusters(ctx, out, inventory([([0,0,0],[1,0,0])]), {1:{0}}, locked, clusters)
        return ctx, out, r, clusters

    def test_high_score_short_sparse_extra_is_removed_whole_with_evidence(self):
        ctx, out, r, clusters = self.case()
        np.testing.assert_array_equal(out['complete_class'][:1000], 3)
        np.testing.assert_array_equal(out['complete_class'][1000:], 4)
        np.testing.assert_array_equal(out['complete_instance'][1000:], 0)
        self.assertEqual(r['removedInstanceCount'], 1)
        self.assertEqual(r['highScoreRemovedPointCount'], 40)
        d = r['decisions'][0]
        self.assertAlmostEqual(d['lengthRatio'], .1)
        self.assertAlmostEqual(d['pointCountRatio'], .04)
        self.assertEqual(d['referenceInstanceIds'], [1])
        np.testing.assert_array_equal(ctx.fused_steel_score, 1)

    def test_length_alone_or_point_count_alone_cannot_reject(self):
        for kwargs in ({'short_points':600}, {'short_length':.8}):
            _, out, r, _ = self.case(**kwargs)
            self.assertEqual(r['removedPointCount'], 0)
            np.testing.assert_array_equal(out['complete_class'], 3)

    def test_merged_outer_fragment_is_evaluated_with_whole_inner_bar(self):
        _, out, r, _ = self.case(merged=True)
        self.assertEqual(r['removedPointCount'], 0)
        np.testing.assert_array_equal(out['complete_instance'], 1)

    def test_locked_hook_cannot_be_filtered_even_if_both_statistics_fail(self):
        _, out, r, _ = self.case(protected=True)
        self.assertEqual(r['removedPointCount'], 0)
        np.testing.assert_array_equal(out['complete_class'], 3)

    def test_pipeline_does_all_merges_before_any_filter(self):
        rods=[([0,0,0],[.4,0,0]),([.5,0,0],[.8,0,0]),([.3,.02,0],[.34,.02,0])]
        ctx, report, sizes = scene(rods, ids=[1,0,0], zones=[1,3,3])
        # Thin the extra observation while preserving original source alignment.
        keep=np.r_[np.arange(sum(sizes[:2])), np.arange(sum(sizes[:2]),sum(sizes),60)]
        for name, value in vars(ctx).items():
            setattr(ctx,name,value[keep])
        ctx.fused_steel_score=np.ones(len(keep),np.float32)
        r=refine_instances(ctx,report,inventory([([0,0,0],[1,0,0])]))
        ops=r['designReview']['operations']
        connections=[i for i,o in enumerate(ops) if o['action'] in ('merge','attach')]
        filters=[i for i,o in enumerate(ops) if o['action']=='filter']
        self.assertTrue(connections)
        self.assertTrue(filters)
        self.assertLess(max(connections),min(filters))
        np.testing.assert_array_equal(ctx.complete_class[:sum(sizes[:2])],3)
        np.testing.assert_array_equal(ctx.complete_instance[:sum(sizes[:2])],1)


if __name__ == '__main__':
    unittest.main()
