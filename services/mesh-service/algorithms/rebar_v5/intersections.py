"""Read-only finite observed-centreline intersection products (never labels)."""
from __future__ import annotations
import math
import numpy as np


def closest_segments(a,b,c,d):
    u,v,w=b-a,d-c,a-c
    aa,bb,cc=float(u@u),float(u@v),float(v@v)
    if min(aa,cc)<1e-20:
        return None
    dd,ee=float(u@w),float(v@w)
    den=aa*cc-bb*bb
    s=np.clip((bb*ee-cc*dd)/den,0,1) if den>1e-20 else 0.0
    t=(bb*s+ee)/cc
    if t<0: t=0.;s=np.clip(-dd/aa,0,1)
    elif t>1: t=1.;s=np.clip((bb-dd)/aa,0,1)
    return a+s*u,c+t*v


def compute_intersections(instances, tolerance=0.001, min_angle_degrees=5.0):
    segments=[]
    for item in sorted(instances,key=lambda x:x["id"]):
        # No fallback to centerline: it may contain unseen bridges.
        for si,path in enumerate(item.get("observedSegments",[])):
            points=np.asarray(path["points"],dtype=float)
            if points.ndim!=2 or points.shape[1]!=3 or not np.isfinite(points).all():
                raise ValueError("invalid observed centreline")
            for ei,(a,b) in enumerate(zip(points[:-1],points[1:])):
                if np.linalg.norm(b-a)>1e-10:
                    segments.append((a,b,{"instanceId":int(item["id"]),"segmentIndex":si,"edgeIndex":ei}))
    # Sweep-and-prune index bounds candidate comparisons in separated scenes.
    segments.sort(key=lambda x:(min(x[0][0],x[1][0]),x[2]["instanceId"],x[2]["segmentIndex"],x[2]["edgeIndex"]))
    candidates=[]; active=[]
    cosine=math.cos(math.radians(min_angle_degrees))
    for a,b,ref in segments:
        lo,hi=np.minimum(a,b),np.maximum(a,b)
        active=[s for s in active if max(s[0][0],s[1][0])+tolerance>=lo[0]]
        for c,d,other in active:
            if other["instanceId"]==ref["instanceId"]: continue
            if np.any(np.maximum(c,d)+tolerance<lo) or np.any(np.minimum(c,d)-tolerance>hi): continue
            dot=abs(float((b-a)@(d-c)))/(np.linalg.norm(b-a)*np.linalg.norm(d-c))
            if dot>cosine: continue
            pair=closest_segments(a,b,c,d)
            if pair is None: continue
            x,y=pair; residual=float(np.linalg.norm(x-y))
            if residual>tolerance+1e-12: continue
            candidates.append({"position":((x+y)/2).tolist(),"instanceIds":sorted([ref["instanceId"],other["instanceId"]]),
                "segmentRefs":[other.copy(),ref.copy()],"angleDegrees":float(np.degrees(np.arccos(np.clip(dot,0,1)))),
                "residual":residual,"evidence":"observed-finite-centerlines"})
        active.append((a,b,ref))
    groups=[]
    for candidate in sorted(candidates,key=lambda x:(*x["position"],x["instanceIds"])):
        position=np.asarray(candidate["position"])
        group=next((g for g in groups if set(g[0]["instanceIds"])&set(candidate["instanceIds"])
            and all(np.linalg.norm(position-np.asarray(x["position"]))<=tolerance for x in g)),None)
        if group is None: groups.append([candidate])
        else: group.append(candidate)
    result=[]
    for ident,group in enumerate(groups,1):
        refs={tuple(sorted(r.items())) for x in group for r in x["segmentRefs"]}
        result.append({"id":ident,"position":np.mean([x["position"] for x in group],axis=0).tolist(),
            "instanceIds":sorted({i for x in group for i in x["instanceIds"]}),
            "segmentRefs":[dict(r) for r in sorted(refs)],"angleDegrees":min(x["angleDegrees"] for x in group),
            "residual":max(x["residual"] for x in group),"evidence":"observed-finite-centerlines"})
    return result
