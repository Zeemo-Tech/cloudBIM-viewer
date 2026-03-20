declare module 'spark-md5' {
  class ArrayBuffer {
    append(data: globalThis.ArrayBuffer): void
    end(raw?: boolean): string
    reset(): void
  }

  const SparkMD5: {
    ArrayBuffer: typeof ArrayBuffer
  }

  export default SparkMD5
}
