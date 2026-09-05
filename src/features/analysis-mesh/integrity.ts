import type * as THREE from 'three'

import { AnalysisMeshContractError } from './contracts'

export async function sha256Hex(buffer: ArrayBuffer): Promise<string> {
  const subtle = globalThis.crypto?.subtle
  if (!subtle) throw new AnalysisMeshContractError('Web Crypto SHA-256 is unavailable')
  const digest = await subtle.digest('SHA-256', buffer)
  return Array.from(new Uint8Array(digest), (value) => value.toString(16).padStart(2, '0')).join('')
}

export async function verifyPayloadHash(buffer: ArrayBuffer, expected: string): Promise<void> {
  if ((await sha256Hex(buffer)).toLowerCase() !== expected.toLowerCase()) {
    throw new AnalysisMeshContractError('artifact payload SHA-256 mismatch')
  }
}

export async function verifyPositionStreamHash(
  geometry: THREE.BufferGeometry,
  expected: string,
): Promise<void> {
  const position = geometry.getAttribute('position')
  if (!position || position.itemSize < 3) {
    throw new AnalysisMeshContractError('analysis tile has no position stream')
  }
  const bytes = new ArrayBuffer(position.count * 3 * Float32Array.BYTES_PER_ELEMENT)
  const view = new DataView(bytes)
  for (let index = 0; index < position.count; index += 1) {
    const offset = index * 3 * Float32Array.BYTES_PER_ELEMENT
    view.setFloat32(offset, position.getX(index), true)
    view.setFloat32(offset + 4, position.getY(index), true)
    view.setFloat32(offset + 8, position.getZ(index), true)
  }
  if ((await sha256Hex(bytes)).toLowerCase() !== expected.toLowerCase()) {
    throw new AnalysisMeshContractError('analysis tile positionHash mismatch')
  }
}
