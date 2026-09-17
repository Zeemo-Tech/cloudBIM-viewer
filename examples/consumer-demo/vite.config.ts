import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

/**
 * 消费方最小示例。
 *
 * `/assets`、`/alignments`、`/measurements` 等前缀需要代理到 CloudBIM 后端，
 * 否则页面能渲染但会一直报 404。端口按实际情况改。
 */
export default defineConfig({
  plugins: [vue()],
  server: {
    port: 5199,
    proxy: {
      '/assets': { target: 'http://127.0.0.1:8090', changeOrigin: true },
      '/alignments': { target: 'http://127.0.0.1:8090', changeOrigin: true },
      '/auth': { target: 'http://127.0.0.1:8090', changeOrigin: true },
      '/system': { target: 'http://127.0.0.1:8090', changeOrigin: true },
    },
  },
})
