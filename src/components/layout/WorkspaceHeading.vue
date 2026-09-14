<script setup lang="ts">
defineProps<{ title: string; description: string }>()
</script>

<template>
  <div class="workspace-heading">
    <h1>{{ title }}</h1>
    <p>{{ description }}</p>
  </div>
</template>

<style scoped>
.workspace-heading { margin-bottom: var(--workspace-gap); }
h1 { margin: 0; font-size: var(--font-size-xl); font-weight: 650; }
p { margin: var(--spacing-sm) 0 0; font-size: var(--font-size-sm); color: var(--text-secondary); }
</style>
