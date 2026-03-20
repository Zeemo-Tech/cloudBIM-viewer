import axios, {
  AxiosError,
  AxiosHeaders,
  type AxiosRequestConfig,
  type AxiosResponse,
  type AxiosResponseHeaders,
  type RawAxiosResponseHeaders,
} from 'axios'
import { getStoredAccessToken } from '@/features/auth/auth.storage'

export interface BackendResult<T> {
  code: number
  data: T
  msg: string
}

type ResponseType = NonNullable<AxiosRequestConfig['responseType']>

interface BackendRequestOptions extends Omit<AxiosRequestConfig, 'url'> {
  responseType?: ResponseType
}

const REQUEST_TIMEOUT = 60_000

export const backendBaseUrl =
  import.meta.env.VITE_API_BASE_URL?.trim() ||
  import.meta.env.VITE_UPLOAD_API_BASE_URL?.trim() ||
  ''

export function normalizeBackendUrl(path: string) {
  if (/^(?:https?:)?\/\//i.test(path) || path.startsWith('blob:') || path.startsWith('data:')) {
    return path
  }

  if (!backendBaseUrl) {
    return path
  }

  return new URL(path, ensureBaseUrlTrailingSlash(backendBaseUrl)).toString()
}

function ensureBaseUrlTrailingSlash(url: string) {
  return url.endsWith('/') ? url : `${url}/`
}

function createApiError(error: unknown) {
  if (axios.isAxiosError(error)) {
    const responseData = error.response?.data
    const message =
      extractErrorMessage(responseData) ||
      error.message ||
      `请求失败: ${error.response?.status ?? 'unknown'}`

    const nextError = new Error(message) as Error & {
      response?: {
        status: number
        data: unknown
      }
    }

    if (error.response) {
      nextError.response = {
        status: error.response.status,
        data: responseData,
      }
    }

    return nextError
  }

  return error instanceof Error ? error : new Error('请求失败，请稍后重试')
}

function extractErrorMessage(data: unknown) {
  if (!data || typeof data !== 'object') {
    return null
  }

  if ('msg' in data && typeof data.msg === 'string' && data.msg.trim()) {
    return data.msg
  }

  if ('message' in data && typeof data.message === 'string' && data.message.trim()) {
    return data.message
  }

  return null
}

function headersToObject(
  headers?: AxiosHeaders | RawAxiosResponseHeaders | AxiosResponseHeaders,
) {
  if (!headers) {
    return {}
  }

  if (headers instanceof AxiosHeaders) {
    return headers.toJSON()
  }

  return { ...headers }
}

function createResponseHeaders(
  headers?: AxiosHeaders | RawAxiosResponseHeaders | AxiosResponseHeaders,
) {
  const nextHeaders = new Headers()

  Object.entries(headersToObject(headers)).forEach(([key, value]) => {
    if (value === undefined || value === null) {
      return
    }

    if (Array.isArray(value)) {
      value.forEach((item) => nextHeaders.append(key, String(item)))
      return
    }

    nextHeaders.set(key, String(value))
  })

  return nextHeaders
}

export const backendClient = axios.create({
  baseURL: backendBaseUrl || undefined,
  timeout: REQUEST_TIMEOUT,
  headers: {
    Accept: 'application/json, text/plain, */*',
    'X-Requested-With': 'XMLHttpRequest',
  },
})

function createAuthorizedHeaders(headers?: unknown) {
  const nextHeaders = AxiosHeaders.from(headers as any)
  const token = getStoredAccessToken()

  if (token && !nextHeaders.has('Authorization')) {
    nextHeaders.set('Authorization', `Bearer ${token}`)
  }

  return nextHeaders
}

backendClient.interceptors.request.use((config) => {
  config.headers = createAuthorizedHeaders(config.headers)

  if (config.url) {
    config.url = normalizeBackendUrl(config.url)
  }

  return config
})

backendClient.interceptors.response.use(
  (response) => response,
  (error: AxiosError) => Promise.reject(createApiError(error)),
)

export async function backendRequestRaw<T = unknown>(
  path: string,
  options: BackendRequestOptions = {},
) {
  try {
    return await backendClient.request<T>({
      ...options,
      url: path,
    })
  } catch (error) {
    throw createApiError(error)
  }
}

export async function backendTusRequestRaw<T = unknown>(
  path: string,
  options: BackendRequestOptions = {},
) {
  try {
    const headers = createAuthorizedHeaders(options.headers)
    headers.delete('X-Requested-With')
    headers.set('Accept', '*/*')

    return await axios.request<T>({
      ...options,
      url: normalizeBackendUrl(path),
      timeout: REQUEST_TIMEOUT,
      headers,
    })
  } catch (error) {
    throw createApiError(error)
  }
}

export async function backendRequest<T = unknown>(
  path: string,
  options: BackendRequestOptions = {},
): Promise<T> {
  const response = await backendRequestRaw<T>(path, options)
  return response.data
}

export async function backendFetch(
  path: string,
  options: BackendRequestOptions = {},
): Promise<Response> {
  const response = await backendRequestRaw<ArrayBuffer>(path, {
    ...options,
    responseType: 'arraybuffer',
  })

  return new Response(response.data, {
    headers: createResponseHeaders(response.headers),
    status: response.status,
    statusText: response.statusText,
  })
}

export function readResponseHeader(
  response: AxiosResponse<unknown>,
  headerName: string,
) {
  const headers = headersToObject(response.headers)
  const matchedKey = Object.keys(headers).find(
    (key) => key.toLowerCase() === headerName.toLowerCase(),
  )

  if (!matchedKey) {
    return null
  }

  const value = headers[matchedKey]

  if (Array.isArray(value)) {
    return value[0] ? String(value[0]) : null
  }

  return value ? String(value) : null
}
