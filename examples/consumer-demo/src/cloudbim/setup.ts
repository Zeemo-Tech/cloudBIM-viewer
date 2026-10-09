/**
 * CloudBIM 查看器包 · 运行时初始化
 *
 * 在应用入口引入一次即可：`import './cloudbim/setup'`
 */
import { configureCloudBim } from '@cloudbim/viewer-core'

// 库模式构建会把 SFC 样式抽取为独立 style.css，包不会自动注入，必须显式引入，
// 否则组件渲染正常但完全没有样式。
import '@cloudbim/viewer-core/style.css'
import '@cloudbim/bim-preview/style.css'
import '@cloudbim/pointcloud-preview/style.css'
import '@cloudbim/split-preview/style.css'
import '@cloudbim/alignment/style.css'

const env = ((import.meta as any).env ?? {}) as Record<string, string | boolean | undefined>

type CloudBimRuntimeOptions = Partial<Parameters<typeof configureCloudBim>[0]>

/** 再次调用可覆盖默认配置（例如切换后端地址或接入宿主登录态）。 */
export function setupCloudBim(overrides: CloudBimRuntimeOptions = {}): void {
  configureCloudBim({
    baseUrl: (env.VITE_API_BASE_URL as string) ?? '', // 留空 = 与宿主同源
    getAccessToken: () => globalThis.sessionStorage?.getItem('cloudbim_token') ?? '', // 返回空则由后端 Cookie 鉴权
    dracoDecoderPath: '/draco/', // 静态目录，install.sh 会把解码器部署到这里
    debug: Boolean(env.DEV),
    ...overrides,
  })
}

setupCloudBim()
