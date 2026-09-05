# 钢筋点云几何分割 PoC 运行手册

> 状态：实验性几何基线；mesh-service 与离线 CLI 可运行，尚未接入 Go 业务 API、数据库和前端。
>
> 输出用于算法筛选与人工复核，不构成钢筋数量、间距或直径的工程验收结论。

## 1. 已实现范围

当前版本 `rebar-geometric-poc-v2` 面向坐标单位为米、板面近似水平、主筋近似为两组充分分离直线的点云：

1. 校验点数、有限值、场景尺度和采样密度。
2. 带法向先验的 Plane-RANSAC 与 SVD 重拟合，建立板面局部坐标系。
3. 按板上高度带筛选候选，局部 PCA 提取线性点并估计无向主方向。
4. 按方向和横向 offset 形成物理轴，合并同一钢筋的可见表面双条纹。
5. 只使用同方向 PCA 证据桥接轴向短缺口，再扩充交叉处的共享支持点。
6. 同一 XY 轴按观测高度层拆分，输出中心线、长度、支持点、轴残差和轴间距摘要。

纯数组核心在 `services/mesh-service/algorithms/rebar_segmentation.py`；文件适配和 CLI 在 `services/mesh-service/rebar_poc.py`；HTTP 契约在 `services/mesh-service/rebar_api.py`。

## 2. 数据与索引契约

- 所有长度均为米。服务不会从 LAS header 的 scale 推断“米/毫米”；场景尺度不可信时直接失败。
- LAS/LAZ 用 `laspy.chunk_iterator` 流式读取，小写 `x/y/z` 已应用 header scale/offset。
- 默认最多向算法传入 200,000 个检测点。超限 LAS/LAZ 按全局原始 record index 做确定性 stable stride；显式给 `voxel_size` 时使用 header minimum 锚定并跨块去重。
- PLY/PCD 由 Open3D 整体解码后再限流；多百万点输入应优先使用 LAS/LAZ。
- `instances[*].support_point_indices` 属于**检测点顺序**。`input.point_index_contract.detection_to_source_index` 将每个检测点映回源文件 reader 顺序。
- 显式体素的唯一体素数超过点数上限时会按源顺序截断，并在 `input.sampling.voxel_selection_truncated` 中标记；此时存在空间偏置，不能用于正式量测。
- `centerline` 的高度是可见点的观测中位高度，不是经过半径补偿的物理钢筋中心高度。

## 3. 启动与 HTTP 调用

开发栈仍由仓库的一键管理器控制。首次构建或 mesh-service 源码变化后先重建镜像，再重启整栈：

```bash
docker compose build mesh-service
scripts/cloudbim-dev.sh restart
scripts/cloudbim-dev.sh status
```

mesh-service 默认只绑定本机 `127.0.0.1:18001`。请求中的路径必须是容器共享存储 `/storage` 下的绝对路径：

```bash
curl -sS http://127.0.0.1:18001/rebar/segment \
  -H 'Content-Type: application/json' \
  -d '{
    "point_cloud_path": "/storage/assets/95b6b41c5857d9eb3407b155/source.las",
    "max_input_points": 200000,
    "params": {
      "plane_ransac_iterations": 200,
      "plane_distance_threshold": 0.0025,
      "min_plane_inlier_ratio": 0.30,
      "ransac_confidence": 0.99,
      "min_rebar_height": 0.004,
      "max_rebar_height": 0.115,
      "height_cluster_gap": 0.015,
      "pca_radius": 0.028,
      "pca_max_neighbors": 64,
      "axis_distance_threshold": 0.012,
      "offset_cluster_gap": 0.020,
      "min_axis_spacing": 0.030,
      "bridge_gap": 0.20
    }
  }' > /tmp/rebar-result.json
```

这组参数只对应仓库当前样本的无标注试跑，不是生产默认值。RANSAC 最大轮数必须足以支持 `min_plane_inlier_ratio` 与 `ransac_confidence` 声明；API 还限制点数×RANSAC、点数×PCA 邻居和点数×方向桶的单请求工作量。

HTTP 状态语义：

- `400`：文件不存在、格式不支持、文件不可读或无有效点。
- `403`：路径或符号链接逃出共享存储。
- `422`：参数关系不合法、资源预算超限或几何前提不成立。
- `429`：remesh、C2M、精配准或另一钢筋任务正在占用单进程重计算门。
- `500`：未预期内部错误；响应不泄漏 traceback。

## 4. 离线 CLI

在仓库固定的 Python 3.11 虚拟环境中运行：

```bash
cd services/mesh-service
../../.cloudbim/mesh-venv/bin/python rebar_poc.py /absolute/path/to/scan.las \
  --max-input-points 200000 \
  --params-json '{"min_plane_inlier_ratio":0.30,"plane_ransac_iterations":200}' \
  --output-json /tmp/rebar-result.json
```

CLI 与 HTTP 共用同一加载器和算法；输出 JSON 通过同目录临时文件、`fsync` 和原子替换发布。`--storage-root` 可为离线批处理增加与 HTTP 相同的目录边界。

## 5. 结果读取

主字段：

- `schema_version`、`units`、`parameters`：版本、单位和本次完整有效参数。
- `input`：源文件计数、有限点计数、采样方法、检测点数及原始索引映射。
- `plane`：板面原点、法向、局部轴、方程、支持率与 RMSE。
- `directions`：稳定方向编号、有向显示角、轴数、offset、相邻轴间距和统计摘要。
- `instances`：实例 ID、方向、物理轴 offset、支持半径、观测高度层、中心线、长度、点数和轴残差。
- `point_sets`：平面、高度带、线性候选、钢筋支持及跨方向软归属的检测点索引。
- `diagnostics`：点距、场景尺度、候选数、实例数和支持点数。

`axis_count` 是不同 XY 物理轴数；同一轴存在多个明确高度层时，`instances` 数可以大于 `axis_count`。

## 6. 当前真实样本的无标注基线

仓库样本 `source.las` 有 9,216,369 点，包围盒约 `4.67 × 1.81 × 0.117 m`，没有可用 RGB 或 intensity。以上述参数和 200,000 点上限运行：

- stable stride 后 196,093 个检测点；两次完整 JSON 的 SHA-256 完全一致。
- 墙钟时间约 2.85 秒，峰值 RSS 约 188 MiB；结果 JSON 约 7.6 MiB。
- 板面支持率 56.27%，平面 RMSE 0.713 mm。
- 两组方向约 `-0.09°` 与 `89.93°`。
- 返回 50 个实例；两方向轴数 8 与 42，间距中位数约 141.0 mm 与 99.0 mm。

这些数值证明 I/O、算法和确定性契约能在真实规模输入上跑通，但样本没有人工实例标签；`50` 不是已确认的真实钢筋数量。参数敏感性检查还显示 PCA 半径 24/28/32 mm 时方向内轴数和间距明显变化，因此当前不满足“相邻参数档稳定”的生产门槛。

## 7. 自动验证

```bash
cd services/mesh-service
../../.cloudbim/mesh-venv/bin/python -m unittest -v \
  test_rebar_segmentation.py test_rebar_api.py
../../.cloudbim/mesh-venv/bin/python -m py_compile \
  main.py algorithms/rebar_segmentation.py rebar_poc.py rebar_api.py \
  test_rebar_segmentation.py test_rebar_api.py
```

当前 36 项测试覆盖：正交网片、噪声/离群、设备高杆、高度双层、可见表面双条纹、宽缺口与交叉链、短缺口桥接、非正交拒绝、错单位/稀疏/非有限输入、RANSAC 置信度、JSON 参数规范化、LAS/PLY 加载、跨块体素、稳定限流、路径逃逸、原子写、防止 CLI 覆盖输入、API 状态和共享重任务 429。

## 8. 尚未完成与下一道门槛

- 尚未实现弯筋、桁架腹筋拓扑、语义网络、直径估计、BIM 规则校验和原始高分辨率量测回归。
- `min_axis_spacing` 会把更近的表面轨迹解释为同一物理轴；它必须来自最小设计筋距/最小可分辨间距。真实钢筋确实更近时，当前模型不适用。
- 设备杆件与钢筋同方向、同高度时，纯几何仍可能误检，必须增加 ROI、固定设备掩膜、RGB/强度或学习式语义过滤。
- PLY/PCD 大文件、显式体素截断和大 JSON 索引映射仍需分块产物协议。
- 在接 Go API/UI 前，至少为正常、反光/锈蚀、遮挡/设备杆三类真实 patch 建立人工单筋与中心线真值，并报告 missed/merged/split、中心线 RMSE、间距 MAE/P95 和参数邻档稳定性。

研究依据、许可证和完整路线见 [技术调研](research/rebar-point-cloud-segmentation.md)。
