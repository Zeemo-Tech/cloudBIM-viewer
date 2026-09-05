import os
import tempfile
import unittest
from pathlib import Path

from fastapi import HTTPException
from analysis_c2m_api import _safe, create_analysis_c2m_router


class C2MApiTest(unittest.TestCase):
    def test_storage_escape_and_symlink_are_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            old = os.environ.get("ANALYSIS_C2M_STORAGE_ROOT"); os.environ["ANALYSIS_C2M_STORAGE_ROOT"] = d
            try:
                (Path(d) / "ok.las").write_bytes(b"x")
                self.assertEqual(_safe("ok.las", True), Path(d, "ok.las").resolve())
                with self.assertRaises(HTTPException): _safe("../outside", False)
                Path(d, "link").symlink_to(Path(d, "ok.las"))
                with self.assertRaises(HTTPException): _safe("link", True)
            finally:
                if old is None: os.environ.pop("ANALYSIS_C2M_STORAGE_ROOT", None)
                else: os.environ["ANALYSIS_C2M_STORAGE_ROOT"] = old
    def test_parent_heavy_task_is_applied(self):
        seen = []
        def heavy(name):
            seen.append(name); return lambda fn: fn
        create_analysis_c2m_router(heavy)
        self.assertEqual(seen, ["analysis-c2m-build"])

    def test_wrapped_endpoint_keeps_json_body_contract(self):
        router = create_analysis_c2m_router(lambda _name: lambda handler: handler)
        route = next(
            route for route in router.routes if getattr(route, "path", "") == "/analysis-c2m/build"
        )
        self.assertEqual([parameter.name for parameter in route.dependant.body_params], ["request"])
        self.assertEqual(route.dependant.query_params, [])
