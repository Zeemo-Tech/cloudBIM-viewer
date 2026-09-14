"""Diagnostic only: actual selected surface points; independently held raw bend points."""
import os
os.environ['OPENBLAS_NUM_THREADS']='1'
from pathlib import Path
import sys,json
import numpy as np
ROOT=next(p for p in Path(__file__).resolve().parents if (p/'services/mesh-service').is_dir());sys.path.insert(0,str(ROOT/'services/mesh-service'))
import rebar_prior_axis as prior
from algorithms.rebar_control_net import _exact_length,_polyline_distances
D=Path(__file__).resolve().parent;f=np.load(D/'fixture-11.npz');fn=prior.fit_prior_axis
count=int(sys.argv[1]) if len(sys.argv)>1 else 6
length=float(f['length']);radius=float(f['radius']);axis,reason=fn(f['points'],f['start'],f['tangent'],length,radius,initial_centerline=f['initial'],guard_bending=True,spline_coefficients=count)
assert axis is not None,reason
st=np.linspace(0,1,17);off=axis['spline'](st)@axis['coeff'];curve=_exact_length(axis['start']+st[:,None]*length*axis['tangent']+off[:,:1]*axis['u']+off[:,1:]*axis['v'],length)
e=np.abs(_polyline_distances(f['validation'],curve)-radius);support=float(np.mean(e<=max(.0009,.36*radius)))
print(json.dumps(dict(coefficients=count,validationPoints=len(e),medianMm=float(np.median(e)*1000),surfaceSupport=support)))
assert support>=.85,'REPRODUCED: locally bowed surface falls outside unchanged cylinder tolerance'
