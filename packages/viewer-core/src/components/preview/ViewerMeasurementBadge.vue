<script setup lang="ts">
import { ref } from 'vue'

export type ViewerMeasurementBadgeOverlay = {
  visible: boolean
  x: number
  y: number
}

const props = withDefaults(
  defineProps<{
    closable?: boolean
    deletable?: boolean
    mainLabel?: string
    mainValue?: string
    overlay: ViewerMeasurementBadgeOverlay
    resettable?: boolean
    rows?: Array<{ label: string; value: string }>
    title?: string
  }>(),
  {
    closable: false,
    deletable: false,
    mainLabel: '',
    mainValue: '',
    resettable: false,
    rows: () => [],
    title: '',
  },
)

const emit = defineEmits<{
  close: []
  delete: []
  dragBy: [delta: { x: number; y: number }]
  resetPosition: []
}>()

const dragging = ref(false)

function startDrag(event: PointerEvent) {
  if ((event.target as HTMLElement | null)?.closest('button')) return

  dragging.value = true
  let previousX = event.clientX
  let previousY = event.clientY

  const move = (moveEvent: PointerEvent) => {
    if (!dragging.value) return
    emit('dragBy', {
      x: moveEvent.clientX - previousX,
      y: moveEvent.clientY - previousY,
    })
    previousX = moveEvent.clientX
    previousY = moveEvent.clientY
  }
  const end = () => {
    dragging.value = false
    window.removeEventListener('pointermove', move)
    window.removeEventListener('pointerup', end)
    window.removeEventListener('pointercancel', end)
  }

  window.addEventListener('pointermove', move)
  window.addEventListener('pointerup', end)
  window.addEventListener('pointercancel', end)
}
</script>

<template>
  <div
    v-if="overlay.visible"
    class="measurement-badge"
    :style="{ transform: `translate(${overlay.x}px, ${overlay.y}px)` }"
  >
    <section :class="['measurement-badge__card', { 'is-dragging': dragging }]">
      <header class="measurement-badge__header">
        <div class="measurement-badge__handle" @pointerdown.stop="startDrag">
          <span class="measurement-badge__dots" aria-hidden="true" />
          <span v-if="title" class="measurement-badge__title">{{ title }}</span>
        </div>
        <div class="measurement-badge__actions">
          <button v-if="resettable" type="button" title="重置卡片位置" @click.stop="emit('resetPosition')">重置</button>
          <button v-if="closable" type="button" class="measurement-badge__close" title="隐藏测量结果" @click.stop="emit('close')">×</button>
        </div>
      </header>

      <div v-if="mainValue" class="measurement-badge__main">
        <span v-if="mainLabel">{{ mainLabel }}</span>
        <strong>{{ mainValue }}</strong>
      </div>

      <div v-if="rows.length" class="measurement-badge__divider" />
      <dl v-if="rows.length" class="measurement-badge__rows">
        <div v-for="row in rows" :key="`${row.label}-${row.value}`">
          <dt>{{ row.label }}</dt>
          <dd>{{ row.value }}</dd>
        </div>
      </dl>

      <footer v-if="deletable" class="measurement-badge__footer">
        <button type="button" @click.stop="emit('delete')">删除</button>
      </footer>
    </section>
  </div>
</template>

<style scoped>
.measurement-badge { position: absolute; top: 0; left: 0; z-index: 26; pointer-events: none; }
.measurement-badge__card { display: flex; flex-direction: column; gap: 5px; min-width: 168px; max-width: 200px; padding: 10px 12px 8px; border: 1px solid rgba(115, 162, 243, .22); border-radius: 10px; background: rgb(8 18 42 / 78%); color: rgba(255,255,255,.9); box-shadow: 0 12px 28px rgba(4,10,34,.3), inset 0 1px 0 rgba(255,255,255,.06); backdrop-filter: blur(14px) saturate(120%); pointer-events: auto; user-select: none; }
.measurement-badge__header, .measurement-badge__actions, .measurement-badge__handle, .measurement-badge__rows > div { display: flex; align-items: center; }
.measurement-badge__header { justify-content: space-between; gap: 8px; }
.measurement-badge__actions { gap: 6px; }
.measurement-badge__handle { min-height: 18px; gap: 8px; cursor: grab; }
.is-dragging .measurement-badge__handle { cursor: grabbing; }
.measurement-badge__dots { width: 16px; height: 10px; opacity: .62; background-image: radial-gradient(circle, rgba(151,186,255,.78) 1px, transparent 1.5px); background-size: 5px 5px; }
.measurement-badge__title { color: rgba(255,255,255,.68); font-size: 12px; font-weight: 600; }
.measurement-badge__actions button, .measurement-badge__footer button { border: 0; border-radius: 5px; padding: 2px 7px; background: rgba(255,255,255,.08); color: rgba(181,206,255,.95); cursor: pointer; font-size: 11px; }
.measurement-badge__actions .measurement-badge__close { width: 18px; height: 18px; padding: 0; color: #fff; font-size: 15px; line-height: 1; }
.measurement-badge__main { display: flex; flex-direction: column; gap: 1px; }
.measurement-badge__main span { color: rgba(145,181,255,.94); font-size: 10px; font-weight: 600; }
.measurement-badge__main strong { color: #fff; font-size: 18px; line-height: 1.15; text-shadow: 0 0 14px rgba(78,102,204,.28); }
.measurement-badge__divider { height: 1px; margin: 2px 0 1px; background: linear-gradient(90deg, rgba(115,162,243,.28), rgba(255,255,255,.04)); }
.measurement-badge__rows { display: flex; flex-direction: column; gap: 5px; margin: 0; }
.measurement-badge__rows > div { justify-content: space-between; gap: 12px; }
.measurement-badge__rows dt, .measurement-badge__rows dd { margin: 0; font-size: 12px; font-weight: 600; }
.measurement-badge__rows dt { color: rgba(161,191,250,.88); }
.measurement-badge__rows dd { color: rgba(255,255,255,.94); text-align: right; }
.measurement-badge__footer { display: flex; justify-content: flex-end; margin-top: 2px; }
.measurement-badge__footer button { color: rgba(255,232,232,.9); }
.measurement-badge__actions button:hover, .measurement-badge__footer button:hover { background: rgba(255,255,255,.16); }
.measurement-badge__footer button:hover { background: rgba(255,90,90,.24); }
</style>
