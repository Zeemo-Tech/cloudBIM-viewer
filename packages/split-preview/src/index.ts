/**
 * @cloudbim/split-preview 模型 / 点云双屏对比页。
 *
 * 用法：
 * ```vue
 * <script setup lang="ts">
 * import { SplitPreviewPage } from '@cloudbim/split-preview'
 * import '@cloudbim/split-preview/style.css'
 *
 * const handleBack = () => router.push('/survey')
 * </script>
 *
 * <template>
 *   <SplitPreviewPage
 *     :bim-asset-id="49"
 *     :pointcloud-asset-id="60"
 *     :bim-display-name="'model.glb'"
 *     :pointcloud-display-name="'scan.las'"
 *     @back="handleBack"
 *   />
 * </template>
 * ```
 */
export { default as SplitPreviewPage } from './SplitPreviewPage.vue'
