"""Detached extension recovery and counterexamples that must remain steel."""
from dataclasses import replace
from types import SimpleNamespace
import unittest

import numpy as np

from algorithms.rebar_overlength_tails import (
    TailParameters, filter_overlength_tails, tail_decision, _length_evidence,
)
from algorithms.design_guided_instances import GuidedParameters, refine_instances
from test_design_guided_instances import inventory, scene


def case(tails=(.462, .498), core_end=.323):
    core = np.linspace(0, core_end, 324)
    tail = np.linspace(*tails, 37)
    along = np.r_[core, tail]
    core_mask = np.arange(len(along)) < len(core)
    return along, core_mask, ~core_mask, np.zeros(len(along), bool)


def fixture():
    along, core, eligible, protected = case()
    context = SimpleNamespace(positions=np.c_[along, np.zeros((len(along), 2))],
        refined_zone=np.where(core, 1, 3), internal_instance=core.astype(np.uint32),
        fused_steel_score=np.ones(len(along)))
    out = dict(complete_class=np.full(len(along), 3, np.uint8),
        complete_instance=np.ones(len(along), np.uint32),
        complete_segment=np.ones(len(along), np.uint32),
        complete_confidence=np.ones(len(along), np.float32))
    # A stale segment endpoint hides the satellite from endpoint-only checks.
    segments = [dict(id=1, instanceId=1, startM=[0, 0, 0], endM=[.323, 0, 0], radiusM=.0025)]
    summary = dict(strong=True, length=.323, interiorFraction=1., fixtureNearFraction=0.,
        axis=np.array([1., 0, 0]), center=np.array([.1615, 0, 0]), radius=.0025,
        coreFits=[dict(axis=np.array([1., 0, 0]))])
    atoms = [dict(instanceId=1, originalInstanceId=1, rows=np.flatnonzero(core), summary=summary)]
    units = inventory([([0, 0, 0], [.28, 0, 0])])['units']
    return context, out, segments, units, {1: {0}}, atoms, [[(.1, 0)]], eligible.astype(np.uint8)


class TailDecisionTests(unittest.TestCase):
    def decide(self, values=None, **kwargs):
        return tail_decision(*(values or case()), .28, .005, **kwargs)

    def test_recovers_whole_right_tail_and_preserves_core(self):
        removed, result = self.decide()
        np.testing.assert_array_equal(removed, case()[2])
        self.assertAlmostEqual(result['afterLengthM'], .323)
        self.assertAlmostEqual(result['tails'][0]['gapM'], .139)

    def test_left_and_both_tails_are_symmetric(self):
        along, core, eligible, protected = case()
        removed, _ = self.decide((-along, core, eligible, protected))
        np.testing.assert_array_equal(removed, eligible)
        along = np.r_[along, -.2, -.19]
        core = np.r_[core, False, False]
        removed, record = self.decide((along, core, ~core, np.zeros(len(along), bool)))
        np.testing.assert_array_equal(removed, ~core)
        self.assertEqual({t['side'] for t in record['tails']}, {'start', 'end'})

    def test_one_far_point_is_not_hidden_by_quantiles(self):
        t = np.r_[np.linspace(0, .323, 10000), .498]
        core = np.arange(len(t)) < len(t)-1
        removed, _ = self.decide((t, core, ~core, np.zeros(len(t), bool)))
        self.assertEqual(np.flatnonzero(removed).tolist(), [len(t)-1])

    def test_continuous_real_overlength_is_never_hard_clipped(self):
        removed, result = self.decide(case((.324, .498)))
        self.assertFalse(removed.any())
        self.assertEqual(result['reason'], 'continuous_overlength')

    def test_core_overlength_and_small_overrun_are_preserved(self):
        for values in (case(core_end=.40), case((.324, .337))):
            with self.subTest(values=values[0][-1]):
                removed, _ = self.decide(values)
                self.assertFalse(removed.any())

    def test_partial_core_does_not_assume_a_centered_design_interval(self):
        removed, _ = self.decide(case((.27, .32), core_end=.15))
        self.assertFalse(removed.any())

    def test_component_crossing_length_boundary_is_not_sliced(self):
        removed, _ = self.decide(case((.34, .50), core_end=.20))
        self.assertFalse(removed.any())

    def test_protected_hook_and_independently_observed_tail_are_preserved(self):
        along, core, eligible, protected = case()
        for allowed, protection in ((np.zeros(len(along), bool), protected), (eligible, eligible)):
            removed, _ = self.decide((along, core, allowed, protection))
            self.assertFalse(removed.any())

    def test_sparse_sampling_and_bin_budget_fail_closed(self):
        t = np.r_[np.linspace(0, .323, 12), .462, .498]
        core = np.arange(len(t)) < 12
        removed, _ = self.decide((t, core, ~core, np.zeros(len(t), bool)))
        self.assertFalse(removed.any())
        t, core, eligible, protected = case()
        t[-1] = 1e9
        removed, result = self.decide((t, core, eligible, protected))
        self.assertFalse(removed.any())
        self.assertEqual(result['reason'], 'bin_budget')

    def test_source_row_order_does_not_affect_decision(self):
        values = case()
        order = np.random.default_rng(5).permutation(len(values[0]))
        removed, _ = self.decide(tuple(v[order] for v in values))
        np.testing.assert_array_equal(removed, values[2][order])

    def test_point_and_component_budgets_preserve_input(self):
        removed, record = self.decide(params=replace(TailParameters(), maximum_instance_points=100))
        self.assertFalse(removed.any())
        self.assertEqual(record['reason'], 'point_budget')
        along, core, _, _ = case()
        along = np.r_[along, np.arange(1., 20., .1)]
        core = np.r_[core, np.zeros(len(along)-len(core), bool)]
        removed, record = self.decide((along, core, ~core, np.zeros(len(along), bool)))
        self.assertFalse(removed.any())
        self.assertEqual(record['reason'], 'block_budget')


class TailIntegrationTests(unittest.TestCase):
    def test_stale_segment_is_refreshed_and_filter_is_idempotent(self):
        args = fixture()
        context, out, segments = args[:3]
        xyz = context.positions.copy()
        operations, report, groups = filter_overlength_tails(*args)
        self.assertEqual(report['removedPointCount'], 37)
        self.assertEqual(report['newDesignQueryCount'], 0)
        self.assertEqual(report['highScoreRemovedPointCount'], 37)
        self.assertEqual(operations[0]['phase'], 'overlength_tail_recovery')
        np.testing.assert_array_equal(out['complete_class'][-37:], 4)
        for name in ('complete_instance', 'complete_segment', 'complete_confidence'):
            np.testing.assert_array_equal(out[name][-37:], 0)
        self.assertEqual(segments[0]['pointCount'], 324)
        self.assertAlmostEqual(segments[0]['endM'][0], .323)
        self.assertEqual(len(groups[1]), 324)
        np.testing.assert_array_equal(context.positions, xyz)
        self.assertEqual(filter_overlength_tails(*args)[1]['removedPointCount'], 0)

    def test_original_owner_and_hook_protection_override_extension_marker(self):
        for mode in ('owner', 'hook'):
            args = fixture()
            if mode == 'owner':
                args[0].internal_instance[-37:] = 2
            protected = np.zeros(len(args[0].positions), bool)
            if mode == 'hook':
                protected[-37:] = True
            self.assertEqual(filter_overlength_tails(*args, protected=protected)[1]['removedPointCount'], 0)

    def test_design_consensus_and_conflicting_assignment(self):
        args = fixture()
        units, atoms = args[3], args[5]
        units.append({**units[0], 'designUnitId': 'alternative', 'lengthM': .285})
        evidence, reason = _length_evidence([(0, atoms[0])], [[(.1, 0), (.15, 1)]], units, {0}, TailParameters())
        self.assertIsNone(reason)
        self.assertEqual(evidence['lengthSource'], 'length_consensus')
        self.assertEqual(evidence['designLengthM'], .285)
        evidence, reason = _length_evidence([(0, atoms[0])], [[(.1, 0), (.95, 1)]], units, {1}, TailParameters())
        self.assertIsNone(reason)
        self.assertEqual(evidence['lengthSource'], 'length_consensus')
        units[1]['lengthM'] = .5
        self.assertEqual(_length_evidence([(0, atoms[0])], [[(.1, 0), (.15, 1)]], units, {0}, TailParameters())[1], 'ambiguous_length')
        self.assertEqual(_length_evidence([(0, atoms[0])], [[(.1, 0), (1., 1)]], units, {1}, TailParameters())[1], 'ambiguous_length')
        self.assertEqual(_length_evidence([(0, atoms[0])], [[(.1, 0), (3., 1)]], units, {1}, TailParameters())[1], 'assignment_without_core_support')

    def test_missing_partial_and_weak_design_evidence_skip_without_search(self):
        for mode in ('missing', 'partial', 'weak'):
            args = list(fixture())
            if mode == 'missing':
                args[6] = [[]]
            elif mode == 'partial':
                args[3][0]['coverage'] = 'partial'
            else:
                args[6] = [[(2.5, 0)]]
            self.assertEqual(filter_overlength_tails(*args)[1]['removedPointCount'], 0)

    def test_rotation_and_large_coordinate_translation(self):
        args = fixture()
        angle = .71
        rotation = np.array([[np.cos(angle), -np.sin(angle), 0], [np.sin(angle), np.cos(angle), 0], [0, 0, 1.]])
        translation = np.array([1e6, -2e6, 30])
        args[0].positions = args[0].positions @ rotation.T+translation
        summary = args[5][0]['summary']
        summary['center'] = rotation @ summary['center']+translation
        summary['axis'] = rotation @ summary['axis']
        summary['coreFits'][0]['axis'] = summary['axis']
        args[3][0]['direction'] = summary['axis']
        for part in args[2]:
            for key in ('startM', 'endM'):
                part[key] = (rotation @ part[key]+translation).tolist()
        self.assertEqual(filter_overlength_tails(*args)[1]['removedPointCount'], 37)

    def test_curved_internal_core_does_not_use_straight_length_cut(self):
        args = fixture()
        args[5][0]['summary']['coreFits'].append(dict(axis=np.array([0., 1., 0.])))
        _, report, _ = filter_overlength_tails(*args)
        self.assertEqual(report['removedPointCount'], 0)
        self.assertEqual(report['skippedReasons'], {'curved_core': 1})

    def test_full_pipeline_recovers_only_late_extension_and_refreshes_reports(self):
        rods = [([0, 0, 0], [.323, 0, 0]), ([.462, 0, 0], [.498, 0, 0])]
        outputs = []
        for enabled in (False, True):
            ctx, internal, sizes = scene(rods, ids=[1, 0], zones=[1, 3])
            ctx.fused_steel_score = np.ones(len(ctx.positions), np.float32)
            result = refine_instances(ctx, internal, inventory([([0, 0, 0], [.28, 0, 0])]),
                params=replace(GuidedParameters(), overlength_tail_filter=enabled))
            outputs.append((ctx, result, sizes))
        before, after = outputs
        self.assertTrue(np.all(before[0].complete_instance == 1))
        n = after[2][0]
        np.testing.assert_array_equal(after[0].complete_class[:n], 3)
        np.testing.assert_array_equal(after[0].complete_class[n:], 4)
        report = after[1]
        self.assertEqual(report['designReview']['overlengthTailFilter']['removedPointCount'], after[2][1])
        self.assertEqual(report['instances'][0]['pointCount'], n)
        self.assertLess(report['instances'][0]['lengthM'], .342)
        self.assertLess(report['instances'][0]['shapeReview']['observedLengthM'], .342)
        self.assertEqual(sum(p['pointCount'] for p in report['segments']), n)
        self.assertEqual(report['unassignedRebarPointCount'], 0)


if __name__ == '__main__':
    unittest.main()
