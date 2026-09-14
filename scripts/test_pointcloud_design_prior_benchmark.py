"""Acceptance metrics must not turn design counts or missing truth into success."""
from copy import deepcopy
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

SCRIPT = Path(__file__).with_name('pointcloud-design-prior-benchmark.py')
spec = importlib.util.spec_from_file_location('design_prior_benchmark', SCRIPT)
benchmark = importlib.util.module_from_spec(spec)
spec.loader.exec_module(benchmark)


def example():
    report = {
        'snapshotFingerprint': 'fixed-model-and-alignment',
        'inventory': {'units': [{'kind': 'short', 'designUnitId': 'short-unit',
                                 'startM': [0, 0, .02], 'endM': [.28, 0, .02]}]},
        'components': [],
    }
    for i in range(3):
        report['components'].append({
            'id': i+1, 'pointCount': 200, 'centerM': [i*.1, 0, .02],
            'kind': 'internal', 'family': 3 if i==0 else 1,
            'baselineClass': 3, 'originalInstanceId': i+1,
            'status': 1 if i<2 else 3, 'instanceId': i+1 if i<2 else 0,
        })
    annotations = benchmark.make_review_set(report, 'source-sha')
    annotations['reviewedBy'] = 'test-fixture'
    for row in annotations['regions']: row['reviewed'] = True
    for i,row in enumerate(annotations['labels']):
        row.update(label='steel' if i<2 else 'noise', category='short' if i==0 else 'other',
                   truthInstanceId=f'observed-{i+1}' if i<2 else None)
    return report, annotations


class ReviewTests(unittest.TestCase):
    def test_noise_improvement_with_full_truth_can_pass(self):
        report, annotations = example()
        result = benchmark.evaluate_review(report, annotations)
        self.assertEqual(result['status'], 'passed')
        self.assertEqual(result['scores']['baseline']['noiseRetention'], 1)
        self.assertEqual(result['scores']['result']['noiseRetention'], 0)

    def test_short_loss_or_false_merge_cannot_pass(self):
        report, annotations = example()
        report['components'][0]['status'] = 3
        self.assertEqual(benchmark.evaluate_review(report, annotations)['status'], 'failed')
        report, annotations = example()
        report['components'][1]['instanceId'] = 1
        result = benchmark.evaluate_review(report, annotations)
        self.assertEqual(result['status'], 'failed')
        self.assertEqual(result['scores']['result']['falseMerges'], 1)

    def test_unlabelled_unreviewed_or_missing_instance_truth_stays_unverified(self):
        for omission in ('reviewer', 'region', 'short', 'instance', 'labels'):
            report, annotations = example()
            if omission=='reviewer': annotations['reviewedBy'] = None
            if omission=='region': annotations['regions'][0]['reviewed'] = False
            if omission=='short': annotations['labels'][0]['category'] = 'unknown'
            if omission=='instance': annotations['labels'][1]['truthInstanceId'] = None
            if omission=='labels':
                for row in annotations['labels']: row['label'] = 'unknown'
            with self.subTest(omission=omission):
                self.assertEqual(benchmark.evaluate_review(report, annotations)['status'], 'unverified')

    def test_zero_instance_ids_are_distinct_unassigned_fragments(self):
        report, annotations = example()
        for c in report['components'][:2]:
            c['originalInstanceId'] = 0
            c['instanceId'] = 7
        for row in annotations['labels'][:2]: row['truthInstanceId'] = 'one-observed-bar'
        result = benchmark.evaluate_review(report, annotations)
        self.assertEqual(result['scores']['baseline']['extraFragments'], 1)
        self.assertEqual(result['scores']['result']['extraFragments'], 0)
        self.assertEqual(result['scores']['baseline']['falseMerges'], 0)

    def test_pending_is_reported_separately_and_windows_are_mode_independent(self):
        report, annotations = example()
        before = benchmark.make_review_set(report, 'source-sha')
        for c in report['components']: c['status'] = 2
        after = benchmark.make_review_set(report, 'source-sha')
        self.assertEqual(before, after)
        result = benchmark.evaluate_review(report, annotations)
        self.assertEqual(result['status'], 'failed')
        self.assertEqual(result['pendingPointsByManualLabel'], {'steel': 400, 'noise': 200, 'unknown': 0})
        self.assertLessEqual(before['regions'][0]['minM'][1], -.2)

    def test_stale_duplicate_or_invalid_labels_are_rejected(self):
        for corruption in ('snapshot', 'component', 'duplicate', 'label'):
            report, annotations = example()
            if corruption=='snapshot': report['snapshotFingerprint'] = 'different'
            if corruption=='component': report['components'][0]['pointCount'] += 1
            if corruption=='duplicate': annotations['labels'].append(deepcopy(annotations['labels'][0]))
            if corruption=='label': annotations['labels'][0]['label'] = 'design-count-correct'
            with self.subTest(corruption=corruption), self.assertRaises(ValueError):
                benchmark.evaluate_review(report, annotations)

    def test_evaluate_only_uses_existing_files_without_running_pipeline(self):
        report, annotations = example()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            result = {'sourceSha256': 'source-sha', 'qualityGate': {}, 'productionPromoted': False,
                      'runs': [{'mode': mode, 'runId': mode} for mode in ('geometry','topology')]}
            for mode in ('geometry','topology'):
                (root/mode).mkdir()
                (root/mode/'design-prior.json').write_text(json.dumps(report))
            (root/'benchmark.json').write_text(json.dumps(result))
            (root/'annotations.json').write_text(json.dumps(annotations))
            args = [str(SCRIPT), '--evaluate-only', '--config', str(root/'unused-config.json'),
                    '--output', str(root), '--report', str(root/'benchmark.json'),
                    '--annotations', str(root/'annotations.json')]
            with patch.object(sys, 'argv', args), patch.object(benchmark.subprocess, 'run') as run:
                benchmark.main()
                run.assert_not_called()
            saved = json.loads((root/'benchmark.json').read_text())
            self.assertEqual(saved['qualityGate']['topology']['status'], 'passed')
            self.assertFalse(saved['productionPromoted'])
            annotations['sourceSha256'] = 'wrong-source'
            (root/'annotations.json').write_text(json.dumps(annotations))
            with patch.object(sys, 'argv', args), self.assertRaises(ValueError):
                benchmark.main()


if __name__=='__main__': unittest.main()
