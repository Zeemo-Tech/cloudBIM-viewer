import { createApp } from 'vue'
import ElementPlus from 'element-plus'
import { configureCloudBim } from '@cloudbim/viewer-core'
import 'element-plus/dist/index.css'
import '@cloudbim/viewer-core/style.css'
// 每个查看器包的 SFC 样式由 Vite 抽取为独立的 style.css，必须显式引入，
// 否则组件渲染正常但完全没有样式。
// 注意：新增功能包时这里要同步补一行（安装器生成的 src/cloudbim/setup.ts 会自动处理这件事）。
import '@cloudbim/denoise/style.css'
import '@cloudbim/bim-preview/style.css'
import '@cloudbim/pointcloud-preview/style.css'
import '@cloudbim/split-preview/style.css'
import '@cloudbim/alignment/style.css'
import '@/style.scss'
import App from '@/App.vue'
import router from '@/router'
import { getStoredAccessToken } from '@/features/auth/auth.storage'

// The host owns deployment configuration and login state; the packages receive
// both through this single entry point.
const apiBaseUrl =
  import.meta.env.VITE_API_BASE_URL?.trim() ||
  import.meta.env.VITE_UPLOAD_API_BASE_URL?.trim() ||
  ''

configureCloudBim({
  baseUrl: apiBaseUrl,
  getAccessToken: getStoredAccessToken,
  debug: import.meta.env.DEV,
})

createApp(App).use(router).use(ElementPlus).mount('#app')
