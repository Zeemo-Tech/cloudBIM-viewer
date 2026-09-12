"""Acceptance tests against independently generated analytic circular tubes."""
import tempfile
import unittest
from unittest.mock import patch
import json
from pathlib import Path
import numpy as np
import trimesh
from algorithms.rebar_sweep import RebarSweepRemesh, remesh_rebar, remesh_solid_parts, sweep_mesh
from analysis_mesh import registry
from analysis_mesh.contracts import Component, ComponentMeshStream, MeshPart, ContractError, mesh_position_hash
from analysis_mesh.artifact import build_artifact


def tube_fixture(bent=True, sides=26, radius=.004):
    theta=np.linspace(0,np.pi,25) if bent else np.zeros(2)
    centers=np.c_[.04*np.cos(theta),.04*np.sin(theta),np.zeros(len(theta))]
    if not bent: centers=np.array([[0.,0.,0.],[0.,.203,0.]])
    radial=np.c_[np.cos(theta),np.sin(theta),np.zeros(len(theta))]
    phi=np.arange(sides)*2*np.pi/sides
    vertices=(centers[:,None,:]+radius*(np.cos(phi)[None,:,None]*radial[:,None,:]+np.sin(phi)[None,:,None]*np.array([0.,0.,1.]))).reshape(-1,3)
    faces=[]
    for i in range(len(centers)-1):
        for j in range(sides):
            a,b=i*sides+j,i*sides+(j+1)%sides
            faces.extend([(a,a+sides,b),(b,a+sides,b+sides)])
    for j in range(1,sides-1):
        faces.extend([(0,j,j+1),((len(centers)-1)*sides,(len(centers)-1)*sides+j+1,(len(centers)-1)*sides+j)])
    mesh=trimesh.Trimesh(vertices=vertices,faces=faces,process=False)
    if mesh.volume<0: mesh.invert()
    return mesh


class RebarSweepTests(unittest.TestCase):
    def test_sparse_sharp_corner_is_not_smoothed_into_an_invented_bow(self):
        centers=np.array([[0.,0.,0.],[.1,0,0],[.1,.1,0]])
        tangents=np.array([[1.,0,0],[1.,1,0],[0.,1,0]])
        tangents/=np.linalg.norm(tangents,axis=1)[:,None]
        source=sweep_mesh(centers,tangents,.004,26,np.array([0.,0.,1.]))
        output,report=remesh_rebar(source,{})
        self.assertEqual(report['status'],'preserved')
        np.testing.assert_array_equal(output.vertices,source.vertices)
        np.testing.assert_array_equal(output.faces,source.faces)

    def test_coincident_duplicate_solids_preserve_complete_input_topology(self):
        source=tube_fixture(False)
        duplicate=trimesh.util.concatenate([source,source.copy()])
        output,reports=remesh_solid_parts(duplicate,{})
        self.assertEqual(len(reports),1)
        self.assertEqual(reports[0]['status'],'preserved')
        np.testing.assert_array_equal(output.vertices,duplicate.vertices)
        np.testing.assert_array_equal(output.faces,duplicate.faces)

    def test_vertex_budget_applies_across_solids_and_components(self):
        a=tube_fixture(False); b=a.copy(); b.apply_translation([.02,0,0])
        params={'crossSectionSides':8,'axialSpacing':.04}
        with patch('algorithms.rebar_sweep.MAX_VERTICES',100):
            single,_=remesh_solid_parts(a,params)
            self.assertLess(len(single.vertices),100)
            with self.assertRaises(ValueError):
                remesh_solid_parts(trimesh.util.concatenate([a,b]),params)
            builder=registry.get('rebar-sweep-component-v1')
            stream=ComponentMeshStream([Component(str(i),[MeshPart(str(i),str(i),m,np.eye(4),mesh_position_hash(m))]) for i,m in enumerate([a,b])],{})
            with self.assertRaises(ValueError): builder.build(stream,params,'')

    def test_straight_polygon_radius_uniform_spacing_and_caps(self):
        source=tube_fixture(False); original=source.vertices.copy()
        for sides in (8,16,32,64):
            mesh,report=remesh_rebar(source,{'crossSectionSides':sides,'axialSpacing':.01})
            self.assertEqual(report['status'],'rebuilt')
            rings=mesh.vertices[:-2].reshape(-1,sides,3); centers=rings.mean(1)
            np.testing.assert_allclose(np.linalg.norm(rings-centers[:,None],axis=2),.004,atol=1e-10)
            np.testing.assert_allclose(np.linalg.norm(np.diff(centers,axis=0),axis=1),.203/21,atol=1e-10)
            self.assertTrue(mesh.is_watertight and mesh.is_winding_consistent)
            self.assertGreater(mesh.volume,0)
        np.testing.assert_array_equal(source.vertices,original)

    def test_half_circle_retains_curvature_and_uniform_arc_spacing(self):
        for sides in (8,16,32):
            mesh,report=remesh_rebar(tube_fixture(),{'crossSectionSides':sides,'axialSpacing':.02,'maxChordError':.00002})
            self.assertEqual(report['status'],'rebuilt')
            centers=mesh.vertices[:-2].reshape(-1,sides,3).mean(1)
            np.testing.assert_allclose(np.linalg.norm(centers[:,:2],axis=1),.04,atol=6e-7)
            theta=np.unwrap(np.arctan2(centers[:,1],centers[:,0])); steps=np.abs(np.diff(theta))*.04
            self.assertLess(np.ptp(steps)/steps.mean(),.0001)
            self.assertLess(abs(report['axisLengthM']-np.pi*.04),1e-6)
            self.assertGreater(centers[:,1].max(),.0399)
            self.assertLessEqual(.04*(1-np.cos(np.max(np.abs(np.diff(theta)))/2)),.00002)
            self.assertTrue(mesh.is_watertight and mesh.is_winding_consistent)

    def test_rotation_translation_preserve_source_frame(self):
        source=tube_fixture(); transform=trimesh.transformations.rotation_matrix(.83,[1,2,3]); transform[:3,3]=[7,-2,.5]
        source.apply_transform(transform); result,report=remesh_rebar(source,{})
        self.assertEqual(report['status'],'rebuilt'); result.apply_transform(np.linalg.inv(transform))
        centers=result.vertices[:-2].reshape(-1,16,3).mean(1)
        np.testing.assert_allclose(np.linalg.norm(centers[:,:2],axis=1),.04,atol=6e-7)
        np.testing.assert_allclose(centers[:,2],0,atol=1e-10)

    def test_export_seams_without_millimetre_cleanup(self):
        source=tube_fixture()
        soup=trimesh.Trimesh(vertices=source.triangles.reshape(-1,3),faces=np.arange(len(source.faces)*3).reshape(-1,3),process=False)
        result,report=remesh_rebar(soup,{})
        self.assertEqual(report['status'],'rebuilt'); self.assertTrue(result.is_watertight)

    def test_parallel_solids_keep_gap(self):
        a=tube_fixture(False); b=a.copy(); b.apply_translation([.009,0,0])
        output,reports=remesh_solid_parts(trimesh.util.concatenate([a,b]),{})
        self.assertEqual([r['status'] for r in reports],['rebuilt','rebuilt'])
        pieces=sorted(output.split(),key=lambda m:m.bounds[0,0]); self.assertEqual(len(pieces),2)
        self.assertAlmostEqual(pieces[1].bounds[0,0]-pieces[0].bounds[1,0],.001,places=8)

    def test_fixture_and_damaged_strip_preserved_with_reason(self):
        damaged=tube_fixture(); damaged.update_faces(np.arange(len(damaged.faces)-1))
        for source in [trimesh.creation.box(extents=[1,.2,.01]),damaged]:
            result,report=remesh_rebar(source,{})
            self.assertEqual(report['status'],'preserved'); self.assertTrue(report['reason'])
            np.testing.assert_array_equal(result.vertices,source.vertices)
            np.testing.assert_array_equal(result.faces,source.faces)

    def test_invalid_parameters_and_vertex_budget(self):
        for params in [{'crossSectionSides':7},{'crossSectionSides':16.5},{'crossSectionSides':True},
                       {'axialSpacing':float('nan')},{'maxChordError':float('inf')},{'axialSpacing':0},{'axialSpacing':1e-10}]:
            with self.assertRaises(ValueError): remesh_rebar(tube_fixture(False),params)

    def test_legacy_file_path_uses_shared_geometry(self):
        with tempfile.TemporaryDirectory() as directory:
            src=Path(directory)/'input.ply'; dest=Path(directory)/'output.ply'; tube_fixture().export(src)
            result=RebarSweepRemesh().run(str(src),str(dest),{'cross_section_sides':8})
            mesh=trimesh.load(dest,process=False)
            self.assertEqual(result.extra['solids'][0]['crossSectionSides'],8)
            self.assertEqual(result.vertex_count_after,len(mesh.vertices)); self.assertTrue(mesh.is_watertight)

    def test_component_identity_diagnostics_and_artifact_chunking(self):
        builder=registry.get('rebar-sweep-component-v1')
        for bad in [{'crossSectionSides':7},{'axialSpacing':0},{'unknown':1}]:
            with self.assertRaises(ContractError): builder.descriptor.effective_parameters(bad)
        mesh=tube_fixture(); fixture=trimesh.creation.box()
        stream=ComponentMeshStream([Component('bar',[MeshPart('bar:part','node',mesh,np.eye(4),mesh_position_hash(mesh))]),
                                    Component('fixture',[MeshPart('fixture:part','other',fixture,np.eye(4),mesh_position_hash(fixture))])],{})
        with tempfile.TemporaryDirectory() as directory:
            params=builder.descriptor.effective_parameters({'crossSectionSides':8}); builder.build(stream,params,directory)
            self.assertEqual([c.global_id for c in stream.components],['bar','fixture'])
            self.assertEqual(stream.components[0].parts[0].part_id,'bar:part')
            manifest=build_artifact(stream,Path(directory)/'artifact',dict(id=builder.descriptor.id,effectiveParameters=params),face_cap=150)
            metrics=json.loads((Path(directory)/'artifact/metrics.json').read_text())
            self.assertEqual(metrics['components'][0]['parts'][0]['remeshDiagnostics']['solids'][0]['status'],'rebuilt')
            self.assertEqual(metrics['components'][1]['parts'][0]['remeshDiagnostics']['solids'][0]['status'],'preserved')
            components=json.loads((Path(directory)/'artifact/components.json').read_text())
            self.assertTrue(all(row['faceCount']<=150 for row in components['tiles']))
            self.assertEqual(manifest['componentCount'],2)


if __name__=='__main__': unittest.main()
