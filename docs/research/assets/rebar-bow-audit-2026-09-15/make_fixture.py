exec((__import__('pathlib').Path(__file__).resolve().parent/'probe.py').read_text().split('results=[];t=time.time()')[0])
unit=units[10];ii=net._candidate_indices(tree,ids,unit['start'],unit['end'],.10,neighbours=768);captured={}
def capture(points,start,tangent,length,radius,**kwargs):
 captured.update(points=points,start=start,tangent=tangent,length=length,radius=radius,initial=kwargs['initial_centerline']);return realaxis(points,start,tangent,length,radius,**kwargs)
net.fit_prior_axis=capture;model,_,_=net._fit_unit(p,n,ii,unit,unit['start'],unit['direction'])
raw=np.load(OUT/'raw-11.npz');rp=raw['points'];src=raw['source'];held=(src%5==0)&~np.isin(src,ii)&(rp[:,0]>4.3)&(rp[:,0]<4.85)
body_held=(src%5==0)&(cKDTree(captured['points']).query(rp)[0]>1e-10)
np.savez_compressed(OUT/'fixture-11.npz',**captured,validation=rp[held],validationBody=rp[body_held],
    validationNormals=raw['normals'][body_held],baselineCurve=np.array(r['instances'][10]['centerlineM']))
print('validation points',held.sum(),'matches saved',np.allclose(model['curve'],r['instances'][10]['centerlineM'],rtol=0,atol=1e-9))
