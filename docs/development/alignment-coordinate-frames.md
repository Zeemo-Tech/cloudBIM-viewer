# 配准页面的 IFC / GLB / 点云坐标约定

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
