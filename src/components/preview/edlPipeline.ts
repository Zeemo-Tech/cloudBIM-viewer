import * as THREE from "three";

export type EdlPipelineOptions = {
  /** 默认开启，接近 Potree 观感 */
  enabled?: boolean;
  /** 明暗强度，越大边缘越深 */
  strength?: number;
  /** 邻域采样半径（像素） */
  radius?: number;
};

const DEFAULT_STRENGTH = 1.0;
const DEFAULT_RADIUS = 1.0;

const edlVertexShader = /* glsl */ `
varying vec2 vUv;

void main() {
  vUv = uv;
  gl_Position = vec4(position.xy, 0.0, 1.0);
}
`;

/**
 * Eye-Dome Lighting：屏幕空间邻域深度差做边缘遮挡。
 * 点云像素之间常露出背景；邻域若落到背景必须忽略，否则近处会被整片涂黑。
 */
const edlFragmentShader = /* glsl */ `
precision highp float;

uniform sampler2D tDiffuse;
uniform sampler2D tDepth;
uniform vec2 resolution;
uniform float cameraNear;
uniform float cameraFar;
uniform float edlStrength;
uniform float edlRadius;
uniform float orthographic;

varying vec2 vUv;

float perspectiveDepthToViewZ(const in float invClipZ, const in float near, const in float far) {
  return (near * far) / ((far - near) * invClipZ - far);
}

float readLinearDepth(const in float fragCoordZ) {
  if (orthographic > 0.5) {
    return mix(cameraNear, cameraFar, fragCoordZ);
  }
  float viewZ = perspectiveDepthToViewZ(fragCoordZ, cameraNear, cameraFar);
  return max(-viewZ, 1e-6);
}

float neighborResponse(const in float depth, const in vec2 offset) {
  float neighbourZ = texture2D(tDepth, vUv + offset).x;
  // 间隙露背景：不算遮挡，避免钻进隧道后近处大片发黑
  if (neighbourZ >= 0.999999) return 0.0;
  float neighbour = readLinearDepth(neighbourZ);
  return max(0.0, log2(neighbour) - log2(depth));
}

void main() {
  vec4 color = texture2D(tDiffuse, vUv);
  float fragCoordZ = texture2D(tDepth, vUv).x;

  // 背景 / 清空像素不做 EDL
  if (fragCoordZ >= 0.999999) {
    gl_FragColor = color;
    return;
  }

  float depth = readLinearDepth(fragCoordZ);
  vec2 texel = edlRadius / resolution;

  float response = 0.0;
  response += neighborResponse(depth, vec2(-texel.x, 0.0));
  response += neighborResponse(depth, vec2( texel.x, 0.0));
  response += neighborResponse(depth, vec2(0.0, -texel.y));
  response += neighborResponse(depth, vec2(0.0,  texel.y));
  response += neighborResponse(depth, vec2(-texel.x, -texel.y));
  response += neighborResponse(depth, vec2(-texel.x,  texel.y));
  response += neighborResponse(depth, vec2( texel.x, -texel.y));
  response += neighborResponse(depth, vec2( texel.x,  texel.y));
  response *= 0.125;

  // 限制最暗比例，避免遮挡边被压成实心黑斑
  float shade = exp(-response * 220.0 * edlStrength);
  shade = max(shade, 0.22);
  gl_FragColor = vec4(color.rgb * shade, color.a);
}
`;

/**
 * 点云 EDL 后处理管线：场景 → 带深度 RT → EDL 全屏着色 → 屏幕。
 * enabled=false 时直接 renderer.render，零额外开销。
 */
export class PointCloudEdlPipeline {
  enabled: boolean;
  strength: number;
  radius: number;

  private readonly renderer: THREE.WebGLRenderer;
  private renderTarget: THREE.WebGLRenderTarget;
  private readonly fsScene = new THREE.Scene();
  private readonly fsCamera = new THREE.OrthographicCamera(-1, 1, 1, -1, 0, 1);
  private readonly material: THREE.ShaderMaterial;
  private readonly quad: THREE.Mesh;
  private width = 1;
  private height = 1;

  constructor(renderer: THREE.WebGLRenderer, options: EdlPipelineOptions = {}) {
    this.renderer = renderer;
    this.enabled = options.enabled ?? true;
    this.strength = options.strength ?? DEFAULT_STRENGTH;
    this.radius = options.radius ?? DEFAULT_RADIUS;

    this.renderTarget = this.createTarget(1, 1);
    this.material = new THREE.ShaderMaterial({
      uniforms: {
        tDiffuse: { value: null as THREE.Texture | null },
        tDepth: { value: null as THREE.Texture | null },
        resolution: { value: new THREE.Vector2(1, 1) },
        cameraNear: { value: 0.1 },
        cameraFar: { value: 1000 },
        edlStrength: { value: this.strength },
        edlRadius: { value: this.radius },
        orthographic: { value: 0 },
      },
      vertexShader: edlVertexShader,
      fragmentShader: edlFragmentShader,
      depthTest: false,
      depthWrite: false,
      // 场景以线性写入 RT；最终 blit 交给 renderer 做 tone mapping / sRGB。
      toneMapped: true,
    });
    this.quad = new THREE.Mesh(new THREE.PlaneGeometry(2, 2), this.material);
    this.quad.frustumCulled = false;
    this.fsScene.add(this.quad);
  }

  setEnabled(enabled: boolean) {
    this.enabled = enabled;
  }

  setStrength(strength: number) {
    this.strength = Math.max(0, strength);
    this.material.uniforms.edlStrength.value = this.strength;
  }

  setRadius(radius: number) {
    this.radius = Math.max(0.25, radius);
    this.material.uniforms.edlRadius.value = this.radius;
  }

  setSize(width: number, height: number) {
    const nextWidth = Math.max(1, Math.floor(width));
    const nextHeight = Math.max(1, Math.floor(height));
    if (nextWidth === this.width && nextHeight === this.height) return;
    this.width = nextWidth;
    this.height = nextHeight;
    this.renderTarget.setSize(nextWidth, nextHeight);
    this.material.uniforms.resolution.value.set(nextWidth, nextHeight);
  }

  /** 按 drawing buffer 尺寸同步（含 pixelRatio）。 */
  syncFromRenderer() {
    const size = new THREE.Vector2();
    this.renderer.getDrawingBufferSize(size);
    this.setSize(size.x, size.y);
  }

  render(scene: THREE.Scene, camera: THREE.Camera) {
    if (!this.enabled) {
      this.renderer.setRenderTarget(null);
      this.renderer.render(scene, camera);
      return;
    }

    try {
      this.syncFromRenderer();

      const previousTarget = this.renderer.getRenderTarget();
      const previousAutoClear = this.renderer.autoClear;
      const previousToneMapping = this.renderer.toneMapping;
      const previousOutputColorSpace = this.renderer.outputColorSpace;

      // 线性场景色进 RT，EDL 在线性空间做邻域明暗，最后再 tone map。
      this.renderer.setRenderTarget(this.renderTarget);
      this.renderer.toneMapping = THREE.NoToneMapping;
      this.renderer.outputColorSpace = THREE.LinearSRGBColorSpace;
      this.renderer.autoClear = true;
      this.renderer.clear();
      this.renderer.render(scene, camera);

      const near =
        "near" in camera && typeof camera.near === "number" ? camera.near : 0.1;
      const far =
        "far" in camera && typeof camera.far === "number" ? camera.far : 1000;
      this.material.uniforms.tDiffuse.value = this.renderTarget.texture;
      this.material.uniforms.tDepth.value = this.renderTarget.depthTexture;
      this.material.uniforms.cameraNear.value = near;
      this.material.uniforms.cameraFar.value = far;
      this.material.uniforms.edlStrength.value = this.strength;
      this.material.uniforms.edlRadius.value = this.radius;
      this.material.uniforms.orthographic.value = (
        camera as THREE.OrthographicCamera
      ).isOrthographicCamera
        ? 1
        : 0;

      this.renderer.toneMapping = previousToneMapping;
      this.renderer.outputColorSpace = previousOutputColorSpace;
      this.renderer.setRenderTarget(null);
      this.renderer.autoClear = true;
      this.renderer.render(this.fsScene, this.fsCamera);

      this.renderer.autoClear = previousAutoClear;
      this.renderer.setRenderTarget(previousTarget);
    } catch (error) {
      // RT / 深度纹理异常时回退直渲，避免整页卡在加载态
      console.error("EDL render failed, fallback to direct render", error);
      this.enabled = false;
      this.renderer.setRenderTarget(null);
      this.renderer.render(scene, camera);
    }
  }

  dispose() {
    this.renderTarget.dispose();
    this.renderTarget.depthTexture?.dispose();
    this.material.dispose();
    this.quad.geometry.dispose();
  }

  private createTarget(width: number, height: number) {
    const depthTexture = new THREE.DepthTexture(width, height);
    depthTexture.format = THREE.DepthFormat;
    depthTexture.type = THREE.UnsignedIntType;

    const target = new THREE.WebGLRenderTarget(width, height, {
      minFilter: THREE.LinearFilter,
      magFilter: THREE.LinearFilter,
      format: THREE.RGBAFormat,
      type: THREE.UnsignedByteType,
      depthBuffer: true,
      stencilBuffer: false,
      depthTexture,
    });
    target.texture.colorSpace = THREE.LinearSRGBColorSpace;
    return target;
  }
}
