<script setup lang="ts">
import { computed } from 'vue'

const props = withDefaults(defineProps<{
  total: number
  currentPage: number
  pageSize: number
  pageSizeOptions?: number[]
}>(), {
  pageSizeOptions: () => [6, 8, 10, 12],
})

const emit = defineEmits<{
  'update:currentPage': [value: number]
  'update:pageSize': [value: number]
}>()

const pageCount = computed(() => Math.max(1, Math.ceil(props.total / props.pageSize)))
const visiblePages = computed<Array<number | string>>(() => {
  if (pageCount.value <= 7) return Array.from({ length: pageCount.value }, (_, index) => index + 1)
  if (props.currentPage <= 4) return [1, 2, 3, 4, 5, 'ellipsis', pageCount.value]
  if (props.currentPage >= pageCount.value - 3) {
    return [1, 'ellipsis', pageCount.value - 4, pageCount.value - 3, pageCount.value - 2, pageCount.value - 1, pageCount.value]
  }
  return [1, 'ellipsis', props.currentPage - 1, props.currentPage, props.currentPage + 1, 'ellipsis-end', pageCount.value]
})

function setPage(page: number) {
  emit('update:currentPage', Math.min(Math.max(1, page), pageCount.value))
}

function setPageSize(event: Event) {
  emit('update:pageSize', Number((event.target as HTMLSelectElement).value))
  emit('update:currentPage', 1)
}
</script>

<template>
  <footer v-if="props.total > 0" class="project-table-footer" role="navigation">
    <div class="pagination">
      <span class="pager-summary">共 {{ props.total }} 条</span>
      <button type="button" class="pager-button" aria-label="上一页" title="上一页" :disabled="props.currentPage <= 1" @click.prevent="setPage(props.currentPage - 1)">‹</button>
      <template v-for="page in visiblePages" :key="String(page)">
        <button v-if="typeof page === 'number'" type="button" class="pager-button" :class="{ active: page === props.currentPage }" :aria-label="`第 ${page} 页`" :aria-current="page === props.currentPage ? 'page' : undefined" @click.prevent="setPage(page)">{{ page }}</button>
        <span v-else class="pager-ellipsis">...</span>
      </template>
      <button type="button" class="pager-button" aria-label="下一页" title="下一页" :disabled="props.currentPage >= pageCount" @click.prevent="setPage(props.currentPage + 1)">›</button>
      <label class="page-size">
        <span class="sr-only">每页显示条数</span>
        <select :value="props.pageSize" @change="setPageSize">
          <option v-for="size in props.pageSizeOptions" :key="size" :value="size">{{ size }} 条/页</option>
        </select>
      </label>
    </div>
  </footer>
</template>

<style scoped>
.project-table-footer{flex:0 0 auto;display:flex;justify-content:center;margin-top:var(--spacing-md);padding:4px 0}.pagination{display:flex;align-items:center;justify-content:center;gap:8px;padding:8px 12px;color:#6f82a0}.pager-summary{margin-right:4px;font-size:13px;font-weight:400;white-space:nowrap}.pager-button,.page-size{height:40px;border:0;border-radius:10px;background:#fff;color:#7b8ca7;box-shadow:5px 5px 12px rgb(169 184 210 / 22%),-5px -5px 12px rgb(255 255 255 / 96%),inset 1px 1px 0 rgb(255 255 255 / 92%)}.pager-button{min-width:40px;padding:0 10px;font-size:13px;font-weight:400;cursor:pointer}.pager-button.active{color:#fff;background:linear-gradient(145deg,#88b4f8,#73a2f3)}.pager-button:disabled{color:#b8c2d2;background:#fff;box-shadow:none;cursor:not-allowed}.pager-ellipsis{padding:0 3px;color:#9caac0;font-weight:400}.page-size{display:inline-flex;align-items:center;overflow:hidden;margin-left:4px}.page-size select{height:40px;min-width:100px;padding:0 12px;border:0;outline:0;color:#536a8c;background:transparent;font-size:13px;font-weight:400;cursor:pointer}.sr-only{position:absolute;width:1px;height:1px;padding:0;margin:-1px;overflow:hidden;clip:rect(0,0,0,0);white-space:nowrap;border:0}.pager-button:focus-visible,.page-size:focus-within{outline:2px solid var(--border-color-focus);outline-offset:2px}@media(max-width:620px){.pagination{flex-wrap:wrap}}
</style>

<style scoped>
.project-table-footer { padding: var(--spacing-md) 0 var(--spacing-xs); }
.pagination { gap: var(--spacing-sm); color: var(--text-secondary); }
.pager-summary { font-size: var(--font-size-sm); }
.pager-button, .page-size { border-radius: var(--radius-sm); background: var(--bg-card); color: var(--text-secondary); box-shadow: var(--shadow-sm); }
.pager-button { font-size: var(--font-size-sm); }
.pager-button.active { color: var(--bg-card); background: var(--color-primary); }
.pager-button:disabled { color: var(--text-disabled); background: var(--bg-card); }
.pager-ellipsis { color: var(--text-tertiary); }
.page-size select { color: var(--text-secondary); font-size: var(--font-size-sm); }
</style>

<style scoped lang="scss">
@use '@cloudbim/viewer-core/styles/workspace-controls.scss' as controls;
.project-table-footer { margin-top: var(--spacing-md); padding: 0; justify-content: flex-end; } .pager-button { @include controls.action; min-width: var(--control-height); padding: 0 var(--spacing-sm); } .pager-button.active { @include controls.primary; } .page-size { border: 1px solid var(--border-color); box-shadow: none; height: var(--control-height); } .page-size select { height: 100%; }
</style>
