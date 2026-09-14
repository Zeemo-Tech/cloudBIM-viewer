from copy import deepcopy
from types import SimpleNamespace
import unittest
import numpy as np
from algorithms.rebar_extension import extend_rebar_instances
from test_internal_rebar import cylinder, model


def scene(gap=False, flip=False):
    samples = [cylinder((-.1, 0, .05), (.1, 0, .05), radius=.004),
               cylinder((.17, 0, .05), (.26, 0, .05), radius=.004),
               cylinder((.35, 0, .05), (.44, 0, .05), radius=.004),
               cylinder((.17, .025, .05), (.26, .025, .05), radius=.004),
               cylinder((.17, 0, .05), (.26, 0, .05), radius=.004)]
    points = np.vstack([s[0] for s in samples]); normals = np.vstack([s[1] for s in samples])
    n = len(samples[0][0]); m = model((-.1, 0, .05), (.1, 0, .05), radius=.004); m['instanceId'] = 1
    labels = np.zeros(len(points), np.uint32); labels[:n] = 1
    classes = np.full(len(points), 3, np.uint8); classes[4*n:] = 2; classes[-1] = 1
    zones = np.full(len(points), 3, np.uint8); zones[:n] = 1
    c = SimpleNamespace(positions=points, normals=-normals if flip else normals,
        refined_class=classes, refined_zone=zones, internal_instance=labels.copy(), internal_segment=labels.copy(),
        internal_confidence=labels.astype(np.float32), internal_rebar_cache={'models':[m]},
        region_cache={'frame_axes':np.eye(2), 'frame_bounds_local':np.array([-.16,.16,-.1,.1]),
                      'frame_inner_bounds_local':np.array([-.1,.1,-.05,.05])})
    report = {'segments':[{'id':1,'instanceId':1,'startM':[-.1,0,.05],'endM':[.1,0,.05],'pointCount':n}],
              'instances':[{'id':1,'lengthM':.2,'pointCount':n,'segmentIds':[1]}]}
    if gap: c.region_cache['frame_bounds_local'][1] = .11
    return c, report, n


class RebarExtensionTests(unittest.TestCase):
    def test_high_score_remote_points_survive_legacy_exterior_rejection(self):
        c, report, n = scene()
        c.fused_steel_score = np.full(len(c.positions), .25, np.float32)
        c.fused_steel_score[2*n:3*n] = 1
        extend_rebar_instances(c, report)
        np.testing.assert_array_equal(c.complete_class[2*n:3*n], 3)
        np.testing.assert_array_equal(c.complete_class[3*n:4*n], 4)

    def test_removed_segment_leaves_stable_noncontiguous_ids(self):
        c, report, n = scene()
        unused = deepcopy(c.internal_rebar_cache['models'][0])
        unused['instanceId'] = 9
        c.internal_rebar_cache['models'].insert(0, unused)
        c.internal_segment[:n] = 2
        report['segments'][0]['id'] = 2
        report['instances'][0]['segmentIds'] = [2]
        result = extend_rebar_instances(c, report)
        np.testing.assert_array_equal(c.complete_segment[:2*n], 2)
        self.assertEqual(result['segments'][0]['id'], 2)

    def test_bridge_frame_reuse_id_reject_remote_and_offset_noise_protect_static(self):
        c, report, n = scene(); before = deepcopy(report); classes = c.refined_class.copy()
        result = extend_rebar_instances(c, report, workers=2)
        np.testing.assert_array_equal(c.complete_instance[:2*n], 1)
        np.testing.assert_array_equal(c.complete_class[2*n:4*n], 4)
        np.testing.assert_array_equal(c.complete_class[4*n:], classes[4*n:])
        np.testing.assert_array_equal(c.refined_class, classes)
        np.testing.assert_array_equal(c.complete_instance[:n], c.internal_instance[:n])
        self.assertEqual(report, before)
        self.assertEqual(result['matchedExteriorPointCount'], n)
        self.assertAlmostEqual(result['segments'][0]['endM'][0], .26)
        self.assertAlmostEqual(result['instances'][0]['lengthM'], .36)
        self.assertEqual(sum(result['counts'].values()), len(c.positions))
        self.assertEqual(result['segments'][0]['pointCount'], 2*n)

    def test_no_general_gap_jumping(self):
        c, report, n = scene(gap=True)
        result = extend_rebar_instances(c, report)
        self.assertEqual(result['matchedExteriorPointCount'], 0)
        np.testing.assert_array_equal(c.complete_class[n:4*n], 4)

    def test_normal_sign_and_workers_do_not_change_ownership(self):
        c, report, _ = scene(); other, _, _ = scene(flip=True)
        extend_rebar_instances(c, report, workers=1); extend_rebar_instances(other, report, workers=3)
        for name in ('complete_class','complete_instance','complete_segment','complete_confidence'):
            np.testing.assert_array_equal(getattr(c,name),getattr(other,name))

    def test_no_seeds_or_no_frame_does_not_label_all_exterior_noise(self):
        for missing in ('seeds','frame'):
            c, report, _ = scene()
            if missing == 'seeds':
                c.internal_rebar_cache['models'] = []; c.internal_instance[:] = 0; c.internal_segment[:] = 0
                report = {'instances':[], 'segments':[]}
            else: c.region_cache = {}
            result = extend_rebar_instances(c, report)
            self.assertFalse(result['enabled']); self.assertEqual(result['rejectedExteriorPointCount'], 0)
            np.testing.assert_array_equal(c.complete_class, c.refined_class)

    def test_axis_facing_surfaces_are_rejected(self):
        c, report, n = scene(); c.normals[n:2*n] = [1,0,0]
        result = extend_rebar_instances(c, report)
        self.assertEqual(result['matchedExteriorPointCount'], 0)

    def test_parallel_instances_keep_separate_ids(self):
        c, report, n = scene()
        second = deepcopy(c.internal_rebar_cache['models'][0]); second['center'][1] = .025; second['instanceId'] = 2
        c.internal_rebar_cache['models'].append(second)
        report['segments'].append(dict(report['segments'][0], id=2, instanceId=2))
        report['instances'].append(dict(report['instances'][0], id=2, segmentIds=[2]))
        c.internal_instance[0] = 2; c.internal_segment[0] = 2
        extend_rebar_instances(c, report)
        np.testing.assert_array_equal(c.complete_instance[n:2*n], 1)
        np.testing.assert_array_equal(c.complete_instance[3*n:4*n], 2)

def append_steel(context, start, end, along=60):
    points, normals, _ = cylinder(start, end, radius=.004, along=along)
    n = len(context.positions)
    context.positions = np.vstack((context.positions, points))
    context.normals = np.vstack((context.normals, normals))
    for name, value in [('refined_class',3),('refined_zone',3),('internal_instance',0),('internal_segment',0),('internal_confidence',0)]:
        old = getattr(context,name)
        setattr(context,name,np.r_[old,np.full(len(points),value,dtype=old.dtype)])
    return slice(n,n+len(points))


class ClusterPreservationTests(unittest.TestCase):
    def assert_whole_clusters(self, context):
        for cluster in np.unique(context.complete_cluster):
            if not cluster:
                continue
            keep = context.complete_cluster == cluster
            self.assertEqual(len(np.unique(context.complete_class[keep])),1)
            self.assertEqual(len(np.unique(context.complete_instance[keep])),1)

    def test_hook_and_weak_normal_points_are_kept_with_attached_cluster(self):
        c, report, n = scene()
        hook = append_steel(c,(.26,0,.05),(.26,.10,.05))
        c.normals[hook] = 0  # No axial normal agreement on the hook is required.
        result = extend_rebar_instances(c,report)
        np.testing.assert_array_equal(c.complete_class[hook],3)
        np.testing.assert_array_equal(c.complete_instance[hook],1)
        self.assertEqual(c.complete_cluster[n],c.complete_cluster[hook.start])
        self.assertGreaterEqual(result['clusterCarriedPointCount'],hook.stop-hook.start)
        self.assert_whole_clusters(c)

    def test_unmatched_hook_cluster_is_rejected_whole(self):
        c, report, n = scene(gap=True)
        hook = append_steel(c,(.26,0,.05),(.26,.10,.05))
        extend_rebar_instances(c,report)
        np.testing.assert_array_equal(c.complete_class[hook],4)
        self.assert_whole_clusters(c)

    def test_cluster_touching_two_instances_remains_whole_and_pending(self):
        c, report, n = scene()
        second = deepcopy(c.internal_rebar_cache['models'][0]); second['center'][1] = .025; second['instanceId'] = 2
        c.internal_rebar_cache['models'].append(second)
        report['segments'].append(dict(report['segments'][0],id=2,instanceId=2))
        report['instances'].append(dict(report['instances'][0],id=2,segmentIds=[2]))
        c.internal_instance[0]=2; c.internal_segment[0]=2
        hook=append_steel(c,(.26,0,.05),(.26,.025,.05),along=20)
        result=extend_rebar_instances(c,report)
        for selected in [slice(n,2*n),slice(3*n,4*n),hook]:
            np.testing.assert_array_equal(c.complete_class[selected],3)
            np.testing.assert_array_equal(c.complete_instance[selected],0)
        self.assertEqual(result['ambiguousClusterCount'],1)
        self.assert_whole_clusters(c)


if __name__ == '__main__': unittest.main()
