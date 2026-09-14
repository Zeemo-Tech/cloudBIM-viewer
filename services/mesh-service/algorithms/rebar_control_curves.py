"""Bounded parametric bends attached to already identified rebar bodies.

A join has a tangency extent and a rounded-corner fraction; a terminal has a
local bend frame, location and radius. Body identities and body axes stay fixed.
Only continuous, non-planar tube evidence can acquire previously unowned points.
"""
from __future__ import annotations

import math
import time
import multiprocessing
from concurrent.futures import ProcessPoolExecutor
from contextlib import ExitStack
import numpy as np
from scipy.optimize import least_squares
from scipy.spatial.distance import cdist


def _unit(v):
    return v / max(float(np.linalg.norm(v)), 1e-12)


def _length(curve):
    return float(np.linalg.norm(np.diff(curve, axis=0), axis=1).sum())


class _CurveResidual:
    """Reuse closest points for the Jacobian instead of repeating the search.

    At a closest segment point its sliding-coordinate derivative cancels in
    the distance derivative. Only the small curve's vertex derivatives remain;
    those are differenced without repeating any point-to-curve projections.
    """
    def __init__(self, model, sample, normals, weights, radius, prior, prior_scale, scale):
        self.model=model
        self.weights=weights
        self.prior=prior
        self.prior_scale=prior_scale
        self.prior_weight=scale*math.sqrt(len(sample))*.03/prior_scale
        valid=np.zeros(len(sample),bool)
        if normals is not None:
            lengths=np.linalg.norm(normals,axis=1)
            valid=np.isfinite(normals).all(axis=1)&(lengths>.5)
            offset=radius*normals[valid]/lengths[valid,None]
        else:
            offset=np.empty((0,3))
        self.valid=valid
        self.query=np.vstack((sample[~valid],sample[valid]+offset,sample[valid]-offset))
        self.n_invalid=int((~valid).sum())
        self.n_valid=int(valid.sum())
        self.radius_offset=np.where(valid,0.,radius)
        self.cached=None

    def fun(self, parameters, data_scale):
        key=(data_scale,parameters.tobytes())
        if self.cached is not None and self.cached[0]==key:
            return self.cached[1]
        curve=self.model(parameters)
        starts,delta=curve[:-1],np.diff(curve,axis=0)
        square=np.einsum('ij,ij->i',delta,delta)
        valid_segments=np.flatnonzero(square>1e-20)
        starts,delta,square=starts[valid_segments],delta[valid_segments],square[valid_segments]
        along=self.query@delta.T-np.einsum('ij,ij->i',starts,delta)
        fractions=np.clip(along/square,0.,1.)
        squares=cdist(self.query,starts,'sqeuclidean')-2*fractions*along+fractions*fractions*square
        nearest=squares.argmin(axis=1)
        at=np.arange(len(self.query))
        distance=np.sqrt(np.maximum(0.,squares[at,nearest]))
        chosen=np.empty(len(self.weights),int)
        chosen[~self.valid]=np.arange(self.n_invalid)
        plus=self.n_invalid+np.arange(self.n_valid)
        minus=plus+self.n_valid
        chosen[self.valid]=np.where(distance[plus]<=distance[minus],plus,minus)
        segment=nearest[chosen]
        fraction=fractions[chosen,segment]
        q=starts[segment]+fraction[:,None]*delta[segment]
        radial=q-self.query[chosen]
        radial/=np.maximum(np.linalg.norm(radial,axis=1)[:,None],1e-12)
        error=(distance[chosen]-self.radius_offset)*self.weights
        logarithm=np.log1p((error/data_scale)**2)
        robust=np.sign(error)*data_scale*np.sqrt(logarithm)
        result=np.r_[robust,(parameters-self.prior)*self.prior_weight]
        self.cached=(key,result,curve,valid_segments[segment],fraction,radial,error,logarithm)
        return result

    def jac(self, parameters, data_scale):
        self.fun(parameters,data_scale)
        _,_,curve,segment,fraction,radial,error,logarithm=self.cached
        jacobian=np.empty((len(self.weights),len(parameters)))
        for j in range(len(parameters)):
            step=1e-6*max(self.prior_scale[j],.001)
            shifted=parameters.copy();shifted[j]+=step
            derivative=(self.model(shifted)-curve)/step
            dq=(1-fraction[:,None])*derivative[segment]+fraction[:,None]*derivative[segment+1]
            jacobian[:,j]=np.einsum('ij,ij->i',radial,dq)*self.weights
        influence=np.ones(len(error))
        nonzero=np.abs(error)>1e-12
        influence[nonzero]=np.abs(error[nonzero])/(data_scale*np.sqrt(logarithm[nonzero])*(1+(error[nonzero]/data_scale)**2))
        return np.vstack((jacobian*influence[:,None],np.diag(self.prior_weight)))


def _project(points, curve):
    """Finite side-surface projection; no spherical cap evidence at open ends."""
    starts, delta = curve[:-1], np.diff(curve, axis=0)
    lengths = np.linalg.norm(delta, axis=1)
    valid = lengths > 1e-10
    starts, delta, lengths = starts[valid], delta[valid], lengths[valid]
    stations = np.r_[0., np.cumsum(lengths)]
    n = len(points)
    best = np.full(n, np.inf)
    closest = np.zeros((n, 3))
    tangent = np.zeros((n, 3))
    station = np.zeros(n)
    index = np.zeros(n, dtype=int)
    for j, (a, d, length) in enumerate(zip(starts, delta, lengths)):
        t = np.clip((points-a) @ d / (length*length), 0., 1.)
        q = a+t[:, None]*d
        square = np.einsum('ij,ij->i', points-q, points-q)
        take = square < best
        best[take], closest[take] = square[take], q[take]
        tangent[take] = d/length
        station[take] = stations[j]+t[take]*length
        index[take] = j
    radial = points-closest
    distance = np.sqrt(best)
    # Projected endpoint balls are not evidence of a flat-cut rebar end.
    along = np.einsum('ij,ij->i', radial, tangent)
    side = ~(((station < 1e-8) & (along < -1e-5)) |
             ((station > stations[-1]-1e-8) & (along > 1e-5)))
    return distance, radial/np.maximum(distance[:, None], 1e-12), tangent, station, side


def _body_anchor(row, side):
    curve = np.asarray(row['centerlineM'], float)
    if side == 'start':
        return curve[0], _unit(curve[0]-curve[1])
    return curve[-1], _unit(curve[-1]-curve[-2])


def _body_point(row, side, inward):
    curve = np.asarray(row['centerlineM'], float)
    ordered = curve if side == 'start' else curve[::-1]
    lens = np.linalg.norm(np.diff(ordered, axis=0), axis=1)
    s = np.r_[0., np.cumsum(lens)]
    at = min(float(inward), float(s[-1])*.4)
    j = min(int(np.searchsorted(s, at, side='right')-1), len(lens)-1)
    p = ordered[j] + (at-s[j])*_unit(ordered[j+1]-ordered[j])
    return p, _unit(ordered[j]-ordered[j+1]), at


def _bezier(p0, p1, t0, t1, count=17):
    h = np.linalg.norm(p1-p0)/3.
    controls = np.array([p0, p0+h*t0, p1-h*t1, p1])
    u = np.linspace(0., 1., count)[:, None]
    return ((1-u)**3*controls[0]+3*u*(1-u)**2*controls[1]
            +3*u*u*(1-u)*controls[2]+u**3*controls[3])


def _join_model(piece, rows):
    left, right = piece['anchors']
    a, t0 = _body_anchor(rows[left['designUnitId']], left['side'])
    b, outward = _body_anchor(rows[right['designUnitId']], right['side'])
    t1 = -outward
    cosine = float(np.clip(t0 @ t1, -1., 1.))
    angle = math.acos(cosine)
    if not .15 < angle < math.pi-.15:
        return None
    arcs=[p for p in piece['primitives'] if p['kind']=='arc']
    if len(arcs)!=1:
        return None
    radius=float(arcs[0]['radiusM'])
    ab = np.linalg.lstsq(np.column_stack((t0, -t1)), b-a, rcond=None)[0]
    q0, q1 = a+ab[0]*t0, b+ab[1]*t1
    skew = q1-q0
    if np.linalg.norm(skew) > max(.006, 2*piece['radiusM']):
        return None
    bend = _unit(t1-cosine*t0)
    max_move = min(.03, .3*min(rows[x['designUnitId']]['designLengthM'] for x in (left,right)))
    lower = max(.5*radius, (ab[0]-max_move)/math.tan(angle/2), (-ab[1]-max_move)/math.tan(angle/2))
    upper = min(4.*radius, (ab[0]+max_move)/math.tan(angle/2), (-ab[1]+max_move)/math.tan(angle/2))
    if not lower < upper:
        return None
    normal = _unit(np.cross(t0, bend))
    # Angular basis and attachment skew are fixed for this two-parameter model.
    phi = np.linspace(0., angle/2, 17)
    sine, one_minus_cosine = np.sin(phi)[:, None], (1-np.cos(phi))[:, None]
    middle = math.cos(angle/2)*t0+math.sin(angle/2)*bend
    across = -math.sin(angle/2)*t0+math.cos(angle/2)*bend
    u = np.linspace(0., 1., 34)
    skew_blend = (3*u*u-2*u*u*u)[:, None]*skew
    def model(parameters):
        extent = parameters[0]
        fraction = parameters[1] if len(parameters)>1 else 1.
        r = extent*fraction
        p0 = q0-extent*math.tan(angle/2)*t0
        first = p0+r*sine*t0+r*one_minus_cosine*bend
        bridge = 2*(extent-r)*math.sin(angle/2)
        second = first[-1]+bridge*middle+r*sine*middle+r*one_minus_cosine*across
        curve = np.vstack((first,second))
        curve += skew_blend
        return curve, {'bendRangeM': [0., _length(curve)], 'normal': normal,
                       'attachments': [(left,curve[0]), (right,curve[-1])],
                       'bendRadiusM': float(r), 'bendAngleRad': angle,
                       'tangentRadiusM':float(extent), 'bridgeLengthM':float(bridge)}
    seed = np.array([np.clip(radius, lower+1e-9, upper-1e-9),1.-1e-8])
    return model, seed, np.array([lower,.2]), np.array([upper,1.]), np.array([radius,1.]), np.array([radius*1.5,.7])


def _terminal_model(piece, rows, *, articulated=False):
    anchor = piece['anchors'][0]
    row = rows[anchor['designUnitId']]
    a, tangent = _body_anchor(row, anchor['side'])
    design = np.asarray(piece['centerlineM'], float)
    # A terminal piece may be ordered tip->body or body->tip.
    # The closest end of the design piece to the unit endpoint identifies join.
    original_anchor = np.asarray(piece['_designAnchors'][0], float)
    if np.linalg.norm(design[0]-original_anchor) > np.linalg.norm(design[-1]-original_anchor):
        design = design[::-1]
    arcs=[p for p in piece['primitives'] if p['kind']=='arc']
    if len(arcs)!=1:
        return None
    arc=arcs[0]
    radius = float(arc['radiusM'])
    angle = abs(float(arc['sweepRad']))
    if not .2 < angle < math.pi*1.8:
        return None
    # Recover the bend side from exact design geometry, then remove axial bias.
    design_tangent = _unit(np.asarray(piece['_designTangents'][0]))
    offsets = design-original_anchor
    transverse = offsets-(offsets@design_tangent)[:, None]*design_tangent
    k = int(np.argmax(np.linalg.norm(transverse, axis=1)))
    bend = transverse[k]-(transverse[k]@tangent)*tangent
    if np.linalg.norm(bend) < 1e-8:
        return None
    bend = _unit(bend)
    normal = _unit(np.cross(tangent, bend))
    tail = max(0., float(piece['designLengthM'])-radius*angle)
    # Blend from an observed body interval, not an extrapolated endpoint.
    evidence = row.get('axisEvidenceRangeM', [0., row['designLengthM']])
    inward = float(evidence[0]) if anchor['side'] == 'start' else row['designLengthM']-float(evidence[-1])
    inward = min(.35, max(.045, inward))
    fixed, fixed_tangent, inward = _body_point(row, anchor['side'], inward)
    transverse_bound = max(.012, 4*piece['radiusM'])
    def model(parameters):
        axial, y, z, roll, r, theta = parameters[:6]
        leg=float(parameters[8]) if len(parameters)>8 else tail
        side = math.cos(roll)*bend+math.sin(roll)*normal
        arc_tangent=tangent
        if len(parameters)>6:
            pitch,yaw=parameters[6:8]
            lifted=math.cos(pitch)*tangent+math.sin(pitch)*_unit(np.cross(tangent,side))
            arc_tangent=math.cos(yaw)*lifted+math.sin(yaw)*side
            side=-math.sin(yaw)*lifted+math.cos(yaw)*side
        plane_normal = _unit(np.cross(arc_tangent, side))
        join = a+axial*tangent+y*bend+z*normal
        phi = np.linspace(0., theta, 41)
        arc_curve = join+r*np.sin(phi)[:, None]*arc_tangent+r*(1-np.cos(phi))[:, None]*side
        lead = _bezier(fixed, join, fixed_tangent, arc_tangent)
        end_tangent = math.cos(theta)*arc_tangent+math.sin(theta)*side
        curve = np.vstack((lead[:-1], arc_curve))
        bend_start, bend_end = _length(lead), _length(curve)
        if leg > 1e-6:
            tip = arc_curve[-1]+leg*end_tangent
            curve = np.vstack((curve, tip))
        return curve, {'bendRangeM': [bend_start, bend_end], 'normal': plane_normal,
                       'attachments': [(anchor,fixed)], 'attachmentLengthM': inward,
                       'bendRadiusM': float(r), 'bendAngleRad': float(theta), 'tailLengthM': leg,
                       'arcCenterM':join+r*side,'arcTangent':arc_tangent,
                       'hookParameters':np.asarray(parameters).tolist()}
    seed = np.array([0.,0.,0.,0.,radius,angle])
    lower = np.array([-.035,-transverse_bound,-transverse_bound,-math.pi/3,radius*.55,max(.2,angle-.4)])
    upper = np.array([.035,transverse_bound,transverse_bound,math.pi/3,radius*1.7,min(math.pi*1.8,angle+.4)])
    prior_scale = np.array([.025,transverse_bound,transverse_bound,.6,radius*.6,.3])
    if articulated:
        seed=np.r_[seed,0.,0.]
        lower=np.r_[lower,-.6,-.6];upper=np.r_[upper,.6,.6]
        lower[1:3]=-max(2*piece['radiusM'],radius*.5)
        upper[1:3]=max(2*piece['radiusM'],radius*.5)
        lower[3:6]=[-math.pi/2,max(1.2*piece['radiusM'],radius*.4),max(.35,angle-.85)]
        upper[3:6]=[math.pi/2,radius*2.1,min(math.pi*1.7,angle+.85)]
        prior_scale=np.r_[prior_scale,.35,.35];prior_scale[3:6]=[.9,radius*.8,.6]
    return model,seed,lower,upper,seed.copy(),prior_scale


def _evidence(points, normals, curve, radius, meta):
    distance, radial, tangent, station, side = _project(points, curve)
    residual = distance-radius
    tol = max(.0009, .36*radius)
    support = side & (np.abs(residual) <= tol)
    valid_normal = np.zeros(len(points), bool)
    alignment = np.ones(len(points))
    if normals is not None:
        nlen = np.linalg.norm(normals, axis=1)
        valid_normal = np.isfinite(normals).all(axis=1) & (nlen > .5)
        n = normals/np.maximum(nlen[:, None], 1e-12)
        alignment = np.abs(np.einsum('ij,ij->i', n, radial))
        support &= ~valid_normal | (alignment >= .75)
    lo, hi = meta['bendRangeM']
    on_bend = (station >= lo) & (station <= hi)
    bins = np.minimum(7, np.maximum(0, ((station-lo)/max(hi-lo,1e-8)*8).astype(int)))
    counts = np.bincount(bins[support & on_bend], minlength=8)
    covered = counts >= 6
    normal_axis = meta['normal']
    across = np.cross(np.broadcast_to(normal_axis,tangent.shape), tangent)
    xy = np.column_stack((np.einsum('ij,ij->i',radial,across), radial@normal_axis))
    shell = xy[support & on_bend]
    spread = 0.
    if len(shell) >= 24:
        centered = shell-np.mean(shell,axis=0)
        spread = float(np.linalg.eigvalsh(centered.T@centered/len(shell))[0])
    normal_spread = 1.
    ns = support & on_bend & valid_normal
    if ns.sum() >= 24:
        n = normals[ns]/np.linalg.norm(normals[ns],axis=1)[:,None]
        local = np.column_stack((np.einsum('ij,ij->i',n,across[ns]),n@normal_axis))
        local /= np.maximum(np.linalg.norm(local,axis=1)[:,None],1e-12)
        normal_spread=float(np.linalg.eigvalsh(local.T@local/len(local))[0])
    def interval_gap(left, right):
        observed = np.sort(station[support & (station >= left) & (station <= right)])
        if len(observed) < 12:
            return float(right-left)
        # Include boundary gaps: six isolated station rings are not a surface.
        return float(np.max(np.diff(np.r_[left, observed, right])))
    bend_gap = interval_gap(lo, hi)
    lead_gap = interval_gap(max(0.,lo-max(.012,4*radius)), lo) if lo > .005 else 0.
    accepted = (int((support & on_bend).sum()) >= 48 and int(covered.sum()) >= 6
                and spread >= .0001 and normal_spread >= .005
                and bend_gap <= max(.0015, .65*radius)
                and lead_gap <= max(.003, 1.25*radius))
    return {'accepted': accepted, 'support': support, 'residual': residual,
            'station':station, 'onBend':on_bend, 'bins':bins, 'tolerance':tol,
            'coveredBins':int(covered.sum()), 'bendSupport':int((support & on_bend).sum()),
            'crossSectionSpread':spread, 'normalSpread':normal_spread,
            'alignment':alignment,'validNormal':valid_normal,'side':side,
            'bendMaxGapM':bend_gap,'attachmentMaxGapM':lead_gap}


def _continuous_terminal_extent(points,normals,curve,radius):
    """Provisional side extent for pose validation; final length is observed later.

    Retain the pose-validation sample and statistic so endpoint refinement does
    not change the accepted arc model or the independent pose check.
    """
    if normals is None or len(points)<32 or len(curve)<3:
        return None
    start=curve[-2];direction=_unit(curve[-1]-start)
    along=(points-start)@direction
    radial=points-start-along[:,None]*direction;distance=np.linalg.norm(radial,axis=1)
    norms=np.linalg.norm(normals,axis=1);normal=normals/np.maximum(norms[:,None],1e-12)
    valid=(np.isfinite(normals).all(axis=1)&(norms>.5)&(along>.001)&(along<.10)
           &(np.abs(distance-radius)<max(.0009,.3*radius))&(np.abs(normal@direction)<.35)
           &(np.abs(np.sum(normal*radial/np.maximum(distance[:,None],1e-12),axis=1))>.8))
    if valid.sum()<32:
        return None
    positions=along[valid];counts=np.histogram(positions,bins=np.arange(0.,.102,.002))[0]
    good=counts>=3
    if not good[:3].any():
        return None
    end=0;gap=0
    for i,has_support in enumerate(good):
        if has_support:end=i;gap=0
        else:gap+=1
        if gap>=2 and i>=2:break
    selected=valid&(along<.002*(end+1))
    if selected.sum()<32 or end<3 or end>=48:
        return None
    shell=normal[selected];spread=np.linalg.eigvalsh(shell.T@shell/len(shell))
    if spread[-2]<.005:
        return None
    return float(np.quantile(along[selected],.99))


def _terminal_length_evidence(points, normals, curve, radius, search_limit=.10):
    """Observe an axial endpoint without changing the fitted circle or its frame.

    Continuous cylindrical support establishes identity. A nearby planar cap
    locates the physical cut; otherwise the last supported side band is only an
    observed extent. A search boundary is never treated as an endpoint.
    """
    if normals is None or len(points)<32 or len(curve)<3:
        return None
    origin=curve[-2];direction=_unit(curve[-1]-origin)
    delta=points-origin;along=delta@direction
    radial=delta-along[:,None]*direction;distance=np.linalg.norm(radial,axis=1)
    norms=np.linalg.norm(normals,axis=1)
    normal=normals/np.maximum(norms[:,None],1e-12)
    finite=np.isfinite(points).all(axis=1)&np.isfinite(normals).all(axis=1)&(norms>.5)
    side=(finite&(along>.001)&(along<search_limit)
          &(np.abs(distance-radius)<max(.0009,.3*radius))
          &(np.abs(normal@direction)<.35)
          &(np.abs(np.sum(normal*radial/np.maximum(distance[:,None],1e-12),axis=1))>.8))
    width=max(.002,.5*radius)
    edges=np.arange(0.,search_limit+width*.5,width)
    if len(edges)<5 or side.sum()<32:return None
    good=np.histogram(along[side],bins=edges)[0]>=3
    if not good[:3].any():return None
    end=0;gap=0
    for i,supported in enumerate(good):
        if supported:end=i;gap=0
        else:gap+=1
        if gap>=2 and i>=2:break
    selected=side&(along<edges[end+1])
    if selected.sum()<32 or end<3 or end>=len(good)-2:return None
    shell=normal[selected]
    if np.linalg.eigvalsh(shell.T@shell/len(shell))[-2]<.005:return None
    # A local terminal band avoids dependence on density far inside the tail.
    terminal=selected&(along>=edges[max(0,end-1)])
    extent=float(np.quantile(along[terminal],.99))
    answer={'source':'continuous-scan-support','lengthM':extent,
            'support':int(selected.sum()),'endpointSupport':int(terminal.sum()),
            'uncertaintyM':width,'physicalEndObserved':False}
    cap=(finite&(np.abs(along-extent)<2*radius)&(distance<1.2*radius)
         &(np.abs(normal@direction)>.7))
    if cap.sum()<12:return answer
    cloud=delta[cap]
    # Reject separate axial surfaces; do not average competing terminal planes.
    center=np.median(cloud,axis=0)
    _,_,axes=np.linalg.svd(cloud-center,full_matrices=False)
    plane=axes[-1]
    residual=np.abs((cloud-center)@plane)
    inlier=residual<max(.0003,.15*radius)
    if inlier.sum()<12 or inlier.mean()<.8:return answer
    cloud=cloud[inlier];center=cloud.mean(axis=0)
    values,axes=np.linalg.eigh((cloud-center).T@(cloud-center)/len(cloud))
    plane=axes[:,0];alignment=abs(float(plane@direction))
    if (alignment<.7 or values[1]<(.2*radius)**2
            or np.sqrt(max(0.,values[0]))>max(.0003,.12*radius)):
        return answer
    length=float(center@plane/(direction@plane))
    if length<extent-width*.5 or length>=search_limit-width:return answer
    # A cap is a disk crossing the axis, not an adjacent parallel face.
    radial_center=center-(center@direction)*direction
    if np.linalg.norm(radial_center)>.65*radius:return answer
    return {'source':'terminal-face','lengthM':length,'support':int(selected.sum()),
            'endpointSupport':int(len(cloud)),
            'uncertaintyM':float(max(.0001,np.sqrt(max(0.,values[0]))/alignment)),
            'physicalEndObserved':True}


def _observed_terminal_extent(points,normals,curve,radius):
    evidence=_terminal_length_evidence(points,normals,curve,radius)
    return None if evidence is None else evidence['lengthM']


def _finalize_terminal_length(result, points, normals, radius, search_limit):
    """Apply the same independently checked scalar measurement to every hook."""
    curve=result['curve'];meta=result['meta']
    if normals is None or meta.get('tailLengthM',0.)<=1e-6:return
    measured=_terminal_length_evidence(points,normals,curve,radius,search_limit)
    if measured is None:return
    checks=[_terminal_length_evidence(points[k::2],normals[k::2],curve,radius,search_limit)
            for k in (0,1)]
    if any(check is None for check in checks):return
    change=abs(checks[0]['lengthM']-checks[1]['lengthM'])
    if change>max(.001,.5*radius):return
    if measured['physicalEndObserved'] and not all(c['physicalEndObserved'] for c in checks):return
    measured['validationChangeM']=change
    # Missing side points alone cannot prove that a previously supported leg is
    # shorter. A visible cut face can; extensions need continuous tube support.
    if not measured['physicalEndObserved'] and measured['lengthM']<meta['tailLengthM']:
        return
    updated=curve.copy()
    updated[-1]=curve[-2]+measured['lengthM']*_unit(curve[-1]-curve[-2])
    result['curve']=updated
    meta['tailLengthM']=measured['lengthM'];meta['tailExtentSource']=measured['source']
    meta['tailLengthEvidence']=measured
    if len(meta['hookParameters'])==9:meta['hookParameters'][8]=measured['lengthM']


def _curve_query_stations(curve, spacing):
    """Cover complete segments, including a tail represented by just two ends."""
    sections=[curve]
    for start,end in zip(curve[:-1],curve[1:]):
        count=int(math.ceil(np.linalg.norm(end-start)/spacing))
        if count>1:
            sections.append(start+np.arange(1,count)[:,None]/count*(end-start))
    return np.vstack(sections)


def _fit_piece(points, normals, piece, rows, *, articulated=False, candidate_out=None, warm_start=None, warm_validation=None):
    prepared = _join_model(piece,rows) if piece['kind']=='join' else _terminal_model(piece,rows,articulated=articulated)
    if prepared is None:
        return None, 'unsupported-or-inconsistent-attachment'
    model,seed,lower,upper,prior,prior_scale = prepared
    initial, meta = model(seed)
    if len(points) < 48:
        return None, 'insufficient-curve-evidence'
    # Deterministic bounded solve. Fixed sample/bin weights compare equal evidence.
    ids = np.arange(len(points))
    if len(ids)>1800:
        order=np.lexsort((points[:,2],points[:,1],points[:,0]))
        ids=order[np.linspace(0,len(order)-1,1800,dtype=int)]
    sample=points[ids]; ns=None if normals is None else normals[ids]
    initial_evidence=_evidence(sample,ns,initial,piece['radiusM'],meta)
    station=initial_evidence['station']
    bin_ids=np.minimum(15,(station/max(_length(initial),1e-8)*16).astype(int))
    counts=np.bincount(bin_ids,minlength=16)
    weights=np.sqrt(np.median(counts[counts>0])/np.maximum(counts[bin_ids],24))
    radius=piece['radiusM']
    scale=max(.00035,.12*radius)
    data_scale=scale
    def fitting_curve(parameters):
        curve,model_meta=model(parameters)
        # The local crop also contains straight body observations. Let the
        # unchanged body explain those points; otherwise a short bend expands
        # merely to absorb points lying beyond its tangency endpoints.
        for j,(anchor,position) in enumerate(model_meta['attachments']):
            body=np.asarray(_clip_display(rows[anchor['designUnitId']],{anchor['side']:position}))
            if j==0:
                toward=body[::-1] if anchor['side']=='start' else body
                curve=np.vstack((toward[:-1],curve))
            else:
                away=body if anchor['side']=='start' else body[::-1]
                curve=np.vstack((curve,away[1:]))
        return curve
    residual=_CurveResidual(fitting_curve,sample,ns,weights,radius,prior,prior_scale,scale)
    def objective(parameters):
        return residual.fun(parameters,data_scale)
    def jacobian(parameters):
        return residual.jac(parameters,data_scale)
    def solve(initial_parameters):
        nonlocal data_scale
        # Graduated robust scale avoids false local minima on a partial arc.
        # Both all-data and independent training solves use the same schedule.
        parameters=initial_parameters
        for data_scale in (max(.002,.7*radius),scale):
            result=least_squares(objective,parameters,jac=jacobian,bounds=(lower,upper),loss='linear',
                                 x_scale=np.maximum(prior_scale,1e-5),max_nfev=65)
            parameters=result.x
        return result
    # Nearby starts expose visible-side mirror minima without an unbounded search.
    seeds=[seed]
    training_seeds=[seed]
    if piece['kind']=='terminal':
        # Bounded coarse pose starts are essential when an extrapolated body
        # endpoint lies more than one scan-noise scale from the visible hook.
        candidates=[]
        for y in np.linspace(lower[1]*.8,upper[1]*.8,7):
            for z in np.linspace(lower[2]*.5,upper[2]*.5,5):
                candidate=seed.copy();candidate[1:3]=[y,z]
                trial_residual=objective(candidate) if warm_start is None else np.zeros(1)
                candidates.append((float(trial_residual@trial_residual),candidate))
        candidates.sort(key=lambda item:item[0])
        seeds=[candidate for _,candidate in candidates[:3]]
        training_seeds=[candidate for _,candidate in candidates]
        if articulated:
            seeds=seeds[:2]+[seed]
            if warm_start is not None:
                warm=np.r_[warm_start[:6],0.,0.]
                seeds=[np.clip(warm,lower+1e-9,upper-1e-9),seed]
    else:
        training_seeds=[np.array([r,f]) for r in np.linspace(lower[0]+1e-8,upper[0]-1e-8,7)
                        for f in (.25,.5,.75,1.-1e-8)]
        seeds=sorted(training_seeds,key=lambda p:float(objective(p)@objective(p)))[:3]
    solutions=[]
    def observed_model(parameters,scan,scan_normals):
        candidate,candidate_meta=model(parameters)
        if articulated and piece['kind']=='terminal' and candidate_meta.get('tailLengthM',0.)>1e-6:
            extent=_continuous_terminal_extent(scan,scan_normals,candidate,radius)
            if extent is not None:
                candidate,candidate_meta=model(np.r_[parameters[:8],extent])
                candidate_meta['tailExtentSource']='continuous-scan-support'
        return candidate,candidate_meta
    for initial_parameters in seeds:
        result=solve(initial_parameters)
        curve,meta=model(result.x)
        evidence=_evidence(points,normals,curve,radius,meta)
        mask=evidence['support'] & evidence['onBend']
        score=float(np.median(np.abs(evidence['residual'][mask]))) if mask.any() else np.inf
        solutions.append((not evidence['accepted'],float(result.cost),score,curve,meta,evidence,result.x))
    solutions.sort(key=lambda s:(s[0],s[1]))
    _,cost,score,curve,meta,evidence,params=solutions[0]
    if candidate_out is not None:
        candidate_out['parameters']=params.copy()
    partial_reason=None
    if not evidence['accepted']:
        if not (articulated and piece['kind']=='terminal' and evidence['bendSupport']>=96
                and evidence['coveredBins']>=3 and evidence['crossSectionSpread']>=.0001
                and evidence['normalSpread']>=.005
                and evidence['attachmentMaxGapM']<=max(.003,1.25*radius)):
            return None,'insufficient-continuous-curved-surface'
        partial_reason='insufficient-continuous-curved-surface'
    if meta['bendRadiusM']<=1.05*radius:
        return None,'self-overlapping-bend'
    for alternative in solutions[1:]:
        if alternative[0] or alternative[1] > cost*1.10+1e-12:
            continue
        if np.max(np.linalg.norm(alternative[3]-curve,axis=1)) > 1.5*radius:
            if not articulated:return None,'ambiguous-curve-support'
            partial_reason='ambiguous-curve-support'
    # Withhold spatial bands for a second bounded solve, then require both band
    # sets to explain the same curved surface. This checks stability, not truth.
    # Fixed spatial stripes remain meaningful when the actual bend has moved
    # away from the short design arc. Clipped design-arc stations collapse such
    # observations onto its endpoints and cannot define a validation partition.
    direction=np.linalg.svd(initial-initial.mean(axis=0),full_matrices=False)[2][0]
    fraction=(points-initial[0])@direction/max(.004,2*radius)+.371
    blocks=np.floor(fraction).astype(int)
    interior=(fraction-blocks>.15)&(fraction-blocks<.85)
    held=(blocks%2==1)&interior
    train_mask=(blocks%2==0)&interior
    if held.sum()<24 or train_mask.sum()<24:
        return None,'insufficient-curve-validation'
    original_sample,original_weights,original_normals=sample,weights,ns
    sampled_train=train_mask[ids]
    sample=original_sample[sampled_train];weights=original_weights[sampled_train]
    ns=None if original_normals is None else original_normals[sampled_train]
    train_bins=bin_ids[sampled_train]
    train_counts=np.bincount(train_bins,minlength=16)
    weights=np.sqrt(np.median(train_counts[train_counts>0])/np.maximum(train_counts[train_bins],24))
    residual=_CurveResidual(fitting_curve,sample,ns,weights,radius,prior,prior_scale,scale)
    # Select an initialization on training evidence alone, independently of
    # all-data parameters or all-data inlier selection.
    checks=[]
    if warm_validation is not None:
        check_starts=[np.clip(np.r_[warm_validation[:6],0.,0.],lower+1e-9,upper-1e-9)]
    else:
        check_starts=sorted(training_seeds,key=lambda p:float(objective(p)@objective(p)))[:3]
        if articulated and piece['kind']=='terminal':check_starts=check_starts[:2]+[seed]
        if warm_start is not None:check_starts=[check_starts[0],seed]
    for check_seed in check_starts:
        checks.append(solve(check_seed))
    if articulated and piece['kind']=='terminal':
        def check_rank(value):
            candidate_curve,candidate_meta=model(value.x)
            candidate_evidence=_evidence(points[train_mask],None if normals is None else normals[train_mask],
                                         candidate_curve,radius,candidate_meta)
            return (-candidate_evidence['coveredBins'],value.cost)
        check=min(checks,key=check_rank)
    else:
        check=min(checks,key=lambda value:value.cost)
    if candidate_out is not None:
        candidate_out['trainingParameters']=check.x.copy()
    check_curve,check_meta=model(check.x)
    check_evidence=_evidence(points,normals,check_curve,radius,check_meta)
    validation_support=int((check_evidence['support'] & check_evidence['onBend'] & held).sum())
    curve_change=float(np.max(np.linalg.norm(check_curve-curve,axis=1)))
    if validation_support < max(24,.65*int((evidence['support']&evidence['onBend']&held).sum())) or curve_change > max(.002, .7*radius):
        if not articulated:return None,'unstable-held-out-curve'
        partial_reason='unstable-held-out-curve'
    observed_change=curve_change
    if articulated:
        observed=evidence['support']&evidence['onBend']&held
        if observed.sum()<24:return None,'insufficient-curve-validation'
        d0,v0,*_=_project(points[observed],curve)
        d1,v1,*_=_project(points[observed],check_curve)
        observed_change=float(np.quantile(np.linalg.norm(d0[:,None]*v0-d1[:,None]*v1,axis=1),.9))
        # Inferred missing geometry may vary, but observed centerlines must agree
        # within one steel radius. Full scan confirmation keeps its stricter gate.
        validation_bins=np.unique(check_evidence['bins'][check_evidence['support']&check_evidence['onBend']&held]).size
        if observed_change>max(.002,radius) or validation_support<24 or validation_bins<3:
            return None,'unstable-observed-hook'
        if normals is not None:
            measured,measured_meta=observed_model(params,points,normals)
            extent_a=_continuous_terminal_extent(points[::2],normals[::2],curve,radius)
            extent_b=_continuous_terminal_extent(points[1::2],normals[1::2],curve,radius)
            if extent_a is not None and extent_b is not None and abs(extent_a-extent_b)<.002:
                curve,meta=measured,measured_meta
                evidence=_evidence(points,normals,curve,radius,meta)
    # A terminal leg without distributed side observations remains unconfirmed.
    if meta.get('tailLengthM',0.)>1e-6:
        tail=evidence['station']>meta['bendRangeM'][1]+.15*meta['tailLengthM']
        far=evidence['station']>meta['bendRangeM'][1]+.65*meta['tailLengthM']
        if (evidence['support']&tail).sum()<24 or (evidence['support']&far).sum()<8:
            if not articulated:return None,'unobserved-terminal-leg'
            partial_reason='unobserved-terminal-leg'
    return {'curve':curve,'meta':meta,'evidence':evidence,
            'validationMaxChangeM':curve_change,'validationSupport':validation_support,
            'observedValidationP90ChangeM':observed_change,
            'validationCurve':check_curve,
            'scanGuidedInference':partial_reason is not None},partial_reason or 'parametric-tube-supported'


def _local_curve_support(points, normals, curve, check_curve, radius, normal_axis):
    """Confirm continuous local surfaces shared by independently fitted shapes.

    Geometry outside these observed intervals remains inference. A point must
    agree with both shapes, including side normals, and belong to a distributed
    curved cross-section; proximity to a design completion alone never suffices.
    """
    empty = np.zeros(len(points), bool)
    if normals is None or len(points) < 48:
        return empty, [], 0.
    d, radial, tangent, station, side = _project(points, curve)
    vd, vradial, _, _, vside = _project(points, check_curve)
    tolerance = max(.0009, .36*radius)
    nlen = np.linalg.norm(normals, axis=1)
    n = normals/np.maximum(nlen[:, None], 1e-12)
    stability = np.linalg.norm(d[:, None]*radial-vd[:, None]*vradial, axis=1)
    good = (side & vside & (nlen > .5) & np.isfinite(normals).all(axis=1)
            & (np.abs(d-radius) <= tolerance) & (np.abs(vd-radius) <= tolerance)
            & (np.abs(np.einsum('ij,ij->i', n, radial)) >= .75)
            & (np.abs(np.einsum('ij,ij->i', n, vradial)) >= .75)
            & (stability <= max(.001, .5*radius)))
    width = max(.006, 2*radius)
    bins = np.floor(station/width).astype(int)
    across = np.cross(np.broadcast_to(normal_axis, tangent.shape), tangent)
    local_normal = np.column_stack((np.einsum('ij,ij->i', n, across), n@normal_axis))
    local_normal /= np.maximum(np.linalg.norm(local_normal, axis=1)[:, None], 1e-12)
    approved = []
    for band in np.unique(bins[good]):
        mask = good & (bins == band)
        observed = np.sort(station[mask])
        if (len(observed) < 12 or np.ptp(observed) < .5*width
                or np.max(np.diff(observed)) > max(.0015, .65*radius)):
            continue
        directions = local_normal[mask]
        if np.linalg.eigvalsh(directions.T@directions/len(directions))[0] < .005:
            continue
        approved.append(band)
    if not approved:
        return empty, [], 0.
    groups = np.split(np.asarray(approved), np.flatnonzero(np.diff(approved) > 1)+1)
    intervals = []
    selected = empty.copy()
    for group in groups:
        if len(group) < 2:
            continue
        mask = good & np.isin(bins, group)
        observed = np.sort(station[mask])
        # A small unobserved gap between two individually dense bands must not
        # silently turn into a confirmed bridge.
        parts = np.split(observed, np.flatnonzero(np.diff(observed) > max(.0015, .65*radius))+1)
        for part in parts:
            if len(part) < 48 or np.ptp(part) < 3*radius:
                continue
            lo, hi = float(part[0]), float(part[-1])
            selected |= mask & (station >= lo) & (station <= hi)
            intervals.append([lo, hi])
    return selected, intervals, float(np.max(stability[selected])) if selected.any() else 0.


def _clip_display(row, changes):
    curve=np.asarray(row['centerlineM'],float)
    start=changes.get('start',curve[0]);end=changes.get('end',curve[-1])
    # Project each attachment onto the original body order and retain interior
    # vertices only. The baseline centerline/nominal-length record is untouched.
    tangent=_unit(curve[-1]-curve[0]);along=(curve-curve[0])@tangent
    lo,hi=(start-curve[0])@tangent,(end-curve[0])@tangent
    if lo>=hi:
        return None
    inside=curve[(along>lo+1e-8)&(along<hi-1e-8)]
    return np.vstack((start,inside,end)).tolist()


def _inferred_join(piece, rows, ambiguous_body_ids=()):
    """Build a conservative design-seeded join without inspecting scan points."""
    from algorithms.rebar_design_curves import _primitive_path
    if piece.get('kind') != 'join' or piece.get('geometrySource') != 'ifc-analytic':
        return None
    anchors=piece.get('anchors')
    unit_ids=piece.get('unitIds')
    if (not isinstance(anchors,list) or len(anchors)!=2
            or not isinstance(unit_ids,list) or len(unit_ids)!=2
            or not all(isinstance(anchor,dict) for anchor in anchors)
            or len(set(unit_ids))!=2 or set(unit_ids)!={a.get('designUnitId') for a in anchors}
            or any(uid in ambiguous_body_ids for uid in unit_ids)):
        return None
    body_rows=[]
    for anchor in anchors:
        uid=anchor.get('designUnitId')
        row=rows.get(uid)
        if (row is None or anchor.get('side') not in ('start','end')
                or row.get('status')!='fitted' or row.get('kind')!='web'):
            return None
        try:
            body=np.asarray(row.get('centerlineM'),float)
        except (TypeError,ValueError):
            return None
        if (body.ndim!=2 or body.shape[1:]!=(3,) or len(body)<2
                or not np.isfinite(body).all() or _length(body)<=1e-8):
            return None
        body_rows.append(row)
    parent=piece.get('designBarId')
    if (parent is None or any(row.get('designBarId') is None
            or str(row.get('designBarId'))!=str(parent) for row in body_rows)):
        return None
    primitives=piece.get('primitives')
    if (not isinstance(primitives,list) or not primitives
            or not all(isinstance(primitive,dict) and primitive.get('kind') in ('line','arc')
                       for primitive in primitives)):
        return None
    analytic_path=_primitive_path(primitives)
    if analytic_path is None:
        return None
    arcs=[primitive for primitive in primitives
          if isinstance(primitive,dict) and primitive.get('kind')=='arc']
    if len(arcs)!=1:
        return None
    try:
        radius=float(arcs[0].get('radiusM'))
        sweep=abs(float(arcs[0].get('sweepRad')))
        design=np.asarray(piece.get('centerlineM'),float)
    except (TypeError,ValueError):
        return None
    if (not math.isfinite(radius) or radius<=1e-8 or not math.isfinite(sweep)
            or not .15<sweep<math.pi-.15 or design.ndim!=2
            or design.shape[1:]!=(3,) or len(design)<2
            or not np.isfinite(design).all() or _length(design)<=1e-8):
        return None
    endpoint_tolerance=max(1e-6,radius*1e-4)
    if (np.linalg.norm(design[0]-analytic_path[0]['startM'])>endpoint_tolerance
            or np.linalg.norm(design[-1]-analytic_path[-1]['endM'])>endpoint_tolerance):
        return None
    def endpoint_tangent(primitive, side):
        if primitive['kind']=='line':
            return _unit(primitive['endM']-primitive['startM'])
        point=primitive['startM'] if side=='start' else primitive['endM']
        tangent=_unit(np.cross(primitive['normal'],point-primitive['centerM']))
        return tangent if primitive['sweepRad']>0 else -tangent
    expected=(_body_anchor(body_rows[0],anchors[0]['side'])[1],
              -_body_anchor(body_rows[1],anchors[1]['side'])[1])
    analytic_tangents=(endpoint_tangent(analytic_path[0],'start'),
                       endpoint_tangent(analytic_path[-1],'end'))
    # Validate each observed body against its analytic endpoint separately.
    # Small fit rotations at opposite ends add in the measured bend angle.
    if any(float(actual@design_tangent)<.97
           for actual,design_tangent in zip(expected,analytic_tangents)):
        return None
    prepared=_join_model(piece,rows)
    if prepared is None:
        return None
    curve,meta=prepared[0](prepared[1])
    if (curve.ndim!=2 or curve.shape[1:]!=(3,) or len(curve)<3
            or not np.isfinite(curve).all() or _length(curve)<=1e-8):
        return None
    tangents=(_unit(curve[1]-curve[0]),_unit(curve[-1]-curve[-2]))
    if any(float(actual@wanted)<.98 for actual,wanted in zip(tangents,expected)):
        return None
    return curve,meta


def _inferred_terminal(piece, rows, design_units, ambiguous_body_ids=()):
    """Complete one analytic hook from its confirmed body and design bend plane.

    A single body cannot observe rotation around its own axis or an occluded
    tail. Those dimensions remain explicit design inference, never scan support.
    """
    from algorithms.rebar_design_curves import _primitive_path
    if piece.get('kind') != 'terminal' or piece.get('geometrySource') != 'ifc-analytic':
        return None
    anchors, ids = piece.get('anchors'), piece.get('unitIds')
    if (not isinstance(anchors, list) or len(anchors) != 1 or not isinstance(anchors[0], dict)
            or not isinstance(ids, list) or len(ids) != 1 or ids[0] in ambiguous_body_ids):
        return None
    anchor = anchors[0]
    uid, side = anchor.get('designUnitId'), anchor.get('side')
    row, design_unit = rows.get(uid), design_units.get(uid)
    if (uid != ids[0] or side not in ('start', 'end') or row is None or design_unit is None
            or row.get('status') != 'fitted' or row.get('kind') != 'straight'
            or piece.get('designBarId') is None
            or str(row.get('designBarId')) != str(piece['designBarId'])
            or str(design_unit.get('designBarId')) != str(piece['designBarId'])):
        return None
    try:
        body = np.asarray(row['centerlineM'], float)
        design = np.asarray(piece['centerlineM'], float)
        start, end = np.asarray(design_unit['startM'], float), np.asarray(design_unit['endM'], float)
        radius = float(piece['radiusM'])
        body_length = float(row['designLengthM'])
    except (KeyError, TypeError, ValueError):
        return None
    if (body.ndim != 2 or body.shape[1:] != (3,) or len(body) < 2 or not np.isfinite(body).all()
            or design.ndim != 2 or design.shape[1:] != (3,) or len(design) < 2 or not np.isfinite(design).all()
            or start.shape != (3,) or end.shape != (3,) or not np.isfinite([start, end]).all()
            or np.linalg.norm(end-start) < 1e-8 or np.any(np.linalg.norm(np.diff(body, axis=0), axis=1) < 1e-8)
            or not math.isfinite(radius) or radius <= 0 or not math.isfinite(body_length) or body_length <= 0):
        return None
    path = _primitive_path(piece.get('primitives'))
    if path is None or len(path) > 2 or sum(p['kind'] == 'arc' for p in path) != 1:
        return None
    if (np.linalg.norm(design[0]-path[0]['startM']) > 1e-6
            or np.linalg.norm(design[-1]-path[-1]['endM']) > 1e-6):
        return None
    original_anchor = start if side == 'start' else end
    reverse = np.linalg.norm(design[-1]-original_anchor) < np.linalg.norm(design[0]-original_anchor)
    first, last = (path[-1], path[0]) if reverse else (path[0], path[-1])
    attached = first['endM'] if reverse else first['startM']
    if first['kind'] != 'arc' or np.linalg.norm(attached-original_anchor) > 1e-5:
        return None
    sign = -1. if reverse else 1.
    path_tangent = sign*np.sign(first['sweepRad'])*_unit(np.cross(first['normal'], attached-first['centerM']))
    design_tangent = _unit(start-end if side == 'start' else end-start)
    _, tangent = _body_anchor(row, side)
    bend_radius, angle = first['radiusM'], abs(first['sweepRad'])
    if (path_tangent@design_tangent < .999 or tangent@design_tangent < .95
            or bend_radius <= 1.05*radius or not .2 < angle < math.pi*1.8):
        return None
    if len(path) == 2:
        free_arc_end = first['startM'] if reverse else first['endM']
        exit_tangent = sign*np.sign(first['sweepRad'])*_unit(np.cross(first['normal'], free_arc_end-first['centerM']))
        tail_tangent = sign*_unit(last['endM']-last['startM'])
        if last['kind'] != 'line' or exit_tangent@tail_tangent < .999:
            return None
    analytic_length = sum(p['length'] for p in path)
    if abs(analytic_length-piece.get('designLengthM', 0.)) > 1e-6:
        return None
    prepared_piece = dict(piece, _designAnchors=[original_anchor], _designTangents=[design_tangent])
    prepared = _terminal_model(prepared_piece, rows)
    if prepared is None:
        return None
    curve, meta = prepared[0](prepared[1])
    if not np.isfinite(curve).all() or _length(curve) <= 0:
        return None
    meta['inferenceBasis'] = {'anchorSide': side, 'bendRadiusM': bend_radius,
        'bendAngleRad': angle, 'tailLengthM': max(0., analytic_length-bend_radius*angle),
        'orientation': 'body-tangent-and-design-plane'}
    return curve, meta


def _stage_inferred_attachments(rows, display_changes, attachments, expected_count=2):
    """Validate all clips atomically; fitted or prior inferred clips win conflicts."""
    staged={uid:dict(changes) for uid,changes in display_changes.items()}
    seen=set()
    for anchor,position in attachments:
        uid,side=anchor.get('designUnitId'),anchor.get('side')
        key=(uid,side)
        try: point=np.asarray(position,float)
        except (TypeError,ValueError): return None
        if (uid not in rows or side not in ('start','end') or key in seen
                or side in staged.get(uid,{}) or point.shape!=(3,) or not np.isfinite(point).all()):
            return None
        seen.add(key)
        staged.setdefault(uid,{})[side]=point
    if expected_count not in (1, 2) or len(seen)!=expected_count:
        return None
    for uid,_ in seen:
        try: display=_clip_display(rows[uid],staged[uid])
        except (TypeError,ValueError,IndexError): return None
        if display is None:
            return None
    return staged


def _curve_worker_init():
    from threadpoolctl import threadpool_limits
    threadpool_limits(limits=1)


def _fit_curve_job(job):
    if job[2]['kind']=='terminal':
        candidate={}
        result,reason=_fit_piece(*job,candidate_out=candidate)
        if result is not None or 'parameters' not in candidate:return result,reason
        return _fit_piece(*job,articulated=True,warm_start=candidate['parameters'],
                          warm_validation=candidate.get('trainingParameters'))
    return _fit_piece(*job)


def _terminal_parameter_report(meta):
    p=meta['hookParameters']
    return {'model':'circular-arc-tangent-tail','offsetM':p[:3],'rollRad':p[3],
            'pitchRad':p[6] if len(p)>6 else 0.,'yawRad':p[7] if len(p)>7 else 0.,
            'radiusM':meta['bendRadiusM'],'sweepRad':meta['bendAngleRad'],
            'tailLengthM':meta['tailLengthM'],'tailExtentSource':meta.get('tailExtentSource','design'),
            'arcCenterM':np.asarray(meta['arcCenterM']).tolist(),
            'arcNormal':np.asarray(meta['normal']).tolist(),
            'arcTangent':np.asarray(meta['arcTangent']).tolist()}


def fit_curved_pieces(points,normals,inventory,instance_rows,status,owner,*,tree,source_ids,progress=None,workers=1,executor=None):
    """Add bend ownership without reassigning bodies; an injected pool stays caller-owned."""
    from algorithms.rebar_design_curves import build_design_curve_pieces
    if isinstance(workers,bool) or not isinstance(workers,(int,np.integer)) or workers<1:
        raise ValueError('curve workers must be a positive integer')
    started=time.perf_counter()
    pieces=build_design_curve_pieces(inventory)
    rows={r['designUnitId']:r for r in instance_rows}
    body_id_counts={uid:sum(r.get('designUnitId')==uid for r in instance_rows) for uid in rows}
    ambiguous_body_ids={uid for uid,count in body_id_counts.items() if count!=1}
    design_units={u['designUnitId']:u for u in inventory.get('units',[])}
    output=[]; fitted=[];jobs=[];scan_inferred={}
    for i,piece in enumerate(pieces):
        if progress is not None: progress('控制网：准备弯段候选',i,len(pieces))
        report={k:piece[k] for k in ('id','designBarId','kind','designLengthM','geometrySource')}
        report.update(unitIds=[rows[u]['id'] for u in piece['unitIds'] if u in rows],
                      diameterM=piece['radiusM']*2,status='pending',reason='body-not-supported',
                      pointCount=0,designCenterlineM=piece['centerlineM'])
        output.append(report)
        if tree is None or not all(u in rows and rows[u]['status']=='fitted' for u in piece['unitIds']):
            continue
        if piece['geometrySource']=='sampled-design':
            report['reason']='analytic-design-required'
            continue
        piece=dict(piece)
        piece['_designAnchors']=[];piece['_designTangents']=[]
        for anchor in piece['anchors']:
            unit=design_units[anchor['designUnitId']]
            point=np.asarray(unit['startM'] if anchor['side']=='start' else unit['endM'])
            direction=_unit(np.asarray(unit['endM'])-unit['startM'])
            piece['_designAnchors'].append(point)
            piece['_designTangents'].append(-direction if anchor['side']=='start' else direction)
        prepared=_join_model(piece,rows) if piece['kind']=='join' else _terminal_model(piece,rows)
        if prepared is None:
            report['reason']='unsupported-or-inconsistent-attachment';continue
        seed_curve,_=prepared[0](prepared[1])
        search_radius=max(.028,8*piece['radiusM'])
        candidate=tree.query_ball_point(seed_curve[::max(1,len(seed_curve)//32)],r=search_radius)
        local=np.unique(np.concatenate(candidate)).astype(np.int64) if any(len(v) for v in candidate) else np.empty(0,np.int64)
        ids=source_ids[local]
        # Neighbour ownership is fixed before local shape fitting.
        ids=ids[(owner[ids]==0)|np.isin(owner[ids],report['unitIds'])]
        if len(ids)>24000:
            ids=ids[np.linspace(0,len(ids)-1,24000,dtype=int)]
        jobs.append((report,piece,ids))
    # Independent local fits read frozen body state. Only the parent process
    # performs competition and ownership writes, in original design order.
    actual_workers=min(int(workers),8,len(jobs)) if len(jobs)>=8 else 1
    with ExitStack() as stack:
        payloads=((points[ids],None if normals is None else normals[ids],piece,
                   {uid:rows[uid] for uid in piece['unitIds']}) for _,piece,ids in jobs)
        if actual_workers>1:
            if executor is None:
                executor=stack.enter_context(ProcessPoolExecutor(max_workers=actual_workers,
                    mp_context=multiprocessing.get_context('spawn'),initializer=_curve_worker_init))
            results=executor.map(_fit_curve_job,payloads,chunksize=1)
        else:
            results=map(_fit_curve_job,payloads)
        for i,((report,piece,ids),(result,reason)) in enumerate(zip(jobs,results)):
            if progress is not None: progress('控制网：局部参数化弯段',i+1,len(jobs))
            report['reason']=reason
            if result is None:continue
            if piece['kind']=='terminal' and 'hookParameters' in result['meta']:
                # Reuse the full-cloud index: the design-seeded shape crop must
                # not silently become the length bound. Only this thin corridor
                # is queried; the angle/radius solve is never repeated.
                radius=piece['radiusM']
                limit=max(.10,2*piece['designLengthM'])
                curve=result['curve'];direction=_unit(curve[-1]-curve[-2])
                stations=np.arange(0.,limit+radius,radius*2)
                candidates=tree.query_ball_point(curve[-2]+stations[:,None]*direction,r=3*radius)
                local=np.unique(np.concatenate(candidates)).astype(np.int64)
                tail_ids=source_ids[local]
                tail_ids=tail_ids[(owner[tail_ids]==0)|np.isin(owner[tail_ids],report['unitIds'])]
                _finalize_terminal_length(result,points[tail_ids],None if normals is None else normals[tail_ids],radius,limit)
                # Report the shape residual on the original shape sample.
                result['evidence']=_evidence(points[ids],None if normals is None else normals[ids],
                                            result['curve'],radius,result['meta'])
                report['terminalParameters']=_terminal_parameter_report(result['meta'])
                if 'tailLengthEvidence' in result['meta']:
                    report['terminalLengthEvidence']=result['meta']['tailLengthEvidence']
            if result.get('scanGuidedInference'):
                scan_inferred[piece['id']]=result
                continue
            evidence=result['evidence'];support=evidence['support']
            report.update(status='fitted',centerlineM=result['curve'].tolist(),
                          fittedLengthM=_length(result['curve']),
                          rmseM=float(np.sqrt(np.mean(evidence['residual'][support]**2))),
                          bendRadiusM=result['meta']['bendRadiusM'],bendAngleRad=result['meta']['bendAngleRad'],
                          attachmentLengthM=result['meta'].get('attachmentLengthM',0.),
                          evidence={'bendSupport':evidence['bendSupport'],'coveredBins':evidence['coveredBins'],
                                    'crossSectionSpread':evidence['crossSectionSpread'],'normalSpread':evidence['normalSpread'],
                                    'bendMaxGapM':evidence['bendMaxGapM'],'attachmentMaxGapM':evidence['attachmentMaxGapM'],
                                    'validationMaxChangeM':result['validationMaxChangeM'],'validationSupport':result['validationSupport']})
            if piece['kind']=='join':
                report.update(tangentRadiusM=result['meta']['tangentRadiusM'],bridgeLengthM=result['meta']['bridgeLengthM'])
            else:
                report.update(tailLengthM=result['meta']['tailLengthM'],attachmentStatus='body-anchored-transition')
            # Assignment must query the complete final tube, not the bounded solve sample.
            query_radius=max(.008,2*piece['radiusM'])
            candidate=tree.query_ball_point(_curve_query_stations(result['curve'],query_radius),r=query_radius)
            local=np.unique(np.concatenate(candidate)).astype(np.int64)
            full=source_ids[local]
            full=full[(owner[full]==0)&np.isin(status[full],[2,3])]
            ev=_evidence(points[full],None if normals is None else normals[full],result['curve'],piece['radiusM'],result['meta'])
            fitted.append((report,result,full,ev))
    # Competing curved pieces must be uniquely better. No accepted body owner is reassigned.
    union=np.unique(np.concatenate([x[2] for x in fitted])) if fitted else np.empty(0,np.int64)
    best=np.full(len(union),np.inf);second=np.full(len(union),np.inf);winner=np.full(len(union),-1,int)
    for j,(report,result,ids,ev) in enumerate(fitted):
        at=np.searchsorted(union,ids)
        score=np.where(ev['support'],np.abs(ev['residual'])/ev['tolerance'],np.inf)
        improve=score<best[at]
        second[at]=np.where(improve,best[at],np.minimum(second[at],score))
        best[at[improve]]=score[improve];winner[at[improve]]=j
    unique=(best<=1.)&(second>best+.18)
    display_changes={}
    for j,(report,result,ids,ev) in enumerate(fitted):
        selected=union[unique&(winner==j)]
        # Existing same-parent body support is valid evidence; added points are
        # kept separately and never inflate the physical body/parent counts.
        if len(selected)<12:
            report.update(status='pending',reason='insufficient-unique-curve-support')
            report.pop('centerlineM',None)
            report.pop('fittedLengthM',None)
            continue
        station=_project(points[selected],result['curve'])[3]
        if len(report['unitIds'])==2:
            unit_owner=np.where(station<_length(result['curve'])/2,report['unitIds'][0],report['unitIds'][1])
        else:unit_owner=np.full(len(selected),report['unitIds'][0],np.uint32)
        owner[selected]=unit_owner;status[selected]=1
        report['pointCount']=len(selected)
        for anchor,position in result['meta']['attachments']:
            display_changes.setdefault(anchor['designUnitId'],{})[anchor['side']]=position
    # Confirmed bodies establish identity and attachment frames for missing
    # joins and terminal hooks; the design plane fixes a terminal's remaining roll.
    # Pure design inference never owns points. Independently checked local
    # observations on a scan-guided terminal may enter the separate pass below.
    inferred=0
    partial=[]
    reports_by_id={report['id']:report for report in output}
    for piece in pieces:
        report=reports_by_id[piece['id']]
        if report['status']!='pending':
            continue
        inferred_join=(_inferred_terminal(piece,rows,design_units,ambiguous_body_ids)
                       if piece['kind']=='terminal' else _inferred_join(piece,rows,ambiguous_body_ids))
        if inferred_join is None:
            continue
        curve,meta=inferred_join
        basis=meta.get('inferenceBasis')
        if piece['id'] in scan_inferred:
            candidate=scan_inferred[piece['id']]
            curve,meta=candidate['curve'],candidate['meta']
        else:
            report.pop('terminalParameters',None)
        staged=_stage_inferred_attachments(rows,display_changes,meta['attachments'],
                                           expected_count=1 if piece['kind']=='terminal' else 2)
        if staged is None:
            continue
        display_changes=staged
        report.update(inferredCenterlineM=curve.tolist(),connectionStatus='design-inferred',
                      inferredLengthM=_length(curve),
                      inferenceMethod='body-anchored-design-terminal' if piece['kind']=='terminal' else 'body-anchored-design-join')
        if piece['kind']=='terminal':
            report['inferenceBasis']=basis
            if piece['id'] in scan_inferred:
                report['inferenceMethod']='scan-guided-parametric-terminal'
                report['inferenceValidation']={
                    'observedP90ChangeM':candidate['observedValidationP90ChangeM'],
                    'heldSupport':candidate['validationSupport'],
                    'coveredBins':candidate['evidence']['coveredBins']}
                if 'validationCurve' in candidate and report['reason'] != 'ambiguous-curve-support':
                    query_radius=max(.008, 2*piece['radiusM'])
                    groups=tree.query_ball_point(_curve_query_stations(curve,query_radius),r=query_radius)
                    local=np.unique(np.concatenate(groups)).astype(np.int64)
                    ids=source_ids[local]
                    ids=ids[(owner[ids]==0)|np.isin(owner[ids],report['unitIds'])]
                    check_curve=candidate['validationCurve'].copy()
                    # Length is independently validated by the terminal-length
                    # estimator. Apply that scalar to the validation pose too.
                    if candidate['meta'].get('tailLengthEvidence') and len(check_curve)==58:
                        check_curve[-1]=check_curve[-2]+candidate['meta']['tailLengthM']*_unit(check_curve[-1]-check_curve[-2])
                    support,intervals,change=_local_curve_support(points[ids],None if normals is None else normals[ids],
                        curve,check_curve,piece['radiusM'],candidate['meta']['normal'])
                    support &= (owner[ids]==0)&np.isin(status[ids],[2,3])
                    if support.sum()>=12:
                        residual=np.abs(_project(points[ids],curve)[0]-piece['radiusM'])
                        partial.append((report,ids[support],residual[support]/max(.0009,.36*piece['radiusM']),intervals,change))
        inferred+=1
    # Fully confirmed curves already own their points. Partial observations
    # compete with each other only on remaining records, with the same margin.
    union=np.unique(np.concatenate([item[1] for item in partial])) if partial else np.empty(0,np.int64)
    best=np.full(len(union),np.inf);second=best.copy();winner=np.full(len(union),-1,int)
    for j,(_,ids,score,_,_) in enumerate(partial):
        at=np.searchsorted(union,ids);improve=score<best[at]
        second[at]=np.where(improve,best[at],np.minimum(second[at],score))
        best[at[improve]]=score[improve];winner[at[improve]]=j
    unique=(best<=1.)&(second>best+.18)
    for j,(report,_,_,intervals,change) in enumerate(partial):
        selected=union[unique&(winner==j)]
        if len(selected)<12:
            continue
        owner[selected]=report['unitIds'][0];status[selected]=1
        report['localSupport']={'method':'independent-continuous-surface-intervals',
            'pointCount':len(selected),'intervalsM':intervals,'validationMaxChangeM':change}
    for uid,changes in display_changes.items():
        display=_clip_display(rows[uid],changes)
        if display is not None:rows[uid]['bodyDisplayCenterlineM']=display
    counts=np.bincount(owner,minlength=len(instance_rows)+1)
    for row in instance_rows:
        row['pointCount']=int(counts[row['id']])
    summary={'designPieces':len(output),'fittedPieces':sum(r['status']=='fitted' for r in output),
             'pendingPieces':sum(r['status']!='fitted' for r in output),
             'inferredPieces':inferred,'unresolvedPieces':sum(r['status']!='fitted' for r in output)-inferred,
             'locallySupportedPieces':sum('localSupport' in r for r in output),
             'locallyMatchedPoints':sum(r.get('localSupport',{}).get('pointCount',0) for r in output),
             'matchedPoints':sum(r['pointCount']+r.get('localSupport',{}).get('pointCount',0) for r in output),
             'workers':actual_workers,'elapsedS':time.perf_counter()-started}
    return output,summary
