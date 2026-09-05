from __future__ import annotations
import unittest
import importlib
import tempfile
from pathlib import Path
import numpy as np

from algorithms.rebar_base import (RebarAlgorithm, RebarAlgorithmError, RebarAlgorithmRegistry,
                                   RebarAnalysis, RebarPointAttributes, UnknownRebarAlgorithmError)
from algorithms.rebar_geometric import GeometricV2Adapter
from rebar_poc import (
    InvalidRebarInputOptionsError,
    normalize_rebar_input_options,
    resolve_point_cloud_path,
)

class _Algorithm(RebarAlgorithm):
    @property
    def descriptor(self): return {"id": "unit", "version": "1"}
    def normalize_parameters(self, raw): return {}
    def analyze(self, sample, parameters): return RebarAnalysis({})
    def project_points(self, points_xyz, analysis):
        return RebarPointAttributes(np.zeros(len(points_xyz), np.uint8), np.zeros(len(points_xyz), np.uint16), np.zeros(len(points_xyz), np.uint32))

class RegistryTests(unittest.TestCase):
    def test_stable_duplicate_and_unknown_errors(self):
        registry = RebarAlgorithmRegistry(); registry.register(_Algorithm())
        with self.assertRaisesRegex(RebarAlgorithmError, "rebar algorithm already registered: unit"):
            registry.register(_Algorithm())
        with self.assertRaisesRegex(UnknownRebarAlgorithmError, "unknown rebar algorithm: absent"):
            registry.get("absent")

    def test_replace_unregister_and_builtin_reload_are_explicit(self):
        registry = RebarAlgorithmRegistry(); first = _Algorithm(); second = _Algorithm()
        registry.register(first); registry.register(second, replace=True)
        self.assertIs(registry.get("unit"), second); self.assertTrue(registry.has("unit"))
        self.assertIs(registry.unregister("unit"), second); self.assertFalse(registry.has("unit"))
        import algorithms
        importlib.reload(algorithms)
        self.assertTrue(algorithms.REBAR_ALGORITHM_REGISTRY.has("geometric-v2"))
        self.assertTrue(algorithms.REBAR_ALGORITHM_REGISTRY.has("geometric-v3"))

    def test_geometric_projects_different_tile_count_by_world_segments(self):
        adapter = GeometricV2Adapter()
        analysis = RebarAnalysis({"schema": "rebar-analysis-v1", "algorithmDetails": {"projection": {"kind": "world-segment-tube-v1", "instances": [
            {"instance": 7, "direction": 2, "worldStart": [0,0,0], "worldEnd": [1,0,0], "radius": .05}
        ]}}})
        attrs = adapter.project_points(np.array([[.5,.01,0],[.5,.2,0]], float), analysis)
        self.assertEqual(attrs.rebar_class.tolist(), [1, 0])
        self.assertEqual(attrs.rebar_direction.tolist(), [2, 0])
        self.assertEqual(attrs.rebar_instance.tolist(), [7, 0])

    def test_geometric_descriptor_drives_generic_parameter_ui(self):
        descriptor = GeometricV2Adapter().descriptor
        properties = descriptor["parameterSchema"]["properties"]
        self.assertEqual(properties["plane_distance_threshold"]["type"], "number")
        self.assertEqual(properties["plane_distance_threshold"]["unit"], "m")
        self.assertEqual(properties["pca_max_neighbors"]["type"], "integer")
        self.assertEqual(
            descriptor["uiHints"]["advanced"],
            descriptor["uiHints"]["order"],
        )

    def test_symlink_without_real_suffix_uses_trusted_requested_format(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); target = root / "payload"; target.write_bytes(b"x")
            link = root / "cloud.ply"; link.symlink_to(target)
            self.assertEqual(resolve_point_cloud_path(link, storage_root=root, point_cloud_format="ply"), target)

    def test_input_options_are_canonical_and_strict(self):
        self.assertEqual(
            normalize_rebar_input_options(
                {"max_input_points": 1234, "voxel_size": 0.004}
            ),
            {"maxInputPoints": 1234, "voxelSize": 0.004},
        )
        for invalid in (
            {"unknown": 1},
            {"maxInputPoints": True},
            {"maxInputPoints": "2000"},
            {"voxelSize": 0},
            {"voxelSize": float("nan")},
            {"voxelSize": 0.01, "voxel_size": 0.01},
        ):
            with self.subTest(invalid=invalid):
                with self.assertRaises(InvalidRebarInputOptionsError):
                    normalize_rebar_input_options(invalid)
