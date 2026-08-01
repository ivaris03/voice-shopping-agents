<script setup lang="ts">
withDefaults(
  defineProps<{
    label: string
    title: string
    description: string
    status?: string
    accent?: boolean
  }>(),
  { status: '待接入', accent: false },
)
</script>

<template>
  <article class="feature-card" :class="{ 'feature-card--accent': accent }">
    <div class="feature-card__meta">
      <span>{{ label }}</span>
      <span class="status">{{ status }}</span>
    </div>
    <h2>{{ title }}</h2>
    <p>{{ description }}</p>
    <slot />
  </article>
</template>
