#!/usr/bin/env python3
"""Render measured V5 snapshots as fixed-extent orthographic XY diagnostic plots."""
import argparse
import csv
import json
from pathlib import Path
import struct
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "services/mesh-service"))


def write_html(out, report):
    """Self-contained data, local image links; no runtime network dependencies."""
    data = json.dumps(report, ensure_ascii=False).replace("</", "<\\/")
    document = r'''<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>点云分类去噪 · 逐步耗时与俯视图</title><style>
*{box-sizing:border-box}body{margin:0;background:#f4f5f6;color:#1c2733;font:15px/1.6 system-ui,"Noto Sans CJK SC",sans-serif}header,main{max-width:1480px;margin:auto;padding:28px 38px}header{border-bottom:1px solid #d4dbe2}h1{font-size:28px;letter-spacing:-.8px;margin:3px 0 10px}h2{font-size:21px;margin:0 0 14px}h3{font-size:16px}p{margin:8px 0}.eyebrow{color:#64748b;font-size:12px;letter-spacing:1.3px}.meta{color:#64748b;font-size:13px}.stats{display:flex;gap:46px;padding:20px 0 6px;flex-wrap:wrap}.stats strong{font-size:30px;display:block;font-weight:600}.stats span{color:#64748b}.finding{padding:14px 18px;background:#e6f3ee;border-left:3px solid #247957;margin:12px 0 24px}.layout{display:grid;grid-template-columns:270px 1fr;gap:22px}.steps{max-height:900px;overflow:auto}button{font:inherit;text-align:left;border:1px solid transparent;background:none;color:inherit;padding:10px 12px;cursor:pointer;border-radius:4px}button small{display:block;color:#64748b;font-size:12px}.steps button{display:block;width:100%;margin-bottom:3px}.steps button.active{background:#fff;border-color:#237957}.steps button:hover{background:#e7ebed}.viewer{min-width:0;background:white;border:1px solid #d9dfe3;padding:22px}.viewer img{width:100%;display:block;background:#0f1723}.viewer a{color:#226b53}.controls{display:flex;align-items:center;gap:10px;margin-top:14px}.controls input{flex:1}.caption{font-size:13px;color:#64748b}.details{display:grid;grid-template-columns:1.2fr 1fr;gap:36px;margin-top:38px}table{width:100%;border-collapse:collapse;font-size:13px}th,td{padding:9px 8px;border-bottom:1px solid #d8dfe4;text-align:left}th{color:#64748b;font-weight:500}td.num{text-align:right;font-variant-numeric:tabular-nums}.bar{height:5px;background:#2c8061;margin-top:4px;min-width:1px}.sub{color:#64748b;font-size:12px}.notes{padding:20px 0;margin-top:26px;border-top:1px solid #d4dbe2;color:#586677}a{color:#216e52}details{margin-top:20px}summary{cursor:pointer}pre{overflow:auto;max-height:300px;font-size:12px}footer{font-size:12px;color:#64748b;padding-top:24px}@media(max-width:900px){header,main{padding:20px}.layout,.details{grid-template-columns:1fr}.steps{display:flex;max-height:none;overflow:auto}.steps button{min-width:180px}.stats{gap:20px}.viewer{padding:12px}}@media print{.steps,.controls{display:none}.layout{display:block}}
</style><header><div class="eyebrow">CLOUDBIM / V5 · 实测诊断 · 2026-09-08</div><h1>分类去噪，到底慢在哪一步？</h1><p class="meta">YB-1mesh2.0.las · 资产 95b6b41c5857d9eb3407b155 · 同一源坐标俯视 · 保存真实中间状态</p><div class="stats" id="stats"></div></header>
<main><div class="finding" id="finding"></div><div class="layout"><nav class="steps" id="steps" aria-label="诊断步骤"></nav><article class="viewer"><h2 id="title"></h2><p id="counts" class="meta"></p><a id="image-link" target="_blank"><img id="image" alt="当前阶段固定视角俯视图"></a><p id="note" class="caption"></p><div class="controls"><button id="prev">← 上一步</button><input id="slider" type="range" min="0" aria-label="选择步骤"><button id="next">下一步 →</button></div><p class="caption">点击图片打开 2700 像素原图，可放大检查。左图看本步分类 / 候选；右图统一用青色突出剩余点或指定类别，不代表钢筋标签。</p></article></div>
<div class="details"><section><h2>完整耗时账本</h2><p class="caption">一次新进程实测，扣除保存快照的直接耗时。下列主行互不重复；夹具子项已包含在夹具总时长中。</p><table><thead><tr><th>步骤</th><th>秒 / 估算计算总时长占比</th></tr></thead><tbody id="timings"></tbody></table></section><section><h2>点数流转</h2><div id="flow"></div><h3>最终原始点归属</h3><table><thead><tr><th>类别</th><th>原始点数</th><th>占比</th></tr></thead><tbody id="classes"></tbody></table><h3>最终归属内部耗时</h3><div id="ownership"></div><h3>夹具的 5 个子步骤</h3><table><tbody id="fixtures"></tbody></table><p class="caption">识别效果待你对照图像判断；当前没有逐点人工标注，不能把“输出与原版本一致”当作准确率。</p></section></div>
<div class="notes"><h2>统计与图像口径</h2><div id="notes"></div><p>俯视图是由本次运行保存的真实点、掩码、模型和最终标签离线渲染的诊断图，不是原应用界面的截图。沿源坐标 −Z 看向 XY 平面，所有图片固定 X/Y 范围与等比例尺。全点投影不做遮挡剔除；同一像素类别绘制顺序为未知→台面→夹具→钢筋→噪声。</p><p>台面 / 夹具的中间图使用 3.5 mm 检测格网代表点；原始噪声与最终归属图使用全量 921 万点；最终显示图使用实际 PNTS 点。点数差别会明确标在图上。候选中心线不等于最终钢筋标签。</p><p>输入分块大小记录在完整测量 JSON 的 readerChunkSize，4 个受内存预算约束的空间 worker，8 GiB 进程组内存上限。未计 HTTP 排队、请求传输和浏览器加载渲染，也没有清空操作系统文件缓存。</p><p><a href="overview.png" target="_blank">俯视图总览</a> · <a href="timings.csv">下载耗时 CSV</a> · <a href="report-data.json">完整测量 JSON</a></p><details><summary>参数与嵌套函数计时（不可重复相加）</summary><pre id="parameters"></pre></details></div><footer>原始测量、阶段模型和数组保存在 ../run-v2（第一轮为 ../run）；本次使用独立产物目录，未改动资产 latest。</footer></main>
<script>const D=__DATA__;let selected=2;const views=D.views;const fmt=n=>Number(n).toLocaleString('zh-CN');const sec=n=>n==null?'—':Number(n).toFixed(3);const total=D.computeElapsedEstimateS;const S=Object.fromEntries(D.stageRecords.map(x=>[x.name,x]));
document.querySelector('#stats').innerHTML=`<div><strong>${sec(total)} s</strong><span>计算耗时估算</span></div><div><strong>${fmt(D.summary.source.finitePointCount)}</strong><span>全量原始点</span></div><div><strong>${fmt(S.denoise.confirmedPointCount)}</strong><span>确认噪声点</span></div><div><strong>${fmt(D.summary.source.sceneClassCounts.unknown)}</strong><span>最终未知点</span></div>`;
document.querySelector('#finding').textContent=`本次噪声检测耗时 ${sec(S.denoise.elapsedS)} 秒，确认噪声 ${fmt(S.denoise.confirmedPointCount)} 点、疑似噪声 ${fmt(S.denoise.pendingPointCount)} 点。夹具阶段 ${sec(S.fixtures.elapsedS)} 秒，占计算耗时约 ${(100*S.fixtures.elapsedS/total).toFixed(1)}%。建议先查看「噪声检测」「夹具识别与排除」「最终未知点」。`;
const steps=document.querySelector('#steps');views.forEach((v,i)=>{const b=document.createElement('button');b.innerHTML=`${v.title}<small>${sec(v.elapsedS)} 秒 · ${fmt(v.inputPointCount)} 个观测点</small>`;b.onclick=()=>show(i);steps.append(b)});
const slider=document.querySelector('#slider');slider.max=views.length-1;slider.oninput=()=>show(+slider.value);document.querySelector('#prev').onclick=()=>show(Math.max(0,selected-1));document.querySelector('#next').onclick=()=>show(Math.min(views.length-1,selected+1));
function show(i){selected=i;const v=views[i];document.querySelector('#title').textContent=v.title;document.querySelector('#counts').textContent=`${sec(v.elapsedS)} 秒 · 当前图 ${fmt(v.inputPointCount)} 点 · 右图 ${fmt(v.remainingPointCount)} 点`;document.querySelector('#image').src=v.image;document.querySelector('#image-link').href=v.image;document.querySelector('#note').textContent=v.note;slider.value=i;[...steps.children].forEach((b,j)=>{b.classList.toggle('active',i===j);b.setAttribute('aria-current',i===j?'step':'false')})}show(selected);
const names={'spatial-index':'空间索引','denoise':'保守噪声检测','features':'全量基础特征 / 检测格网','table':'台面识别与排除','fixtures':'夹具处理（总计）','planar-bars':'平面钢筋','terminal-hooks':'末端弯钩','web-bars':'斜腹杆','ownership-review':'全局归属复核（关闭）','raw-support-verification':'原始点支持验证','raw-ownership-finalization':'全量原始点最终归属','intersections':'理论交点'};
const rows=[['读取 / 准备初始样本',D.functionTimings['source-bootstrap'].sumElapsedS]];D.stageRecords.forEach(s=>{rows.push([names[s.name],s.elapsedS]);if(s.name==='fixtures')rows.push(['夹具排除后边界特征重算',D.functionTimings['multiscale:analyze:after-fixture']?.sumElapsedS||0])});if(D.functionTimings.export_sidecars)rows.push(['特征 / 原始标签导出',D.functionTimings.export_sidecars.sumElapsedS]);rows.push(['显示瓦片标签投影 / 写出',D.functionTimings['display-tile-rewrite'].sumElapsedS]);const residual=total-rows.reduce((s,x)=>s+x[1],0);rows.push(['其他：掩码衔接、复制、摘要与发布等',residual]);document.querySelector('#timings').innerHTML=rows.map(([n,t])=>`<tr><td>${n}</td><td class="num">${sec(t)} · ${(100*t/total).toFixed(1)}%<div class="bar" style="width:${100*t/total}%"></div></td></tr>`).join('')+`<tr><td><strong>合计（估算）</strong></td><td class="num"><strong>${sec(total)}</strong></td></tr>`;
document.querySelector('#flow').innerHTML=`<p>原始输入 <b>${fmt(S.denoise.inputPointCount)}</b> → 去噪后 <b>${fmt(S.denoise.inputPointCount-S.denoise.confirmedPointCount)}</b></p><p>检测代表点 <b>${fmt(S.table.inputPointCount)}</b> → 去台面后 <b>${fmt(S.fixtures.inputPointCount)}</b> → 去夹具后 <b>${fmt(S['planar-bars'].inputPointCount)}</b> → 腹杆检测输入 <b>${fmt(S['web-bars'].inputPointCount)}</b></p><p class="caption">检测格网减少的是几何检测候选，不是从原始点云删除记录。</p>`;
document.querySelector('#classes').innerHTML=Object.entries({unknown:'未知',table:'台面',rebar:'钢筋',noise:'噪声',fixture:'夹具'}).map(([k,n])=>{const c=D.summary.source.sceneClassCounts[k];return `<tr><td>${n}</td><td class="num">${fmt(c)}</td><td class="num">${(100*c/D.summary.source.finitePointCount).toFixed(2)}%</td></tr>`}).join('');
document.querySelector('#fixtures').innerHTML=Object.entries({boundaryFeaturesElapsedS:'台面排除后边界特征',detectionElapsedS:'初始面检测',rawRefinementElapsedS:'原始点夹具面细化',boltDetectionElapsedS:'螺栓检测',maskElapsedS:'生成夹具掩码'}).map(([k,n])=>`<tr><td>${n}</td><td class="num">${sec(S.fixtures[k])} 秒</td></tr>`).join('');
document.querySelector('#notes').innerHTML=`<p>真实进程墙钟 ${sec(D.wallElapsedS)} 秒；保存诊断快照 ${sec(D.snapshotOverheadS)} 秒；相减得到计算耗时估算 ${sec(total)} 秒。函数包装开销和快照对后续缓存的影响未完全消除，因此这是有观测插桩的一次运行，不是多次均值或无插桩极限速度。</p><p>源码运行期间变化：${D.codeChangedDuringRun?'是':'否'}；源文件保持一致：${D.sourceUnchanged?'是':'否'}；原始标签覆盖 ${fmt(D.renderValidation.rawLabelCoverage)} 点，无重复或缺失；检测点坐标与原始 source_index 逐点精确核对通过。</p>`;
document.querySelector('#ownership').innerHTML=`<p>边界更新准备 ${sec(D.functionTimings._prime_boundary_updates?.sumElapsedS)} 秒；逐点分类函数累计 ${sec(D.functionTimings.classify?.sumElapsedS)} 秒（${D.functionTimings.classify?.calls} 次调用）。这两项包含在最终归属阶段，不重复加入总时长。</p>`;
document.querySelector('#parameters').textContent=JSON.stringify({parameters:D.parameters,functionTimings:D.functionTimings,note:D.functionTimingNote},null,2);
</script></html>'''
    (out / "index.html").write_text(document.replace("__DATA__", data))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--evidence", type=Path, required=True)
    parser.add_argument("--artifact", type=Path, required=True)
    args = parser.parse_args()
    import numpy as np
    import laspy
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from PIL import Image
    plt.rcParams["font.family"] = "Noto Sans CJK JP"
    plt.rcParams["axes.unicode_minus"] = False
    out = args.evidence.parent / "report"
    out.mkdir(exist_ok=True)
    report = json.loads((args.evidence / "measurement.json").read_text())
    assert report["completed"] and not report["codeChangedDuringRun"] and report["sourceUnchanged"]
    with laspy.open(report["source"]) as source:
        count = source.header.point_count
        xyz = np.lib.format.open_memmap(out / "source-xyz.npy", mode="w+", dtype="float64", shape=(count, 3))
        offset = 0
        for chunk in source.chunk_iterator(250000):
            xyz[offset:offset+len(chunk)] = np.column_stack((chunk.x, chunk.y, chunk.z))
            offset += len(chunk)
    assert offset == count and np.isfinite(xyz).all()
    mins, maxs = xyz[:, :2].min(axis=0), xyz[:, :2].max(axis=0)
    center = (mins + maxs) / 2
    span = (maxs - mins) * 1.04
    lo, hi = center-span/2, center+span/2
    extent = [lo[0], hi[0], lo[1], hi[1]]
    width = 1800
    height = max(400, round(width * span[1] / span[0]))
    background = np.array([15, 23, 35], dtype=np.uint8)
    colors = {0: [122, 141, 162], 1: [203, 213, 225], 2: [45, 212, 191], 3: [232, 121, 249], 4: [251, 146, 60]}
    def raster(points, labels=None, mask=None, color=None):
        if mask is not None:
            points = points[mask]
            if labels is not None:
                labels = labels[mask]
        coords = np.floor((points[:, :2]-lo) / span * [width-1, height-1]).astype(np.int32)
        valid = np.all((coords >= 0) & (coords < [width, height]), axis=1)
        indices = coords[valid, 0].astype(np.int64) + width*coords[valid, 1]
        pixels = np.tile(background, (height*width, 1))
        if labels is None:
            density = np.bincount(indices, minlength=height*width)
            occupied = density > 0
            strength = np.minimum(1., .45 + .16*np.log1p(density[occupied]))
            tint = np.array(color or [181, 201, 219])
            pixels[occupied] = background + (tint-background)*strength[:, None]
        else:
            labels = labels[valid]
            # X-ray projection: categories are painted in the documented order,
            # never claim this is z-buffer visibility or a native UI screenshot.
            for kind in (0, 1, 4, 2, 3):
                pixels[indices[labels == kind]] = colors[kind]
        return pixels.reshape(height, width, 3)
    entries = []
    def plot(key, title, elapsed, points, labels=None, keep=None, note="", lines=(), right_title=None, markers=()):
        file = key + ".png"
        fig, axes = plt.subplots(1, 2, figsize=(18, 6), facecolor="#0f1723")
        left = raster(points, labels)
        right = raster(points, mask=keep, color=[45, 212, 191])
        titles = ["当前阶段：全部观测点 / 分类或候选中心线", right_title or "进入下一阶段的观测点"]
        for ax, image, subtitle in zip(axes, (left, right), titles):
            ax.imshow(image, origin="lower", extent=extent, interpolation="nearest")
            ax.set_title(subtitle, color="#e2e8f0", fontsize=12, pad=12)
            ax.set_xlabel("源坐标 X（米）", color="#94a3b8")
            ax.set_ylabel("源坐标 Y（米）", color="#94a3b8")
            ax.tick_params(colors="#94a3b8", labelsize=9)
            for spine in ax.spines.values():
                spine.set_color("#334155")
            ax.set_xlim(lo[0], hi[0]); ax.set_ylim(lo[1], hi[1]); ax.set_aspect("equal")
        for item in lines:
            for segment in item.get("observedSegments", []):
                path = np.asarray(segment["points"])
                axes[0].plot(path[:, 0], path[:, 1], color="#f0abfc", linewidth=.65, alpha=.9)
        if markers:
            locations = np.array([item["position"] for item in markers])
            axes[0].scatter(locations[:, 0], locations[:, 1], s=10, color="#f43f5e", linewidths=.2, edgecolors="white")
        timing_label = f"{elapsed:.3f} 秒" if elapsed is not None else "效果复查，无新增计算"
        fig.suptitle(f"{title}  |  {timing_label}", color="#f8fafc", fontsize=19, x=.06, ha="left")
        retained = len(points) if keep is None else int(np.count_nonzero(keep))
        fig.text(.06, .08, f"本图观测点 {len(points):,}  ·  右图 {retained:,} 点  ·  沿源坐标 −Z 俯视，X 向右 / Y 向上", color="#cbd5e1", fontsize=11)
        fig.text(.06, .047, note, color="#94a3b8", fontsize=9)
        fig.subplots_adjust(left=.06, right=.98, top=.83, bottom=.22, wspace=.15)
        fig.savefig(out / file, dpi=150, facecolor=fig.get_facecolor()); plt.close(fig)
        item = dict(key=key, title=title, elapsedS=elapsed, inputPointCount=len(points), remainingPointCount=retained, note=note, image=file)
        entries.append(item)
        print(json.dumps(item, ensure_ascii=False), flush=True)
    records = {item["name"]: item for item in report["stageRecords"]}
    def seconds(key):
        return records[key]["elapsedS"]
    raw_noise = np.load(args.evidence / "denoise.npz")
    noise = raw_noise["noise"].astype(bool)
    detection = np.load(args.evidence / "features.npz")
    pts = detection["xyz"]
    source_indices = detection["source_index"]
    assert np.array_equal(pts, xyz[source_indices])
    scene = np.zeros(len(pts), np.uint8)
    raw_note = "全量 LAS 原始点；无抽样。投影像素可能重叠，点数来自源记录，不是截图像素数。"
    detect_note = "3.5 mm 检测格网代表点；不是原始点删减。白=台面，橙=夹具，灰=未分；粉线=实测候选中心线。"
    plot("00-source", "原始输入", report["functionTimings"]["source-bootstrap"]["sumElapsedS"], xyz, note=raw_note, right_title="原始输入（对照）")
    plot("01-spatial-index", "01 空间索引", seconds("spatial-index"), xyz, note=raw_note, right_title="建索引后（点集不变）")
    plot("02-denoise", "02 保守噪声检测", seconds("denoise"), xyz, np.where(noise, 3, 0), ~noise,
         note=f"原始点确认噪声 {int(noise.sum()):,}；疑似噪声 {int(raw_noise['suspect'].sum()):,}。噪声标粉色；灰色为保留点。")
    plot("03-features", "03 全量特征 / 检测格网", seconds("features"), pts, note=detect_note, right_title="用于几何检测的代表点")
    table = np.load(args.evidence / "table.npz")["table"]
    scene[table] = 1
    plot("04-table", "04 台面识别与排除", seconds("table"), pts, scene, ~table, detect_note)
    fixture = np.load(args.evidence / "fixtures.npz")["fixture"]
    residual_rows = np.flatnonzero(~table)
    scene[residual_rows[fixture]] = 4
    active = scene == 0
    plot("05-fixtures", "05 夹具识别与排除", seconds("fixtures"), pts, scene, active, detect_note)
    boundary = report["functionTimings"].get("multiscale:analyze:after-fixture", {}).get("sumElapsedS", 0.)
    plot("06-boundary", "06 夹具排除后边界特征重算", boundary, pts, scene, active,
         "额外计时：现有阶段日志未单列的 multiscale 调用；点集不变，只重算特征。")
    planar = json.loads((args.evidence / "planar-bars.json").read_text())["planar"]
    plot("07-planar", "07 平面钢筋检测", seconds("planar-bars"), pts, scene, active,
         detect_note + " 本步建立候选模型，尚非最终逐点归属。", planar, "本阶段使用的台面 / 夹具排除后点集")
    hooks = json.loads((args.evidence / "terminal-hooks.json").read_text())["planar"]
    body = np.load(args.evidence / "terminal-hooks.npz")["body"]
    web_input = active.copy(); web_input[np.flatnonzero(active)[body]] = False
    plot("08-hooks", "08 末端弯钩 / 平面筋保护掩码", seconds("terminal-hooks"), pts, scene, web_input,
         "右图为实际进入腹杆检测的点集，使用运行时 body 掩码；左侧粉线含新增弯钩。", hooks)
    web_models = json.loads((args.evidence / "web-bars.json").read_text())
    plot("09-web", "09 斜腹杆检测", seconds("web-bars"), pts, scene, web_input,
         "右图显示腹杆检测输入；本步输出候选模型，不把候选管范围当成最终钢筋标签。", web_models["planar"]+web_models["web"], "腹杆检测输入（尚未最终归属）")
    for number, key, title in ((10, "ownership-review", "全局归属复核（默认关闭）"), (11, "raw-support-verification", "原始点支持验证")):
        model = json.loads((args.evidence / (key+".json")).read_text())
        plot(f"{number:02d}-{key}", title, seconds(key), pts, scene, web_input,
             "几何预览仍使用相同检测代表点；粉线为本步结束时保存的模型。最终标签见下一步。", model["instances"], "腹杆检测输入（对照）")
    labels = np.zeros(count, np.uint8)
    roles = np.zeros(count, np.uint8)
    seen = np.zeros(count, np.uint8)
    for path in sorted((args.artifact / "labels").glob("*.npz")):
        with np.load(path) as chunk:
            ids = chunk["source_index"]
            assert len(np.unique(ids)) == len(ids) and not seen[ids].any()
            labels[ids] = chunk["scene_class"]; roles[ids] = chunk["rebar_role"]; seen[ids] = 1
    assert seen.all()
    counts = np.bincount(labels, minlength=5)
    expected = report["summary"]["source"]["sceneClassCounts"]
    assert counts.tolist() == [expected[k] for k in ("unknown", "table", "rebar", "noise", "fixture")]
    plot("12-raw-ownership", "12 全量原始点最终归属", seconds("raw-ownership-finalization"), xyz, labels, labels == 2,
         "全量原始标签按 source_index 精确回填。白=台面，橙=夹具，青=钢筋，灰=未知，粉=噪声；右侧仅钢筋。", right_title="最终确认为钢筋的原始点")
    plot("13-intersections", "13 理论交点", seconds("intersections"), xyz, labels, labels == 2,
         "左图红色标记为实测中心线的理论交点；交点是独立几何元数据，不改变点云分类。", right_title="钢筋点云（本步不改变标签）",
         markers=json.loads((args.evidence / "intersections.json").read_text())["intersections"])
    for key, title, kind in (("14-unknown", "复查：最终未知点", 0), ("15-fixture", "复查：最终夹具点", 4), ("16-table", "复查：最终台面点", 1)):
        plot(key, title, None, xyz, labels, labels == kind, "效果复查视图，无新增计算步骤；重点检查钢筋是否被错分到右图。", right_title=title)
    if "export_sidecars" in report["functionTimings"]:
        plot("17-export", "特征 / 原始标签导出", report["functionTimings"]["export_sidecars"]["sumElapsedS"], xyz, labels, labels == 2,
             "导出属性列、特征和原始标签；本步不改变分类点集。右图统一以青色突出钢筋点。", right_title="原始钢筋标签（导出后不变）")
    # Decode the actual final display points, retaining their distinct population.
    tile_points, tile_labels = [], []
    for path in sorted((args.artifact / "tiles").rglob("*.pnts")):
        raw = path.read_bytes()
        _, _, ftj, ftb, btj, _ = struct.unpack_from("<6I", raw, 4)
        feature = json.loads(raw[28:28+ftj]); batch = json.loads(raw[28+ftj+ftb:28+ftj+ftb+btj])
        n = feature["POINTS_LENGTH"]; start = 28+ftj
        if "POSITION" in feature:
            points = np.frombuffer(raw, "<f4", n*3, start+feature["POSITION"]["byteOffset"]).reshape(-1, 3).astype(float)
        else:
            points = np.frombuffer(raw, "<u2", n*3, start+feature["POSITION_QUANTIZED"]["byteOffset"]).reshape(-1, 3).astype(float)
            points = points/65535*np.array(feature["QUANTIZED_VOLUME_SCALE"])+np.array(feature["QUANTIZED_VOLUME_OFFSET"])
        tile_points.append(points)
        tile_labels.append(np.frombuffer(raw, "u1", n, 28+ftj+ftb+btj+batch["SCENE_CLASS"]["byteOffset"]).copy())
    dp, dl = np.concatenate(tile_points), np.concatenate(tile_labels)
    plot("18-display", "显示瓦片标签投影 / 写出", report["functionTimings"]["display-tile-rewrite"]["sumElapsedS"], dp, dl, dl == 2,
         "来自最终 PNTS 的实际显示点；显示点数和原始点数不同，近邻传递不等于 source_index 身份。", right_title="显示瓦片中的钢筋点")
    report["renderValidation"] = {"rawLabelCoverage": int(seen.sum()), "rawSceneCounts": counts.tolist(),
        "detectionSourceCoordinatesExact": True, "displayPointCount": len(dp), "extentXY": extent,
        "projection": "source XY orthographic along -Z; all-point x-ray raster, category paint order unknown/table/fixture/rebar/noise",
        "nativeUIScreenshot": False, "timedRendering": False}
    report["views"] = entries
    (out / "report-data.json").write_text(json.dumps(report, ensure_ascii=False, indent=2))
    write_html(out, report)
    with (out / "timings.csv").open("w", newline="") as stream:
        writer = csv.writer(stream); writer.writerow(["stage", "elapsed_seconds", "input_points", "confirmed_points", "pending_points"])
        for item in report["stageRecords"]:
            writer.writerow([item.get(k, "") for k in ("name", "elapsedS", "inputPointCount", "confirmedPointCount", "pendingPointCount")])
        writer.writerow(["after-fixture-boundary-features", boundary])
        for key in ("source-bootstrap", "export_sidecars", "display-tile-rewrite"):
            if key in report["functionTimings"]:
                writer.writerow([key, report["functionTimings"][key]["sumElapsedS"]])
        accounted = sum(item["elapsedS"] for item in report["stageRecords"]) + boundary
        accounted += sum(report["functionTimings"].get(key, {}).get("sumElapsedS", 0.)
                         for key in ("source-bootstrap", "export_sidecars", "display-tile-rewrite"))
        writer.writerow(["other-disjoint-work", report["computeElapsedEstimateS"]-accounted])
        writer.writerow(["COMPUTE_TOTAL_ESTIMATE_DO_NOT_ADD", report["computeElapsedEstimateS"]])
        writer.writerow(["SNAPSHOT_OVERHEAD_EXCLUDED_FROM_COMPUTE", report["snapshotOverheadS"]])
        writer.writerow(["WALL_TOTAL_DO_NOT_ADD", report["wallElapsedS"]])
    with (out / "nested-function-timings.csv").open("w", newline="") as stream:
        writer = csv.writer(stream)
        writer.writerow(["function", "calls", "accumulated_elapsed_seconds", "maximum_call_seconds", "accounting"])
        for key, value in report["functionTimings"].items():
            writer.writerow([key, value["calls"], value["sumElapsedS"], value["maxElapsedS"], "nested/parallel elapsed; do not add to stage totals"])
    with (out / "fixture-substeps.csv").open("w", newline="") as stream:
        writer = csv.writer(stream); writer.writerow(["substep", "elapsed_seconds", "accounting"])
        for key, value in records["fixtures"].items():
            if key.endswith("ElapsedS"):
                writer.writerow([key, value, "included in fixtures stage"])
    # Compact contact sheet for a single glance; original images remain available.
    selected = ["02-denoise", "04-table", "05-fixtures", "07-planar", "08-hooks", "09-web", "12-raw-ownership", "14-unknown", "18-display"]
    by_key = {item["key"]: item for item in entries}
    thumbs = [Image.open(out / by_key[key]["image"]).convert("RGB").resize((900, 300)) for key in selected]
    sheet = Image.new("RGB", (1800, 300*5), "#0f1723")
    for index, thumb in enumerate(thumbs):
        sheet.paste(thumb, ((index % 2)*900, (index // 2)*300))
    sheet.save(out / "overview.png")
    (out / "source-xyz.npy").unlink()
    print(str(out), flush=True)


if __name__ == "__main__":
    main()
