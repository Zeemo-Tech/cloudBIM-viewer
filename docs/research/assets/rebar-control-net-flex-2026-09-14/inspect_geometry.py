"""Read-only geometry-coverage diagnostic; run with .cloudbim/mesh-venv/bin/python.
No fitting, reclassification, or production writes. Local run data is required.
"""
import argparse
import json
from collections import Counter
from pathlib import Path
import numpy as np
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib.font_manager import FontProperties

ROOT = Path(__file__).resolve().parents[4]
parser = argparse.ArgumentParser()
parser.add_argument('--run', type=Path, default=ROOT / 'backend/data/assets/95b6b41c5857d9eb3407b155/pointcloud-steps/20260914T084630-1abadfc7')
parser.add_argument('--out', type=Path, default=Path(__file__).resolve().parent)
args = parser.parse_args()
args.out.mkdir(parents=True, exist_ok=True)
report = json.loads((args.run / 'control-net.json').read_text())
inv = report['inventory']
units = {u['designUnitId']: u for u in inv['units']}
instances = report['instances']
rows = []
for bar in inv['bars']:
    pts = np.asarray(bar['points'])
    full_length = float(np.linalg.norm(np.diff(pts, axis=0), axis=1).sum())
    unit_length = sum(units[uid]['lengthM'] for uid in bar['unitIds'])
    rows.append(dict(barId=bar['designBarId'], pointCount=len(pts), unitCount=len(bar['unitIds']),
                     excludedHookRunCount=bar.get('excludedHookRunCount', 0),
                     parentPolylineLengthM=full_length, matchingUnitLengthM=unit_length,
                     unrepresentedLengthM=full_length-unit_length,
                     instanceIds=[i['id'] for i in instances if i['designBarId'] == bar['designBarId']]))
summary = dict(runId=args.run.name, version=report['version'], physicalParents=len(rows),
               matchingUnits=len(units), kindCounts=dict(Counter(u['kind'] for u in units.values())),
               axisModels=dict(Counter(i.get('axisModel') for i in instances)),
               curvedParents=[r for r in rows if r['pointCount'] > 2],
               caveats=['Lengths use stored sampled parent polylines, not exact analytic arcs.',
                        'Parent length minus unit lengths is representation coverage, not scan error or missing-point count.',
                        'Design and fitted geometry differ in pose; plotted design is initialization, not measured truth.'])
arc_diagnostics = []
hook_bar = next(b for b in inv['bars'] if b.get('excludedHookRunCount'))
web_bar = next(b for b in inv['bars'] if len(b['unitIds']) == 36)
for name, bar, section in [('hook', hook_bar, slice(2, 7)), ('web', web_bar, slice(2, 5))]:
    p = np.asarray(bar['points'])[section]
    q = p-p.mean(axis=0)
    _, _, basis = np.linalg.svd(q)
    xy = q @ basis[:2].T
    solution = np.linalg.lstsq(np.column_stack((2*xy, np.ones(len(xy)))),
                               (xy*xy).sum(axis=1), rcond=None)[0]
    radius = float(np.sqrt(solution[2]+solution[:2] @ solution[:2]))
    chords = np.linalg.norm(np.diff(p, axis=0), axis=1)
    sag = radius-np.sqrt(np.maximum(0, radius*radius-chords*chords/4))
    arc_diagnostics.append(dict(kind=name, inferredRadiusM=radius,
                                maxChordSagM=float(sag.max()),
                                maxChordForPointOneMmSagM=float(2*np.sqrt(2*radius*.0001-.0001**2)),
                                method='Circle inferred from stored design samples; not IFC analytic metadata or scan fit.'))
summary['arcSamplingDiagnostics'] = arc_diagnostics
(args.out / 'geometry-coverage.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2))

points = np.load(args.run / 'positions.npy', mmap_mode='r')
owner = np.load(args.run / 'control_instance.npy', mmap_mode='r')
table = np.load(args.run / 'shared_table_mask.npy', mmap_mode='r')
font = FontProperties(fname='/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc')
plt.rcParams.update({'font.family': font.get_name(), 'axes.unicode_minus': False,
                     'figure.facecolor': '#ffffff', 'axes.facecolor': '#f7f9fc',
                     'axes.edgecolor': '#cbd5e1', 'text.color': '#172033'})
fig, axes = plt.subplots(1, 2, figsize=(12, 5.1), constrained_layout=True)
hook = next(b for b in inv['bars'] if b.get('excludedHookRunCount'))
web = next(b for b in inv['bars'] if len(b['unitIds']) == 36)
# Hook covers its tip and attachment. Web shows a middle valley and adjacent straight bodies.
hp = np.asarray(hook['points'])
wp = np.asarray(web['points'])
choices = [(hook, hp[0] + [-.02, 0, -.03], [.12, .022, .085], '长筋 #2：弯钩未进入直段模型'),
           (web, wp[88], [.14, .025, .085], '连续腹杆：中间接头缺少曲线连接')]
plot_rows = []
for ax, (bar, center, half, title) in zip(axes, choices):
    center, half = np.asarray(center), np.asarray(half)
    ids = []
    for begin in range(0, len(points), 500_000):
        batch = np.asarray(points[begin:begin+500_000])
        mask = (np.abs(batch-center) <= half).all(axis=1) & (table[begin:begin+len(batch)] == 0)
        ids.extend((begin+np.flatnonzero(mask)).tolist())
    ids = np.asarray(ids, dtype=np.int64)
    mine_ids = [i['id'] for i in instances if i['designBarId'] == bar['designBarId']]
    mine = np.isin(owner[ids], mine_ids)
    xy = (points[ids]-center)[:, [0, 2]]*1000
    ax.scatter(xy[~mine, 0], xy[~mine, 1], s=.7, c='#8993a4', alpha=.35, rasterized=True, label='其他非台面点')
    ax.scatter(xy[mine, 0], xy[mine, 1], s=1, c='#167ca6', alpha=.6, rasterized=True, label='当前归属本父筋的点')
    p = (np.asarray(bar['points'])-center)[:, [0, 2]]*1000
    ax.plot(*p.T, color='#9855bd', ls='--', lw=1.5, label='完整设计折线（初始化）')
    for index, instance in enumerate(i for i in instances if i['id'] in mine_ids):
        p = (np.asarray(instance['centerlineM'])-center)[:, [0, 2]]*1000
        ax.plot(*p.T, color='#da7d11', lw=2, label='当前拟合轴线' if index == 0 else None)
    ax.set_xlim(-half[0]*1000, half[0]*1000)
    ax.set_ylim(-half[2]*1000, half[2]*1000)
    ax.set_aspect('equal')
    ax.grid(alpha=.15)
    ax.set_title(title, fontsize=12, weight='bold')
    ax.set_xlabel('局部 X / mm')
    ax.set_ylabel('局部 Z / mm')
    ax.legend(fontsize=8, loc='lower left')
    plot_rows.append(dict(title=title, centerM=center.tolist(), halfExtentM=half.tolist(),
                          pointCount=len(ids), currentParentOwnedPointCount=int(mine.sum())))
fig.suptitle('真实数据的模型覆盖诊断 · v11\n紫色仅为设计先验；此图未运行新拟合，也不能作为精度真值', fontsize=14)
fig.savefig(args.out / 'geometry-gaps.png', dpi=180)
summary['plotWindows'] = plot_rows
(args.out / 'geometry-coverage.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2))
print(json.dumps({k:v for k,v in summary.items() if k != 'curvedParents'}, ensure_ascii=False, indent=2))
