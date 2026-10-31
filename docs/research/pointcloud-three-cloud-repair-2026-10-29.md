# 钢筋分类回归修复与三份点云验证

2026-10-29，`hong-pc`，`dev-hong`。本次接续 [YB-1 回归审查](pointcloud-yb1-regression-audit-2026-10-29.md)，已修改算法、更新本地服务，并通过业务接口重跑三份保存的数据。当前额外柔性轴线版本为 `bim-flexible-axis-v9`。

## 修复结果

| 数据 / Scan–BIM | 原源点数 | 修复前钢筋点 | 修复后钢筋点 | 修复前完整计算 | 修复后完整计算 |
|---|---:|---:|---:|---:|---:|
| YB-1 / 5–7 | 3,900,373 | 1,818,971 | 1,836,753 | 73.15 s | 23.12 s |
| Lumos 单层 / 10–9 | 1,841,337 | 578,624 | 578,624 | 9.14 s | 7.26 s |
| 夹具双层 / 14–13 | 4,038,141 | 886,617 | 887,740 | 49.30 s | 43.98 s |

时间取对应 manifest 的 `elapsedSeconds`，包含读取、分类和导出。YB-1 两次业务计算为 23.36 / 23.12 s，HTTP 往返为 23.98 / 23.73 s。双层首次重建模型缓存为 54.47 s，缓存命中后为 43.98 s，HTTP 分别为 55.72 / 45.25 s。单层 HTTP 为 8.00 s。没有把只计分类函数的计时混入此表。

YB-1 与第一代 v24 的全量源行比较：原钢筋点丢失 **0**、原钢筋实例变化 **0**，额外增加 6 点；退化版本丢失的 **49,044** 个历史钢筋点全部恢复。与直接修复前的 v8 比较，有 49,048 个非钢筋点恢复为钢筋，同时有 31,266 个 v8 新认定的钢筋点退回非钢筋，净增加 17,782 点。两种比较基线不可混淆。

单层的导出钢筋记录、所有原始属性和实例归属与历史结果全量一致；旧预览的 500,000 个已核验源行的类别和实例也全部一致。双层与直接基线相比，新增钢筋点 4,641、转出钢筋点 3,518，保留钢筋中实例变化 28；仍执行复杂场景的弯曲恢复。

## 改动及接受边界

1. 在重复截面追踪前审核已有模型，最多检查 4,096 个实际源点。依据表面距离、法向、24 个轴向分箱、覆盖长度、设计偏移、邻筋身份及附近真实夹具/台面点，决定是否需要再次求解。快路径要求至少 21 个有支持的分箱、85% 轴向跨度；附近有排除表面时采用更严格的支持比例。缺失、局部、偏移过大或身份冲突的模型仍进入原求解器。
2. 模型替换使用旧源行的独立区域检查。对于通过置信检查的既有模型，新模型若丢失任一有支持分箱的大多数观测，则整体拒绝该次替换，保留旧模型和旧归属。不会把旧模型的点并入已改变的新模型。低置信度旧模型仍可被修正，已有相邻钢筋和旧轴线释放测试覆盖该路径。
3. 修复纯钢筋 IFC 没有场景夹具时的兼容问题。明确记录 `not-applicable-no-scene-products`，走控制网分类；实际存在场景产品但全部解析失败，仍会拒绝。缺失 GLB 对象、错误坐标和非法变换的校验继续保留。
4. 所有分类路径都输出完整 `source-labels.npz`，保证第 i 行对应输入 LAS 第 i 条记录，便于全源核验和出图。

YB-1 的 54 个长筋候选中，51 个通过已有模型审核，2 个额外追踪只得到片段，1 个新拟合因损害既有区域被拒绝。其额外长筋阶段由审查回放中的 41.60 s 降至业务运行约 2.45 s。双层仍有 40 根执行并接受额外柔性拟合；本次没有关停复杂场景恢复。

## 验证

- 新增 5 项已有模型修订测试，覆盖尾段丢失、避免重复求解、刚体旋转、邻近夹具的更严格门槛、错误平行旧轨迹可以释放。
- 新增 2 项纯钢筋场景测试，覆盖合法空场景和真实解析失败的区别；导出测试增加全源标签与源行一致性检查。
- `test_bim_body_revision`、`test_bim_body_ownership`、`test_bim_bent_bars`、`test_bim_bent_performance`、`test_bim_guided_classification`、`test_pointcloud_denoise`、`test_bim_scene_geometry` 已通过。包含并行与顺序所有权等价、旧模型点撤销、表面约束、曲线/端面顺序和坐标校验。
- 每份数据通过真实 `/alignments/bim/denoise` 和 `/alignments/bim/denoise/latest`，均返回可用的新结果，`fresh=true`。YB-1 和双层各重复两次，全源 `classes / instances / sceneInstances` 完全相同；三份业务结果也与隔离回放逐源行一致。
- 三份导出 LAS 的每条保留源记录、每个原始维度、实例 ID 都与输入筛选结果一致。预览的源行标签、实例、float32 坐标全部核对。
- 28 个输入及历史文件的 SHA256 未变；资产关联、保存配准矩阵原字符串未变。新结果分别保存到独立版本目录，业务 latest 指向修复结果。

本次证明了上述回归恢复、确定性、数据保真和实测耗时。三份数据没有独立人工逐点真值，所以钢筋数量、模型支持率和图像不等于分类准确率的独立评估。

## 图片和数据追溯

[三份手机图册，共 21 张](../development/assets/three-cloud-denoise-repair-2026-10-29/index.html)：YB-1 6 张、Lumos 单层 9 张、双层 6 张。含原始范围整体、斜视、物理实例、相同源行前后对照和两处局部。单层另有试件范围对照及两处全源细节。

整体图按源行等距抽样，局部图绘制裁剪范围内全部共同标签行；旧单层结果仅有预览标签的限制已在图上注明。新单层另绘完整源行局部。未知点保持灰色，未被隐藏或改成噪点。所有图使用原始 LAS 坐标、等比例正交投影，无点插值或坐标改动；原始配准只记录，未在无 BIM 叠加的点云图上重复应用。每幅图的相机、裁剪、源行、哈希和结果版本均有 provenance。

证据：[逐源行及导出核验](../development/assets/three-cloud-denoise-repair-2026-10-29/source-row-proof.json)、[接受结果](../development/assets/three-cloud-denoise-repair-2026-10-29/acceptance.json)、[业务计时](../development/assets/three-cloud-denoise-repair-2026-10-29/business-runs.json)、[数据/版本清单](../development/assets/three-cloud-denoise-repair-2026-10-29/datasets.json)。

复核命令（项目根目录）：

```bash
.cloudbim/mesh-venv/bin/python scripts/qa/verify_denoise_source_rows.py --manifest docs/development/assets/three-cloud-denoise-repair-2026-10-29/datasets.json --output /tmp/three-cloud-source-proof.json
.cloudbim/mesh-venv/bin/python scripts/qa/render_denoise_comparison.py --manifest docs/development/assets/three-cloud-denoise-repair-2026-10-29/datasets.json --output /tmp/three-cloud-figures
```

最终业务版本：YB-1 `44f70eab06324411c835d262`；单层 `b3983151f852919c73bdc8c5`；双层 `64f34a48d494bb3c7c848ca5`。
