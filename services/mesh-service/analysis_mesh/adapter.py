"""Production PyMeshLab component-isolated isotropic remeshing adapter."""
from __future__ import annotations

from pathlib import Path
from typing import Any
import numpy as np

from .contracts import AlgorithmDescriptor, ComponentMeshStream, MeshPart, mesh_position_hash
from .quality import mesh_quality_metrics


class PyMeshLabIsotropicComponent:
    descriptor = AlgorithmDescriptor(
        id="pymeshlab-isotropic-component-v1", label="PyMeshLab isotropic per component",
        implementationVersion="1.2.0", contractVersion="1", capabilities=("component-isolated", "isotropic-remesh", "quality-metrics", "source-model-frame"),
        parameterSchema={"targetEdgeLength": {"type": "number", "minimum": 0.000001},
                         "featureAngleDegrees": {"type": "number", "minimum": 0, "maximum": 180},
                         "iterations": {"type": "integer", "minimum": 1, "maximum": 100}},
        defaults={"targetEdgeLength": 0.02, "featureAngleDegrees": 60.0, "iterations": 10},
    )

    def build(self, stream: ComponentMeshStream, effectiveParameters: dict[str, Any], workspace: str) -> ComponentMeshStream:
        # Import here: contract/loader tests remain useful on environments without PyMeshLab.
        import pymeshlab
        for component in stream.components:
            for index, part in enumerate(component.parts):
                source_quality = mesh_quality_metrics(part.mesh)
                vertices = np.asarray(part.mesh.vertices, dtype=np.float64)
                faces = np.asarray(part.mesh.faces, dtype=np.int32)
                ms = pymeshlab.MeshSet()
                ms.add_mesh(pymeshlab.Mesh(vertex_matrix=vertices, face_matrix=faces))
                target = float(effectiveParameters["targetEdgeLength"])
                # PyMeshLab renamed this wrapper between supported releases.
                target_value = pymeshlab.AbsoluteValue(target) if hasattr(pymeshlab, "AbsoluteValue") else (pymeshlab.PureValue(target) if hasattr(pymeshlab, "PureValue") else target)
                ms.meshing_isotropic_explicit_remeshing(
                    targetlen=target_value,
                    iterations=effectiveParameters["iterations"], featuredeg=float(effectiveParameters["featureAngleDegrees"]),
                    adaptive=False, checksurfdist=True, collapseflag=True, splitflag=True, swapflag=True,
                    smoothflag=True, reprojectflag=True)
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
