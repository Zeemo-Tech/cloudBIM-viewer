import os
import tempfile
import unittest
from pathlib import Path

from fastapi import HTTPException
from analysis_mesh_api import _safe, create_analysis_mesh_router


class ApiPathTest(unittest.TestCase):
    def test_paths_cannot_escape_shared_storage(self):
        with tempfile.TemporaryDirectory() as d:
            previous = os.environ.get("ANALYSIS_MESH_STORAGE_ROOT")
            os.environ["ANALYSIS_MESH_STORAGE_ROOT"] = d
            try:
                inside = Path(d) / "input.glb"; inside.write_bytes(b"x")
                self.assertEqual(_safe("input.glb", must_exist=True), inside.resolve())
                with self.assertRaises(HTTPException): _safe("../outside.glb", must_exist=False)
            finally:
                if previous is None: os.environ.pop("ANALYSIS_MESH_STORAGE_ROOT", None)
                else: os.environ["ANALYSIS_MESH_STORAGE_ROOT"] = previous

    def test_wrapped_endpoint_keeps_json_body_contract(self):
        router = create_analysis_mesh_router(lambda _name: lambda handler: handler)
        route = next(
            route for route in router.routes if getattr(route, "path", "") == "/analysis-mesh/build"
        )
        self.assertEqual([parameter.name for parameter in route.dependant.body_params], ["request"])
        self.assertEqual(route.dependant.query_params, [])
