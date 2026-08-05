<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted } from 'vue'
import {
  formatCatalogAttributeLabel,
  formatCatalogAttributeValue,
  formatCategoryLabel,
  type Product,
} from './index'

const props = withDefaults(
  defineProps<{
    product: Product
    actionLabel?: string
    actionDisabled?: boolean
  }>(),
  {
    actionLabel: '',
    actionDisabled: false,
  },
)

const emit = defineEmits<{
  (event: 'close'): void
  (event: 'action'): void
}>()

const statusLabels: Record<Product['status'], string> = {
  draft: '草稿',
  on_sale: '在售',
  off_sale: '已下架',
}

const attributeEntries = computed(() => Object.entries(props.product.attributes ?? {}))
const productInitial = computed(() => props.product.name.slice(0, 1))

function formatPrice(value: string | number) {
  return Number(value).toLocaleString('zh-CN', { minimumFractionDigits: 2, maximumFractionDigits: 2 })
}

function formatDate(value: string) {
  if (!value) return ''
  const date = new Date(value)
  return Number.isNaN(date.getTime()) ? '' : date.toLocaleDateString('zh-CN')
}

function handleImageError(event: Event) {
  const image = event.currentTarget
  if (!(image instanceof HTMLImageElement)) return
  image.hidden = true
}

function handleKeydown(event: KeyboardEvent) {
  if (event.key === 'Escape') emit('close')
}

onMounted(() => {
  document.addEventListener('keydown', handleKeydown)
  document.body.classList.add('has-product-detail-modal')
})

onBeforeUnmount(() => {
  document.removeEventListener('keydown', handleKeydown)
  document.body.classList.remove('has-product-detail-modal')
})
</script>

<template>
  <div class="product-detail-backdrop" role="presentation" @click.self="emit('close')">
    <section
      class="product-detail-dialog"
      role="dialog"
      aria-modal="true"
      aria-labelledby="product-detail-title"
      aria-describedby="product-detail-description"
    >
      <header class="product-detail-header">
        <div>
          <span class="eyebrow">商品详情</span>
          <p class="product-detail-merchant">{{ product.merchantName || '声选商品' }}</p>
        </div>
        <button class="product-detail-close" type="button" aria-label="关闭商品详情" @click="emit('close')">×</button>
      </header>

      <div class="product-detail-body">
        <div class="product-detail-visual" aria-hidden="true">
          <img v-if="product.imageUrls?.length" :src="product.imageUrls[0]" :alt="product.name" @error="handleImageError" />
          <span class="product-detail-visual__fallback">{{ productInitial }}</span>
        </div>

        <div class="product-detail-content">
          <div class="product-detail-tags">
            <span class="badge">{{ formatCategoryLabel(product.categoryL2) }}</span>
            <span class="badge" :class="{ 'badge--disabled': product.status !== 'on_sale' }">{{ statusLabels[product.status] }}</span>
          </div>
          <p v-if="product.categoryL1" class="product-detail-category">{{ formatCategoryLabel(product.categoryL1) }} / {{ formatCategoryLabel(product.categoryL2) }}</p>
          <h2 id="product-detail-title">{{ product.name }}</h2>
          <p v-if="product.brand" class="product-detail-brand">{{ product.brand }}</p>
          <p id="product-detail-description" class="product-detail-description">{{ product.description || '暂无商品描述。' }}</p>

          <div class="product-detail-price-row">
            <strong class="product-detail-price">¥{{ formatPrice(product.price) }}</strong>
            <span class="product-detail-stock" :class="{ 'product-detail-stock--empty': product.stock <= 0 }">
              {{ product.stock > 0 ? `库存 ${product.stock}` : '暂时无库存' }}
            </span>
          </div>

          <dl class="product-detail-facts">
            <div><dt>商品编码（SKU）</dt><dd>{{ product.sku || '—' }}</dd></div>
            <div><dt>商品状态</dt><dd>{{ statusLabels[product.status] }}</dd></div>
            <div v-if="formatDate(product.updatedAt)"><dt>最近更新</dt><dd>{{ formatDate(product.updatedAt) }}</dd></div>
          </dl>
        </div>
      </div>

      <div v-if="product.sellingPoints.length" class="product-detail-section">
        <h3>商品卖点</h3>
        <ul class="product-detail-points">
          <li v-for="point in product.sellingPoints" :key="point">{{ point }}</li>
        </ul>
      </div>

      <div v-if="attributeEntries.length" class="product-detail-section">
        <h3>商品参数</h3>
        <dl class="product-detail-attributes">
          <div v-for="[key, value] in attributeEntries" :key="key">
            <dt>{{ formatCatalogAttributeLabel(key) }}</dt>
            <dd>{{ formatCatalogAttributeValue(value) }}</dd>
          </div>
        </dl>
      </div>

      <footer class="product-detail-footer">
        <span class="muted">商品信息以当前页面展示为准</span>
        <div class="section-actions">
          <button class="secondary-button" type="button" @click="emit('close')">关闭</button>
          <button v-if="actionLabel" class="primary-button" type="button" :disabled="actionDisabled" @click="emit('action')">{{ actionLabel }}</button>
        </div>
      </footer>
    </section>
  </div>
</template>
