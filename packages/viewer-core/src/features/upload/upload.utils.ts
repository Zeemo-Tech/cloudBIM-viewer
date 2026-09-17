export function formatFileSize(size: number): string {
  if (size === 0) {
    return '0 B'
  }

  const units = ['B', 'KB', 'MB', 'GB', 'TB']
  const exponent = Math.min(
    Math.floor(Math.log(size) / Math.log(1024)),
    units.length - 1,
  )
  const value = size / 1024 ** exponent

  return `${value.toFixed(value >= 100 || exponent === 0 ? 0 : 1)} ${units[exponent]}`
}

export function getFileExtension(fileName: string): string {
  const extension = fileName.split('.').pop()
  return extension ? extension.toLowerCase() : ''
}

export function matchesAcceptedExtension(
  fileName: string,
  extensions: string[],
): boolean {
  const extension = getFileExtension(fileName)
  return extensions.includes(extension)
}
