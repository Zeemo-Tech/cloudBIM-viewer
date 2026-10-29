"""Explicit scene semantics, fixture-first ownership and short-bar evidence."""
from copy import deepcopy
from functools import partial
from concurrent.futures import ProcessPoolExecutor
from contextlib import nullcontext
import multiprocessing
import numpy as np
from algorithms.bim_scene_classification import classify_scene_surfaces, recover_scene_alternatives, VERSION as SCENE_VERSION
from algorithms.bim_short_connectors import recover_connectors, VERSION as SHORT_VERSION
from algorithms.bim_bent_bars import recover_bent_bars, VERSION as BENT_VERSION
from algorithms.bim_embedded_extent import extend_embedded_axes, VERSION as EXTENT_VERSION
from algorithms.rebar_control_net import fit_control_net, _fit_worker_init
from algorithms.rebar_control_curves import fit_curved_pieces
from algorithms.rebar_control_endpoints import complete_end_faces
from scipy.spatial import cKDTree
from pipeline_timing import timed_call

VERSION=SCENE_VERSION+'+'+SHORT_VERSION+'+'+BENT_VERSION+'+'+EXTENT_VERSION


def structural_inventory(inventory, scene):
    """Remove positively typed non-rebars; unresolved structural products remain."""
    result=deepcopy(inventory)
    nonsteel={o['globalId'] for o in scene.get('objects',[]) if o['category'] in ('fixture','embedded','pipe')}
    result['bars']=[b for b in result['bars'] if b.get('ifcGlobalId') not in nonsteel]
    bars={b['designBarId'] for b in result['bars']}
    result['units']=[u for u in result['units'] if u['designBarId'] in bars]
    units={u['designUnitId'] for u in result['units']}
    result['relations']=[r for r in result.get('relations',[]) if r.get('from') in units and r.get('to') in units]
    if 'coverage' in result:
        result['coverage'].update(physicalBarCount=len(bars),matchingUnitCount=len(units),
            unresolvedBars=sum(not b.get('unitIds') for b in result['bars']))
    return result


def fit_bim_guided_scene(points,normals,table_mask,inventory,scene,*,workers=1,progress=None,timings=None):
    run=partial(timed_call,{} if timings is None else timings)
    inventory=structural_inventory(inventory,scene)
    scene_report,labels,scene_owner=run('sceneSurfaces',classify_scene_surfaces,points,normals,table_mask,scene,inventory,progress=progress,allow_fallback=False,workers=workers)
    # One request owns one bounded process pool across all structural passes.
    # Every pass still confirms ownership before the next dependent pass starts.
    pool=(ProcessPoolExecutor(max_workers=min(8,int(workers)),
          mp_context=multiprocessing.get_context('spawn'),initializer=_fit_worker_init)
          if workers>1 else nullcontext(None))
    with pool as executor:
        report,arrays=run('controlNet',fit_control_net,points,table_mask,inventory,normals=normals,mode='aligned',
            excluded_mask=labels>0,workers=workers,curve_workers=workers,progress=progress,worker_pool=executor,
            complete_members=False)
        recovery=run('shortConnectors',recover_connectors,points,normals,inventory,report['instances'],arrays['control_status'],
            arrays['control_instance'],excluded=labels>0,progress=progress)
        owner=arrays['control_instance'];status=arrays['control_status']
        bent=run('bentBars',recover_bent_bars,points,normals,inventory,report['instances'],status,owner,scene,scene_report,
            excluded=(labels>0)|np.asarray(table_mask,bool),progress=progress,workers=workers,executor=executor)
        # All bodies reach their final validated pose before dependent geometry
        # can claim points. An earlier hook/cap must never survive a body change
        # merely because an additive second pass cannot revoke its ownership.
        source_ids=np.flatnonzero((labels==0)&~np.asarray(table_mask,bool)&np.isfinite(points).all(axis=1))
        tree=cKDTree(np.asarray(points)[source_ids]) if len(source_ids) else None
        pieces,summary=run('curvedPieces',fit_curved_pieces,points,normals,inventory,report['instances'],status,owner,
            tree=tree,source_ids=source_ids,workers=workers,progress=progress,executor=executor)
        report['curvedPieces']=pieces;report['curveSummary']=summary
        report['counts']['endFaceMatchedPoints']=run('endFaces',complete_end_faces,points,normals,
            report['instances'],pieces,owner,status,tree,source_ids)
    # Resolve alternate fixture faces only after structural body and terminal
    # evidence, so a nearby compatible fixture cannot hide that evidence.
    scene_report,labels,scene_owner=run('sceneAlternatives',recover_scene_alternatives,points,normals,table_mask,scene,inventory,
        scene_report,labels,scene_owner,excluded_mask=owner>0,workers=workers)
    extent=run('embeddedExtent',extend_embedded_axes,points,normals,scene,scene_report,labels,scene_owner,
        np.asarray(table_mask,bool)|(owner>0))
    status[labels>0]=4
    scene_counts=np.bincount(scene_owner,minlength=len(scene_report['objects'])+1)
    for row in scene_report['objects']:row['pointCount']=int(scene_counts[row['id']])
    scene_report['counts']={name:int(np.count_nonzero(labels==value)) for name,value in (('fixture',2),('embedded',5),('pipe',6))}
    scene_report['bentBarRecovery']=bent
    scene_report['embeddedAxialExtension']=extent
    count=np.bincount(owner,minlength=len(report['instances'])+1)
    for row in report['instances']:row['pointCount']=int(count[row['id']])
    for key,value in (('table',0),('matched',1),('pending',2),('removed',3),('excluded',4)):
        report['counts'][key]=int(np.count_nonzero(status==value))
    report['counts']['fittedUnits']=sum(r['pointCount']>0 for r in report['instances'])
    report['counts']['designBars']=len(inventory['bars'])
    pieces=report.get('curvedPieces',[])
    if 'curveSummary' in report:
        report['curveSummary'].update(fittedPieces=sum(p['status']=='fitted' for p in pieces),
            pendingPieces=sum(p['status']!='fitted' for p in pieces),
            inferredPieces=sum(bool(p.get('inferredCenterlineM')) for p in pieces),
            locallySupportedPieces=sum('localSupport' in p for p in pieces),
            matchedPoints=sum(p.get('pointCount',0)+p.get('localSupport',{}).get('pointCount',0) for p in pieces))
        report['curveSummary']['unresolvedPieces']=len(pieces)-report['curveSummary']['inferredPieces']-report['curveSummary']['fittedPieces']
        report['curveSummary']['locallyMatchedPoints']=sum(p.get('localSupport',{}).get('pointCount',0) for p in pieces)
    report['connectorRecovery']=recovery
    report['bentBarRecovery']=bent
    report['embeddedAxialExtension']=extent
    report['policy']['sceneExclusion']='only supported non-rebar surface rows; original post-table geometry scope retained'
    report['policy']['ownershipPolicy']='BIM-guided scene surfaces; provisional structural bodies; validated body revisions replace their source support; curves and cut faces attach once to final bodies; IFC local axial-end fitting'
    report['policy']['registrationFramework']='adjust IFC component rigid pose, sweep-axis and local axial-end degrees of freedom against original scan surfaces; unsupported model geometry remains inferred'
    scene_report['connectorRecovery']=recovery
    scene_report['structuralSummary']={'designPhysicalBarCount':len({b['ifcGlobalId'] for b in inventory['bars']}),
        'matchedPhysicalBarCount':len({r['designBarId'].split(':')[0] for r in report['instances'] if r['pointCount']>0}),
        'matchingUnitCount':len(inventory['units']),'matchedUnitCount':report['counts']['fittedUnits']}
    arrays['scene_class']=labels;arrays['scene_instance']=scene_owner
    return report,arrays,scene_report,inventory
