"""Analytic illustration only; no project scan data or measured accuracy claims."""
from pathlib import Path
import numpy as np
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib.font_manager import FontProperties
from matplotlib.collections import LineCollection
from matplotlib.colors import Normalize

OUT = Path(__file__).resolve().parent
font = FontProperties(fname='/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc')
plt.rcParams.update({'font.family': font.get_name(), 'axes.unicode_minus': False, 'font.size': 11})
theta = np.linspace(0, 2*np.pi, 361)
design = 4*np.column_stack([np.cos(theta), np.sin(theta)])
shift = np.array([6., 0.])
observed = design + shift
normal_component = np.column_stack([np.cos(theta), np.sin(theta)]) @ shift
assert np.allclose(np.linalg.norm(observed-design, axis=1), 6)
assert abs(normal_component[:-1].mean()) < 1e-12
assert np.isclose(normal_component.max(), 6) and np.isclose(normal_component.min(), -6)

fig, axes = plt.subplots(1, 3, figsize=(13.3, 4.1), constrained_layout=True)
fig.suptitle('同一根钢筋平移 6 mm：三种显示回答不同问题（解析示意，非实测）', fontsize=15)
for ax in axes:
    ax.set_aspect('equal')
    ax.set_xlim(-6, 12); ax.set_ylim(-6, 6)
    ax.set_xlabel('横向位置 / mm'); ax.set_ylabel('竖向位置 / mm')
    ax.set_xticks([-4, 0, 6, 10]); ax.set_yticks([-4, 0, 4])
    ax.grid(alpha=.16)
    ax.plot(design[:, 0], design[:, 1], '--', color='#8793a1', lw=1.5)

axes[0].set_title('① 几何叠加：位移方向')
axes[0].plot(observed[:, 0], observed[:, 1], color='#087e8b', lw=3)
axes[0].scatter([0, 6], [0, 0], c=['#8793a1', '#087e8b'], s=26)
axes[0].annotate('', xy=(6, 0), xytext=(0, 0), arrowprops={'arrowstyle':'->', 'color':'#252e3c', 'lw':1.8})
axes[0].text(3, .6, '+6 mm', ha='center')
axes[0].text(0, -5.3, '虚线：设计截面', ha='center', fontsize=10)
axes[0].text(8, -5.3, '实线：拟合截面', ha='center', fontsize=10)

axes[1].set_title('② 中心线偏移着色：全周同值')
axes[1].plot(observed[:, 0], observed[:, 1], color='#cc6e1c', lw=6)
axes[1].text(6, 0, '全周 6 mm\n半径变化 0', ha='center', va='center')

axes[2].set_title('③ 同侧表面法向分量：圆周变号')
segments = np.stack([observed[:-1], observed[1:]], axis=1)
collection = LineCollection(segments, cmap='coolwarm', norm=Normalize(-6, 6), linewidths=6)
collection.set_array((normal_component[:-1]+normal_component[1:])/2)
axes[2].add_collection(collection)
axes[2].text(6, 0, '圆周均值 ≈ 0\n不代表没有位移', ha='center', va='center')
bar = fig.colorbar(collection, ax=axes[2], orientation='horizontal', fraction=.09, pad=.13, ticks=[-6, 0, 6])
bar.set_label('同侧对应位移在设计径向上的分量 / mm')
fig.savefig(OUT/'deviation-semantics.png', dpi=170, facecolor='white')
print('Analytic checks passed; saved', OUT/'deviation-semantics.png')
