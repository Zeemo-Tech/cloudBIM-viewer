"""Workbench model replacement must update every design consumer together."""
from copy import deepcopy
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import laspy
import numpy as np

from rebar_design_inputs import resolve_design_inputs
from rebar_design_prior import SCHEMA, digest_file, inventory_from_bars


def snapshot(source, digest, radii=(.003, .004)):
    bars = [dict(designBarId=str(i), points=[[0., i*.08, .04], [.3, i*.08, .04]],
                 radiusM=radius, coverage='complete') for i, radius in enumerate(radii)]
    return dict(schema=SCHEMA, sourcePath=str(source), sourceSha256=digest,
                fingerprint='model-' + str(radii), modelInfo={'name': str(radii)},
                inventory=inventory_from_bars(bars, np.eye(4).ravel().tolist()))


class DesignInputTests(unittest.TestCase):
    def test_folded_parent_counts_and_units_are_distinct_and_input_is_unchanged(self):
        value = snapshot('/tmp/test-scan.las', 'a'*64, (.003,))
        value['inventory'] = inventory_from_bars([
            dict(designBarId='folded', points=[[0., 0., .02], [.1, 0., .12], [.2, 0., .02]],
                 radiusM=.003, coverage='complete')], np.eye(4).ravel().tolist())
        original = deepcopy(value)
        inputs = resolve_design_inputs(value, '/tmp/test-scan.las', 'a'*64)
        self.assertEqual(inputs.dimensions['diametersM'], [.006])
        self.assertEqual(inputs.dimensions['counts']['physicalBarCount'], 1)
        self.assertEqual(inputs.dimensions['counts']['matchingUnitCount'], 2)
        self.assertEqual(inputs.dimensions['counts']['webStraightUnitCount'], 2)
        self.assertEqual(inputs.dimensions['horizontalLengthsM'], [])
        self.assertEqual(value, original)

    def test_source_binding_and_conflicting_model_dimensions_are_rejected(self):
        value = snapshot('/tmp/test-scan.las', 'a'*64)
        for source, digest in [('/tmp/other.las', 'a'*64), ('/tmp/test-scan.las', 'b'*64)]:
            with self.assertRaises(ValueError): resolve_design_inputs(value, source, digest)
        for field, bad in [('diameterM', -.008), ('diameterM', float('nan')),
                           ('diameterM', .010), ('lengthM', .5)]:
            changed = deepcopy(value); changed['inventory']['units'][0][field] = bad
            with self.subTest(field=field, value=bad), self.assertRaises(ValueError):
                resolve_design_inputs(changed, '/tmp/test-scan.las', 'a'*64)
        value['inventory']['coverage']['physicalBarCount'] = 500
        with self.assertRaises(ValueError): resolve_design_inputs(value, '/tmp/test-scan.las', 'a'*64)

    def test_unresolved_or_absent_design_is_explicitly_unavailable(self):
        value = snapshot('/tmp/test-scan.las', 'a'*64)
        value['inventory'] = inventory_from_bars([
            dict(designBarId='unknown', points=[], radiusM=None, coverage='unresolved')],
            np.eye(4).ravel().tolist())
        for item in (None, value):
            inputs = resolve_design_inputs(item, '/tmp/test-scan.las', 'a'*64)
            self.assertFalse(inputs.dimensions['available'])
            self.assertEqual(inputs.dimensions['diametersM'], [])

    def test_workbench_replacement_and_missing_design_never_load_old_association(self):
        from pointcloud_step_pipeline import run_from_source
        from test_normal_geometry_classifier import round_tube
        points, _ = round_tube(radius=.003)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); source = root/'scan.las'
            las = laspy.LasData(laspy.LasHeader(point_format=3, version='1.2'))
            las.header.scales = [.00001]*3
            las.x, las.y, las.z = points.T
            las.write(source); digest = digest_file(source)
            first = snapshot(source, digest, (.004,))
            replacement = snapshot(source, digest, (.003, .004))
            with patch('algorithms.pointcloud_segmentation.load_dimension_priors',
                       side_effect=AssertionError('workbench used stale source association')):
                for design, expected in [(first, [.008]), (replacement, [.006, .008]), (None, [])]:
                    with self.subTest(expected=expected):
                        run = run_from_source(source, root/'runs', workers=1, through_step=6,
                                              design_prior=design, preview_limit=30)
                        self.assertEqual(run.context.dimension_priors['diametersM'], expected)
                        report = run.manifest['designInputs']
                        self.assertEqual(report['enabled'], design is not None)
                        if design is not None:
                            self.assertEqual(report['design']['diametersM'], expected)
                            self.assertEqual(report['design']['counts']['matchingUnitCount'], len(expected))
                            self.assertEqual(run.context.dimension_priors['provenance']['snapshotFingerprint'], design['fingerprint'])
                            self.assertEqual(run.manifest['preprocessing']['floatingZones']['snapshotFingerprint'], design['fingerprint'])
                        else:
                            self.assertFalse(run.manifest['preprocessing']['floatingZones']['enabled'])
                        self.assertEqual(digest_file(source), digest)


if __name__ == '__main__':
    unittest.main()
