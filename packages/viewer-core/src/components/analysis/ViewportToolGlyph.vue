<script setup lang="ts">
/**
 * 公司固定资产项目统一的分析视口图标。
 * 图形与 Tunnel Section Analyzer 的 CalibrationWorkspace 保持一致。
 */
type ViewportToolGlyphName =
  | 'projectionOrthographic'
  | 'projectionPerspective'
  | 'grid'
  | 'wireframe'
  | 'measure'
  | 'pointCloud'
  | 'solidModel'
  | 'clipping'

withDefaults(defineProps<{
  name: ViewportToolGlyphName
  hidden?: boolean
  size?: number | string
}>(), {
  hidden: false,
  size: 18,
})
</script>

<template>
  <svg
    class="viewport-tool-glyph"
    xmlns="http://www.w3.org/2000/svg"
    viewBox="0 0 16 16"
    :width="size"
    :height="size"
    aria-hidden="true"
    focusable="false"
  >
    <g v-if="name === 'projectionOrthographic'">
      <path d="M2.5 5.5h8v8h-8z" fill="none" stroke="currentColor" stroke-width="1.25" stroke-linejoin="round" />
      <path d="M2.5 5.5l3-3h8M10.5 5.5l3-3M10.5 13.5l3-3V2.5" fill="none" stroke="currentColor" stroke-width="1.25" stroke-linejoin="round" stroke-linecap="round" />
    </g>
    <g v-else-if="name === 'projectionPerspective'">
      <path d="M2.5 5.5h8v8h-8z" fill="none" stroke="currentColor" stroke-width="1.25" stroke-linejoin="round" />
      <path d="M5 3h6.5v5M2.5 5.5L5 3M10.5 5.5l1-2.5M10.5 13.5l1-5.5M2.5 13.5L5 8" fill="none" stroke="currentColor" stroke-width="1.25" stroke-linejoin="round" stroke-linecap="round" />
    </g>
    <g v-else-if="name === 'grid'">
      <path d="M5 2.2v11.6M8 2.2v11.6M11 2.2v11.6M2.2 5h11.6M2.2 8h11.6M2.2 11h11.6" fill="none" stroke="currentColor" stroke-width="1.15" stroke-linecap="round" />
    </g>
    <g v-else-if="name === 'wireframe'">
      <circle cx="8" cy="8" r="5.5" fill="none" stroke="currentColor" stroke-width="1.15" />
      <ellipse cx="8" cy="8" rx="5.5" ry="2" fill="none" stroke="currentColor" stroke-width="1.1" />
      <ellipse cx="8" cy="8" rx="2" ry="5.5" fill="none" stroke="currentColor" stroke-width="1.1" />
      <ellipse cx="8" cy="5.4" rx="4.7" ry="1.15" fill="none" stroke="currentColor" stroke-width="1" />
      <ellipse cx="8" cy="10.6" rx="4.7" ry="1.15" fill="none" stroke="currentColor" stroke-width="1" />
    </g>
    <g v-else-if="name === 'measure'">
      <path d="M3.5 5.5v5M12.5 5.5v5M3.5 8h9" fill="none" stroke="currentColor" stroke-width="1.15" stroke-linecap="round" />
      <path d="M3.5 8 5.6 6.4 5.6 9.6zM12.5 8 10.4 6.4 10.4 9.6z" fill="currentColor" />
    </g>
    <g v-else-if="name === 'pointCloud'">
      <circle cx="5" cy="4.5" r="0.95" fill="currentColor" />
      <circle cx="8.5" cy="3.5" r="0.95" fill="currentColor" />
      <circle cx="11.5" cy="5.5" r="0.95" fill="currentColor" />
      <circle cx="4" cy="8" r="0.95" fill="currentColor" />
      <circle cx="7.5" cy="7.5" r="0.95" fill="currentColor" />
      <circle cx="10.5" cy="9.5" r="0.95" fill="currentColor" />
      <circle cx="6" cy="11.5" r="0.95" fill="currentColor" />
      <circle cx="9.5" cy="12.5" r="0.95" fill="currentColor" />
    </g>
    <g v-else-if="name === 'solidModel'">
      <path d="M3.5 7.2h2v5.3h-2zM10.5 7.2h2v5.3h-2z" fill="currentColor" opacity="0.34" />
      <path d="M3.5 4.8h9v2.4H3.5z" fill="currentColor" opacity="0.48" />
      <path d="M3.5 12.5V7.2h2V12.5M10.5 12.5V7.2h2V12.5M3.5 7.2h9M3.5 4.8h9v2.4H3.5M4.5 4.8l1-1h7l1 1" fill="none" stroke="currentColor" stroke-width="1.15" stroke-linejoin="round" stroke-linecap="round" />
    </g>
    <g v-else-if="name === 'clipping'">
      <path d="M3.5 4.5h5.5v7H3.5z" fill="currentColor" opacity="0.22" />
      <path d="M3.5 4.5h5.5v7H3.5zM9 3.5v9" fill="none" stroke="currentColor" stroke-width="1.15" stroke-linejoin="round" stroke-linecap="round" />
      <path d="M9.8 4.8h3.2v2.4M9.8 8.8h3.2v2.4M10.2 4.5v7M13.4 4.5v7" fill="none" stroke="currentColor" stroke-width="1.1" stroke-linecap="round" stroke-dasharray="1.5 1.3" />
    </g>
    <path v-if="hidden" d="M3 13L13 3" fill="none" stroke="currentColor" stroke-width="1.35" stroke-linecap="round" />
  </svg>
</template>

<style scoped>
.viewport-tool-glyph {
  display: block;
  flex-shrink: 0;
}
</style>
