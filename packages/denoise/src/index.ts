/**
 * @cloudbim/denoise 设计辅助点云去噪页。
 *
 * 只负责「点云分类与去噪」这一件事：读取后端最新的 BIM↔点云配准结果，调用
 * `POST /alignments/bim/denoise`，在查看器里按类别或按钢筋实例预览结果，并导出
 * 清洗后的钢筋点云（LAS）。页面不依赖 vue-router，也不依赖其它功能包。
 *
 * 前置条件（页面会自己从后端读取并在缺失时给出提示）：
 * - 点云已完成无台面预处理（否则可在页面内补充预处理）；
 * - 该 BIM 与点云组合已保存配准矩阵（去噪需要设计模型与点云对齐）。
 *
 * 用法：
 * ```vue
 * <script setup lang="ts">
 * import { DenoisePage } from '@cloudbim/denoise'
 * import '@cloudbim/denoise/style.css'
 *
 * const handleBack = () => router.push('/survey')
 * </script>
 *
 * <template>
 *   <DenoisePage
 *     :pointcloud-asset-id="60"
 *     :bim-asset-id="49"
 *     :pointcloud-display-name="'scan.las'"
 *     :bim-display-name="'model.glb'"
 *     @back="handleBack"
 *   />
 * </template>
 * ```
 */
export { default as DenoisePage } from './DenoisePage.vue'
export { default as DenoisePanel } from './DenoisePanel.vue'
export {
  DENOISE_CLASSES,
  applyDenoisePreviewAppearance,
  parseDenoisePreview,
  type DenoiseColorMode,
} from './denoisePreview'
