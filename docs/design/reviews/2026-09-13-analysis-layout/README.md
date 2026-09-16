# 点云分析布局调整 · 2026-09-13

按 impeccable layout 和 DESIGN.md 的 Operate 模式改进。保留蓝白界面、科学配色、50px 工具栏、360px 任务面板和已有业务门槛。在 dev-hong 上修改，保留任务开始时已有的未提交修改。

## 主要变化

- 配准页移除网格均匀化区域；IFC 预览的原始 IFC / 网格结果切换、重新生成和刷新状态仍可用。
- 分类与去噪移除点云下载，并同步更新帮助和空状态文案；类别、实例配色和显隐保持。
- 偏差页移除降采样、逐筋选择/导出、手动加载/清空区域。计算明确发送 downsampleEnabled=false，使用第二步的全量保留钢筋点。图表、容差、范围、色带和显示设置保存保持；统计详情默认折叠，失效和失败警告仍独立显示。
- 结果计算完成自动显示；修复后台场景就绪时空场景未加载的分支。仅在偏差步骤加载，保留版本、请求、资源和新鲜度检查；加载期间显示状态，未成功显示时可重试。
- 移除画布左上角显示小组件。左侧三圆点按钮打开点大小（1–5 px）及适用的原始点云配色。EDL 保留左侧快捷按钮。弹层支持键盘、Esc、点击外部和切步关闭。
- 透视/正交合并为单按钮，默认透视，按钮名称说明当前模式和切换方向。左侧按钮 36×36px，小高度视口减少间距，不缩小控件。

## 原始截图

以下 2560×1440 和 1920×1080 文件均为原始视口截图，16:9，未裁剪、拉伸或拼接。修改前来自本次编辑前的实际运行页面；不是 Git HEAD，也未覆盖上一轮截图目录。

| 步骤 | 2560×1440 前 | 2560×1440 后 | 1920×1080 |
| --- | --- | --- | --- |
| 配准 | [修改前](/home/hong/hong_project/cloudBIM-viewer/docs/design/reviews/2026-09-13-analysis-layout/before-step1-2560x1440.png) | [修改后](/home/hong/hong_project/cloudBIM-viewer/docs/design/reviews/2026-09-13-analysis-layout/after-step1-2560x1440.png) | [修改前](/home/hong/hong_project/cloudBIM-viewer/docs/design/reviews/2026-09-13-analysis-layout/before-step1-1920x1080.png) / [修改后](/home/hong/hong_project/cloudBIM-viewer/docs/design/reviews/2026-09-13-analysis-layout/after-step1-1920x1080.png) |
| 分类与去噪 | [修改前](/home/hong/hong_project/cloudBIM-viewer/docs/design/reviews/2026-09-13-analysis-layout/before-step2-2560x1440.png) | [修改后](/home/hong/hong_project/cloudBIM-viewer/docs/design/reviews/2026-09-13-analysis-layout/after-step2-2560x1440.png) | [修改前](/home/hong/hong_project/cloudBIM-viewer/docs/design/reviews/2026-09-13-analysis-layout/before-step2-1920x1080.png) / [修改后](/home/hong/hong_project/cloudBIM-viewer/docs/design/reviews/2026-09-13-analysis-layout/after-step2-1920x1080.png) |
| 偏差对比 | [修改前](/home/hong/hong_project/cloudBIM-viewer/docs/design/reviews/2026-09-13-analysis-layout/before-step3-2560x1440.png) | [修改后](/home/hong/hong_project/cloudBIM-viewer/docs/design/reviews/2026-09-13-analysis-layout/after-step3-2560x1440.png) | [修改前](/home/hong/hong_project/cloudBIM-viewer/docs/design/reviews/2026-09-13-analysis-layout/before-step3-1920x1080.png) / [修改后](/home/hong/hong_project/cloudBIM-viewer/docs/design/reviews/2026-09-13-analysis-layout/after-step3-1920x1080.png) |
| 报告 | [修改前](/home/hong/hong_project/cloudBIM-viewer/docs/design/reviews/2026-09-13-analysis-layout/before-step4-2560x1440.png) | [修改后](/home/hong/hong_project/cloudBIM-viewer/docs/design/reviews/2026-09-13-analysis-layout/after-step4-2560x1440.png) | [修改前](/home/hong/hong_project/cloudBIM-viewer/docs/design/reviews/2026-09-13-analysis-layout/before-step4-1920x1080.png) / [修改后](/home/hong/hong_project/cloudBIM-viewer/docs/design/reviews/2026-09-13-analysis-layout/after-step4-1920x1080.png) |

Mac 兼容视口保留各自原始比例，单独提供：

| 视口 | 四个步骤 |
| --- | --- |
| 1512x982 | [步骤 1](/home/hong/hong_project/cloudBIM-viewer/docs/design/reviews/2026-09-13-analysis-layout/after-step1-1512x982.png) / [步骤 2](/home/hong/hong_project/cloudBIM-viewer/docs/design/reviews/2026-09-13-analysis-layout/after-step2-1512x982.png) / [步骤 3](/home/hong/hong_project/cloudBIM-viewer/docs/design/reviews/2026-09-13-analysis-layout/after-step3-1512x982.png) / [步骤 4](/home/hong/hong_project/cloudBIM-viewer/docs/design/reviews/2026-09-13-analysis-layout/after-step4-1512x982.png) |
| 1440x900 | [步骤 1](/home/hong/hong_project/cloudBIM-viewer/docs/design/reviews/2026-09-13-analysis-layout/after-step1-1440x900.png) / [步骤 2](/home/hong/hong_project/cloudBIM-viewer/docs/design/reviews/2026-09-13-analysis-layout/after-step2-1440x900.png) / [步骤 3](/home/hong/hong_project/cloudBIM-viewer/docs/design/reviews/2026-09-13-analysis-layout/after-step3-1440x900.png) / [步骤 4](/home/hong/hong_project/cloudBIM-viewer/docs/design/reviews/2026-09-13-analysis-layout/after-step4-1440x900.png) |
| 1280x800 | [步骤 1](/home/hong/hong_project/cloudBIM-viewer/docs/design/reviews/2026-09-13-analysis-layout/after-step1-1280x800.png) / [步骤 2](/home/hong/hong_project/cloudBIM-viewer/docs/design/reviews/2026-09-13-analysis-layout/after-step2-1280x800.png) / [步骤 3](/home/hong/hong_project/cloudBIM-viewer/docs/design/reviews/2026-09-13-analysis-layout/after-step3-1280x800.png) / [步骤 4](/home/hong/hong_project/cloudBIM-viewer/docs/design/reviews/2026-09-13-analysis-layout/after-step4-1280x800.png) |
| 1280x720 | [步骤 1](/home/hong/hong_project/cloudBIM-viewer/docs/design/reviews/2026-09-13-analysis-layout/after-step1-1280x720.png) / [步骤 2](/home/hong/hong_project/cloudBIM-viewer/docs/design/reviews/2026-09-13-analysis-layout/after-step2-1280x720.png) / [步骤 3](/home/hong/hong_project/cloudBIM-viewer/docs/design/reviews/2026-09-13-analysis-layout/after-step3-1280x720.png) / [步骤 4](/home/hong/hong_project/cloudBIM-viewer/docs/design/reviews/2026-09-13-analysis-layout/after-step4-1280x720.png) |

[左侧点大小和配色弹层，1280×720](/home/hong/hong_project/cloudBIM-viewer/docs/design/reviews/2026-09-13-analysis-layout/pointcloud-tools-1280x720.png)

## 验证

- npm run build：通过（vue-tsc 和 Vite）。存在原有的 >500kB 分块体积提示。
- node --experimental-strip-types --test src/views/alignment/alignmentLifecycle.regression.test.ts：10/10 通过。新增用例覆盖全量计算参数、完成后自动显示、后台结果就绪、避免重复/并发加载、离开步骤以及过期结果。
- git diff --check：通过。Impeccable 检测结果 []。
- 4 步骤 × 6 视口：24 个布局状态无整页横向溢出；所有工具按钮保持 36px，1280×720 内全部可见。长任务面板内部滚动。主视口画布宽 2150px；1920 下 1510px；1280 下 870px。收起面板后 1280 下画布宽 1230px。
- 浏览器实测：默认透视、切换正交及返回透视；点大小键盘从 2.5 调至 2.6 并恢复，focus-visible 轮廓可见；强度/紫黄色带/真彩切换；Esc 关闭弹层；分类全隐提示及只看钢筋；偏差手动/自适应切换；面板收起/展开；Esc 退出测量选中状态；四步骤返回后仍有画布；新鲜结果进入步骤 3 自动显示。
- 实际路径：项目 → 扫描点云 → 分析 → 返回扫描点云 → 设计模型 → IFC 预览 → 返回模型列表 → 再进分析，均可用。IFC 预览确认存在网格结果和重新生成入口，未触发重新生成。
- 控制台有任务修改前已出现的 3d-tiles-renderer PNTS 长度断言，以及截图 ReadPixels 性能提示；未将它们计作“全清”。本次未处理输入瓦片长度问题。控制台工具汇总 0 errors，但将 assert 单独记录，详见原始日志。
- 未为外观验证执行昂贵的真实全量计算、保存配准或重新网格化；计算提交与异步完成分支由真实函数配合受控服务替身的回归测试覆盖。未做 macOS 真机/Retina GPU 或完整无障碍认证。

## 独立审查

子智能体 layout_audit 只读检查了控件分组、阅读顺序、密度、窄窗口和自动加载风险，指出后台就绪未加载分支；由主智能体实施并验证。未写源文件或启动服务，任务已完成；运行时未提供关闭工具，因此未宣称槽位释放。

证据：[布局数据](/home/hong/hong_project/cloudBIM-viewer/docs/design/reviews/2026-09-13-analysis-layout/layout-validation.json)、[测试日志](/home/hong/hong_project/cloudBIM-viewer/docs/design/reviews/2026-09-13-analysis-layout/tests.txt)、[构建日志](/home/hong/hong_project/cloudBIM-viewer/docs/design/reviews/2026-09-13-analysis-layout/build.txt)、[机械检测](/home/hong/hong_project/cloudBIM-viewer/docs/design/reviews/2026-09-13-analysis-layout/detector.json)、[控制台记录](/home/hong/hong_project/cloudBIM-viewer/docs/design/reviews/2026-09-13-analysis-layout/console-errors.txt)。
