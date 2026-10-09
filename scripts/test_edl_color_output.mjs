// GPU regression: execute the real EDL shader against linear grid/background
// and depth fixtures, without depending on a browser, login or uploaded assets.
// Run: node --experimental-strip-types scripts/test_edl_color_output.mjs
import { execFileSync } from 'node:child_process'
import { existsSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import * as THREE from 'three'
import { PointCloudEdlPipeline } from '../packages/viewer-core/src/components/preview/edlPipeline.ts'

// The pixel test drives a surfaceless EGL context via libEGL.so.1, so it only
// runs on Linux hosts that also have the mesh-service venv. Skip elsewhere
// instead of reporting a hard failure for a missing platform dependency.
const pythonPath = fileURLToPath(new URL('../.cloudbim/mesh-venv/bin/python', import.meta.url))
if (process.platform !== 'linux' || !existsSync(pythonPath)) {
  console.log(`SKIP: EDL color output requires Linux + EGL and ${pythonPath}; platform=${process.platform}`)
  process.exit(0)
}

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
    pythonPath,
    [fileURLToPath(new URL('./test_edl_color_output.py', import.meta.url))],
    { input: JSON.stringify({ vertex, fragment }), encoding: 'utf8' },
  )
  process.stdout.write(result)
} finally {
  pipeline.dispose()
}
