import type { AssetRepresentation } from '@/api/backend-file'

export interface SelectedPointcloudRepresentation {
  kind: 'source' | 'table-free'
  url: string
  version?: string
  tableFreeAvailable: boolean
}

export function pointcloudRepresentationUrl(representation: AssetRepresentation) {
  const direct = representation.url?.trim()
  if (direct) return direct
  const base = representation.baseUrl?.trim().replace(/\/$/, '')
  return base ? `${base}/tiles/tileset.json` : ''
}

export function readyTableFreeRepresentation(representations: AssetRepresentation[], requiredVersion?: string) {
  return representations.find((representation) =>
    representation.kind === 'table-free' &&
    representation.format === '3d-tiles' &&
    representation.status === 'ready' &&
    (!requiredVersion || representation.version === requiredVersion) &&
    Boolean(pointcloudRepresentationUrl(representation)),
  ) ?? null
}

export function selectPointcloudRepresentation(
  sourceUrl: string,
  representations: AssetRepresentation[],
  preferTableFree: boolean,
  requiredVersion?: string,
): SelectedPointcloudRepresentation {
  const tableFree = readyTableFreeRepresentation(representations, requiredVersion)
  if (preferTableFree && tableFree) {
    return {
      kind: 'table-free',
      url: pointcloudRepresentationUrl(tableFree),
      version: tableFree.version,
      tableFreeAvailable: true,
    }
  }
  return {
    kind: 'source',
    url: sourceUrl,
    tableFreeAvailable: Boolean(tableFree),
  }
}
