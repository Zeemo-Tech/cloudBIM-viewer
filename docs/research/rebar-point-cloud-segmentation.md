# 浇筑前叠合板钢筋点云分割与实例提取技术调研

> 调研日期：2026-09-05
>
> 场景：预制工厂内、浇筑前叠合板钢筋网/桁架筋；输入为带或不带 RGB 的点云。
>
> 目标区分：**语义分割**回答“哪些点是钢筋”，**实例提取**回答“每一根钢筋分别是什么”，后者才足以稳定计算数量、中心线、间距和直径。

## 1. 结论先行

有合适算法，但不建议把问题押在单一网络或单一 RANSAC 上。对现场图中的密集交叉钢筋、桁架筋、绑扎接触、遮挡、锈蚀/反光、底模/叠合板平面，以及横跨构件上方的非钢筋管杆，最稳妥的是“**学习式语义过滤 + 几何先验实例化 + 设计规则校验**”三段式。

推荐优先级如下：

1. **首个 PoC：几何基线。** ROI/坐标对齐 -> 平面与高度带分离 -> PCA 线性特征和方向聚类 -> 逐方向 Line-RANSAC/迭代 Hough -> 断点桥接 -> 中心线、间距和完整度计算。它不需要标注，可很快验证传感器点密度是否够用。
2. **生产候选：PTv3/PointNeXt 语义分割 + 上述几何实例化。** 网络负责排除底模、板面、吊具、传感器横杆等“形似钢筋”的干扰；几何模块利用钢筋直径、两组主方向、层高、设计间距和连续性把同一语义类拆成单根实例。
3. **研究候选：OneFormer3D 端到端实例分割。** 2025 年已经有一篇直接针对钢筋网的合成到真实验证，报告真实集 `92.1 mAP`，但真实测试只有 12 个受控样本、每个仅 8--14 根直筋，且论文明确出现相邻钢筋合并和边缘缺失；适合作为对照，不宜直接把该数字外推到现场图的复杂叠合板。
4. **如果输入天然是同步 RGB-D/有组织点云：2D mask + 深度投影值得并行做。** 2024 年双层双向板筋论文和 2025 年 Rebar-YOLOv8-seg 工作都走这条路。它通常比从零训练纯 3D 网络省数据，但阴影、锈蚀、湿润反光会损伤 mask 边缘，且直径测量对边缘尤其敏感。

不推荐直接使用原始欧氏聚类/DBSCAN 来分单根钢筋：交叉点和绑扎点会使整张钢筋网成为一个连通簇。圆柱 RANSAC 也不是默认首选；只有点云在钢筋圆周上有足够角度覆盖、法线可靠时才成立。结构光或单侧深度点云往往只形成近似带状/平面点，相关实验证明此时无法可靠做圆/圆柱拟合。[Wang et al., Buildings 2024](https://doi.org/10.3390/buildings14113693)

## 2. 针对两张现场图的约束判断

从附件可见，目标并非“孤立圆柱检测”，而是具有强工程先验的密集结构：

- 钢筋以近似正交的底层网片和连续桁架筋为主，存在大量真实接触/投影交叉；单纯连通域必然粘连。
- 有不同高度层、斜腹杆、边缘伸出筋和可能的吊点/预埋件；只建“水平钢筋/背景”二类会丢掉下游所需语义。
- 底面大而平整，适合 RANSAC 平面和高度归一化；但下层筋离底面较近，删除阈值过大又会吃掉钢筋下缘。
- 构件上方有数根粗长横杆/管路，其线性和圆柱性甚至强于细钢筋；必须用 ROI、直径范围、颜色/强度、相对层高或设备固定掩膜排除。
- 表面存在锈蚀、积水/反光、阴影和遮挡。Buildings 2024 的现场实验也观察到锈蚀和上层筋阴影导致直径误判，说明训练集必须专门覆盖这些情况。[论文原文](https://doi.org/10.3390/buildings14113693)

因此，建议最终输出不只是 `rebar/background`，而是至少包含：`板面/底模`、`网片筋`、`桁架上弦/下弦筋`、`桁架腹筋`、`预埋/绑扎附件`、`临时设备管杆`、`其他背景`；同时为每根需要计量的主筋保存 `instance_id` 和 `centerline_id`。

## 3. 高相关钢筋论文

| 工作 | 实际技术类型 | 结果与价值 | 代码/数据状态与局限 |
|---|---|---|---|
| [A Synthetic Data Generation Pipeline for Point-Cloud-Based Rebar Segmentation, ISARC 2025](https://doi.org/10.22260/ISARC2025/0147) | 真正 3D 点云实例分割；Blender/Infinigen + SfM/MVS 合成点云训练 OneFormer3D | 238 个合成样本，190/48 划分；12 个真实样本，手机距目标约 70 cm、每组 48--79 张图、8--14 根筋、间距 5--25 cm；真实 `mAP@[.50:.95]=92.1` | 论文未给出作者数据/生成管线仓库；受控规模小。作者明确报告相邻筋合并、边缘漏点，建议 DBSCAN + 形状先验后处理；也明确未覆盖复杂结构、遮挡、照明和不同扫描分辨率 |
| [Intelligent Inspection Method ... RC Slab, Buildings 2024](https://doi.org/10.3390/buildings14113693) | **2D 语义 mask + 3D 点云几何**，不是纯 3D 深度网络；RANSAC 分底模/上下层/单筋，K-means 分两主方向 | ORBBEC Gemini 2；684 张图组成 RL-600，K-Net 测试 IoU 93.37%；2 m × 2 m 双层双向试件，400/500/600 mm 采距。400 mm 时保护层/层距最大误差分别为 1.43/1.32 mm，500 mm 时为 1.18/0.37 mm；摘要称直径准确率 98.4% | 开放全文，但 `Data contained within article`，未发现专用代码/数据仓库；依赖旧版 mmsegmentation/Python 3.7/PyTorch 1.10/CUDA 11.3。600 mm 时保护层/层距最大误差升至 3.76/2.22 mm；被遮挡下层筋被排除。摘要还称保护层最大误差 0.41 mm，正文表格并不支持；正文结论的直径 97.8% 也与摘要 98.4% 不一致，复现时应以逐项表格为准 |
| [Automatic measurement ... Rebar-YOLOv8-seg and depth data, Measurement 2025](https://doi.org/10.1016/j.measurement.2024.116111) | 2D YOLOv8 实例/语义 mask 与深度映射到 3D | 与同步 RGB-D 场景高度契合，可绕开纯 3D 标注成本 | 未核验到作者官方 GitHub；不能把它归类成纯点云网络。期刊页面可核验论文元数据，但完整复现资产未公开 |
| [Autonomous dimensional inspection ... semantically enriched 3D models, Automation in Construction 2024](https://doi.org/10.1016/j.autcon.2024.105303) | Mask R-CNN 图像分割 -> 3D 映射 -> 点云聚类/实例识别 | 支持“语义先过滤、几何再计量”的组合路线 | 未核验到官方代码；SfM/MVS 对重复网格、反光和运动模糊敏感 |
| [Automated dimensional quality assessment ... formwork and rebar, Automation in Construction 2020](https://doi.org/10.1016/j.autcon.2020.103077) | 传统点云：Line-RANSAC + Circular-RANSAC 等几何处理 | 直接证明直线/圆拟合路线可做模板和钢筋尺寸质检 | 无已核验官方代码；对直、规则、稠密扫描更有利，对桁架斜杆/弯折/遮挡泛化有限 |
| [Robust Segmentation of Planar and Linear Features of Terrestrial Laser Scanner Point Clouds, Sensors 2018](https://doi.org/10.3390/s18030819) | 稳健 PCA（Det-MCD）区分共面/共线点，再做 robust complete-linkage 聚类 | 两个施工现场数据集报告 precision 96.8%、recall 97.7%；研究对象明确包含 reinforcement bar，适合做抗离群的线性候选生成 | 仅说明 MATLAB 实现，未发布代码；指标不是密集钢筋实例 AP，交叉处仍需连续性补全 |
| [Shape Recognition with Point Clouds in Rebars, ISARC 2012](https://www.iaarc.org/publications/proceedings_of_the_29th_isarc/shape_recognition_with_point_clouds_in_rebars.html) | 切片、5 mm 栅格化、连通分组、跨切片连续性 | 说明钢筋可按“线/切片连续体”而非完整圆柱恢复，对竖筋、箍筋等不同方向分解有启发 | 规则较老、分辨率和构件方向依赖强，无现代点级/实例指标及代码 |
| [Multiple Cylinder Detection in Organized Point Clouds, Sensors 2021](https://doi.org/10.3390/s21227630) | 有组织 RGB-D 点云法线、球坐标映射/MSER、多圆柱候选、2D 圆拟合 | 若保留深度图像拓扑，可作为单根候选局部圆柱精修参考 | 无公开代码；依赖稳定法线和可见曲面，高反射及长物体测量有局限，不适合直接处理交叉密集网片 |
| [Automatic evaluation of rebar spacing using LiDAR data, 2021](https://doi.org/10.1016/j.autcon.2021.103890)；[field application, 2023](https://doi.org/10.1016/j.autcon.2022.104708) | LiDAR + 切片/投影/几何识别 | 表明传统几何方法可进入桥梁现场并做间距评价 | 重点是局部排布与间距，不是通用点级语义/任意实例分割；未核验官方代码 |
| [Geometric and semantic point cloud data ... reinforcement cages, 2022](https://doi.org/10.1016/j.autcon.2022.104334) | 自上而下 LiDAR 分解、语义处理和中心点拟合 | 与钢筋笼数量、直径、间距质控直接相关 | 论文可核验，未确认有可直接下载并带训练划分的公开基准/官方代码 |
| [Improved Density Clustering ... Irregular Rebar Mesh, ASCE 2025](https://doi.org/10.1061/JSUED2.SUENG-1551) | 改进密度聚类，用于不规则钢筋网间距 | 比标准 DBSCAN 更贴近“不规则网片 + 间距” | 对应 [GitHub](https://github.com/longzezhou/Improved-Density-Clustering-for-Spacing-Measurement-in-Irregular-Rebar-Mesh-from-3D-Point-Clouds) 根目录只有两个 PCD 和一个 RAR；RAR 内约有 25 个 MATLAB `.m` 文件及 PCD，并非“完全无源码”，但无 README、入口/环境说明或正式 LICENSE，归档内引用的 `license.txt` 也缺失，仍无法安全复用或稳定复现 |
| [Automated recognition ... low-cost 3D laser scanner, Measurement 2025](https://doi.org/10.1016/j.measurement.2024.115765) | 低成本 3D 激光扫描 + 预制桥构件钢筋识别/尺寸评估 | 对“工厂固定工位、成本受限”有采集参考价值 | 未核验到官方仓库；需结合自身最小钢筋直径重新验证点距和量测精度 |
| [Point cloud and ML ... corrugated pipes and rebars, 2024](https://doi.org/10.1016/j.autcon.2024.105493) | 邻域协方差/几何特征 + 分类/聚类，再以增强 RANSAC 和圆拟合计量 | 证明“可学习局部特征 + 强几何拟合”适合预制构件中的细长构件与管件 | 未核验到官方代码；对象和叠合板相近，但波纹管/长钢筋的尺度、遮挡不同 |
| [Automated rebar diameter classification ... point cloud ML, 2021](https://doi.org/10.1016/j.autcon.2020.103476) | 点云手工特征/机器学习做名义直径分类 | 若目标只需在有限国标规格中分类，往往比连续圆柱拟合更稳 | 未核验到官方代码；分类不能替代实例完整性和间距检测 |
| [Quality Inspection ... Geometry-Prior Segmentation, Buildings 2026](https://doi.org/10.3390/buildings16020338) | 免训练；柱钢筋笼极坐标/柱坐标、角度直方图、Kasa 圆拟合、圆周先验、轴向断点桥接 | 18 组 BIM-TLS；展示了“先验坐标系 + 直方图峰值 + 局部拟合 + 桥接”的可解释实例化思想 | 场景是柱纵筋和套筒，不可直接照搬到平面叠合板；TLS 套筒 recall 仅 57.5%，说明遮挡/单向扫描仍是瓶颈。开放全文，未见代码仓库 |
| [Automated as-built rebar reconstruction ... Elastic Cylindrical Growth, Automation in Construction 2026](https://doi.org/10.1016/j.autcon.2026.106974) | 高分辨率 TLS 点云分块；局部圆柱 RANSAC 候选、跨块弹性圆柱生长与合并、B-spline 中心线、BIM 对比 | 相对基线 F1 提升 8%--40%，B-spline 将中心线 RMSE 从 7.8 mm 降至 2.7 mm；为弯曲、跨块钢筋提供了比“无限直线”更完整的后续路线 | 依赖圆柱可见性和高分辨率 TLS；未核验到作者代码/数据发布，不能据摘要复现；与单侧 RGB-D 带状点的可迁移性待实测 |

## 4. 通用点云网络与开源实现

| 模型/框架 | 适合度 | 官方代码状态（截至 2026-09-05） | 主要风险 |
|---|---|---|---|
| [Point Transformer V3, CVPR 2024](https://openaccess.thecvf.com/content/CVPR2024/html/Wu_Point_Transformer_V3_Simpler_Faster_Stronger_CVPR_2024_paper.html) / [Pointcept](https://github.com/Pointcept/Pointcept) | **首选纯 3D 语义主干**；全局/局部上下文较强，适合从复杂设备背景中找钢筋 | 作者官方；Pointcept 2026-08 仍有提交，包含 PTv3 和语义分割训练框架 | CUDA/spconv/FlashAttention 等环境较重；仍要控制 grid size，过粗体素会抹掉细筋 |
| [PointNeXt, NeurIPS 2022](https://openreview.net/forum?id=EAcWgk7JM58) / [官方仓库](https://github.com/guochengqian/PointNeXt) | **首选轻量对照**；点式局部邻域对细几何友好，架构比 Transformer 简单 | 官方 OpenPoints/PointNeXt，2026-07 仍有提交，MIT | 邻域采样对点密度变化敏感；大场景需合理切块和重叠推理 |
| [KPConv, ICCV 2019](https://openaccess.thecvf.com/content_ICCV_2019/html/Thomas_KPConv_Flexible_and_Deformable_Convolution_for_Point_Clouds_ICCV_2019_paper.html) / [PyTorch](https://github.com/HuguesTHOMAS/KPConv-PyTorch) | 形状和局部曲面建模强，可作高精度语义基线 | 作者官方，MIT；仓库仍可访问且 2025 年有提交 | 自定义 C++/邻域算子，安装和现代 CUDA 兼容成本高；原始文档主要针对 Ubuntu 18.04 |
| [RandLA-Net, CVPR 2020](https://openaccess.thecvf.com/content_CVPR_2020/html/Hu_RandLA-Net_Efficient_Semantic_Segmentation_of_Large-Scale_Point_Clouds_CVPR_2020_paper.html) / [官方仓库](https://github.com/QingyongHu/RandLA-Net) | 大点云、高吞吐语义基线 | 官方实现可用，但最后实质提交约 2023；另有 [Open3D-ML](https://github.com/isl-org/Open3D-ML) 集成 | 官方环境是 Python 3.5 + TF 1.11 + CUDA 9，直接部署风险很高；随机下采样也可能损害细筋边缘 |
| [PointNet++, NeurIPS 2017](https://arxiv.org/abs/1706.02413) / [官方仓库](https://github.com/charlesq34/pointnet2) | 最小可解释基线、验证标注/数据管线 | 官方，但 TF 1.2/Python 2.7、自定义算子，最后提交约 2022 | 不建议作为新生产栈；可改用现代 PointNeXt 实现同类思路 |
| [OneFormer3D, CVPR 2024](https://openaccess.thecvf.com/content/CVPR2024/html/Kolodiazhnyi_OneFormer3D_One_Transformer_for_Unified_Point_Cloud_Segmentation_CVPR_2024_paper.html) / [官方仓库](https://github.com/filaPro/oneformer3d) | **端到端实例分割首选研究基线**；已有钢筋专用论文验证 | 作者官方；基于 mmdetection3d 1.1，仓库最后提交 2024-10；提供 Dockerfile；代码为 [CC BY-NC 4.0](https://github.com/filaPro/oneformer3d/blob/main/LICENSE) | 文档称单卡通常需 24--32 GB；spconv/MinkowskiEngine、superpoint 预处理和 checkpoint 修复增加复现成本；“NC”不允许未经授权的商业使用，不能直接进入商业产品依赖 |
| [Mask3D, ICRA 2023](https://arxiv.org/abs/2210.03105) / [官方仓库](https://github.com/JonasSchult/Mask3D) | OneFormer3D 的实例分割对照 | 作者官方，MIT；最后提交 2023-10 | MinkowskiEngine 和较旧训练栈；对密集、同类、接触的细杆仍需几何后处理 |
| [MinkowskiEngine](https://openaccess.thecvf.com/content_CVPR_2019/html/Choy_4D_Spatio-Temporal_ConvNets_Minkowski_Convolutional_Neural_Networks_CVPR_2019_paper.html) / [NVIDIA 仓库](https://github.com/NVIDIA/MinkowskiEngine) | 稀疏体素卷积底座，不是开箱即用的钢筋算法 | NVIDIA 官方；最后提交 2024-03 | CUDA/PyTorch 编译兼容问题较多；体素尺寸若接近钢筋直径会造成不可逆信息损失 |

注：名称含 “Cylinder” 的 LiDAR 语义网络（例如 Cylinder3D）是柱坐标体素划分，并不是圆柱拟合/钢筋实例提取算法，不能因名称相似而优先选择。

## 5. 传统算法：何时有用，何时会失效

| 算法 | 推荐用途 | 成立条件 | 主要失败模式/实现 |
|---|---|---|---|
| 平面 RANSAC | 找底模/板面，建立局部 `z=0`，按高度带分层 | 底面是最大或可约束法向的平面；阈值小于下层筋与板面的有效间隙 | 湿面噪声、翘曲、多块模板；阈值过大误删钢筋。[PCL 平面分割](https://pointclouds.org/documentation/tutorials/planar_segmentation.html)、[Open3D segment_plane](https://www.open3d.org/docs/latest/tutorial/geometry/pointcloud.html#Plane-segmentation) |
| PCA 法线/曲率/线性度 | 平面/杆件候选筛选，估计局部轴向；特征如 `linearity=(λ1-λ2)/λ1` | 邻域半径应略大于点距、小于筋间距；点密度相对均匀 | 肋纹、交叉点、边缘和稀疏点会污染法线；多尺度特征比单一半径稳 |
| Line-RANSAC | 分离直的网片筋、拟合中心线 | 每根筋有足够连续内点；已按高度和主方向拆分 | 逐根提取有顺序偏差，交叉处抢点；需长度、半径、方向和设计间距约束。[PCL line model](https://pointclouds.org/documentation/classpcl_1_1_sample_consensus_model_line.html) |
| Cylinder/Circle RANSAC | 估半径/圆柱轴；高密度 TLS 多视角时有价值 | 至少覆盖可辨识圆弧，法线可靠，半径搜索范围已知 | 单侧 RGB-D、稀疏/带状点、肋纹使模型退化；先验证圆周覆盖。[PCL 圆柱分割](https://pointclouds.org/documentation/tutorials/cylinder_segmentation.html) |
| 迭代 3D Hough | 从有缺口、噪声的点中找多条长直线 | 直筋为主，方向离散，参数空间分辨率可控 | 内存/计算和量化误差；密集近邻平行线需细网格。[IPOL 论文、演示和源码](https://www.ipol.im/pub/art/2017/208/)、[作者 GitHub](https://github.com/cdalitz/hough-3d-lines) |
| 法线区域生长 | 在表面连续且法线平滑时先分片 | 高质量法线、曲率阈值可分 | 肋纹使同一筋断裂，接触处跨筋泄漏。[PCL 官方教程](https://pointclouds.org/documentation/tutorials/region_growing_segmentation.html) |
| 欧氏聚类/DBSCAN | 语义过滤后清除游离噪声、连接同轴短断点 | 不同目标实际有空间间隙，或先按方向/层/管状域门控 | 原始钢筋网在交叉/绑扎处全连通，不能直接分实例。[PCL](https://pointclouds.org/documentation/tutorials/cluster_extraction.html)、[Open3D DBSCAN](https://www.open3d.org/docs/latest/tutorial/geometry/pointcloud.html#DBSCAN-clustering) |
| 曲线/骨架化 | 从已分割钢筋点恢复弯筋、桁架腹筋中心线 | 输入已较干净、采样连续 | 交叉点会产生拓扑歧义，且骨架本身不保留直径；只应作后处理。可参考带开源实现的 [TriplClust/IPOL](https://www.ipol.im/pub/art/2019/234/)；L1-medial/拉普拉斯收缩类方法通常工程依赖更重 |

另一个可直接试跑的钢筋仓库是 [DTU-PAS/Rebar-segmentation-Ransac](https://github.com/DTU-PAS/Rebar-segmentation-Ransac)。它面向裸露钢筋，使用 RealSense D435i、点云与深度图，测试设置为约 11 mm 钢筋、相机距 40--60 cm。名称虽含 RANSAC，但其 [`fitLineRANSAC`](https://github.com/DTU-PAS/Rebar-segmentation-Ransac/blob/main/catkin_ws/src/rebarsegmenation/src/utils.cpp#L45-L56) 实际调用的是 OpenCV `cv::fitLine(..., DIST_L2, ...)`，不是逐筋 RANSAC；平面部分才使用 PCL RANSAC，并带有 20 mm/有符号高度带等场景硬编码。README 中“直径、深度、中心线、钢筋端点”仍列为 TODO，环境锁定 Ubuntu 20.04 + ROS Noetic + PCL 1.10，又没有许可证和原始数据，适合读算法结构，不适合直接作为生产依赖。

多基本形状检测还可参考 [CGAL Shape Detection 官方实现](https://github.com/CGAL/cgal/tree/main/Shape_detection)：Efficient RANSAC/区域生长支持平面和圆柱，技术成熟，但相关模块标注为 GPL-3.0-or-later 或商业许可，闭源产品需先做许可证评估。Python 快速试验可用 [pyRANSAC-3D](https://github.com/leomariga/pyRANSAC-3D)，但维护者在圆柱实现中明确警告当前版本在真实数据上效果不好，因此只建议用它验证平面/直线流程，不应据其圆柱结果做工程判断。

## 6. 推荐组合路线

### 路线 A：无标注几何 PoC（2--3 周）

1. 传感器标定、ROI 裁剪、统计/半径离群点去除；保留原始高分辨率点用于最终量测，另建下采样副本用于检测。
2. 法向受限 Plane-RANSAC 找板面/底模，变换到板局部坐标；按 `z` 直方图或 GMM 分下层网片、上层/桁架和设备横杆。
3. 计算多尺度 PCA 特征；在每个高度带内对局部轴向做球面/角度直方图，提取两组主要网片方向和桁架方向。
4. 每个方向单独做 Line-RANSAC 或迭代 Hough；以“离轴距离 < 约 0.6--0.8×名义直径、方向差、长度、层高、设计间距”为门控。
5. 在同轴、间隙短的线段间做 axial bridging；交叉区不要用最近邻抢点，而应允许点对候选轴软归属，最终以全局平行性、周期性和 BIM/设计表优化。
6. 若多视角表面足够完整，再在中心线法平面做稳健圆/椭圆拟合；否则输出名义直径类别，不输出伪精确连续直径。

### 路线 B：语义网络 + 几何实例化（推荐生产方向，6--10 周）

- 主模型先做 `PTv3-S`（Pointcept）和 `PointNeXt-S` 二选一/对照。输入至少 XYZ；若传感器提供可靠 RGB/强度，再加入 RGB/reflectance、归一化高度、法线/线性度。
- 使用重叠滑窗/patch 推理，模型只负责点级语义；将高置信钢筋点回映到原始分辨率，再运行路线 A 的方向、中心线和规则模块。
- 损失使用 class-balanced CE + Lovasz 或 Dice/Focal，重点提高钢筋召回和边界；训练增强必须包括点丢失、距离相关噪声、随机遮挡、不同密度、反射缺失、旋转和尺度扰动。
- 设计/BIM 若可获得，注册后作为软先验：预期数量、两组方向、名义直径集合、间距范围、桁架位置。不要让设计模型直接覆盖实测异常，否则会“校正掉”真正缺筋/错位。

### 路线 C：OneFormer3D / 3D 实例网络（并行研究）

- 先复现官方 ScanNet/S3DIS 数据格式，再以每根钢筋 `instance_id` 训练；使用合成数据扩大形状、直径、锈蚀、背景、遮挡和点密度变化。
- 输出后仍需做轴线拟合、合并/拆分纠错和工程规则检查。实例 AP 高并不保证间距最大误差、漏筋率满足验收要求。
- 算力预算至少按 24 GB 显存估；若交付周期紧，不应让其阻塞路线 A/B。

## 7. 数据采集与分辨率建议

以下是 PoC 的工程起点，不是传感器宣传页上的标称值替代品，必须用实测点云验证：

- 先确定最小钢筋直径 `d_min`。检测用点云的横向点间距建议 `<= d_min/3`，即可见直径上至少约 3--5 个有效采样；要连续估直径而非只分名义规格，应争取更密采样和多视角圆弧覆盖。
- 重复扫描标准球/圆柱和板面，要求工作距离内深度重复性/平面 RMSE 初步达到 `<= 1--2 mm` 或 `<= 0.1 d_min`（取更严格者）。否则只承诺中心线/间距，不承诺毫米级直径。
- 结构光/RGB-D 先将工作距离控制在约 0.4--0.5 m 做局部扫描；Buildings 2024 在 600 mm 的保护层测量误差明显增大，而 500 mm 内较稳。大板应依靠移动龙门/机器人和配准，不要为“一帧覆盖整板”牺牲点距。
- 每块板至少采集一个近法向视角和两个相反方向的 30--45° 斜视角；保证相邻站位充分重叠并布置刚性标靶/编码点。单顶视角只能看到上半表面，下层筋和交叉处会系统性缺失。
- 同步保存原始 RGB、深度、点云、内外参、曝光/距离、时间戳、工单/BIM、名义直径和人工尺量真值。湿润、锈蚀、强光/阴影、不同底模颜色、遮挡和设备杆件必须成为数据分层变量。

## 8. 标注、划分与指标

### 标注

- 语义标签按第 2 节的组件类别；实例标签以**物理钢筋**为单位，跨越交叉点、短时遮挡仍保持同一 ID。
- 额外保存中心线 polyline、名义直径、上下层/网片方向、可见性和 `occluded/intersection/corrosion/glare` 难例属性。
- 可用几何基线或 BIM 注册预标注，再人工修正；合成数据可自动给语义和实例标签，但至少保留一套纯真实测试集。
- 数据集按“板件/生产批次/日期”划分，不能随机把同一块板的相邻 patch 分到训练和测试，否则会严重泄漏背景与排布。

### 建议 PoC 数据量

- 无标注阶段：先采 8--12 块板，覆盖 2--3 种规格和极端光照/湿润状态，用于传感器与几何可行性判断。
- 语义阶段：建议 30--50 块独立板件、每块多个站位；至少 5--10 块完全留作跨日期测试。先精标 100--200 个局部场景/patch，再依据错误分布主动补标。
- 实例网络阶段：真实数据仍不足时，采用 Blender/BIM 合成，但随机化传感器点丢失、遮挡、配准误差和现场非钢筋杆件；不能只改变材质和灯光。

### 评价指标

- 点级语义：钢筋 Precision、Recall、F1、IoU，类别 mIoU；因背景占比大，不以 overall accuracy 为主指标。
- 实例：AP50、`mAP@[.50:.95]`、PQ，以及每根筋的 missed/merged/split rate、数量误差和实例完整度。
- 几何验收：中心线 Chamfer/横向 RMSE，间距 MAE/P95/最大绝对误差，层高/保护层误差，名义直径准确率，缺筋/错位的 Precision/Recall。
- 工程指标：每平方米处理时间、峰值显存/内存、人工复核时间，以及按距离、角度、锈蚀、湿润、遮挡、筋径分层后的最差组结果。

建议 PoC 的初始过线条件可设为：钢筋点 Recall `>= 97%`、IoU `>= 90%`；单筋漏检率 `< 1%`、合并/拆分率 `< 2%`；间距 MAE `<= 3 mm` 且 P95 `<= 5 mm`。这些阈值应再按企业验收规范和最小允许偏差调整，不能仅复制论文指标。

## 9. 最小验证矩阵与停止条件

用同一批真实测试数据并排比较四条基线：

1. Plane + height + PCA + Line-RANSAC。
2. Plane + height + iterative Hough。
3. PointNeXt/PTv3 语义 + 1 的几何实例化。
4. RGB 2D segmentation + depth projection + 1 的几何实例化。

只有在 3/4 的真实难例指标明显优于几何基线时，才进入 OneFormer3D 实例网络。若原始点云在 `d_min` 上不足 3 个稳定横向点、600 mm 附近误差已经接近验收容差，或相反斜视角仍看不到下层筋，应先改采集系统；继续换网络不会恢复传感器未采到的几何信息。

## 10. 可执行的技术选型

- **最快落地栈：** Open3D/PCL + NumPy/SciPy/scikit-learn，先完成平面、高度、PCA、Line-RANSAC/Hough、轴线和量测。
- **推荐学习栈：** PyTorch + Pointcept/PTv3；若安装/显存压力过大，退到 PointNeXt/OpenPoints。
- **RGB-D 备选：** OpenMMLab MMSegmentation 或当前维护的 YOLO segmentation 实现，mask 投影到原始深度；不要依赖论文中的旧 Python/CUDA 组合。
- **实例研究栈：** OneFormer3D Docker 隔离部署；与业务环境解耦，避免 mmdetection3d/spconv/MinkowskiEngine 版本锁污染主工程。

总体判断：这个场景的优势是结构规则、工位相对固定、浇筑前完全可见；难点是密集连接与传感器遮挡。它非常适合“几何先验增强的分割”，而不是把它当作一个没有工程结构的通用点云分类题。

## 11. 几何 PoC：可执行参数、前置条件与证据边界

为了避免把某篇论文在特定试件上的常数误当作本项目默认值，以下统一使用：清洁钢筋 ROI 的中位最近邻点距 `s50`、最小名义筋径 `d_min`、最小设计筋距 `p_min`、平面残差稳健尺度 `sigma_plane = 1.4826 × MAD`、当前估计平面内点比例 `w_hat`。长度一律先转换为米；论文中的毫米常数仅作为对照。

RANSAC 迭代数按期望成功率计算，而不是抄固定轮数：

```text
N = ceil(log(1 - confidence) / log(1 - w_hat^sample_size))
```

其中平面 `sample_size=3`、直线 `sample_size=2`。例如 Buildings 2024 根据“约 75% 点来自底模、其中约 40% 可用于拟合”估计 `w_hat≈0.30`，在 `confidence=0.99` 下得到约 170 次平面采样；单根筋约占层内点的 10% 时，直线约需 460 次。这只是该试件的数据比例，不是跨工位常数。[Buildings 2024 全文](https://doi.org/10.3390/buildings14113693)

| 步骤 | 已发表事实或官方 API 事实 | 本项目 PoC 的工程假设与首轮参数化方式 | 必须满足的失败门槛 |
|---|---|---|---|
| 单位、精度、坐标原点 | laspy 的小写 `x/y/z` 已应用 LAS header 的 scale/offset，大写 `X/Y/Z` 是原始整数；LAS 点格式不保证一定有 RGB、强度或分类字段。[laspy 教程](https://laspy.readthedocs.io/en/latest/complete_tutorial.html) | 输入契约明确为米；先以 `float64` 应用 scale/offset 并减去局部原点，再进入任何 `float32`/GPU 或下采样步骤；逐文件记录 CRS、scale、offset、可用维度 | 非有限值、单位/场景尺度不可信、局部坐标转换不可逆时直接拒绝，不返回“成功但无实例” |
| 点密度可辨识性 | ISARC 2012 的 TLS 实验把钢筋上的采样间隔控制在 `<4 mm`，并用约 `1.5--2.0×` 点距处理噪声；其后又使用 5 mm 栅格。这些数值只适用于其扫描仪和试件。[论文 PDF](https://www.iaarc.org/publications/fulltext/Shape_recognition_with_point_clouds_in_rebars.pdf) | 每个距离/入射角分层统计 `s50` 和 P95，而不是只看全场平均。`s50 <= d_min/3` 作为本项目采集 PoC 的保守起点，属于工程假设，不是论文定律 | 若最细钢筋横向长期少于约 3 个稳定采样，先改传感器距离/视角；算法不得承诺可靠直径 |
| 检测副本下采样 | Open3D `voxel_down_sample` 会生成体素均值点；`voxel_down_sample_and_trace` 还能返回原始点索引映射。[Open3D 0.18 PointCloud API](https://www.open3d.org/docs/0.18.0/python_api/open3d.geometry.PointCloud.html) | 仅在检测副本上扫描 `{s50, 1.5s50, 2s50}` 量级的 voxel，并裁剪到明显小于 `d_min`；量测始终回到原始分辨率。需要点级证据时必须使用 trace 或自建索引映射 | 下采样后单根筋断裂、相邻筋合并，或无法映回原始点时禁用该尺度 |
| 底面拟合 | Buildings 2024 使用 `0.8×设计保护层` 作为其底模平面距离阈值；Open3D `segment_plane(distance_threshold, ransac_n, num_iterations, probability)` 只提供平面 RANSAC。[Open3D 0.18 API](https://www.open3d.org/docs/0.18.0/python_api/open3d.geometry.PointCloud.html) | 首轮按 `2/3/4 × sigma_plane` 扫描阈值，并以法向先验、内点比例和重拟合 RMSE 选取；迭代数由上式和实测 `w_hat` 决定。现有 `3 mm/500 次` 仅是合成测试默认值 | 阈值接近下层筋离板面的有效净距、平面内点不足，或残差呈明显翘曲/多平面时停止；不能继续把所有高度解释成保护层 |
| 上下层与高度带 | Buildings 2024 在自身双层试件上用 `50 mm` 分层，并随机取 1000 点求层高；其保护层公式还包含 `1.2×下层名义直径` 修正 | 优先依据高度直方图/GMM 峰和设计/BIM 的层高、筋径范围确定高度带；固定 `8--80 mm` 只能作为当前单层合成网片的显式配置，不应成为现场隐式默认值 | 两层峰不可分、下层被遮挡，或设备杆件与钢筋同高时，要求额外语义/ROI 证据并降低结果置信度 |
| 局部 PCA 与线性度 | PCA 线性度定义和 Open3D/SciPy 邻域 API 可复用，但相关钢筋论文没有给出可跨传感器复制的统一半径 | 对半径和邻居数做密度归一化扫描；候选半径应大于局部点距/杆宽的噪声尺度、又明显小于 `p_min`。现有 `32 mm` 半径、最少 7 邻居、线性度 `0.55` 是合成夹具契约，需用真实 patch 校准 | 半径变化一档就造成方向峰/实例数突变，或交叉点污染占主导时，不通过稳定性检查 |
| 主方向估计 | 密集网片具有两组强方向是场景先验，不是所有桁架筋/弯筋的性质 | 对无向轴使用双角度直方图；首轮比较 `5°/8°/12°` 容差，并把“两组近正交方向”作为网片约束。现有 180 个 bin、最多 2 方向、`12°` 是 PoC 默认值 | 存在第三主方向、曲线/桁架腹筋，或两峰不能稳定分开时必须转到多方向/曲线分支，不能强压成正交网格 |
| 单筋实例化 | Buildings 2024 的 Line-RANSAC 阈值略大于半个名义直径；剩余点低于层内初始点的 6% 时停止，并丢弃少于平均单筋点数 40% 的候选 | 直线 RANSAC 若采用，阈值按名义半径、实测噪声共同标定。当前实现并非逐筋 Line-RANSAC，而是“局部 PCA 轴向支持 + 横向 offset 聚类 + 管状距离门控”，应以真实标注分别验证 merge/split | 任一候选只靠交叉点支撑、长度不足、偏离主方向，或 offset 周期性不可信时丢弃；不得仅靠 DBSCAN 连通性命名单筋 |
| 断点桥接与交叉软归属 | 论文证明遮挡需要桥接，但没有可迁移的统一最大断裂长度 | 仅在同一候选轴、方向/横向偏移/层高均一致时允许桥接；gap 上限按已知遮挡物尺寸和 `p_min` 扫描。当前 `130 mm` 仍只在合成遮挡上得到覆盖 | 对 gap 阈值敏感、桥接跨过不同端头，或设计上允许真实搭接/断筋时输出歧义，不强行合并 |
| 圆柱/直径 | Buildings 2024 明确观察到结构光钢筋点更像带状平面，因而无法可靠圆/圆柱拟合；2026 ECG 路线则以高分辨率 TLS 圆柱可见性为前提 | 先计算法截面角度覆盖和多视角一致性；不满足时只输出中心线/名义规格候选。连续直径估计作为独立能力验收 | 单侧可见弧、点距/噪声接近直径差、肋纹主导或拟合置信区间过宽时禁止输出伪精确直径 |

因此，参数搜索的交付物不应只是“一组默认值”，还应包含每个真实 patch 的点距分布、平面残差、方向峰、阈值敏感性、被拒绝原因和原始点证据索引。这样才能区分采集失败、模型前提不成立和参数失配。

另外两组已发表参数适合用来设计扫参范围，但不能直接成为默认值：Sensors 2018 在 Leica HDS6100 误差仿真上使用 35 mm 球邻域，并以归一化特征值 `k_min<0.04、k_max>0.73` 判线性点；这些阈值来自特定传感器的 Monte Carlo，不等同于当前实现的 `(lambda1-lambda2)/lambda1` 线性度，也不能移植到 Gemini/LAS。[Sensors 2018](https://doi.org/10.3390/s18030819) Buildings 2026 在 `phi16--phi20` 柱笼的 18 组 BIM--TLS 数据上固定了 0.5° 角度桶、5 mm 高度桶、20 mm PCA 半径/最少 15 邻居、12° NMS 等值，但没有 held-out 调参集；它们只能说明一个完整参数集如何记录，不能用于本项目的平面细筋默认值。[Buildings 2026](https://doi.org/10.3390/buildings16020338)

## 12. 本仓库 Python 栈：可直接使用的 API 与限制

仓库当前固定 [NumPy 1.26.4、Open3D 0.18.0、laspy 2.5.4、SciPy 1.13.1](../../services/mesh-service/requirements.txt)。下表以这些固定版本为准，而不是“latest”文档：

| 组件 | 可直接使用 | 与本实现有关的限制/建议 |
|---|---|---|
| Open3D 0.18 | `segment_plane`、`estimate_normals`、`remove_radius_outlier`、`remove_statistical_outlier`、`cluster_dbscan`、`voxel_down_sample_and_trace` | legacy `PointCloud` 有平面分割，没有法向受限的平面采样，也没有直线/圆柱 RANSAC；法向先验要在候选拟合后检查，实例直线需自写、PCL/CGAL 或当前 offset 法。法向估计应显式给 `KDTreeSearchParamHybrid(radius,max_nn)`，而不是依赖 KNN=30 默认值。普通 `voxel_down_sample` 不保留逐点回映。DBSCAN 会预计算 epsilon 邻域，`eps` 大时可能占用大量内存，[官方教程](https://www.open3d.org/docs/release/tutorial/geometry/pointcloud.html#DBSCAN-clustering)也明确警告这一点，故不能在整张密集网片上盲跑 |
| SciPy 1.13.1 `cKDTree` | `query(..., workers=-1)` 可算最近邻点距；`query_ball_point(..., workers=-1)` 可做局部 PCA 邻域。[cKDTree](https://docs.scipy.org/doc/scipy-1.13.1/reference/generated/scipy.spatial.cKDTree.html)、[query](https://docs.scipy.org/doc/scipy-1.13.1/reference/generated/scipy.spatial.cKDTree.query.html)、[query_ball_point](https://docs.scipy.org/doc/scipy-1.13.1/reference/generated/scipy.spatial.cKDTree.query_ball_point.html) | 树通常引用连续的 double 数据，建树后修改底层数组会使结果失真；不确定生命周期时用 `copy_data=True`。`query_ball_point` 返回逐查询点的邻居列表，最坏情况下内存接近所有邻接边；大云按带 halo 的块查询，或先用 `return_length=True` 做容量/密度检查。测量/回归验收使用精确搜索 `eps=0` |
| laspy 2.5.4 | `laspy.open()` 可只读 header，`chunk_iterator()` 可流式处理大 LAS/LAZ；小写坐标自动应用 scale/offset。[基础文档](https://laspy.readthedocs.io/en/latest/basic.html)、[LasReader API](https://laspy.readthedocs.io/en/latest/api/laspy.lasreader.html) | `laspy.read()` 会整体载入；大文件不应默认使用。`header.parse_crs()` 可能返回 `None`，且本仓库未固定其 pyproj 依赖，header scale 也不是可靠的物理单位声明。LAZ 需要可选 backend，本仓库 extras 同时固定了 `lazrs/laszip`；lazrs 不支持 waveform，laszip 可支持但单线程。[安装说明](https://laspy.readthedocs.io/en/latest/installation.html)；RGB/intensity/classification 均需先检查 point-format dimensions，分块 PCA/实例化必须为块边界增加 halo |

截至本快照，[几何 PoC 核心](../../services/mesh-service/algorithms/rebar_segmentation.py)只依赖 NumPy/SciPy，并没有调用 Open3D 或 laspy；[文件适配层](../../services/mesh-service/rebar_poc.py)则已用 laspy 分块读取 LAS/LAZ，并用 Open3D 解码 PLY/PCD。这让纯数组核心容易测试，也把格式、限流和索引回映隔离在边界层。坐标必须保持 `float64` 并先减局部原点再进入任何 `float32`/GPU 路径，否则大地坐标下的毫米级差值可能已丢失；CRS、RGB、强度等属性保存仍待补齐。

## 13. 公开代码与数据：许可证和可复现性审计

“GitHub 可见”不等于“允许复制进产品”。GitHub 官方说明：仓库没有许可证时适用默认版权，其他人通常无权复制、分发或制作衍生作品。[GitHub licensing a repository](https://docs.github.com/en/repositories/managing-your-repositorys-settings-and-features/customizing-your-repository/licensing-a-repository)

| 资产 | 可获取内容与复现状态 | 许可证结论（截至 2026-09-05） | 本项目处置 |
|---|---|---|---|
| [ISARC 2025 钢筋合成数据论文](https://www.iaarc.org/publications/fulltext/147_A_Synthetic_Data_Generation_Pipeline_for_Point-Cloud-Based_Rebar_Segmentation.pdf) | 论文给出 238 个合成样本、190/48 划分和 12 个真实测试样本，并列训练超参；未发现作者数据、Blender 管线或 rebar checkpoint 下载。正式 proceedings 的真实集 `mAP@[.50:.95]=92.1`，而作者项目页/海报出现 95.4，故以正式论文为准 | 论文公开不等于训练资产获许可；无法端到端复现实文 `92.1 mAP` | 可复现其思路和自建数据协议，不把该指标当成本仓库回归基准；论文提出的 DBSCAN/形状约束只是 future work，不是已经验证的后处理 |
| [OneFormer3D](https://github.com/filaPro/oneformer3d) | 官方 Docker/配置/通用数据集说明存在，可复现通用 ScanNet 等流程；缺少上述钢筋论文的生成资产与 checkpoint | 官方代码 [CC BY-NC 4.0](https://github.com/filaPro/oneformer3d/blob/main/LICENSE)，未经另行许可不适合作为商业产品依赖 | 只做隔离研究对照；商业化前取得授权或换许可证兼容实现 |
| [Infinigen](https://github.com/princeton-vl/infinigen) / [Poly Haven](https://polyhaven.com/license) | 合成场景生成器和 CC0 材质可获取，但需要自行建设钢筋参数化资产、传感器退化和实例标注导出 | Infinigen 为 [BSD-3-Clause](https://github.com/princeton-vl/infinigen/blob/main/LICENSE)；Poly Haven 资产 CC0，允许商业使用 | 可作为自建合成管线的许可友好底座，并保存每个具体资产的来源清单 |
| [DTU-PAS/Rebar-segmentation-Ransac](https://github.com/DTU-PAS/Rebar-segmentation-Ransac) | 有 C++/PCL/ROS 源码和环境说明；README 中直径、深度、中心线、端点仍是 TODO；无数据集/release | 根目录未见 LICENSE，不能直接复制/分发到产品 | 只读算法思路；若要采用实现，先向作者取得明确许可证 |
| [Improved Density Clustering ... Irregular Rebar Mesh](https://github.com/longzezhou/Improved-Density-Clustering-for-Spacing-Measurement-in-Irregular-Rebar-Mesh-from-3D-Point-Clouds) | 根目录有两个 PCD 与一个 RAR；RAR 内约有 25 个 MATLAB `.m` 文件/PCD，但无 README、入口、环境或测试，且源码提到的 `license.txt` 不在归档内，难以从干净环境复现实文流程 | 根目录与归档均未见正式 LICENSE；源码和点云不能默认再分发或派生 | 不纳入依赖或正式 benchmark；联系作者索要运行入口、数据说明和授权 |
| [synthetic-datasets-for-rebar](https://github.com/whiesty/synthetic-datasets-for-rebar) / [Buildings 2023 论文](https://doi.org/10.3390/buildings13030585) | release 提供约 2500 张 RGB 图及 mask/标签，但它是 **2D 图像数据**，不是 3D 点云训练集 | 仓库未见 LICENSE；论文开放许可不能自动替代独立数据包的授权声明 | 仅可评估 2D RGB 分支的数据形式；正式下载/训练前向作者确认数据许可 |
| [CGAL Shape Detection](https://doc.cgal.org/latest/Manual/packages.html#PkgShapeDetection) | 成熟的 Efficient RANSAC/区域生长，支持 plane/cylinder 等基本形状 | 官方包表将 Shape Detection 标为 GPL，也提供商业许可路径 | 闭源产品不直接链接 GPL 模块；采购商业许可或保留自研/许可兼容实现 |

许可证审计的直接结论是：当前几何 PoC 自研 NumPy/SciPy 核心最容易进入产品；OneFormer3D 和无许可证钢筋仓库只能做研究证据，不能因技术相关就复制代码。任何后续数据集都应登记文件级来源、版本/哈希、用途、可分发性和模型训练衍生权。

## 14. 2026-09-05 仓库实施状态、证据与下一步

本节记录的是当前仓库实施快照，而不是论文路线的完成度。

| 能力 | 当前状态 | 可核验证据 | 尚缺内容 |
|---|---|---|---|
| 几何数组核心 | **已有独立 PoC，尚未形成产品功能** | [rebar_segmentation.py](../../services/mesh-service/algorithms/rebar_segmentation.py)；输出 schema 为 `rebar-geometric-poc-v2` | 覆盖单个近似平面、充分分离的直线主方向和同轴多高度层；弯筋、桁架腹筋拓扑和直径仍未实现 |
| 已实现步骤 | 尺度/有限值检查、RANSAC 置信度契约、法向受限平面与 SVD 重拟合、高度带、分批有界邻居 PCA、无向主方向、表面双条纹合并、同轴高度分层、同方向证据桥接、跨方向软归属、中心线和间距摘要 | 核心模块及其 diagnostics/point-support 索引；对抗审查后修复了交叉链伪桥接、多层幽灵中心线、双边表面拆筋、近二次邻域内存和方向编号翻转 | 当前实例化不是第 6 节所写的逐筋 Line-RANSAC；`min_axis_spacing` 等物理先验仍必须由设计和真实标注校准 |
| 自动测试 | **核心、I/O、CLI 与 API 契约测试已通过** | 2026-09-05 运行 `test_rebar_segmentation.py test_rebar_api.py`：36/36 通过；覆盖 3+4 根正交直筋、噪声/设备杆、短/宽遮挡、交叉软归属、双高度层、双表面条纹、非正交拒绝、错单位/稀疏/非有限输入、LAS/PLY、跨块采样、路径边界、防覆盖输入、API 错误与 429 | 尚无带人工真值的真实 missed/merged/split、中心线和间距回归；合成通过不能证明现场准确率 |
| 文件 I/O 与索引回映 | **已实现 PoC 适配层** | [rebar_poc.py](../../services/mesh-service/rebar_poc.py)：LAS/LAZ 分块读取、稳定 stride/跨块体素、检测点到 reader record 映射及原子 JSON；PLY/PCD 可读 | 尚未保留/导出 CRS、RGB、强度等属性；PLY/PCD 仍整体物化；量测尚未回到原始高分辨率邻域 |
| 服务/前后端集成 | **mesh-service 已接，产品链路未接** | [rebar_api.py](../../services/mesh-service/rebar_api.py) 与 `/rebar/segment`；复用共享重任务 gate，含存储边界、Pydantic 和单请求工作量限制；见 [运行手册](../rebar-segmentation-poc.md) | Go 鉴权代理、数据库结果、进度/取消、前端点云/中心线复核与导出未实现 |
| 真实无标注试跑 | **可重复运行，未达到准确率验收** | 当前 9,216,369 点 LAS 稳定限流到 196,093 点；完整 CLI 墙钟约 2.85 s、峰值 RSS 约 188 MiB；两次 JSON 哈希一致；板面 RMSE 0.713 mm、方向约 -0.09°/89.93°、输出 50 实例 | 无人工单筋 ID，`50` 不是确认数量；PCA 半径 24/28/32 mm 时方向内轴数和间距跳变，未通过邻档稳定性门槛 |
| 学习路线 B/C | **调研完成，未开始训练** | 第 3、4、13 节 | 真实标注、许可决策、GPU 环境、基线 checkpoint、独立测试集 |

### 下一阶段执行顺序

1. **先冻结真实数据契约。** 选 2--3 个可脱敏代表性 LAS/LAZ 小片：正常网片、锈蚀/反光、遮挡/设备杆；随文件记录 CRS/单位、scale/offset、传感器距离/角度、名义筋径/间距及人工单筋 ID。没有这些证据，不继续“调默认参数”。
2. **把当前真实 LAS 切成可复核 patch 并标注。** 先标正常网片、反光/锈蚀、遮挡/设备杆三类；用现有 CLI 固定 seed 和完整参数，报告点级 recall/IoU、单筋 missed/merged/split、中心线 RMSE、间距 MAE/P95 及邻档敏感性。
3. **回到原始分辨率量测。** 当前检测点已有 reader record 映射；下一步围绕候选中心线回读原始 LAS 邻域，保存属性/CRS 和点证据，不使用 stride/体素代表点直接给毫米量测。
4. **真实验收后再接 Go/UI。** 为版本化结果增加鉴权代理、任务取消/进度、持久化、原始点/中心线叠加、人工拆分/合并和导出；不要把未校准阈值变成静默业务默认值。
5. **最后并行学习路线。** PTv3/PointNeXt 仅在几何基线暴露出可量化的语义混淆后投入标注；OneFormer3D 只做隔离研究，先解决 CC BY-NC 许可和钢筋论文资产不可复现问题。

### 几何 PoC 进入集成前的最小验收清单

- 三类真实 patch 均可重复运行，固定 seed 的 JSON 除计时字段外稳定；单位错误和前提不成立会返回明确失败。
- 原始点索引/属性到实例和中心线可追溯；下采样不会把相邻钢筋合并且量测不使用体素中心代替原始点。
- 参数在相邻档位下不会导致实例数大幅跳变；若跳变，diagnostics 能定位到点距、平面、高度、方向或桥接阶段。
- 交叉处允许共享证据但不整网粘连；短遮挡可桥接，真实端头/搭接不会被无条件合并。
- 真实集达到第 8 节经业务确认后的阈值，并单独披露最差距离、入射角、锈蚀/湿润和遮挡组，而不是只给总平均。
