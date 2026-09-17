export const ANALYSIS_MESH_ARTIFACT_V1 = 'analysis-mesh-artifact-v1' as const
export const ANALYSIS_MESH_COMPONENTS_V1 = 'analysis-mesh-components-v1' as const
export const ANALYSIS_C2M_RESULT_V1 = 'analysis-c2m-result-v1' as const

export class AnalysisMeshContractError extends Error {}

export type AnalysisMeshArtifactFile = {
  sha256: string
  byteLength: number
}

export type AnalysisModelFrame = {
  sourceBounds: { min: [number, number, number]; max: [number, number, number] }
  normalizationCenter: [number, number, number]
}

export type AnalysisMeshManifest = {
  artifactVersion: typeof ANALYSIS_MESH_ARTIFACT_V1
  immutable: true
  entryPath: 'tileset.json'
  componentsPath: 'components.json'
  metricsPath: 'metrics.json'
  modelFrame: AnalysisModelFrame
  contentHash: string
  algorithm: {
    id: string
    implementationVersion: string
    contractVersion: string
    effectiveParameters: Record<string, unknown>
  }
  componentCount: number
  tileCount: number
  faceCap: number
  files: Record<string, AnalysisMeshArtifactFile>
}

export type AnalysisMeshPart = {
  partId: string
  nodeName: string
  faceCount: number
  positionHash: string
  tiles: string[]
}

export type AnalysisMeshComponent = {
  ifcGlobalId: string
  parts: AnalysisMeshPart[]
}

export type AnalysisMeshTile = {
  tileId: string
  uri: string
  ifcGlobalId: string
  partId: string
  positionHash: string
  vertexCount: number
  faceCount: number
  byteLength: number
  sha256: string
}

export type AnalysisMeshComponents = {
  schema: typeof ANALYSIS_MESH_COMPONENTS_V1
  tree: unknown
  components: AnalysisMeshComponent[]
  tiles: AnalysisMeshTile[]
}

export type C2MTileBinding = {
  tileId: string
  distancePath: string
  vertexCount: number
  positionHash: string
  ifcGlobalId: string
  partId: string
}

export type AnalysisC2MStats = {
  knownCount: number
  unknownCount: number
  min?: number
  max?: number
  mean?: number
  std?: number
}

export type AnalysisC2MTile = C2MTileBinding & {
  sha256: string
  byteLength: number
  stats: AnalysisC2MStats
}

export type AnalysisC2MManifest = {
  schema: typeof ANALYSIS_C2M_RESULT_V1
  immutable: true
  contentHash: string
  unknownEncoding: {
    type: 'ieee754-float32'
    value: 'NaN'
    byteOrder: 'little-endian'
  }
  inputAnalysisMesh: {
    contentHash: string
    artifactVersion: typeof ANALYSIS_MESH_ARTIFACT_V1
    modelFrame: AnalysisModelFrame
  }
  algorithm: {
    id: string
    implementationVersion: string
    contractVersion: string
    effectiveParameters: Record<string, unknown>
  }
  scan: { contentHash: string; pointsBefore: number; pointsAfter: number }
  transformColumnMajor: number[]
  tiles: AnalysisC2MTile[]
  components: Array<{ ifcGlobalId: string; stats: AnalysisC2MStats }>
  global: AnalysisC2MStats
  files: Record<string, AnalysisMeshArtifactFile>
}

const isRecord = (value: unknown): value is Record<string, unknown> =>
  typeof value === 'object' && value !== null && !Array.isArray(value)

const isHash = (value: unknown): value is string =>
  typeof value === 'string' && /^[a-f0-9]{64}$/i.test(value)

const isInteger = (value: unknown, minimum = 0): value is number =>
  typeof value === 'number' && Number.isInteger(value) && value >= minimum

const isNonEmptyString = (value: unknown): value is string =>
  typeof value === 'string' && value.trim().length > 0

const isSafeRelativeArtifactPath = (value: unknown): value is string => {
  if (!isNonEmptyString(value) || value.startsWith('/') || value.includes('\\')) return false
  if (/[?#]/.test(value) || /^[a-z][a-z0-9+.-]*:/i.test(value)) return false
  return value.split('/').every((segment) => segment !== '' && segment !== '.' && segment !== '..')
}

function isModelFrame(value: unknown): value is AnalysisModelFrame {
  if (!isRecord(value) || !isRecord(value.sourceBounds)) return false
  const low = value.sourceBounds.min
  const high = value.sourceBounds.max
  const center = value.normalizationCenter
  const isFiniteVector3 = (row: unknown): row is [number, number, number] =>
    Array.isArray(row) && row.length === 3 && row.every((item) => typeof item === 'number' && Number.isFinite(item))
  if (!isFiniteVector3(low) || !isFiniteVector3(high) || !isFiniteVector3(center)) return false
  return low.every((item, index) => item <= high[index]! && Math.abs(center[index]! - (item + high[index]!) / 2) <= 1e-9)
}

function collectTreeIds(value: unknown, ids: Set<string>): void {
  if (!isRecord(value) || !isNonEmptyString(value.id) || !Array.isArray(value.children)) {
    throw new AnalysisMeshContractError('invalid IFC component tree node')
  }
  if (ids.has(value.id)) throw new AnalysisMeshContractError('duplicate IFC component tree id')
  ids.add(value.id)
  value.children.forEach((child) => collectTreeIds(child, ids))
}

export function parseAnalysisMeshManifest(value: unknown): AnalysisMeshManifest {
  if (!isRecord(value)) throw new AnalysisMeshContractError('manifest must be an object')
  const algorithm = value.algorithm
  const files = value.files
  if (
    value.artifactVersion !== ANALYSIS_MESH_ARTIFACT_V1 ||
    value.immutable !== true ||
    value.entryPath !== 'tileset.json' ||
    value.componentsPath !== 'components.json' ||
    value.metricsPath !== 'metrics.json' ||
    !isModelFrame(value.modelFrame) ||
    !isHash(value.contentHash) ||
    !isRecord(algorithm) ||
    !isNonEmptyString(algorithm.id) ||
    !isNonEmptyString(algorithm.implementationVersion) ||
    !isNonEmptyString(algorithm.contractVersion) ||
    !isRecord(algorithm.effectiveParameters) ||
    !isInteger(value.componentCount, 1) ||
    !isInteger(value.tileCount, 1) ||
    !isInteger(value.faceCap, 1) ||
    !isRecord(files)
  ) {
    throw new AnalysisMeshContractError('invalid analysis-mesh v1 manifest')
  }
  for (const path of ['components.json', 'metrics.json', 'tileset.json']) {
    const file = files[path]
    if (!isRecord(file) || !isHash(file.sha256) || !isInteger(file.byteLength)) {
      throw new AnalysisMeshContractError(`invalid required artifact file: ${path}`)
    }
  }
  return value as AnalysisMeshManifest
}

export function parseAnalysisMeshComponents(value: unknown): AnalysisMeshComponents {
  if (!isRecord(value) || value.schema !== ANALYSIS_MESH_COMPONENTS_V1 || !('tree' in value)) {
    throw new AnalysisMeshContractError('invalid analysis-mesh components document')
  }
  if (!Array.isArray(value.components) || !Array.isArray(value.tiles)) {
    throw new AnalysisMeshContractError('components and tiles must be arrays')
  }
  const components = value.components as unknown[]
  const tiles = value.tiles as unknown[]
  const treeIds = new Set<string>()
  collectTreeIds(value.tree, treeIds)
  const componentIds = new Set<string>()
  const partIds = new Set<string>()
  const partOwners = new Map<string, string>()
  const partFaceCounts = new Map<string, number>()
  const partTileIds = new Map<string, Set<string>>()
  const declaredTileOwners = new Map<string, string>()
  for (const item of components) {
    if (!isRecord(item) || !isNonEmptyString(item.ifcGlobalId) || componentIds.has(item.ifcGlobalId) || !treeIds.has(item.ifcGlobalId) || !Array.isArray(item.parts)) {
      throw new AnalysisMeshContractError('invalid component identity')
    }
    componentIds.add(item.ifcGlobalId)
    if (item.parts.length === 0) throw new AnalysisMeshContractError('component has no mesh parts')
    for (const part of item.parts) {
      if (
        !isRecord(part) ||
        !isNonEmptyString(part.partId) ||
        !isNonEmptyString(part.nodeName) ||
        !isInteger(part.faceCount, 1) ||
        !isHash(part.positionHash) ||
        !Array.isArray(part.tiles) ||
        part.tiles.length === 0 ||
        part.tiles.some((tileId) => !isNonEmptyString(tileId))
      ) {
        throw new AnalysisMeshContractError('invalid component part')
      }
      if (partIds.has(part.partId)) throw new AnalysisMeshContractError('duplicate component part id')
      const declaredTiles = new Set<string>()
      for (const tileId of part.tiles) {
        if (declaredTiles.has(tileId) || declaredTileOwners.has(tileId)) {
          throw new AnalysisMeshContractError('analysis tile is declared by multiple parts')
        }
        declaredTiles.add(tileId)
        declaredTileOwners.set(tileId, part.partId)
      }
      partIds.add(part.partId)
      partOwners.set(part.partId, item.ifcGlobalId)
      partFaceCounts.set(part.partId, part.faceCount)
      partTileIds.set(part.partId, declaredTiles)
    }
  }
  const tileIds = new Set<string>()
  const actualPartFaceCounts = new Map<string, number>()
  for (const tile of tiles) {
    if (
      !isRecord(tile) ||
      !isNonEmptyString(tile.tileId) ||
      tileIds.has(tile.tileId) ||
      !isSafeRelativeArtifactPath(tile.uri) ||
      !isNonEmptyString(tile.ifcGlobalId) ||
      !componentIds.has(tile.ifcGlobalId) ||
      !isNonEmptyString(tile.partId) ||
      partOwners.get(tile.partId) !== tile.ifcGlobalId ||
      !partTileIds.get(tile.partId)?.has(tile.tileId) ||
      !isHash(tile.positionHash) ||
      !isHash(tile.sha256) ||
      !isInteger(tile.vertexCount, 1) ||
      !isInteger(tile.faceCount, 1) ||
      !isInteger(tile.byteLength, 1)
    ) {
      throw new AnalysisMeshContractError('invalid analysis-mesh tile')
    }
    tileIds.add(tile.tileId)
    actualPartFaceCounts.set(
      tile.partId,
      (actualPartFaceCounts.get(tile.partId) ?? 0) + tile.faceCount,
    )
  }
  if (tileIds.size !== declaredTileOwners.size) {
    throw new AnalysisMeshContractError('component parts and tile registry differ')
  }
  for (const tileId of declaredTileOwners.keys()) {
    if (!tileIds.has(tileId)) throw new AnalysisMeshContractError('component part references unknown tile')
  }
  for (const [partId, faceCount] of partFaceCounts) {
    if (actualPartFaceCounts.get(partId) !== faceCount) {
      throw new AnalysisMeshContractError('component part face count differs from tiles')
    }
  }
  return value as AnalysisMeshComponents
}

export function parseC2MDistances(
  buffer: ArrayBuffer,
  binding: Pick<C2MTileBinding, 'vertexCount'>,
): Float32Array {
  if (buffer.byteLength !== binding.vertexCount * Float32Array.BYTES_PER_ELEMENT) {
    throw new AnalysisMeshContractError('C2M vertex count does not match tile binding')
  }
  const result = new Float32Array(binding.vertexCount)
  const view = new DataView(buffer)
  for (let index = 0; index < result.length; index += 1) {
    result[index] = view.getFloat32(index * Float32Array.BYTES_PER_ELEMENT, true)
  }
  return result
}

function isStats(value: unknown): value is AnalysisC2MStats {
  if (!isRecord(value) || !isInteger(value.knownCount) || !isInteger(value.unknownCount)) return false
  for (const field of ['min', 'max', 'mean', 'std'] as const) {
    const item = value[field]
    if (item !== undefined && (typeof item !== 'number' || !Number.isFinite(item))) return false
  }
  return value.knownCount === 0
    ? value.min === undefined && value.max === undefined && value.mean === undefined && value.std === undefined
    : [value.min, value.max, value.mean, value.std].every(
        (item) => typeof item === 'number' && Number.isFinite(item),
      )
}

export function parseAnalysisC2MManifest(value: unknown): AnalysisC2MManifest {
  if (!isRecord(value) || value.schema !== ANALYSIS_C2M_RESULT_V1 || value.immutable !== true) {
    throw new AnalysisMeshContractError('invalid analysis-c2m v1 manifest')
  }
  const encoding = value.unknownEncoding
  const input = value.inputAnalysisMesh
  const algorithm = value.algorithm
  const scan = value.scan
  if (
    !isHash(value.contentHash) ||
    !isRecord(encoding) ||
    encoding.type !== 'ieee754-float32' ||
    encoding.value !== 'NaN' ||
    encoding.byteOrder !== 'little-endian' ||
    !isRecord(input) ||
    !isHash(input.contentHash) ||
    input.artifactVersion !== ANALYSIS_MESH_ARTIFACT_V1 ||
    !isModelFrame(input.modelFrame) ||
    !isRecord(algorithm) ||
    !isNonEmptyString(algorithm.id) ||
    !isNonEmptyString(algorithm.implementationVersion) ||
    !isNonEmptyString(algorithm.contractVersion) ||
    !isRecord(algorithm.effectiveParameters) ||
    !isRecord(scan) ||
    !isHash(scan.contentHash) ||
    !isInteger(scan.pointsBefore, 1) ||
    !isInteger(scan.pointsAfter, 1) ||
    !Array.isArray(value.transformColumnMajor) ||
    value.transformColumnMajor.length !== 16 ||
    value.transformColumnMajor.some((item) => typeof item !== 'number' || !Number.isFinite(item)) ||
    !Array.isArray(value.tiles) ||
    value.tiles.length === 0 ||
    !Array.isArray(value.components) ||
    value.components.length === 0 ||
    !isStats(value.global) ||
    !isRecord(value.files)
  ) {
    throw new AnalysisMeshContractError('invalid analysis-c2m v1 contract')
  }
  for (const [path, file] of Object.entries(value.files)) {
    if (
      !isSafeRelativeArtifactPath(path) ||
      !isRecord(file) ||
      !isHash(file.sha256) ||
      !isInteger(file.byteLength)
    ) {
      throw new AnalysisMeshContractError('invalid analysis-c2m file registry')
    }
  }
  const componentIds = new Set<string>()
  const componentCounts = new Map<string, { known: number; unknown: number }>()
  for (const component of value.components) {
    if (!isRecord(component) || !isNonEmptyString(component.ifcGlobalId) || componentIds.has(component.ifcGlobalId) || !isStats(component.stats)) {
      throw new AnalysisMeshContractError('invalid analysis-c2m component')
    }
    componentIds.add(component.ifcGlobalId)
    componentCounts.set(component.ifcGlobalId, { known: 0, unknown: 0 })
  }
  const tileIds = new Set<string>()
  const distancePaths = new Set<string>()
  for (const tile of value.tiles) {
    if (
      !isRecord(tile) ||
      !isNonEmptyString(tile.tileId) ||
      tileIds.has(tile.tileId) ||
      !isNonEmptyString(tile.ifcGlobalId) ||
      !componentIds.has(tile.ifcGlobalId) ||
      !isNonEmptyString(tile.partId) ||
      !isSafeRelativeArtifactPath(tile.distancePath) ||
      distancePaths.has(tile.distancePath) ||
      !isHash(tile.positionHash) ||
      !isHash(tile.sha256) ||
      !isInteger(tile.vertexCount, 1) ||
      tile.byteLength !== tile.vertexCount * Float32Array.BYTES_PER_ELEMENT ||
      !isStats(tile.stats)
    ) {
      throw new AnalysisMeshContractError('invalid analysis-c2m tile')
    }
    const counts = componentCounts.get(tile.ifcGlobalId)!
    counts.known += tile.stats.knownCount
    counts.unknown += tile.stats.unknownCount
    const file = value.files[tile.distancePath]
    if (!isRecord(file) || file.sha256 !== tile.sha256 || file.byteLength !== tile.byteLength) {
      throw new AnalysisMeshContractError('analysis-c2m tile file binding mismatch')
    }
    tileIds.add(tile.tileId)
    distancePaths.add(tile.distancePath)
  }
  let globalKnown = 0
  let globalUnknown = 0
  for (const component of value.components) {
    const counts = componentCounts.get(component.ifcGlobalId)!
    if (
      component.stats.knownCount !== counts.known ||
      component.stats.unknownCount !== counts.unknown
    ) {
      throw new AnalysisMeshContractError('analysis-c2m component statistics mismatch')
    }
    globalKnown += counts.known
    globalUnknown += counts.unknown
  }
  if (value.global.knownCount !== globalKnown || value.global.unknownCount !== globalUnknown) {
    throw new AnalysisMeshContractError('analysis-c2m global statistics mismatch')
  }
  return value as AnalysisC2MManifest
}

export async function loadC2MTile(
  baseURL: string,
  binding: C2MTileBinding,
  meshPositionHash: string,
  fetcher: typeof fetch = fetch,
) {
  if (!isHash(binding.positionHash) || binding.positionHash !== meshPositionHash) {
    throw new AnalysisMeshContractError('C2M positionHash binding mismatch')
  }
  const response = await fetcher(new URL(binding.distancePath, baseURL))
  if (!response.ok) throw new AnalysisMeshContractError(`C2M request failed: ${response.status}`)
  return parseC2MDistances(await response.arrayBuffer(), binding)
}
