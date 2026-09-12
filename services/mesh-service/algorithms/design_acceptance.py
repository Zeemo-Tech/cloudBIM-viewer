"""Independent acceptance from owned source points, never nominal segment endpoints."""
from dataclasses import asdict
import numpy as np
from scipy.optimize import least_squares
from .design_evidence_contract import AcceptancePolicy

VERSION = 'observed-design-acceptance-v1'

def observed_geometry(points):
    p = np.asarray(points, float)
    if len(p) < 12:
        return None
    # Coordinate ordering makes sampling invariant to source record order.
    p = p[np.lexsort(p.T[::-1])]
    sample = p[np.linspace(0, len(p)-1, min(len(p), 4096), dtype=int)]
    center = np.mean(sample, axis=0)
    _, axes = np.linalg.eigh((sample-center).T @ (sample-center))
    axis = axes[:, -1]
    if axis[np.argmax(np.abs(axis))] < 0: axis = -axis
    helper = np.eye(3)[np.argmin(np.abs(axis))]
    u = np.cross(axis, helper); u /= np.linalg.norm(u); v = np.cross(axis, u)
    basis = np.array([u,v]); q = (sample-center)@basis.T
    initial, *_ = np.linalg.lstsq(np.c_[2*q, np.ones(len(q))], (q*q).sum(1), rcond=None)
    radius = np.sqrt(max(float(initial[2]+initial[:2]@initial[:2]), 1e-8))
    fit = least_squares(lambda x: np.linalg.norm(q-x[:2],axis=1)-x[2],
                        [*initial[:2],np.clip(radius,.0001001,.099999)], bounds=([-np.inf,-np.inf,.0001],[np.inf,np.inf,.1]),
                        loss='soft_l1', f_scale=.0005, max_nfev=40)
    center += fit.x[:2]@basis
    t = (p-center)@axis
    # Robust occupied source extent. Nominal/fitted endpoints are never read.
    low, high = np.quantile(t,[.001,.999])
    radial = q-fit.x[:2]
    angles = np.sort(np.mod(np.arctan2(radial[:,1],radial[:,0]),2*np.pi))
    arc = 2*np.pi-np.max(np.diff(np.r_[angles,angles[0]+2*np.pi]))
    return dict(center=center,axis=axis,start=center+low*axis,end=center+high*axis,
                lengthM=float(high-low),diameterM=float(2*fit.x[2]),
                surfaceErrorM=float(np.median(np.abs(fit.fun))),arcDegrees=float(np.rad2deg(arc)),
                pointCount=len(p))

def evaluate_acceptance(positions, owners, classes, instances, inventory, *, policy=None,
                        elapsed_s=None, baseline_s=None):
    policy = policy or AcceptancePolicy()
    positions,owners,classes = np.asarray(positions),np.asarray(owners),np.asarray(classes)
    if owners.shape != (len(positions),) or classes.shape != owners.shape:
        raise ValueError('Acceptance source arrays must align')
    units = {u['designUnitId']:u for u in inventory.get('units',[])}
    records = {int(i['id']):i for i in instances}
    rows = np.flatnonzero((owners>0)&(classes==3)); order = rows[np.argsort(owners[rows],kind='stable')]
    ids, offsets = np.unique(owners[order], return_index=True)
    geometries={}; result=[]; mapped={uid:[] for uid in units}
    for index, owner in enumerate(ids):
        owner=int(owner); source=order[offsets[index]:offsets[index+1] if index+1<len(ids) else len(order)]
        g=observed_geometry(positions[source]); item=records.get(owner,{})
        uid=item.get('designUnitId'); unit=units.get(uid); reasons=[]
        row=dict(instanceId=owner,designUnitId=uid,pointCount=len(source),reportedLengthM=item.get('lengthM'))
        if unit is None: reasons.append('unmatched_design_unit')
        else: mapped[uid].append(owner)
        if g is None: reasons.append('insufficient_observation')
        else:
            row.update({k:(value.tolist() if isinstance(value,np.ndarray) else value) for k,value in g.items()})
            if unit is not None:
                start,end=np.array(unit['startM']),np.array(unit['endM']); d=end-start; d/=np.linalg.norm(d)
                angle=float(np.rad2deg(np.arccos(np.clip(abs(d@g['axis']),0,1))))
                midpoint=(start+end)/2; delta=g['center']-midpoint
                offset=float(np.linalg.norm(delta-(delta@d)*d))
                length_error=abs(g['lengthM']-unit['lengthM'])
                row.update(lengthErrorM=length_error,positionErrorM=offset,angleErrorDegrees=angle,
                           designLengthM=unit['lengthM'],designDiameterM=unit['diameterM'],kind=unit['kind'])
                if length_error>max(policy.length_absolute_m,policy.length_relative*unit['lengthM']):reasons.append('length')
                if angle>policy.angle_degrees:reasons.append('direction')
                if abs(g['diameterM']-unit['diameterM'])>policy.diameter_m:reasons.append('diameter')
                if g['surfaceErrorM']>.0015 or g['arcDegrees']<20:reasons.append('unsupported_surface')
                if unit['kind']=='short':
                    if abs(g['center'][2]-midpoint[2])>policy.layer_m:reasons.append('short_layer')
                elif offset>policy.position_m:reasons.append('position')
                geometries[uid]=g
        row.update(passed=not reasons,reasons=reasons);result.append(row)
    missing=[uid for uid, values in mapped.items() if not values]
    duplicates=[uid for uid, values in mapped.items() if len(values)>1]
    topology=[]; checked=set()
    for edge in inventory.get('relations',[]):
        a,b=edge['from'],edge['to']; key=(min(a,b),max(a,b),edge['kind'])
        if key in checked or a not in geometries or b not in geometries:continue
        checked.add(key)
        if units[a]['kind']=='short' or units[b]['kind']=='short':continue
        ga,gb=geometries[a],geometries[b];kind=edge['kind'];failure=None
        if kind=='parallel':
            da=ga['axis'];delta=gb['center']-ga['center'];dist=np.linalg.norm(delta-(delta@da)*da)
            if abs(dist-edge['distanceM'])>2*policy.position_m:failure='parallel_spacing'
        elif kind=='crossing' and edge.get('order') in ('above','below'):
            # Height at the actual XY crossing, not the midpoint of inclined bars.
            mat=np.column_stack((ga['axis'][:2],-gb['axis'][:2]))
            if abs(np.linalg.det(mat))>1e-8:
                t,s=np.linalg.solve(mat,gb['center'][:2]-ga['center'][:2])
                dz=(gb['center']+s*gb['axis'])[2]-(ga['center']+t*ga['axis'])[2]
                if (edge['order']=='above' and dz<=0) or (edge['order']=='below' and dz>=0):failure='crossing_order'
            else:failure='missing_crossing'
        elif kind=='next':
            distance=min(np.linalg.norm(x-y) for x in (ga['start'],ga['end']) for y in (gb['start'],gb['end']))
            if distance>2*policy.length_absolute_m:failure='disconnected_web'
        if failure:topology.append({'from':a,'to':b,'reason':failure})
    performance=None if elapsed_s is None or baseline_s is None else elapsed_s<=baseline_s*policy.max_time_ratio
    failed=sum(not r['passed'] for r in result)
    passed=bool(units) and len(ids)==len(units) and not missing and not duplicates and not failed and not topology
    geometry_passed=passed
    if performance is False:passed=False
    return dict(version=VERSION,status=('passed' if performance is True else 'pending_performance') if passed else 'failed',geometryPassed=geometry_passed,
                policy=asdict(policy),expectedInstances=len(units),observedInstances=len(ids),missingUnits=missing,
                duplicateUnits=duplicates,failedInstances=failed,topologyFailures=topology,instances=result,
                performancePassed=performance,elapsedS=elapsed_s,baselineS=baseline_s,
                lengthMeaning='0.1–99.9 percentile span of owned source points on independently observed axis; no inferred endpoint extension',
                shortException='same-layer translation and local adjacency only')
