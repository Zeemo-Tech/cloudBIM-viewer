# 配准交互补充修复 · 2026-09-13

- 右侧新增「操作手柄」开关，独立控制平移和旋转 Gizmo 的可见性与交互。不改变模型矩阵、不置脏配准、不禁用数值输入；在步骤切换、剖切及相机投影切换时保留用户偏好。
- 完成校准的重复提示来自 handleSaveAlignment 和 handleCalibrationComplete 各自调用一次 ElMessage.success。移除外层重复提示，沿用保存函数的单一成功/失败提示，并使用 savingCalibration 锁定提交；finally 保证失败后恢复按钮。
- 剖切箭头不再使用剖切框 maxDim 设置尺寸。按相机投影、深度、视口 CSS 高度计算世界比例，标称长度 48px、框面间隔 8px、拾取半径 9px。箭头仍朝各面的法线方向，保留三维透视方向的自然缩短。单元几何仅创建一次，更新位置和缩放时复用。
- EDL 入口与点大小、原始点云配色合并为左侧「点云显示」按钮；正交视图提示 EDL 暂停，切回透视保留设定。

## 验证

- npm run build：通过（仍有已有分块体积提示）。
- node --experimental-strip-types --test src/views/alignment/alignmentLifecycle.regression.test.ts：15/15 通过。
- 测试覆盖重复提交、单条提示、失败解锁、手柄独立于数值编辑、工作流/剖切门槛、透视/正交缩放、几何复用和拾取命中。
- 浏览器真实「完成校准」按钮使用受控接口成功响应测试：一次 POST 被拦截，等待时按钮 disabled=true，重复触发不新增 POST，成功消息恰好一条。没有写入真实配准数据；拦截器已撤销。
- 浏览器实际拖动 X 正向剖切手柄：框宽 4.445655m → 3.746756m；箭头 48px → 48px；切换正交后仍 48px，geometry UUID 不变。
- 手柄关闭后平移/旋转 helper 均隐藏、controller 均 disabled，数值输入保持 enabled，模型矩阵和位移数值不变；相机切换和跨步骤返回保持隐藏偏好。
- EDL 开关 true → false，恢复 true。正交时显示暂停说明。
- 2560×1440、1920×1080、1512×982、1440×900、1280×800、1280×720：无整页横向溢出，弹层均在视口内，小窗口可滚动至完成校准按钮。主截图保持原始 16:9。
- 运行时登录曾过期，通过仓库文档中的本地 demo 账号正常重新登录恢复。原有 PNTS 长度断言未作为本次变更处理。

## 截图

- [主视口控件 2560×1440](controls-2560x1440.png)
- [1920×1080](controls-1920x1080.png)
- [1512×982](controls-1512x982.png)
- [1440×900](controls-1440x900.png)
- [1280×800](controls-1280x800.png)
- [1280×720](controls-1280x720.png)
- [剖切箭头 2560×1440](clipping-fixed-arrows-2560x1440.png)

实现范围：BimPointcloudAlignView.vue、alignment/index.scss、alignmentLifecycle.regression.test.ts；保留任务前已有修改，dev-hong 未切换。
