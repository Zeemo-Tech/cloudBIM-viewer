# 新点云流程 · 第 1 步：法向量

当前原始点云共 **9,216,369 点**。浏览器实际发起的全量重跑，法向量阶段 **5.001 秒**，服务端流程 **7.901 秒**，吞吐 **184 万点/秒**，进程峰值内存 **959 MiB**。暂不统一法向量朝向；没有加入去噪或分类步骤。

## 使用

独立调试地址：<http://127.0.0.1:8766>。用户明确要求独立调试端口，因此此工具使用独立 Python 进程；不启动或替换受 `scripts/cloudbim-dev.sh` 管理的前端、API、数据库和 mesh 服务。

```bash
.cloudbim/mesh-venv/bin/python scripts/pointcloud-debug.py \
  --source backend/data/assets/95b6b41c5857d9eb3407b155/source.las \
  --port 8766
```

当前后台进程 PID 文件为 `.cloudbim/pointcloud-debug/server.pid`，日志为同目录 `server.log`。终止本次独立调试进程可执行 `kill "$(cat .cloudbim/pointcloud-debug/server.pid)"`。后续修改 Python 算法后需要重新启动此进程；纯页面修改刷新即可。

页面提供原始点云 / 第 1 步切换、同步旋转缩放的双视图、法向量有符号和绝对值着色、稀疏箭头、顶视 / 斜视、点大小、历史运行、LAS / Manifest 下载、PNG 导出和分项耗时。

“从第 1 步重新运行”每次重新读取源文件、建树、计算和保存。重新打开历史结果仅加载已保存数据。浏览器使用 100 万个确定性源记录索引样本以限制传输与显存；全部 921.6 万点参与计算和持久化。

## 计算和缓存约定

- `algorithms/pointcloud_normals.py`：精确 kNN + 局部中心化协方差 + 最小特征值特征向量。默认 k=32，包含查询点自身；没有采样、点删除、坐标变换或朝向传播。
- 共享一棵只读 `cKDTree`；每个线程对连续 8,192 点做 native kNN 查询和批量矩阵计算。最多排队 2×workers 个块；邻域临时内存随块大小 / 线程数增长，不保留 N×k 的全局巨型矩阵。
- 并行发生在完整的查询 / PCA 块层；块内部 `cKDTree.query(workers=1)` 和 `threadpool_limits(1)` 避免嵌套线程竞争。选择本机实测最快的 16 线程。
- `StepRun.context` 保留源 XYZ、KD 树、法向量、有效性、曲率、邻域半径，供同轮后续步骤直接复用。几何或参与点集改变时须重建对应索引。下次调试重跑创建全新 context。
- KD 树只驻留内存，不用 pickle 持久化；重启后已保存的点属性仍可直接 mmap 读取，若再做邻域查询则重建树。
- 重复点、共线邻域或最小特征值数值重根使用 `normal_valid=0` 和零法向量。`normal_curvature` 是 λmin / trace(C)，不表示准确率。

相关主文档：[SciPy 邻域查询](https://docs.scipy.org/doc/scipy/reference/generated/scipy.spatial.KDTree.query.html)、[NumPy 批量线性代数](https://numpy.org/doc/stable/reference/routines.linalg.html)。

## 持久化

源资产：`backend/data/assets/95b6b41c5857d9eb3407b155/source.las`（既有诊断中的 YB-1mesh2.0.las）。SHA-256：`eb229b7c514e918c03534184eb56ceabdfd850c57b1b3503172bd8f290ebab52`。

结果位置：`backend/data/assets/95b6b41c5857d9eb3407b155/pointcloud-steps/<runId>/`。

- `pointcloud-with-normals.las`：自包含点云；保留原始所有点、坐标量化、点序和原属性，附加 `normal_x/y/z`（float32）、`normal_valid`（uint8）、`normal_curvature` / `normal_radius`（float32）Extra Bytes。法向量与原生 LAS XYZ 同坐标系。
- `positions.npy`（float64）、`normals.npy`、`normal_valid.npy`、`curvature.npy`、`neighbor_radius.npy`、`colors.npy`：后续计算可直接 mmap 读取。**数组第 i 行就是原始 LAS 第 i 条记录**；`colors.npy` 是显示用 RGB / 强度转换，原始强度等属性完整保存在 LAS。
- `manifest.json`：算法版本、源身份、参数、属性约定、耗时、点数、预览映射、缓存范围。
- `preview/`：二进制显示属性，位置相对 manifest 中的 origin；bounds 也相对 origin。`source_indices.bin` 明确映射显示点到原始记录。
- 使用隐藏临时目录完成写出及 fsync，再原子重命名并更新 `latest.json`；失败不会发布部分结果。旧轮次保持独立。

已保留的全量轮次：首次基准 `20260908T074309-3d1c5f4e`；页面重跑 `20260908T074752-14eb363a`。

## 实测

同一源文件、k=32、每次独立从源运行，CPU affinity 16。OS 文件缓存未清空。下列是服务端墙钟，不含 HTTP 排队 / 下载或浏览器绘制；CPU 累计时间是各线程 CPU 时间的总和，不能与墙钟相加。只比较实现内线程配置，不声称已证明所有算法 / 硬件中的理论最优。

| 线程数 | 法向量阶段 | 完整流程 | 对单线程法向量加速 |
|---|---:|---:|---:|
| 1 | 41.104 s | 43.994 s | 1.00× |
| 8 | 5.867 s | 8.791 s | 7.01× |
| 16 | 5.076 s | 7.972 s | 8.10× |
| 16（页面重跑） | 5.001 s | 7.901 s | 8.22× |

页面重跑分项：读取与 SHA 校验 0.176 s；验证坐标 / 建树 1.823 s；法向量 5.001 s；持久化及 flush 0.544 s；预览准备 0.356 s。记录包含少量调度 / 统计开销，总和以 totalS 为准。

原始测量 JSON 在 `.cloudbim/pointcloud-debug/full-run-{1,8,16}.json` 和 `ui-run.json`。

## 验证和效果

`cd services/mesh-service && ../../.cloudbim/mesh-venv/bin/python -m unittest test_pointcloud_normals.py`：5 项通过，覆盖大坐标倾斜平面、球面、串并行等价、退化 / 非有限输入、保存读取、源点身份、重新运行及失败不发布。

独立 verifier 对全量 LAS 逐块复查：9,216,369 点全部覆盖；原有 15 个属性 byte-equal；LAS 法向量与 NPY 逐点相等；有效法向量长度范围 0.99999994–1.0。源文件保持原内容。独立小数据 HTTP 验证状态 / 运行、跨来源拒绝、额外参数拒绝通过。

浏览器实际执行重新运行、禁用运行按钮、完成后新版本和参数 / 计时更新、方向绝对值着色、顶视和箭头显示；未发现控制台错误。JS 语法及 `git diff --check` 通过。

全量效果图：`.cloudbim/pointcloud-debug/normals-effect.png`。使用持久化的全量点沿 −Z 正射投影，各像素取最高 Z 点；没有采样。依次为原始强度、|n|、(n+1)/2。属于离线几何投影，不是浏览器截图，绘图耗时也不计入算法测量。

```bash
.cloudbim/mesh-venv/bin/python scripts/pointcloud-normals-figure.py \
  backend/data/assets/95b6b41c5857d9eb3407b155/pointcloud-steps/20260908T074752-14eb363a \
  .cloudbim/pointcloud-debug/normals-effect.png
```

平面在绝对值模式中接近蓝色（Z 法线），曲面 / 棱边产生不同颜色；有符号模式仍可见朝向翻转。全部法向量数值有效不代表真实场景估计已达到满意质量，具体邻域效果由该视图继续检查。

协作记录：explorer（gpt-5.6-terra / medium）完成存储调查；worker（同模型 / effort）完成页面与定向修正；verifier（同模型 / effort）完成独立核验；三个子任务均已结束，由 root 完成集成、基准、页面验证与效果图。
