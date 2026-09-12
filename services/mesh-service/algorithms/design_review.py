"""Workbench adapter: measured hypotheses before denoising, exclusive final ownership."""
from dataclasses import asdict
import numpy as np
from .design_evidence_contract import VERSION, RobustnessPolicy, State, Reason
from .design_acceptance import evaluate_acceptance


def _surface_cost(points, model):
    d=points-model['center']; t=d@model['axis']
    return np.abs(np.linalg.norm(d-t[:,None]*model['axis'],axis=1)-model['radius'])+2*np.maximum(model['low']-t,np.maximum(t-model['high'],0))


def _ownership_evidence(context, rows, target, candidate, segments, policy):
    """A failed whole rod does not invalidate its locally supported points.

    Compare against the frozen pre-review segment, never a provisional candidate
    or a cylinder fitted to the entire (possibly bent) owner. Missing incumbent
    geometry is uncertainty, not counterevidence authorizing a transfer.
    """
    cost = _surface_cost(context.positions[rows], candidate.model)
    foreign = (context.complete_instance[rows] > 0) & (context.complete_instance[rows] != target)
    allowed = ~foreign
    counts = dict(comparedPointCount=int(foreign.sum()), supportedIncumbentPointCount=0,
                  missingIncumbentPointCount=0, improvedTransferPointCount=0,
                  supportedSegmentPointCount=0)
    foreign_rows = np.flatnonzero(foreign)
    segment_ids = context.complete_segment[rows[foreign_rows]]
    for sid in np.unique(segment_ids):
        local = foreign_rows[segment_ids == sid]
        segment = segments.get(int(sid))
        if segment is None:
            counts['missingIncumbentPointCount'] += len(local)
            continue
        start = np.asarray(segment['startM'], dtype=float)
        end = np.asarray(segment['endM'], dtype=float)
        length = np.linalg.norm(end-start)
        radius = float(segment['radiusM'])
        if not np.isfinite([*start, *end, radius, length]).all() or length <= 0 or radius <= 0:
            counts['missingIncumbentPointCount'] += len(local)
            continue
        valid = context.complete_instance[rows[local]] == segment['instanceId']
        counts['missingIncumbentPointCount'] += int((~valid).sum())
        local = local[valid]
        # Independent Step06 fits describe a coherent measured fragment. A few
        # surface outliers near a crossing must not split that fragment into
        # multiple owners. Provisional design fits cannot protect themselves.
        fit_error = segment.get('fitMedianErrorM')
        if (segment.get('candidateSource') != 'design-fixed-radius' and fit_error is not None
                and np.isfinite(fit_error) and 0 <= fit_error <= policy.max_surface_error):
            counts['supportedSegmentPointCount'] += len(local)
            counts['supportedIncumbentPointCount'] += len(local)
            continue
        model = dict(center=(start+end)/2, axis=(end-start)/length, radius=radius,
                     low=-length/2-policy.max_surface_error, high=length/2+policy.max_surface_error)
        incumbent = _surface_cost(context.positions[rows[local]], model)
        supported = incumbent <= policy.max_surface_error
        counts['supportedIncumbentPointCount'] += int(supported.sum())
        transfer = (~supported) & (cost[local] <= policy.max_surface_error) & (
            cost[local] + policy.ownership_improvement_m < incumbent)
        # Only independent confirmed evidence can displace an existing owner.
        transfer &= candidate.evidence.anchor_eligible
        allowed[local[transfer]] = True
        counts['improvedTransferPointCount'] += int(transfer.sum())
    counts['preservedPointCount'] = int(np.count_nonzero(foreign & ~allowed))
    return allowed, cost, counts


def prepare_review(context, inventory, *, output, workers=1, progress=None, policy=None):
    from .design_candidates import generate_candidates
    from .design_evidence import evaluate_evidence
    policy=policy or RobustnessPolicy()
    candidates, report=(generate_candidates(context,inventory,policy=policy,workers=workers,progress=progress) if policy.enable_candidates else ([],{'disabledByAblation':True}))
    for values in output.values():values[:]=0
    hard=np.asarray(context.shared_table_mask,bool)|np.asarray(context.shared_floating_noise,bool)
    output['review_state'][hard]=State.EXCLUDED_BOUNDARY;output['review_reason'][hard]=Reason.HARD_BOUNDARY
    best=np.full(len(context.positions),np.inf,np.float32)
    chosen=np.full(len(context.positions),-1,np.int32)
    accepted=[];records=[]
    for candidate in candidates:
        candidate.evidence=evaluate_evidence(candidate.metrics,policy=policy)
        records.append(dict(designUnitId=candidate.unit_id,attempt=candidate.attempt,metrics=candidate.metrics,
                            provenance=candidate.provenance,evidence=candidate.evidence.report()))
        rows=np.asarray(candidate.rows,dtype=np.int64);rows=rows[~hard[rows]];candidate.rows=rows
        if not candidate.evidence.accepted:
            unknown=rows[output['review_state'][rows]==0]
            output['review_state'][unknown]=candidate.evidence.state;output['review_reason'][unknown]=candidate.evidence.reason
            continue
        idx=len(accepted);accepted.append(candidate)
        cost=_surface_cost(context.positions[rows],candidate.model)
        take=cost<best[rows];chosen[rows[take]]=idx;best[rows[take]]=cost[take]
    for i,candidate in enumerate(accepted):
        rows=candidate.rows[chosen[candidate.rows]==i]
        # A provisional hypothesis cannot alter classification/protect itself.
        if candidate.evidence.anchor_eligible and policy.enable_reclassification:
            output['review_changed'][rows]=(context.refined_class[rows]!=3)
            context.refined_class[rows]=3
        output['review_state'][rows]=candidate.evidence.state;output['review_reason'][rows]=candidate.evidence.reason
    for name,values in output.items():setattr(context,name,values)
    context.design_candidates=accepted
    context.design_review_report=dict(version=VERSION,policy=asdict(policy),generation=report,candidates=records,
        candidateCount=len(candidates),supportedCandidateCount=len(accepted),reclassifiedPointCount=int(output['review_changed'].sum()),
        scoreMeaning='measured support ranking, not probability',fusionImmutable=True,
        stateCodes={str(int(value)):value.name.lower() for value in State},reasonCodes={str(int(value)):value.name.lower() for value in Reason})
    return context.design_review_report


def seed_internal(context, models, output):
    """Only independently confirmed surface fits enter Step05 support mechanisms."""
    candidates=getattr(context,'design_candidates',None) or []
    next_owner=max((m['instanceId'] for m in models),default=0)+1
    for c in candidates:
        if not c.evidence.anchor_eligible:continue
        rows=c.rows[(context.refined_class[c.rows]==3)&(output['internal_instance'][c.rows]==0)]
        if len(rows)<12:continue
        # Compatible remaining observations join an existing measured rod. A
        # tiny residual cannot inherit full-candidate support to invent a rod.
        occupied=output['internal_instance'][c.rows];positive=occupied[occupied>0]
        owner=None
        if len(positive):
            ids,counts=np.unique(positive,return_counts=True);target=int(ids[np.argmax(counts)])
            peers=[m for m in models if m['instanceId']==target and abs(m['axis']@c.model['axis'])>.98 and abs(m['radius']-c.model['radius'])<.001]
            if peers and counts.max()>=.5*len(c.rows):owner=target
            elif len(rows)<.4*len(c.rows):continue
        if owner is None:owner=next_owner;next_owner+=1
        m=dict(c.model);m.update(instanceId=owner,group=owner,
            fitMedianErrorM=c.metrics['cylinder_error_m'],trackInfo=dict(lengthM=float(m['high']-m['low']),diameterM=2*m['radius'],family=0,lengthAnomaly=False))
        models.append(m);sid=len(models)
        context.review_changed[rows]=1
        output['internal_instance'][rows]=owner;output['internal_segment'][rows]=sid
        output['internal_type'][rows]=m['type'];output['internal_confidence'][rows]=min(.85,c.evidence.score)


def finalize_review(context, report, inventory, *, acceptance_policy=None):
    """Reconcile competing observed candidates with existing Hungarian matching.

    Candidate coordinates never become points. Retained source points only; hooks
    remain in their existing owners. Failed/extra existing owners stay observable.
    """
    from .design_guided_instances import _assign_units, _candidate_sets, GuidedParameters
    from .design_evidence import retry_decision
    candidates=getattr(context,'design_candidates',None) or []
    policy=RobustnessPolicy(**context.design_review_report['policy'])
    units=inventory['units']
    by_id={u['designUnitId']:u for u in units}
    owners=context.complete_instance; classes=context.complete_class
    before=evaluate_acceptance(context.positions,owners,classes,report['instances'],inventory,policy=acceptance_policy)
    good={r['instanceId'] for r in before['instances'] if r['passed']}
    before_rows={r['instanceId']:r for r in before['instances']}
    occupied=np.flatnonzero(owners>0);ordered=occupied[np.argsort(owners[occupied],kind='stable')]
    unique,starts=np.unique(owners[ordered],return_index=True)
    owner_rows={int(oid):ordered[start:starts[i+1] if i+1<len(starts) else len(ordered)] for i,(oid,start) in enumerate(zip(unique,starts))}
    live=[]
    # Collapse repeated hypotheses for the same observed surface, not merely same design unit.
    for c in sorted(candidates,key=lambda c:(-c.evidence.score,c.unit_id,c.attempt)):
        rows=c.rows[(classes[c.rows]==3)&(context.internal_type[c.rows]!=5)]
        if len(rows)<12:continue
        if any(len(np.intersect1d(rows,x.rows,assume_unique=False))>.6*min(len(rows),len(x.rows)) for x in live):continue
        c.rows=rows;live.append(c)
    atoms=[];summaries=[]
    for i,c in enumerate(live):
        m=c.model
        summary=dict(center=m['center'],axis=m['axis'],radius=m['radius'],length=c.metrics['span_m'],strong=c.evidence.anchor_eligible,webRole='straight' if m['type']==3 else None)
        summaries.append(summary);atoms.append(dict(instanceId=i+1,summary=summary))
    ranked,_=_candidate_sets(summaries,inventory,'geometry')
    good_units={r['designUnitId'] for r in before['instances'] if r['passed']}
    for i,c in enumerate(live):
        if by_id[c.unit_id]['kind']=='short':
            # Same-layer translation has no XY position prior, including matching.
            ranked[i]=[]
            for j,u in enumerate(units):
                if u['kind']!='short':continue
                center=(np.array(u['startM'])+u['endM'])/2
                direction=np.array(u['endM'])-u['startM'];direction/=np.linalg.norm(direction)
                angle=abs(float(c.model['axis']@direction))
                if abs(center[2]-c.model['center'][2])>policy.short_layer_tolerance or angle<np.cos(np.deg2rad(policy.max_angle_degrees)):continue
                cost=abs(c.metrics['span_m']-u['lengthM'])/max(.020,.05*u['lengthM'])+abs(2*c.model['radius']-u['diameterM'])/.0015+3*(1-angle)
                ranked[i].append((float(cost),j))
            ranked[i].sort()
    ranked=[[(cost,u) for cost,u in row if units[u]['designUnitId'] not in good_units] for row in ranked]
    choices,extras=_assign_units({i:[i] for i in range(len(atoms))},atoms,ranked,units,GuidedParameters())
    retries=[]
    anchors=[c for c in live if c.evidence.anchor_eligible]
    if anchors and summaries and policy.enable_topology_retry and policy.max_retries>=2:
        topology_ranked,_=_candidate_sets(summaries,inventory,'topology')
        for i,c in enumerate(live):
            if by_id[c.unit_id]['kind']=='short':continue
            instruction=retry_decision(dict(unit_id=c.unit_id,rows=c.rows,attempt=1),[],anchors=anchors,policy=policy)
            if instruction['retry'] and instruction['action']=='compare_neighbors':
                revised=[(cost,u) for cost,u in topology_ranked[i] if units[u]['designUnitId'] not in good_units]
                if revised!=ranked[i]:
                    ranked[i]=revised;retries.append(dict(candidate=i,attempt=2,**instruction))
        if retries:choices,extras=_assign_units({i:[i] for i in range(len(atoms))},atoms,ranked,units,GuidedParameters())
    unit_owner={r.get('designUnitId'):r['id'] for r in report['instances'] if r.get('designUnitId')}
    next_owner=int(owners.max())+1;next_segment=int(context.complete_segment.max())+1
    best=np.full(len(owners),np.inf,np.float32);winning=np.full(len(owners),-1,np.int32)
    chosen=[];operations=[];ownership_comparisons=[]
    incumbent_segments={s['id']:s for s in report['segments']}
    for i,c in enumerate(live):
        u=choices.get(i)
        if u is None:continue
        uid=units[u]['designUnitId'];existing=unit_owner.get(uid)
        if existing in good:continue
        rows=c.rows[~np.isin(owners[c.rows],list(good))]
        # Existing curved hook owners are not retargeted by straight hypotheses.
        hook_clusters={r['id'] for r in report.get('clusters',[]) if r.get('category')=='curved-exterior'}
        rows=rows[~np.isin(context.complete_cluster[rows],list(hook_clusters))]
        allowed,cost,comparison=_ownership_evidence(context,rows,existing,c,incumbent_segments,policy)
        ownership_comparisons.append(dict(designUnitId=uid,**comparison))
        rows=rows[allowed];cost=cost[allowed]
        if len(rows)<12:continue
        if existing is not None:
            proposed=np.union1d(rows,owner_rows.get(existing,[])).astype(np.int64)
            local=evaluate_acceptance(context.positions[proposed],np.ones(len(proposed),int),np.full(len(proposed),3),[dict(id=1,designUnitId=uid)],dict(units=[units[u]],relations=[]),policy=acceptance_policy)
            previous=before_rows[existing];after=local['instances'][0]
            if set(after['reasons'])-set(previous['reasons']):continue
        take=cost<best[rows];best[rows[take]]=cost[take];winning[rows[take]]=len(chosen)
        chosen.append((c,u,existing))
    for index,(c,u,existing) in enumerate(chosen):
        rows=c.rows[winning[c.rows]==index]
        if len(rows)<12:continue
        owner=existing or next_owner
        if existing is None:next_owner+=1
        changed=owners[rows]!=owner;context.review_changed[rows[changed]]=1
        owners[rows]=owner;context.complete_segment[rows]=next_segment
        context.complete_confidence[rows]=c.evidence.score
        m=c.model;start=m['center']+m['axis']*m['low'];end=m['center']+m['axis']*m['high']
        report['segments'].append(dict(id=next_segment,instanceId=owner,type=m['type'],startM=start.tolist(),endM=end.tolist(),radiusM=m['radius'],pointCount=len(rows),candidateSource='design-fixed-radius'))
        if existing is None:report['instances'].append(dict(id=owner,designUnitId=units[u]['designUnitId'],designBarId=units[u]['designBarId'],type=m['type'],diameterM=2*m['radius'],lengthM=float(m['high']-m['low']),modelKind='observed-design-candidate',reviewStatus='matched'))
        operations.append(dict(designUnitId=units[u]['designUnitId'],instanceId=owner,pointCount=len(rows),changedOwnerPointCount=int(changed.sum()),action='observed_candidate_assignment'))
        next_segment+=1
    counts=np.bincount(context.complete_segment,minlength=next_segment)
    report['segments']=[{**s,'pointCount':int(counts[s['id']])} for s in report['segments'] if counts[s['id']]]
    final=[]
    owner_counts=np.bincount(owners)
    for entry in report['instances']:
        oid=entry['id']
        if oid>=len(owner_counts) or owner_counts[oid]==0:continue
        final.append({**entry,'pointCount':int(owner_counts[oid]),'segmentIds':[s['id'] for s in report['segments'] if s['instanceId']==oid]})
    report['instances']=final;report['instanceCount']=len(final)
    report['unassignedRebarPointCount']=int(np.count_nonzero((classes==3)&(owners==0)))
    context.design_review_report['assignment']=dict(operations=operations,unselectedCandidateCount=len(extras),topologyRetries=retries,stopReason='no_new_independent_support',alternatives=[dict(candidate=i,choices=[dict(cost=float(cost),designUnitId=units[u]['designUnitId']) for cost,u in row],margin=float(row[1][0]-row[0][0]) if len(row)>1 else None) for i,row in enumerate(ranked)])
    context.design_review_report['assignment']['ownershipEvidence']=dict(version='local-incumbent-v1',comparisons=ownership_comparisons)
    acceptance=evaluate_acceptance(context.positions,owners,classes,final,inventory,policy=acceptance_policy)
    context.design_review_report['acceptance']=acceptance
    # Independent final denoising remains negative evidence, not overwritten by proposal support.
    removed=(context.internal_type==5)|(classes==4)
    reviewed=removed&(context.review_state>0)&(context.review_state!=State.EXCLUDED_BOUNDARY)
    context.review_state[reviewed]=State.REJECTED_OBSERVED;context.review_reason[reviewed]=Reason.DENOISING_COUNTEREVIDENCE
    return acceptance
