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
    const vec3 blue = vec3(59.0, 130.0, 246.0) / 255.0;
    const vec3 cyan = vec3(34.0, 211.0, 238.0) / 255.0;
    const vec3 edge = vec3(134.0, 239.0, 172.0) / 255.0;
    const vec3 green = vec3(34.0, 197.0, 94.0) / 255.0;
    const vec3 amber = vec3(251.0, 191.0, 36.0) / 255.0;
    const vec3 red = vec3(255.0, 82.0, 82.0) / 255.0;
    float t = clamp(position, 0.0, 1.0);
    if (t < 0.25) return mix(blue, cyan, t * 4.0);
    if (t <= 0.5) return mix(edge, green, (t - 0.25) * 4.0);
    if (t <= 0.75) return mix(green, edge, (t - 0.5) * 4.0);
    return mix(amber, red, (t - 0.75) * 4.0);
  }

  float distancePosition(float value) {
    if (value < -tolerance) {
      return 0.25 * (value + colorRange) / (colorRange - tolerance);
    }
    if (value <= 0.0) return 0.25 + 0.25 * (value + tolerance) / tolerance;
    if (value <= tolerance) return 0.5 + 0.25 * value / tolerance;
    return 0.75 + 0.25 * (value - tolerance) / (colorRange - tolerance);
  }

  void main() {
    bool known = vDistance <= 0.0 || vDistance > 0.0;
    if (!known) {
      gl_FragColor = vec4(vec3(168.0, 178.0, 193.0) / 255.0, 1.0);
      return;
    }
    float position = clamp(distancePosition(vDistance), 0.0, 1.0);
    if (discreteMode > 0.5) {
      if (position < 0.25) position = min(0.249999, floor(position * 4.0 * bandCount) / bandCount / 4.0);
      else if (position > 0.75) position = max(0.750001, 0.75 + ceil((position - 0.75) * 4.0 * bandCount) / bandCount / 4.0);
      else position = 0.25 + floor((position - 0.25) * 2.0 * bandCount + 0.5) / bandCount / 2.0;
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
