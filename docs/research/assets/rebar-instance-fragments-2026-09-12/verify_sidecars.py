from pathlib import Path
import sys,json,numpy as np
from scipy.spatial import cKDTree
sys.path.insert(0,'services/mesh-service')
from pointcloud_tile_sidecar import pnts_positions,referenced_pnts,RECORD,HEADER
asset=Path('backend/data/assets/95b6b41c5857d9eb3407b155');r=asset/'pointcloud-steps/20260912T092723-e388d613';p=np.load(r/'positions.npy',mmap_mode='r');tree=cKDTree(p);arrays={k:np.load(r/(k+'.npy'),mmap_mode='r') for k in RECORD.names};mismatch={k:0 for k in arrays};count=0
files=referenced_pnts(asset/'tiles')
for file,rel in files:
 q=pnts_positions(file);_,idx=tree.query(q,k=1,workers=4);a=np.fromfile(r/'tile-attributes/complete-rebar'/(str(rel)+'.bin'),dtype=RECORD,offset=HEADER.size)
 for k in arrays:mismatch[k]+=int(np.count_nonzero(a[k]!=arrays[k][idx]))
 count+=len(q)
result={'tiles':len(files),'points':count,'mismatches':mismatch};print(json.dumps(result));Path(__file__).with_name('sidecars.json').write_text(json.dumps(result,indent=2));assert not any(mismatch.values())
