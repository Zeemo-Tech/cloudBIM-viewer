import { defineConfig, mergeConfig } from 'vite'
import mainConfig from './vite.config'

// Reuse the app, authentication and API proxies; keep dependency caches and ports separate.
export default defineConfig(async context => {
  const base = typeof mainConfig === 'function' ? await mainConfig(context) : await mainConfig
  return mergeConfig(base, {
    cacheDir: '.cloudbim/rebar-debug-vite',
    server: { port: 5174, strictPort: true },
  })
})
