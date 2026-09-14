import os
os.environ['MPLBACKEND']='Agg'
from pathlib import Path
import numpy as np
import matplotlib.pyplot as plt
D=Path('/tmp/cloudbim-bow-audit-20260915');raw=np.load(D/'raw-11.npz');p=raw['points'];o=raw['owner'];sel=np.arange(0,len(p),4)
fig,axs=plt.subplots(2,1,figsize=(11,7),layout='constrained',sharex=True)
for ax,var,title in zip(axs,['saved','knots10'],['Current: 6 coefficients','Diagnostic experiment: 10 coefficients, same points and tolerance']):
 ax.scatter(p[sel,0],p[sel,1]*1000,c=np.where(o[sel]==11,'#54b795','#b4b6bd'),s=2,rasterized=True,label='Scan (colors from current run)')
 c=np.load(D/f'axis-11-{var}.npy');ax.plot(c[:,0],c[:,1]*1000,color='#d86f17',lw=2,label='Fitted axis')
 ax.plot(c[:,0],c[:,1]*1000+4,color='#d86f17',lw=.8,ls='--',label='Projected tube envelope (radius 4 mm)');ax.plot(c[:,0],c[:,1]*1000-4,color='#d86f17',lw=.8,ls='--')
 ax.axvspan(4.3,4.85,color='#407ad2',alpha=.1,label='Measured bend segment')
 ax.set(title=title,ylabel='Y (mm)',ylim=(-1803,-1768));ax.grid(alpha=.2);ax.legend(fontsize=8,loc='upper left')
axs[-1].set_xlabel('X (m)');fig.savefig(D/'local-bow-capacity.png',dpi=160)
