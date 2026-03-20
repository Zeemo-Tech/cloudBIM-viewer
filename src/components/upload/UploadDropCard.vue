<script setup lang="ts">
import { computed, ref } from 'vue'
import { ElMessage } from 'element-plus'
import {
  Box,
  CircleCheckFilled,
  Delete,
  Document,
  Files,
  FolderOpened,
  UploadFilled,
} from '@element-plus/icons-vue'
import type { UploadFileConfig } from '@/features/upload/upload.types'
import {
  formatFileSize,
  getFileExtension,
  matchesAcceptedExtension,
} from '@/features/upload/upload.utils'

const props = defineProps<{
  config: UploadFileConfig
  file: File | null
}>()

const emit = defineEmits<{
  'update:file': [file: File | null]
}>()

const inputRef = ref<HTMLInputElement | null>(null)
const isDragging = ref(false)

const iconComponent = computed(() => {
  return props.config.kind === 'bim' ? Box : Files
})

const fileTypeLabel = computed(() => {
  if (!props.file) {
    return props.config.kind === 'bim' ? 'BIM 模型' : '点云文件'
  }

  return getFileExtension(props.file.name).toUpperCase() || 'FILE'
})

function openFilePicker() {
  inputRef.value?.click()
}

function emitFile(file: File) {
  if (!matchesAcceptedExtension(file.name, props.config.extensions)) {
    ElMessage({
      type: 'warning',
      grouping: true,
      message: `${props.config.title} 格式不正确，请选择 ${props.config.accept} 文件。`,
    })
    return
  }

  emit('update:file', file)
}

function handleFileInputChange(event: Event) {
  const target = event.target as HTMLInputElement
  const selectedFile = target.files?.[0]

  if (!selectedFile) {
    return
  }

  emitFile(selectedFile)
  target.value = ''
}

function handleDrop(event: DragEvent) {
  event.preventDefault()
  isDragging.value = false

  const selectedFile = event.dataTransfer?.files?.[0]

  if (!selectedFile) {
    return
  }

  emitFile(selectedFile)
}

function handleDragOver(event: DragEvent) {
  event.preventDefault()
}

function handleDragEnter(event: DragEvent) {
  event.preventDefault()
  isDragging.value = true
}

function handleDragLeave(event: DragEvent) {
  event.preventDefault()

  const currentTarget = event.currentTarget as HTMLElement | null
  const relatedTarget = event.relatedTarget as Node | null

  if (!currentTarget || (relatedTarget && currentTarget.contains(relatedTarget))) {
    return
  }

  isDragging.value = false
}

function handleRemoveFile() {
  emit('update:file', null)
}
</script>

<template>
  <article
    class="upload-card"
    :class="[
      `is-${config.kind}`,
      {
        'is-dragging': isDragging,
        'has-file': !!file,
      },
    ]"
    @drop="handleDrop"
    @dragover="handleDragOver"
    @dragenter="handleDragEnter"
    @dragleave="handleDragLeave"
  >
    <div class="card-head">
      <div class="head-badge">
        <el-icon :size="20">
          <component :is="iconComponent" />
        </el-icon>
      </div>
      <div class="head-copy">
        <h3>{{ config.title }}</h3>
        <p>{{ config.subtitle }}</p>
      </div>
    </div>

    <button
      type="button"
      class="dropzone"
      :class="{ 'is-dragging': isDragging, 'has-file': !!file }"
      @click="openFilePicker"
    >
      <input
        ref="inputRef"
        class="native-input"
        type="file"
        :accept="config.accept"
        @change="handleFileInputChange"
      />

      <template v-if="file">
        <div class="selected-state">
          <div class="selected-icon">
            <el-icon :size="28"><CircleCheckFilled /></el-icon>
          </div>
          <div class="selected-copy">
            <strong>{{ file.name }}</strong>
            <div class="selected-meta">
              <span>{{ formatFileSize(file.size) }}</span>
              <span class="dot" />
              <span>{{ fileTypeLabel }}</span>
            </div>
          </div>
          <div class="selected-actions">
            <el-button size="small" plain @click.stop="openFilePicker">
              替换
            </el-button>
            <el-button
              size="small"
              type="danger"
              plain
              :icon="Delete"
              @click.stop="handleRemoveFile"
            >
              移除
            </el-button>
          </div>
        </div>
      </template>

      <template v-else>
        <div class="empty-state">
          <div class="empty-icon">
            <el-icon :size="34">
              <UploadFilled />
            </el-icon>
          </div>
          <strong>{{ config.placeholder }}</strong>
          <p>{{ config.description }}</p>
          <div class="chips">
            <span class="chip">
              <el-icon><Document /></el-icon>
              {{ config.accept }}
            </span>
            <span class="chip">
              <el-icon><FolderOpened /></el-icon>
              本地选择
            </span>
          </div>
        </div>
      </template>
    </button>

    <div v-if="$slots.actions" class="card-actions">
      <slot name="actions" />
    </div>
  </article>
</template>

<style scoped>
.upload-card {
  border-radius: 20px;
  padding: 22px;
  background: #fff;
  border: 1px solid #e5eaf1;
  box-shadow: 0 10px 30px rgba(15, 23, 42, 0.05);
}

.upload-card.is-dragging {
  transform: translateY(-1px);
}

.card-head {
  display: flex;
  align-items: center;
  gap: 14px;
  margin-bottom: 20px;
}

.head-badge {
  width: 40px;
  height: 40px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  border-radius: 12px;
  color: #2563eb;
  background: #eff6ff;
  border: 1px solid #dbeafe;
}

.head-copy h3 {
  margin: 0;
  font-size: 1rem;
  color: #0f172a;
}

.head-copy p {
  margin: 4px 0 0;
  font-size: 0.88rem;
  color: #64748b;
}

.dropzone {
  width: 100%;
  min-height: 250px;
  border: 1px dashed #cbd5e1;
  border-radius: 18px;
  background: #f8fafc;
  padding: 22px;
  cursor: pointer;
  transition:
    border-color 0.25s ease,
    transform 0.25s ease,
    box-shadow 0.25s ease;
}

.is-bim .dropzone:hover,
.is-bim .dropzone.is-dragging {
  border-color: #93c5fd;
  box-shadow: 0 10px 24px rgba(37, 99, 235, 0.08);
}

.is-pointcloud .dropzone:hover,
.is-pointcloud .dropzone.is-dragging {
  border-color: #93c5fd;
  box-shadow: 0 10px 24px rgba(37, 99, 235, 0.08);
}

.native-input {
  display: none;
}

.empty-state,
.selected-state {
  min-height: 230px;
  display: flex;
  flex-direction: column;
  justify-content: center;
}

.empty-state {
  align-items: center;
  text-align: center;
}

.empty-icon {
  width: 60px;
  height: 60px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  border-radius: 18px;
  margin-bottom: 16px;
  color: #2563eb;
  background: #fff;
  border: 1px solid #e5eaf1;
}

.empty-state strong {
  max-width: 320px;
  font-size: 1rem;
  line-height: 1.5;
  color: #0f172a;
}

.empty-state p {
  margin: 10px 0 0;
  max-width: 360px;
  line-height: 1.7;
  color: #64748b;
}

.chips {
  display: flex;
  gap: 10px;
  flex-wrap: wrap;
  justify-content: center;
  margin-top: 18px;
}

.chip {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  height: 34px;
  padding: 0 14px;
  border-radius: 999px;
  background: #fff;
  border: 1px solid #e2e8f0;
  color: #475569;
  font-size: 0.85rem;
}

.selected-state {
  gap: 18px;
}

.selected-icon {
  width: 48px;
  height: 48px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  border-radius: 14px;
  color: #22c55e;
  background: #f0fdf4;
  border: 1px solid #bbf7d0;
}

.selected-copy strong {
  display: block;
  font-size: 1rem;
  color: #0f172a;
  word-break: break-all;
}

.selected-meta {
  display: flex;
  align-items: center;
  gap: 10px;
  margin-top: 10px;
  color: #64748b;
  font-size: 0.9rem;
}

.dot {
  width: 4px;
  height: 4px;
  border-radius: 50%;
  background: #94a3b8;
}

.selected-actions {
  display: flex;
  gap: 10px;
  flex-wrap: wrap;
}

.card-actions {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  margin-top: 18px;
  padding-top: 18px;
  border-top: 1px solid #edf2f7;
}

@media (max-width: 768px) {
  .upload-card {
    padding: 18px;
    border-radius: 18px;
  }

  .dropzone {
    min-height: 220px;
    padding: 18px;
  }
}
</style>
