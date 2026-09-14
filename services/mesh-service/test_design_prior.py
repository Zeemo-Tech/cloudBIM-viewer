from copy import deepcopy
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import laspy
import numpy as np

from rebar_design_prior import inventory_from_bars, prepare_snapshot, validate_snapshot, extract_model, _resolve_active_asset, SCHEMA
from rebar_design_prior import digest_file
from algorithms.design_prior_refinement import refine_design_prior, ATTRIBUTES, PriorParameters
from test_internal_rebar import cylinder

IDENTITY = np.eye(4).ravel(order='F').tolist()


def inventory(paths, radius=.004, same_parent=False):
    if same_parent:
        bars = [dict(designBarId='folded', points=np.vstack([paths[0][0], *[p[1] for p in paths]]).tolist(),
                     radiusM=radius, coverage='complete', source='ifc-analytic')]
    else:
        bars = [dict(designBarId=f'bar{i}', points=np.array(path).tolist(), radiusM=radius,
                     coverage='complete', source='ifc-analytic') for i, path in enumerate(paths)]
    return inventory_from_bars(bars, IDENTITY)


def observed(paths, radius=.004, owners=None):
    clouds = [cylinder(*path, radius=radius, along=max(30, int(np.linalg.norm(np.diff(path, axis=0))/.002)), around=16) for path in paths]
    owners = owners or list(range(1, len(paths)+1))
    points = np.vstack([c[0] for c in clouds]); normals = np.vstack([c[1] for c in clouds])
    labels = np.concatenate([np.full(len(c[0]), owner, np.uint32) for owner, c in zip(owners, clouds)])
    context = SimpleNamespace(positions=points, normals=normals, normal_valid=np.ones(len(points), np.uint8),
        refined_class=np.full(len(points), 3, np.uint8), complete_class=np.full(len(points), 3, np.uint8),
        complete_instance=labels, complete_cluster=np.zeros(len(points), np.uint32), classification_cache=None, region_cache={})
    report = {'instances': [], 'segments': []}
    for i, (owner, path, cloud) in enumerate(zip(owners, paths, clouds)):
        report['segments'].append(dict(id=i+1, instanceId=owner, startM=list(path[0]), endM=list(path[1]), radiusM=radius))
        report['instances'].append(dict(id=owner, family=4 if abs(path[1][2]-path[0][2]) > .03 else 3,
                                        pointCount=len(cloud[0]), segmentIds=[i+1]))
    return context, report


class MatchingUnitTests(unittest.TestCase):
    def setUp(self):
        self.webs = [((i*.1, 0., .02 if i%2 == 0 else .12), ((i+1)*.1, 0., .12 if i%2 == 0 else .02)) for i in range(6)]

    def test_one_parent_six_units_six_observed_instances_geometry_and_topology(self):
        design = inventory(self.webs, same_parent=True)
        self.assertEqual(design['coverage']['physicalBarCount'], 1)
        self.assertEqual(design['coverage']['webStraightUnitCount'], 6)
        for mode in ('geometry', 'topology'):
            c, r = observed(self.webs); original = c.positions.copy()
            result = refine_design_prior(c, r, design, mode=mode)
            np.testing.assert_array_equal(c.positions, original)
            self.assertEqual(set(c.prior_instance), set(range(1, 7)))
            self.assertEqual(result['counts']['mergedInstances'], 0)
            self.assertEqual(len({a['designUnitId'] for a in result['components'] if a['status'] == 1}), 6)
            self.assertEqual({a['designBarId'] for a in result['components']}, {'folded'})

    def test_missing_web_is_not_synthesized_or_forced(self):
        c, r = observed(self.webs[:2]+self.webs[3:])
        count = len(c.positions)
        result = refine_design_prior(c, r, inventory(self.webs, same_parent=True), mode='topology')
        self.assertEqual(len(c.prior_class), count)
        self.assertEqual(len(set(c.prior_instance)), 5)
        self.assertEqual(result['counts']['mergedInstances'], 0)

    def test_same_unit_continuous_fragments_merge_but_neighboring_units_do_not(self):
        parts = [((0., 0., .02), (.12, 0., .02)), ((.135, 0., .02), (.28, 0., .02))]
        c, r = observed(parts)
        result = refine_design_prior(c, r, inventory([((0,0,.02),(.28,0,.02))]))
        self.assertEqual(set(c.prior_instance), {1})
        self.assertEqual(result['counts']['mergedInstances'], 1)
        self.assertGreater(result['counts']['mergedPoints'], 0)
        # Same parent, collinear but explicitly distinct units must not merge.
        design = inventory([parts[0], parts[1]])
        for bar in design['bars']: bar['designBarId'] = 'parent'
        for unit in design['units']: unit['designBarId'] = 'parent'
        c, r = observed(parts)
        result = refine_design_prior(c, r, design)
        self.assertEqual(set(c.prior_instance), {1, 2})
        self.assertEqual(result['counts']['mergedInstances'], 0)

    def test_parallel_fragments_and_overlapping_duplicates_never_merge(self):
        for second in [((.13,.009,.02),(.28,.009,.02)), ((.05,0.,.02),(.2,0.,.02))]:
            c, r = observed([((0.,0.,.02),(.15,0.,.02)), second])
            result = refine_design_prior(c, r, inventory([((0,0,.02),(.28,0,.02))]))
            self.assertEqual(set(c.prior_instance), {1,2})
            self.assertEqual(result['counts']['mergedInstances'], 0)

    def test_merge_target_with_pending_other_unit_support_is_not_consumed(self):
        parts = [((0.,0.,.02),(.06,0.,.02)), ((.14,0.,.02),(.22,0.,.02)),
                 ((.07,0.,.02),(.11,0.,.02))]
        c,r = observed(parts, owners=[1,1,2])
        design = inventory([((0,0,.02),(.1,0,.02)), ((.12,0,.02),(.22,0,.02))])
        result = refine_design_prior(c,r,design,params=PriorParameters(match_margin=0))
        self.assertEqual(result['counts']['mergedInstances'],0)
        self.assertEqual(set(c.prior_instance),{1,2})
        # Existing instances remain atomic even when their support is ambiguous.
        self.assertEqual(sum(x['originalInstanceId']==1 for x in result['components']),1)

    def test_cross_spacing_displaced_or_wrong_diameter_short_is_preserved(self):
        for y, radius in [(.2,.004), (1.,.004), (0.,.006)]:
            c, r = observed([((0.,y,.02),(.28,y,.02))], radius=radius)
            before = deepcopy(r)
            result = refine_design_prior(c, r, inventory([((0,0,.02),(.28,0,.02))]))
            np.testing.assert_array_equal(c.prior_class, 3)
            self.assertEqual(r, before)
            self.assertAlmostEqual(result['components'][0]['observedDiameterM'], 2*radius, delta=.001)
            if y == 1.: self.assertEqual(result['components'][0]['status'], 2)

    def test_ambiguous_same_size_neighbors_are_pending_not_noise(self):
        c, r = observed([((0.,0.,.02),(.28,0.,.02))])
        design = inventory([((0,-.02,.02),(.28,-.02,.02)), ((0,.02,.02),(.28,.02,.02))])
        result = refine_design_prior(c, r, design)
        self.assertEqual(result['components'][0]['status'], 2)
        np.testing.assert_array_equal(c.prior_class, 3)

    def test_unresolved_design_retains_original_labels_and_ids(self):
        c, r = observed(self.webs)
        design = inventory_from_bars([dict(designBarId='unknown', points=[], radiusM=None, coverage='unresolved')], IDENTITY)
        result = refine_design_prior(c, r, design)
        self.assertFalse(result['enabled'])
        np.testing.assert_array_equal(c.prior_instance, c.complete_instance)

    def test_discarded_exterior_hook_is_never_recovered_even_at_design(self):
        hook = [((0.,0.,.02),(.28,0.,.02)), ((.28,0.,.02),(.28,.08,.02))]
        c, r = observed(hook)
        c.complete_class[:] = 4; c.complete_instance[:] = 0; c.complete_cluster[:] = 17
        result = refine_design_prior(c, r, inventory([hook[0]]))
        self.assertEqual(len(result['components']), 0)
        self.assertEqual(len(set(c.prior_class)), 1)
        self.assertEqual(len(set(c.prior_instance)), 1)
        np.testing.assert_array_equal(c.prior_class, 4)
        self.assertEqual(result['counts']['recoveredPoints'],0)
        np.testing.assert_array_equal(c.prior_action,0)
        # Table and fixture are not part of the candidate population.
        c.refined_class[:2] = [1,2]; c.complete_class[:2] = [1,2]; c.complete_cluster[:2] = 0
        refine_design_prior(c, r, inventory([hook[0]]))
        np.testing.assert_array_equal(c.prior_class[:2], [1,2])

    def test_linking_does_not_reclassify_retained_weak_candidates(self):
        rng = np.random.default_rng(5)
        p = rng.normal(size=(200,3))*.001
        c = SimpleNamespace(positions=p, normals=np.zeros_like(p), normal_valid=np.zeros(len(p),np.uint8),
            refined_class=np.full(len(p),3,np.uint8), complete_class=np.full(len(p),3,np.uint8),
            complete_instance=np.zeros(len(p),np.uint32), complete_cluster=np.ones(len(p),np.uint32), classification_cache=None)
        result = refine_design_prior(c, {'instances':[],'segments':[]}, inventory([((0,0,0),(.28,0,0))]))
        self.assertEqual(result['counts']['filteredPoints'], 0)
        np.testing.assert_array_equal(c.prior_class,c.complete_class)
        np.testing.assert_array_equal(c.prior_status,2)

    def test_retained_whole_exterior_cluster_keeps_one_component(self):
        c,r=observed([((0.,0.,.02),(.28,0.,.02))])
        c.complete_instance[:]=0;c.complete_cluster[:]=17
        result=refine_design_prior(c,r,inventory([((0,0,.02),(.28,0,.02))]))
        self.assertEqual(len(result['components']),1)
        self.assertEqual(len(set(c.prior_instance)),1)
        np.testing.assert_array_equal(c.prior_instance,0)
        self.assertEqual(result['components'][0]['instanceId'],0)
        np.testing.assert_array_equal(c.prior_class,c.complete_class)

    def test_three_fragment_chain_can_link_without_end_to_end_gap_requirement(self):
        parts=[((0.,0.,.02),(.2,0.,.02)),((.3,0.,.02),(.5,0.,.02)),((.6,0.,.02),(.8,0.,.02))]
        c,r=observed(parts)
        result=refine_design_prior(c,r,inventory([((0,0,.02),(.8,0,.02))]))
        self.assertEqual(len(set(c.prior_instance)),1)
        self.assertEqual(result['counts']['mergedInstances'],2)
        np.testing.assert_array_equal(c.prior_class,c.complete_class)

    def test_large_unobserved_gap_is_not_joined_to_satisfy_design_count(self):
        c,r=observed([((0.,0.,.02),(.2,0.,.02)),((.8,0.,.02),(1.,0.,.02))])
        result=refine_design_prior(c,r,inventory([((0,0,.02),(1,0,.02))]))
        self.assertEqual(result['counts']['mergedInstances'],0)
        self.assertEqual(len(set(c.prior_instance)),2)

    def test_small_fragment_near_quarter_of_long_observed_span_is_supported(self):
        c,r = observed([((0.,0.,.02),(1.,0.,.02))])
        rng = np.random.default_rng(5)
        p = rng.normal(size=(200,3))*.001 + [.25,.004,.02]
        n = len(c.positions)
        c.positions = np.vstack([c.positions,p]); c.normals = np.vstack([c.normals,np.zeros_like(p)])
        c.normal_valid = np.r_[c.normal_valid,np.zeros(len(p),np.uint8)]
        c.refined_class = c.complete_class = np.full(n+len(p),3,np.uint8)
        c.complete_instance = np.r_[c.complete_instance,np.zeros(len(p),np.uint32)]
        c.complete_cluster = np.r_[c.complete_cluster,np.ones(len(p),np.uint32)]
        result = refine_design_prior(c,r,inventory([((0,0,.02),(1,0,.02))]))
        np.testing.assert_array_equal(c.prior_class[n:],3)
        self.assertEqual(result['counts']['filteredPoints'],0)
        self.assertEqual(c.prior_status[-1],2)


class SnapshotTests(unittest.TestCase):
    def test_database_binding_follows_current_model_and_alignment_without_stale_fallback(self):
        config={'sourcePath':'/tmp/prior-binding/assets/scan/source.las','ifcPath':'old.ifc',
                'databaseBinding':{'scanAssetId':5,'dataRoot':'/tmp/prior-binding'}}
        row={'scanDir':'/app/data/assets/scan','modelDir':'/app/data/assets/new','modelName':'replacement.ifc',
             'bimAssetId':7,'uploadDir':'/app/data/uploads/new','alignmentId':2,'scanToBim':IDENTITY}
        with patch('rebar_design_prior.subprocess.run',return_value=SimpleNamespace(returncode=0,stdout=json.dumps(row))) as run:
            result=_resolve_active_asset(config)
            self.assertEqual(result['ifcPath'],'/tmp/prior-binding/uploads/new/source')
            self.assertEqual(result['bimAssetId'],7)
            self.assertEqual(result['scanToBim'],IDENTITY)
            self.assertIn('linked_bim_id',run.call_args.kwargs['input'])
        with patch('rebar_design_prior.subprocess.run',return_value=SimpleNamespace(returncode=0,stdout='')):
            with self.assertRaises(ValueError):_resolve_active_asset(config)

    def test_actual_replacement_ifc_has_144_web_units_and_12_short_bars(self):
        root=Path(__file__).resolve().parents[2]
        ifc=root/'backend/data/uploads/f51d87ced765177fdd1fbdee/source'
        glb=root/'backend/data/assets/88d5ff2f0b10c8507273a309/model.glb'
        if not ifc.is_file() or not glb.is_file(): self.skipTest('local replacement BIM unavailable')
        raw=extract_model(ifc,glb)
        matrix=[1,0,0,0,0,0,-1,0,0,1,0,0,.00309074978451207,-.007138338687760412,-.0029162709479768623,1]
        result=inventory_from_bars(raw['bars'],matrix)
        self.assertEqual(result['coverage']['physicalBarCount'],70)
        self.assertEqual(result['coverage']['webPhysicalBarCount'],4)
        self.assertEqual(result['coverage']['webStraightUnitCount'],144)
        self.assertEqual(result['coverage']['matchingUnitCount'],210)
        short=[u for u in result['units'] if u['kind']=='short']
        self.assertEqual(len(short),12)
        np.testing.assert_allclose([u['lengthM'] for u in short],.28,atol=1e-6)

    def test_long_hook_leg_is_not_counted_as_an_independent_web(self):
        bars=[dict(designBarId='hooked',points=[[0,0,0],[.04,0,.04],[4.2,0,.04]],radiusM=.004,coverage='complete')]
        result=inventory_from_bars(bars,IDENTITY)
        self.assertEqual(len(result['units']),1)
        self.assertEqual(result['coverage']['webStraightUnitCount'],0)
        self.assertEqual(result['bars'][0]['excludedHookRunCount'],1)

    def test_inverse_transform_layers_and_parent_identity(self):
        matrix = np.eye(4); matrix[:3,3] = [1,2,3]
        bars = [dict(designBarId='x', points=[[1,2,3],[2,2,3],[3,2,4]], radiusM=.004, coverage='complete')]
        result = inventory_from_bars(bars, matrix.ravel(order='F').tolist())
        np.testing.assert_allclose(result['units'][0]['startM'], [0,0,0])
        self.assertEqual(len(result['bars']), 1)
        self.assertEqual(len(result['units']), 2)
        self.assertEqual(result['units'][1]['kind'], 'web')
        self.assertTrue(any(e['kind']=='next' for e in result['relations']))

    def test_model_and_alignment_cache_invalidate_separately_and_source_binding(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp); (root/'scan.las').write_bytes(b'scan'); (root/'model.ifc').write_text('a')
            path=root/'config.json'
            config={'sourcePath':'scan.las','sourceSha256':'a'*64,'ifcPath':'model.ifc','scanToBim':IDENTITY}
            path.write_text(json.dumps(config))
            model={'bars':[dict(designBarId='b',points=[[0,0,.02],[.28,0,.02]],radiusM=.004,coverage='complete')], 'ifcUnitScale':1,'diagnostics':{}}
            with patch('rebar_design_prior.extract_model', return_value=model) as extract:
                first=prepare_snapshot(path); second=prepare_snapshot(path)
                self.assertEqual(extract.call_count,1)
                self.assertFalse(first['preparation']['modelCacheHit']); self.assertTrue(second['preparation']['alignmentCacheHit'])
                config['scanToBim']=IDENTITY.copy();config['scanToBim'][12]=.01;path.write_text(json.dumps(config))
                third=prepare_snapshot(path)
                self.assertEqual(extract.call_count,1)
                self.assertTrue(third['preparation']['modelCacheHit']); self.assertFalse(third['preparation']['alignmentCacheHit'])
                (root/'model.ifc').write_text('b'); fourth=prepare_snapshot(path)
                self.assertEqual(extract.call_count,2)
                self.assertNotEqual(third['fingerprint'],fourth['fingerprint'])
            validate_snapshot(fourth,root/'scan.las','a'*64)
            with self.assertRaises(ValueError):validate_snapshot(fourth,root/'scan.las','b'*64)
            with self.assertRaises(ValueError):validate_snapshot(fourth,root/'another.las','a'*64)

    def test_invalid_rigid_transform_rejected(self):
        with self.assertRaises(ValueError): inventory_from_bars([], [0]*16)


class ArtifactTests(unittest.TestCase):
    def test_withdrawn_steps_reject_new_runs_without_publishing(self):
        from pointcloud_step_pipeline import run_from_source
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for step, prior in [(7, 'off'), (6, 'geometry'), (6, 'topology')]:
                with self.assertRaises(ValueError):
                    run_from_source(root/'source.las', root/'out', workers=1,
                                    through_step=step, prior_mode=prior)
            self.assertFalse((root/'out').exists())


if __name__ == '__main__': unittest.main()
