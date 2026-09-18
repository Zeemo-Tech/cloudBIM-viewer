<script setup lang="ts">
import { ref, watch } from 'vue'
import { Delete } from '@element-plus/icons-vue'

const props = withDefaults(defineProps<{
  tag?: string
  edit?: boolean
  text?: string
  editable?: boolean
  removable?: boolean
  label?: string
}>(), {
  tag: 'span',
  edit: false,
  text: '',
  editable: true,
  removable: true,
  label: '该内容',
})

const emit = defineEmits<{
  (e: 'update:text', value: string): void
  (e: 'remove'): void
}>()

// While editing, the rendered text is intentionally decoupled from the prop so
// Vue re-renders never rewrite the contenteditable node (which would reset the
// caret). The latest value is committed on blur and re-synced whenever the node
// is not focused (for example after an undo).
const root = ref<HTMLElement | null>(null)
const display = ref(props.text)
watch(() => props.text, (value) => {
  const active = document.activeElement
  const focused = Boolean(root.value && active && root.value.contains(active))
  if (!props.edit || !focused) display.value = value
})
watch(() => props.edit, () => {
  display.value = props.text
})

function onBlur(event: FocusEvent) {
  if (!props.edit || !props.editable) return
  const value = (((event.target as HTMLElement).innerText) || '')
    .replace(/\u00a0/g, ' ')
    .replace(/\s+$/, '')
  if (value !== props.text) emit('update:text', value)
}
</script>

<template>
  <component
    :is="tag"
    ref="root"
    class="report-editable"
    :class="{ 'is-editing': edit && editable, 'is-block': edit && !editable }"
    :contenteditable="edit && editable ? 'true' : undefined"
    :data-report-editable="edit && editable ? 'true' : undefined"
    @blur="onBlur"
  >
    <slot>{{ display }}</slot>
    <button
      v-if="edit && removable"
      type="button"
      class="report-editable__remove"
      :title="`删除${label}`"
      :aria-label="`删除${label}`"
      @mousedown.prevent
      @click.stop="emit('remove')"
    >
      <el-icon><Delete /></el-icon>
    </button>
  </component>
</template>

<style scoped>
.report-editable {
  position: relative;
}

.report-editable.is-editing {
  border-radius: 3px;
  outline: 1px dashed #72a5dc;
  outline-offset: 3px;
  cursor: text;
}

.report-editable__remove {
  position: absolute;
  z-index: 8;
  top: -11px;
  right: -11px;
  width: 20px;
  height: 20px;
  display: grid;
  place-items: center;
  padding: 0;
  border: 0;
  border-radius: 50%;
  color: #fff;
  background: #d9534f;
  box-shadow: 0 2px 6px rgb(0 0 0 / 26%);
  cursor: pointer;
  opacity: 0.92;
}

.report-editable__remove:hover {
  background: #c9302c;
  opacity: 1;
}

.report-editable__remove :deep(.el-icon) {
  font-size: 12px;
}

@media print {
  .report-editable.is-editing {
    outline: none;
  }

  .report-editable__remove {
    display: none !important;
  }
}
</style>
