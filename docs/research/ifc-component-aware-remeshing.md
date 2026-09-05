# IFC 构件感知重网格：调研与方案选择依据

> 调研日期：2026-09-05；范围：非理想 IFC tessellation（尤其非圆截面扫掠体）重网格后的网格质量，以及保留 IFC 构件树和逐构件几何/偏差数据。本文不包含实现或实测结论。证据标签：**资料确认** = 规范、官方文档、官方源码或原论文明确陈述；**源码推断** = 对本仓当前代码的可复核推论；**需实验验证** = 尚未在本仓 fixture 上测量。

## 结论先行

1. 当前管线会把完整 `model.glb` 合为一个 PyMeshLab 当前层，最后规范化成一份 PLY。**源码推断**：IFC `GlobalId`、构件边界、空间树、原 GLB node/primitive 与任何属性在这条输入/输出契约中均不能被保留。因此“网格各向异性”和“构件身份丢失”是两个独立问题，不能靠调大现有 `iterations` 一并解决。
2. 现有 PyMeshLab/VCG 的 *Isotropic Explicit Remeshing* 是以单一目标长度为中心的局部 split/collapse/swap/relax/project 流程；它可改善三角形质量和边长均匀性，却没有 IFC 构件 ID、face-patch map 或受约束边 property map 参数。**资料确认**：它可以仅处理已选面（`selectedonly`），而这不是构件分区标签传播机制。必须区分三角形形状质量和空间采样密度：非圆截面本身不是任一问题的充分根因；坏的输入三角化、只做中点细分、有限迭代及 feature/偏差约束才可能使其中一项或两项变差，具体主因必须测量。
3. 推荐的产品方向不是“全模型重网格后找回 ID”，而是**优先从 IFC 产出不可变的构件清单与层级，再逐构件（或显式的共享边界 patch）重网格**，为每个输出写独立统计与原始面参考。只有在转换器明确保证 node/primitive 与 `GlobalId` 的稳定映射时，GLB 才可作为等价入口。第一阶段保留双资产（原始 GLB + component manifest + component PLY/GLB），先交付可追溯 C2M；第二阶段如需跨构件连续网格，采用 CGAL patch/constraint 的受控 POC。不要把 Open3D 或 Instant Meshes 当作当前 isotropic + 元数据保持的替换品。

## 当前实现审计与两个问题的根因

### 已确认的当前数据流

| 环节 | 观察 | 证据标签与影响 |
|---|---|---|
| 后端输入 | `callRemeshService()` 固定读取资产目录的 `model.glb`，POST 给 mesh service。 | **源码推断**：输入已是渲染资产，接口没有 IFC 文件或 element manifest。 |
| mesh service | GLB 由 trimesh 读取；Scene 的各 geometry 应用 node transform 后由 `trimesh.util.concatenate()` **先合为一个 mesh**，再导出中间 PLY。算法随后 `MeshSet.load_new_mesh()`，若仍有多层又执行 `generate_by_merging_visible_meshes()`。 | **源码推断**：身份丢失的首个确定位置是 GLB → 中间 PLY，而不只是 PyMeshLab 滤镜内部；layer 不能作为构件树载体。 |
| `bim_isotropic_only` | 清理、可选 QEM，再调用显式 isotropic；后处理再 merge close vertices、3 轮仅 swap/smooth/project。 | **源码推断**：任何输入 primitive/node 分界没有传入 filter。 |
| 输出/前端 | 服务端强制 float32 PLY，Go 写 `mesh_remesh.ply`；只回传前/后 vertex、face 数。 | **源码推断**：PLY + 四个总数没有构件 ID、关系、每构件 deviation 或 provenance 字段。 |

### 本机 Git / 单资产数据契约抽检（非外部一手资料）

下列是根代理对本机历史和一份现存资产的审计结果；它们是**本机事实**，不是外部标准或算法质量证据。

- 当前仓找不到旧设计文档或可达旧提交；唯一找回线索在相邻参考工程 `/home/hong/hong_project/zhongjian/zhongjian-back`：`36bead8` 初版四算法（PyMeshLab Iso/Taubin、Open3D QEM/Laplacian；其中 Open3D QEM 有未定义变量 bug）；`525c1dca` 加入 CGAL polygon-soup repair + QEM + isotropic；`a4355f` 删除 Open3D/CGAL wrapper 与轻量算法，提交理由为实现收敛和镜像/构建成本，**不能解读为质量实测淘汰**；`b1044718` 用 `surface_dist_ratio=.2` 处理圆柱变形；`b16e0e` 硬化 sliver；`a20acfe` 添加 benchmark。
- 该单资产 metadata 顶层键为 `elements,idKey,projectId,tree`：有 21 个 `elements`、tree 有 20 节点；其 GLB 有 15 个 nodes/meshes/primitives，node GUID 映射至 15 个有几何构件，所有 GLB 层级均无 `extras`。这说明该资产的 metadata 与 GLB 之间已有一条**资产特定** GUID 对照，但不是当前 remesh job 的输入契约。
- 对应 remesh PLY 仅有 `x,y,z,vertex_indices`。生产链路为 GLB scene concatenate → PLY，PyMeshLab merge/clean → trimesh normalize，C2M 全程无 component label，metadata 不会传给 remesh。因此“标签丢失”是**已审计的数据通道事实**；“任何特定 GLB 都会如此”仍是需扩大样本验证的推断。
- production 与 `benchmark_remesh.py` 不是同一配方：生产 preprocessor midpoint subdivision 2 轮、QEM 仅输入 `>30k` 面且最小面数 1000、sliver 1 轮；benchmark 固定 subdivision 3 轮、总是 QEM/min 100、无 sliver；pure-isotropic benchmark 10 轮而生产 15 轮。既有 benchmark 把 `min_edge` 越小越好归类，指标方向错误；圆柱弦高也不能冒充通用 C2M 误差。仓内没有固定 fixture 或保存的版本化结果。故任何旧 benchmark 数字均不能用于本轮质量结论。
- 本机参考工程的实际转换器报告 `IfcOpenShell IfcConvert 0.8.4-e8eb5e4 (OCC 7.8.1)`；其 `ifc_bundle --help` 只暴露 input/meta/glb/ifcconvert/jobs/pretty/include-spaces/include-unassigned/progress，**不透传** mesher linear/angular deflection。故“先调 IFC tessellation”值得作为 POC，但当前产品链路不能直接配置：须先改 bundle/转换契约或改为逐元素解析。IfcOpenShell 0.8.5 的 geometry settings 文档可作为后续 API 资料，[官方设置文档](https://docs.ifcopenshell.org/ifcopenshell/geometry_settings.html)所述默认值不得硬套为该 0.8.4 二进制的已证实行为；需用 `IfcConvert --help` 或实验确认。

### 问题 A：必须拆开测的两种“各向异性”

**资料确认**：CGAL 对同类 incremental isotropic remeshing 的描述是 edge split、collapse、flip、切向松弛及向原表面投影；目标可为 uniform 或 curvature-adaptive sizing field。[CGAL PMP 手册](https://doc.cgal.org/latest/Polygon_mesh_processing/index.html#Chapter_PolygonMeshProcessing) 也明确说明，迭代数越多，网格才趋近目标长度。PyMeshLab 的官方 filter 文档定义了同类的重复 edge flip/collapse/relax/refine，目标为提高 aspect ratio 与拓扑规则性，并公开 `iterations`、`adaptive`、`selectedonly`、`targetlen`、`featuredeg`、`checksurfdist`、`maxsurfdist`、split/collapse/swap/smooth/reproject 等参数。[PyMeshLab filter 文档](https://pymeshlab.readthedocs.io/en/latest/filter_list.html#meshing-isotropic-explicit-remeshing)

**资料确认（本仓版本与参数）**：`services/mesh-service/requirements.txt` 固定 `pymeshlab==2023.12.post2`。当前 `bim_isotropic_only` 实际传入：`iterations` 1–20（默认 15）、`adaptive`（默认 true）、`featuredeg`（60°）、`checksurfdist` true、split/collapse/swap/smooth/reproject true、绝对值包装的 `targetlen` 与 `maxsurfdist=t×surface_dist_ratio`，接着执行 merge-close-vertices 和无 split/collapse 的三轮松弛。PyMeshLab 的 `AbsoluteValue`/`PureValue` 包装仅影响参数单位表达；它不是各向异性 sizing field。现有代码的 “`[0.8t,1.33t]`” 注释/tooltip 是本仓断言，尚不能当作本模型实测保证。

**A1：元素形状各向异性（拉长三角形/角度差）。** 这是单个三角形的 aspect ratio、最小角、最大角或 edge ratio 问题，不等同于高曲率处有更多小面。**源码推断**：当前默认 `bim_preprocessor` 先可选 QEM，再执行 `meshing_surface_subdivision_midpoint`（中点细分）。中点细分保留父三角形的相似形，不能单独修复瘦长三角形；之后 isotropic 默认仅 5 轮，因而没有证据说明所有初始坏形状均已收敛。特征/边界与表面距离约束会合理地限制 relax、collapse 或重投影可移动的空间，使狭窄区域、端帽和棱边邻域残留差形状。`bim_isotropic_only` 虽默认 15 轮，但 API 默认算法是 `bim_preprocessor`，两者不可混为当前默认表现。

**A2：空间边长密度各向异性（不同位置/方向的采样密度）。** 这是沿扫掠轴、横截面、平面或高曲率区的单位面积顶点/边长分布问题。**资料确认**：CGAL 明确把 uniform sizing 与 curvature-adaptive sizing 区分开；后者在高曲率区域有更短的边，[CGAL PMP 手册](https://doc.cgal.org/latest/Polygon_mesh_processing/index.html#Chapter_PolygonMeshProcessing)。因此“曲率自适应导致密度不均”可能是正确、有意的结果，不应以 A1 的坏三角形判据判失败。**源码推断**：当前 PyMeshLab 参数 `adaptive=True`，但“adaptive”的具体 VCG 策略及它在本 IFC tessellation 上的方向/曲率效应尚未在本仓验证；不得把非圆截面或 adaptive 开关单独认定为根因。

**源码推断（两个问题的共同干扰项）**：

- 输入 GLB 的三角化密度、长窄三角形和扫掠方向可能已经不均匀；有限轮局部操作无法保证全局收敛。
- `checksurfdist`、`reprojectflag` 与 `featuredeg` 会优先限制位置改动/保护折线。保留棱边正确，但在翼缘厚度、角部、端帽和窄带周围与单一 `t` 不可同时满足时，A1 与 A2 都可能受限。
- QEM 在 isotropic 前改变拓扑，而 `merge_close_vertices` 可跨原构件边界合并近点；二者均会改变后续局部邻域。没有原始面到输出面的映射，不能将误差归因到某个构件或某类截面。

**需实验验证**：将 A1（最小角、aspect ratio、edge ratio）和 A2（按 surface class、位置及扫掠轴/横截面方向统计 `edge_length/t`、单位面积采样密度、p05/p50/p95/CV）分开报告；按平面、圆弧、非圆扫掠、特征边邻域分桶。不要只用全局面数或平均边长判定，也不要把非圆截面作为充分归因。

### 问题 B：构件树与独立几何/偏差不可追溯

IFC 不是扁平三角面集合。**资料确认**：`IfcRoot.GlobalId` 是全软件世界唯一标识；`IfcRelAggregates` 定义空间分解，`IfcRelContainedInSpatialStructure` 将产品放入空间。buildingSMART 规定物理元素只应直接包含于一个空间结构，空间包含与空间分解共同形成项目树。[IfcRelContainedInSpatialStructure](https://standards.buildingsmart.org/IFC/RELEASE/IFC4_3/HTML/lexical/IfcRelContainedInSpatialStructure.htm)；[Spatial Structure 概念](https://standards.buildingsmart.org/IFC/RELEASE/IFC4_3/HTML/concepts/Object_Connectivity/Spatial_Structure/content.html)。

**资料确认**：IfcOpenShell geometry iterator 默认逐元素处理 3D geometry，返回该 shape 的扁平顶点和三角面；它支持 `include` 过滤和多核/缓存。可由 element 的 `GlobalId` 建立一一对应 manifest。[Geometry iterator](https://docs.ifcopenshell.org/ifcopenshell/geometry_iterator.html)；官方几何设置示例也显示 `IfcConvert --include=attribute GlobalId ...`。[IfcOpenShell geometry settings 源码](https://github.com/IfcOpenShell/IfcOpenShell/blob/v0.8.0/src/ifcopenshell-python/docs/ifcopenshell/geometry_settings.rst)。IfcOpenShell 的 `get_container()`、`get_contained()`、`get_decomposition()` 可取直接/间接容器与分解关系。[element utilities](https://docs.ifcopenshell.org/autoapi/ifcopenshell/util/element/index.html)

因此当前全局合并 + PLY 输出不是“metadata 漏导出”，而是**表示层与作业边界本身消除了 identity**。后续 C2M 即使能算全局距离，也不能可靠回答“此距离属于哪个 IFC 构件、相对该构件原始表面的偏差是多少”。

## 找回的旧资料状态

- 当前分支可见的历史只显示 mesh-service 于提交 `293e8d9` 引入，未发现本地 `docs/research` 中已有 IFC 构件感知 remesh 设计文档；相邻参考工程的具体找回状态和提交序列见上节。这些是 **本机 Git 审计**，而非官方算法资料。
- 现有代码中“基准证实”“最优”等注释及参考工程的参数变更是工作树线索，不是本调研可重现的证据；尤其生产/benchmark 配方不一致、`min_edge` 目标方向错误，且圆柱弦高不是通用 C2M 偏差。应在本方案 fixture 下重新生成版本化报告。

## 算法候选矩阵

| 候选 | 能力与证据 | 身份/边界能力 | 适配结论 |
|---|---|---|---|
| 继续 PyMeshLab/VCG，全模型 | 显式 isotropic 参数完整；`selectedonly` 仅限已有面选择。 | 无 IFC-aware property map；合层及 PLY 契约丢树。 | 仅作现有基线，不能满足可追溯性。 |
| PyMeshLab/VCG，逐构件 job | 同一 filter 每个构件独立运行。 | manifest 在 job 外保留；共享界面不会天然共形。 | 第一阶段可行、改动最小；需隔离/处理相邻构件接缝。 |
| CGAL `isotropic_remeshing`，逐 patch | `face_patch_map` 在新面生成时更新；`edge_is_constrained_map`、`vertex_is_constrained_map`、`protect_constraints`、`relax_constraints` 都是官方 named parameters。受选区的 patch boundary 默认 constrained；sizing function 可逐 edge 定义 target。 [API](https://doc.cgal.org/latest/PMP_Remeshing/group__PMP__local__remeshing__grp.html)；[sizing-field concept](https://doc.cgal.org/latest/Polygon_mesh_processing/classPMPSizingField.html)。 | 可把构件边界、特征边和输出 face 的 `component_id` 作为明确 property map；可采用 uniform、curvature-adaptive 或业务自定义 sizing。 | 推荐的第二阶段 POC；注意受保护约束边若过长，官方警告可能质量差甚至不终止，须先 `split_long_edges()`。 |
| Open3D | 官方 API 是 QEM decimation（目标三角数、最大误差、边界权重）和 vertex clustering；没有官方 `TriangleMesh` isotropic remesh API。 [API](https://www.open3d.org/docs/release/python_api/open3d.geometry.TriangleMesh.html)；[官方教程](https://www.open3d.org/docs/release/tutorial/geometry/mesh.html)。 | 无 IFC map/patch 机制。 | 不替换 VCG/CGAL；可用于现有清理、距离或简化对照。 |
| Instant Meshes | 原论文/官方仓库是 interactive field-aligned mesh generator，工作流先 orientation field 后 position field，并输出网格。 [原论文](https://igl.ethz.ch/projects/instant-meshes/instant-meshes-SA-2015-jakob-et-al-compressed.pdf)；[官方源码](https://github.com/wjakob/instant-meshes)。 | 不是带 IFC metadata 的批处理 patch remesher；field alignment 目标也不同于等边 C2M 采样。 | 不进入首轮服务端候选；若追求 quad/方向场另立 POC。 |

## 构件保持架构三案

| 方案 | 表示与作业边界 | 优点 | 代价/边界 |
|---|---|---|---|
| A. Sidecar manifest + 每构件独立 mesh（推荐第一阶段） | IFC iterator 逐 element 导出；`components/{GlobalId}/input.*`、`remesh.*`、`metrics.json`；全局 `manifest.json` 保存 node tree、关系、transform、schema/version、源 IFC hash。 | 最清楚的 identity/误差归属；失败可重试单构件；原始 GLB 不变。 | 接缝不保证共形；小构件 jobs 多，需要打包/并发策略。 |
| B. 单 GLB、多 node/primitive + `extras` | 一个 node/primitive 对应 component，`node.extras.ifcGlobalId` 与 manifest 交叉校验。glTF base spec 支持 node hierarchy、mesh primitive 和任意 `extras`。[glTF 2.0 spec](https://registry.khronos.org/glTF/specs/2.0/glTF-2.0.html) | 渲染/选取直接，常规 Three.js 低门槛。 | `extras` 是应用数据，不是标准 IFC 语义；重网格后仍须维护 primitive 归属；单 primitive-node flattening 需测试。 |
| C. GLB feature IDs + `EXT_mesh_features` / `EXT_structural_metadata` | 顶点/纹理 feature ID 指向 property table，property table 存 GlobalId、IfcClass、统计。3D Tiles 官方 glTF 规范说明 mesh features 识别几何/子几何，structural metadata 可按 vertex/texel/feature 存元数据。[3D Tiles glTF](https://github.com/CesiumGS/3d-tiles/blob/main/specification/TileFormats/glTF/README.adoc) | GPU/feature-level 查询与 3D Tiles 生态路径。 | Three.js 官方 GLTFLoader 当前列出的内置支持扩展不含二者；需 `register()` 自定义 loader/plugin 或自己读取 extension。未知扩展虽可进 `userData.gltfExtensions`，但不是语义解码承诺。[GLTFLoader docs](https://threejs.org/docs/pages/GLTFLoader.html)；[官方源码](https://github.com/mrdoob/three.js/blob/master/examples/jsm/loaders/GLTFLoader.js)。不应作为第一阶段唯一真相源。 |

当前 PLY 也可以扩展自定义 vertex property，Three.js `PLYLoader` 提供自定义属性名映射；但本仓 normalize/export 会丢失这些属性，而且 face-level 构件归属无法仅靠共享顶点标签无歧义表达。因此 PLY 可作几何产物，不应单独承担构件树和 provenance 的真相源。[PLYLoader 文档](https://threejs.org/docs/pages/PLYLoader.html)

### 推荐的元数据最小契约

逻辑 `component_id = IFC GlobalId`；一个 component 条目至少记录：`ifcGlobalId`、`ifcClass`、`sourceEntityExpressId`（仅调试，不作跨文件键）、`name`、直接/推导空间路径、父聚合路径、source geometry hash、local/world transform、输入/输出 URI、算法与参数、输入/输出顶点/面数、每构件偏差统计、失败状态和 manifest schema version。由于一个 `IfcProduct` 可能含多个 representation item 或断开的几何体，几何分片另用稳定复合键，例如 `(GlobalId, representationIdentifier, itemId, partIndex)`，不要强迫“一构件仅一 mesh”。面级 provenance 应为输出 face → component ID/geometry-part ID；需要回溯时另存 output face → source triangle/barycentric 或最近点 reference，而不是从重网格后几何反推。

## 分阶段推荐方案

1. **阶段 0：冻结证据与接口。** 选定 IFC fixture，建立只读 component manifest（IfcOpenShell iterator + `GlobalId` + spatial/aggregate paths），保留原 IFC 和 `model.glb` 的 hash。定义 metric JSON schema；任何重网格均不得覆盖原资产。
2. **阶段 1：构件隔离 PyMeshLab 基线。** 每个可三角化 product 独立重网格，输出独立 PLY/GLB 和 metric；manifest 仅汇总引用。共享边界构件以“独立表面”标注，禁止 merge-close 跨 job。用原始/重网格双层显示与 GlobalId 选取验证树没有漂移。
3. **阶段 2：CGAL constrained-patch POC。** 选梁、柱、非圆扫掠管件和相邻构件接缝。将构件边界和识别的 crease 写入 `edge_is_constrained_map`，将 `GlobalId` 写入 `face_patch_map`，用统一/曲率/业务 sizing field 对照；记录约束预 split 与是否保护约束。只有质量、偏差、运行时间均过门槛才扩大。
4. **阶段 3：把 C2M 比较采样与展示/管理几何解耦（待实验）。** 保持原 IFC/GLB + 逐构件重网格产物服务于展示、选取和构件管理；C2M 另用逐构件面积均匀的表面采样，或直接使用 reference 的 scan-to-triangle 最近表面距离。这样不必为了 quick vertex-to-point 采样均匀性而强迫整个模型改拓扑。需要以同一基准比较采样误差、薄壁/特征漏检、速度和内存后才决定，不能假定其优于重网格。
5. **阶段 4：交付格式选择。** 若 Three.js 选取和逐构件报表已满足，采用 A + B（manifest 为真相源，`extras` 为便利索引）；只有确认使用 Cesium/3D Tiles 或完成 Three.js extension reader 后，加入 C。不要把 `extensionsRequired` 用于当前前端不能解码的 metadata extension。

## Benchmark、fixture 与量化门槛（待实验设定）

### Fixture 集合

- `F1`：规则 box/wall（平面与直角，校验不无故增密）。
- `F2`：圆柱与圆管（基准曲面）。
- `F3`：矩形、工字、槽钢或椭圆等非圆截面沿长路径扫掠（核心各向异性用例）。
- `F4`：短翼缘/薄壁/端帽/小倒角（特征与最小尺寸）。
- `F5`：两个相邻、共享/近邻边界但 GlobalId 不同的构件（防串号、接缝）。
- `F6`：真实 anonymized IFC 子集，含 Project/Site/Building/Storey/Space、aggregates 和 containment。

每个 fixture 固定 IFC、tessellation setting、单位、目标长度集合（例如构件尺度的 1%、5%、10%，具体数值经单位确认后确定）、算法版本与随机性设置；保存输入 hash，禁止以视觉截图代替数据。

### 指标与建议验收门槛

| 维度 | 测量 | 建议门槛（需产品确认/实验校准） |
|---|---|---|
| 均匀性 | 按方向和 surface class 统计 `edge_length/t` 的 p05/p50/p95、CV；三角最小角/最大 aspect ratio。 | 相对现有全局 PyMeshLab 基线，F3 的 p95/p05 和 CV 不变差；明确报告 feature 邻域豁免。不要预设“全部边落在某区间”。 |
| 几何保真 | output→input 与 input→output 的 sampled nearest-surface distance（max、p95、RMS）；按 component。 | p95/max 不超过已批准的模型容差，且独立于 filter 的局部 `maxsurfdist` 声称。 |
| 拓扑 | 非流形边/顶点、退化面、连通分量、边界长度；共享边界 gap/overlap。 | 不能新增非流形；F5 不得跨 GlobalId 焊接；阶段 1 接缝差须显式记录。 |
| identity | manifest GlobalId 覆盖率、唯一性、输出 face/component 覆盖、树路径可复算。 | 可几何化 product 100% 有且仅有一个 component ID；输出 face 100% 可归属；无跨 GlobalId face。 |
| C2M 可用性 | 每构件与全局 C2M distance 分布、失败率、运行时间/峰值内存。 | 相对原始网格不出现未解释的 p95 回归；预算由真实资产与部署资源确认。 |
| 比较采样 POC | 原 mesh、逐构件 remesh、面积均匀采样、scan-to-triangle 四路径对同一扫描的误差/漏检/耗时/内存。 | 只有逐构件 p95、特征邻域漏检率和资源预算不劣于批准基线时，才让解耦路径替代 C2M 的重网格依赖。 |

## 风险与待确认问题

- IFC 一个 `IfcProduct` 可有多个 representation/形体，opening、fill、assembly、type/occurrence 与空间/聚合关系是否作为独立 component，必须先写归属规则；`GlobalId` 仅对 root 实体稳定，Express id 不跨文件稳定。
- IFC tessellation 容差、world/local placement、单位和 GLB node transform 必须固定并记录，否则“偏差”混入坐标变换错误。
- 阶段 1 的独立构件会在共享面形成重复或不共形网格；若 C2M 仅对表面最近距离可接受，但若需 watertight/有限元邻接，应优先推进 CGAL patch POC。
- CGAL 约束保护与目标长度冲突是已知风险；官方要求/警告受约束长边需预分割，且过长会带来质量或终止风险。[CGAL API](https://doc.cgal.org/latest/PMP_Remeshing/group__PMP__local__remeshing__grp.html)
- PyMeshLab 2023.12.post2 的实际 filter 参数枚举、layer/selection 在服务 Docker 内的运行行为，及对 malformed GLB 的转换语义，仍需以本镜像的最小脚本和 fixture 验证；本文没有声称已实测。
- 面积均匀采样和 scan-to-triangle 的比较路径，必须确认 C2M 当前距离方向、符号/法向要求、加速结构和薄壁两侧语义；它是实验候选而不是已经批准的替代实现。
- Three.js 版本、当前 GLTFLoader 的 node flattening 和未知 extension 的 `userData` 留存，需写 importer contract test；不能仅凭规范假设 runtime 支持 `EXT_mesh_features`/`EXT_structural_metadata`。

## 来源（均为一手）

- [PyMeshLab 官方 filter list：Isotropic Explicit Remeshing](https://pymeshlab.readthedocs.io/en/latest/filter_list.html#meshing-isotropic-explicit-remeshing)
- [MeshLab 官方源码仓库](https://github.com/cnr-isti-vclab/meshlab)；[PyMeshLab 官方源码仓库](https://github.com/cnr-isti-vclab/PyMeshLab)
- [CGAL Polygon Mesh Processing 用户手册](https://doc.cgal.org/latest/Polygon_mesh_processing/index.html#Chapter_PolygonMeshProcessing)，[isotropic_remeshing API](https://doc.cgal.org/latest/PMP_Remeshing/group__PMP__local__remeshing__grp.html)，[sizing field 概念](https://doc.cgal.org/latest/Polygon_mesh_processing/classPMPSizingField.html)
- [IfcOpenShell geometry iterator](https://docs.ifcopenshell.org/ifcopenshell/geometry_iterator.html)，[element utilities](https://docs.ifcopenshell.org/autoapi/ifcopenshell/util/element/index.html)，[geometry settings 官方源码](https://github.com/IfcOpenShell/IfcOpenShell/blob/v0.8.0/src/ifcopenshell-python/docs/ifcopenshell/geometry_settings.rst)
- buildingSMART IFC 4.3：[IfcRelContainedInSpatialStructure](https://standards.buildingsmart.org/IFC/RELEASE/IFC4_3/HTML/lexical/IfcRelContainedInSpatialStructure.htm)，[Spatial Structure](https://standards.buildingsmart.org/IFC/RELEASE/IFC4_3/HTML/concepts/Object_Connectivity/Spatial_Structure/content.html)
- Khronos：[glTF 2.0 specification](https://registry.khronos.org/glTF/specs/2.0/glTF-2.0.html)，[glTF extensions 规则](https://github.com/KhronosGroup/glTF/blob/main/extensions/README.md)；[3D Tiles glTF feature/metadata 规范](https://github.com/CesiumGS/3d-tiles/blob/main/specification/TileFormats/glTF/README.adoc)
- [Three.js GLTFLoader 官方文档](https://threejs.org/docs/pages/GLTFLoader.html)，[GLTFLoader 官方源码](https://github.com/mrdoob/three.js/blob/master/examples/jsm/loaders/GLTFLoader.js)
- [Three.js PLYLoader 官方文档](https://threejs.org/docs/pages/PLYLoader.html)
- [Open3D TriangleMesh 官方 API](https://www.open3d.org/docs/release/python_api/open3d.geometry.TriangleMesh.html)，[官方 mesh tutorial](https://www.open3d.org/docs/release/tutorial/geometry/mesh.html)
- [Instant Field-Aligned Meshes 原论文](https://igl.ethz.ch/projects/instant-meshes/instant-meshes-SA-2015-jakob-et-al-compressed.pdf)，[作者官方源码](https://github.com/wjakob/instant-meshes)
