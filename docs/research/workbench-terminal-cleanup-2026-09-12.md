# 练武场接触端清理与验收容差

本轮按用户要求放宽几何验收：方向 15°，长度 `max(30 mm, 10%)`。位置 10 mm、直径 1.5 mm、所在层及拓扑要求保留。腹杆连接端点间隙单独保持 40 mm，避免长度容差影响拓扑。原 5° / `max(20 mm, 5%)` 验收结果并行保存到 `strictAcceptance`。放宽只影响最终验收；候选发现和实例归属仍采用原识别约束。

统一 Python 入口为 `design_evidence_contract.workbench_acceptance_policy`，页面、HTTP `acceptancePolicy` 和离线参数共用其校验。页面提供方向、长度绝对值和百分比输入；不按单根钢筋放宽。

## 接触端后处理

`terminalMode=off|fixture-peel` 独立于 `robustnessMode`，要求运行到第 06 步且有服务端设计快照。目标是普通筋、内部短筋与夹具相邻的端部，腹杆不参与。实际弯钩簇的源点受保护，同实例的直段仍可复核。

每端单独使用向内相邻的 80 mm 实测支撑段拟合固定设计半径圆柱；需要足够支持和较小残差。检查区限制为端部 40 mm 或全长 20% 的较小值。只有超出局部圆柱外表面容差且附近存在实测夹具点的点才剥离。真实圆形端面内部、独立邻杆交叉点和缺乏可靠局部拟合的区域保留。坐标、早期分类、Step 05 剔除结果均不修改，也不补点。

最初整根中段轴线方案在长筋轻微弯曲时误删了真实末端。独立多视图检查发现该问题后改为逐端局部拟合，并增加弯曲钢筋反例；该初版不是推荐检查结果。保留历史运行便于追溯，最终运行使用 `rebar-terminal-cleanup-v2-local-tip`。

源行对齐的 `terminal_removed`、`terminal_reason`、`terminal_previous_instance`、`terminal_previous_segment` 同时写入 NPY、LAS 和浏览器预览。清理后重算最终验收，并保存本轮清理前验收。普通预览有采样，因此另生成包含全部剥离点及邻近源点的末端局部预览；此次局部预览限制为 30 万点并优先保留全部 708 个剥离点，浏览器不再下采样。

## 可视化检查

1. 在当前练武场选择第 06 步。
2. 点击“末端局部细节（包含全部剥离点）”。
3. 在“结果对照”中切换“第六步结果”和“末端清理前（本轮）”。
4. 在“结果范围”中选择“仅末端剥离点”，可按原实例编号继续筛选。
5. 验收面板显示当前容差和原严格阈值的失败数；计算完成不等于验收通过。

局部视图的 `sourceIndicesUrl` 仍指向原 LAS 行号，只改变显示范围。前后切换只在浏览器恢复颜色及原归属用于对照，不修改最终 LAS/NPY。

## 运行与回退

算法在 `codex/workbench-robustness` 独立工作树。原 `dev-hong` 只接入开发管理器的 workbench-only 支持，正式 mesh、backend 和 frontend 服务没有重建。

原工作区 `.env` 的 `POINTCLOUD_WORKBENCH_CODE_ROOT=.cloudbim/worktrees/robustness` 让练武场使用该工作树代码，数据和结果目录保持原路径。以下命令均在原工作区执行：

```bash
scripts/cloudbim-dev.sh stop --workbench-only
scripts/cloudbim-dev.sh start --workbench-only
```

页面关闭末端清理即可恢复原算法路径。要回退运行代码，停止练武场、删除 `.env` 的 `POINTCLOUD_WORKBENCH_CODE_ROOT` 设置，再使用同一管理器启动练武场。保留全部历史结果目录。

## 验证

- 39 项 Python 算法/验收/端到端测试；12 项 HTTP/调试器测试。
- 4 组 JavaScript 检查，含实际 Three.js 前后显示、剥离点筛选和原实例过滤；实际服务的 manifest 加载验证。
- 全量 9,216,369 点以 16 线程、同源设计快照运行；逐行比较早期数组、最终归属、剥离追溯列及 LAS/NPY 一致性。
- `scripts/audit-workbench-terminals.py` 输出逐行审计；`scripts/plot-workbench-terminals.py` 输出逐实例 CSV 及局部多视图。
- 当前运行环境没有可用浏览器，未宣称完成真实 WebGL 手工交互检查；HTTP、实际页面预渲染加载、Three.js 筛选逻辑和源点多视图已验证。

最终运行标识和量化结果见同目录的 `workbench-terminal-cleanup-validation-2026-09-12.json`。几何与拓扑仍有未通过项，不能据数量达到 210 宣称整体验收通过；本轮后处理旨在减少可观察的接触端残留，不代表所有夹具残留都已清除。

最终运行：`20260912T065132-c62fdaaa`，源码行为提交 `450319c`。14 个实例剥离 708 点，210 个实例全部保留，68.295 秒（冻结基线的 1.670 倍）。当前容差下清理前后均为 51 项实例几何不合格、236 个拓扑冲突；原严格阈值下 169 项实例不合格。因此端部外观清理有作用，但没有据此宣称整体几何验收改善。

检查入口：<http://10.0.0.4:8766/?run=20260912T065132-c62fdaaa>。局部多视图和逐实例 CSV 位于独立工作树 `.cloudbim/terminal-review/verified-local-evidence/`。全局与局部两个实际 HTTP 预览均已通过页面预渲染校验；局部视图验证包含全部 708 个剥离点。
