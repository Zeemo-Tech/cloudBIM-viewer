import { ref, watch, type Ref } from 'vue'

/** Select the generated mesh once both the source scene and artifact are ready. */
export function useBimRemeshDisplay(
  loaded: Readonly<Ref<boolean>>,
  artifactKey: Readonly<Ref<string>>,
  setVisible: (visible: boolean) => Promise<unknown>,
) {
  const visible = ref(false)
  const busy = ref(false)
  const error = ref('')
  const requested = ref(true)
  const revision = ref(0)

  watch([loaded, artifactKey, requested, revision], async (_, __, onCleanup) => {
    let cancelled = false
    onCleanup(() => { cancelled = true })
    const target = loaded.value && Boolean(artifactKey.value) && requested.value
    // The renderer restores the source immediately, before downloading a new mesh.
    visible.value = false
    busy.value = target
    error.value = ''
    try {
      await setVisible(target)
      if (!cancelled) visible.value = target
    } catch (cause) {
      if (!cancelled) error.value = cause instanceof Error ? cause.message : '加载保形网格失败'
    } finally {
      if (!cancelled) busy.value = false
    }
  }, { immediate: true, flush: 'post' })

  return {
    visible, busy, error,
    toggle() {
      requested.value = !visible.value
      revision.value++ // Also permits retrying a failed download of the same artifact.
    },
    reset() {
      requested.value = true
      revision.value++
    },
  }
}
