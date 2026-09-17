/**
 * 宿主应用注入的运行时配置。
 *
 * 包本身不读取 `import.meta.env`，也不依赖宿主的登录态实现：API 基址与访问令牌
 * 都由宿主动态提供，因此同一个包可以同时被不同部署（同源代理 / 独立域名）复用。
 */
export interface CloudBimRuntimeOptions {
  /** API 根地址，例如 `https://bim.example.com/api`。留空表示与宿主同源。 */
  baseUrl?: string
  /** 返回当前访问令牌。返回空值时依赖后端下发的 HttpOnly Cookie。 */
  getAccessToken?: () => string | null | undefined
  /** 输出调试日志。 */
  debug?: boolean
  /**
   * Draco 解码器目录（需以 `/` 结尾）。默认 `/draco/`，即宿主把 three 的
   * draco 解码器放在自身静态资源目录下。
   */
  dracoDecoderPath?: string
  /** 收到 401 时的回调，宿主可在此跳转登录页。 */
  onUnauthorized?: (error: unknown) => void
}

interface CloudBimRuntime {
  baseUrl: string
  getAccessToken?: () => string | null | undefined
  debug: boolean
  dracoDecoderPath: string
  onUnauthorized?: (error: unknown) => void
}

/** Draco 解码器的默认目录，与云端部署的 `public/draco/` 保持一致。 */
export const DEFAULT_DRACO_DECODER_PATH = '/draco/'

const runtime: CloudBimRuntime = {
  baseUrl: '',
  debug: false,
  dracoDecoderPath: DEFAULT_DRACO_DECODER_PATH,
}

function normalizeBaseUrl(value: string) {
  const trimmed = value.trim()

  if (!trimmed) {
    return ''
  }

  return trimmed.endsWith('/') ? trimmed.slice(0, -1) : trimmed
}

/**
 * 配置包的运行时行为，通常在应用启动时调用一次。
 * 未传入的字段保持原值，因此可以分多次合并配置。
 */
export function configureCloudBim(options: CloudBimRuntimeOptions = {}) {
  if (options.baseUrl !== undefined) {
    runtime.baseUrl = normalizeBaseUrl(options.baseUrl)
  }

  if (options.getAccessToken !== undefined) {
    runtime.getAccessToken = options.getAccessToken
  }

  if (options.debug !== undefined) {
    runtime.debug = Boolean(options.debug)
  }

  if (options.dracoDecoderPath !== undefined) {
    const path = options.dracoDecoderPath.trim()
    runtime.dracoDecoderPath = path ? (path.endsWith('/') ? path : `${path}/`) : DEFAULT_DRACO_DECODER_PATH
  }

  if (options.onUnauthorized !== undefined) {
    runtime.onUnauthorized = options.onUnauthorized
  }
}

export function getCloudBimRuntime(): Readonly<CloudBimRuntime> {
  return runtime
}

export function isCloudBimDebug(): boolean {
  return runtime.debug
}
