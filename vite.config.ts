import { fileURLToPath, URL } from 'node:url'
import { defineConfig, loadEnv, type ProxyOptions } from 'vite'
import vue from '@vitejs/plugin-vue'

// https://vite.dev/config/
export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), '')
  const apiProxyTarget =
    env.VITE_API_PROXY_TARGET ||
    env.VITE_UPLOAD_API_PROXY_TARGET ||
    'http://127.0.0.1:8090'

  return {
    server: {
      host: '0.0.0.0',
      proxy: {
        '/health': createProxyConfig(apiProxyTarget),
        '/auth': createProxyConfig(apiProxyTarget),
        '/uploads': createProxyConfig(apiProxyTarget),
        '/assets': createProxyConfig(apiProxyTarget),
        '/mesh': createProxyConfig(apiProxyTarget),
        '/scans': createProxyConfig(apiProxyTarget),
        '/alignments': createProxyConfig(apiProxyTarget),
      },
    },
    resolve: {
      alias: {
        '@': fileURLToPath(new URL('./src', import.meta.url)),
      },
    },
    plugins: [vue()],
  }
})

function createProxyConfig(target: string): ProxyOptions {
  return {
    target,
    changeOrigin: true,
    ws: true,
    timeout: 0,
    proxyTimeout: 0,
    configure(proxy) {
      proxy.on('proxyReq', (proxyRequest) => {
        // Browser requests may arrive through an SSH-forwarded local port.
        proxyRequest.setHeader('origin', 'http://127.0.0.1:5173')
      })
    },
  }
}
