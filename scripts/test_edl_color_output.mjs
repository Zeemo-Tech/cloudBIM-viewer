// GPU regression: execute the real EDL shader against linear grid/background
// and depth fixtures, without depending on a browser, login or uploaded assets.
// Run: node --experimental-strip-types scripts/test_edl_color_output.mjs
import { execFileSync } from 'node:child_process'
import { fileURLToPath } from 'node:url'
import * as THREE from 'three'
import { PointCloudEdlPipeline } from '../src/components/preview/edlPipeline.ts'

const pipeline = new PointCloudEdlPipeline({})
const shader = pipeline.material
const expand = source => source.replace(/#include <(\w+)>/g, (_, name) => THREE.ShaderChunk[name])
const vertex = `#version 300 es
precision highp float;
in vec3 position;
#define uv (position.xy * 0.5 + 0.5)
${shader.vertexShader.replaceAll('varying', 'out')}`
const fragment = `#version 300 es
precision highp float;
out vec4 fragColor;
${THREE.ShaderChunk.colorspace_pars_fragment}
vec4 linearToOutputTexel(vec4 value) { return sRGBTransferOETF(value); }
${expand(shader.fragmentShader).replaceAll('varying', 'in').replaceAll('texture2D', 'texture').replaceAll('gl_FragColor', 'fragColor')}`
try {
  const result = execFileSync(
    fileURLToPath(new URL('../.cloudbim/mesh-venv/bin/python', import.meta.url)),
    [fileURLToPath(new URL('./test_edl_color_output.py', import.meta.url))],
    { input: JSON.stringify({ vertex, fragment }), encoding: 'utf8' },
  )
  process.stdout.write(result)
} finally {
  pipeline.dispose()
}
