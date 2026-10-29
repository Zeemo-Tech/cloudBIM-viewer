# 三维视口导航与退化恢复

## 问题与修复

Three.js r173 的默认 OrbitControls 使用相机到 target 的距离计算透视平移量，
并以乘法缩短同一距离完成放大。默认 `minDistance=0`。
在分析页和统一预览页的真实初始化代码中，连续 1000 次滚轮放大后，轨道半径
降到约 `5e-14`，100 像素平移仅改变 `3.55e-15` 个世界单位，反向滚轮也几乎不动。
此时 `enabled=true`；这是导航尺度退化，不是渲染进程死锁。

另一个独立问题是正交相机的 Fit / Home 只更新机位和视锥，没有重置 `zoom`。
浏览器重现中，点击重置后仍保留约 40 倍放大。

`NavigationOrbitControls` 是安装版本 OrbitControls 的薄扩展：

- 透视轨道保留工作距离，取近裁面距离的两倍、位置浮点精度下限和 `0.0001` 中的最大值。
- 对已重合的相机和 target，沿当前视线前移 target，保持相机当前位置和观察方向。
- 接近安全距离后继续向内滚轮时，相机和 target 同步前移，避免卡在旧观察中心。
- 使用屏幕平移和鼠标位置缩放；正交缩放上下限由视锥跨度、裁面与工作距离计算。
- 不覆盖调用方明确设置的距离限制；Fit 改变近裁面后重新计算安全界限。
- 鼠标扩展监听在冒泡阶段，保持变换手柄、测量工具的捕获阶段优先权；禁用控制器和销毁后不移动相机。
- 正交 Fit 和 Home / 预设全景重新设 `zoom=1`。普通 resize 不重置用户缩放。

CloudBIM 的分析页、统一 BIM / 点云 / C2M 预览及操作员视口使用此控制器。
隧道前端的共享 `projectionCameras.createOrbitControls` 和去噪实验视口同步使用同一实现。
两个仓库目前没有共享发布包，修订时同时运行两边的导航回归。

## 成熟软件参考

[Blender 导航首选项](https://docs.blender.org/manual/en/5.0/editors/preferences/navigation.html)
提供 Auto Depth、Zoom to Mouse Position 和 Dolly；其
[视口故障说明](https://docs.blender.org/manual/en/latest/troubleshooting/3d_view.html)
说明接近观察中心会导致缩放停住，并列出 Dolly、重新定中心和框选等恢复办法。
[Navisworks Center](https://help.autodesk.com/cloudhelp/2023/ENU/Navisworks-Freedom/files/GUID-2D3B5D97-CB04-4039-8E47-DB80EA8995C1.htm)
将指定点作为缩放焦点和旋转中心。

本次实现鼠标位置缩放、安全距离、近中心前移和可靠全景复位。
没有新增自动表面深度拾取或自由漫游模式；稀疏点云上的表面拾取需要另行定义选择规则。

## 坐标与验证边界

仅改变相机和观察中心，沿现有世界视线计算。工程 Z-up → 视口 Y-up 的完整换基、
单位、右手系和已保存配准矩阵沿用现有契约。以非对称位置 `(100,20,130)` /
target `(100,-20,30)` 检验退化恢复方向；既有俯视、屏幕旋转和投影切换回归继续运行。

```sh
node --experimental-strip-types --test scripts/test_orbit_navigation.mjs scripts/test_rebar_inspection_camera.mjs scripts/test_operator_camera_motion.mjs
CLOUDBIM_NAVIGATION_E2E=1 node scripts/test_orbit_navigation_browser.mjs
```

浏览器脚本使用隔离 Chromium：CloudBIM 打开已保存 BIM 13 / scan 14 的真实分析页，
禁止应用写请求；隧道使用本地开发服务器实际共享控制器和 WebGL 几何夹具，
不冒充历史隧道任务的端到端验证。证据输出到忽略目录
`.cloudbim/assessment-20261029-navigation/`，检查鼠标拖动、反向滚轮、正交复位、
模型世界矩阵不变及浏览器错误。

本地验收：CloudBIM 真实分析页连续 1000 次放大后，100 像素平移在透视/正交下分别产生约 0.00850 / 0.00912 m 位移；反向滚轮和正交 Home 通过。512 个已有模型/点云世界矩阵逐项未变，浏览器错误为零。隧道 WebGL 夹具也通过连续缩放后的鼠标平移与退出检查。
