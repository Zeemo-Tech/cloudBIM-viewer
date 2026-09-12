#!/usr/bin/env python3
"""Compare retained point ownership by design identity, ignoring run-local IDs."""
import argparse
import json
from pathlib import Path
import numpy as np


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('reference', type=Path)
    parser.add_argument('regressed', type=Path)
    parser.add_argument('fixed', type=Path)
    parser.add_argument('output', type=Path)
    args = parser.parse_args()
    if args.output.exists():
        raise FileExistsError(args.output)
    roots = [args.reference, args.regressed, args.fixed]
    manifests = [json.loads((root/'manifest.json').read_text()) for root in roots]
    units = sorted({r['designUnitId'] for m in manifests for r in m['completeRebar']['instances'] if r.get('designUnitId')})
    unit_codes = {uid: i+1 for i, uid in enumerate(units)}
    canonical = []
    for root, manifest in zip(roots, manifests):
        owner = np.load(root/'complete_instance.npy', mmap_mode='r')
        lookup = np.zeros(int(owner.max())+1, np.int32)
        for record in manifest['completeRebar']['instances']:
            lookup[record['id']] = unit_codes.get(record.get('designUnitId'), 0)
        canonical.append(lookup[owner])
    reference, regressed, fixed = canonical
    changed = (reference > 0) & (regressed > 0) & (reference != regressed)
    remaining = changed & (fixed != reference)
    new = (reference > 0) & (fixed > 0) & (reference != fixed) & ~changed
    baseline_owner = np.load(args.regressed/'complete_instance.npy', mmap_mode='r')
    fixed_owner = np.load(args.fixed/'complete_instance.npy', mmap_mode='r')
    transfer_rows = np.flatnonzero(baseline_owner != fixed_owner)
    pairs, counts = np.unique(np.column_stack((baseline_owner[transfer_rows], fixed_owner[transfer_rows])), axis=0, return_counts=True)
    report = dict(runIds=[m['runId'] for m in manifests], sourcePoints=len(fixed),
        regressedDesignOwnerPoints=int(changed.sum()), restoredDesignOwnerPoints=int(np.count_nonzero(changed & ~remaining)),
        unresolvedRegressionPoints=int(remaining.sum()), newDesignOwnerChangesVsReference=int(new.sum()),
        ownerChangesVsRegressed=[dict(previous=int(a), final=int(b), pointCount=int(n)) for (a,b),n in zip(pairs,counts)],
        unchangedVsRegressed={name:bool(np.array_equal(np.load(args.regressed/(name+'.npy'),mmap_mode='r'),np.load(args.fixed/(name+'.npy'),mmap_mode='r')))
            for name in ('complete_class','terminal_removed','terminal_reason','terminal_fragment','terminal_origin')},
        ownershipEvidence=manifests[2]['designEvidence']['assignment']['ownershipEvidence'])
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2)+'\n')
    print(json.dumps({k:v for k,v in report.items() if k not in ('ownershipEvidence','ownerChangesVsRegressed')}, ensure_ascii=False, indent=2))
    assert not remaining.any(), 'Previously regressed source points still have different owners'
    # The historical output is a regression reference, not annotated ground
    # truth. Additional ownership changes remain explicit for geometric review.


if __name__ == '__main__':
    main()
