# 正式流程的设计辅助点云去噪

正式流程调整为：01 配准 → 02 点云分类与去噪 → 03 偏差对比 → 04 报告。

旧资产预览中的钢筋分割面板及 `rebar-segmentation` 前后端公开接口已移除。正式界面仅通过配准后的新步骤发起计算，独立练武场保留作为算法验证工具。旧算法的底层公共类型、文件安全工具和回归测试仍供共享模块使用。

## 算法与输出

新入口 `services/mesh-service/pointcloud_denoise.py` 调用练武场同一套 `segment_points(through_step=6)` 和 `refine_instances(mode='topology')`，包括最终未归属点、端部、尾部及卫星碎片清理。设计几何与尺寸先验都从当前点云、IFC、GLB 和已保存配准矩阵构建同一快照。

- 支持 3 至 2000 万个 LAS/LAZ 源点，默认法向量近邻数为 32。
- 全量运行分类，最终保留 `complete_class == 3` 的钢筋点。
- `cleaned.las` 按原记录顺序保留全部原始属性和扫描坐标，不生成设计点，不二次变换点坐标。
- `preview.ply` 最多采样 50 万点，包含最终分类标签、32 位 `instance` 编号和局部原点，用于原始、分类、钢筋实例显示切换。默认显示保留钢筋的实例色，同一实例的内外各段共用颜色，使用练武场相同的确定性配色。偏差计算读取全量保留点。
- `denoise-v2` 开始导出实例编号；旧预览仍可查看分类，实例视图会提示重新运行去噪，不会把所有钢筋伪装成同一实例。LAS 导出仍保留原始记录属性。
- 中间数组在临时目录内计算，完成后清理；已发布的每个输出版本独立保存，避免影响正在读取旧文件的计算。

## 接口和失效规则

- `POST /alignments/bim/denoise`：`modelScanFileId`、`modelBimFileId`、可选 `normalK`。
- `GET /alignments/bim/denoise/latest`：同一资产对的已保存结果，返回 `fresh`、版本和分类统计。
- `GET /alignments/bim/denoise/artifacts/{cleaned.las|preview.ply}`：必须携带资产对和 `version`，校验所有权、路径及当前输入。
- `POST /alignments/bim/c2m`：必须携带当前 `denoiseVersion`，不再接受原始点云回退。

结果利用已有资产衍生表，按点云与 BIM 配对保存，不需要新增数据库表。输入身份包含原始点云文件身份、设计模型文件身份和配准矩阵；重新配准、替换设计模型或缺失产物后，必须重新去噪。首次生成去噪结果也会使原始点云生成的旧 C2M 结果失效。

快速 C2M 与逐构件分析都读取 `cleaned.las`，并使用原配准矩阵。后续分析的生成、发布和资源读取均检查输入是否仍有效。去噪、C2M 和相关发布沿用资产对锁、配准变更锁及 mesh-service 重计算互斥。

## 验证（2026-09-12）

- `npm run build`：通过。
- `cd backend && go test ./...`：通过。
- Python 3.11 项目虚拟环境中：新去噪测试、设计快照、设计输入、完整实例清理及 C2M reference 测试通过。
- 真实样本：9,216,369 个源点 → 1,857,478 个钢筋点，210 个实例；工作台 5,315,996 点，夹具 1,890,864 点，噪声 152,031 点。
- 与练武场 `20260912T113748-23c11a16` 的最终分类逐条对照，全部保留记录与全部原始属性精确一致。源 SHA-256：`eb229b7c514e918c03534184eb56ceabdfd850c57b1b3503172bd8f290ebab52`。
- 正式容器链路：登录后对 Scan 5 / BIM 7 调用去噪接口，约 60 秒完成，保留点数与上述样本一致；最新结果 `fresh=true`，通过后端成功下载 LAS 与 PLY，并校验 LAS 点数和 PLY 预览点数。记录见 `.cloudbim/denoise-validation/backend-validation.json`。
- 实例赋色：Python 导出测试及前端 PLY 解析测试通过，包含跨片段同色、不同实例异色、超过 255 的编号和旧预览兼容提示。正式接口重新生成的 50 万点预览中，100,734 个钢筋采样点覆盖 210 个实例，对应 210 种颜色；LAS 文件摘要与变更前一致。记录见 `.cloudbim/denoise-validation/instance-color-validation.json`。
- `.cloudbim/mesh-venv/bin/python scripts/test_cloudbim_storage_mount.py`：默认命名卷和本地目录挂载两种模式通过。该测试复现并防止后端与计算服务读取不同存储目录的问题。
- 本地验证记录：`.cloudbim/denoise-validation/`。浏览器工具未提供可用浏览器，因此未完成交互式视觉验收。

开发栈通过 `scripts/cloudbim-dev.sh start` 构建和启动。

后端的 `/app/data` 和计算服务的 `/storage` 必须挂载同一来源，统一使用 `CLOUDBIM_DATA_MOUNT`（默认 `cloudbim-data`）。开发环境覆盖为本地目录时，两端必须同时生效，否则计算会报 `input must be a regular file within shared storage`。修改挂载配置后通过开发栈管理脚本重新启动，无需重新上传或保存配准。
