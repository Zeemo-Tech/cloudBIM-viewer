# 配准页面的 IFC / GLB / 点云坐标约定

## 实体与开洞的坐标校验（2026-10-29）

`bim_scene_geometry` 只把实体表面纳入 GLB 对应检查与后续场景分类。
`IfcFeatureElementSubtraction`（含 `IfcOpeningElement`）是扣除宿主几何的
布尔体积，不是实体；开洞效果已在宿主三角网格中体现，GLB 正常不包含独立
开洞节点。提取器在 IFC iterator 和逐构件处理两处排除这类体积，并通过
`excludedSubtractionProducts` / `excludedSubtractionGlobalIds` 留下诊断记录。
其他实体缺失、原点错误或超差仍拒绝计算；坐标映射、单位、完整配准逆矩阵及
10 微米阈值均不变。设计几何缓存契约升级为 `validated-scene-model-v2`。

YB-1 的 BIM 7 原始 IFC 中，开洞 `2jqcr8yK9IVTmHDkz0GE2r` 通过
`IfcRelVoidsElement` 归属楼板 `3dYhDaEm16Muyx5FFuUpwf`。GLB 含楼板和
70 根钢筋，无独立开洞节点。修复前报 1 匹配 / 1 缺失；修复后实体检查通过，
包围盒最大误差 1.5697e-7 m、采样顶点最大距离 1.9187e-7 m。
`test_bim_scene_geometry` 覆盖可三角化开洞的正确排除、真实实体缺失拒绝、
非对称毫米模型的完整逆变换、错误原点拒绝和缓存隔离。

## 数据坐标与显示坐标

配准页面以右手、Z 向上的工程坐标承载点云，Three.js 视口以 Y 向上显示。
`contentGroup → engineeringRoot → BIM pivot / pointcloud wrapper` 是实际层级。
`engineeringRoot` 使用 `B = Rx(-π/2)`，列向量映射为：

```text
B = [ 1  0  0  0 ]       (x, y, z) → (x, z, -y)
    [ 0  0  1  0 ]
    [ 0 -1  0  0 ]
    [ 0  0  0  1 ]
```

IFC 转换得到的 GLB 已是 Y-up。加载 GLB 时，将居中后的 `bimPivot`
初始化为 `B⁻¹ = Rx(+π/2)`，再经过公共 `engineeringRoot`。
因此原 GLB 的 Y 轴仍指向视口上方，点云的 Z 轴也指向视口上方。
该操作是旋转，行列式为 +1，单位长度不变，没有镜像或缩放。
不能直接给 GLB 再套一次 Z-up → Y-up，也不能修改其原始顶点后继续使用旧配准矩阵。

`createCenteredPivot` 保留 GLB 包围盒中心 `c`，子模型平移 `-c`；
默认 pivot 位置仍为零。这只是加载时的居中显示，不意味着两个文件已配准。
初始方向、编辑基准及“重置变换”均记录修正后的 quaternion。

## 保存及恢复

接口中的矩阵 `A` 保持为 **扫描原始坐标 → GLB 原始坐标**，不是扫描 → IFC。
API `modelMatrix` 是 Three.js 的列主序数组，文中的矩阵以数学行列展示。

```text
W_bim_raw = W_pivot · T(-c)
A = inverse(W_bim_raw) · W_scan_raw
W_pivot_restore = W_scan_raw · inverse(A) · T(c)
L_pivot_restore = inverse(W_parent) · W_pivot_restore
```

恢复时直接分解 `L_pivot_restore` 并替换 pivot 的位置及 quaternion，
不能在已恢复的姿态上再叠加 `B⁻¹`。已有配准记录因此保持原有语义。
共同的视口旋转及整体居中平移会在保存时抵消。

## 2026-10-21 试用数据验证

Lumos 项目中的 BIM 11 / 点云 12 来自“夹具加钢筋预埋.ifc”和
“registered_denoised.ply”的试用副本。原 IFC 单位为毫米，世界几何转换为米后是
Z-up；实际网页 GLB 与该几何之间的关系为 `B`，无额外原点平移。
验证了三个位置不对称的构件，而非只检查包围盒或拟合残差：

| IFC entity | GlobalId | 逐顶点对应最大距离（米） |
| --- | --- | --- |
| #38 | 1LrzhBKtb5tx9WBeLzkUa4 | 2.62e-9 |
| #526142 | 2OE5epc6D0w9QeY9_6D_id | 7.42e-9 |
| #969583 | 1L1UO$Fm537BEo6vwjvraY | 1.90e-9 |

点云 12 的 6,920,846 点 LAS 已应用“原始相机坐标 → IFC 原生世界坐标”
的自动刚性初配准，因此该试用对的网页矩阵为 `A = B`。
通过现有 `/alignments/bim` 接口保存为记录 4；写入前确认该试用对没有历史记录。
三个基准点对与页面保存姿态的接口约定一致，仅用于传输已知变换；
API 返回的近零点对 RMSE 不是实测配准精度。

WebGL 页面重新加载后，读取实际场景矩阵验证三个非对称坐标的 scan / GLB
世界位置一致，最大回差小于 6e-16 米，矩阵行列式为 +1。
这验证坐标映射与恢复，不代表柔性实例分割或现场精度已经验收。

本地证据位于 `.cloudbim/assessment-20261021/`：
`trial_provenance.json` 保留原始扫描 → IFC 的完整变换及来源索引；
`web_glb_validation.json` 保留实际 GLB 对应检查；
`web_alignment_axis_mapping.json` 保留网页矩阵、接口请求和回读。

回归测试：`scripts/test_alignment_transform_state.mjs` 执行实际页面函数，
覆盖初始轴向、非对称坐标、原始顶点及中心保留、任意已保存姿态的恢复、
保存往返以及方向重置。`matrix.test.ts` 验证矩阵代数约定。

## 巡检相机的竖直方向

巡检定位使用视口世界坐标包围盒，不改变模型及已保存配准。斜俯视的相机位置
相对目标方向为 `(0, cos(18°), sin(18°))`，`camera.up` 始终保留视口世界
`(0, 1, 0)`，即工程坐标的 `+Z`。将世界竖直方向投影到画面平面所得的
`screenUp` 只用于计算构件的画面高度，不能写回 `camera.up`。

OrbitControls 初始化时缓存绕转坐标基；若巡检把 `camera.up` 改成倾斜的
`screenUp`，后续鼠标绕转、平移和预设视图会使用不一致的竖直方向。
`scripts/test_rebar_inspection_camera.mjs` 执行真实页面函数和 OrbitControls，
覆盖透视/正交投影、连续前后切换、绕转后的工程 Z 屏幕方向、退出动画及手动定位。

## 巡检顺序（2026-10-29）

巡检在首次前后导航或手动点选时，从完整设计构件的世界坐标包围盒生成并锁定
顺序，不再沿用 IFC / 结果数组顺序。先按空间跨度从短到长分组（包围盒对角线，
用于视觉分组，不是钢筋下料展开长度），再按固定巡检视角逐排从左到右、从上到下。
同组跨度容差为 `max(20 mm, 最短跨度 × 5%)`，同排中心的画面高度容差为 20 mm；
同排内左右坐标相差不超过 20 mm 的构件按从上到下排列，防止叠层钢筋的微小
横向差异颠倒上下顺序。分组均以组首为固定锚点，避免两两容差比较导致非传递
排序或连续扩张。

画面右方向为视口 `+X`，上方向为 `(0, sin(18°), -cos(18°))`，与巡检相机
共用倾角。位置来自 `comparisonBarBounds` 的完整 `matrixWorld` 变换，单位为米；
不能用原始 IFC/GLB 轴或随鼠标绕转变化的相机坐标替代。缺测/待复核钢筋也按
设计几何排列；无有效几何的条目保留在末尾，以 IFC ID 稳定排序。

“仅异常”是完整巡检序列的子序列。切换筛选优先保留当前钢筋，被排除时移到
后续最近的合格条目（末端回到最后一项）；手动点选后也从该钢筋继续前后导航。
相机移动、容差调整和渐进加载不重排已开始的巡检；退出巡检或结果更换后重建。
报告原始数组、构件身份、模型矩阵和保存的配准数据不变。

`rebarInspectionOrder.test.ts` 验证打乱输入、同规格误差、固定视角的非对称坐标、
分组边界和缺失几何；`test_rebar_inspection_camera.mjs` 执行真实页面导航、筛选、
顺序锁定及相机动画。

本地 WebGL 验证使用 Lumos 项目 3 / BIM 13 / 点云 14 的既有 90 根钢筋，完整
走完 90 次前进、5 次后退和 8 次异常筛选导航，并验证取消筛选保留当前钢筋。
相邻巡检目标中心的平均世界坐标距离从 1.0789 m 降至 0.5202 m（该模型），
521 个场景节点的世界矩阵变化为零；无页面异常或业务写入。证据位于
`.cloudbim/assessment-20261029-inspection-order/browser-validation.json` 及同目录截图。
验收浏览器隔离了 Vite 热更新连接，避免共享工作区的其他构建重载该测试页面；
应用数据、真实模型和导航交互均使用运行中的本地开发栈。

## 顶视图、底视图和视图切换

立方体的“顶/底”对应相机位于视口 `+Y/-Y`，朝目标看。两者均保留
`camera.up=(0,1,0)`；OrbitControls 的极角安全限位使方向偏离极点约
`1e-6 rad`，避免视线与 up 平行。顶视中工程 `+X` 向画面右、`+Y` 向画面上；
底视中工程 `+X` 仍向右、`+Y` 向下，不交换轴、不镜像几何。

显式旋转 90° 只改变画面朝向。切换透视/正交时复制相机的 up，控制器始终
以世界 Y-up 初始化，再恢复画面 up、相机位置及目标，避免将画面旋转误作
新的工程竖直轴。控制器构造时会先针对原点更新，故须恢复原相机位置。
顶视、预设视图、整体定位、投影切换及立方体旋转会取消尚未结束的巡检动画，
防止旧动画覆盖用户刚选择的视角。上述路径由同一相机回归脚本验证。

## 配准手柄与数值编辑（2026-10-28）

工程坐标的 +X、+Y、+Z 分别映射为视口 +X、−Z、+Y。手柄使用世界坐标，
颜色按工程坐标统一为 X 红、Y 绿、Z 蓝；蓝色旋转环绕视口 +Y，即工程 +Z。
分析页的方向三轴也采用相同映射，其他页面的默认视口三轴保持原有语义。
轴上两个箭头允许双向移动；悬停提示与增量读数使用工程坐标。

侧栏位移是相对编辑基准的工程坐标位移，单位为米。设父节点完整矩阵为 P，
基准局部位置为 b，输入工程位移 d，则 `p_local = P⁻¹ (P b + B d)`。
反向读取采用 `d = B⁻¹ (P p_local − P b)`。不能再交换 target.position 的
Y/Z 分量，因为 target 已挂在 engineeringRoot 下。此公式同时保留父节点的
平移、旋转和尺度；非对称点验证使用额外旋转、平移及统一尺度的父节点。

设父节点世界旋转为 Qp、基准局部旋转为 Qb，输入的世界旋转增量为 D，
则 `Qlocal = inverse(Qp) · D · Qp · Qb`。侧栏 Z 旋转与蓝环共用世界 +Y
及右手正方向。方向数值的内部 orientationDegY 是这个世界 Y 分量，
界面名称为工程 Z；已保存的 pitch/roll 仍保留，不重写历史配准矩阵。

输入由 `transformHandleInteraction.ts` 统一分发，两个 Three.js 控制器保留
各自的拾取和变换数学，但不再独立监听同一 canvas。平移与旋转拾取重叠时，
箭头/平面优先；悬停和按下使用同一判定。一次拖拽固定一个控制器和 pointerId，
相机在整个拖拽期间保持原姿态。释放后才提交 dirty 状态；Esc、取消事件、
丢失捕获、窗口失焦、隐藏或卸载会恢复拖拽前的姿态。未完成的拖拽不能保存或
进入后续分析，取消不使已有分析结果失效。

回归入口：`scripts/test_alignment_gizmo.mjs`（真实 Three 控制器与 DOM 事件顺序）、
`scripts/test_alignment_transform_state.mjs`（数值、非对称坐标及保存往返）、
`scripts/test_alignment_gizmo_browser.mjs`（可选的本地真实 WebGL 页面）。
Leica 对照与验证边界见 [手柄交互说明](alignment-manipulator.md)。
