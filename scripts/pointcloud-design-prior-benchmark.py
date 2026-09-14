#!/usr/bin/env python3
"""Alternate A/B/C in fresh processes; retain full artifacts and honest review gates.

Run with .cloudbim/mesh-venv/bin/python. The configured source/alignment is fixed;
review labels are never inferred from design counts or algorithm predictions.
"""
import argparse
import hashlib
import json
from pathlib import Path
import statistics
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'services/mesh-service'))
from rebar_design_prior import prepare_snapshot


def write(path, value):
    Path(path).write_text(json.dumps(value, indent=2, ensure_ascii=False, allow_nan=False))


def make_review_set(report, source_sha256):
    regions = []
    for unit in report['inventory']['units']:
        if unit['kind'] == 'short':
            lo = [min(a,b)-.35 for a,b in zip(unit['startM'], unit['endM'])]
            hi = [max(a,b)+.35 for a,b in zip(unit['startM'], unit['endM'])]
            regions.append({'id': 'short-'+unit['designUnitId'], 'kind': 'short', 'minM': lo, 'maxM': hi, 'reviewed': False})
    # Deterministic candidates for review, not truth. The entire component list
    # can be labelled; these windows provide convenient representative views.
    for kind, select in [
        ('web', lambda c: c.get('family') == 4),
        # Selection must not depend on B/C matching decisions: both modes are
        # assessed against precisely the same fixed review windows.
        ('crossing', lambda c: c['kind'] == 'internal' and c.get('family') not in (3,4)),
        ('fixture-hook', lambda c: c['kind'] == 'exterior' and c['pointCount'] > 1000),
        ('debris', lambda c: c['pointCount'] < 100),
    ]:
        selected = [c for c in report['components'] if select(c)]
        for c in selected[:2]:
            regions.append({'id': f'{kind}-{c["id"]}', 'kind': kind, 'componentId': c['id'],
                            'minM': [x-.12 for x in c['centerM']], 'maxM': [x+.12 for x in c['centerM']], 'reviewed': False})
    identities = [{'id': c['id'], 'pointCount': c['pointCount'], 'centerM': c['centerM']} for c in report['components']]
    fingerprint = hashlib.sha256(json.dumps(identities, sort_keys=True).encode()).hexdigest()
    return {'schema': 'design-prior-review-v1', 'sourceSha256': source_sha256,
            'componentFingerprint': fingerprint, 'snapshotFingerprint': report['snapshotFingerprint'],
            'labelPolicy': 'Manual observation only. label=steel/noise/unknown; truthInstanceId identifies an observed straight web segment, NOT its design parent.',
            'reviewedBy': None, 'regions': regions,
            'labels': [{'componentId': c['id'], 'label': 'unknown', 'category': 'unknown', 'truthInstanceId': None} for c in report['components']]}


def evaluate_review(report, annotations):
    expected = make_review_set(report, annotations['sourceSha256'])
    if expected['componentFingerprint'] != annotations['componentFingerprint'] or expected['snapshotFingerprint'] != annotations['snapshotFingerprint']:
        raise ValueError('复核标签与组件/模型快照不匹配')
    lookup = {c['id']: c for c in report['components']}
    scores = {}
    if len({r['componentId'] for r in annotations['labels']}) != len(annotations['labels']):
        raise ValueError('重复复核组件标签')
    if any(r['componentId'] not in lookup or r['label'] not in ('steel','noise','unknown') for r in annotations['labels']):
        raise ValueError('无效复核组件或标签')
    labelled = [r for r in annotations['labels'] if r['label'] in ('steel', 'noise')]
    if not labelled or not annotations.get('reviewedBy'):
        return {'status': 'unverified', 'reason': '尚无人工复核标签；数量和算法对应不能作为真值', 'scores': {}}
    for mode in ('baseline', 'result'):
        steel_total = steel_kept = noise_total = noise_kept = 0
        short_total = short_kept = 0
        truth_to_predictions, predictions_to_truth = {}, {}
        for row in labelled:
            c = lookup[row['componentId']]
            kept = c['baselineClass'] == 3 if mode == 'baseline' else c['status'] != 3
            instance = c['originalInstanceId'] if mode == 'baseline' else c['instanceId']
            count = c['pointCount']
            if row['label'] == 'steel':
                steel_total += count; steel_kept += count*kept
                if row.get('category') == 'short':
                    short_total += count; short_kept += count*kept
                if row.get('truthInstanceId') and kept:
                    # Unassigned components cannot be silently counted as one
                    # perfectly merged instance merely because their ID is 0.
                    key = str(instance) if instance else f'unassigned-{c["id"]}'
                    truth_to_predictions.setdefault(row['truthInstanceId'], set()).add(key)
                    predictions_to_truth.setdefault(key, set()).add(row['truthInstanceId'])
            else:
                noise_total += count; noise_kept += count*kept
        scores[mode] = {'steelRetention': steel_kept/steel_total if steel_total else None,
                        'shortSteelRetention': short_kept/short_total if short_total else None,
                        'noiseRetention': noise_kept/noise_total if noise_total else None,
                        'extraFragments': sum(max(0,len(v)-1) for v in truth_to_predictions.values()),
                        'falseMerges': sum(len(v)>1 for v in predictions_to_truth.values())}
    a,b=scores['baseline'],scores['result']
    reviewed_regions={r['id'] for r in annotations.get('regions',[]) if r.get('reviewed') is True}
    coverage=all(r['id'] in reviewed_regions for r in expected['regions'])
    instance_coverage=all(bool(r.get('truthInstanceId')) for r in labelled if r['label']=='steel')
    complete = coverage and instance_coverage and all(a[k] is not None and b[k] is not None for k in ('steelRetention','noiseRetention','shortSteelRetention'))
    safe = complete and b['steelRetention'] >= a['steelRetention'] and b['shortSteelRetention'] >= a['shortSteelRetention'] and b['falseMerges'] <= a['falseMerges']
    improves = complete and (b['noiseRetention'] < a['noiseRetention'] or b['extraFragments'] < a['extraFragments'])
    return {'status': 'passed' if safe and improves else 'failed' if complete else 'unverified',
            'labelledComponents': len(labelled), 'totalComponents': len(lookup), 'scores': scores,
            'allReviewRegionsCompleted': coverage,
            'allLabelledSteelHasInstanceTruth': instance_coverage,
            'pendingComponents': sum(c['status']==2 for c in report['components']),
            'pendingPointsByManualLabel': {label:sum(lookup[r['componentId']]['pointCount'] for r in annotations['labels']
                if r['label']==label and lookup[r['componentId']]['status']==2) for label in ('steel','noise','unknown')},
            'scope': 'Only manually labelled components; no whole-scan accuracy claim.'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--report', type=Path, required=True)
    parser.add_argument('--workers', type=int, default=16)
    parser.add_argument('--rounds', type=int, default=3)
    parser.add_argument('--budget-seconds',type=float,default=120.,help='full online process wall-time budget; model first extraction is separate')
    parser.add_argument('--annotations', type=Path)
    parser.add_argument('--evaluate-only', action='store_true', help='score existing artifacts without rerunning the point-cloud pipeline')
    args=parser.parse_args()
    if args.evaluate_only:
        if not args.annotations: parser.error('--evaluate-only requires --annotations')
        result=json.loads(args.report.read_text());annotations=json.loads(args.annotations.read_text())
        if annotations['sourceSha256'] != result['sourceSha256']: raise ValueError('复核源文件不匹配')
        for mode in ('geometry','topology'):
            run=next(r for r in reversed(result['runs']) if r['mode']==mode)
            report=json.loads((args.output/run['runId']/'design-prior.json').read_text())
            result['qualityGate'][mode]=evaluate_review(report,annotations)
        write(args.report,result);print(json.dumps(result['qualityGate'],ensure_ascii=False))
        return
    if args.rounds < 3: parser.error('验收基准至少交替运行三轮')
    if not 0 < args.budget_seconds < float('inf'): parser.error('budget must be finite and positive')
    args.output.mkdir(parents=True, exist_ok=True);args.report.parent.mkdir(parents=True, exist_ok=True)
    snapshot=prepare_snapshot(args.config)
    source=Path(snapshot['sourcePath'])
    runs=[]
    for repeat in range(args.rounds):
        for mode in ('off','geometry','topology'):
            log=args.report.parent/f'benchmark-{repeat+1}-{mode}.log'
            command=[sys.executable,str(ROOT/'scripts/pointcloud-debug.py'),'--compute-only','--source',str(source),
                     '--output',str(args.output.resolve()),'--workers',str(args.workers),'--through-step','7',
                     '--prior-mode',mode,'--prior-config',str(args.config.resolve())]
            start=time.perf_counter()
            with log.open('w') as stream:
                subprocess.run(command,stdout=stream,stderr=subprocess.STDOUT,check=True,timeout=600,cwd=ROOT)
            manifest=json.loads(log.read_text())
            if manifest['source']['sha256'] != snapshot['sourceSha256'] or (mode!='off' and manifest['designPrior']['snapshotFingerprint']!=snapshot['fingerprint']):
                raise ValueError('基准期间源文件、关联模型或配准发生变化；请按固定输入重新运行')
            record={'mode':mode,'repeat':repeat+1,'runId':manifest['runId'],
                    'wallS':manifest['timings']['totalS'],'processWallS':time.perf_counter()-start,
                    'cpuS':manifest['performance']['cpuS'],'peakRssMB':manifest['performance']['peakRssMB'],
                    'priorS':manifest['timings'].get('designPriorS',0),
                    'counts':manifest.get('designPrior',{}).get('counts',{})}
            runs.append(record)
            print(json.dumps(record,ensure_ascii=False),flush=True)
    medians={mode:{key:statistics.median(r[key] for r in runs if r['mode']==mode)
                   for key in ('wallS','processWallS','cpuS','peakRssMB','priorS')} for mode in ('off','geometry','topology')}
    comparison={mode:{'wallOverhead':medians[mode]['wallS']/medians['off']['wallS']-1,
                      'cpuOverhead':medians[mode]['cpuS']/medians['off']['cpuS']-1,
                      'memoryOverhead':medians[mode]['peakRssMB']/medians['off']['peakRssMB']-1,
                      'timeBudgetS':args.budget_seconds,
                      'timeGatePassed':medians[mode]['processWallS'] <= args.budget_seconds}
                for mode in ('geometry','topology')}
    final=json.loads((args.output/runs[-1]['runId']/'manifest.json').read_text())
    review=make_review_set(final['designPrior'],final['source']['sha256'])
    review_path=args.report.parent/'review-set.json'
    # Never overwrite completed human annotations on a repeated benchmark.
    if review_path.exists(): review_path=args.report.parent/f'review-set-{runs[-1]["runId"]}.json'
    write(review_path,review)
    annotations=json.loads(args.annotations.read_text()) if args.annotations else review
    if annotations['sourceSha256'] != final['source']['sha256']: raise ValueError('复核源文件不匹配')
    quality={}
    for mode in ('geometry','topology'):
        run=next(r for r in reversed(runs) if r['mode']==mode)
        report=json.loads((args.output/run['runId']/'design-prior.json').read_text())
        quality[mode]=evaluate_review(report,annotations)
    result={'schema':'design-prior-benchmark-v1','sourceSha256':final['source']['sha256'],'workers':args.workers,
            'rounds':args.rounds,'modelPreparation':snapshot['preparation'],'runs':runs,'medians':medians,
            'comparison':comparison,'reviewSet':str(review_path.resolve()),'qualityGate':quality,
            'productionPromoted':False,'measurement':'Fresh processes, fixed source and threads, full pipeline including exports. Model preparation is separate; OS page cache is not flushed.'}
    write(args.report,result)
    print(json.dumps({'medians':medians,'comparison':comparison,'qualityGate':quality},ensure_ascii=False),flush=True)


if __name__=='__main__':main()
