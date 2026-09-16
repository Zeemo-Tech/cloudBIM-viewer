"""Offline source recompute using the workbench's own artifact writer.

Uses the project Python environment; starts no service and leaves running
workbenches alone. The resulting immutable run is served by the existing UI.
"""
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
import json, sys, time
ROOT = Path(__file__).resolve().parents[4]
spec = spec_from_file_location('tail_review_workbench', ROOT/'scripts/pointcloud-debug.py')
module = module_from_spec(spec); sys.modules[spec.name] = module; spec.loader.exec_module(module)
asset = ROOT/'backend/data/assets/95b6b41c5857d9eb3407b155'
state = module.DebugState(asset/'source.las', asset/'pointcloud-steps', ROOT/'.cloudbim/design-prior/config.json')
assert state.start(32, 16, 7, 'topology')
last = None
while True:
    status = state.snapshot()
    stage = status['progress']['stage']
    if stage != last:
        print(stage, flush=True); last = stage
    if status['status'] != 'running': break
    time.sleep(1)
if status['status'] != 'complete': raise RuntimeError(status['error'])
(Path(__file__).parent/'published-run.json').write_text(json.dumps(status['latest'], indent=2))
print(json.dumps(status['latest'], indent=2), flush=True)
