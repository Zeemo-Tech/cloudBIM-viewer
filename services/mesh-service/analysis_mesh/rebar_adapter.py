"""Component-preserving circular rebar sweep reconstruction."""
from __future__ import annotations

from algorithms.rebar_sweep import remesh_solid_parts, check_vertex_budget
from .contracts import AlgorithmDescriptor, ComponentMeshStream, MeshPart, mesh_position_hash
from .quality import mesh_quality_metrics


class RebarSweepComponent:
    descriptor = AlgorithmDescriptor(
        id='rebar-sweep-component-v1', label='钢筋保形均匀化（多边形截面 / 沿轴等距）',
        implementationVersion='1.0.0', contractVersion='1',
        capabilities=('component-isolated', 'rebar-sweep', 'quality-metrics', 'source-model-frame'),
        parameterSchema={
            'crossSectionSides': {'type': 'integer', 'minimum': 8, 'maximum': 128},
            'axialSpacing': {'type': 'number', 'minimum': .0001, 'maximum': 1.},
            'maxChordError': {'type': 'number', 'minimum': .000001, 'maximum': .01},
        },
        defaults={'crossSectionSides': 16, 'axialSpacing': .01, 'maxChordError': .0001},
    )

    def build(self, stream: ComponentMeshStream, effectiveParameters, workspace):
        params = self.descriptor.effective_parameters(effectiveParameters)
        vertex_count = 0
        for component in stream.components:
            for index, part in enumerate(component.parts):
                output, report = remesh_solid_parts(part.mesh, params)
                vertex_count += len(output.vertices)
                check_vertex_budget(vertex_count)
                component.parts[index] = MeshPart(
                    part.part_id, part.node_name, output, part.transform,
                    mesh_position_hash(output), mesh_quality_metrics(part.mesh),
                    {'solids': report},
                )
        return stream
