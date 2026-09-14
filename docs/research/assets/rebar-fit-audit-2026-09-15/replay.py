from audit import *
from algorithms.rebar_control_net import fit_control_net
def main():
 r=json.loads((RUN/'control-net.json').read_text());p=np.load(RUN/'positions.npy');n=np.load(RUN/'normals.npy');table=np.load(RUN/'shared_table_mask.npy')
 expected={key:np.load(RUN/(key+'.npy')) for key in ['control_status','control_instance']}
 results=[]
 for workers in [8,4]:
  t=time.time();report,attrs=fit_control_net(p,table,r['inventory'],normals=n,workers=workers,curve_workers=workers)
  value={'workers':workers,'elapsedS':time.time()-t,'counts':report['counts'],'differentPoints':{key:int(np.count_nonzero(attrs[key]!=value)) for key,value in expected.items()}}
  results.append(value);print(json.dumps(value),flush=True)
  (OUT/f'replay-{workers}.json').write_text(json.dumps(report))
  assert all(v==0 for v in value['differentPoints'].values()),value
 (OUT/'replay.json').write_text(json.dumps(results,indent=2))
if __name__=='__main__':main()
