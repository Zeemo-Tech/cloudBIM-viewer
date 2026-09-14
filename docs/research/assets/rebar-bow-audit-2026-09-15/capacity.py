exec((__import__('pathlib').Path(__file__).resolve().parent/'probe.py').read_text().split('results=[];t=time.time()')[0])
results=[]
for number in [11,62,137]:
 unit=units[number-1];old=np.array(r['instances'][number-1]['centerlineM']);raw=np.load(OUT/f'raw-{number}.npz');rp=raw['points'];rn=raw['normals'];src=raw['source'];rad=unit['radius'];tol=max(.0009,.36*rad);captured={}
 ii=net._candidate_indices(tree,ids,unit['start'],unit['end'],max(.05,min(.10,.22*unit['length']),9*rad),neighbours=768)
 def capture(points,*args,**kwargs):
  captured.update(points=points,args=args,kwargs=kwargs);return realaxis(points,*args,**kwargs)
 net.fit_prior_axis=capture;model,reason,_=net._fit_unit(p,n,ii,unit,unit['start'],unit['direction']);net.fit_prior_axis=realaxis
 np.save(OUT/f'selected-{number}.npy',captured['points'])
 held=(src%5==0)&~np.isin(src,ii);train=src%5!=0
 def measure(c):
  res=np.abs(net._polyline_distances(rp,c)-rad)
  return dict(medianMm=float(np.median(res)*1000),support=float(np.mean(res<=tol)),heldMedianMm=float(np.median(res[held])*1000),heldSupport=float(np.mean(res[held]<=tol)),heldPoints=int(held.sum()))
 results.append(dict(id=number,variant='saved',**measure(old)))
 for name,count,smoothing,rawfit,dense in [('dense65',6,.003,False,True),('smooth10x',6,.0003,False,False),('nosmooth',6,0,False,False),('knots10',10,.003,False,False),('knots14',14,.003,False,False),('raw6',6,.003,True,False),('raw10',10,.003,True,False),('raw14',14,.003,True,False)]:
  source=inspect.getsource(realaxis).replace('np.r_[np.zeros(4), 1/3, 2/3, np.ones(4)]',f'np.r_[np.zeros(4), np.linspace(0,1,{count-2})[1:-1], np.ones(4)]').replace('np.eye(6)',f'np.eye({count})').replace('* .003',f'* {smoothing}')
  namespace=dict(prior.__dict__);exec(source,namespace)
  axis,reason=namespace['fit_prior_axis'](rp[train] if rawfit else captured['points'],*captured['args'],**captured['kwargs'],spline_coefficients=count)
  rec=dict(id=number,variant=name,reason=reason)
  if axis:
   st=np.linspace(0,1,65 if dense else 17);off=axis['spline'](st)@axis['coeff'];c=net._exact_length(axis['start']+st[:,None]*unit['length']*axis['tangent']+off[:,:1]*axis['u']+off[:,1:]*axis['v'],unit['length'])
   np.save(OUT/f'axis-{number}-{name}.npy',c);rec.update(model=axis['shapeModel'],rmseMm=axis['fitRmseM']*1000,**measure(c))
  results.append(rec)
 print(number,[(x['variant'],round(x.get('heldSupport',0),3),round(x.get('heldMedianMm',0),2)) for x in results if x['id']==number],flush=True)
 (OUT/'capacity.json').write_text(json.dumps(results,indent=2))
