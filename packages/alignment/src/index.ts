/**
 * @cloudbim/alignment 配准与分析工作区。
 *
 * 四步流程：点云与工程坐标配准 → 点云分类与去噪 → 偏差对比（含 C2M 距离分析、
 * 钢筋巡检与逐钢筋对比）→ 出报告。
 *
 * 用法：
 * ```vue
 * <script setup lang="ts">
 * import { AlignmentPage } from '@cloudbim/alignment'
 * import '@cloudbim/alignment/style.css'
 *
 * const handleBack = () => router.push('/survey')
 * const handleStepChange = (step: number) =>
 *   router.replace({ query: { ...route.query, step: String(step) } })
 * </script>
 *
 * <template>
 *   <AlignmentPage
 *     :bim-asset-id="49"
 *     :pointcloud-asset-id="60"
 *     :bim-display-name="'model.glb'"
 *     :pointcloud-display-name="'scan.las'"
 *     :initial-step="Number(route.query.step) || 1"
 *     @back="handleBack"
 *     @step-change="handleStepChange"
 *   />
 * </template>
 * ```
 */
export { default as AlignmentPage } from './AlignmentPage.vue'
// 去噪面板已迁往独立功能包，这里仅作兼容重导出。
export { DenoisePanel } from '@cloudbim/denoise'
