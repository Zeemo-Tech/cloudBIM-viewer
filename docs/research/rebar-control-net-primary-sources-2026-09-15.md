# 钢筋控制网：中心线替代重网格最近面比较的证据边界

日期：2026-09-15。本文只记录已读取的一手论文和官方文档；它不是对本仓库实现或任一完整“钢筋中心线检测—匹配—变形诊断”流水线的验证。

## 结论（针对当前方案的工程推断）

**可以把“按实例的控制中心线 + 截面支持”作为钢筋走向、横向偏位和局部弯曲的主判据，但不能无条件替代扫描到重网格的面距离。** 适用前提是：实例身份已可靠分割；扫描与 BIM 在稳定控制基准下配准；每段都有足够的截面支持、覆盖率和不确定度；曲线参数对应关系明确。这使中心线指标不再直接受显示网格和最近面查询影响，但拟合及曲线对应仍可能偏置；面比较应保留为截面半径/表面残差、未分割区域的辅助证据。中心线失配时不能悄悄回退面距离并沿用同一指标标签。

这不是“论文已证明本仓库全流程”的说法。下列来源分别支撑圆柱参数不确定度、由截面中心迭代更新扫掠轴线、配准基准、无网格点云距离及曲线对应的原则；把它们组合为钢筋控制网是工程设计推断。

## 建议的可报告指标

| 层级 | 指标（按弧长参数 `s` 分段） | 判读与必须附带的质量信息 |
| --- | --- | --- |
| 身份/可用性 | 实例匹配置信度、点归属比例、有效弧长覆盖率、最大无数据间隙、每截面内点数/方位覆盖 | 覆盖率以可靠区间并集长度计算；1–99% 轴向跨度另报，不能代表洞内有观测。不可把设计长度当作实测长度。 |
| 中心线几何 | 明确单调对应后 `Δc(s)=c_scan(φ(s))-c_BIM(s)` 的三分量；在 BIM 平行运输标架中的横向量、横向模 `||Δc_perp||`、可辨识的轴向分量；median、P95、max | 主告警宜用横向量和质量/经标定的不确定度；全局刚体残差须同报，防止把配准误差称为钢筋变形。直段不使用未定义的 Frenet 法向。 |
| 走向/形状 | 切向夹角 `acos(t_scan·t_BIM)`、曲率差 `κ_scan-κ_BIM`、弧长差、端点相对 BIM 的三维偏位 | 曲率需平滑尺度和最小支持长度；端点被遮挡时，端点/长度指标为缺失。 |
| 截面/表面 | 每个正常截面相对固定设计半径的径向残差 median、P95/MAD，以及截面圆心拟合残差 | 这是中心线无法代替的表面证据；把残差同扫描噪声和拟合协方差比较，避免把噪声报告为变形。 |
| 判定 | 每段 `pass / warning / fail / insufficient-data`，并保存阈值、置信区间、支持点和版本 | 阈值不是文献直接给出的通用毫米数，须来自项目公差、仪器与现场验证。 |

### 不可观测性与缺失数据

以下是由测量模型作出的推导，而非某篇钢筋试验的结论：没有端点、弯钩、接头、表面纹理或其他锚点时，一段完美圆柱的局部表面不含沿轴向的绝对参数化信息，因此扫描片段与设计曲线之间可发生**轴向滑移**的多解；圆截面对绕轴旋转也不变，因此**扭转**不可由光滑圆钢筋表面识别。不要用最近点配对偷偷选择其中一个解。应显示为未约束/低置信度，或仅在可靠端点、特征和连续可见段锚定后报告轴向量；跨越遮挡洞不得插值成“实测变形”。

## 可视化建议（工程推断）

1. 以 BIM 中心线弧长等距采样；每个采样环生成一小段扫掠管，按 `||Δc_perp||` 或有符号局部方向分量着色。模长用顺序色带，有符号分量用以零为中心的发散色带；未知用独立灰色/纹理，不能与零值混淆。另用箭头/短线画位移向量。
2. 扫描中心线画成细黑/白线，BIM 线画成对照线；端点和弯钩单独标记。低覆盖段用灰色虚线或透明，而不是色带的“零偏差”。
3. 可切换三层：`中心线偏移`、`截面半径残差`、`数据质量`（点数、方位覆盖、置信区间）。点击环显示 `s`、三分量、P95、覆盖和 `pass/warning/insufficient-data`。
4. 使用曲线生成管体本身是可行的前端表示：three.js 的官方 [TubeGeometry 文档](https://threejs.org/docs/pages/TubeGeometry.html) 将路径、管半径、路径分段和径向分段作为输入。它只证明渲染能力，不证明测量正确性；数值应取自保存的控制网数据，而不是从颜色反推。

## 已读取来源与可支持的最小主张

1. Franaszek, M. (2012), *Variances of Cylinder Parameters Fitted to Range Data*, Journal of Research of NIST, DOI [10.6028/jres.117.015](https://doi.org/10.6028/jres.117.015)，开放全文：[PMC4553871](https://pmc.ncbi.nlm.nih.gov/articles/PMC4553871/)。论文推导单站量测点云经非线性最小二乘拟合圆柱参数的方差，并明确将设计公差与拟合参数不确定度共同用于 as-built/as-designed 验收。**限制：**模型是无限长规则圆柱、特定量测误差假设；不能直接给出弯曲钢筋、遮挡片段或多站配准后的置信区间。
2. Yi et al. (2020), *Tunnel Deformation Inspection via Global Spatial Axis Extraction from 3D Raw Point Cloud*, Sensors, DOI [10.3390/s20236815](https://doi.org/10.3390/s20236815)，开放全文：[PMC7730831](https://pmc.ncbi.nlm.nih.gov/articles/PMC7730831/)。在有遮挡、缺失、噪声和离群点的扫掠结构上，论文从设计轴法平面取截面、拟合圆心并以 B-spline 迭代更新空间轴。支持“截面中心—控制曲线”可作为抗网格依赖的构建思路。**限制：**对象是隧道，不是细钢筋；作者验证的是其隧道任务，不能外推为钢筋实例分割/端部测量的验证。
3. Vezočnik et al. (2009), *Use of Terrestrial Laser Scanning Technology for Long Term High Precision Deformation Monitoring*, Sensors, DOI [10.3390/s91209873](https://doi.org/10.3390/s91209873)，开放全文：[PMC3267200](https://pmc.ncbi.nlm.nih.gov/articles/PMC3267200/)。论文为长期变形监测把 TLS 与静态 GNSS、精密全站仪结合，以取得稳定参考系统；指出不稳定基准会把基准自身运动混入平移、旋转和形变。支持先报告控制网/配准残差再解释构件偏差。**限制：**是管线案例；并非 BIM 钢筋容差标准。
4. Lague, Brodu & Leroux (2013), *Accurate 3D comparison of complex topography with terrestrial laser scanner*, ISPRS JPRS 82:10–26，作者公开稿：[arXiv:1302.1183](https://arxiv.org/abs/1302.1183)，DOI [10.1016/j.isprsjprs.2013.04.009](https://doi.org/10.1016/j.isprsjprs.2013.04.009)。M3C2 直接比较两点云，按局部法线测平均变化并显式计算置信区间，避免网格生成；论文也把“可跟踪同名点的位移场”与“无同名点时的距离”区分开。支持保留带不确定度的点云面/截面残差作为辅证。**限制：**法向表面变化不等于构件中心线位移，且需要尺度、法向和粗糙度参数。
5. Kim et al. (2021), *Displacement Estimation Error in Laser Scanning Monitoring of Retaining Structures Considering Roughness*, Sensors, DOI [10.3390/s21217370](https://doi.org/10.3390/s21217370)，开放全文：[PMC8588410](https://pmc.ncbi.nlm.nih.gov/articles/PMC8588410/)。作者在人工位移试验中比较 C2C、C2M、M3C2，报告点云分辨率影响 C2C，曲率位置会令 C2M 低估，M3C2 在其材料/参数下误差最低；表面粗糙度、曲率和方向均影响结果。支持不把修补重网格最近面距离当作唯一真值。**限制：**保留结构表面实验，不能据此宣称 M3C2 对钢筋总是最佳。
6. Abdi et al. (2022), *Automatic generation of structural geometric digital twins from point clouds*, Automation in Construction, DOI [10.1016/j.autcon.2022.104407](https://doi.org/10.1016/j.autcon.2022.104407)，开放全文：[PMC9789981](https://pmc.ncbi.nlm.nih.gov/articles/PMC9789981/)。论文从点云取得构件位置/截面形状，再以空间关系和分类推断语义、更新 BIM，并在两个真实施工项目对尺寸/处理时间评价。支持将“身份/语义”和“几何测量”分层保存。**限制：**梁、柱和支撑案例；不验证钢筋的密集相邻实例分离。

## 根代理补充：直接面向钢筋的研究

以下读取了出版商提供的摘要/公开预览，未取得付费全文；只引用预览明确支持的研究路线，不据此审计完整算法、复现实验或承诺本项目精度。

7. Wang et al. (2026), *Automated as-built rebar reconstruction and quality assessment from point clouds via Elastic Cylindrical Growth*, Automation in Construction 187, 106974，[出版商预览](https://www.sciencedirect.com/science/article/pii/S0926580526002153)，DOI [10.1016/j.autcon.2026.106974](https://doi.org/10.1016/j.autcon.2026.106974)。公开摘要/引言描述了圆柱生长提取钢筋、B-spline 中心线精化、广义圆柱重建，再与 BIM 比较直径、间距和弯角的流程。这是与本项目方向直接相关的先例，支持“分割—中心线—参数化比较”路线；没有证明本项目现有控制网已解决全部遮挡、实例身份或去噪。抓取证据：`.firecrawl/rebar-ecg-20260915.md`。
8. Cheng et al. (2023), *Reconstruction of tunnel lining rebars from terrestrial laser scanning data*, Structural Concrete 24(1), 563–582，[出版商页面](https://onlinelibrary.wiley.com/doi/10.1002/suco.202200897)。摘要描述弯曲钢筋的语义分割、实例标号和重建，并以人工标签评价分割。这支持将实例分割与几何重建分别验收；实验对象是隧道衬砌钢筋，不等同于当前钢筋笼。抓取证据：`.firecrawl/rebar-tunnel-20260915.md`。
9. Jeong et al. (2026), *Smartphone LiDAR-Based Workflow for Fast On-Site Inspection of Placed Reinforced Bars with BIM*, Construction Research Congress 2026，[出版商页面](https://ascelibrary.org/doi/10.1061/9780784486979.095)。摘要描述聚类、单筋中心线、位置/方向/长度及 BIM 初步比对，明确是 mock-up 概念验证，仍需全尺寸工地验证。说明路线存在，不能从论文标题推断已解决现场完整可靠性。抓取证据：`.firecrawl/rebar-lidar-20260915.md`。

## 落地前的最小验证集（建议，不是文献结论）

以人工已知横移、轴向滑移、弯曲和端点截短的合成/标定扫描分别检验：是否只在可观测方向报告；覆盖下降时置信区间是否扩大或转为 `insufficient-data`；中心线热图与独立截面残差是否一致。再用原有 C2M/修补网格结果做回归对照，而非删除它后仅以视觉效果验收。

原始检索与抓取文本保存在本任务临时目录 `/tmp/cloudbim-control-net-research-20260915/sources/`，未写入仓库，也未向外部服务发送任何私有仓库内容。
