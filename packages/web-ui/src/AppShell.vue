<script setup lang="ts">
import type { NavItem } from './index'

defineProps<{
  eyebrow: string
  title: string
  description: string
  navItems: NavItem[]
  actionLabel: string
  heroCompact?: boolean
  activeNavHref?: string
  workspaceLinks?: Array<{
    label: string
    description: string
    href: string
  }>
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
        <a
          v-for="item in navItems"
          :key="item.href"
          :href="item.href"
          :class="{ 'nav--active': item.href === activeNavHref }"
          :aria-current="item.href === activeNavHref ? 'page' : undefined"
        >{{ item.label }}</a>
      </nav>
      <details v-if="workspaceLinks?.length" class="workspace-switcher">
        <summary class="workspace-switcher__trigger">切换工作台</summary>
        <div class="workspace-switcher__menu" role="menu" aria-label="其他工作台">
          <a
            v-for="workspace in workspaceLinks"
            :key="workspace.href"
            class="workspace-switcher__item"
            :href="workspace.href"
            role="menuitem"
          >
            <strong>{{ workspace.label }}</strong>
            <span>{{ workspace.description }}</span>
          </a>
        </div>
      </details>
      <button class="action-button" type="button">{{ actionLabel }}</button>
    </header>

    <main>
      <section class="hero" :class="{ 'hero--compact': heroCompact }">
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
