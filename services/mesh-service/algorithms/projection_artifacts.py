"""Render projection evidence from measured grids, outside classifier timing."""
from html import escape
from pathlib import Path

import numpy as np
from PIL import Image


PALETTE = np.array([[11, 16, 32], [100, 116, 139], [245, 158, 11], [45, 212, 191]], np.uint8)


def _heat(values, log=False):
    value = np.log1p(values) if log else values
    positive = value[value > 0]
    limit = float(np.quantile(positive, .995)) if len(positive) else 1.
    normalized = np.clip(value/max(limit, 1.e-12), 0, 1)
    stops = np.array([[11, 16, 32], [37, 74, 136], [29, 158, 167], [202, 224, 73], [255, 234, 180]])
    rgb = np.stack([np.interp(normalized, np.linspace(0, 1, len(stops)), stops[:, i]) for i in range(3)], axis=2)
    return rgb.astype(np.uint8), limit


def _layers_svg(report, cache):
    width, height = 1200, 680+max(0, (len(report['layers'])-1)//3)*28
    left, right = 100, 1160
    raw = cache["histogram_all"]; remaining = cache["histogram_remaining"]
    origin = cache["histogram_origin"]; size = report["parameters"]["histogram_bin"]
    z = origin+(np.arange(len(raw))+.5)*size
    xmin, xmax = origin, origin+len(raw)*size
    sx = lambda value: left+(value-xmin)/(xmax-xmin)*(right-left)
    parts = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">',
             '<rect width="100%" height="100%" fill="#0b1020"/>',
             '<g font-family="Noto Sans CJK SC,Microsoft YaHei,sans-serif" fill="#dfe8f8">',
             '<text x="40" y="37" font-size="24">Z 轴密度投影 · 原始高度与候选分层</text>',
             '<text x="40" y="66" font-size="14" fill="#94a3b8">全部原始点参与计数；先显示台面峰，再查看移除台面后的高度峰。候选层数由数据决定。</text>']
    colors = ['#2dd4bf', '#f59e0b', '#a78bfa', '#f472b6']
    for panel, (counts, top, bottom, title) in enumerate([(raw, 112, 276, '全部点云'), (remaining, 355, 580, '移除台面后')]):
        maximum = max(float(counts.max()), 1.)*1.08
        sy = lambda value: bottom-value/maximum*(bottom-top)
        parts.append(f'<text x="{left}" y="{top-15}" font-size="18">{title}</text>')
        if panel:
            for i, layer in enumerate(report['layers']):
                x1, x2 = sx(layer['lowM']), sx(layer['highM'])
                parts.append(f'<rect x="{x1:.2f}" y="{top}" width="{x2-x1:.2f}" height="{bottom-top}" fill="{colors[i%4]}" opacity="0.11"/>')
                parts.append(f'<line x1="{sx(layer["heightM"]):.2f}" x2="{sx(layer["heightM"]):.2f}" y1="{top}" y2="{bottom}" stroke="{colors[i%4]}" stroke-dasharray="5 5"/>')
        for tick in np.linspace(0, maximum, 5):
            y = sy(tick)
            parts.append(f'<line x1="{left}" x2="{right}" y1="{y:.2f}" y2="{y:.2f}" stroke="#26334b"/>')
            parts.append(f'<text x="{left-12}" y="{y+5:.2f}" text-anchor="end" font-size="12" fill="#94a3b8">{int(tick):,}</text>')
        coordinates = ' '.join(f'{sx(a):.2f},{sy(b):.2f}' for a, b in zip(z, counts))
        parts.append(f'<polyline points="{coordinates}" fill="none" stroke="{"#94a3b8" if not panel else "#2dd4bf"}" stroke-width="2"/>')
        for mm in np.arange(np.ceil(xmin*1000/10)*10, xmax*1000, 10):
            x = sx(mm/1000)
            parts.append(f'<text x="{x:.2f}" y="{bottom+22}" text-anchor="middle" font-size="12">{mm:.0f}</text>')
    parts.append('<text x="1155" y="621" text-anchor="end" font-size="14">原始 Z 高度 / mm</text>')
    for i, layer in enumerate(report['layers']):
        label = f'{layer["id"]}. {layer["name"]}  {layer["heightM"]*1000:.1f} mm'
        parts.append(f'<text x="{45+(i%3)*370}" y="{656+(i//3)*28}" font-size="16" fill="{colors[i%4]}">{escape(label)}</text>')
    parts.append('</g></svg>')
    return '\n'.join(parts)


def write_projection_artifacts(directory, run_id, report, cache):
    target = Path(directory)/"projection"; target.mkdir()
    density, density_limit = _heat(cache["density"], log=True)
    span, span_limit = _heat(cache["height_span"])
    binary = np.where(cache["binary"][:, :, None], np.array([227, 235, 247], np.uint8), PALETTE[0])
    images = {'binary': (binary, '移除台面后的俯视二值图'),
              'density': (density, '俯视点密度 · 对数色阶，亮处点数多'),
              'height': (span, '每个俯视像素的 Z 跨度 · 辅助定位竖向面'),
              'classes': (PALETTE[cache["image_labels"]], '投影分类 · 钢筋绿 / 夹具黄')}
    if 'fixture_edge_image' in cache:
        images['edges'] = (PALETTE[cache['fixture_edge_image']], '夹具贴边回收 · 黄：从钢筋改回夹具的原始点 / 灰：非台面点')
    if 'fixture_footprint_image' in cache:
        images['fixtures'] = (PALETTE[cache['fixture_footprint_image']], '夹具俯视范围 · 实际剔除受实测高度限制')
    for view in report.get('multiview', {}).get('views', []):
        images[view['key']] = (PALETTE[cache[view['key']]],
            f"侧向 {view['angleDeg']:g}° · 绿：腹杆恢复候选（标高与夹具范围复核前） · {view['sliceCount']} 个重叠切片")
    for key, (rgb, title) in images.items():
        Image.fromarray(np.flipud(rgb)).save(target/f'{key}.png', compress_level=2)
        report['images'][key] = {'url': f'/runs/{run_id}/projection/{key}.png', 'title': title}
    (target/'layers.svg').write_text(_layers_svg(report, cache), encoding='utf-8')
    report['images']['layers'] = {'url': f'/runs/{run_id}/projection/layers.svg', 'title': 'Z 轴密度曲线与候选高度层'}
    report['imageScale'] = {'orientation': '+X right, +Y up', 'densityLogDisplayMax': density_limit,
                            'heightDisplayMaxM': span_limit, 'displayPercentile': 99.5,
                            'note': 'Display saturation only; classification uses uncapped counts/spans'}
