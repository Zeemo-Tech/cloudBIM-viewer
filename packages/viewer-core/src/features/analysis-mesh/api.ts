import {
  parseAnalysisMeshComponents,
  parseAnalysisMeshManifest,
  parseAnalysisC2MManifest,
  type AnalysisC2MManifest,
  type AnalysisMeshComponents,
  type AnalysisMeshManifest,
} from './contracts'

const ensureTrailingSlash = (value: string) => (value.endsWith('/') ? value : `${value}/`)

export function resolveAnalysisArtifactURL(baseURL: string, relativePath: string) {
  const normalized = ensureTrailingSlash(baseURL)
  if (/^(?:https?:)?\/\//i.test(normalized)) return new URL(relativePath, normalized).toString()
  if (typeof window !== 'undefined') {
    return new URL(relativePath, new URL(normalized, window.location.origin)).toString()
  }
  return `${normalized}${relativePath}`
}

async function fetchJSON(url: string, fetcher: typeof fetch) {
  const response = await fetcher(url)
  if (!response.ok) throw new Error(`analysis mesh request failed: ${response.status}`)
  return response.json() as Promise<unknown>
}

export type AnalysisMeshArtifact = {
  baseURL: string
  manifest: AnalysisMeshManifest
  components: AnalysisMeshComponents
  tilesetURL: string
}

export async function loadAnalysisMeshArtifact(baseURL: string, fetcher: typeof fetch = fetch) {
  const normalized = ensureTrailingSlash(baseURL)
  const manifest = parseAnalysisMeshManifest(await fetchJSON(`${normalized}manifest.json`, fetcher))
  const components = parseAnalysisMeshComponents(
    await fetchJSON(resolveAnalysisArtifactURL(normalized, manifest.componentsPath), fetcher),
  )
  if (
    manifest.componentCount !== components.components.length ||
    manifest.tileCount !== components.tiles.length
  ) {
    throw new Error('analysis mesh manifest and component index counts differ')
  }
  return {
    baseURL: normalized,
    manifest,
    components,
    tilesetURL: resolveAnalysisArtifactURL(normalized, manifest.entryPath),
  } satisfies AnalysisMeshArtifact
}

export function analysisMeshRepresentationBase(assetId: number, version: string) {
  return `/assets/${assetId}/representations/analysis-mesh/${encodeURIComponent(version)}/`
}

export async function loadAnalysisC2MManifest(url: string, fetcher: typeof fetch = fetch) {
  return parseAnalysisC2MManifest(await fetchJSON(url, fetcher)) satisfies AnalysisC2MManifest
}
