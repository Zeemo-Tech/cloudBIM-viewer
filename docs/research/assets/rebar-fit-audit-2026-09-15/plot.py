from audit import *
from algorithms.rebar_control_net import _frame
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
def main():
 data=np.load(OUT/'ends-data.npy',allow_pickle=True).item();fig,axes=plt.subplots(1,3,figsize=(12,4.4))
 for ax,number in zip(axes,[28,29,30]):
  d=data[f'{number}-start'];xy=d['xy']*1000;owner=d['owner'];base=np.load(OUT/f'axis-{number}-192.npy');new=np.load(OUT/f'axis-{number}-768.npy');a=base[0];t=(base[1]-a)/np.linalg.norm(base[1]-a);u,v=_frame(t)
  stations=(new-a)@t;order=np.argsort(stations);center=np.array([np.interp(.055,stations[order],((new-a)@vec)[order]) for vec in [u,v]])*1000
  ax.scatter(xy[owner==0,0],xy[owner==0,1],s=4,c='#89919e',alpha=.65,label='Unassigned scan')
  ax.scatter(xy[owner>0,0],xy[owner>0,1],s=4,c='#a553b2',alpha=.6,label='Assigned scan')
  for point,color,label in [(np.zeros(2),'#df791d','Saved fit (192 neighbors)'),(center,'#058a9b','Diagnostic fit (768 neighbors)')]:
   ax.add_patch(plt.Circle(point,d['radius']*1000,fill=False,color=color,lw=2,label=label));ax.plot(*point,'+',color=color,ms=8)
  ax.set(title=f'Unit #{number} / first 15–95 mm',xlabel='Transverse u (mm)',ylabel='Transverse v (mm)',aspect='equal');ax.grid(alpha=.15);ax.set_xlim(-10,12);ax.set_ylim(-11,11)
 handles,labels=axes[0].get_legend_handles_labels();fig.legend(handles,labels,loc='lower center',ncol=2,fontsize=9)
 fig.suptitle('Same raw endpoint points, same 4 mm radius, same fitter; only candidate count changes',fontsize=11)
 fig.tight_layout(rect=(0,.14,1,.95));fig.savefig(OUT/'endpoint-sections.png',dpi=180);plt.close(fig)
if __name__=='__main__':main()
