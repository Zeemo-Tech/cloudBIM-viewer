# cloudBIM viewer backend

这是 `cloudBIM-viewer` 的实模一致后端。业务元数据、账号、上传会话和对齐结果全部持久化到关系数据库（支持 PostgreSQL 或 MySQL）；文件只作为对象内容存储在 `CLOUDBIM_DATA_DIR`，不使用 `state.json` 或浏览器本地数据伪造状态。

后端使用 Gin + GORM + PostgreSQL/MySQL，采用 Tus 1.0 断点续传、固定数量转换 worker、启动任务恢复、数据库连接池、请求 ID、严格 CORS、路径 containment 校验和优雅停机。上传完成后会调用真实转换工具，转换失败只会返回 `failed`，不会伪造 `ready`。

## 本地开发

先启动数据库。你已经安装 MySQL 时，可以直接使用 MySQL 8：

```bash
# 在 MySQL Workbench 执行一次：
CREATE DATABASE IF NOT EXISTS cloudbim CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;

cd /Users/monica/Desktop/cloudBIM-viewer/backend
cp .env.example .env
# 修改 .env 中 DB_PASSWORD 为你的 MySQL 密码
go test ./...
go run .
```

默认服务地址为 `http://127.0.0.1:8090`。开发环境默认账号为 `demo` / `demo123456`，注册码为 `laochen`。设置 `CLOUDBIM_SEED_DEMO=false` 可关闭演示账号。

## 配置

| 变量 | 默认值 | 说明 |
| --- | --- | --- |
| `APP_ENV` | `development` | 生产部署设为 `production` |
| `ADDR` | `:8090` | HTTP 监听地址 |
| `CLOUDBIM_DATA_DIR` | `./data` | 上传源文件和转换产物目录 |
| `JWT_SECRET` | 开发密钥 | 生产必须显式设置且至少 32 位；密钥变更会使旧 token 失效 |
| `JWT_EXPIRES_IN` | 开发 `720h`（30 天），生产 `24h` | JWT 有效期，支持 `h/m/s` 及便捷的 `d`（天）格式，例如 `30d`；生产可设置更短值 |
| `REGISTER_CODE` | `laochen` | 注册码，生产必须显式设置 |
| `DB_DRIVER` | `postgres` | 数据库驱动，可选 `postgres` 或 `mysql` |
| `DB_HOST/PORT/USER/PASSWORD/NAME` | 按驱动默认 | 数据库连接参数；MySQL 默认端口 `3306`、用户 `root` |
| `DB_SSLMODE` | `disable` | 生产建议 `require` 或更严格模式 |
| `CORS_ALLOW_ORIGINS` | 本地前端地址 | 逗号分隔的明确 Origin，允许 `*` 但会关闭凭据 |
| `PROCESSING_WORKERS` | `2` | 并发 IFC/LAS 转换数，范围 1-32 |
| `UPLOAD_CHUNK_LIMIT` | `67108864` | 单个 Tus 分片最大字节数 |
| `UPLOAD_FILE_LIMIT` | `107374182400` | 单个文件最大字节数 |
| `IFC_BUNDLE_BIN` | PATH/参考工具 | IFC -> GLB + metadata |
| `GOCESIUMTILER_BIN` | PATH/参考工具 | LAS -> Cesium 3D Tiles |
| `MESH_SERVICE_URL` | `http://127.0.0.1:8001` | 网格均匀化与精细化配准服务地址；BIM 上传转换完成后会自动调用 `/remesh` 生成均匀化 PLY，服务不可用时可在校准页稍后重试 |
| `MESH_SERVICE_STORAGE_DIR` | `/storage` | C2M 网格服务容器内共享数据卷路径；必须与 mesh-service 的挂载点一致 |

服务启动时执行 GORM schema migration，并在连接后 Ping 数据库；连接失败会直接退出，避免服务处于假健康状态。开发环境会自动读取当前目录的 `.env`；生产环境设置 `APP_ENV=production` 后只读取进程环境变量，不读取本地 `.env`。生产环境不应依赖自动演示账号，建议由部署流程创建正式账号。

### 使用 MySQL Workbench

1. 在 Workbench 连接本机 MySQL（通常为 `127.0.0.1:3306`）。
2. 执行 `CREATE DATABASE IF NOT EXISTS cloudbim CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;`。
3. （生产建议）创建只授予 `cloudbim.*` 权限的专用 MySQL 用户，不要让应用使用 root。
4. 复制 `.env.example` 为 `.env`，设置 `DB_DRIVER=mysql` 和真实的 `DB_PASSWORD`。
5. 在 `backend` 目录用 `go run .` 启动后端。首次启动会自动创建业务表。

Workbench 只是管理工具，不会自动启动 MySQL Server。若连接失败，请先在 macOS 的 MySQL 菜单中启动 MySQL Server，并用 `mysqladmin -h 127.0.0.1 -P 3306 -u root -p ping` 验证服务状态。

## 真实 IFC/LAS 转换

```bash
export IFC_BUNDLE_BIN=/opt/tools/ifc_bundle
export GOCESIUMTILER_BIN=/opt/tools/gocesiumtiler
```

也会尝试从 `PATH` 查找，并回退到 `../../zhongjian-back/tools/`。参考项目中的转换器是 Linux x86_64 ELF，macOS arm64 无法直接执行；请在 Linux/Docker 中运行，或提供对应平台的二进制。转换产物分别为 BIM 的 `model.glb`、`metadata.json`，以及点云的 `tiles/tileset.json` 和其子资源。

## Docker Compose（生产示例）

先准备密钥和工具目录：

```bash
export APP_ENV=production
export JWT_SECRET="$(openssl rand -hex 32)"
export JWT_EXPIRES_IN=24h
export ZHONGJIAN_BACK_DIR=/path/to/zhongjian-back
docker compose up --build
```

Compose 默认按本地开发运行，使用固定的本地 JWT 密钥和 `720h`（30 天）有效期；因此容器重启不会让旧 token 因密钥随机变化而失效。生产部署请显式设置 `APP_ENV=production`、强随机 `JWT_SECRET`、`JWT_EXPIRES_IN` 和其他敏感变量，并把它们持久化到密钥管理系统，不要在每次重启时重新执行 `openssl rand`。生产环境默认有效期为 24 小时，可按安全策略设置更短值。

本机已有其他项目占用 PostgreSQL `5432` 时，本项目映射到宿主机 `15432`，容器内部连接仍为 `postgres:5432`。后端容器固定为 `linux/amd64`，用于在 Apple Silicon 上通过 OrbStack 运行参考项目的 Linux amd64 转换器。

## API

- 认证：`POST /auth/register`、`POST /auth/login`、`GET /auth/me`
- Tus：`POST /uploads`、`HEAD/PATCH/GET/DELETE /uploads/:id`
- 资产：`GET /assets`、`GET /assets/:id`、`DELETE /assets/:id`
- 资源：`/assets/:id/glb`、`/assets/:id/metadata`、`/assets/:id/tiles/*`
- 对齐：`GET /scans`、`GET /scans/:id/calibration`、`POST/GET /alignments/bim`、`POST /alignments/bim/fine`（需配置 `MESH_SERVICE_URL`）
- 网格均匀化：`GET /mesh/algorithms`、`POST /assets/:id/mesh/remesh`、`GET /assets/:id/mesh/remesh/status`、`GET /assets/:id/mesh/remesh/latest`
- 健康：`GET /health`（同时检查 PostgreSQL）
