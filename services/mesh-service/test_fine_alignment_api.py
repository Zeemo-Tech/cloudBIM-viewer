"""Fine alignment routing, storage boundaries, and hard-rejection behavior."""
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from pydantic import ValidationError

import main


IDENTITY = [1., 0., 0., 0., 0., 1., 0., 0., 0., 0., 1., 0., 0., 0., 0., 1.]


class FineAlignmentAPITests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.environment = patch.dict(os.environ, {'ANALYSIS_MESH_STORAGE_ROOT': str(self.root)})
        self.environment.start()
        self.addCleanup(self.environment.stop)
        self.denoise = self.root / 'denoise' / 'version-1'
        self.denoise.mkdir(parents=True)
        for name in ('cleaned.las', 'instance-map.json', 'control-net.json'):
            (self.denoise / name).write_bytes(b'fixture')
        self.scan, self.mesh = self.root / 'scan.las', self.root / 'mesh.ply'
        self.scan.write_bytes(b'fixture')
        self.mesh.write_bytes(b'fixture')

    def request(self, **kwargs):
        return main.FineAlignRequest(init_transform=IDENTITY, **kwargs)

    def assert_error(self, response, text=None):
        self.assertEqual(response.status_code, 400)
        if text:
            self.assertIn(text, json.loads(response.body)['msg'])

    def test_auto_and_explicit_rebar_use_classified_directory_without_mesh(self):
        result = {'method': 'rebar-weighted-rigid-v1', 'diagnostics': {'heldOutBars': 8}}
        for method in ('auto', 'rebar'):
            with self.subTest(method=method), patch('algorithms.rebar_registration.fine_rebar_registration', return_value=result) as rebar, patch('algorithms.icp_registration.fine_registration') as icp:
                response = main.align_fine(self.request(method=method, denoise_dir=str(self.denoise)))
                self.assertIs(response, result)
                self.assertEqual(rebar.call_args.kwargs, {
                    'denoise_dir': str(self.denoise), 'init_matrix_16': IDENTITY,
                    'rmse_regress_ratio': 1.05, 'fitness_regress_ratio': .95,
                    'apply_when_regressed': False,
                })
                icp.assert_not_called()

    def test_legacy_auto_and_explicit_icp_keep_original_inputs(self):
        for method in ('auto', 'icp'):
            with self.subTest(method=method), patch('algorithms.rebar_registration.fine_rebar_registration') as rebar, patch('algorithms.icp_registration.fine_registration', return_value={'metrics': {'futureMetric': 7}}) as icp:
                response = main.align_fine(self.request(method=method, scan_path=str(self.scan), mesh_path=str(self.mesh)))
                self.assertEqual(response['method'], 'point-to-plane-icp')
                self.assertEqual(response['metrics']['futureMetric'], 7)
                self.assertEqual(response['diagnostics'], {})
                self.assertEqual(icp.call_args.kwargs['scan_path'], str(self.scan))
                self.assertEqual(icp.call_args.kwargs['remesh_ply_path'], str(self.mesh))
                rebar.assert_not_called()

    def test_rebar_missing_classification_never_falls_back_to_icp(self):
        with patch('algorithms.icp_registration.fine_registration') as icp:
            self.assert_error(main.align_fine(self.request(method='rebar', scan_path=str(self.scan), mesh_path=str(self.mesh))), 'denoise_dir')
            (self.denoise / 'instance-map.json').unlink()
            self.assert_error(main.align_fine(self.request(denoise_dir=str(self.denoise), scan_path=str(self.scan), mesh_path=str(self.mesh))))
            icp.assert_not_called()

    def test_invalid_or_stale_geometry_is_not_overridden(self):
        with patch('algorithms.rebar_registration.fine_rebar_registration', side_effect=ValueError('control net alignment snapshot is stale')) as rebar, patch('algorithms.icp_registration.fine_registration') as icp:
            response = main.align_fine(self.request(method='rebar', denoise_dir=str(self.denoise), apply_when_regressed=True))
            self.assert_error(response, 'snapshot is stale')
            rebar.assert_called_once()
            icp.assert_not_called()

    def test_path_and_symlink_escapes_are_rejected_before_solver(self):
        with tempfile.TemporaryDirectory() as outside, patch('algorithms.rebar_registration.fine_rebar_registration') as rebar:
            self.assert_error(main.align_fine(self.request(method='rebar', denoise_dir=outside)), 'shared storage')
            (self.denoise / 'cleaned.las').unlink()
            other = Path(outside) / 'private.las'
            other.write_bytes(b'private')
            (self.denoise / 'cleaned.las').symlink_to(other)
            self.assert_error(main.align_fine(self.request(method='rebar', denoise_dir=str(self.denoise))), 'shared storage')
            rebar.assert_not_called()

    def test_initial_transform_must_be_rigid_and_parameters_finite(self):
        bad_matrix = IDENTITY.copy()
        bad_matrix[0] = 2.
        with patch('algorithms.rebar_registration.fine_rebar_registration') as rebar:
            response = main.align_fine(main.FineAlignRequest(method='rebar', denoise_dir=str(self.denoise), init_transform=bad_matrix))
            self.assert_error(response, 'rigid')
            rebar.assert_not_called()
        for parameters in ({'method': 'unknown'}, {'rmse_regress_ratio': float('inf')}, {'fitness_regress_ratio': -1}):
            with self.subTest(parameters=parameters), self.assertRaises(ValidationError):
                self.request(**parameters)
        with self.assertRaises(ValidationError):
            main.FineAlignRequest(init_transform=[0.] * 15)


if __name__ == '__main__':
    unittest.main()
