# 分析页配准手柄

2026-10-28。分析页粗配准同时显示平移手柄和工程 Z 旋转环。

## 故障与修复

原实现创建两个绑定同一 canvas 的 TransformControls。箭头、平面与旋转环
拾取范围重叠时，两个控制器会同时响应同一次 pointerdown / pointermove。
使用实际页面 mountControls 和已安装的 Three.js 重现：水平拖动导致透视
视图额外旋转 46.755556°、正交视图额外旋转 53.809248°；相机矩阵不变。

现在两个控制器由单一输入模块驱动。一次拾取只高亮一个手柄，一次拖拽只由
一个控制器处理。箭头与平面在重叠处优先，松开前不会转交给旋转环；触摸无需
提前悬停。相机、点云及模型尺度保持不变。空白区的相机操作仍由 OrbitControls
处理。隐藏手柄后可继续用侧栏数值编辑。

拖拽中显示操作名与工程坐标增量（米/度）；Esc 恢复拖拽前的位置和姿态。
pointercancel、lostpointercapture、窗口 blur、隐藏、关闭编辑及卸载也会清理
捕获并回滚。完成拖拽才提交未保存状态，取消不会使已有分析结果失效。
Ctrl + 滚轮保留 60%–200% 的大小偏好，切换投影时保留；最终显示尺寸还受
下述 CSS 像素和视口上限约束。

## 屏幕尺寸与模型比例（2026-10-29）

原实现的上限只约束 `TransformControls.size` 的倍率，没有约束投影后的像素
范围。大窗口下控件过大；透视相机偏离模型中心时，Three r173 按相机与
原点的空间距离缩放，投影却取决于相机方向上的深度，接近眼平面可使手柄膨胀。

设计参考：

- [3ds Max Gizmos Preferences](https://help.autodesk.com/cloudhelp/2026/ENU/3DSMax-Customizing/files/GUID-8700D320-6D91-4F86-8EE4-1907EF8330D0.htm)
  将手柄尺寸设为视口尺寸的百分比，平移和旋转另有相对尺寸设置。
- [Blender Viewport Preferences](https://docs.blender.org/manual/en/latest/editors/preferences/viewport.html)
  提供独立的 Gizmo Size（直径）设置。

采用屏幕尺寸为主、模型屏幕占比为辅的规则。下面的具体数值是本项目的设计
选择，并非行业标准或上述软件的默认值。尺寸指以手柄中心为圆心、包含箭头
和旋转弧的屏幕包络直径，使用 CSS 像素，避免 DPI 或绘图缓冲区尺寸干扰。

| 项目 | 规则 |
| --- | --- |
| 模型比例 | 默认参考所选 BIM 投影包围盒长边的 30%；达到上下限后不再保持固定比例 |
| 默认目标直径 | `clamp(模型屏幕长边 × 0.30, 140, 220)` CSS px |
| 手动偏好 | Ctrl + 滚轮，60%–200%；恢复默认按钮回到 100% |
| 最终目标下限 | 120 CSS px；极小视口优先遵守视口上限 |
| 最终硬上限 | `min(280 CSS px, 视口短边 × 0.30)` |
| 近看 / 远看 | 近看达到上限后停止放大；远看保留可操作下限，不强求始终与小模型等比例 |
| 拖拽 | 锁定本次目标直径，禁止滚轮和重置改变大小；依然补偿透视投影并遵守视口上限 |
| 原点越过近裁剪面 | 手柄尺寸归零、不可拾取；回到可见深度后恢复 |

默认目标直径包含保守投影余量；实际可见箭头/弧的外沿通常略小。原点偏离
画面时优先避免透视膨胀，极端视角不保证达到可读下限，不将原点移到屏幕边缘。

`transformHandleInteraction.ts` 缓存 BIM 的局部包围盒（保留父子完整矩阵），
每帧仅投影八个角点，并根据相机深度、偏心量、FOV/zoom、视口 CSS 尺寸
反算 Three 的 `size`。箭头和旋转环共用包络和大小偏好，拾取器使用同一个
控制器尺寸。选择/显示状态刷新时重建缓存，缩放不遍历点云或整份 BIM。
显示事件只请求重绘；实际 `objectChange` 才同步数值与模型边界。此改动不改变
模型的坐标、旋转约束、真实尺度、保存矩阵或历史结果。

恢复按钮沿用 `fine-reset-link` 控件和既有颜色 token；隐藏手柄或剖切时不显示，
拖拽中禁用。保留蓝环、工程 XYZ 颜色、悬停反馈与精确数值输入，无新弹窗。

## Leica Cyclone 3DR 对照

依据官方 [Free Move 操作说明](https://rcdocs.leica-geosystems.com/v1/docs/cyclone-3dr-freemovec)
及 [2023.1.0 发布说明第 16–17 页](https://cyclone3dr.leica-geosystems.com/download/Cyclone3DRVersion2023.1/ReleaseNotes/Leica%20Cyclone%203DR%202023.1.0%20REL.pdf)。
本次对照的是文档公开的交互语义，没有运行 Leica 桌面软件，也没有其内部拾取实现。

| 操作 | Leica 官方行为 | 本次分析页实现 |
| --- | --- | --- |
| 轴向移动 | 拖动有色轴 | 箭头只平移；X 红、Y 绿、Z 蓝 |
| 平面移动 | 拖动有色平面 | 方块约束两个自由度，水平面不改变高程 |
| 视平面移动 | 拖动中心球 | 保留中心手柄沿视平面平移 |
| 旋转 | 拖动对应颜色的弧 | 保留配准页原有的工程 Z 竖直约束，蓝环只旋转 |
| 精确调整 | 悬停后 Tab 输入数值与单位 | 侧栏输入米/度；修正父坐标换算，与手柄方向一致 |
| 手柄大小 | Ctrl + 滚轮 | 同样快捷键；有最小/最大尺寸并保留到投影切换 |
| 恢复操作 | 支持撤销/重做 | Esc 取消当前拖拽；保留整体重置，未增加多步撤销历史 |
| 改变操纵中心、方向 | 中心点及轴可重定义 | 保留模型中心与世界坐标，未加入自定义 UCS |
| 模型缩放 | 白色双向箭头 | 配准保持刚性和真实尺度，不开放缩放 |

事件互斥与中断回滚是本项目的实现选择；官方资料未公开其事件调度方式。
坐标公式见 [配准坐标约定](alignment-coordinate-frames.md)。

## 验证

```bash
node --experimental-strip-types --test scripts/test_alignment_gizmo.mjs scripts/test_alignment_transform_state.mjs scripts/test_rebar_inspection_camera.mjs packages/alignment/src/transformChangeEvents.test.ts
npx vue-tsc --noEmit -p packages/alignment/tsconfig.json --types node,vite/client --allowJs
npm run build
CLOUDBIM_GIZMO_E2E=1 node scripts/test_alignment_gizmo_browser.mjs
```

Node 检查覆盖真实拾取重叠、透视/正交、三轴/三平面/视平面移动、Z 旋转、
非对称坐标、父节点完整变换、保存恢复、相机导航、多指干扰、各类中断及清理。
方向三轴的默认视口模式和工程模式分别验证。无预先悬停的触摸在首个渲染帧
也不得跳动：锁定轴后先更新拖拽平面，再记录起点。本轮相关检查 **61/61 通过**，
包类型检查、工作区构建及真实页面 WebGL 检查均通过。

浏览器检查使用独立的 Chromium/SwiftShader WebGL，在管理脚本启动的
`127.0.0.1:5173` 上读取 Lumos 的 BIM 13 / 点云 14，执行真实指针拖拽并读取
实际场景矩阵。测试禁止应用的写入请求，没有保存临时位姿或运行新的分析。
检查水平重叠拖拽、XY 平面、Z 旋转、取消还原、相机及已载入点云矩阵；
正常 mouseup 只提交一次变换，拖拽中禁止保存或进入后续分析；
截图覆盖 1920×1080、2560×1440、1280×800。浏览器脚本错误和应用写入请求均为 0。

本机证据位于 `.cloudbim/assessment-20261028-gizmo/`。这是浏览器软件渲染及
合成指针的证据；物理触屏/触控笔、目标机器硬件 GPU、Leica 桌面并排操作
未验证。可用 `CLOUDBIM_GIZMO_URL`、`CLOUDBIM_GIZMO_EVIDENCE`、
`CLOUDBIM_PLAYWRIGHT_MODULE`、`CLOUDBIM_CHROMIUM` 覆盖测试地址和环境。

### 2026-10-29 尺寸验收

上述四组 Node 检查本次 **74/74 通过**。新增检查通过真实页面接线及 Three.js
几何顶点测量屏幕包络，覆盖大视口、相机 zoom、偏心原点、模型比例、近看平台、
远看下限、窗口尺寸、恢复大小、拖拽锁定、真实单位变化及近裁剪面恢复。
修复前，大视口及偏心原点的三个像素上限用例均失败；修复后全部通过。
`npm run build`、alignment 独立 `vue-tsc` 均通过；构建仍有原有的大 chunk 提示。

浏览器使用同一 BIM 13 / 点云 14，在 **DPR 1 与 1.5 各完成 17 项检查**。
截图/布局覆盖 1920×1080、2560×1440、1707×960、1512×982、1440×900、1280×800。
测量要求原点实际处于视口内；透视和正交均使用原生滚轮，放大后的正交手柄
再次执行真实拾取、拖拽和取消。手柄恢复大小不改变模型与相机，全部尺寸操作
不使已保存配准变脏；最终另外验证正常拖拽提交及后续分析门禁。

| 场景（DPR 1） | 实测可见包络直径 / CSS px |
| --- | ---: |
| 1920×1080 默认视角 | 135.26 |
| 2560×1440 | 177.53 |
| 1280×800 | 132.53 |
| 1280×800，透视放大 | 195.45 |
| 1280×800，手动偏好达到 200% | 203.61 |
| 1280×800，正交 zoom 2.79 | 206.42 |

1280×800 的实际三维视口为 870×711 CSS px，其上限为 213.3 px。
两种 DPR 在相同初始/窗口视角下测得相同 CSS 尺寸；滚轮事件的浏览器合并会
改变动态缩放的最终相机位置，因此这些动态行不用于跨 DPR 精确位姿比较。
两轮最终浏览器验收的脚本错误和应用写入请求均为 **0**。

本机证据：`.cloudbim/assessment-20261029-gizmo-size/`，其中 `before/` 为原界面，
`after/`、`dpr-1.5/` 含最终截图和 `browser-checks.json`；`build.log` 和
`typecheck.log` 为构建/类型检查记录。复跑高 DPI：

```bash
CLOUDBIM_GIZMO_E2E=1 CLOUDBIM_GIZMO_DPR=1.5 CLOUDBIM_GIZMO_EVIDENCE=.cloudbim/assessment-20261029-gizmo-size/dpr-1.5 node scripts/test_alignment_gizmo_browser.mjs
```

这些是 Chromium/SwiftShader 和合成输入证据，未扩展为目标硬件 GPU 或物理触屏验证。
