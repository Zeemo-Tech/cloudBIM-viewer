import SparkMD5 from 'spark-md5'

interface WorkerMessage {
  file: File
  chunkSize?: number
}

interface WorkerResponse {
  type: 'progress' | 'success' | 'error'
  progress?: number
  hash?: string
  error?: string
}

const DEFAULT_CHUNK_SIZE = 2 * 1024 * 1024
const MIN_CHUNK_SIZE = 512 * 1024
const MAX_CHUNK_SIZE = 10 * 1024 * 1024

const normalizeChunkSize = (chunkSize: number): number => {
  return Math.max(MIN_CHUNK_SIZE, Math.min(MAX_CHUNK_SIZE, chunkSize))
}

const postResponse = (response: WorkerResponse): void => {
  self.postMessage(response)
}

self.onmessage = async (event: MessageEvent<WorkerMessage>) => {
  const { file, chunkSize = DEFAULT_CHUNK_SIZE } = event.data

  if (!file || file.size === 0) {
    postResponse({
      type: 'error',
      error: '文件为空或无效',
    })
    return
  }

  const normalizedChunkSize = normalizeChunkSize(chunkSize)
  const totalChunks = Math.ceil(file.size / normalizedChunkSize)

  try {
    const spark = new SparkMD5.ArrayBuffer()

    for (let index = 0; index < totalChunks; index++) {
      const start = index * normalizedChunkSize
      const end = Math.min(start + normalizedChunkSize, file.size)
      const chunk = file.slice(start, end)
      const arrayBuffer = await chunk.arrayBuffer()

      spark.append(arrayBuffer)
      const progress = Math.floor(((index + 1) / totalChunks) * 100)
      postResponse({ type: 'progress', progress })
    }

    postResponse({
      type: 'success',
      hash: spark.end(),
    })
  } catch (error) {
    postResponse({
      type: 'error',
      error: error instanceof Error ? error.message : '计算文件 hash 失败',
    })
  }
}

export {}
