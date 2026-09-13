<script setup lang="ts">
import SimpleUploadView from '@/views/upload/SimpleUploadView.vue'
import type { AuthSession } from '@/features/auth/auth.service'
import type { UploadKind } from '@/features/upload/upload.types'

withDefaults(defineProps<{
  modelValue: boolean
  session: AuthSession
  projectId: number
  projectName?: string
  allowedKinds?: UploadKind[]
  title?: string
}>(), {
  allowedKinds: () => ['bim', 'pointcloud'],
  title: '上传文件',
})

const emit = defineEmits<{
  'update:modelValue': [value: boolean]
  uploaded: [kind: UploadKind]
}>()

function handleUploaded(kind: UploadKind) {
  emit('uploaded', kind)
  emit('update:modelValue', false)
}
</script>

<template>
  <el-dialog
    class="file-upload-dialog"
    :model-value="modelValue"
    :title="title"
    width="min(760px, calc(100vw - 32px))"
    top="max(16px, 5dvh)"
    append-to-body
    destroy-on-close
    :close-on-click-modal="false"
    @update:model-value="emit('update:modelValue', $event)"
  >
    <SimpleUploadView
      :session="session"
      :project-id="projectId"
      :project-name="projectName"
      :allowed-kinds="allowedKinds"
      compact
      @uploaded="handleUploaded"
    />
  </el-dialog>
</template>

<style>
.file-upload-dialog { padding: var(--workspace-gutter); border: 1px solid var(--border-color-light); border-radius: var(--radius-md); background: var(--bg-card); box-shadow: var(--shadow-lg); }
.file-upload-dialog .el-dialog__header { margin: 0 0 var(--workspace-gap); padding: 0 var(--spacing-xl) 0 0; }
.file-upload-dialog .el-dialog__title { font-size: var(--font-size-lg); font-weight: 600; color: var(--text-primary); }
.file-upload-dialog .el-dialog__headerbtn { top: var(--spacing-sm); right: var(--spacing-sm); }
.file-upload-dialog .el-dialog__body { max-height: calc(90vh - 96px); max-height: calc(90dvh - 96px); padding: 0; overflow-y: auto; overscroll-behavior: contain; }
</style>
