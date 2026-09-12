"""The file and component entry points share the sole rebar sweep algorithm."""
from pathlib import Path
import tempfile
import unittest

import numpy as np
import trimesh

from algorithms import ALGORITHM_REGISTRY
from algorithms.rebar_sweep import RebarSweepRemesh
from analysis_mesh import registry
from analysis_mesh.contracts import Component, ComponentMeshStream, ContractError, MeshPart, mesh_position_hash
from test_rebar_sweep import tube_fixture


class SharedRebarRemeshTests(unittest.TestCase):
    def test_only_rebar_algorithm_is_available(self):
        self.assertEqual(list(ALGORITHM_REGISTRY), ['rebar_sweep'])
        self.assertEqual([row['id'] for row in registry.descriptors()], ['rebar-sweep-component-v1'])
        for retired in ('bim_preprocessor', 'bim_isotropic_only'):
            self.assertNotIn(retired, ALGORITHM_REGISTRY)
        with self.assertRaises(ContractError):
            registry.get('pymeshlab-isotropic-component-v1')

    def test_file_and_component_outputs_have_identical_positions(self):
        with tempfile.TemporaryDirectory() as directory:
            source_path=Path(directory)/'source.ply'
            output_path=Path(directory)/'result.ply'
            tube_fixture().export(source_path)
            source=trimesh.load(source_path,process=False)
            RebarSweepRemesh().run(str(source_path),str(output_path),{'cross_section_sides':8,'axial_spacing':.005})
            direct=trimesh.load(output_path,process=False)
            part=MeshPart('bar:part','bar node',source,np.eye(4),mesh_position_hash(source))
            stream=ComponentMeshStream([Component('bar',[part])],{})
            builder=registry.get('rebar-sweep-component-v1')
            params=builder.descriptor.effective_parameters({'crossSectionSides':8,'axialSpacing':.005})
            adapted=builder.build(stream,params,directory).components[0].parts[0]
            self.assertEqual(mesh_position_hash(adapted.mesh),mesh_position_hash(direct))
            self.assertEqual(adapted.part_id,'bar:part')
            self.assertEqual(adapted.remesh_diagnostics['solids'][0]['status'],'rebuilt')

    def test_old_parameters_are_rejected_instead_of_ignored(self):
        with self.assertRaisesRegex(ValueError,'target_edge_length'):
            RebarSweepRemesh().run('unused','unused',{'target_edge_length':.1})
        builder=registry.get('rebar-sweep-component-v1')
        with self.assertRaises(ContractError):
            builder.descriptor.effective_parameters({'targetEdgeLength':.1})


if __name__=='__main__': unittest.main()
