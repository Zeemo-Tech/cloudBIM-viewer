# 钢筋分割 V5 工作交接（2026-09-06 暂停）

用户主动要求今天停止，明天在新窗口继续。此文件记录未验收的开发状态，不是完成报告。不要继续上一轮的阈值试错；先重建少数失败实例的证据，再决定最小修复。

## 1. 任务与不可改变的合同

仓库：`/home/hong/hong_project/cloudBIM-viewer`。

用户已授权实施独立 `geometric-v5`，流程为：去噪 → 全量特征 → 最低台面 → 夹具 → 分层钢筋、遮挡关联和弯钩 → 独立斜腹杆 → 全局归属复核 → 交点后处理。

- 米制、Z 已校正，台面是最低大平面；腹杆为独立斜杆，在连接处不与上下层钢筋合并。
- 纯点云，不使用 BIM；原始文件不改，阶段 mask 与最终标签分离。
- 每个有效坐标记录保留原始 `source_index:uint64`；过滤非有限坐标不重新编号，重复 XYZ 也不合并记录。
- 场景类别固定：0 未知、1 台面、2 钢筋、3 噪声、4 夹具。实例 0 为未确定归属；候选实例用稀疏列表，不能强行指派。
- 所有原始有效点保存多尺度特征/有效性，噪声也保留记录。磁盘空间块和完整 halo；不得全量拼接原始坐标或构造全量邻接矩阵。
- 四类产物：features、labels、几何 result.json、诊断。合同 `rebar-analysis-v2`、`rebar-artifact-manifest-v2`、`rebar-raw-labels-v2`、`rebar-features-v1`、`rebar-visualization-v3`。
- 交点是独立产物，只读最终有限实测中心线，最大 1 mm 残差、默认至少 5° 无向夹角；不用推断段/无限延长线/钢筋半径放宽容差，不能改变点级标签或实例数量。
- 原始标签为准，Tiles 仅展示；发布完整校验后原子替换 latest，失败保留旧成功产物，不做旧产物迁移或自动清理。
- **V5 验收后才应成为默认算法。当前开发代码已经把多个默认入口设成 V5，这是尚未满足验收的风险；下次须明确处理，不能直接当作已完成默认切换。**

完整流程和参数说明见同目录 `rebar-v5.md`；验收命令与口径见 `rebar-v5-validation.md`。这两份文档仍是开发中说明。

## 2. 工作区、运行与保护要求

- 有大量本次开始前的未提交 C2M、remesh、V4 修改。**不要 reset/clean/revert 全部工作区，不要把全部 git diff 当作本次修改。**
- 开工基线在 `.cloudbim/v5-baseline/manifest.json`、`working-tree.patch`、`files/`；基线 HEAD `77a774b4a72df62c65f304352c182b6178f35860`。本轮未创建提交。
- 关键临时诊断已复制到 `.cloudbim/v5-handoff/`，含当前 V5 源码指纹。不要依赖 `/tmp` 文件明天仍存在。
- 暂停时已通过 `scripts/cloudbim-dev.sh stop` 停止前后端、PostgreSQL 和 mesh-service；status 已确认停止。真实 benchmark development-04 已主动中止。不要继续使用旧 session/PID。
- 集成栈只能用 `scripts/cloudbim-dev.sh start|status|logs|stop`，不得手动启动 npm dev、vite、go run、docker compose。固定前端 5173、API 8090。
- Python 必须使用 `.cloudbim/mesh-venv/bin/python`，3.11；不用系统 Python/Conda base。该环境 sqlite3 扩展有链接警告，比较工具不要依赖 SQLite。
- 有 `.codegraph/`，定位代码先用 CodeGraph，之后再 rg/读取。
- 本轮使用 3 个子代理：Sol/high 只读规划与复核、Terra/medium 后端几何、Terra/medium 前端及工具。若继续委派，遵循 AGENTS 和 multi-agent-router，禁止子代理再派子代理，隔离文件写入。

## 3. 已写入实现（不等于验收完成）

`services/mesh-service/algorithms/rebar_v5/`：

| 文件 | 现有实现 |
| --- | --- |
| contracts.py / __init__.py | V5 参数、描述符、独立入口，BLAS 限制为一线程避免小矩阵线程开销 |
| spatial.py | LAS 原始索引磁盘空间分块、halo 查询、过密核心细分、预算超限明确失败 |
| features.py | 保守局部去噪、分批多尺度 PCA、法向/切向有效性、特征输出 |
| scene.py | 最低台面、有限观测面域、夹具面片；最新增加方管平行侧面约束的顶底 companion 面候选 |
| bolts.py | 有限夹具邻近短圆柱和宽头部证据，实际表面 cells mask |
| rods.py | 分层俯视 Hough、局部圆截面物理中心拟合、端点关联、弯钩、条带侧视腹杆和 3D 兜底 |
| verification.py | 原始点支持复核、有限实测段、短末端支持；保留仅供身份关联的低独有支持候选 |
| ownership.py | 局部重叠、唯一端点关联、实测曲线保留、最终片段合并；**当前存在重要错误，见后文** |
| projection.py | 按圆柱表面残差做有限段候选竞争，分批 top2 |
| pipeline.py | 阶段调度、最多两轮释放重检、原始标签、边界特征更新、实例修剪、Tiles 转移、产物导出 |
| intersections.py | 独立有限实测段交点、残差、引用与去重 |

共享服务与后端：`rebar_poc.py`、`rebar_api.py`、`rebar_stream.py`、`rebar_tiles.py`、`rebar_base.py`、注册模块；Go `backend/rebar.go`、`rebar_provider.go`、`main.go`、测试。包含源文件描述符固定/变化检查、PLY/PCD 解码前资源检查、V2 特征/标签、原子发布、特征鉴权路由、缓存指纹、30 分钟预算。Go stage 目录创建与 Python immutable publish 冲突已修复。

前端：`src/api/backend-rebar.ts`、`RebarSegmentationPanel.vue`、`UnifiedViewer3D.vue`、`AssetPreviewView.vue`、`src/features/rebar-visualization/`。V5 场景图例无交点类别，实测实线/推断虚线，独立交点对象、开关、拾取、关联实例和残差显示、资源释放。V5 不显示误导性的 bootstrap 最大点数/voxel 参数。

最新还改了以下内容，**尚无最后一次完整回归覆盖所有这些修改**：

- fixture_mask 用世界 AABB 预筛选后再判断有限面 cells，避免逐面遍历所有点的 Python 元组查询。
- Tiles 精确匹配的原始点直接数组复制，只对非精确/争议点循环仲裁。
- stage_features 复用已保存的台面/夹具边界 PCA 更新，避免实例修剪再分类时重复计算。
- 描述符补充部分参数上限；review 图限制固定 ROI 坐标范围。
- scene.py companion 面与一个新测试（子代理报告 scene 10 项通过）。

## 4. 必须优先解决的已知错误

### A. 16 mm 近邻钢筋被错误合并（最高优先级）

Sol 只读复核确认：开发 top 场景 GT3/GT4 两根平行筋，轴 y≈-.550 / -.534、半径约 .004，最终都被放进 pred3。其 observedSegments 同时含两条完整平行轴，GT3/GT4 各 900 点。**这违反“不允许近邻筋错误合并”，不能被总体 IoU/召回掩盖。**

合并前普通实例 8、10 分别正确。低 unique 桥候选 17 含两根的 18+21 个 raw 点，但支持覆盖门槛对一根 .462、另一根 .538；目前证据不足以断言它直接造成合并。需要重建 ownership.py 三类 union 的确切路径（轴线重叠、association-only、endpoint），检查传递合并是否违反整个组的半径/横向间距/唯一性约束。

证据：`.cloudbim/v5-handoff/v5_topdiag05b.json`、`analyze_top_merge.py`。不要全局放宽角度、半径或横向距离。

### B. 同一钢筋的表面条带被当成独立实例

同一 top 场景两个额外实例：

- pred5：GT1 基准杆左尾，最终 41 点，rawSupportCount=42，约 `[-1.49086,-.79488,.02588] → [-1.19469,-.80651,.01958]`，r=.00939；与正确主 pred7 共享 27 raw 点。它也覆盖主模型遗漏左端，不能简单丢掉所有点。
- pred9：GT5 hook 直臂条带，最终 7 点，但 semanticSupportCount=61、rawSupportCount=98；约 `[-.0800,.3880,.03475] → [.3561,.41087,.03475]`，r=.00391；主 hook pred6 r=.00752，两者 raw tube 共享约 102 点。

建议方向（未实现）：按长实例的**局部 observedSegment**检查短候选 raw 支持的包含/支配关系，并将支持回到主轴复核；不能只拿弯钩整条 centerline 比较。verification 目前残差与候选顺序会影响独有支持判定，应检查顺序无关性。

### C. 合并元数据失真

ownership.py 将 merged rawSupportCount 取成员最大值，不是原始 source_index 支持并集。上述 1800 点两杆错误组却报告 900。即便先修几何，也要纠正支持计数，不能用错误分母做最终模型保留判断。semanticSupportCount 含候选归属，需与确定归属计数明确区分。

### D. 半密度方管 63 点最终被判为钢筋

开发 top、density=.5 场景 precision=.989149，低于 .99；63 个错误 steel 点来自方管顶底面 z=.06/.10、x≈[-1.80,-.80]、y≈[-.28,-.241]，归入两个假实例。

先前“只扩大面片连接距离”建议经验证无效且引发回退，相关尝试已撤销。最新 companion 面实现借已确认平行侧面提议顶底面，再按原始支持验证；子代理报告方管 raw mask 已覆盖 700/706=99.15%，钢筋误 mask=0，scene 10 项测试通过。**但全流程仍泄漏这 63 点**，说明现在主要问题在 pipeline 的释放/最终类别竞争。

重点检查 classify() 的 bar_conf 仅依赖局部线性度/方向，而 fixture_conf 与面片置信度/残差比较；方管的强轴向特征可能击败正确平面证据。应比较真实模型与点的证据、拟合质量和法向，不做“夹具永远优先”，不靠抬高一个分数凑验收。

## 5. 测试与结果边界

已观察通过：

- Python 完整 rebar 回归 **172 项**，日志 `.cloudbim/rebar-v5-validation/unit-tests-latest.log`。在最新 stage_features 缓存、描述符上限、companion 面变更之前，不能宣称是最终源码的全覆盖回归。
- Go `go test ./...` 通过（最近一次 cached）。
- `npm run build` 通过；可视化 Node 测试此前 9 项通过。
- 实际 Three.js 图层模块的 Chrome/WebGL 合成浏览器夹具通过：隐藏中心线仍显示/拾取交点、独立关闭、释放资源。证据 `.cloudbim/v5-ui-review/overlay-browser-result.json`、`overlay-centerlines-hidden-intersections-visible.png`。
- 这是模块合成 UI 测试，**不是已登录的完整资产页面验收**；真实页面无可用登录态，未创建测试账户或写入认证数据。

最近完整开发合成报告 `.cloudbim/rebar-v5-validation/development-suite-05.json`：

| 场景 | 未通过项 |
| --- | --- |
| parameter-top | IoU50 instance precision=.857142857；另只读诊断发现近邻错误合并，必须独立锁定 |
| parameter-full | 当时报告所有 gates 通过 |
| parameter-top-half-density | steel precision=.989149156，IoU50 instance precision=.866666667 |

这些报告早于最新 companion 面/若干性能改动；子代理后续单案例仍报告半密度泄漏未改善。不要把开发指标当现场准确率。

固定目标不变：基础 steel P≥.99/R≥.98，hook/web R≥.90，hook parent≥.95，IoU.5 instance P/R≥.90，台面/夹具误删 steel 各≤.01，近邻筋和连接处不能错误合并，交点解析测试全部正确且标签前后不变。

**留出 seed20261017 尚未运行。** 不要先用留出数据调参。开发 seed20260905；先冻结源代码和参数再执行留出。评估器曾修复“precision 排除非钢筋假阳性”和“instance matching 排除所有非钢筋 truth”的错误；当前负样本参与误检统计，只有 scene=steel 且 truthID=0 的实例归属不评分，语义仍评分。不要撤销这些修复。

## 6. 真实数据与性能（尚无成功全链路产物）

原始 LAS：`backend/data/assets/95b6b41c5857d9eb3407b155/source.las`，9,216,369 点，184327607 字节。
SHA256：`eb229b7c514e918c03534184eb56ceabdfd850c57b1b3503172bd8f290ebab52`。
同资产 `/tiles` 可复用；旧 V4 输出仅作对照，不是真值。

真实 performance 报告在 `backend/data/rebar-v5-validation/`：

- development-01：600k candidate 上限失败；实测空间体素有 808771，现上限改为1M，不改变体素分辨率或全量特征。
- development-02：BLAS 线程风暴导致超时，中止；现已单线程。
- development-03：bolt circle x0 infeasible，已修复并有测试。
- development-04：主动中止于 **1502.58 秒**，峰值约 **860 MiB**。未成功发布。旧代码阶段耗时：空间4.4s、去噪44.4s、全特征228.4s、台面3.9s、夹具71.7s、平面筋18.8s、腹杆24.9s、全局归属400.4s、raw verification14.4s；中断时最终 raw labels 仅约21个核心。栈位于 fixture_mask 的逐点面域判断。最新 AABB 优化尚未完成真实重测。

目标依然完整冷进程≤900s、峰值RSS≤2GiB，全量原始特征不降采样。benchmark 明确报告 OS page cache 未清空，源码变化使性能记录失效。

固定真实 ROI 在 `docs/research/rebar-v5-review-regions.json`；**尚未生成成功 V5 真实 ROI 图与完整人工复核记录**。区域名中的 hook/web 是待复核候选，不能声称已确认物理真值。

工具：`scripts/rebar-v5-benchmark.py`、`scripts/rebar-v5-review.py`；`scripts/rebar-v5-compare.py` 和 `scripts/test_rebar_v5_compare.py` 为暂停时子代理新增工具。比较 CLI 使用 chunk 外排 run + 堆归并 source_index，不用 SQLite，也不按最大索引分配数组。子代理报告 3 项小夹具测试通过，覆盖不同 chunk 布局、非连续索引、重复 XYZ、值篡改、缺点、checksum 篡改；但其最后命令用了 `python3`，**不符合项目环境约定，下一窗口须用项目 3.11 venv 重新验证**。没有真实两份 9M 产物对比，也没有外排磁盘/耗时实测。该子代理已停止，无遗留运行进程。

## 7. 建议新窗口执行顺序

1. 读 AGENTS、此交接、基线指纹和最近失败报告；先核对工作区，不重写整套 V5，不开真实重计算。
2. 使用已保存诊断重建 16mm pair 的确切 union 路径，添加“完整 pipeline 不得合并 GT3/GT4”回归；同时约束 association-only 传递合并和支持并集计数。
3. 独立处理局部表面条带重复实例、正确方管 mask 被最终竞争推翻的问题。每次改动对应物理证据与失败用例，避免连串未经验证的阈值调整。
4. 开发套件全部通过并检查近邻/腹杆独立性后，冻结源码与参数，运行留出集；失败如实记录。
5. 再做稳定源码的真实完整 benchmark；按阶段 profile 优化，不在性能运行中同时改算法。成功后固定 ROI 图与错误记录、改变读取块大小的产物对比。
6. 完成最终 Python、Go、前端构建/交互测试、原子发布/鉴权集成验证、文档和报告。满足验收后才完成默认切换，不能因已写了默认值就宣称验收成功。

常用命令：

```bash
# cwd: services/mesh-service
../../.cloudbim/mesh-venv/bin/python -m unittest discover -p 'test_rebar*.py'
../../.cloudbim/mesh-venv/bin/python rebar_validation.py --algorithm geometric-v5 --v5-suite --output ../../.cloudbim/rebar-v5-validation/development-next.json
# 冻结后才加 --include-holdout

# cwd: repository root，output 必须新目录
.cloudbim/mesh-venv/bin/python scripts/rebar-v5-benchmark.py --source backend/data/assets/95b6b41c5857d9eb3407b155/source.las --tiles backend/data/assets/95b6b41c5857d9eb3407b155/tiles --output backend/data/rebar-v5-validation/development-next
```

请明确区分“代码已写入”“单元测试通过”“合成验收通过”“真实复核/性能通过”，不要用前者代替后者。
