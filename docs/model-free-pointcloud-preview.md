# 无设计模型点云预览

无设计模型的 shared segmentation 流程使用全部已分类钢筋提取实例，
高度分层使用全部非台面点。测得的框仅用于方向与区域参考，不能把框外、
框边或未定位钢筋排除出实例处理。带设计输入的流程保留原来的内框约束。

## 同一个实测、同一个点云

主预览通过 `pointcloud-segmentation` 派生表示读取一份完整点云。
“真彩”“查看分类”“查看实例”仅切换同一几何体的颜色；台面、夹具、
钢筋和噪点不会创建独立资产。浏览瓦片仍遵循原始点云的 LOD/抽样设置，
报告中的数量来自全部源点。实例是几何候选，不等同于真实钢筋根数。

分类瓦片保留原始 XYZ、RGB 和层级；`REBAR_CLASS` 使用 0 未定、1 台面、
2 夹具、3 钢筋、4 噪点，`REBAR_INSTANCE=0` 表示未归属。
此编码仅用于 `segmentation-class` 显示模式，不能解释为历史钢筋分类编码。

## 从离线结果发布预览

使用项目 `.cloudbim/mesh-venv/bin/python` 执行
`services/mesh-service/pointcloud_segmentation_preview.py`，参数为：

- `--source`：该资产未修改的原始 LAS。
- `--source-tiles`：原始点云瓦片目录。
- `--run`：本次完整无设计模型分割的输出目录。
- `--output`：资产目录内尚不存在的新版本目录。
- `--leveling`：若分析使用摆正副本，提供含 `sourceToLeveledRowMajor` 的 JSON。

发布器逐行核对全部源点与分析坐标，允许 LAS 量化误差（20 微米），
拒绝点序、点数或坐标不一致的结果；复制瓦片后写入属性，不改动归档。
结果生成后，由服务器运维将该目录注册为原资产的 `DBAssetDerivative`：
`kind=pointcloud-segmentation`、`format=3d-tiles`、`status=ready`、
`entry_path=tiles/tileset.json`，独立版本号，`metadata_json` 为输出 manifest。
该步骤不是新建实测记录。网页仅查看已发布结果，不自动启动离线分割。

## 坐标约定

manifest 的 `plane` 使用源 LAS 中的 `origin`、朝向物体的单位 `normal`
和米制 `clearanceM`。预览对唯一父节点施加刚体变换，将台面法向映射到
源 Z-up、再映射到 Three.js Y-up。所有类别共同变换，过滤仍在源坐标中计算。
归档 LAS 不变；测量保存时转换回历史源视图坐标，加载时再变换到当前视图，
并重算高度差、水平距离与坡度。

## 验证

- mesh-service：`python -m unittest test_internal_rebar test_shared_scene test_layering_branch_contract test_rebar_segmentation test_pointcloud_segmentation_preview`
- 前端：`node --experimental-strip-types --test src/features/pointcloud/displayFrame.test.ts src/features/pointcloud/tableVisibility.test.ts`
- 构建：`npm run build`

Python 命令需在 mesh-service 目录使用 `../../.cloudbim/mesh-venv/bin/python`。
浏览器需检查：分类与实例切换保持几何和相机、隐藏/显示台面、窄窗口下按钮可达、
窗口缩放后 canvas 尺寸一致，以及台面法向变换结果接近 `[0, 1, 0]`。
