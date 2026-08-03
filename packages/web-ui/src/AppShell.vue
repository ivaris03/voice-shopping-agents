<script setup lang="ts">
import type { NavItem } from './index'

defineProps<{
  eyebrow: string
  title: string
  description: string
  navItems: NavItem[]
  actionLabel: string
}>()
</script>

<template>
  <div id="top" class="app-shell">
    <header class="topbar">
      <a class="brand" href="#top" :aria-label="`${title} 首页`">
        <span class="brand-mark" aria-hidden="true">声</span>
        <span>{{ title }}</span>
      </a>
      <nav class="nav" aria-label="主导航">
        <a v-for="item in navItems" :key="item.href" :href="item.href">{{ item.label }}</a>
      </nav>
      <button class="action-button" type="button">{{ actionLabel }}</button>
    </header>

    <main>
      <section class="hero">
        <div>
          <p class="eyebrow">{{ eyebrow }}</p>
          <h1><slot name="headline" /></h1>
          <p class="hero-copy">{{ description }}</p>
          <slot name="hero-action" />
        </div>
        <slot name="hero-panel" />
      </section>

      <section class="content-grid" aria-label="功能概览">
        <slot />
      </section>
    </main>
  </div>
</template>
