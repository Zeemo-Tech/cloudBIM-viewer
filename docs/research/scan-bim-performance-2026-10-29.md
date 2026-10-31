# Scan-vs-BIM 默认计算加速（2026-10-29）

同一份去噪结果、分析网格、保存配准和默认参数下，`compute_instance_comparison` 的本机直接调用中位耗时：scan14/BIM13 从 **9.208 s 降至 4.726 s（1.95 倍）**，scan5/BIM7 从 **11.680 s 降至 5.442 s（2.15 倍）**。完整距离向量、未知掩码、网格及各钢筋统计、测量、检查报告逐项相同；仅排除计时字段。

证据：[全部样本、校验和环境](assets/scan-bim-performance-2026-10-29-c2m/summary.json)、[回归日志](assets/scan-bim-performance-2026-10-29-c2m/regressions-final.log)。这是独立 Python 计算层的测量，未包含 HTTP、产物导出和浏览器加载；应用集成与页面自动计算由主任务单独验证。

## 固定输入与计时

| 输入 | 去噪版本 | 分析网格版本 | 钢筋 / 顶点 / 已知顶点 |
| --- | --- | --- | --- |
| scan14 / BIM13 | `903d8138e664cb81a2fe9aa0` | `am-0d2e5afa20f07d83d5c9e645a6278acc` | 90 / 405,876 / 299,090 |
| scan5 / BIM7 | `44f70eab06324411c835d262` | `am-e38ab53d7057cc9df8f0d0baa8f77f24` | 70 / 402,812 / 300,177 |

使用数据库当前保存的 4×4 配准矩阵，沿用米制、列主序输入解码和原完整变换。没有修改轴向、坐标映射、符号、搜索范围、法向约束或算法版本。默认参数为不降采样、voxel 0.002 m、同侧法向约束开启、夹角 30°、K=32、最大搜索距离 0.2 m、公差 0.01 m、直方图 ±0.03 m / 60 bins。完整路径和矩阵保存在 [scan14 请求](assets/scan-bim-performance-2026-10-29-c2m/scan14-bim13-request.json)及 [scan5 请求](assets/scan-bim-performance-2026-10-29-c2m/scan5-bim7-request.json)。

| 输入 | 原实现 wall 样本 / s | 最终 4 线程 wall 样本 / s | 中位耗时降低 |
| --- | --- | --- | --- |
| scan14 / BIM13 | 9.257, 9.159 | 5.020, 4.508, 4.823, 4.629 | 48.67% |
| scan5 / BIM7 | 11.713, 11.647 | 5.446, 5.437 | 53.41% |

每次在新的项目 Python 进程中导入代码后计时；所有样本顺序运行，不同时运行基准或测试。测量区间覆盖完整函数，包括读入、所有计算、Open3D 合并和最终统计。系统文件缓存未清空，因此这些数字是本机缓存状态下的比较，不代表冷盘或端到端服务耗时。另一次 9.846 s 的原实现 cProfile 运行只用于定位，不计入上表。

环境：项目 `.cloudbim/mesh-venv`，Python 3.11.15、NumPy 1.26.4、SciPy 1.13.1、Open3D 0.18.0、trimesh 4.6.8，16 个逻辑 CPU。原实现来自提交 `b32b61a2d7700314cce993223a2eac288fe9935b`，源文件 SHA256 `743e432f065d5e52212b15c863592713c37b31c73290570a583b3e7e4859f741`。

## 瓶颈与实现

[原实现 profile](assets/scan-bim-performance-2026-10-29-c2m/baseline-14-1.profile.txt) 的累积时间显示：同侧匹配约 4.455 s，控制单元投影与支持裁剪约 1.801 s，实体网格修复/法向约 1.635 s，横截面测量约 1.022 s；181 次 `np.isin` 约 0.250 s。主要瓶颈是各钢筋顺序执行的几何工作。

实现包含三项改动：

1. 对实例 ID 做一次稳定分组，并复用原始点数。稀疏 uint32 ID 不分配以最大 ID 为长度的计数数组；每个实例内部仍按源点行序取点。
2. 各钢筋独立加载网格、调用原控制证据准备函数、测量和查询；线程池按钢筋目录顺序收取结果，由主线程统一确定顶点区间并合并报告。
3. 默认使用 4 个线程，可通过 `REBAR_COMPARISON_WORKERS` 设置；实际数量限制为 1–8，并受 CPU 数及钢筋数限制。多钢筋并发时，普通最近点分支的内部 KD 查询固定 1 个 worker，避免嵌套 `workers=-1`。

同侧匹配、控制网和横截面算法函数均保持原样。控制证据依据原 matched 实例准备，即使某根钢筋因复核状态未进入距离计算，检查报告仍保留它已有的可信观测。

仅做分组和计数复用的 scan14 单线程样本为 8.822 s。加入每根钢筋的控制准备后，最终实现 1 / 2 线程样本为 9.457 / 5.821 s；该准备方式的收益来自并发。最终 8 线程的 scan14 样本为 4.864 / 5.016 / 4.739 s，中位数 4.864 s；scan5 为 5.785 s。4 线程在这两份输入上表现更好。

独立基准进程峰值 RSS 包括导入和结果保存：scan14 的 4 线程约 1.27–1.36 GiB，8 线程约 1.64–1.71 GiB；scan5 的 4 线程约 1.72–1.76 GiB，8 线程约 3.35 GiB。因此默认保留 4 个线程。并发阶段的 `meshLoad` / `distanceQuery` / `measurements` 是各钢筋耗时之和，会互相重叠；请求 wall 应看 `total`、`barComparison` 或基准 `wallSeconds`。

## 一致性、保护与复现

基准工具对完整 float32 距离向量执行严格逐元素比较并单独检查 NaN 掩码；对顶点、三角面和顶点法向执行严格比较；对其余全部结果字典执行严格比较，仅递归排除 `timings` / `timingSeconds`。所有记录的对照均通过，距离最大绝对差为 **0**。因此同时覆盖顶点区间、实例顺序、原始/降采样点数、复核与未归属计数、各钢筋统计、横截面测量、检查几何和间距报告。

41 项相关 Python 回归通过，包含新增的稀疏 ID / 源行稳定性、多线程与单线程的逐项比较、非对称刚体变换、降采样、多 tile、复核/缺测与检查证据保留，以及已有比较、控制网、扫描表面、测量和工作台测试。

隔离运行只把输出写入 `.cloudbim/performance-scan-bim-20261029-c2m/`，没有启动或重启服务，没有写入业务 API 或数据库。所有配对运行的请求、LAS 和实例映射 SHA256 相同；[运行前](assets/scan-bim-performance-2026-10-29-c2m/business-inputs.json)与[运行后](assets/scan-bim-performance-2026-10-29-c2m/business-inputs-after.json)的保存配准、资产和 derivative 指针相同。大型完整结果和数组保留在该隔离目录；紧凑证据随本文保存。源数据和历史结果未覆盖。

```bash
# 仓库根目录，重放最终默认实现并对照已保存的原实现结果。
.cloudbim/mesh-venv/bin/python scripts/benchmark_rebar_comparison.py \
  docs/research/assets/scan-bim-performance-2026-10-29-c2m/scan14-bim13-request.json \
  .cloudbim/performance-scan-bim-20261029-c2m/replay-default-14 \
  --compare .cloudbim/performance-scan-bim-20261029-c2m/baseline-14-1

# 也可使用 --module-source 指向从上述原提交导出的 rebar_comparison.py。
```

```bash
# services/mesh-service 目录
../../.cloudbim/mesh-venv/bin/python -m unittest \
  test_rebar_comparison_performance.py test_rebar_comparison.py \
  test_rebar_control_comparison.py test_rebar_scan_surface.py \
  test_rebar_metrics.py test_rebar_workbench.py
```
