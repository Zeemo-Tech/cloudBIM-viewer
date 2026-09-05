# CloudBIM C2M 功能与历史完整审计

> 审计日期：2026-09-05
>
> 审计范围：当前工作区、前端、Go 后端、mesh-service、数据库契约、测试、全部可达 Git 历史、本地分支、远端跟踪分支及远端 refs。
>
> 审计方式：只读源码与 Git 对象检查、运行态数据库/产物盘点，以及一个不写文件的合成几何复现。
>
> 注意：正文描述的是实施前的审计基线；其后完成情况以“实施结果”一节为准。

## 0. 实施结果（2026-09-05）

本文第 11 节定义的“最小且正确的下一版”已经完成并通过跨层验收：

- P0 结果可信度：结果保存输入 fingerprint，latest 返回 `fresh/stale` 与原因；配准、精调或 remesh 变化后，API 和前端都立即阻止旧 PLY/BIN 使用。
- P0 生命周期：compute/recolor 采用唯一临时文件、`fsync` 和原子发布；Go 事务切换引用后才回收旧产物，资产删除同步清关联记录；启动及每 6 小时清扫超过 1 小时且无 DB 引用的白名单产物。
- P0 并发与安全：同资产对操作串行，recolor 带不可变 `resultVersion` 做基线 CAS；下载也校验 revision 并禁用缓存；产物路径拒绝符号链接逃逸，容器与宿主用 setgid 目录和 `0660` 文件共享读写权限。
- P1 参数契约：配色色域 C、直方图视窗 H、容差 T 和桶数均有统一边界；mesh 返回 normalized visualization，Go 严格验证直方图桶数、边界、总数和 overflow；统计、直方图、PLY 与 `distances.bin` 统一使用 raw distance。
- P1/P2 前端：提供自动、全范围、±50/100/200 mm、H 跟随 C、区间外计数和即时本地预览；修复三分屏清屏；删除无效“确认应用”；支持鉴权后的 little-endian float32 distance 下载和 Shift 重心插值拾取。
- 配色契约：Python PLY、Three.js 动态着色和 CSS 色标统一为五色 stop 的 sRGB 插值，零值为绿色，色域外为 `#3a3a3a`。

验收包括 Go 普通/竞态测试与 vet、前端生产构建、mesh 容器内 22 项 C2M 单测、合成几何测试，以及真实开发数据的 latest → revision 下载 → recolor → 旧 revision 409 → 新 revision 下载链路。启动清扫已回收 10 个历史孤儿产物。

仍按本文建议独立推进、未在本轮冒险开放的内容是：reference profile、normal constraint、异步 job/取消恢复、超差 shader 筛选与统计导出。横向多后端部署还需数据库级 advisory lock/外键；极端大 LAS/PLY 还需明确资源预算。这些不属于本轮单实例 quick profile 的发布边界。

## 1. 结论先行

用户记忆中的“C2M 直方图 + 配色上下限调节”**从未作为 UI 提交到本仓库**。仓库从 C2M 第一版起就有直方图数据、范围参数类型和原始逐顶点距离，但页面始终只传 `voxelSize`。因此，这更像是参考项目的界面，或与后来加入的点云强度范围条混淆，而不是本仓库中被删除的完整 C2M 控件。

真正值得补回的能力不止一个，建议顺序如下：

1. **P0：先补结果溯源、失效机制和文件回收。** 当前配准矩阵或 remesh 改变后，旧 C2M 仍会作为 latest 返回；重复计算还会留下孤儿 PLY/BIN。若先做可调色 UI，会放大“看起来可用但结果已过期”的风险。
2. **P1：接通配色色域、直方图范围和容差。** `distances.bin`、`/c2m/recolor` 和统计函数都已存在，无需重跑最近邻距离。但容差变化必须同步重算 `withinToleranceRatio`，直方图范围变化必须重新分桶，不能只 recolor。
3. **P1：修复三分屏“清屏”回归，并删除或实现“确认应用”。** 当前清屏实际调用 reload；“确认应用”只改本地布尔值，却提示会用于后续标注。
4. **P2：接入逐点偏差拾取和超差筛选。** 源码明确为此保存逐顶点距离，这是当前最有价值的隐藏产品意图。
5. **P2：完成真正的 reference profile。** scan-point-to-triangle 几何核心已经存在，但完整 profile 明确返回 HTTP 501；当前不应先增加 profile 选择器。
6. **暂缓开放法向约束。** 参数链路存在，但实现和默认值契约有缺陷，尚无可复核 benchmark 结果，不能直接作为高级 UI 开关发布。

## 2. 已调查的历史范围

- Git 中共有 32 个可达 commit。
- 本地分支：`dev-hong`、`main`。
- 远端跟踪分支：`origin/main`、`origin/feat/chen`；`git ls-remote` 未发现其它远端分支或 tag。
- 检查了所有 commit tree、删除/重命名记录和 `refs/codex/turn-diffs/*`。后者只是当前会话工作树快照，不是仓库历史。
- 所有历史中均不存在 `c2mColormap.ts`，也不存在 C2M 直方图 UI、范围滑块 UI、recolor UI、偏差过滤 UI或统计导出 UI。

当前未提交工作区新增了：

- `src/components/preview/C2MHistogramLegend.vue`
- C2M `visualization` 元数据落库与返回
- 直方图 `overflowCount`
- 校准页和三分屏中的直方图/色标展示

这些是本轮最近接入的能力，不是历史上恢复出来的旧实现。

## 3. 参考项目交叉核对

算法注释里提到但 CloudBIM 从未拥有的 `c2mColormap.ts`，实际存在于另一套已提交工程：

- 前端：`/home/hong/hong_project/zhongjian/zhongjian`
- 后端：`/home/hong/hong_project/zhongjian/zhongjian-back`

这套参考实现完整接通过以下能力：

- `src/utils/c2mColormap.ts`：从原始逐顶点距离做浏览器端实时着色和直方图重分桶；
- `ScanBimComputePanel.vue:398-545`：合格界限、色温上限、桶数、直方图、色标和“确认应用”；
- `ScanBimComputePanel.vue:1053-1069`：计算时传递色域、直方图、容差、kNN 和法向参数；
- `ScanBimComputePanel.vue:1093-1119`：调用服务端 recolor 固化新 PLY；
- `BimPointcloudAlign/index.vue:4119-4213`：下载 `distances.bin`、挂载 geometry `distance` attribute，并即时更新顶点色；
- `src/utils/c2mPick.ts` 与 `BimPointcloudAlign/index.vue:6244-6255`：Shift 点击三角面后以 barycentric 权重插值逐点偏差；
- `internal/handler/c2m_handler.go:500-533`：鉴权后的 distances 下载；
- `internal/handler/c2m_handler.go:555-720`：Go recolor 代理和新 PLY 入库；
- 低 BBox IoU 警告、Min/Max/Mean/Std/P50/P90/P95/P99 展示、动态 remesh 参数 schema，以及打印/报告中的 C2M 直方图。

因此，用户对“以前见过直方图和范围调节”的记忆是准确的，但来源是参考工程，不是 CloudBIM 已提交历史。参考 UI 只有一个对称半宽 `+/-maxDistance`，并没有独立可调的负下限和正上限；双端范围条来自点云强度功能，不能直接解释为 C2M 的历史设计。

参考实现可作为产品原型，不应原样移植：

- 本地直方图与统计使用 raw distances，服务端 PLY 使用 Laplacian 平滑后的 distances，确认应用前后可能出现视觉跳变；
- “停止计算”只 abort 浏览器请求，无法取消已经进入 mesh-service 的任务；
- recolor 生成新文件并切换引用，但同样没有完整的旧产物回收和输入 stale 判定；
- signed P95/P99 会被正负偏差分布影响，CloudBIM 主摘要应优先使用 P95Abs、RMSE 和容差内比例；
- 参考实现开放了法向约束参数，但没有解决本文 7.5 节中的符号语义、缺省布尔值和 benchmark 证据问题。

**判断：** 借鉴它的 distances artifact、实时调色、recolor 和点选链路；重新设计结果版本、统一 raw/smoothed 语义、任务取消和文件生命周期。

## 4. 当前 C2M 链路

| 环节 | 当前行为 | 主要证据 |
|---|---|---|
| UI 发起计算 | 只提交 `profile: quick` 和 `voxelSize` | `src/views/alignment/BimPointcloudAlignView.vue:204-225` |
| 前端 API 类型 | 声明了色域、直方图、容差、kNN 和法向参数 | `src/api/backend-c2m.ts:54-68` |
| Go 代理 | 为缺省参数填固定值并全部转发 | `backend/main.go:2864-2927` |
| quick 算法 | BIM 网格顶点到降采样 scan 点的有符号最近邻距离 | `services/mesh-service/main.py:341-381` |
| 统计 | 原始逐顶点距离的 signed stats、absolute stats 和对称直方图 | `services/mesh-service/algorithms/c2m_distance.py:396-436` |
| 着色 | 距离先做 5 轮 Laplacian 平滑，再按固定五色发散色图写 PLY | `services/mesh-service/main.py:388-410` |
| 保存 | 每个 scan/BIM 对只保留一行 DB latest，同时保存 PLY/BIN 路径 | `backend/main.go:2962-2973` |
| 展示 | 前端下载已经着色的 PLY；浏览器没有读取原始距离 | `src/components/preview/UnifiedViewer3D.vue:1551-1604` |

默认值是：配色色域 `+/-0.10 m`、直方图 `+/-1.0 m`、50 桶、容差 `+/-0.05 m`、voxel `0.05 m`。当前真实数据的 latest 结果有 11,942 个网格顶点，绝大多数落在 `[-0.04, +0.04] m` 两桶内，说明 `+/-1 m / 50` 对厘米级观察确实过宽。

## 5. 已实现但没有接通的能力

### 5.1 计算参数从第一版起就存在，但 UI 从未使用

`f3f17fa9`（2026-09-02，首次加入 C2M）的 `src/api/backend-c2m.ts:27-40` 已经声明：

- `maxColormapDistance`
- `maxHistogramDistance`
- `histogramBins`
- `toleranceLimit`
- `knnK`
- `normalConstraintEnabled`
- `normalHalfSpaceOnly`
- `normalMaxAngleDeg`
- `normalFallbackMode`

同一版本的校准页只发送 `voxelSize`。扫描全部历史 Vue tree 后，没有任何页面实际传过上述参数。当前代码仍是这一状态，见 `src/views/alignment/BimPointcloudAlignView.vue:213-218`。

**判断：** 色域、直方图范围、桶数和容差值得接回；法向相关参数不应直接接 UI，原因见 6.5。

### 5.2 `/c2m/recolor` 从 mesh-service 首版就存在

`293e8d94`（2026-09-03）首次加入 mesh-service 时，同时提交了 `/c2m/recolor`。它读取已保存的 `distances.bin`，重新做平滑和配色，生成新 PLY，不重新计算最近邻距离。当前实现见 `services/mesh-service/main.py:437-510`。

它支持：

- `max_colormap_distance`
- `tolerance_limit`
- `smoothing_iterations`
- `smoothing_strength`

但 Go 后端至今只注册 compute、latest、colored-ply 三个 C2M 路由，见 `backend/main.go:3187-3189`。前端也没有 recolor API。

**判断：高价值，值得接回。** 但不能只转发端点，还必须原子更新 DB 中的 PLY 路径和可视化参数、删除被替换的旧 PLY，并在容差变化时更新容差内比例。

### 5.3 原始逐顶点距离已经保存，产品意图是动态着色与点选

计算服务将每个 remesh 顶点的 float32 原始有符号距离写到 `dist_*.bin`。源码注释明确写着“供前端动态着色与点选插值”，见 `services/mesh-service/main.py:388-394`；平滑函数注释又写“供批注点选精确插值”，见 `services/mesh-service/algorithms/c2m_distance.py:439-456`。

`DistancesPath` 从首次 C2M 数据表设计起就被保存，当前字段见 `backend/main.go:205-237`。但：

- latest 不返回 `distancesAvailable` 或下载地址；
- Go 没有安全的 distances 文件端点；
- Three.js 不加载该数组；
- 没有把它挂为 geometry attribute；
- 没有射线拾取后的三角形重心插值。

**判断：高价值，值得作为第二阶段补回。** 它可支持即时调色、鼠标点选真实偏差、缺陷标注和超差过滤。

### 5.4 统计和诊断数据多于 UI 展示

服务还计算并保存 `std`、`p50`、`p90`、`p99`，以及 scan 原始 BBox、变换后 BBox、mesh BBox。当前 UI 只展示 IoU 和少量主统计，见 `src/views/alignment/BimPointcloudAlignView.vue:5055-5062`。

**判断：** BBox 详细值和 signed percentiles 适合放在折叠诊断区，不应占据主摘要。工程判断更应突出 `meanAbs`、`RMSE`、`p95Abs`、容差内比例和超差数量。

### 5.5 高精度 reference 几何核心存在，但产品链路未实现

`15ebe6a5`（2026-09-05）加入了 scan point 到 mesh triangle 的有符号距离核心，具备：

- scan-to-triangle，而不是 quick 的 mesh-vertex-to-point；
- 分块查询；
- float64 外部输入输出；
- 大坐标 local-origin rebasing。

实现见 `services/mesh-service/algorithms/c2m_distance.py:279-346`，基础测试见 `services/mesh-service/test_c2m_reference_core.py:42-66`。但 `/c2m/compute` 对 `reference` 明确返回 501，见 `services/mesh-service/main.py:275-299`。

**判断：值得完成，但不是“接 UI”任务。** 还缺 LAS 全量/抽样策略、产物定义、scan 点着色或 mesh 投影方式、性能预算、统计采样语义和完整 E2E。完成前不要展示 reference 选择器。

## 6. 历史上真实存在后被删/替换的能力

### 6.1 独立 C2M 预览的清空按钮被删除

初版 `f3f17fa9:src/components/preview/C2MResultPreviewPanel.vue:285-297` 有右上角清空按钮，并调用真实 `clearResult()`。`022c37c8` 在模板中删除了该按钮，但保留方法供三分屏共享工具栏调用。

`cb0b8c48` 将独立渲染器替换为 `UnifiedViewer3D` 后，适配层把 `clearResult` 错误映射成 `reload`。当前：

- `src/components/preview/C2MResultPreviewPanel.vue:60-64`：`clearResult: () => viewerRef.value?.reload()`
- `src/views/preview/SplitPreviewView.vue:666-670`：共享“清屏”操作仍调用 `clearResult()`

因此，三分屏的 C2M 清屏现在实际上是重新加载。

**判断：应修复行为，不必恢复独立红色按钮。** 统一工具栏的清屏入口已经足够。

### 6.2 独立 C2M renderer 被统一 viewer 替代

`cb0b8c48` 删除了 C2M 组件内自建的 scene/camera/controls，改用 `UnifiedViewer3D`。旧组件本身也没有直方图、范围控制或 recolor，所以这次替换没有删除用户记忆中的那套 UI。

**判断：不应恢复独立 renderer。** 应继续在共享 viewer 上补领域能力。

## 7. 类型、注释、参数、数据与显示之间的断层

### 7.1 `c2mColormap.ts` 是从未兑现的注释，不是被删文件

算法两处声称与前端 `c2mColormap.ts` 保持一致，见 `services/mesh-service/algorithms/c2m_distance.py:32` 和 `:357`。但全部 32 个 commit、全部分支、远端 refs、删除记录和当前 Codex tree refs 中都没有这个文件。

同时模块头部仍写“零偏差为白色”，实际色图零点是绿色，见同文件 `:13-15` 与 `:32-39`。

**判断：** 这是参考项目遗留注释或未提交设计。应建立单一配色契约，至少由 API 返回 stops/颜色，避免 Python 和 CSS 分别硬编码。

### 7.2 `overflowCount` 长期只存在于类型

前端 `overflowCount?: number` 从 `f3f17fa9` 第一版就存在，但所有已提交算法版本只返回 `binEdges` 和 `counts`。当前未提交工作区才首次在 `compute_statistics()` 中实现它，见 `services/mesh-service/algorithms/c2m_distance.py:431-435`。

**判断：** 当前补实现是合理的，但应增加后端契约测试和前端组件测试，不能继续依赖可选字段掩盖断层。

### 7.3 原始统计、平滑颜色和 UI 容差不是同一份数据

- 直方图和所有统计来自原始距离：`services/mesh-service/main.py:375-381`。
- PLY 颜色来自平滑后的距离：同文件 `:396-409`。
- `withinToleranceRatio` 用请求的原始容差统计：`services/mesh-service/algorithms/c2m_distance.py:410-424`。
- 着色函数会把容差钳制到色域以内：同文件 `:365-366`。
- 当前 UI 又独立把显示容差钳制到色域：`src/components/preview/C2MHistogramLegend.vue:19-30`。

结果是：直方图柱、绿色容差区、PLY 表面颜色和“容差内比例”不保证逐点对应。若用户把容差设得大于色域，DB 还会保存原始容差，而着色和 UI 使用钳制值。

**判断：P1 必须统一。** API 应返回服务实际采用的 normalized 参数；UI 应明确“统计使用原始距离、颜色使用平滑距离”，或让用户选择原始/平滑显示。

### 7.4 结果没有输入版本，latest 可能已经过期

DBC2MResult 只以 scan/BIM 对为唯一键，未保存：

- 配准记录 ID、矩阵 hash 或 alignment 更新时间；
- remesh 参数/hash/完成时间；
- scan 源文件 hash/version；
- smoothing 参数；
- normal/kNN 参数；
- 完整 normalized 请求；
- 结果 `createdAt/updatedAt` 的 API 输出。

证据见 `backend/main.go:205-239` 和 `:2822-2861`。保存粗配准会覆盖 alignment 行但不使 C2M 失效，见 `backend/main.go:2367-2387`；fine alignment 应用后也只更新 alignment，见 `:2623-2637`；强制 remesh 同样不删除或标 stale 的 C2M。

前端保存新配准后也不会清空/刷新 `c2mResult`，见 `src/views/alignment/BimPointcloudAlignView.vue:3959-3975`。页面挂载时只按资产 ID 获取 latest，见 `:4437-4446`。

**判断：P0。** 这是结果可信度问题，比范围滑块更优先。

### 7.5 法向约束参数存在，但当前实现不能安全开放

问题一：有效候选始终要求 `cos_angle >= cos(maxAngle)`，见 `services/mesh-service/algorithms/c2m_distance.py:185-212`。在默认角度小于 90 度时，这天然排除了负 dot 候选，即便 `normal_half_space_only=false` 也一样。因此启用约束后，有效候选几乎只产生正号；背侧点会被筛掉或 fallback。

本次用一个朝 +Z 的三角面和上下两侧 scan 点做了只读复现：

```text
constraint=false, half-space=true  -> [ +0.1, -0.1, -0.1 ], negative=2
constraint=true,  half-space=true  -> [ +0.1, +1.005, +1.005 ], negative=0
constraint=true,  half-space=false -> [ +0.1, +1.005, +1.005 ], negative=0
```

问题二：Go 的 `bool` 无法区分“未传”和“显式 false”。前端若只传 `normalConstraintEnabled: true`，Go 仍会把未传的 `normalHalfSpaceOnly` 作为 false 发给服务，从而覆盖 mesh-service 的 true 默认。证据见 `backend/main.go:2657-2671`、`:2774-2788` 和 `services/mesh-service/main.py:257-262`。

问题三：benchmark 只有脚本内一句“建议默认 k=8、75 度”，没有提交运行结果或质量标签，见 `services/mesh-service/benchmark_c2m_normal_constraint.py:73-115`。

**判断：暂不接 UI。** 先定义筛选语义、用可空 bool 保留缺省值、补正负两侧单测和真实标注 benchmark。

### 7.6 参数验证不足，保存的是请求值而非实际值

Go 只对 `<=0` 设置默认值，没有合理上限和参数间约束，见 `backend/main.go:2899-2924`。mesh-service 的 Pydantic model 也没有 `Field` 范围验证，见 `services/mesh-service/main.py:245-262`。

风险包括：

- 极大 `histogramBins` 带来的资源消耗；
- `tolerance > colormap range` 时统计、颜色和 DB 元数据不一致；
- 无效 fallback 字符串静默按 nearest 执行，见 `services/mesh-service/algorithms/c2m_distance.py:244-260`；
- smoothing 参数未落库，结果不可复现。

**判断：** 接 UI 前必须补服务端 normalized 参数与边界校验。

### 7.7 快速模式的名称、方向与符号解释容易误导

产品名叫 C2M，但 quick 实际 metric 是 `mesh-vertices-to-scan-points`，见 `services/mesh-service/main.py:412-420`。这对 scan 缺失、遮挡、杂点和非均匀采样敏感，不能等同于标准 scan-to-triangle cloud-to-mesh。

符号说明也不一致：算法模块说负值是凹入、正值是凸出，见 `services/mesh-service/algorithms/c2m_distance.py:6-15`；旧 E2E 脚本却打印“负=内缩、正=外凸”，见 `services/mesh-service/test_c2m_e2e.py:127-135`。更根本的是，符号依赖 mesh winding/vertex normal，结果中没有法线可靠性诊断。

**判断：** UI 应显示 metric direction 和符号约定；对外使用“快速预估”是正确的。reference 完成前不要把它描述成正式验收距离。

### 7.8 “确认应用”是无实际效果的 UI

`confirmC2MApply()` 只把 `c2mApplied` 设为 true 并弹出“将用于后续标注查看”，见 `src/views/alignment/BimPointcloudAlignView.vue:287-290`。全仓库没有任何其它消费者，`c2mApplied` 只控制按钮文字，见 `:166` 和 `:5052`。

**判断：P1。** 在真正建立“结果版本 -> 标注会话”的持久关联前，应删除该按钮和误导文案；若要保留，则必须明确实现 downstream 状态。

### 7.9 历史 API 类型与返回形态不完全一致

- 后端在无效历史直方图时返回 `[]`，见 `backend/main.go:2852`；前端类型却声明为对象或 undefined，见 `src/api/backend-c2m.ts:48`。
- 后端对历史记录省略四项 absolute stats，见 `backend/main.go:2794-2812`；前端类型把它们声明为必填，见 `src/api/backend-c2m.ts:14-27`。当前页面通过格式化函数容忍 undefined，但类型没有表达事实。
- `coloredPlyAvailable` 只检查路径字符串非空，不检查文件存在，见 `backend/main.go:2860`。

**判断：** 应返回 `null`/omit，而不是类型外的 `[]`；历史字段应显式 optional；available 应基于可读取产物或返回 artifact 状态。

## 8. 生命周期、运行态和测试发现

### 8.1 重算和删除会留下数据垃圾

每次 compute 生成新的 UUID PLY/BIN，随后用同一 scan/BIM 行覆盖 DB，但没有删除旧路径，见 `services/mesh-service/main.py:388-410` 与 `backend/main.go:2962-2971`。recolor 每次也生成新 PLY，见 `services/mesh-service/main.py:496-507`。

删除资产时只删除 asset derivatives、asset 行和资产目录，不删除 alignment、C2M 行或全局 `c2m_results` 文件，见 `backend/main.go:1254-1270`。

运行态已经验证该问题：`backend/data/c2m_results` 中有 5 对 PLY/BIN，而数据库只有 1 条 C2M 记录，只引用最新一对；其余 4 对已经是孤儿文件。

**判断：P0。** 更新 DB 与文件替换应采用“生成新文件 -> 事务切换引用 -> 提交后删除旧文件”的流程，并增加周期性 orphan sweep。

### 8.2 同步长请求缺少任务状态和取消

前端 C2M 超时为 10 分钟，见 `src/api/backend-c2m.ts:70-75`；Go 到 mesh-service 的超时为 30 分钟，见 `backend/main.go:2927-2936`。mesh-service 用进程级单槽 semaphore 串行化 remesh、C2M、recolor 和 fine alignment，见 `services/mesh-service/main.py:66-101` 及各端点装饰器。

当前没有 C2M job ID、进度、取消、恢复或自动按 `Retry-After` 重试。客户端超时/断开后，服务计算可能继续并产生未落库文件。

**判断：P2。** 在大模型或 reference 上线前，应改成异步作业；quick 小模型可暂时保留同步路径。

### 8.3 测试覆盖的实际边界

现有自动测试覆盖：

- quick 基础距离行为和统计；
- absolute stats；
- histogram overflow（当前未提交新增）；
- reference 几何核心的分块与大坐标 rebasing；
- reference 501 契约；
- heavy-task gate；
- Go 的 profile、JSON mapping 和历史默认值。

明显缺失：

- recolor 成功路径与 DB 代理集成测试；
- 范围/容差 normalized 参数测试；
- normal constraint 的正负侧正确性；
- alignment/remesh 变化后的 stale/invalidation；
- 资产删除和重算后的文件清理；
- C2M Vue 组件测试与三分屏清屏行为；
- 从浏览器 compute 到 latest/PLY 的 E2E。

`test_c2m_e2e.py` 和 `test_c2m_e2e_v2.py` 使用硬编码的旧容器路径与真实数据，并主要通过打印和 `main()` 返回码运行，不是稳定的可移植 CI 测试。

## 9. 推荐恢复清单

### P0：结果可信度与生命周期

1. 为 C2M 结果保存 `alignmentHash/alignmentUpdatedAt`、`remeshHash/remeshFinishedAt`、scan artifact version、完整 normalized params 和算法版本。
2. latest 返回 `fresh/stale`、stale 原因和计算时间；配准保存、fine applied、force remesh 时主动标 stale。
3. 资产删除时清理相关 alignment、C2M DB 行和 PLY/BIN；重算/recolor 时回收旧产物。
4. 删除或真正实现“确认应用”；修复共享清屏调用 reload 的回归。
5. 统一参数校验和服务实际采用值，修复历史返回类型。

### P1：用户当前需要的范围调节

1. 增加 C2M 专用的**对称物理范围**控件，单位 m/mm，不直接复用只支持 0..1 的 `PointcloudColorRangeBar.vue`。
2. 将三个概念分开：
   - 配色色域 `+/-C`：只改变颜色映射；
   - 直方图视窗 `+/-H`：从 raw distances 重新分桶；
   - 工程容差 `+/-T`：改变色标节点和容差内/外统计，要求 `0 < T <= C`。
3. 提供“直方图跟随配色色域”开关，默认开启，仍允许诊断时分开。
4. 默认不再固定 `+/-1 m`。推荐首次结果使用 `ceilNice(max(p95Abs 或 p99Abs, tolerance) * 1.2)` 的对称稳健范围，并提供 `+/-50 mm`、`+/-100 mm`、`+/-200 mm`、自动、全范围预设。
5. 拖动期间本地预览色标，结束或 debounce 后调用后端；服务返回 normalized 参数后刷新 DB、PLY、统计和 histogram。
6. 显示区间外数量，并允许“一键扩到全范围”。

### P2：高价值分析能力

1. 暴露鉴权后的 distances artifact，将 raw distance 挂到 geometry attribute。
2. 鼠标点选 mesh 时用三角形 barycentric 权重插值并显示偏差、符号和容差状态。
3. 增加“只看超差 / 仅正偏差 / 仅负偏差 / 全部”模式；优先用 shader discard/透明度，不复制网格。
4. 导出当前统计 JSON/CSV 和当前 colored PLY；报告中记录输入版本、metric direction、色域、容差、平滑参数和算法版本。
5. 将详细 signed stats 与 BBox 诊断放入折叠面板。

### P3：算法深化

1. 完成 reference profile 的全链路和性能预算。
2. 修复 normal constraint 语义后再 benchmark，且默认仍保持关闭。
3. 若要做正式验收，增加 scan 清理/ROI、双向距离或覆盖率指标，避免 quick 单向最近邻掩盖缺失扫描区域。
4. 逐步改成异步 job，提供进度、取消与恢复。

## 10. 不建议恢复或直接开放的内容

- **不恢复独立 C2M renderer。** 共享 viewer 是更合理的方向。
- **不把 smoothing iterations/strength 放在主界面。** 可在高级诊断中提供“原始/平滑”和少量预设，完整数值留给专家设置。
- **不现在展示 reference profile 选择器。** 当前选择必然得到 501。
- **不现在展示 normal constraint 开关。** 当前算法与默认值契约有缺陷。
- **不把 std、signed P50/P90/P99 全部堆在主摘要。** 它们可用于诊断，但不如绝对误差和容差统计直观。
- **不直接复用点云强度范围条的实现。** 它限定在归一化 0..1，并在无真实数据时生成装饰性假直方图，见 `src/components/preview/PointcloudColorRangeBar.vue:41-55`；C2M 必须只展示真实物理数据。

## 11. 建议实施顺序

1. **结果版本化与 stale 判定**：先保证展示的是当前 alignment/remesh 对应的结果。
2. **后端 recolor/rebin 契约**：一个鉴权端点接收 `C/H/T`，从 DB 获取安全路径，不允许前端提交任意磁盘路径；返回完整更新后的 C2MResult。
3. **原子产物替换与清理**：DB 事务切换引用，提交后删除旧 PLY；容差/直方图从 BIN 重算。
4. **C2M 范围 UI**：自动范围、预设、手动输入、滑块、reset、overflow。
5. **前端动态 distance attribute**：点选、过滤和即时 shader 着色。
6. **reference 与异步 job**：作为独立算法项目推进。

最小且正确的下一版，不是只加一个 `+/-0.1 m` 输入框，而是同时完成：`stale/provenance + recolor/rebin + C/H/T 参数统一 + 旧文件回收 + UI 控件`。这样用户调整上下限时，颜色、直方图、容差比例和数据库 latest 才会始终指向同一个可复现结果。
