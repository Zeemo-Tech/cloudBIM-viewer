# cloudBIM viewer

简洁版实模一致系统：前端使用 Vue 3 + TypeScript，后端使用 Gin + GORM，账号、资产、上传任务和对齐结果保存在数据库，IFC/LAS 上传后由真实转换工具生成 GLB / 3D Tiles。

## 本机用 MySQL 启动

Workbench 是数据库管理客户端，必须先确认 MySQL Server 已经启动。Workbench 连接 `127.0.0.1:3306` 后执行：

```sql
CREATE DATABASE IF NOT EXISTS cloudbim
  CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;

-- 生产环境建议使用专用账号（开发时也可以先用 root）
CREATE USER IF NOT EXISTS 'cloudbim'@'localhost' IDENTIFIED BY '请替换为强密码';
GRANT ALL PRIVILEGES ON cloudbim.* TO 'cloudbim'@'localhost';
FLUSH PRIVILEGES;
```

然后配置并启动后端：

```bash
cd /Users/monica/Desktop/cloudBIM-viewer/backend
cp .env.example .env
# 编辑 .env：DB_DRIVER=mysql；如果使用上面的专用账号，同时设置 DB_USER=cloudbim
# DB_PASSWORD 改成对应的 MySQL 密码
go run .
```

后端地址：`http://127.0.0.1:8090`。首次启动会自动迁移业务表。验证数据库服务：

```bash
/usr/local/mysql/bin/mysqladmin -h 127.0.0.1 -P 3306 -u root -p ping
```

如果返回连接失败，请在 macOS 的 MySQL 系统菜单中启动 MySQL Server；仅打开 Workbench 不会启动服务。

另一个可选方案是使用项目自带的 PostgreSQL Compose：

```bash
cd /Users/monica/Desktop/cloudBIM-viewer/backend
docker compose up postgres -d
go run .
```

要启用真实 IFC/LAS 转换，请使用完整 Linux 容器（推荐 Apple Silicon + OrbStack）：

```bash
cd /Users/monica/Desktop/cloudBIM-viewer/backend
export JWT_SECRET="$(openssl rand -hex 32)"
export REGISTER_CODE=laochen
export JWT_EXPIRES_IN=24h
export ZHONGJIAN_BACK_DIR=/Users/monica/Desktop/zhongjian-back
docker compose up --build
```

该模式会启动独立 PostgreSQL 和 `linux/amd64` 后端，转换器在容器内执行。宿主机 PostgreSQL 端口映射为 `15432`，不会与其他项目的 `5432` 冲突。

## 启动前端

保持后端运行，再开一个终端：

```bash
cd /Users/monica/Desktop/cloudBIM-viewer
pnpm install
pnpm dev
```

前端默认地址为 `http://localhost:5173`，开发代理已指向 `http://127.0.0.1:8090`（见 `.env.local`）。

## 账号

开发环境默认会创建演示账号：`demo` / `demo123456`，注册验证码为 `laochen`。正式使用建议注册自己的账号，并将 `CLOUDBIM_SEED_DEMO=false`。账号数据保存在当前配置的数据库 `db_users` 表中，不保存在浏览器或本地 JSON 文件中。

## 真实模型转换

后端不会伪造预览。IFC 需要 `ifc_bundle`，LAS 需要 `gocesiumtiler`；可通过 `IFC_BUNDLE_BIN`、`GOCESIUMTILER_BIN` 指定路径。参考项目中的转换器是 Linux x86_64 版本，macOS arm64 不能直接执行，真实转换请在 Linux/Docker 中运行或提供对应平台二进制。转换失败时资产会标记为 `failed`，不会显示成可预览的 `ready`。

更多 API、环境变量和生产部署说明见 [backend/README.md](backend/README.md)。
