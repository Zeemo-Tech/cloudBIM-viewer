export function createUploadHeaders(extraHeaders?: HeadersInit) {
  const headers = new Headers(extraHeaders)

  const result: Record<string, string> = {}
  headers.forEach((value, key) => {
    result[key] = value
  })

  return result
}
