# V5 分类去噪逐步实测与俯视图（2026-09-08）

最终采用第二轮完整运行：全量 9,216,369 点，墙钟 **367.712 秒**；快照直接开销 **0.349 秒**；计算耗时估算 **367.364 秒**。最明确的现象是噪声检测耗时 9.000 秒，却没有标记任何确认噪声或疑似噪声。

[打开交互报告](/home/hong/hong_project/cloudBIM-viewer/.cloudbim/stage-audit-20260908/report/index.html) · [俯视图总览](/home/hong/hong_project/cloudBIM-viewer/.cloudbim/stage-audit-20260908/report/overview.png) · [完整耗时 CSV](/home/hong/hong_project/cloudBIM-viewer/.cloudbim/stage-audit-20260908/report/timings.csv)

## 完整耗时

| 步骤 | 秒 | 计算耗时占比 |
|---|---:|---:|
| 读取与初始样本 | 0.261 | 0.07% |
| 空间索引 | 3.967 | 1.08% |
| 保守噪声检测 | 9.000 | 2.45% |
| 全量特征与检测格网 | 35.028 | 9.53% |
| 台面 | 1.572 | 0.43% |
| 夹具 | 107.776 | 29.34% |
| 夹具排除后的边界特征重算（原日志未单列） | 0.359 | 0.10% |
| 平面筋 | 14.266 | 3.88% |
| 末端弯钩 | 4.588 | 1.25% |
| 斜腹杆 | 78.439 | 21.35% |
| 全局归属复核（关闭） | 0.000 | 0.00% |
| 原始支持验证 | 4.715 | 1.28% |
| 全量原始归属 | 67.250 | 18.31% |
| 理论交点 | 0.015 | 0.00% |
| 特征与原始标签导出 | 31.686 | 8.63% |
| 显示瓦片标签投影与写出 | 6.656 | 1.81% |
| 其他掩码衔接、复制、摘要与发布等 | 1.788 | 0.49% |
| **合计（估算）** | **367.364** | **100%** |

夹具子步骤均包含在夹具总时长中，不重复加总：

- 台面排除后边界特征：2.023 秒。
- 初始面检测：16.488 秒。
- 原始点面细化：74.562 秒。
- 螺栓检测：13.819 秒。
- 夹具掩码：0.884 秒。

最终归属内部：边界更新准备 42.169 秒，逐点分类函数累计 24.260 秒（137 次调用）；这些时间已包含在最终归属阶段。其他并行 worker 的调用累计耗时可能重叠，不能作为墙钟相加。

## 点数与效果复查

- 原始输入 9,216,369 点；确认噪声 0，疑似噪声 0。
- 几何检测格网代表点 808,771 → 排除台面后 355,850 → 排除夹具后 189,285 → 腹杆检测输入 103,570。格网代表点减少不等于删除原始点。
- 最终原始分类：台面 5,299,984；夹具 1,945,477；钢筋 1,270,490；未知 700,418；噪声 0。
- 最终显示瓦片：2,302,885 点，其中钢筋 317,462 点。显示近邻传递不等于原始源索引身份。
- 未知点俯视图中仍可见连续条状结构，值得人工核查是否存在钢筋漏分；没有人工逐点标注，不能据此给出准确率。

## 图像口径

提供 19 张 2700×900 PNG，包含各主阶段、导出、显示瓦片及未知/夹具/台面的单独复查视图。每图左侧为本步点集与分类或保存的候选中心线，右侧为剩余点或指定类别。右图青色仅用于突出选中点集，不表示该点集都是钢筋。

这些是从真实中间状态离线渲染的诊断图，不是原应用历史界面的截图。固定源坐标 XY 范围、沿 −Z 俯视、等比例尺、全点投影不做深度遮挡剔除；不对计算点集另作截图抽样。源点与最终归属图为全量原始点，中间几何图使用实际检测代表点，最终显示图解码实际 PNTS。粉线表示候选实测中心线，红点为理论交点。

## 复验与限制

- 第二轮 `completed=true`、`sourceUnchanged=true`、`codeChangedDuringRun=false`。源 LAS SHA-256 为 `eb229b7c514e918c03534184eb56ceabdfd850c57b1b3503172bd8f290ebab52`。
- 全量原始标签 source_index 无重复、无缺失；808,771 个检测点坐标与 LAS 源索引逐点精确相等。
- 第二轮与原有 performance-speed-20260908-v2 对照：1,716 个原始标签数组精确相等，354 个 PNTS 逐文件字节相同。
- 浏览器验证通过 19 张图加载、前后步骤切换；无页面异常、NaN 或横向溢出。两份脚本编译检查及 git diff --check 通过。
- 当前运行中的 mesh 容器与工作区 pipeline.py、features.py SHA-256 相同。
- 第二轮使用正常服务的 100,000 点读取块；4 workers，8 GiB 进程组内存上限、禁用进程组 swap。未通过新建端口或手动启动服务运行；使用项目 Python 环境执行独立测量进程，因为需要在不更新资产 latest 的情况下捕获中间状态。
- 一次有插桩的实测，扣除直接快照开销仍不完全消除包装函数与缓存影响。未清空 OS 文件缓存，未计 HTTP 排队、传输或浏览器渲染；不是用户点击到显示完成的耗时。
- 第一轮墙钟 366.892 秒，仅用于对照。第一轮采集脚本的检测点索引存在重复排序，已保留原快照与精确修复记录；第二轮直接采集正确索引并独立核验。第一轮未单独捕获导出计时，因此报告只采用完整的第二轮记录。

## 复跑

使用新目录名，避免覆盖已保存的不可变产物：

```bash
systemd-run --user --scope --quiet -p MemoryMax=8G -p MemorySwapMax=0 -- \
  .cloudbim/mesh-venv/bin/python scripts/rebar-v5-stage-audit.py \
  --source backend/data/assets/95b6b41c5857d9eb3407b155/source.las \
  --tiles backend/data/assets/95b6b41c5857d9eb3407b155/tiles \
  --output backend/data/rebar-v5-validation/NEW-AUDIT-VERSION \
  --evidence .cloudbim/NEW-AUDIT/run

.cloudbim/mesh-venv/bin/python scripts/rebar-v5-stage-report.py \
  --evidence .cloudbim/NEW-AUDIT/run \
  --artifact backend/data/rebar-v5-validation/NEW-AUDIT-VERSION
```

## 证据与协作

- [原始测量与源码哈希](/home/hong/hong_project/cloudBIM-viewer/.cloudbim/stage-audit-20260908/run-v2/measurement.json)
- [报告数据与图像口径核验](/home/hong/hong_project/cloudBIM-viewer/.cloudbim/stage-audit-20260908/report/report-data.json)
- [最终标签/瓦片等价](/home/hong/hong_project/cloudBIM-viewer/.cloudbim/stage-audit-20260908/equivalence-v2.json)
- [浏览器核验](/home/hong/hong_project/cloudBIM-viewer/.cloudbim/stage-audit-20260908/browser-validation.json)
- [第一轮采集索引修复记录](/home/hong/hong_project/cloudBIM-viewer/.cloudbim/stage-audit-20260908/run/snapshot-correction.json)

- explorer / gpt-5.6-terra / medium：核对 UI 默认项、执行流程和采集位置；完成，已停止。
- verifier / gpt-5.6-terra / medium：发现并复核采集索引问题，核对计时口径；第二轮 808,771 个索引坐标精确核验通过，完成，已停止。最终运行、等价性和浏览器验证由 root 完成。
- 本轮只新增诊断/报告脚本与本文；未修改分类算法、原始点云或资产 latest，保留工作区原有修改。
