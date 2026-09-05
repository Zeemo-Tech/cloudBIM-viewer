"""Layered and strip-local candidate generation; observed rod instances."""
from __future__ import annotations
from dataclasses import replace
import numpy as np
from scipy.optimize import least_squares
from scipy.signal import find_peaks
from scipy.spatial import cKDTree

from ..rebar_v4_geometry import (line_primitives, deduplicate_primitives,
    trace_primitive_graph, perpendicular_basis, canonical_direction, LinePrimitive)


def hough_lines(xy, tangents, p):
    """Orientation-gated 2-D Hough; bounded one-angle accumulators."""
    if len(xy)<p.min_primitive_votes:
        return []
    result=[]
    for angle in np.arange(0.,180.,p.hough_angle_step_degrees):
        axis=np.array([np.cos(np.deg2rad(angle)),np.sin(np.deg2rad(angle))])
        selected=np.abs(tangents@axis)>=np.cos(np.deg2rad(p.orientation_tolerance_degrees))
        if selected.sum()<p.min_primitive_votes: continue
        normal=np.array([-axis[1],axis[0]])
        rho=xy[selected]@normal
        bins=np.rint(rho/p.offset_cell_size).astype(np.int64)
        unique,count=np.unique(bins,return_counts=True)
        # Compact sparse bins avoid arrays proportional to world coordinates.
        peaks=np.flatnonzero(count>=max(p.min_primitive_votes,int(count.max()*.35)))
        for row in peaks:
            result.append((int(count[row]),axis, float(unique[row]*p.offset_cell_size)))
    result.sort(key=lambda x:-x[0])
    accepted=[]
    for votes,axis,rho in result:
        if any(abs(float(axis@a))>np.cos(np.deg2rad(3)) and abs(rho-r)<p.offset_cell_size*2 for _,a,r in accepted): continue
        accepted.append((votes,axis,rho))
        if len(accepted)>=p.max_orientation_modes*8: break
    return accepted


def primitives(points, f, p, rows=None, minimum_votes=None):
    if rows is None: rows=np.arange(len(points))
    return line_primitives(points[rows],f["axis_tangent"][rows],f["axis_linearity"][rows],
        minimum_linearity=p.min_linearity, orientation_tolerance_degrees=p.orientation_tolerance_degrees,
        minimum_votes=minimum_votes or p.min_primitive_votes, maximum_modes=p.max_orientation_modes,
        offset_cell=p.offset_cell_size, axial_gap=p.axial_gap, minimum_length=p.min_primitive_length,
        min_radius=p.min_radius,max_radius=p.max_radius)


def hough_seeds(points, tangents, rows, candidates, basis, p):
    """Turn voted Hough cells into finite 3-D primitive seeds.

    These seeds are deliberately additive: straight high-vote runs receive a
    global vote-backed candidate while the local detector remains responsible
    for changing-direction hooks and other curved observations.
    """
    if not candidates or not len(rows):
        return []
    cloud = points[rows]
    coords = cloud @ basis.T
    tangent2 = tangents[rows] @ basis.T
    result = []
    for votes, axis2, rho in candidates:
        normal2 = np.array([-axis2[1], axis2[0]])
        aligned = np.abs(tangent2 @ axis2) >= np.cos(np.deg2rad(p.orientation_tolerance_degrees))
        # Keep neighbouring cylinders out of a voted surface stripe. The raw
        # circle refit below expands around this seed to recover its centre.
        selected = aligned & (np.abs(coords @ normal2 - rho) <= .50*p.offset_cell_size)
        if int(selected.sum()) < p.min_primitive_votes:
            continue
        support = cloud[selected]
        direction = canonical_direction(axis2 @ basis)
        center = np.mean(support, axis=0)
        axial = (support-center) @ direction
        order = np.argsort(axial, kind="stable")
        # A Hough peak is a line hypothesis, never evidence that an empty
        # middle span is observed. Each unsupported axial gap gets a separate
        # seed; tracing may later add an explicitly inferred bridge.
        groups = np.split(order, np.flatnonzero(np.diff(axial[order]) > p.axial_gap) + 1)
        for group in groups:
            if len(group) < p.min_primitive_votes:
                continue
            piece = support[group]
            piece_axial = axial[group]
            lo, hi = np.percentile(piece_axial, [1, 99])
            if hi-lo < p.min_primitive_length:
                continue
            radial = np.linalg.norm(piece-center-piece_axial[:, None]*direction, axis=1)
            radius = float(np.clip(np.quantile(radial, .55), p.min_radius, p.max_radius))
            result.append(LinePrimitive(center+lo*direction, center+hi*direction, direction,
                radius, int(len(group)), float(votes)))
    return result


def refine_axis(primitive, support, p):
    """Fit a physical circular section, allowing partial observed arcs."""
    axis=primitive.tangent; center=(primitive.start+primitive.end)/2
    a,b=perpendicular_basis(axis); basis=np.vstack((a,b))
    delta=support-center; axial=delta@axis
    radial=delta-axial[:,None]*axis
    selected=(np.abs(axial)<=primitive.length/2+p.support_distance)&(np.linalg.norm(radial,axis=1)<=p.max_radius*1.8+p.support_distance)
    cloud=support[selected]
    if len(cloud)<p.min_primitive_votes: return primitive,0.25
    if len(cloud)>3000: cloud=cloud[np.linspace(0,len(cloud)-1,3000,dtype=int)]
    xy=(cloud-center)@basis.T
    # Restrict support to the current physical rod before fitting, to prevent
    # an adjacent parallel rod from pulling the fitted centre into the gap.
    # Keep the circle fit local to its seed. A 16 mm centre-to-centre pair has
    # only an 8 mm surface gap; the previous 1.6× gate recruited the neighbour
    # and fitted a broad phantom cylinder between both physical bars.
    near=np.linalg.norm(xy,axis=1)<=min(
        p.max_radius*1.35,
        max(primitive.radius*1.35, p.min_radius*2)+p.support_distance,
    )
    if near.sum()>=p.min_primitive_votes: xy=xy[near]
    initial=np.array([0.,0.,np.clip(primitive.radius,p.min_radius+1e-8,p.max_radius-1e-8)])
    fit=least_squares(lambda x:np.linalg.norm(xy-x[:2],axis=1)-x[2], initial,
        bounds=([-p.max_radius,-p.max_radius,p.min_radius],[p.max_radius,p.max_radius,p.max_radius]),
        loss="soft_l1", f_scale=0.001, max_nfev=60)
    errors=np.abs(np.linalg.norm(xy-fit.x[:2],axis=1)-fit.x[2])
    eig=np.linalg.svd(fit.jac,compute_uv=False)
    condition=float(eig[-1]/max(eig[0],1e-12))
    residual=float(np.quantile(errors,.8))
    # A one-sided scan is weaker than a full ring, but remains physical when
    # its centre/radius solution has bounded leverage and small residual.
    if not fit.success or condition<.015 or residual>max(.002,p.support_distance*.6):
        return primitive,.35
    shift=fit.x[:2]@basis
    return replace(primitive,start=primitive.start+shift,end=primitive.end+shift,radius=float(fit.x[2])),float(np.clip(1-residual/p.support_distance,0,1))


def _same_axis(first, second, p):
    """True only for overlapping physical axes, never merely close bars."""
    direction = canonical_direction(first.tangent + second.tangent)
    if abs(float(first.tangent @ second.tangent)) < np.cos(np.deg2rad(5.0)):
        return False
    first_mid = (first.start + first.end) / 2
    second_mid = (second.start + second.end) / 2
    delta = second_mid - first_mid
    lateral = np.linalg.norm(delta - float(delta @ direction) * direction)
    # An 8 mm diameter close pair is 16 mm apart; use a substantially tighter
    # centre gate than either tube's labeling support radius.
    if lateral > max(.006, .45 * (first.radius + second.radius)):
        return False
    first_lo, first_hi = -first.length/2, first.length/2
    second_at = float(delta @ direction)
    second_lo, second_hi = second_at-second.length/2, second_at+second.length/2
    overlap = max(0., min(first_hi, second_hi) - max(first_lo, second_lo))
    return overlap >= .60 * min(first.length, second.length)


def _deduplicate_refined(refined, p):
    accepted=[]
    for primitive, score in sorted(refined, key=lambda item:(-item[0].length, -item[1], -item[0].point_count)):
        if any(_same_axis(primitive, other, p) for other, _ in accepted):
            continue
        accepted.append((primitive, score))
    return accepted


def trace(prims, support,p, role,layer_id=None):
    if not prims:return []
    prims=deduplicate_primitives(prims)
    refined=[refine_axis(primitive,support,p) for primitive in prims]
    # A primitive is a physical rod candidate only after a conditioned circle
    # fit. Point count alone used to admit every Hough/local surface stripe.
    refined=[item for item in refined if item[1] >= .45 and not (
        item[0].radius >= .90*p.max_radius and item[1] < .96
    )]
    refined=_deduplicate_refined(refined, p)
    prims=[item[0] for item in refined]
    if not prims:
        return []
    # Separate calls for each role ensure a diagonal cannot chain into chords.
    traced=trace_primitive_graph(prims,join_gap=p.join_gap,observed_join_gap=p.observed_join_gap,
        maximum_turn_degrees=p.max_turn_degrees if role=="planar" else 8.,minimum_instance_length=p.min_instance_length)
    result=[]
    for item in traced:
        scores=[]
        for segment in item.observed_segments:
            midpoint=np.mean(segment,axis=0)
            matching=[score for primitive,score in refined
                if np.linalg.norm(midpoint-(primitive.start+primitive.end)/2)
                <= primitive.length/2+p.observed_join_gap]
            if matching: scores.extend(matching)
        result.append({"id":0,"role":role,"layerId":layer_id,"centerline":item.centerline.tolist(),
            "observedSegments":[{"points":segment.tolist()} for segment in item.observed_segments],
            "inferredSegments":[{"points":segment.tolist(),"source":"geometric-gap"} for segment in item.inferred_segments],
            "radius":float(item.radius),"pointCount":int(item.point_count),"length":float(item.length),
            "confidence":float(np.mean(scores)) if scores else .35,"evidence":"mixed" if item.inferred_segments else "observed",
            "direction":item.tangent.tolist()})
    return result


def planar_bars(points,f,p):
    # Surface-side stripes on a horizontal tube often acquire a small vertical
    # slope. Keep planar extraction genuinely planar; steep material belongs
    # to the web pass instead of creating many diagonal copies per bar.
    planar_limit=np.sin(np.deg2rad(min(p.planar_angle_degrees, 5.0)))
    horizontal=(np.abs(f["axis_tangent"][:,2])<=planar_limit)&(f["axis_linearity"]>=p.min_linearity)
    rows=np.flatnonzero(horizontal)
    if not len(rows): return [],[],{"houghCandidateCount":0}
    order=rows[np.argsort(points[rows,2],kind="stable")]
    groups=np.split(order,np.flatnonzero(np.diff(points[order,2])>p.layer_gap)+1)
    instances=[];layers=[];count=0
    for indices in groups:
        if len(indices)<p.min_primitive_votes:continue
        height=float(np.median(points[indices,2])); ident=len(layers)+1
        candidates=hough_lines(points[indices,:2],f["axis_tangent"][indices,:2],p)
        count+=len(candidates)
        layer={"id":ident,"height":height,"minHeight":float(points[indices,2].min()),"maxHeight":float(points[indices,2].max()),"pointCount":len(indices)}
        layers.append(layer)
        # Hough gives global straight axes; local tangent primitives retain
        # curves whose direction changes along an observed hook.
        rows_mask=np.zeros(len(points),bool);rows_mask[indices]=True
        # A coarse connected height band may contain several densely spaced
        # layers. Its support must cover every member, not only its median.
        band=(points[:,2]>=points[indices,2].min()-p.max_radius)&(points[:,2]<=points[indices,2].max()+p.max_radius)
        local=primitives(points,f,p,np.flatnonzero(band))
        local=[x for x in local if abs(x.tangent[2])<=planar_limit]
        # Hough votes actively seed straight planar candidates; local primitives
        # are retained to recover hooks and other nonconstant-tangent paths.
        local.extend(hough_seeds(points, f["axis_tangent"], indices, candidates,
            np.array([[1.,0.,0.],[0.,1.,0.]]), p))
        instances.extend(trace(local,points[band],p,"planar",ident))
    return deduplicate_instances(instances,p),layers,{"houghCandidateCount":count}


def distance_to_paths(points, instances, p, protect=False):
    selected=np.zeros(len(points),bool)
    for item in instances:
        for path in item["observedSegments"]:
            q=np.asarray(path["points"])
            for a,b in zip(q[:-1],q[1:]):
                axis=b-a; length=float(np.linalg.norm(axis))
                if length<1e-10:continue
                t=(points-a)@axis/(length*length)
                distance=np.linalg.norm(points-(a+np.clip(t,0,1)[:,None]*axis),axis=1)
                in_range=(t>=0)&(t<=1)
                if protect: in_range&=(t*length>p.axis_radius)&((1-t)*length>p.axis_radius)
                selected|=in_range&(distance<=item["radius"]+p.support_distance)
    return selected


def web_bars(points,f,p,layers,planar):
    linear=f["axis_linearity"]>=p.min_linearity
    diagonal=np.abs(f["axis_tangent"][:,2])>np.sin(np.deg2rad(p.planar_angle_degrees))
    rows=np.flatnonzero(linear&diagonal)
    if len(rows)<5:return [],{"stripCount":0,"houghCandidateCount":0,"fallback":not bool(layers)}
    heights=[l["height"] for l in layers]
    if len(heights)>=2:
        # Soft height bounds: points outside remain available to fallback.
        bounded=(points[rows,2]>=min(heights)-p.axis_radius)&(points[rows,2]<=max(heights)+p.axis_radius)
    else:bounded=np.ones(len(rows),bool)
    azimuths=[]
    for tangent in f["axis_tangent"][rows]:
        direction=tangent[:2]; norm=np.linalg.norm(direction)
        if norm<.1:continue
        direction=direction/norm
        if not any(abs(direction@other)>np.cos(np.deg2rad(10)) for other in azimuths):azimuths.append(direction)
        if len(azimuths)>=12:break
    detected=[];strip_count=hough_count=0
    for direction in azimuths:
        transverse=np.array([-direction[1],direction[0]])
        offsets=points[rows,:2]@transverse
        step=p.web_strip_width-p.web_strip_overlap
        for start in np.arange(offsets.min()-p.web_strip_overlap,offsets.max()+step,step):
            selected=rows[(offsets>=start)&(offsets<start+p.web_strip_width)&bounded]
            if len(selected)<5:continue
            strip_count+=1
            if strip_count > max(1024, p.max_orientation_modes * 64):
                raise ValueError("V5 web strip budget exceeded; reduce scene extent or increase strip width")
            side=np.column_stack((points[selected,:2]@direction,points[selected,2]))
            tangent=np.column_stack((f["axis_tangent"][selected,:2]@direction,f["axis_tangent"][selected,2]))
            candidates=hough_lines(side,tangent,p)
            hough_count+=len(candidates)
            local=primitives(points,f,p,selected,minimum_votes=5)
            local.extend(hough_seeds(points, f["axis_tangent"], selected, candidates,
                np.vstack((np.array([direction[0], direction[1], 0.]), np.array([0.,0.,1.]))), p))
            detected.extend(x for x in local if abs(x.tangent[2])>np.sin(np.deg2rad(p.planar_angle_degrees)))
    # Bounded all-direction residual recovery is independent from layer priors.
    fallback=primitives(points,f,p,rows,minimum_votes=5)
    detected=deduplicate_primitives(detected+fallback)
    return deduplicate_instances(trace(detected,points,p,"web"),p),{"stripCount":strip_count,"houghCandidateCount":hough_count,"fallback":True}


def deduplicate_instances(instances,p):
    accepted=[]
    for item in sorted(instances,key=lambda x:(-x["length"],tuple(x["centerline"][0]))):
        for other in accepted:
            if item["role"]!=other["role"]:continue
            first=LinePrimitive(np.asarray(item["centerline"])[0], np.asarray(item["centerline"])[-1],
                np.asarray(item["direction"]), float(item["radius"]), int(item["pointCount"]), 0.)
            second=LinePrimitive(np.asarray(other["centerline"])[0], np.asarray(other["centerline"])[-1],
                np.asarray(other["direction"]), float(other["radius"]), int(other["pointCount"]), 0.)
            if _same_axis(first, second, p):
                break
        else:
            accepted.append(item)
    return accepted


def finalize_instances(instances,p):
    representatives=[]
    instances=sorted(instances,key=lambda x:(tuple(np.round(np.min(np.asarray(x["centerline"]),axis=0),8)),x["role"]))
    for ident,item in enumerate(instances,1):
        item["id"]=ident
        direction=np.asarray(item["direction"])
        match=next((i for i,d in enumerate(representatives) if abs(direction@d)>=np.cos(np.deg2rad(12))),None)
        if match is None:match=len(representatives);representatives.append(direction)
        item["directionId"]=match+1
    connections=[]
    planar=[i for i in instances if i["role"]=="planar"]
    for web in (i for i in instances if i["role"]=="web"):
        for end,point in enumerate(np.asarray(web["centerline"])[[0,-1]]):
            connected=[i["id"] for i in planar if distance_to_paths(point[None], [i], replace(p,support_distance=p.support_distance+web["radius"]))[0]]
            if connected:connections.append({"webInstanceId":web["id"],"endpointIndex":end,"candidateInstanceIds":connected,"confirmed":len(connected)==1})
    return instances,connections
