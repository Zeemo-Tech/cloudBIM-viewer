"""Only the rebar sweep profile is accepted at the remesh boundary."""
import asyncio
import inspect
from io import BytesIO
import json
from pathlib import Path
import unittest
from fastapi import UploadFile
import main
from test_rebar_sweep import tube_fixture


class RemeshAPITests(unittest.TestCase):
    def request(self, **parameters):
        defaults={name:param.default.default for name,param in inspect.signature(main.remesh).parameters.items() if name!='file'}
        defaults.update(parameters)
        return main.remesh(file=UploadFile(filename='source.ply',file=BytesIO(tube_fixture(False).export(file_type='ply'))),**defaults)

    def test_default_request_uses_rebar_sweep(self):
        response=self.request()
        try:
            self.assertEqual(response.status_code,200)
            self.assertEqual(int(response.headers['x-vertex-after']),22*16+2)
            self.assertTrue(Path(response.path).is_file())
        finally:
            if response.background:asyncio.run(response.background())

    def test_retired_algorithms_and_parameters_are_rejected(self):
        for algorithm in ('bim_preprocessor','bim_isotropic_only'):
            self.assertEqual(self.request(algorithm=algorithm).status_code,400)
        response=self.request(params_json='{"target_edge_length":0.1}')
        self.assertEqual(response.status_code,400)
        self.assertIn('target_edge_length',json.loads(response.body)['msg'])
        self.assertEqual(self.request(params_json='[]').status_code,400)

    def test_algorithm_catalog_has_only_the_supported_profile(self):
        self.assertEqual([row['name'] for row in main.list_algorithms()],['rebar_sweep'])


if __name__=='__main__':unittest.main()
