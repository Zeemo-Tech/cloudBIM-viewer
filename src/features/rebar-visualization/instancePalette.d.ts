export function buildInstancePalette(instances: ReadonlyArray<{
  id: number
  paths: number[][][]
}>): Map<number, [number, number, number]>

export function instanceColorDistance(a: readonly number[], b: readonly number[]): number
