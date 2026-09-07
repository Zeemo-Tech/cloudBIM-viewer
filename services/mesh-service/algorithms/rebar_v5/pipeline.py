"""Stage orchestration over immutable, disk-backed raw point records."""
from __future__ import annotations
from dataclasses import asdict
import hashlib
import json
from pathlib import Path
import shutil
import tempfile
import time
import logging
import resource

import numpy as np
from scipy.spatial import cKDTree

from ..rebar_base import RebarAnalysis, RebarInputContext, RebarPointAttributes
from ..rebar_v4_geometry import build_segment_index
from .contracts import (Params, UNKNOWN, TABLE, REBAR, NOISE, FIXTURE, AMBIGUOUS, VERSION,
    FIXTURE_SQUARE_TUBE, FIXTURE_PLATE, FIXTURE_BOLT, ROLE_PLANAR, ROLE_WEB)
from .features import denoise, multiscale
from .spatial import SpatialStore, SpatialBudgetExceeded
from .scene import detect_table, table_mask, detect_fixtures, fixture_mask, fixture_candidates, refine_fixture_faces
from .bolts import detect_bolts, bolt_mask
from .rods import planar_bars, web_bars, distance_to_paths, finalize_instances
from .hooks import recover_terminal_hooks
from .intersections import compute_intersections
from .verification import verify_raw_instances
from .ownership import merge_fragments
from .projection import project_surface_top2


class StageLog(list):
    def append(self,item):
        item['peakRssBytes']=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss*1024
        super().append(item)
        logging.getLogger(__name__).info('V5 stage %s: %.2f s, peak RSS %.1f MiB',item['name'],item.get('elapsedS',0),item['peakRssBytes']/1024**2)


class Runtime:
    """Own temporary resources explicitly; no temporary paths enter JSON."""
    def __init__(self,p):
        self.temp=tempfile.TemporaryDirectory(prefix="rebar-v5-")
        self.path=Path(self.temp.name)
        self.store=SpatialStore(self.path/"spatial",p)
        self.p=p;self.feature_chunks=[];self.feature_bounds=[];self.noise_maps={};self.suspect_maps={}
        self.cached_projection=None
        self.label_chunks={}
        self.transfer_cache=None
        self.update_chunks={}
        self.updated_features=set()

    def close(self):
        self.noise_maps.clear();self.suspect_maps.clear()
        self.transfer_cache=None
        self.temp.cleanup()

    def masks(self,records,name="noise"):
        mapping=self.noise_maps if name=="noise" else self.suspect_maps
        keys=np.floor(records["xyz"]/self.p.block_size).astype(np.int64)
        result=np.zeros(len(records),bool)
        if not len(records):return result
        unique,inverse=np.unique(keys,axis=0,return_inverse=True)
        for ordinal,key in enumerate(unique):
            key=tuple(map(int,key));rows=np.flatnonzero(inverse==ordinal)
            source=self.store.records(key)["source_index"]
            positions=np.searchsorted(source,records["source_index"][rows])
            result[rows]=mapping[key][positions]
        return result

    def assign_mask(self,records,values,name):
        mapping=self.noise_maps if name=="noise" else self.suspect_maps
        keys=np.floor(records["xyz"]/self.p.block_size).astype(np.int64)
        unique,inverse=np.unique(keys,axis=0,return_inverse=True)
        for ordinal,key in enumerate(unique):
            key=tuple(map(int,key));rows=np.flatnonzero(inverse==ordinal)
            positions=np.searchsorted(self.store.records(key)["source_index"],records["source_index"][rows])
            mapping[key][positions]=values[rows]


def prepare(context,p):
    runtime=Runtime(p);stages=StageLog()
    try:
        start=time.perf_counter();runtime.store.build(context.iter_chunks())
        for key,(_,count) in runtime.store.cells.items():
            for name,mapping in (("noise",runtime.noise_maps),("suspect",runtime.suspect_maps)):
                # Two byte masks require 2N bytes, not thousands of open mmap
                # file descriptors. Coordinates and features remain on disk.
                mapping[key]=np.zeros(count,dtype=np.uint8)
        cores=list(runtime.store.cores())
        stages.append({"name":"spatial-index","inputPointCount":runtime.store.count,"coreCount":len(cores),"elapsedS":time.perf_counter()-start})
        start=time.perf_counter();noise_count=suspect_count=0
        for _,lo,hi in cores:
            support=runtime.store.query(lo-p.halo-p.detection_voxel_size,hi+p.halo+p.detection_voxel_size)
            core=np.all((support["xyz"]>=lo)&(support["xyz"]<hi),axis=1)
            records=support[core]
            noise,suspect=denoise(support["xyz"],records["xyz"],p)
            runtime.assign_mask(records,noise,"noise");runtime.assign_mask(records,suspect,"suspect")
            noise_count+=int(noise.sum());suspect_count+=int(suspect.sum())
        stages.append({"name":"denoise","inputPointCount":runtime.store.count,"confirmedPointCount":noise_count,"pendingPointCount":suspect_count,"elapsedS":time.perf_counter()-start})
        start=time.perf_counter();parts=[];feature_parts=[];indices_parts=[];count=0
        # The detection lattice is anchored in world coordinates. Keep the
        # lowest source index in each cell, independent of reader chunk sizes.
        for ordinal,(_,lo,hi) in enumerate(cores):
            support=runtime.store.query(lo-p.halo-p.detection_voxel_size,hi+p.halo+p.detection_voxel_size)
            core=np.all((support["xyz"]>=lo)&(support["xyz"]<hi),axis=1)
            records=support[core];noise=runtime.masks(support)
            features=multiscale(support["xyz"][~noise],records["xyz"],p)
            core_noise=noise[core]
            for value in features.values():value[core_noise]=0
            payload={"source_index":records["source_index"],"xyz":records["xyz"],"noise":core_noise.astype(np.uint8),
                     "noise_suspect":runtime.masks(records,"suspect").astype(np.uint8),**features}
            path=runtime.path/f"features-{ordinal:06d}.npz"
            np.savez_compressed(path,**payload)
            runtime.feature_chunks.append(path)
            runtime.feature_bounds.append((lo.copy(),hi.copy()))
            # Pick the minimum source record across the entire voxel, including
            # its halo portion. Only the owning core emits that representative.
            # This also stays stable when dense spatial cores are subdivided.
            active=np.flatnonzero(~noise)
            voxel=np.floor(support["xyz"][active]/p.detection_voxel_size).astype(np.int64)
            _,first=np.unique(voxel,axis=0,return_index=True)
            representatives=active[first]
            representatives=representatives[core[representatives]]
            keep=np.searchsorted(records["source_index"],support["source_index"][representatives])
            count+=len(keep)
            if count>p.detection_point_limit:
                raise ValueError("V5 detection candidate budget exceeded; choose a larger detection voxel without dropping raw features")
            parts.append(records["xyz"][keep]);indices_parts.append(records["source_index"][keep]);feature_parts.append({k:v[keep] for k,v in features.items()})
        points=np.concatenate(parts) if parts else np.empty((0,3))
        indices=np.concatenate(indices_parts) if indices_parts else np.empty(0,np.uint64)
        # Stable source ordering makes all tie-breaking independent of blocks.
        order=np.argsort(indices,kind="stable");points=points[order];indices=indices[order]
        f={key:np.concatenate([part[key] for part in feature_parts])[order] for key in feature_parts[0]} if feature_parts else multiscale(points,points,p)
        stages.append({"name":"features","inputPointCount":runtime.store.count,"featurePointCount":runtime.store.count,"detectionPointCount":len(points),"elapsedS":time.perf_counter()-start})
        return runtime,points,f,stages
    except Exception:
        runtime.close();raise


def refine_fixture_support(runtime, plane, proposals, p):
    """Re-fit detected finite faces from coalesced bounded raw-store regions."""
    regions=[]
    proposal_bounds={}
    for face in proposals:
        try:
            origin,normal,axes,extent=(np.asarray(face[key],dtype=float) for key in ("origin","normal","axes","halfExtent"))
        except (KeyError,TypeError,ValueError):
            continue
        if axes.shape != (2,3) or extent.shape != (2,) or not np.isfinite(np.r_[origin,normal,axes.ravel(),extent]).all():continue
        reach=np.abs(axes).T@extent+np.abs(normal)*(p.fixture_surface_distance+p.fixture_offset_gap)
        lo,hi=origin-reach,origin+reach
        proposal_bounds[id(face)]=(lo,hi)
        overlaps=[index for index,(lower,upper,_) in enumerate(regions) if np.all(lo<=upper)&np.all(hi>=lower)]
        if not overlaps:
            regions.append([lo,hi,[face]]);continue
        lower=np.minimum.reduce([lo]+[regions[index][0] for index in overlaps])
        upper=np.maximum.reduce([hi]+[regions[index][1] for index in overlaps])
        grouped=[face]
        for index in reversed(overlaps):
            grouped.extend(regions.pop(index)[2])
        regions.append([lower,upper,grouped])
    refined=[]
    for lo,hi,faces in regions:
        try:
            records=runtime.store.query(lo,hi)
        except SpatialBudgetExceeded:
            # Keep the fitter's physical context; only raw ownership is tiled.
            halo=max(p.fixture_min_length,4*p.fixture_grid_cell,
                     2*p.max_radius+2*p.fixture_surface_distance,
                     2.5*p.fixture_min_width+p.fixture_surface_distance)
            for _,core_lo,core_hi in runtime.store.cores():
                lower,upper=np.maximum(lo,core_lo),np.minimum(hi,core_hi)
                if np.any(lower>=upper):continue
                support_lo,support_hi=np.maximum(lo,lower-halo),np.minimum(hi,upper+halo)
                local_faces=[face for face in faces if
                             np.all(proposal_bounds[id(face)][0]<support_hi) and
                             np.all(proposal_bounds[id(face)][1]>support_lo)]
                if not local_faces:continue
                records=runtime.store.query(support_lo,support_hi)
                valid=~runtime.masks(records)
                valid&=~table_mask(records["xyz"],plane,p)
                support=records["xyz"][valid]
                owned=support[np.all((support>=lower)&(support<upper),axis=1)]
                for face in refine_fixture_faces(support,local_faces,p):
                    # Core bounds own counts, not a clip of observed grid cells.
                    face["coreBounds"]=[lower.tolist(),upper.tolist()]
                    evidence=owned[fixture_mask(owned,[face],p)]
                    if not len(evidence):continue
                    local=(evidence-np.asarray(face["origin"]))@np.asarray(face["axes"]).T
                    face["occupiedCells"]=np.unique(np.floor(local/face["gridSize"]).astype(np.int64),axis=0).tolist()
                    face["supportCount"]=len(evidence)
                    refined.append(face)
            continue
        valid=~runtime.masks(records)
        valid&=~table_mask(records["xyz"],plane,p)
        refined.extend(refine_fixture_faces(records["xyz"][valid],faces,p))
    return refined


def projection_entries(instances,p):
    entries=[]
    for item in instances:
        entries.append({"id":item["id"],"instance":item["id"],"directionId":item.get("directionId",0),
            "radius":item["radius"]+p.support_distance,"observedSegments":item["observedSegments"],"centerline":item["centerline"],
            "role":item.get("role", "unresolved")})
    return entries


def _canonical_face_normal(value):
    normal=np.asarray(value,dtype=float);length=np.linalg.norm(normal)
    if not np.isfinite(length) or length<=0:return np.zeros(3)
    normal=normal/length
    pivot=np.flatnonzero(np.abs(normal)>1e-12)
    return -normal if len(pivot) and normal[pivot[0]]<0 else normal


def classify(points,analysis,features=None,noise=None):
    """Arbitrate scene ownership, without any crossing point semantics."""
    p=Params.from_value(analysis.data["algorithmDetails"]["parameters"])
    details=analysis.data["algorithmDetails"]
    runtime=getattr(analysis,"resources",None)
    if runtime is not None and runtime.cached_projection is not None:index=runtime.cached_projection
    else:
        index=build_segment_index(details["projection"]["instances"])
        if runtime is not None:runtime.cached_projection=index
    projected=project_surface_top2(points,index,p.support_distance)
    table=table_mask(points,details.get("plane"),p)
    surfaces=fixture_candidates(points,details.get("fixture",{}).get("surfaces",[]),p)
    fixture=np.zeros(len(points),bool);fixture_conf=np.zeros(len(points))
    fixture_kind=np.zeros(len(points),np.uint8)
    if features is not None:
        planar=(features["surface_planarity"]>=p.min_planarity)&features["surface_valid"].astype(bool)
        normals=np.asarray(features.get("surface_normal",np.zeros((len(points),3))),dtype=float)
        normal_length=np.linalg.norm(normals,axis=1)
        normal_valid=np.asarray(features.get("surface_normal_valid",features["surface_valid"]),bool)&(normal_length>.9)
        normals=np.divide(normals,normal_length[:,None],out=np.zeros_like(normals),where=normal_length[:,None]>0)
    else:
        planar=np.zeros(len(points),bool);normal_valid=np.zeros(len(points),bool);normals=np.zeros((len(points),3))
    for face in surfaces:
        support=fixture_mask(points,[face],p)
        if not support.any():continue
        normal=_canonical_face_normal(face['normal'])
        residual=np.abs((points[support]-np.asarray(face['origin']))@np.asarray(face['normal']))
        evidence=.65+.33*face.get('confidence',.8)*np.exp(-.5*(residual/p.fixture_fit_distance)**2)
        rows=np.flatnonzero(support);aligned=np.abs(normals[rows]@normal)
        evidence*=np.where(planar[rows]&normal_valid[rows],.75+.25*aligned,1.)
        # Max over complete per-face evidence is independent of face order.
        kind={"square-tube-face":FIXTURE_SQUARE_TUBE,"plate":FIXTURE_PLATE}.get(face.get('kind'),0)
        chosen=(evidence>fixture_conf[rows]+1e-10)|((np.abs(evidence-fixture_conf[rows])<=1e-10)&(kind>fixture_kind[rows]))
        fixture_kind[rows[chosen]]=kind
        fixture_conf[rows]=np.maximum(fixture_conf[rows],evidence)
        fixture|=support
    for model in details.get('fixture',{}).get('bolts',[]):
        support=bolt_mask(points,[model],p)
        evidence=.75+.22*model.get('confidence',.7)
        chosen=support & (evidence>=fixture_conf-1e-10)
        fixture_kind[chosen]=FIXTURE_BOLT
        fixture_conf[support]=np.maximum(fixture_conf[support],evidence)
        fixture|=support
    steel=projected.best_id>0
    table_conf=np.where(table,.95,0.)
    if features is not None:
        linear=(features["axis_linearity"]>=p.min_linearity)&features["axis_valid"].astype(bool)
        table_conf*=np.where(planar,1.,.65)
        tangent=np.abs(np.einsum("ij,ij->i",features["axis_tangent"],projected.best_tangent))
        cylinder_fit=np.clip(1.-projected.best_distance,0.,1.)
        bar_conf=np.where(steel, (.65+.30*np.clip(features["axis_linearity"],0,1)*tangent)*(.70+.30*cylinder_fit),0.)
        radial_alignment=np.abs(np.einsum('ij,ij->i',normals,projected.best_normal))
        bar_conf*=np.where(planar&normal_valid,.75+.25*radial_alignment,1.)
        bar_conf[steel&~linear]*=.85
    else:bar_conf=np.where(steel,.8,0.)
    scene=np.zeros(len(points),np.uint8)
    scene[table]=TABLE;scene[fixture&(fixture_conf>table_conf)]=FIXTURE
    # A fitted observed axis with strong local tangent support may release an
    # erroneous planar mask; no class has unconditional priority.
    win=steel&(bar_conf>np.maximum(table_conf,fixture_conf)+.02)
    if not p.ownership_review_enabled:
        # Debug mode exposes the sequential masks without a later competing
        # cylinder undoing them. Raw-support validation remains enabled.
        scene[table]=TABLE
        scene[fixture&~table]=FIXTURE
        win=steel&~table&~fixture
    scene[win]=REBAR
    if noise is not None:scene[np.asarray(noise,bool)]=NOISE
    matched=scene==REBAR
    difference=np.full(len(points),np.inf)
    competing=(projected.best_id>0)&(projected.second_id>0)
    difference[competing]=projected.second_distance[competing]-projected.best_distance[competing]
    ambiguous=matched&(projected.second_id>0)&(difference<=p.ambiguity_margin)
    ids=np.where(matched&~ambiguous,projected.best_id,0).astype(np.uint32)
    directions=np.where(matched&~ambiguous,projected.best_direction,0).astype(np.uint16)
    flags=np.where(ambiguous,AMBIGUOUS,0).astype(np.uint8)
    confidence=np.maximum.reduce([table_conf,fixture_conf,bar_conf]).astype(np.float32)
    confidence[scene==UNKNOWN]=0;confidence[scene==NOISE]=1
    instance_conf=np.where(matched, np.clip(difference/p.ambiguity_margin,0,1),0).astype(np.float32)
    instance_conf[~np.isfinite(instance_conf)]=0
    fixture_kind[scene!=FIXTURE]=0
    role=np.zeros(len(points),np.uint8)
    entries=sorted(details['projection']['instances'], key=lambda item:item['id'])
    if entries:
        identifiers=np.array([item['id'] for item in entries],np.uint32)
        roles=np.array([{'planar':ROLE_PLANAR,'web':ROLE_WEB}.get(item.get('role'),0) for item in entries],np.uint8)
        selected=np.flatnonzero(ids>0)
        positions=np.searchsorted(identifiers,ids[selected])
        valid=positions<len(identifiers)
        selected=selected[valid];positions=positions[valid]
        valid=identifiers[positions]==ids[selected]
        role[selected[valid]]=roles[positions[valid]]
    attrs=RebarPointAttributes(matched.astype(np.uint8),directions,ids,scene,flags,confidence,instance_conf,fixture_kind,role)
    rows=np.flatnonzero(ambiguous)
    candidates={"point_indices":rows.astype(np.uint32),"offsets":np.arange(0,2*len(rows)+1,2,dtype=np.uint64),
                "instance_ids":np.column_stack((projected.best_id[rows],projected.second_id[rows])).ravel().astype(np.uint32)}
    return attrs,candidates


def raw_chunk(ordinal,analysis):
    runtime=analysis.resources
    if ordinal not in runtime.label_chunks:
        with np.load(runtime.feature_chunks[ordinal]) as raw:
            f=stage_features(ordinal,raw,analysis)
            attrs,candidates=classify(raw["xyz"],analysis,f,raw["noise"])
            path=runtime.path/f"raw-labels-{ordinal:06d}.npz"
            np.savez_compressed(path,source_index=raw["source_index"],scene_class=attrs.scene_class,
                rebar_class=attrs.rebar_class,rebar_instance=attrs.rebar_instance,rebar_direction=attrs.rebar_direction,
                rebar_flags=attrs.rebar_flags,class_confidence=attrs.class_confidence,instance_confidence=attrs.instance_confidence,
                fixture_kind=attrs.fixture_kind,rebar_role=attrs.rebar_role,
                **{"candidate_"+k:v for k,v in candidates.items()})
            runtime.label_chunks[ordinal]=path
    with np.load(runtime.label_chunks[ordinal]) as saved:
        return {k:saved[k] for k in saved.files}


def stage_features(ordinal,raw,analysis):
    """Persist sparse full-resolution boundary updates separately from base PCA."""
    runtime=analysis.resources;p=runtime.p;details=analysis.data["algorithmDetails"]
    features={k:raw[k].copy() for k in raw.files if k.startswith(("surface_","axis_"))}
    # Final instance pruning can require another ownership pass, but does not
    # change the table/fixture models. Reuse their already persisted PCA update.
    if ordinal in runtime.updated_features:
        path=runtime.update_chunks.get(ordinal)
        if path is not None:
            with np.load(path) as saved:
                for stage in ('table','fixture'):
                    index_key=stage+'_source_index'
                    if index_key not in saved:continue
                    rows=np.searchsorted(raw['source_index'],saved[index_key])
                    for key in features:
                        features[key][rows]=saved[stage+'_'+key]
        return features
    xyz=raw["xyz"];lo,hi=runtime.feature_bounds[ordinal]
    support=runtime.store.query(lo-p.halo-p.detection_voxel_size,hi+p.halo+p.detection_voxel_size)
    valid=~runtime.masks(support)
    coordinates=support["xyz"]
    table=table_mask(coordinates,details.get("plane"),p)
    fixture=fixture_mask(coordinates,details.get("fixture",{}).get("surfaces",[]),p)|bolt_mask(coordinates,details.get('fixture',{}).get('bolts',[]),p)
    core_table=table_mask(xyz,details.get("plane"),p)
    core_fixture=fixture_mask(xyz,details.get("fixture",{}).get("surfaces",[]),p)|bolt_mask(xyz,details.get('fixture',{}).get('bolts',[]),p)
    payload={}
    for name,excluded,newly in (("table",table,table),("fixture",table|fixture,fixture)):
        if not np.any(newly&valid):continue
        near=cKDTree(coordinates[newly&valid]).query(xyz,distance_upper_bound=p.halo)[0]<p.halo
        core_excluded=core_table if name=="table" else core_table|core_fixture
        rows=np.flatnonzero(near&~core_excluded&~raw["noise"].astype(bool))
        if not len(rows):continue
        updates=multiscale(coordinates[valid&~excluded],xyz[rows],p)
        payload[name+"_source_index"]=raw["source_index"][rows]
        for key,value in updates.items():
            features[key][rows]=value;payload[name+"_"+key]=value
    if payload:
        path=runtime.path/f"updates-{ordinal:06d}.npz"
        np.savez_compressed(path,**payload);runtime.update_chunks[ordinal]=path
    runtime.updated_features.add(ordinal)
    return features


def transfer_labels(points,analysis):
    """Map display samples from classified raw neighbours, preserving doubt.

    Full-resolution sidecars are authoritative. A PNTS sample has no source
    identity, so a mixed neighbourhood is never asserted to be an exact label.
    """
    runtime=analysis.resources;p=runtime.p
    arrays={"rebar_class":np.zeros(len(points),np.uint8),"rebar_direction":np.zeros(len(points),np.uint16),
        "rebar_instance":np.zeros(len(points),np.uint32),"scene_class":np.zeros(len(points),np.uint8),
        "rebar_flags":np.zeros(len(points),np.uint8),"class_confidence":np.zeros(len(points),np.float32),
        "instance_confidence":np.zeros(len(points),np.float32),
        "fixture_kind":np.zeros(len(points),np.uint8),"rebar_role":np.zeros(len(points),np.uint8)}
    candidate_rows=[];candidate_lists=[]
    keys=np.floor(points/p.block_size).astype(np.int64)
    unique,inverse=np.unique(keys,axis=0,return_inverse=True)
    for group,key in enumerate(unique):
        rows=np.flatnonzero(inverse==group);key=tuple(map(int,key))
        if runtime.transfer_cache is not None and runtime.transfer_cache[0]==key:
            _,tree,values,source_candidates=runtime.transfer_cache
        else:
            lo=np.asarray(key)*p.block_size-p.support_distance
            hi=(np.asarray(key)+1)*p.block_size+p.support_distance
            pieces=[];parts={k:[] for k in arrays};source_candidates={};offset=0
            for ordinal,(lower,upper) in enumerate(runtime.feature_bounds):
                if np.any(lower>=hi) or np.any(upper<=lo):continue
                with np.load(runtime.feature_chunks[ordinal]) as raw:
                    xyz=raw["xyz"]
                select=np.all((xyz>=lo)&(xyz<hi),axis=1);selected=np.flatnonzero(select)
                if not len(selected):continue
                labels=raw_chunk(ordinal,analysis)
                pieces.append(xyz[selected])
                for name in arrays:parts[name].append(labels[name][selected])
                cr=labels["candidate_point_indices"];co=labels["candidate_offsets"];ci=labels["candidate_instance_ids"]
                for j,local in enumerate(cr):
                    pos=np.searchsorted(selected,local)
                    if pos<len(selected) and selected[pos]==local:
                        source_candidates[offset+pos]=ci[int(co[j]):int(co[j+1])].tolist()
                offset+=len(selected)
                if offset>p.neighbourhood_point_limit:raise ValueError("display label transfer exceeds V5 neighbourhood budget")
            if not pieces:continue
            source=np.concatenate(pieces);tree=cKDTree(source)
            values={name:np.concatenate(parts[name]) for name in arrays}
            runtime.transfer_cache=(key,tree,values,source_candidates)
        d,nearest=tree.query(points[rows],k=min(4,tree.n),distance_upper_bound=p.support_distance)
        if d.ndim==1:d,nearest=d[:,None],nearest[:,None]
        valid=np.isfinite(d[:,0]);target=rows[valid];best=nearest[valid,0]
        for name in arrays:arrays[name][target]=values[name][best]
        valid_rows=np.flatnonzero(valid)
        # Exact source samples dominate raw-derived PNTS tiles. Copy those in
        # arrays; only uncertain or interpolated samples need CSR arbitration.
        review=valid_rows[(d[valid_rows,0]>1e-6)|((arrays['rebar_flags'][target]&AMBIGUOUS)!=0)]
        for q in review:
            row=rows[q]
            neighbours=nearest[q,np.isfinite(d[q])]
            # Exact source samples (including float32 tile roundoff) retain
            # their authoritative label. Nonexact mixed samples stay pending.
            mixed=d[q,0]>1e-6
            scenes=np.unique(values["scene_class"][neighbours]) if mixed else np.array([arrays["scene_class"][row]])
            ids=set(source_candidates.get(int(nearest[q,0]),[]))
            if mixed:
                ids.update(int(i) for i in values["rebar_instance"][neighbours] if i>0)
                for neighbour in neighbours:ids.update(source_candidates.get(int(neighbour),[]))
            if len(scenes)>1:
                for name in arrays:arrays[name][row]=0
                arrays['rebar_flags'][row]=AMBIGUOUS
            elif arrays["scene_class"][row]==REBAR and (len(ids)>=2 or arrays["rebar_flags"][row]&AMBIGUOUS):
                arrays["rebar_instance"][row]=0;arrays["rebar_direction"][row]=0;arrays["rebar_flags"][row]=AMBIGUOUS
                arrays["rebar_role"][row]=0
                if len(ids)>=2:candidate_rows.append(int(row));candidate_lists.append(sorted(ids))
            if mixed:
                for name in ("fixture_kind", "rebar_role"):
                    if len(np.unique(values[name][neighbours]))>1:arrays[name][row]=0
    if candidate_rows:
        order=np.argsort(candidate_rows);candidate_lists=[candidate_lists[i] for i in order];candidate_rows=np.asarray(candidate_rows)[order]
    offsets=np.r_[0,np.cumsum([len(ids) for ids in candidate_lists])].astype(np.uint64)
    candidates={"point_indices":np.asarray(candidate_rows,np.uint32),"offsets":offsets,
                "instance_ids":np.asarray([i for ids in candidate_lists for i in ids],np.uint32)}
    return RebarPointAttributes(**arrays),candidates


def finalize_raw_ownership(analysis):
    """Remove models with no actual semantic support; retain ambiguous evidence."""
    runtime=analysis.resources;p=runtime.p;items=analysis.data["instances"]
    def ownership_counts(count):
        confirmed=np.zeros(count,np.int64);candidate=np.zeros(count,np.int64)
        for ordinal in range(len(runtime.feature_chunks)):
            labels=raw_chunk(ordinal,analysis)
            confirmed+=np.bincount(labels["rebar_instance"],minlength=count)[:count]
            candidate+=np.bincount(labels["candidate_instance_ids"],minlength=count)[:count]
            if ordinal%32==0:
                logging.getLogger(__name__).info('V5 raw ownership: %d/%d spatial cores',ordinal+1,len(runtime.feature_chunks))
        return confirmed,candidate

    confirmed,candidate=ownership_counts(len(items)+1);counts=confirmed+candidate
    # A cylinder fitted mainly to a confirmed fixture corner can retain a few
    # boundary outliers. Those outliers are not sufficient semantic support
    # for the entire physical model, regardless of its absolute point count.
    retained=[item for item in items if counts[item["id"]]>=p.min_primitive_votes
              and counts[item['id']]/max(item.get('rawSupportCount',0),1)>=.20]
    removed=len(items)-len(retained)
    if not removed:
        for item in retained:
            ident=item["id"];item["confirmedSupportCount"]=int(confirmed[ident]);item["candidateSupportCount"]=int(candidate[ident]);item["semanticSupportCount"]=int(counts[ident])
        return {"removedInstanceCount":0,"rawPointCount":runtime.store.count}
    # finalize_instances uses geometry sort; establish the ID mapping via a
    # temporary internal identity removed before writing the JSON contract.
    for item in retained:item['_previousId']=item['id']
    finalized,connections=finalize_instances(retained,p)
    mapping=np.zeros(len(items)+1,np.uint32);direction=np.zeros_like(mapping,dtype=np.uint16)
    for item in finalized:
        old=item.pop('_previousId');mapping[old]=item['id'];direction[old]=item['directionId']
    analysis.data['instances']=finalized;analysis.data['connections']=connections
    analysis.data['algorithmDetails']['projection']['instances']=projection_entries(finalized,p)
    runtime.cached_projection=None
    for ordinal,path in list(runtime.label_chunks.items()):
        with np.load(path) as raw:labels={k:raw[k] for k in raw.files}
        lost=(labels['rebar_instance']>0)&(mapping[labels['rebar_instance']]==0)
        lost_candidates=np.any(mapping[labels['candidate_instance_ids']]==0)
        if lost.any() or lost_candidates:
            # A removed cylinder was the only source of these positive scene
            # labels. Re-arbitrate against remaining evidence, rather than
            # preserving that discarded model's self-induced rebar class.
            del runtime.label_chunks[ordinal]
            raw_chunk(ordinal,analysis)
            continue
        old=labels['rebar_instance'];labels['rebar_instance']=mapping[old];labels['rebar_direction']=direction[old]
        rows=labels['candidate_point_indices'];offsets=labels['candidate_offsets'];ids=labels['candidate_instance_ids']
        new_rows=[];new_offsets=[0];new_ids=[]
        for i,row in enumerate(rows):
            candidates=np.unique(mapping[ids[int(offsets[i]):int(offsets[i+1])]])
            candidates=candidates[candidates>0]
            if len(candidates)==1:
                labels['rebar_instance'][row]=candidates[0];labels['rebar_flags'][row]=0
                labels['rebar_direction'][row]=finalized[int(candidates[0])-1]['directionId']
            elif len(candidates)>1:
                new_rows.append(row);new_ids.extend(candidates);new_offsets.append(len(new_ids))
        labels['candidate_point_indices']=np.asarray(new_rows,np.uint32)
        labels['candidate_offsets']=np.asarray(new_offsets,np.uint64)
        labels['candidate_instance_ids']=np.asarray(new_ids,np.uint32)
        np.savez_compressed(path,**labels)
    confirmed,candidate=ownership_counts(len(finalized)+1)
    for item in finalized:
        ident=item["id"];item["confirmedSupportCount"]=int(confirmed[ident]);item["candidateSupportCount"]=int(candidate[ident]);item["semanticSupportCount"]=int(confirmed[ident]+candidate[ident])
    return {"removedInstanceCount":removed,"rawPointCount":runtime.store.count}


def analyze(context,p):
    runtime,points,features,stages=prepare(context,p)
    try:
        start=time.perf_counter();plane=detect_table(points,features,p)
        table=table_mask(points,plane,p)
        stages.append({"name":"table","inputPointCount":len(points),"confirmedPointCount":int(table.sum()),"confirmed":plane is not None,"elapsedS":time.perf_counter()-start})
        start=time.perf_counter()
        residual=points[~table];f={k:v[~table].copy() for k,v in features.items()}
        # Recompute the boundary with the masked surface excluded. Feature
        # updates are kept separate from the source-level base feature cache.
        if plane is not None and len(residual):
            near=np.abs((residual-np.asarray(plane["origin"]))@np.asarray(plane["normal"]))<p.axis_radius
            updated=multiscale(residual,residual[near],p)
            for key,value in updated.items():f[key][near]=value
        surfaces=detect_fixtures(residual,f,p,plane)
        surfaces=refine_fixture_support(runtime,plane,surfaces,p)
        bolts,bolt_diagnostic=detect_bolts(residual,f,surfaces,p)
        fixture=fixture_mask(residual,surfaces,p)|bolt_mask(residual,bolts,p)
        stages.append({"name":"fixtures","inputPointCount":len(residual),"confirmedPointCount":int(fixture.sum()),"candidateCount":len(surfaces),
                       "boltCount":len(bolts),"boltDiagnostics":bolt_diagnostic,"elapsedS":time.perf_counter()-start})
        active=residual[~fixture];af={k:v[~fixture].copy() for k,v in f.items()}
        if fixture.any() and len(active):
            near=cKDTree(residual[fixture]).query(active,distance_upper_bound=p.axis_radius)[0]<p.axis_radius
            updated=multiscale(active,active[near],p)
            for key,value in updated.items():af[key][near]=value
        start=time.perf_counter();planar,layers,diagnostic=planar_bars(active,af,p)
        stages.append({"name":"planar-bars","inputPointCount":len(active),"candidateCount":len(planar),"elapsedS":time.perf_counter()-start,**diagnostic})
        body=distance_to_paths(active,planar,p,protect=True)
        # Direction protects web contact observations from a horizontal tube.
        body&=np.abs(af["axis_tangent"][:,2])<=np.sin(np.deg2rad(p.planar_angle_degrees))
        start=time.perf_counter()
        previous_segments=[len(item['observedSegments']) for item in planar]
        planar,hook_diagnostic=recover_terminal_hooks(active,af,p,planar,layers)
        # Only newly observed terminal paths shield their surface from web
        # detection. A hook's vertical arm must survive the horizontal gate.
        hook_paths=[dict(item,observedSegments=item['observedSegments'][count:])
                    for item,count in zip(planar,previous_segments)
                    if len(item['observedSegments'])>count]
        body|=distance_to_paths(active,hook_paths,p)
        stages.append({"name":"terminal-hooks","inputPointCount":len(active),
                       **hook_diagnostic,"elapsedS":time.perf_counter()-start})
        start=time.perf_counter();web,wdiag=web_bars(active[~body],{k:v[~body] for k,v in af.items()},p,layers,planar)
        stages.append({"name":"web-bars","inputPointCount":int((~body).sum()),"candidateCount":len(web),"elapsedS":time.perf_counter()-start,**wdiag})
        instances,connections=finalize_instances(planar+web,p)
        data={"schema":"rebar-analysis-v2","instances":instances,"connections":connections,"layers":layers,"intersections":[],
              "algorithmDetails":{"parameters":asdict(p),"plane":plane,"fixture":{"surfaces":surfaces,"bolts":bolts},"projection":{"kind":"observed-polyline-tube-v3","instances":projection_entries(instances,p)}},
              "diagnostics":{"stages":stages,"sourcePointCount":runtime.store.count,"detectionPointCount":len(points)}}
        result=RebarAnalysis(data,resources=runtime)
        start=time.perf_counter()
        if p.ownership_review_enabled:
            # Two bounded review passes are available only where a previous mask
            # actually competes with observed axial evidence.
            released=0;review_records=[]
            early_fixture=fixture_mask(points,surfaces,p)|bolt_mask(points,bolts,p)
            excluded=table|early_fixture
            # Inspect contradictory axial evidence even if an early scene mask
            # hid a whole rod and no initial instance could compete for its points.
            probe=excluded&features["axis_valid"].astype(bool)&(features["axis_linearity"]>.65)
            probe&=features["axis_linearity"]>features["surface_planarity"]
            if np.count_nonzero(probe)>=p.min_primitive_votes:
                local=cKDTree(points[probe]).query(points,distance_upper_bound=p.halo)[0]<p.halo
                pf={k:v[local] for k,v in features.items()}
                additional,_,_=planar_bars(points[local],pf,p)
                additional_web,_=web_bars(points[local],pf,p,layers,additional)
                from .rods import deduplicate_instances
                instances,connections=finalize_instances(deduplicate_instances(instances+additional+additional_web,p),p)
                data["instances"]=instances;data["connections"]=connections
                data["algorithmDetails"]["projection"]["instances"]=projection_entries(instances,p);runtime.cached_projection=None
            for iteration in range(2):
                attrs,_=classify(points,result,features)
                release=excluded&(attrs.scene_class==REBAR)
                if not release.any():break
                released+=int(release.sum());excluded[release]=False
                near=cKDTree(points[release]).query(points,distance_upper_bound=p.halo)[0]<p.halo
                rows=(~excluded)&near
                local_features=multiscale(points[~excluded],points[rows],p)
                extra,_,_=planar_bars(points[rows],local_features,p)
                extra_web,_=web_bars(points[rows],local_features,p,layers,extra)
                from .rods import deduplicate_instances
                instances,connections=finalize_instances(deduplicate_instances(instances+extra+extra_web,p),p)
                data["instances"]=instances;data["connections"]=connections
                data["algorithmDetails"]["projection"]["instances"]=projection_entries(instances,p);runtime.cached_projection=None
                review_records.append({"pass":iteration+1,"releasedPointCount":int(release.sum()),"tableReleaseCount":int((release&table).sum()),
                                      "fixtureReleaseCount":int((release&early_fixture).sum()),"recheckPointCount":int(rows.sum()),"candidateCount":len(extra)+len(extra_web)})
            stages.append({"name":"ownership-review","enabled":True,"inputPointCount":len(points),"releasedPointCount":released,"maxPasses":2,"passes":review_records,
                           "contradictoryMaskPointCount":int(probe.sum()),"elapsedS":time.perf_counter()-start})
        else:
            stages.append({"name":"ownership-review","enabled":False,"maxPasses":0,"passes":[],
                           "releasedPointCount":0,"elapsedS":0.})
        start=time.perf_counter()
        instances,raw_diagnostic=verify_raw_instances(runtime,instances,p,preserve_association=True)
        instances=merge_fragments(instances,p)
        instances,connections=finalize_instances(instances,p)
        data["instances"]=instances;data["connections"]=connections
        data["algorithmDetails"]["projection"]["instances"]=projection_entries(instances,p)
        runtime.cached_projection=None
        stages.append({"name":"raw-support-verification",**raw_diagnostic,"elapsedS":time.perf_counter()-start})
        start=time.perf_counter()
        ownership_diagnostic=finalize_raw_ownership(result)
        stages.append({"name":"raw-ownership-finalization",**ownership_diagnostic,"elapsedS":time.perf_counter()-start})
        instances=data['instances']
        start=time.perf_counter()
        data["intersections"]=compute_intersections(instances,p.intersection_tolerance,p.intersection_min_angle_degrees)
        stages.append({"name":"intersections","instanceCount":len(instances),"intersectionCount":len(data["intersections"]),"elapsedS":time.perf_counter()-start})
        data['diagnostics']['temporaryByteSize']=sum(path.stat().st_size for path in runtime.path.rglob('*') if path.is_file())
        return result
    except Exception:
        runtime.close();raise


def export_sidecars(directory,analysis):
    """Persist all finite source rows, including excluded noise and uncertainty."""
    directory=Path(directory);runtime=analysis.resources;p=runtime.p
    feature_dir=directory/"features";label_dir=directory/"labels"
    feature_dir.mkdir();label_dir.mkdir()
    feature_chunks=[];label_chunks=[];updates=[];counts=np.zeros(5,np.int64);ambiguous=0;total=0
    fixture_counts=np.zeros(4,np.int64);role_counts=np.zeros(3,np.int64)
    feature_payload={};payload={}
    for ordinal,path in enumerate(runtime.feature_chunks):
        with np.load(path) as raw:
            features={k:raw[k] for k in raw.files if k.startswith(("surface_","axis_"))}
            xyz=raw["xyz"];ids=raw["source_index"]
            labels=raw_chunk(ordinal,analysis)
            attrs=RebarPointAttributes(**{name:labels[name] for name in RebarPointAttributes.__dataclass_fields__})
            candidates={key:labels["candidate_"+key] for key in ("point_indices","offsets","instance_ids")}
            attrs.validate(len(ids));name=f"{ordinal:06d}.npz"
            feature_payload={"source_index":ids,**features,"noise":raw["noise"],"noise_suspect":raw["noise_suspect"]}
            np.savez_compressed(feature_dir/name,**feature_payload)
            payload={"source_index":ids,"scene_class":attrs.scene_class,"rebar_class":attrs.rebar_class,
                     "rebar_instance":attrs.rebar_instance,"rebar_direction":attrs.rebar_direction,"rebar_flags":attrs.rebar_flags,
                     "class_confidence":attrs.class_confidence,"instance_confidence":attrs.instance_confidence,
                     "fixture_kind":attrs.fixture_kind,"rebar_role":attrs.rebar_role,
                     **{"candidate_"+key:value for key,value in candidates.items()}}
            np.savez_compressed(label_dir/name,**payload)
            runtime.label_chunks[ordinal]=label_dir/name
            if ordinal in runtime.update_chunks:
                update_name=f"updates-{ordinal:06d}.npz"
                shutil.copyfile(runtime.update_chunks[ordinal],feature_dir/update_name)
                updates.append({"path":update_name,"coreChunk":name,"sha256":hashlib.sha256((feature_dir/update_name).read_bytes()).hexdigest()})
            for folder,records in ((feature_dir,feature_chunks),(label_dir,label_chunks)):
                item={"path":name,"count":len(ids),"firstSourceIndex":int(ids.min()),"lastSourceIndex":int(ids.max()),"sha256":hashlib.sha256((folder/name).read_bytes()).hexdigest()}
                records.append(item)
            counts+=np.bincount(attrs.scene_class,minlength=5);total+=len(ids);ambiguous+=int(np.count_nonzero(attrs.rebar_flags&AMBIGUOUS))
            fixture_counts+=np.bincount(attrs.fixture_kind[attrs.scene_class==FIXTURE],minlength=4)
            role_counts+=np.bincount(attrs.rebar_role[attrs.scene_class==REBAR],minlength=3)
    if total!=runtime.store.count:
        raise ValueError('incomplete source feature/label output; refusing publication')
    feature_manifest={"schema":"rebar-features-v1","algorithmVersion":VERSION,"indexSpace":"source-reader-record","finitePointCount":total,
                      "sourceFingerprint":runtime.store.fingerprint,"parameters":asdict(p),"chunks":feature_chunks,
                      "attributes":{k:{"dtype":str(v.dtype),"shape":list(v.shape[1:])} for k,v in feature_payload.items()},"baseMask":"confirmed-noise-excluded",
                      "boundaryUpdates":{"encoding":"sparse-source-index","applyOrder":["table","fixture"],"attributes":"stage prefix followed by base attribute name","chunks":updates}}
    label_manifest={"schema":"rebar-raw-labels-v2","sourceFingerprint":runtime.store.fingerprint,"indexSpace":"source-reader-record","finitePointCount":total,"ambiguousPointCount":ambiguous,
                    "sceneClassCounts":dict(zip(("unknown","table","rebar","noise","fixture"),map(int,counts))),"chunks":label_chunks,
                    "fixtureKindCounts":dict(zip(("unknown","squareTube","plate","bolt"),map(int,fixture_counts))),
                    "rebarRoleCounts":dict(zip(("unresolved","planar","web"),map(int,role_counts))),
                    "attributes":{k:{"dtype":str(v.dtype),"shape":list(v.shape[1:])} for k,v in payload.items()},"candidates":{"encoding":"sparse-csr","point_indices":"local row in source_index"}}
    for folder,manifest in ((feature_dir,feature_manifest),(label_dir,label_manifest)):
        (folder/"manifest.json").write_text(json.dumps(manifest,indent=2))
    return {k:v for k,v in label_manifest.items() if k not in ("chunks","attributes","candidates")}
