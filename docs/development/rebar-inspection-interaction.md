# 钢筋巡视交互与透明显示（2026-10-29）

适用范围：分析工作区第三步的逐钢筋巡视。沿用既有蓝白界面、偏差色标、
巡视顺序和坐标约定。此修改只影响交互和显示，不改变配准、实例归属、偏差计算或报告。

## 参考与取舍

- [Navisworks Selection Tools](https://help.autodesk.com/cloudhelp/2023/ENU/Navisworks/files/GUID-E84B196F-343D-4C97-9AAD-1099CD188BE6.htm)
  区分导航与选择，并提供细线、点的拾取半径。这里保留共用鼠标的习惯，在完整手势结束后判定选择。
- [Onshape Isolate and Transparency](https://www.onshape.com/en/resource-center/tech-tips/cad-assembly-visibility-isolate-transparency)
  通过透明度保留未选构件的空间上下文。这里同时弱化设计表面和扫描点云。
- [SOLIDWORKS Selection Through Transparency](https://help.solidworks.com/2024/English/SolidWorks/sldworks/c_Select_Through_Transparency.htm)
  为遮挡对象提供穿透选择。这里用 Alt + 单击沿当前射线切换重叠钢筋。

这些是交互参考；具体阈值和实现由本工作区的密集钢筋场景决定。

## 已定位的问题

1. `handleRebarInspectionPointerDown` 原来在按下时直接选择并启动相机动画，
   OrbitControls 尚未获得拖动轨迹就被打断。修复前的实际页面函数测试稳定失败。
2. 合并 C2M PLY 使用全局顶点区间绑定钢筋，网格本身没有 IFC ID 或 `instance` 属性。
   原拾取只解析对象元数据和点实例；命中表面后仍可能无法找出钢筋，只能偶然依赖附近点云。
3. 非当前钢筋只被改成灰色，仍写入不透明深度，因而挡住当前目标。

## 交互契约

| 操作 | 结果 |
| --- | --- |
| 左键按下 | 只记录手势；不选中、不移动相机、不阻止 OrbitControls |
| 移动超过 5 CSS px（触摸 10 px） | 本次手势永久作为导航，即使最后回到起点也不点选 |
| 同一指针在视口内松开，且未拖动 | 选中构件，保留当前相机位置、朝向和目标 |
| Alt + 单击 | 在鼠标下解析出的构件候选中按深度切换 |
| 上一根、下一根 | 沿已有空间顺序选择并定位 |
| 定位当前钢筋 | 显式执行当前钢筋的相机取景 |
| 滚轮、多指、右键、取消、失焦、失去捕获、Esc、切换模式 | 取消未完成的点选 |
| 可见裁剪手柄 | 优先获得拖动，不触发构件点选 |

按下时可以打断尚未完成的巡视取景动画，恢复相机控制。手动点选不自动飞回俯视。
已有 Y-up 相机、工程坐标映射及配准矩阵保持原约定。

## 拾取

`rebarInspectionInteraction.ts` 将手势和几何拾取独立出来：

- 精确命中设计/偏差表面优先；空命中时在 6 CSS px 容差窗口内作八方向补采样。
- 拾取时临时检查双面，随后还原材质，不改变实际绘制的正反面设置。
- 过滤隐藏祖先、隐藏对象、辅助显示层、相机近远裁剪范围与材质裁剪平面。
- 仅在没有有效网格候选时尝试扫描点；以实际点投影到屏幕的距离作最终判定，
  避免一个宽泛的世界坐标拾取半径选到光标外的前景点。
- 合并 PLY 的三角面只有在三个顶点都属于同一钢筋区间时才归属该钢筋。
  这条规则限于合并结果根节点，不能把 BIM 局部顶点编号当作全局区间。
- 元数据的多个候选字段逐项检查，非 IFC 的对象名称不再遮蔽其他有效字段。

## 显示与恢复

周边透明度默认 **80%**，范围 **0–95%**，步进 **5%**；本次页面内切换钢筋时保留设置。
滑块带可访问名称、百分比反馈和键盘焦点。未选中钢筋时不显示该滑块。

`rebarInspectionAppearance.ts` 管理显示材质和选中层：

- 非当前钢筋维持中性灰、半透明、`depthWrite=false`。
- 当前设计表面和所属扫描点使用原材质，维持原有偏差/实例颜色和深度写入。
- 合并网格和点云通过独立的选中索引绘制前景，复用位置、颜色和归属属性；
  不裁掉原始顶点流，不重排原始距离数组或实例编号。
- 缺测钢筋没有扫描前景，不补造观测点。
- 前景标记为辅助层，不能参与再次拾取、范围取景或偏差重新着色的源网格统计。
- 退出巡视、切换到非比较步骤、进入调试视图和释放场景时清理显示层并恢复原材质。

## 验证

自动回归运行实际页面函数与安装的 Three.js，不以源码字符串匹配代替行为断言：

```bash
node --experimental-strip-types scripts/test_rebar_inspection_interaction.mjs
node --experimental-strip-types scripts/test_rebar_inspection_camera.mjs
node --experimental-strip-types scripts/test_rebar_debug_scene.mjs
node --experimental-strip-types scripts/test_alignment_transform_state.mjs
node --experimental-strip-types scripts/test_alignment_gizmo.mjs
node --experimental-strip-types packages/alignment/src/alignmentLifecycle.regression.test.ts
npm run build
CLOUDBIM_INSPECTION_E2E=1 node scripts/test_rebar_inspection_browser.mjs
```

19 项交互/拾取/透明恢复测试、23 项巡视相机测试、16 项变换测试、25 项操纵器测试、
21 项生命周期/裁剪测试及真实调试场景回归通过。完整构建通过；保留既有大包体积提示。

浏览器验证使用托管本地栈，Lumos 项目 3、BIM 13、点云 14 的现有 90 根钢筋结果，
405,876 个网格顶点和 500,000 个预览点。Chromium WebGL / SwiftShader 下验证：

- 在实际钢筋上按下、拖动再回到起点：相机旋转，选中钢筋不变。
- 钢筋笼内部低角度单击：选择正确，当前相机矩阵不变。
- 两根重叠钢筋：Alt + 单击可以选择第二根。
- 键盘把透明度调到 85%：周边材质不透明度为 0.15，当前前景保持 1。
- 退出后材质和深度恢复；515 个原有模型/点云对象的世界矩阵保持一致。
- 前后位置、法向、实例、分类、距离和原始颜色数组的字节指纹一致。
- 1920×1080 与 2560×1440 截图通过检查，无页面横向溢出。
- 浏览器无运行异常、无业务写请求；未重新计算或保存配准、点云及历史结果。

证据位于 `.cloudbim/assessment-20261029-inspection/`：`browser-checks.json`、
`browser-run.log`、`inspection-low-angle-1920.png`、`inspection-low-angle-2560.png`、
`inspection-locate-2560.png` 和各测试/构建日志。耗时记录是该环境单次端到端观测，
不是跨设备性能基准。截图重建了类似的内部低视角，不声称恢复附件的精确相机姿态。

未将此验证扩展为 WebGPU、实体触屏设备或所有模型规模的验收。
