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
    width="min(820px, 94vw)"
    top="5vh"
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
.file-upload-dialog{overflow:hidden;border:1px solid #dfe7f1;border-radius:22px;background:#f5f8fc;box-shadow:none}
.file-upload-dialog .el-dialog__header{box-sizing:border-box;height:58px;display:flex;align-items:center;margin:0;padding:0 22px;}
.file-upload-dialog .el-dialog__title{color:#2d486d;font-size:15px;font-weight:650}
.file-upload-dialog .el-dialog__headerbtn{top:8px;right:10px}
.file-upload-dialog .el-dialog__body{max-height:calc(90vh - 58px);padding:0;overflow:auto}
</style>
