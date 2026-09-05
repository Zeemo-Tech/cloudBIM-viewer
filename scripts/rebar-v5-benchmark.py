#!/usr/bin/env python3
"""Cold isolated artifact benchmark; never updates an asset's latest pointer."""
import argparse
import hashlib
import json
from pathlib import Path
import resource
import sys
import time
import traceback
import logging

REPO=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(REPO/'services/mesh-service'))


def digest(path):
    value=hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda:stream.read(1024*1024),b''):value.update(block)
    return value.hexdigest()


def main():
    logging.basicConfig(level=logging.INFO,format='%(asctime)s %(message)s')
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source',type=Path,required=True)
    parser.add_argument('--tiles',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--storage-root',type=Path,default=REPO/'backend/data')
    parser.add_argument('--chunk-size',type=int,default=250000)
    parser.add_argument('--parameters',type=Path)
    args=parser.parse_args()
    if args.chunk_size<=0:parser.error('chunk size must be positive')
    if args.output.exists():parser.error('output must be a new immutable version directory')
    from algorithms.rebar_v5.contracts import Params
    from dataclasses import asdict
    import rebar_stream
    from rebar_poc import compute_rebar_artifact
    parameters=asdict(Params.from_value(json.loads(args.parameters.read_text()) if args.parameters else {}))
    original=rebar_stream.iter_source_chunks
    rebar_stream.iter_source_chunks=lambda path,file_format=None:original(path,file_format,chunk_size=args.chunk_size)
    report={'schema':'rebar-v5-performance-v1','source':str(args.source.resolve()),'sourceSha256':digest(args.source),
            'parameters':parameters,'readerChunkSize':args.chunk_size,
            'codeSha256':{str(p.relative_to(REPO)):digest(p) for p in sorted((REPO/'services/mesh-service/algorithms/rebar_v5').glob('*.py'))},
            'targets':{'elapsedS':900,'peakRssBytes':2*1024**3},'humanTruth':False,
            'runMode':'fresh-process-without-algorithm-cache','osPageCache':'not-flushed'}
    report_path=args.output.with_suffix('.performance.json')
    report_path.parent.mkdir(parents=True,exist_ok=True)
    report_path.write_text(json.dumps(report,indent=2))
    started=time.perf_counter()
    try:
        manifest=compute_rebar_artifact(point_cloud_path=str(args.source.resolve()),point_cloud_format=args.source.suffix.lstrip('.'),
            source_tileset_path=str(args.tiles.resolve()),output_directory=str(args.output.resolve()),artifact_version=args.output.name,
            algorithm='geometric-v5',input_options={},parameters=parameters,storage_root=str(args.storage_root.resolve()))
        report.update(completed=True,summary=manifest['summary'],diskBytes={folder:sum(p.stat().st_size for p in (args.output/folder).rglob('*') if p.is_file()) for folder in ('features','labels','tiles')})
    except Exception as error:
        report.update(completed=False,error=str(error));traceback.print_exc()
    finally:
        report['elapsedS']=time.perf_counter()-started
        report['peakRssBytes']=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss*1024
        report['codeChangedDuringRun']=any(digest(REPO/path)!=expected for path,expected in report['codeSha256'].items())
        report['performancePassed']=report.get('completed',False) and not report['codeChangedDuringRun'] and report['elapsedS']<=900 and report['peakRssBytes']<=2*1024**3
        report_path.write_text(json.dumps(report,indent=2));print(json.dumps(report),flush=True)
    return 0 if report.get('completed') else 1


if __name__=='__main__':sys.exit(main())
