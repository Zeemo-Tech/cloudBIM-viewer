# 隧道与 CloudBIM 查看器交互对齐验收

日期：2026-10-30。CloudBIM 分支 `dev-hong`；隧道前端分支 `dev/hong`。

## 交付范围

CloudBIM 配准页、BIM 预览、点云预览共用新的 `SectionBoxController`。
剖切框的面、短箭头、描边、角点和旋转算法来自隧道前端
`src/views/analysis/components/clipBoxGizmo.ts`、`clipBoxRotation.ts`、
`orientedClipVolume.ts`。交互包括：

- 拖面或短箭头调整六面的范围，拖边旋转；正对面的视角可拖角点旋转。
- Shift 拾取背面；透视与正交投影均可使用。
- Esc、指针取消、捕获丢失、窗口失焦、页面隐藏、关闭剖切与卸载均回退本次拖动。
- 模型分块继续加载时保留用户的剖切范围；“重新贴合模型”显式重置。
- 配准手柄在剖切期间隐藏，相机在剖切拖动期间锁定；被裁掉的几何不参与拾取。

工具栏使用隧道的矢量投影图标与移动图标、18 px 图标尺寸、按钮间距、
选中色与边框。剖切框沿用浅青色描边、悬停强调及深色提示框。
点云预览中的提示框按显示工具区的实际高度放置，避免遮住视角和全屏按钮。

修复了点云预览向组件传入布尔值而非 `{ enabled }` 的接线错误。
浏览器测试还发现并修复了“工具栏按钮失焦取消首次剖切拖动”的问题：
只有窗口本身失焦才取消拖动。预览卸载时在取消回调之后停止渲染循环。

隧道前端迁入 CloudBIM 的真实 Three `TransformControls` 和单指针仲裁，
使用独立代理适配原有枢轴、方向轴拾取和数值变换接口。箭头/平面负责移动，
竖向轴圆环负责旋转；Ctrl + 滚轮调整大小，Esc 完整撤销。
拖动期间禁止保存，完成后才提交数值姿态。中心线编辑仍使用原有专用控件。
详见相邻隧道仓库的 `docs/agents/alignment-transform-manipulator.md`。

## 坐标与数据边界

CloudBIM 剖切框在查看器世界坐标（米，Y 向上）工作。六个裁切平面法向朝内，
保留 `distanceToPoint >= 0` 的区域。框旋转围绕当前选中范围的世界中心，
同时更新局部范围偏移；剖切不修改点云、BIM 或配准矩阵。

隧道工程坐标 `(N,E,Z)` 通过原有且唯一的 `Rx(-π/2)` 映射为查看器
`(N,Z,-E)`。移动使用父矩阵完整逆线性部分；旋转使用
`parentWorld^-1 * T(pivot) * R_viewY * T(-pivot) * objectWorldStart`。
测试包含非恒等父旋转、反射绑定和四个非对称非共面点。
现有数值姿态契约支持刚性/等距反射绑定，未扩展为任意剪切矩阵编辑。

两边均在原工作区修改，保留此前未提交改动。浏览器拦截所有非读取请求，
没有保存配准、覆盖源数据或历史分析，也没有访问生产环境。

## 验证结果

| 项目 | 结果 |
| --- | --- |
| CloudBIM 剖切几何、真实 Three 拾取、事件仲裁 | 35 项通过 |
| CloudBIM 原有手柄、变换状态、钢筋交互、相机导航 | 33 + 16 + 22 + 8 项通过 |
| 隧道手柄、原自由移动、测量、校准步骤和模式切换 | 91 项通过 |
| CloudBIM 工作区包构建及应用类型检查/构建 | 通过 |
| 隧道 `npm run build`，含 `vue-tsc -b` | 通过 |
| 两边 `git diff --check`、定向 Impeccable 检测 | 通过，检测结果 `[]` |

CloudBIM 浏览器使用项目 3 的 BIM 13 与点云 14，实际鼠标操作覆盖配准页的
透视/正交拖面与旋转、精确撤销、重置、1280/2560 宽布局，以及两个预览页。
相机和源模型矩阵保持不变，页面异常和写请求均为 0。
隧道浏览器使用项目 55 的既有任务 105，验证两种投影下移动、旋转、撤销、
保存禁用、大小调整，扫描与设计绑定矩阵不变；页面异常和写请求均为 0。

这些是本机 Chromium + SwiftShader 的 WebGL 验证，没有据此声称硬件 GPU、
WebGPU 或生产环境验收。构建仍有原有的大包体积提示。

复现 CloudBIM（通过 `scripts/cloudbim-dev.sh` 运行本地栈）：

```bash
node scripts/test_section_box.mjs
node scripts/test_alignment_gizmo.mjs
node scripts/test_alignment_transform_state.mjs
node scripts/test_rebar_inspection_interaction.mjs
node scripts/test_orbit_navigation.mjs
npm run build
CLOUDBIM_SECTION_E2E=1 node scripts/test_section_box_browser.mjs
```

浏览器脚本只在内存中生成本地短期认证，禁止写请求，并在结束时关闭独立浏览器。
可用 `CLOUDBIM_SECTION_SURFACE=alignment|bim|pointcloud` 单独复测页面。
完整本机日志位于 `.cloudbim/assessment-20261030-viewer-parity/`；
原始未提交差异备份为该目录中的 `preexisting.patch`。

## 可复查产物

- [CloudBIM 浏览器验收数据](assets/viewer-parity-2026-10-30/cloudbim-browser.json)
- [点云提示布局复验](assets/viewer-parity-2026-10-30/pointcloud-layout.json)
- [配准页](assets/viewer-parity-2026-10-30/alignment.png)
- [BIM 预览](assets/viewer-parity-2026-10-30/bim-preview.png)
- [点云预览](assets/viewer-parity-2026-10-30/pointcloud-preview.png)
