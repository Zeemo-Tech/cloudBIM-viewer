/**
 * @cloudbim/bim-preview 设计模型预览页。
 *
 * 包含：IFC/GLB 全屏查看、线框与剖切、坐标轴/参考网格、画布背景、测量与定位，
 * 以及保形网格（重新生成网格）的状态与结果切换。
 *
 * 用法：
 * ```vue
 * <script setup lang="ts">
 * import { BimPreviewPage } from '@cloudbim/bim-preview'
 * import '@cloudbim/bim-preview/style.css'
 *
 * const handleBack = () => router.push('/design/bim')
 * </script>
 *
 * <template>
 *   <BimPreviewPage
 *     :asset-id="49"
 *     :display-name="'model.glb'"
 *     :project-name="'测试项目'"
 *     :project-id="1"
 *     back-label="返回模型列表"
 *     @back="handleBack"
 *   />
 * </template>
 * ```
 */
export { default as BimPreviewPage } from './BimPreviewPage.vue'
export { useBimRemeshDisplay } from './bimRemeshDisplay'
