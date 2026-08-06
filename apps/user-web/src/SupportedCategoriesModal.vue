<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted } from 'vue'

import { formatCategoryLabel, type Category } from '@voice-shopping/web-ui'

const props = defineProps<{
  categories: Category[]
}>()

const emit = defineEmits<{
  (event: 'close'): void
}>()

const categoryGroups = computed(() => {
  const groups = new Map<string, Category[]>()
  for (const category of props.categories) {
    const categories = groups.get(category.categoryL1) ?? []
    categories.push(category)
    groups.set(category.categoryL1, categories)
  }
  return [...groups.entries()].map(([categoryL1, categories]) => ({ categoryL1, categories }))
})

function handleKeydown(event: KeyboardEvent) {
  if (event.key === 'Escape') emit('close')
}

onMounted(() => {
  document.addEventListener('keydown', handleKeydown)
  document.body.classList.add('has-supported-categories-modal')
})

onBeforeUnmount(() => {
  document.removeEventListener('keydown', handleKeydown)
  document.body.classList.remove('has-supported-categories-modal')
})
</script>

<template>
  <div class="supported-categories-backdrop" role="presentation" @click.self="emit('close')">
    <section
      class="supported-categories-dialog"
      role="dialog"
      aria-modal="true"
      aria-labelledby="supported-categories-title"
      aria-describedby="supported-categories-description"
    >
      <header class="supported-categories-header">
        <div>
          <span class="eyebrow">品类范围</span>
          <h2 id="supported-categories-title">当前支持的二级品类</h2>
          <p id="supported-categories-description">导购可基于以下品类理解需求并推荐商品。</p>
        </div>
        <button class="product-detail-close" type="button" aria-label="关闭支持品类弹窗" @click="emit('close')">×</button>
      </header>

      <div v-if="categoryGroups.length" class="supported-categories-list">
        <section v-for="group in categoryGroups" :key="group.categoryL1" class="supported-categories-group">
          <h3>{{ formatCategoryLabel(group.categoryL1) }}</h3>
          <ul>
            <li v-for="category in group.categories" :key="category.id">
              {{ formatCategoryLabel(category.categoryL2) }}
            </li>
          </ul>
        </section>
      </div>
      <p v-else class="supported-categories-empty">暂未配置支持的二级品类。</p>
    </section>
  </div>
</template>
