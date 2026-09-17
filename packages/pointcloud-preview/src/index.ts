/**
 * @cloudbim/pointcloud-preview 扫描点云预览页。
 *
 * 包含：3D Tiles 点云全屏查看、真彩/强度/台面分色与色带、EDL 显示增强、点大小、
 * 台面隐藏与补充预处理、视角导航立方体、剖面，以及测量（测距/定位/面积）。
 *
 * 用法：
 * ```vue
 * <script setup lang="ts">
 * import { PointcloudPreviewPage } from '@cloudbim/pointcloud-preview'
 * import '@cloudbim/pointcloud-preview/style.css'
 *
 * const handleBack = () => router.push('/survey')
 * </script>
 *
 * <template>
 *   <PointcloudPreviewPage
 *     :asset-id="5"
 *     :display-name="'YB-1mesh2.0.las'"
 *     :project-name="'测试项目'"
 *     @back="handleBack"
 *   />
 * </template>
 * ```
 */
export { default as PointcloudPreviewPage } from './PointcloudPreviewPage.vue'
