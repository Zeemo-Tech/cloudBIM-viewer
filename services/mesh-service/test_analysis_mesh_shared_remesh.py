"""Regression coverage for the shared legacy/component BIM remesh core."""
from __future__ import annotations

import tempfile
import unittest

import numpy as np
import pymeshlab
import trimesh

from algorithms.pymeshlab_remesh import resolve_bim_preprocessor_config, run_bim_preprocessor_core
from analysis_mesh.adapter import PyMeshLabIsotropicComponent
from analysis_mesh.contracts import Component, ComponentMeshStream, MeshPart, mesh_position_hash
from analysis_mesh.quality import mesh_quality_metrics


def stretched_sliver_mesh() -> trimesh.Trimesh:
    """A valid, very non-uniform planar fan with one deliberately thin face."""
    vertices = np.array([
        [0.0, 0.0, 0.0], [8.0, 0.0, 0.0], [8.0, 1.0, 0.0],
        [0.0, 1.0, 0.0], [0.002, 0.00002, 0.0],
    ])
    faces = np.array([[0, 1, 4], [1, 2, 4], [2, 3, 4], [3, 0, 4]])
    return trimesh.Trimesh(vertices=vertices, faces=faces, process=False)


class SharedBIMRemeshTests(unittest.TestCase):
    def setUp(self) -> None:
        self.builder = PyMeshLabIsotropicComponent()
        self.effective = self.builder.descriptor.effective_parameters({
            "targetEdgeLength": 0.25,
            "cleanTolerance": 0.01,
            "useDecimation": False,
            "subdivisionIterations": 2,
            "iterations": 5,
        })

    def _stream(self, mesh: trimesh.Trimesh) -> ComponentMeshStream:
        part = MeshPart("part-1", "stretched slab", mesh, np.eye(4), mesh_position_hash(mesh))
        return ComponentMeshStream([Component("component-42", [part])], {"id": "root", "children": []})

    def test_adapter_preserves_identity_and_improves_stretched_sliver_quality(self) -> None:
        source = stretched_sliver_mesh()
        before = mesh_quality_metrics(source)
        stream = self.builder.build(self._stream(source), self.effective, tempfile.mkdtemp())
        component = stream.components[0]
        part = component.parts[0]
        after = mesh_quality_metrics(part.mesh)

        self.assertEqual(component.global_id, "component-42")
        self.assertEqual(part.part_id, "part-1")
        self.assertEqual(part.node_name, "stretched slab")
        np.testing.assert_array_equal(part.transform, np.eye(4))
        self.assertEqual(part.position_hash, mesh_position_hash(part.mesh))
        self.assertIsNotNone(part.source_quality)
        self.assertEqual(part.source_quality["faceCount"], before["faceCount"])
        self.assertEqual(after["degenerateFaceCount"], 0)
        self.assertLess(after["edgeLength"]["coefficientOfVariation"], before["edgeLength"]["coefficientOfVariation"] * 0.75)
        self.assertLess(after["edgeLength"]["p95"], before["edgeLength"]["p95"] * 0.5)
        self.assertLess(after["triangleAspectRatio"]["p95"], before["triangleAspectRatio"]["p95"] * 0.5)

    def test_adapter_and_legacy_core_have_identical_result_for_equivalent_parameters(self) -> None:
        source = stretched_sliver_mesh()
        direct = pymeshlab.MeshSet()
        direct.add_mesh(pymeshlab.Mesh(
            vertex_matrix=np.asarray(source.vertices, dtype=np.float64),
            face_matrix=np.asarray(source.faces, dtype=np.int32),
        ))
        config = resolve_bim_preprocessor_config({
            "target_edge_length": self.effective["targetEdgeLength"],
            "clean_tolerance": self.effective["cleanTolerance"],
            "use_decimation": self.effective["useDecimation"],
            "decimation_ratio": self.effective["decimationRatio"],
            "subdivision_iterations": self.effective["subdivisionIterations"],
            "subdivision_threshold_ratio": self.effective["subdivisionThresholdRatio"],
            "adaptive": self.effective["adaptive"],
            "crease_angle": self.effective["featureAngleDegrees"],
            "use_isotropic": self.effective["useIsotropic"],
            "isotropic_iterations": self.effective["iterations"],
            "surface_dist_ratio": self.effective["surfaceDistanceRatio"],
            "isotropic_collapse": self.effective["isotropicCollapse"],
            "sliver_merge_ratio": self.effective["sliverMergeRatio"],
            "sliver_relax_checksurfdist": self.effective["sliverRelaxCheckSurfaceDistance"],
        })
        run_bim_preprocessor_core(direct, config, face_count_before=len(source.faces))
        direct_mesh = trimesh.Trimesh(
            vertices=direct.current_mesh().vertex_matrix(), faces=direct.current_mesh().face_matrix(), process=False,
        )
        adapted = self.builder.build(self._stream(source), self.effective, tempfile.mkdtemp()).components[0].parts[0].mesh

        self.assertEqual(mesh_position_hash(adapted), mesh_position_hash(direct_mesh))
        self.assertEqual(mesh_quality_metrics(adapted), mesh_quality_metrics(direct_mesh))


if __name__ == "__main__":
    unittest.main()
