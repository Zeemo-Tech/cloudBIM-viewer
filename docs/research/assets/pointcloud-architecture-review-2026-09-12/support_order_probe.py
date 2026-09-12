"""Minimal reproduction: pending points protect satellites before disappearing."""
import sys, json
from pathlib import Path
from types import SimpleNamespace
import numpy as np

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT/'services/mesh-service'))
from algorithms.rebar_cluster_quality import final_fragment_filter

main = np.c_[np.linspace(0, 1, 501), np.zeros((501, 2))]
satellite = np.c_[np.linspace(.5, .502, 10), np.full(10, .05), np.zeros(10)]
pending = np.c_[np.linspace(.5, .502, 30), np.full(30, .051), np.zeros(30)]
context = SimpleNamespace(positions=np.vstack([main, satellite, pending]),
    fused_steel_score=np.r_[np.ones(501), np.full(10, .3), np.ones(30)])
records = []
for order in ['current_order', 'resolve_pending_before_satellite_review']:
    out = dict(complete_class=np.full(541, 3, np.uint8),
        complete_instance=np.r_[np.ones(511), np.zeros(30)].astype(np.uint32),
        complete_segment=np.r_[np.ones(511), np.zeros(30)].astype(np.uint32),
        complete_confidence=np.ones(541, np.float32))
    if order != 'current_order':
        out['complete_class'][511:] = 4
    report = final_fragment_filter(context, out)
    out['complete_class'][out['complete_instance'] == 0] = 4
    records.append(dict(order=order,
        remainingSatellitePoints=int(np.count_nonzero(out['complete_class'][501:511] == 3)), report=report))
print(json.dumps(records, ensure_ascii=False))
(Path(__file__).parent/'support-order.json').write_text(json.dumps(records, indent=2))
