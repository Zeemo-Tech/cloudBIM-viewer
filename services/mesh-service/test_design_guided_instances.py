"""Step 06 behavior on observed rods, never count-only expectations."""
from types import SimpleNamespace
import unittest
import numpy as np
from algorithms.design_guided_instances import refine_instances
from rebar_design_prior import inventory_from_bars
from test_internal_rebar_recovery import cylinder


def inventory(lines):
    bars=[{'designBarId':str(i),'points':[a,b],'radiusM':.0025,'coverage':'complete'} for i,(a,b) in enumerate(lines)]
    return inventory_from_bars(bars,np.eye(4).T.ravel().tolist())


def scene(rods, ids=None, zones=None):
    samples=[cylinder(a,b,along=121) for a,b in rods]
    points,normals=[np.vstack([s[i] for s in samples]) for i in (0,1)]
    sizes=[len(s[0]) for s in samples]
    ids=ids if ids is not None else list(range(1,len(rods)+1))
    owners=np.repeat(ids,sizes).astype(np.uint32)
    types=np.repeat([3 if abs(np.array(b)[2]-np.array(a)[2])>.02 else 1 for a,b in rods],sizes).astype(np.uint8)
    segments=[{'id':i+1,'instanceId':owner,'type':int(types[sum(sizes[:i])]),'startM':a,'endM':b,'radiusM':.0025,'pointCount':sizes[i],'fitMedianErrorM':0.} for i,((a,b),owner) in enumerate(zip(rods,ids)) if owner]
    report={'segments':segments,'instances':[{'id':int(owner),'type':next(s['type'] for s in segments if s['instanceId']==owner),'pointCount':int(np.sum(owners==owner))} for owner in sorted(set(ids)) if owner]}
    ctx=SimpleNamespace(positions=points,normals=normals,refined_class=np.full(len(points),3,np.uint8),
        refined_zone=np.repeat(zones if zones is not None else [1]*len(rods),sizes).astype(np.uint8),
        internal_type=np.where(owners,types,4).astype(np.uint8),internal_instance=owners,
        internal_segment=np.repeat([i+1 if owner else 0 for i,owner in enumerate(ids)],sizes).astype(np.uint32),
        internal_confidence=np.where(owners,.9,0).astype(np.float32))
    return ctx,report,sizes


def short_external_arc(offset=0.):
    """A 25 mm exposed crown next to a clamp; too flat to fit independently."""
    ctx,report,sizes=scene([([0,0,0],[.4,0,0])])
    x,theta=np.meshgrid(np.linspace(.50,.525,31),np.linspace(-.10,.10,9))
    arc=np.c_[x.ravel(),offset+.0025*np.sin(theta.ravel()),.0025*np.cos(theta.ravel())]
    normals=np.c_[np.zeros(x.size),np.sin(theta.ravel()),np.cos(theta.ravel())]
    count=len(ctx.positions);n=len(arc)
    ctx.positions=np.vstack([ctx.positions,arc,arc+[0,0,-.003]])
    ctx.normals=np.vstack([ctx.normals,normals,normals])
    for name,tail in {'refined_class':np.r_[np.full(n,3),np.full(n,2)],
        'refined_zone':np.full(2*n,3),'internal_type':np.r_[np.full(n,4),np.zeros(n)],
        'internal_instance':np.zeros(2*n),'internal_segment':np.zeros(2*n),
        'internal_confidence':np.zeros(2*n)}.items():
        values=getattr(ctx,name);setattr(ctx,name,np.r_[values,tail].astype(values.dtype))
    return ctx,report,slice(count,count+n)


def glued_external_arc(edge_length=.12):
    """A T junction: a short axial crown touches a much longer transverse clamp."""
    ctx,report,arc=short_external_arc()
    x,y=np.meshgrid(np.linspace(.4994,.5006,7),np.linspace(-edge_length/2,edge_length/2,241))
    edge=np.c_[x.ravel(),y.ravel(),np.full(x.size,.0025)]
    start=len(ctx.positions);n=len(edge)
    ctx.positions=np.vstack([ctx.positions,edge,edge+[0,0,-.003]])
    ctx.normals=np.vstack([ctx.normals,np.tile([0.,0.,1.],(2*n,1))])
    for name,tail in {'refined_class':np.r_[np.full(n,3),np.full(n,2)],
        'refined_zone':np.full(2*n,3),'internal_type':np.r_[np.full(n,4),np.zeros(n)],
        'internal_instance':np.zeros(2*n),'internal_segment':np.zeros(2*n),
        'internal_confidence':np.zeros(2*n)}.items():
        values=getattr(ctx,name);setattr(ctx,name,np.r_[values,tail].astype(values.dtype))
    return ctx,report,arc,slice(start,start+n)


class GuidedTests(unittest.TestCase):
    def run_case(self,rods,design,ids=None,zones=None):
        ctx,report,sizes=scene(rods,ids,zones)
        before={name:value.copy() for name,value in vars(ctx).items()}
        result=refine_instances(ctx,report,inventory(design))
        for name,values in before.items():np.testing.assert_array_equal(getattr(ctx,name),values)
        counts=np.bincount(ctx.complete_instance)
        self.assertEqual(sum(result['counts'].values()),len(ctx.positions))
        self.assertEqual(sum(i['pointCount'] for i in result['instances']),np.count_nonzero(ctx.complete_instance))
        for i in result['instances']:self.assertEqual(counts[i['id']],i['pointCount'])
        return ctx,result,sizes

    def test_long_rod_gap_merges_with_displaced_design(self):
        ctx,r,_=self.run_case([([0,0,0],[.35,0,0]),([.55,0,0],[1,0,0])], [([0,.025,0],[1,.025,0])])
        self.assertEqual(r['instanceCount'],1)
        self.assertEqual(r['designReview']['mergedInstances'],1)
        self.assertEqual(len(ctx.positions),2904)

    def test_short_external_arc_is_linked_before_fixture_filter(self):
        ctx,report,arc=short_external_arc()
        r=refine_instances(ctx,report,inventory([([0,0,0],[.55,0,0])]))
        np.testing.assert_array_equal(ctx.complete_class[arc],3)
        np.testing.assert_array_equal(ctx.complete_instance[arc],1)
        self.assertEqual(r['instanceCount'],1)

    def test_low_score_partial_arc_keeps_its_observed_axis_support(self):
        ctx, report, arc = short_external_arc()
        ctx.fused_steel_score = np.ones(len(ctx.positions), np.float32)
        ctx.fused_steel_score[arc] = .25
        refine_instances(ctx, report, inventory([([0, 0, 0], [.55, 0, 0])]))
        np.testing.assert_array_equal(ctx.complete_class[arc], 3)
        np.testing.assert_array_equal(ctx.complete_instance[arc], 1)

    def test_final_unassigned_residual_is_noise_even_with_high_score(self):
        rod = ([0, 0, 0], [.3, 0, 0])
        for mode in ('geometry', 'topology'):
            with self.subTest(mode=mode):
                ctx, report, _ = scene([rod])
                count = len(ctx.positions)
                ctx.positions = np.vstack([ctx.positions, [[.8, .2, .1], [.8, .3, .1], [.8, .4, .1]]])
                ctx.normals = np.vstack([ctx.normals, np.tile([0., 0., 1.], (3, 1))])
                for name in ('refined_class', 'refined_zone', 'internal_type', 'internal_instance',
                             'internal_segment', 'internal_confidence'):
                    values = getattr(ctx, name)
                    fill = 3 if name in ('refined_class', 'refined_zone') else 4 if name == 'internal_type' else 0
                    setattr(ctx, name, np.r_[values, np.full(3, fill, dtype=values.dtype)])
                ctx.fused_steel_score = np.r_[np.ones(count), [.25, .65, 1.]].astype(np.float32)
                before = ctx.positions.copy()
                result = refine_instances(ctx, report, inventory([rod]), mode=mode)
                np.testing.assert_array_equal(ctx.complete_class[count:], 4)
                np.testing.assert_array_equal(ctx.complete_class[:count], 3)
                np.testing.assert_array_equal(ctx.positions, before)
                self.assertEqual(result['designReview']['exteriorDenoising']['removedPointCount'], 2)
                self.assertEqual(result['designReview']['finalUnassignedNoise']['removedPointCount'], 1)
                self.assertEqual(result['designReview']['finalUnassignedNoise']['highScoreRemovedPointCount'], 1)
                self.assertEqual(result['unassignedRebarPointCount'], 0)
                for name in ('complete_instance', 'complete_segment', 'complete_confidence'):
                    np.testing.assert_array_equal(getattr(ctx, name)[count:], 0)

    def test_parallel_but_off_axis_external_arc_is_not_protected(self):
        ctx,report,arc=short_external_arc(offset=.04)
        refine_instances(ctx,report,inventory([([0,0,0],[.55,0,0])]))
        np.testing.assert_array_equal(ctx.complete_class[arc],4)

    def test_glued_transverse_fixture_cannot_veto_the_short_axial_branch(self):
        ctx,report,arc,edge=glued_external_arc()
        r=refine_instances(ctx,report,inventory([([0,0,0],[.55,0,0])]))
        # Away from the actual crossing, the short rod must be recovered whole.
        axial=np.arange(arc.start,arc.stop)[ctx.positions[arc,0]>.507]
        transverse=np.arange(edge.start,edge.stop)[np.abs(ctx.positions[edge,1])>.012]
        np.testing.assert_array_equal(ctx.complete_instance[axial],1)
        np.testing.assert_array_equal(ctx.complete_class[axial],3)
        np.testing.assert_array_equal(ctx.complete_instance[transverse],0)
        np.testing.assert_array_equal(ctx.complete_class[transverse],4)
        self.assertEqual(r['instanceCount'],1)
        self.assertGreater(r['designReview']['separatedExteriorClusters'],0)

    def test_transverse_edge_crossing_an_axis_is_not_itself_an_exterior_rod(self):
        ctx,report,arc,edge=glued_external_arc()
        ctx.refined_class[arc]=2;ctx.internal_type[arc]=0
        refine_instances(ctx,report,inventory([([0,0,0],[.55,0,0])]))
        np.testing.assert_array_equal(ctx.complete_instance[edge],0)
        np.testing.assert_array_equal(ctx.complete_class[edge],4)

    def test_glued_branch_split_survives_rotation_and_reversed_normals(self):
        ctx,report,arc,edge=glued_external_arc()
        axial=np.arange(arc.start,arc.stop)[ctx.positions[arc,0]>.512]
        angle=.71;rotation=np.array([[np.cos(angle),-np.sin(angle),0],
            [np.sin(angle),np.cos(angle),0],[0,0,1.]])
        ctx.positions=ctx.positions@rotation.T;ctx.normals=-ctx.normals@rotation.T
        for part in report['segments']:
            for key in ('startM','endM'):part[key]=(np.array(part[key])@rotation.T).tolist()
        line=[(np.array(p)@rotation.T).tolist() for p in ([0,0,0],[.55,0,0])]
        refine_instances(ctx,report,inventory([line]),workers=2)
        np.testing.assert_array_equal(ctx.complete_instance[axial],1)

    def test_two_short_rods_glued_by_one_edge_keep_distinct_internal_owners(self):
        ctx,report,arc,edge=glued_external_arc()
        points,normals=cylinder([0,.035,0],[.4,.035,0],along=121)
        second=ctx.positions[arc]+[0,.035,0];second_normals=ctx.normals[arc].copy()
        start=len(ctx.positions);n=len(points);m=len(second)
        ctx.positions=np.vstack([ctx.positions,points,second]);ctx.normals=np.vstack([ctx.normals,normals,second_normals])
        for name,tail in {'refined_class':np.full(n+m,3),'refined_zone':np.r_[np.full(n,1),np.full(m,3)],
            'internal_type':np.r_[np.full(n,1),np.full(m,4)],'internal_instance':np.r_[np.full(n,2),np.zeros(m)],
            'internal_segment':np.r_[np.full(n,2),np.zeros(m)],'internal_confidence':np.r_[np.full(n,.9),np.zeros(m)]}.items():
            values=getattr(ctx,name);setattr(ctx,name,np.r_[values,tail].astype(values.dtype))
        report['instances'].append({'id':2,'type':1,'pointCount':n})
        report['segments'].append({'id':2,'instanceId':2,'type':1,'startM':[0,.035,0],
            'endM':[.4,.035,0],'radiusM':.0025,'pointCount':n,'fitMedianErrorM':0.})
        r=refine_instances(ctx,report,inventory([([0,0,0],[.55,0,0]),([0,.035,0],[.55,.035,0])]))
        a=np.arange(arc.start,arc.stop)[ctx.positions[arc,0]>.507]
        b=np.arange(start+n,start+n+m)[second[:,0]>.507]
        np.testing.assert_array_equal(ctx.complete_instance[a],1)
        np.testing.assert_array_equal(ctx.complete_instance[b],2)
        self.assertEqual(r['instanceCount'],2)

    def test_round_transverse_hook_near_fixture_is_not_a_flat_edge(self):
        rods=[([0,0,0],[.4,0,0]),([.5,0,0],[.65,0,0]),([.65,0,0],[.65,.035,0])]
        ctx,report,sizes=scene(rods,ids=[1,0,0],zones=[1,3,3])
        points=ctx.positions[-sizes[-1]:].copy();normals=ctx.normals[-sizes[-1]:].copy();n=len(points)
        ctx.positions=np.vstack([ctx.positions,points+[0,0,-.003]]);ctx.normals=np.vstack([ctx.normals,normals])
        for name,value in {'refined_class':2,'refined_zone':3,'internal_type':0,
            'internal_instance':0,'internal_segment':0,'internal_confidence':0}.items():
            values=getattr(ctx,name);setattr(ctx,name,np.r_[values,np.full(n,value)].astype(values.dtype))
        refine_instances(ctx,report,inventory([([0,0,0],[.7,0,0])]))
        np.testing.assert_array_equal(ctx.complete_class[:sum(sizes)],3)
        np.testing.assert_array_equal(ctx.complete_instance[:sum(sizes)],1)

    def test_short_tip_with_weak_normals_keeps_the_supported_parent(self):
        ctx,report,arc=short_external_arc()
        other=([0,.08,0],[.4,.08,0]);points,normals=cylinder(*other,along=121);n=len(points)
        ctx.positions=np.vstack([ctx.positions,points]);ctx.normals=np.vstack([ctx.normals,normals])
        for name,value in {'refined_class':3,'refined_zone':1,'internal_type':1,
            'internal_instance':2,'internal_segment':2,'internal_confidence':.9}.items():
            values=getattr(ctx,name);setattr(ctx,name,np.r_[values,np.full(n,value)].astype(values.dtype))
        report['instances'].append({'id':2,'type':1,'pointCount':n})
        report['segments'].append({'id':2,'instanceId':2,'type':1,'startM':other[0],
            'endM':other[1],'radiusM':.0025,'pointCount':n,'fitMedianErrorM':0.})
        ctx.normals[arc][20:]=[1,0,0]
        r=refine_instances(ctx,report,inventory([([0,0,0],[.55,0,0]),other]))
        np.testing.assert_array_equal(ctx.complete_instance[arc],1)
        owners={s['id']:s['instanceId'] for s in r['segments']}
        self.assertEqual({owners[int(s)] for s in ctx.complete_segment[arc]},{1})

    def test_mixed_fusion_scores_do_not_split_an_observed_hook_near_fixture(self):
        rods = [([0, 0, 0], [.4, 0, 0]), ([.5, 0, 0], [.65, 0, 0]),
                ([.65, 0, 0], [.65, .035, 0])]
        ctx, report, sizes = scene(rods, ids=[1, 0, 0], zones=[1, 3, 3])
        steel_count = len(ctx.positions)
        fixture = ctx.positions[-sizes[-1]:] + [0, 0, -.003]
        ctx.positions = np.vstack([ctx.positions, fixture])
        ctx.normals = np.vstack([ctx.normals, ctx.normals[-sizes[-1]:]])
        for name, value in {'refined_class': 2, 'refined_zone': 3, 'internal_type': 0,
                            'internal_instance': 0, 'internal_segment': 0, 'internal_confidence': 0}.items():
            values = getattr(ctx, name)
            setattr(ctx, name, np.r_[values, np.full(len(fixture), value)].astype(values.dtype))
        ctx.fused_steel_score = np.zeros(len(ctx.positions), np.float32)
        ctx.fused_steel_score[:steel_count] = np.resize([1., .65, .25], steel_count)
        scores = ctx.fused_steel_score.copy()
        refine_instances(ctx, report, inventory([([0, 0, 0], [.7, 0, 0])]))
        np.testing.assert_array_equal(ctx.complete_class[:steel_count], 3)
        np.testing.assert_array_equal(ctx.complete_instance[:steel_count], 1)
        np.testing.assert_array_equal(ctx.complete_class[steel_count:], 2)
        np.testing.assert_array_equal(ctx.fused_steel_score, scores)

    def test_early_extension_follows_later_interior_instance_merge(self):
        rods=[([0,0,0],[.15,0,0]),([.2,0,0],[.4,0,0]),([.5,0,0],[.525,0,0])]
        ctx,r,sizes=self.run_case(rods,[([0,0,0],[.55,0,0])],ids=[1,2,0],zones=[1,1,3])
        np.testing.assert_array_equal(ctx.complete_instance,1)
        owners={s['id']:s['instanceId'] for s in r['segments']}
        self.assertEqual({owners[int(s)] for s in ctx.complete_segment},{1})

    def test_interior_and_exterior_share_identity_across_wide_fixture(self):
        rods=[([0,0,0],[.4,0,0]),([.95,.003,0],[1.4,.003,0])]
        ctx,r,sizes=self.run_case(rods,[([0,.02,0],[1.4,.02,0])],ids=[1,0],zones=[1,3])
        self.assertEqual(r['instanceCount'],1)
        self.assertEqual(r['designReview']['acrossFixtureMerges'],1)
        self.assertTrue(np.all(ctx.complete_instance==1))

    def test_overlapping_surface_fragments_of_same_cylinder_merge(self):
        rods=[([0,0,0],[.6,0,0]),([.5,0,0],[1,0,0])]
        _,r,_=self.run_case(rods,[([0,.02,0],[1,.02,0])])
        self.assertEqual(r['instanceCount'],1)

    def test_density_supported_endpoint_ignores_sparse_transverse_tail(self):
        rods=[([0,0,0],[.42,0,0]),([.46,0,0],[1,0,0])]
        ctx,report,sizes=scene(rods)
        # A few wrong assignments on a crossing bar used to create apparent
        # axial overlap and block the otherwise continuous long rod.
        ctx.positions[:18]=np.c_[np.linspace(.5,.68,18),np.full(18,.006),np.zeros(18)]
        r=refine_instances(ctx,report,inventory([([0,0,0],[1,0,0])]))
        self.assertEqual(r['instanceCount'],1)

    def test_count_competition_reserves_slot_for_long_supported_rod(self):
        rods=[([0,.018,0],[1,.018,0]),([.2,0,0],[.25,0,0])]
        _,r,_=self.run_case(rods,[([0,0,0],[1,0,0])])
        matched=[i for i in r['instances'] if i['designUnitId']]
        self.assertEqual([i['id'] for i in matched],[1])
        self.assertEqual(r['instanceCount'],1)

    def test_straight_web_gap_merges_without_merging_other_diagonal(self):
        rods=[([0,0,0],[.07,0,.07]),([.11,0,.11],[.18,0,.18]),([.18,0,.18],[.36,0,0])]
        _,r,_=self.run_case(rods,[([0,.01,0],[.18,.01,.18]),([.18,.01,.18],[.36,.01,0])])
        self.assertEqual(r['instanceCount'],2)

    def test_close_parallel_mixture_splits_and_one_arc_does_not(self):
        rods=[([0,-.004,0],[.24,-.004,0]),([0,.004,0],[.24,.004,0])]
        ctx,r,sizes=self.run_case(rods,rods,ids=[1,1])
        self.assertEqual(r['instanceCount'],2)
        self.assertEqual(r['designReview']['splitInstances'],1)
        self.assertNotEqual(np.median(ctx.complete_instance[:sizes[0]]),np.median(ctx.complete_instance[sizes[0]:]))
        _,single,_=self.run_case([([0,0,0],[.24,0,0])],rods)
        self.assertEqual(single['instanceCount'],1)

    def test_overlap_is_not_merged_to_meet_design_count(self):
        rods=[([0,0,0],[.5,0,0]),([0,.008,0],[.5,.008,0])]
        _,r,_=self.run_case(rods,[rods[0]])
        self.assertEqual(r['instanceCount'],2)
        self.assertEqual(r['designReview']['mergedInstances'],0)

    def test_misplaced_short_rod_and_missing_design_are_preserved(self):
        rods=[([0,.24,0],[.28,.24,0])]
        ctx,r,_=self.run_case(rods,[([0,0,0],[.28,0,0]),([0,1,0],[.28,1,0])])
        self.assertEqual(r['instanceCount'],1)
        np.testing.assert_array_equal(ctx.complete_class,3)
        self.assertGreaterEqual(r['designReview']['unobservedUnits'],1)

    def test_noise_is_never_recovered_even_on_design_centerline(self):
        rod=([0,0,0],[.3,0,0]);ctx,report,_=scene([rod])
        ctx.internal_type[:100]=5;ctx.internal_instance[:100]=0;ctx.internal_segment[:100]=0
        refine_instances(ctx,report,inventory([rod]))
        np.testing.assert_array_equal(ctx.complete_class[:100],4)
        np.testing.assert_array_equal(ctx.complete_instance[:100],0)

    def test_high_fused_support_blocks_internal_noise_mapping(self):
        rod=([0,0,0],[.3,0,0]);ctx,report,_=scene([rod])
        ctx.internal_type[:100]=5;ctx.internal_instance[:100]=0;ctx.internal_segment[:100]=0
        ctx.fused_steel_score=np.zeros(len(ctx.positions),np.float32)
        ctx.fused_steel_score[:100]=1
        result=refine_instances(ctx,report,inventory([rod]))
        np.testing.assert_array_equal(ctx.complete_class[:100],3)
        self.assertGreaterEqual(result['designReview']['blockedNoisePoints'],100)
        self.assertEqual(result['designReview']['protectionThreshold'],.9)

    def test_spatially_rejected_high_scores_are_not_resurrected_by_design(self):
        rod=([0,0,0],[.3,0,0])
        for mode in ('geometry', 'topology'):
            ctx,report,_=scene([rod])
            ctx.internal_type[:100]=5;ctx.internal_instance[:100]=0;ctx.internal_segment[:100]=0
            ctx.internal_confidence[:100]=0
            ctx.fused_steel_score=np.ones(len(ctx.positions),np.float32)
            report['denoising']={'highScoreOverrideAllowed':True}
            refine_instances(ctx,report,inventory([rod]),mode=mode)
            np.testing.assert_array_equal(ctx.complete_class[:100],4)
            np.testing.assert_array_equal(ctx.complete_instance[:100],0)
            np.testing.assert_array_equal(ctx.complete_class[100:],3)
            np.testing.assert_array_equal(ctx.fused_steel_score,1)

    def test_step05_cloth_boundary_cannot_be_undone_by_high_scores(self):
        rod=([0,0,0],[.3,0,0]);ctx,report,_=scene([rod])
        ctx.internal_type[:100]=5;ctx.internal_instance[:100]=0;ctx.internal_segment[:100]=0
        ctx.internal_confidence[:100]=0
        ctx.fused_steel_score=np.ones(len(ctx.positions),np.float32)
        report['denoising']={'designBoundaryAppliesToAllSteel': True}
        refine_instances(ctx,report,inventory([rod]))
        np.testing.assert_array_equal(ctx.complete_class[:100], 4)
        np.testing.assert_array_equal(ctx.complete_instance[:100], 0)
        np.testing.assert_array_equal(ctx.complete_class[100:], 3)

    def test_round_external_rod_gets_observed_identity(self):
        rod=([0,0,0],[.3,0,0]);ctx,r,_=self.run_case([rod],[rod],ids=[0],zones=[3])
        self.assertEqual(r['instanceCount'],1)
        self.assertTrue(np.all(ctx.complete_instance>0))

    def test_long_parallel_rods_are_not_exempt_from_mixture_split(self):
        rods=[([0,-.004,0],[2,-.004,0]),([0,.004,0],[2,.004,0])]
        _,r,_=self.run_case(rods,rods,ids=[1,1])
        self.assertEqual(r['instanceCount'],2)
        self.assertEqual(r['designReview']['splitInstances'],1)

    def test_gradual_bend_uses_local_tangents_across_a_gap(self):
        rods=[([0,0,0],[.2,.002,0]),([.2,.002,0],[.4,.008,0]),
              ([.6,.018,0],[.8,.032,0]),([.8,.032,0],[1,.05,0])]
        _,r,_=self.run_case(rods,[([0,.01,0],[1,.01,0])],ids=[1,1,2,2])
        self.assertEqual(r['instanceCount'],1)

    def test_two_sides_of_one_bowed_rod_remain_one_instance(self):
        rod=([0,0,0],[2,0,0]);ctx,report,_=scene([rod])
        ctx.positions[:,1]+=.009*np.cos(ctx.positions[:,0]*np.pi)
        design=inventory([([0,-.004,0],[2,-.004,0]),([0,.004,0],[2,.004,0])])
        r=refine_instances(ctx,report,design)
        self.assertEqual(r['instanceCount'],1)
        self.assertEqual(r['designReview']['splitInstances'],0)

    def test_perpendicular_normals_alone_do_not_prove_round_steel(self):
        rod=([0,0,0],[.3,0,0]);ctx,report,_=scene([rod],ids=[0],zones=[3])
        ctx.normals[:]=[0,0,1]
        r=refine_instances(ctx,report,inventory([rod]))
        self.assertEqual(r['instanceCount'],0)
        np.testing.assert_array_equal(ctx.complete_class,4)

    def test_equal_cylinder_surface_competition_stays_pending(self):
        from algorithms.internal_rebar import assign_cylinders, InternalRebarParameters
        models=[{'center':np.array([0.,y,0.]),'axis':np.array([1.,0,0]),'low':-.1,'high':.1,
                 'radius':.004,'instanceId':i+1} for i,y in enumerate((-.004,.004))]
        points=np.array([[0.,0.,0.]])
        labels,_,cache=assign_cylinders(points,np.array([[0.,1.,0.]]),models,instance_margin=.0005)
        self.assertEqual(labels[0],0)
        self.assertTrue(cache['ambiguous'][0])
        models[1]['instanceId']=1
        labels,_,cache=assign_cylinders(points,np.array([[0.,1.,0.]]),models,instance_margin=.0005)
        self.assertGreater(labels[0],0)
        self.assertFalse(cache['ambiguous'][0])

    def test_unused_old_fit_cannot_reclaim_exterior_points(self):
        rod=([0,0,0],[.3,0,0]);loose=([.14,.2,0],[.15,.2,0])
        ctx,report,sizes=scene([rod,loose],ids=[1,0],zones=[1,3])
        report['segments'].append({'id':3,'instanceId':1,'type':1,'startM':[0,.2,0],
            'endM':[.3,.2,0],'radiusM':.0025,'pointCount':0,'fitMedianErrorM':0.})
        r=refine_instances(ctx,report,inventory([rod]))
        np.testing.assert_array_equal(ctx.complete_instance[sizes[0]:],0)
        self.assertNotIn(3,[part['id'] for part in r['segments']])

    def test_fixture_edge_requires_positive_fixture_evidence(self):
        x,y=np.meshgrid(np.linspace(0,.12,121),np.linspace(-.0008,.0008,9));edge=np.c_[x.ravel(),y.ravel(),np.zeros(x.size)]
        fixture=edge+np.array([0,0,-.003]);p=np.vstack([edge,fixture]);n=len(edge)
        ctx=SimpleNamespace(positions=p,normals=np.tile([0.,0.,1.],(len(p),1)),refined_class=np.r_[np.full(n,3),np.full(n,2)].astype(np.uint8),
            refined_zone=np.full(len(p),3,np.uint8),internal_type=np.zeros(len(p),np.uint8),internal_instance=np.zeros(len(p),np.uint32),internal_segment=np.zeros(len(p),np.uint32),internal_confidence=np.zeros(len(p),np.float32))
        r=refine_instances(ctx,{'instances':[],'segments':[]},inventory([([0,0,0],[.3,0,0])]))
        np.testing.assert_array_equal(ctx.complete_class[:n],4)
        np.testing.assert_array_equal(ctx.complete_class[n:],2)
        self.assertEqual(r['designReview']['filteredPoints'],n)

    def test_high_fused_support_survives_fixture_edge_rejection(self):
        x,y=np.meshgrid(np.linspace(0,.12,121),np.linspace(-.0008,.0008,9));edge=np.c_[x.ravel(),y.ravel(),np.zeros(x.size)]
        fixture=edge+np.array([0,0,-.003]);p=np.vstack([edge,fixture]);n=len(edge)
        score=np.r_[np.ones(n),np.zeros(n)].astype(np.float32)
        ctx=SimpleNamespace(positions=p,normals=np.tile([0.,0.,1.],(len(p),1)),
            refined_class=np.r_[np.full(n,3),np.full(n,2)].astype(np.uint8),
            refined_zone=np.full(len(p),3,np.uint8),internal_type=np.zeros(len(p),np.uint8),
            internal_instance=np.zeros(len(p),np.uint32),internal_segment=np.zeros(len(p),np.uint32),
            internal_confidence=np.zeros(len(p),np.float32),fused_steel_score=score)
        result=refine_instances(ctx,{'instances':[],'segments':[]},inventory([([0,0,0],[.3,0,0])]))
        np.testing.assert_array_equal(ctx.complete_class[:n],3)
        np.testing.assert_array_equal(ctx.complete_class[n:],2)
        self.assertEqual(result['designReview']['blockedNoisePoints'],n)
        self.assertEqual(result['designReview']['filteredPoints'],0)

    def test_old_instance_identity_does_not_protect_fixture_edge(self):
        rod=([0,0,0],[.3,0,0]);ctx,report,sizes=scene([rod])
        # A narrow curved clamp strip can have a small circle-fit residual.
        x,y=np.meshgrid(np.linspace(0,.2,121),np.linspace(-.002,.002,12))
        strip=np.c_[x.ravel(),y.ravel(),(.0001*(y/.002)**2).ravel()]
        n=len(strip);fixture=strip+[0,.003,0]
        ctx.positions=np.vstack([strip,fixture]);ctx.normals=np.tile([0.,0.,1.],(2*n,1))
        for key in ('refined_class','refined_zone','internal_type','internal_instance','internal_segment','internal_confidence'):
            values=np.zeros(2*n,dtype=getattr(ctx,key).dtype)
            values[:n]=3 if key=='refined_class' else 1
            values[n:]=2 if key=='refined_class' else 0
            setattr(ctx,key,values)
        r=refine_instances(ctx,report,inventory([rod]))
        self.assertEqual(r['instanceCount'],0)
        self.assertTrue(np.all(ctx.complete_class[:n]==4))
        self.assertTrue(np.all(ctx.complete_instance==0))


if __name__=='__main__':unittest.main()
