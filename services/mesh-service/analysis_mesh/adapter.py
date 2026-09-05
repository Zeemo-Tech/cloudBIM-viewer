"""Production PyMeshLab component-isolated isotropic remeshing adapter."""
from __future__ import annotations

from pathlib import Path
from typing import Any
import numpy as np

from algorithms.pymeshlab_remesh import resolve_bim_preprocessor_config, run_bim_preprocessor_core
from .contracts import AlgorithmDescriptor, ComponentMeshStream, MeshPart, mesh_position_hash
from .quality import mesh_quality_metrics


class PyMeshLabIsotropicComponent:
    descriptor = AlgorithmDescriptor(
        id="pymeshlab-isotropic-component-v1", label="PyMeshLab isotropic per component",
        implementationVersion="2.0.0", contractVersion="1", capabilities=("component-isolated", "isotropic-remesh", "quality-metrics", "source-model-frame"),
        parameterSchema={
            # Match the shared resolver exactly so recorded effective parameters
            # are also the values that the remesher executes.
            "targetEdgeLength": {"type": "number", "minimum": 0.001},
            "cleanTolerance": {"type": "number", "minimum": 0},
            "useDecimation": {"type": "boolean"},
            "decimationRatio": {"type": "number", "minimum": 0.05, "maximum": 0.95},
            "subdivisionIterations": {"type": "integer", "minimum": 0, "maximum": 10},
            "subdivisionThresholdRatio": {"type": "number", "minimum": 1, "maximum": 4},
            "adaptive": {"type": "boolean"},
            "featureAngleDegrees": {"type": "number", "minimum": 0, "maximum": 90},
            "useIsotropic": {"type": "boolean"},
            "iterations": {"type": "integer", "minimum": 1, "maximum": 20},
            "surfaceDistanceRatio": {"type": "number", "minimum": 0.01, "maximum": 2},
            "isotropicCollapse": {"type": "boolean"},
            "sliverMergeRatio": {"type": "number", "minimum": 0, "maximum": 0.1},
            "sliverRelaxCheckSurfaceDistance": {"type": "boolean"},
        },
        defaults={
            "targetEdgeLength": 0.1, "cleanTolerance": 0.005,
            "useDecimation": True, "decimationRatio": 0.5,
            "subdivisionIterations": 2, "subdivisionThresholdRatio": 2.0,
            "adaptive": True, "featureAngleDegrees": 60.0,
            "useIsotropic": True, "iterations": 5,
            "surfaceDistanceRatio": 0.5, "isotropicCollapse": True,
            "sliverMergeRatio": 0.03, "sliverRelaxCheckSurfaceDistance": True,
        },
    )

    def build(self, stream: ComponentMeshStream, effectiveParameters: dict[str, Any], workspace: str) -> ComponentMeshStream:
        config = resolve_bim_preprocessor_config({
            "target_edge_length": effectiveParameters["targetEdgeLength"],
            "clean_tolerance": effectiveParameters["cleanTolerance"],
            "use_decimation": effectiveParameters["useDecimation"],
            "decimation_ratio": effectiveParameters["decimationRatio"],
            "subdivision_iterations": effectiveParameters["subdivisionIterations"],
            "subdivision_threshold_ratio": effectiveParameters["subdivisionThresholdRatio"],
            "adaptive": effectiveParameters["adaptive"],
            "crease_angle": effectiveParameters["featureAngleDegrees"],
            "use_isotropic": effectiveParameters["useIsotropic"],
            "isotropic_iterations": effectiveParameters["iterations"],
            "surface_dist_ratio": effectiveParameters["surfaceDistanceRatio"],
            "isotropic_collapse": effectiveParameters["isotropicCollapse"],
            "sliver_merge_ratio": effectiveParameters["sliverMergeRatio"],
            "sliver_relax_checksurfdist": effectiveParameters["sliverRelaxCheckSurfaceDistance"],
        })
        import pymeshlab
        for component in stream.components:
            for index, part in enumerate(component.parts):
                source_quality = mesh_quality_metrics(part.mesh)
                vertices = np.asarray(part.mesh.vertices, dtype=np.float64)
                faces = np.asarray(part.mesh.faces, dtype=np.int32)
                ms = pymeshlab.MeshSet()
                ms.add_mesh(pymeshlab.Mesh(vertex_matrix=vertices, face_matrix=faces))
                run_bim_preprocessor_core(ms, config, face_count_before=len(faces))
                result = ms.current_mesh()
                import trimesh
                mesh = trimesh.Trimesh(vertices=result.vertex_matrix(), faces=result.face_matrix(), process=False)
                component.parts[index] = MeshPart(
                    part.part_id,
                    part.node_name,
                    mesh,
                    part.transform,
                    mesh_position_hash(mesh),
                    source_quality,
                )
        return stream
