# ------------------------------------------------------------------------------
# 阶段 1: 构建前端静态产物 (Build Stage)
# ------------------------------------------------------------------------------
FROM hub.macb.cc/library/node:20-alpine AS builder

WORKDIR /app

# 启用 corepack 并锁定兼容的 pnpm 版本
RUN corepack enable && corepack prepare pnpm@9.15.4 --activate

# 先拷贝依赖声明文件以最大化利用构建缓存
COPY package.json pnpm-lock.yaml* package-lock.json* ./

# 安装依赖
RUN pnpm install

# 拷贝前端源码并执行编译
COPY . .
RUN pnpm build

# ------------------------------------------------------------------------------
# 阶段 2: 生产环境 Nginx 镜像 (Production Stage)
# ------------------------------------------------------------------------------
FROM hub.macb.cc/library/nginx:alpine

# 替换默认 Nginx 配置（包含 SPA 路由与大文件 Tus 代理设置）
COPY nginx.conf /etc/nginx/conf.d/default.conf

# 拷贝静态资源产物
COPY --from=builder /app/dist /usr/share/nginx/html

EXPOSE 80

CMD ["nginx", "-g", "daemon off;"]
