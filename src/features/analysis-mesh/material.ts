import * as THREE from 'three'

import { AnalysisMeshContractError } from './contracts'

export type C2MColorMode = 'continuous' | 'discrete'

const vertexShader = /* glsl */ `
  attribute float c2mDistance;
  varying float vDistance;
  void main() {
    vDistance = c2mDistance;
    gl_Position = projectionMatrix * modelViewMatrix * vec4(position, 1.0);
  }
`

const fragmentShader = /* glsl */ `
  varying float vDistance;
  uniform float tolerance;
  uniform float colorRange;
  uniform float bandCount;
  uniform float discreteMode;

  vec3 colorStop(float position) {
    const vec3 c0 = vec3(0.050980, 0.278431, 0.631373);
    const vec3 c1 = vec3(0.000000, 0.737255, 0.831373);
    const vec3 c2 = vec3(0.000000, 0.784314, 0.325490);
    const vec3 c3 = vec3(1.000000, 0.839216, 0.000000);
    const vec3 c4 = vec3(0.835294, 0.000000, 0.000000);
    float scaled = clamp(position, 0.0, 1.0) * 4.0;
    if (scaled < 1.0) return mix(c0, c1, scaled);
    if (scaled < 2.0) return mix(c1, c2, scaled - 1.0);
    if (scaled < 3.0) return mix(c2, c3, scaled - 2.0);
    return mix(c3, c4, scaled - 3.0);
  }

  float distancePosition(float value) {
    if (value <= -tolerance) {
      return 0.25 * (value + colorRange) / (colorRange - tolerance);
    }
    if (value <= 0.0) return 0.25 + 0.25 * (value + tolerance) / tolerance;
    if (value < tolerance) return 0.5 + 0.25 * value / tolerance;
    return 0.75 + 0.25 * (value - tolerance) / (colorRange - tolerance);
  }

  void main() {
    bool known = vDistance <= 0.0 || vDistance > 0.0;
    if (!known || abs(vDistance) > colorRange) {
      gl_FragColor = vec4(0.227451, 0.227451, 0.227451, 1.0);
      return;
    }
    float position = clamp(distancePosition(vDistance), 0.0, 1.0);
    if (discreteMode > 0.5) {
      position = floor(position * (bandCount - 1.0) + 0.5) / (bandCount - 1.0);
    }
    gl_FragColor = vec4(colorStop(position), 1.0);
  }
`

export class C2MShaderMaterial extends THREE.ShaderMaterial {
  constructor() {
    super({
      uniforms: {
        tolerance: { value: 0.05 },
        colorRange: { value: 0.1 },
        bandCount: { value: 5 },
        discreteMode: { value: 0 },
      },
      vertexShader,
      fragmentShader,
      side: THREE.DoubleSide,
    })
  }

  setDistances(geometry: THREE.BufferGeometry, distances: Float32Array) {
    const positions = geometry.getAttribute('position')
    if (!positions || positions.count !== distances.length) {
      throw new AnalysisMeshContractError('C2M geometry vertex count mismatch')
    }
    geometry.setAttribute('c2mDistance', new THREE.BufferAttribute(distances, 1))
    return this
  }

  setMode(mode: C2MColorMode) {
    this.uniforms.discreteMode.value = mode === 'discrete' ? 1 : 0
  }

  setThresholds(tolerance: number, colorRange: number, bandCount = 5) {
    if (!(tolerance > 0) || !(colorRange > tolerance)) {
      throw new AnalysisMeshContractError('color range must be greater than positive tolerance')
    }
    this.uniforms.tolerance.value = tolerance
    this.uniforms.colorRange.value = colorRange
    this.uniforms.bandCount.value = Math.max(2, Math.floor(bandCount))
  }
}
