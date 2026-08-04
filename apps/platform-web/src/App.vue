<script setup lang="ts">
import {
  AppShell,
  ProductDetailModal,
  requestJson,
  type Category,
  type CategoryLevelOne,
  type CategorySlot,
  type ItemsResponse,
  type Merchant,
  type Order,
  type Product,
} from '@voice-shopping/web-ui'
import { computed, onBeforeUnmount, onMounted, reactive, ref, watch } from 'vue'

interface MerchantGroup {
  ownerUserId: string
  ownerDisplayName: string
  stores: Merchant[]
}

interface CategorySupport {
  productCount: number
  agentCandidateCount: number
  requiredSlotCount: number
  optionalSlotCount: number
}

type ProductStockFilter = 'all' | 'available' | 'low' | 'out'

const navItems = [
  { label: '支持目录', href: '#/taxonomy' },
  { label: '商品浏览', href: '#/products' },
  { label: '商家治理', href: '#/merchants' },
  { label: '全量订单', href: '#/orders' },
]

const currentRoute = ref('/taxonomy')
const merchants = ref<Merchant[]>([])
const products = ref<Product[]>([])
const orders = ref<Order[]>([])
const categories = ref<Category[]>([])
const categoryLevelOnes = ref<CategoryLevelOne[]>([])
const loading = ref(true)
const categorySaving = ref(false)
const statusSaving = ref(false)
const error = ref('')
const selectedProduct = ref<Product | null>(null)

const categoryL1Form = reactive({ code: '' })
const categoryForm = reactive({ categoryL1Id: '', categoryL2: '' })
const slotForm = reactive({ categoryL1Id: '', categoryId: '', key: '', isRequired: true, enumValues: '' })
const disabledReason = ref('')
const taxonomyQuery = ref('')
const taxonomyLevelOneFilter = ref('')

const selectedMerchantKeys = ref<string[]>([])
const selectedStoreIds = ref<string[]>([])
const productQuery = ref('')
const productStatus = ref('')
const productCategory = ref('')
const productStock = ref<ProductStockFilter>('all')
const orderStatus = ref('')

const isTaxonomyPage = computed(() => currentRoute.value === '/taxonomy')
const isProductPage = computed(() => currentRoute.value === '/products')
const isMerchantPage = computed(() => currentRoute.value === '/merchants')
const isOrdersPage = computed(() => currentRoute.value === '/orders')
const isLevelOneEditor = computed(() => currentRoute.value === '/taxonomy/level-one/new')
const isLevelTwoEditor = computed(() => currentRoute.value === '/taxonomy/level-two/new')
const isSlotEditor = computed(() => /^\/taxonomy\/slot\/(new|edit\/[^/]+)$/.test(currentRoute.value))
const isTaxonomyEditor = computed(() => isLevelOneEditor.value || isLevelTwoEditor.value || isSlotEditor.value)
const slotEditorId = computed(() => currentRoute.value.match(/^\/taxonomy\/slot\/edit\/(.+)$/)?.[1] ?? '')
const editingSlot = computed(() => categories.value.flatMap((category) => category.slots).find((slot) => slot.id === slotEditorId.value))
const statusStoreId = computed(() => currentRoute.value.match(/^\/merchants\/([^/]+)\/status$/)?.[1] ?? '')
const statusStore = computed(() => merchants.value.find((store) => store.id === statusStoreId.value))
const isStatusEditor = computed(() => Boolean(statusStoreId.value))
const isOperationPage = computed(() => isTaxonomyEditor.value || isStatusEditor.value)

const merchantGroups = computed<MerchantGroup[]>(() => {
  const groups = new Map<string, MerchantGroup>()
  for (const store of merchants.value) {
    const group = groups.get(store.ownerUserId)
    if (group) group.stores.push(store)
    else groups.set(store.ownerUserId, {
      ownerUserId: store.ownerUserId,
      ownerDisplayName: store.ownerDisplayName || `商家账号 ${store.ownerUserId.slice(0, 8)}…`,
      stores: [store],
    })
  }
  return [...groups.values()]
})
const availableStores = computed(() => {
  if (!selectedMerchantKeys.value.length) return merchants.value
  return merchants.value.filter((store) => selectedMerchantKeys.value.includes(store.ownerUserId))
})
const visibleProducts = computed(() => {
  const query = productQuery.value.trim().toLowerCase()
  return products.value.filter((product) => {
    const store = merchants.value.find((item) => item.id === product.merchantId)
    const matchesQuery = !query || `${product.name} ${product.sku} ${product.brand ?? ''} ${product.merchantName ?? ''}`.toLowerCase().includes(query)
    const matchesMerchant = !selectedMerchantKeys.value.length || Boolean(store && selectedMerchantKeys.value.includes(store.ownerUserId))
    const matchesStore = !selectedStoreIds.value.length || selectedStoreIds.value.includes(product.merchantId)
    const matchesStatus = !productStatus.value || product.status === productStatus.value
    const matchesCategory = !productCategory.value || product.categoryL2 === productCategory.value
    const matchesStock = productStock.value === 'all'
      || (productStock.value === 'available' && product.stock > 0)
      || (productStock.value === 'low' && product.stock > 0 && product.stock <= 10)
      || (productStock.value === 'out' && product.stock <= 0)
    return matchesQuery && matchesMerchant && matchesStore && matchesStatus && matchesCategory && matchesStock
  })
})
const visibleOrders = computed(() => orderStatus.value ? orders.value.filter((item) => item.status === orderStatus.value) : orders.value)
const enabledStores = computed(() => merchants.value.filter((item) => item.isEnabled).length)
const successfulOrders = computed(() => orders.value.filter((item) => item.status === 'success'))
const grossMerchandiseValue = computed(() => successfulOrders.value.reduce((sum, item) => sum + Number(item.totalAmount), 0))
const categorySupportById = computed<Map<string, CategorySupport>>(() => {
  const support = new Map<string, CategorySupport>()
  const storesById = new Map(merchants.value.map((store) => [store.id, store]))
  for (const category of categories.value) {
    const categoryProducts = products.value.filter((product) => product.categoryL2 === category.categoryL2)
    support.set(category.id, {
      productCount: categoryProducts.length,
      agentCandidateCount: categoryProducts.filter((product) => {
        const store = storesById.get(product.merchantId)
        return product.status === 'on_sale' && product.stock > 0 && Boolean(store?.isEnabled)
      }).length,
      requiredSlotCount: category.requiredSlots.length,
      optionalSlotCount: category.optionalSlots.length,
    })
  }
  return support
})
const totalAgentCandidateProducts = computed(() =>
  [...categorySupportById.value.values()].reduce((total, support) => total + support.agentCandidateCount, 0),
)
const categoriesWithAgentCandidates = computed(() =>
  [...categorySupportById.value.values()].filter((support) => support.agentCandidateCount > 0).length,
)
const totalRequiredSlots = computed(() => categories.value.reduce((total, category) => total + category.requiredSlots.length, 0))
const slotCategories = computed(() => categories.value.filter((category) => category.categoryL1Id === slotForm.categoryL1Id))
const hasTaxonomyFilter = computed(() => Boolean(taxonomyQuery.value.trim() || taxonomyLevelOneFilter.value))
const taxonomyGroups = computed(() => {
  const query = taxonomyQuery.value.trim().toLowerCase()
  return categoryLevelOnes.value
    .filter((levelOne) => !taxonomyLevelOneFilter.value || levelOne.id === taxonomyLevelOneFilter.value)
    .map((levelOne) => {
      const levelMatchesQuery = !query || levelOne.code.toLowerCase().includes(query)
      const matchingCategories = categories.value.filter((category) => {
        if (category.categoryL1Id !== levelOne.id) return false
        if (levelMatchesQuery) return true
        const searchableText = [
          category.categoryL1,
          category.categoryL2,
          categoryDisplayName(category.categoryL2),
          ...category.slots.flatMap((slot) => [slot.key, ...slot.enumValues.map(String)]),
        ].join(' ').toLowerCase()
        return searchableText.includes(query)
      })
      return { levelOne, categories: matchingCategories }
    })
    .filter((group) => !hasTaxonomyFilter.value || group.categories.length > 0)
})
const activeNavHref = computed(() => {
  if (isProductPage.value) return '#/products'
  if (isMerchantPage.value || isStatusEditor.value) return '#/merchants'
  if (isOrdersPage.value) return '#/orders'
  return '#/taxonomy'
})
const pageHeadline = computed(() => {
  if (isLevelOneEditor.value) return '搭建品类树的第一层。'
  if (isLevelTwoEditor.value) return '让二级品类有清晰归属。'
  if (isSlotEditor.value) return editingSlot.value ? '更新商品可理解的属性。' : '把可筛选的商品属性定义清楚。'
  if (isStatusEditor.value) return statusStore.value?.isEnabled ? '谨慎调整店铺的供给状态。' : '恢复店铺到可售供给中。'
  if (isProductPage.value) return '从全局看见，每一件商品。'
  if (isMerchantPage.value) return '用清晰边界，管理每一家店。'
  if (isOrdersPage.value) return '用全局数据，守住交易质量。'
  return '当前支持什么品类？'
})
const pageDescription = computed(() => {
  if (isOperationPage.value) return '这是独立的操作页面，完成后会回到对应的管理视图。'
  if (isProductPage.value) return '默认全局浏览；可按商家、店铺单选或多选，再叠加商品条件过滤。'
  if (isMerchantPage.value) return '店铺启停会实时影响用户端的商品浏览和 Agent 推荐候选。'
  if (isOrdersPage.value) return '查看全平台订单状态与交易结果，快速识别异常记录。'
  return '从品类、可推荐商品和必填槽位三个维度，快速定位导购范围。'
})

function routeFromHash() {
  const route = window.location.hash.replace(/^#/, '') || '/taxonomy'
  const allowed = ['/taxonomy', '/products', '/merchants', '/orders', '/taxonomy/level-one/new', '/taxonomy/level-two/new', '/taxonomy/slot/new'].includes(route)
    || /^\/taxonomy\/slot\/edit\/[^/]+$/.test(route)
    || /^\/merchants\/[^/]+\/status$/.test(route)
  if (!allowed) {
    window.location.hash = '#/taxonomy'
    return
  }
  currentRoute.value = route
  window.scrollTo({ top: 0, behavior: 'auto' })
}

function goTo(route: string) {
  if (currentRoute.value === route) {
    window.scrollTo({ top: 0, behavior: 'smooth' })
    return
  }
  window.location.hash = `#${route}`
}

function formatPrice(value: number) {
  return Number(value).toLocaleString('zh-CN', { minimumFractionDigits: 2, maximumFractionDigits: 2 })
}

function formatDateTime(value: string) {
  const date = new Date(value)
  return Number.isNaN(date.getTime()) ? '—' : date.toLocaleString('zh-CN', { hour12: false })
}

function categoryLabel(value: string) {
  return value.replaceAll('_', ' ')
}

const categoryDisplayNames: Record<string, string> = {
  HEADPHONES: '耳机',
  COFFEE_MACHINE: '咖啡机',
  ELECTRIC_KETTLE: '电热水壶',
  RUNNING_SHOES: '跑鞋',
  WATCHES: '腕表',
  LIPSTICK: '口红',
}

function categoryDisplayName(value: string) {
  const label = categoryDisplayNames[value]
  return label ? `${label} · ${value}` : value
}

function getCategorySupport(category: Category): CategorySupport {
  return categorySupportById.value.get(category.id) ?? {
    productCount: 0,
    agentCandidateCount: 0,
    requiredSlotCount: category.requiredSlots.length,
    optionalSlotCount: category.optionalSlots.length,
  }
}

function parseEnumValues(value: string): Array<string | number | boolean> {
  return [...new Set(value.split(/[,，]+/).map((item) => item.trim()).filter(Boolean))].map((item) => {
    if (item === 'true') return true
    if (item === 'false') return false
    const number = Number(item)
    return Number.isFinite(number) ? number : item
  })
}

async function loadData() {
  loading.value = true
  error.value = ''
  try {
    const [merchantData, productData, orderData, categoryData, categoryL1Data] = await Promise.all([
      requestJson<ItemsResponse<Merchant>>('/platform/merchants'),
      requestJson<ItemsResponse<Product>>('/platform/products'),
      requestJson<ItemsResponse<Order>>('/platform/orders'),
      requestJson<ItemsResponse<Category>>('/platform/categories'),
      requestJson<ItemsResponse<CategoryLevelOne>>('/platform/category-level-ones'),
    ])
    merchants.value = merchantData.items
    products.value = productData.items
    orders.value = orderData.items
    categories.value = categoryData.items
    categoryLevelOnes.value = categoryL1Data.items
    selectedMerchantKeys.value = selectedMerchantKeys.value.filter((key) => merchantGroups.value.some((group) => group.ownerUserId === key))
    selectedStoreIds.value = selectedStoreIds.value.filter((id) => merchants.value.some((store) => store.id === id))
    if (!categoryForm.categoryL1Id && categoryLevelOnes.value[0]) categoryForm.categoryL1Id = categoryLevelOnes.value[0].id
    if (slotEditorId.value && !editingSlot.value) goTo('/taxonomy')
    if (statusStoreId.value && !statusStore.value) goTo('/merchants')
  } catch (reason) {
    error.value = reason instanceof Error ? reason.message : '平台数据加载失败'
  } finally {
    loading.value = false
  }
}

function resetSlotForm(slot?: CategorySlot) {
  const category = slot ? categories.value.find((item) => item.slots.some((item) => item.id === slot.id)) : undefined
  const initialCategory = category ?? categories.value[0]
  Object.assign(slotForm, {
    categoryL1Id: initialCategory?.categoryL1Id ?? '',
    categoryId: initialCategory?.id ?? '',
    key: slot?.key ?? '',
    isRequired: slot?.isRequired ?? true,
    enumValues: slot?.enumValues.map(String).join('，') ?? '',
  })
}

watch(currentRoute, () => {
  if (isSlotEditor.value) resetSlotForm(editingSlot.value)
  if (isStatusEditor.value) disabledReason.value = statusStore.value?.disabledReason ?? '平台人工审核'
})
watch(categories, () => {
  if (isSlotEditor.value) resetSlotForm(editingSlot.value)
})
watch(() => slotForm.categoryL1Id, (categoryL1Id) => {
  if (editingSlot.value) return
  slotForm.categoryId = categories.value.find((category) => category.categoryL1Id === categoryL1Id)?.id ?? ''
})
watch(selectedMerchantKeys, (keys) => {
  if (!keys.length) return
  const allowedStoreIds = new Set(merchants.value.filter((store) => keys.includes(store.ownerUserId)).map((store) => store.id))
  selectedStoreIds.value = selectedStoreIds.value.filter((storeId) => allowedStoreIds.has(storeId))
})

async function createCategoryLevelOne() {
  if (!categoryL1Form.code.trim()) return
  categorySaving.value = true
  error.value = ''
  try {
    await requestJson('/platform/category-level-ones', { method: 'POST', body: JSON.stringify({ code: categoryL1Form.code.trim() }) })
    categoryL1Form.code = ''
    await loadData()
    goTo('/taxonomy')
  } catch (failure) {
    error.value = failure instanceof Error ? failure.message : '一级分类创建失败'
  } finally {
    categorySaving.value = false
  }
}

async function createCategory() {
  if (!categoryForm.categoryL1Id || !categoryForm.categoryL2.trim()) return
  categorySaving.value = true
  error.value = ''
  try {
    await requestJson('/platform/categories', {
      method: 'POST',
      body: JSON.stringify({ categoryL1Id: categoryForm.categoryL1Id, categoryL2: categoryForm.categoryL2.trim() }),
    })
    categoryForm.categoryL2 = ''
    await loadData()
    goTo('/taxonomy')
  } catch (failure) {
    error.value = failure instanceof Error ? failure.message : '二级分类创建失败'
  } finally {
    categorySaving.value = false
  }
}

async function saveSlot() {
  const enumValues = parseEnumValues(slotForm.enumValues)
  if (!slotForm.categoryId || !slotForm.key.trim() || !enumValues.length) return
  categorySaving.value = true
  error.value = ''
  try {
    if (editingSlot.value) {
      await requestJson(`/platform/category-slots/${editingSlot.value.id}`, {
        method: 'PATCH', body: JSON.stringify({ isRequired: slotForm.isRequired, enumValues }),
      })
    } else {
      await requestJson(`/platform/categories/${slotForm.categoryId}/slots`, {
        method: 'POST', body: JSON.stringify({ key: slotForm.key.trim(), isRequired: slotForm.isRequired, enumValues }),
      })
    }
    resetSlotForm()
    await loadData()
    goTo('/taxonomy')
  } catch (failure) {
    error.value = failure instanceof Error ? failure.message : '槽位保存失败'
  } finally {
    categorySaving.value = false
  }
}

async function deleteCategoryLevelOne(category: CategoryLevelOne) {
  if (!window.confirm(`确认删除一级分类“${category.code}”吗？`)) return
  try {
    await requestJson(`/platform/category-level-ones/${category.id}`, { method: 'DELETE' })
    await loadData()
  } catch (failure) {
    error.value = failure instanceof Error ? failure.message : '一级分类删除失败'
  }
}

async function deleteCategory(category: Category) {
  if (!window.confirm(`确认删除二级分类“${category.categoryL2}”吗？`)) return
  try {
    await requestJson(`/platform/categories/${category.id}`, { method: 'DELETE' })
    await loadData()
  } catch (failure) {
    error.value = failure instanceof Error ? failure.message : '二级分类删除失败'
  }
}

async function deleteSlot(slot: CategorySlot) {
  if (!window.confirm(`确认删除槽位“${slot.key}”吗？`)) return
  try {
    await requestJson(`/platform/category-slots/${slot.id}`, { method: 'DELETE' })
    await loadData()
  } catch (failure) {
    error.value = failure instanceof Error ? failure.message : '槽位删除失败'
  }
}

function openSlotEditor(slot?: CategorySlot) {
  goTo(slot ? `/taxonomy/slot/edit/${slot.id}` : '/taxonomy/slot/new')
}

function browseCategoryProducts(category: Category) {
  clearProductScope()
  productQuery.value = ''
  productStatus.value = ''
  productStock.value = 'all'
  productCategory.value = category.categoryL2
  goTo('/products')
}

function selectAllMerchants() {
  selectedMerchantKeys.value = merchantGroups.value.map((merchant) => merchant.ownerUserId)
  selectedStoreIds.value = []
}

function clearProductScope() {
  selectedMerchantKeys.value = []
  selectedStoreIds.value = []
}

function clearProductFilters() {
  clearProductScope()
  productQuery.value = ''
  productStatus.value = ''
  productCategory.value = ''
  productStock.value = 'all'
}

function openProduct(product: Product) {
  selectedProduct.value = product
}

function closeProductDetails() {
  selectedProduct.value = null
}

function openStatusEditor(store: Merchant) {
  goTo(`/merchants/${store.id}/status`)
}

async function saveStoreStatus() {
  if (!statusStore.value) return
  if (statusStore.value.isEnabled && !disabledReason.value.trim()) {
    error.value = '禁用店铺时必须填写原因'
    return
  }
  statusSaving.value = true
  error.value = ''
  try {
    await requestJson(`/platform/merchants/${statusStore.value.id}/status`, {
      method: 'PATCH',
      body: JSON.stringify({
        isEnabled: !statusStore.value.isEnabled,
        disabledReason: statusStore.value.isEnabled ? disabledReason.value.trim() : undefined,
      }),
    })
    await loadData()
    goTo('/merchants')
  } catch (failure) {
    error.value = failure instanceof Error ? failure.message : '店铺状态更新失败'
  } finally {
    statusSaving.value = false
  }
}

onMounted(() => {
  routeFromHash()
  window.addEventListener('hashchange', routeFromHash)
  void loadData()
})

onBeforeUnmount(() => window.removeEventListener('hashchange', routeFromHash))
</script>

<template>
  <AppShell
    eyebrow="VOICE COMMERCE · PLATFORM"
    title="声选平台"
    :description="pageDescription"
    :nav-items="navItems"
    :active-nav-href="activeNavHref"
    :hero-compact="true"
    action-label="平台管理员"
  >
    <template #headline>{{ pageHeadline }}</template>
    <template #hero-action>
      <div v-if="isTaxonomyPage" class="section-actions"><button class="primary-button" type="button" @click="goTo('/taxonomy/level-one/new')">新增一级品类</button><button class="secondary-button" type="button" @click="goTo('/taxonomy/level-two/new')">新增二级品类</button><button class="ghost-button" type="button" @click="openSlotEditor()">新增槽位</button></div>
      <button v-else-if="isOperationPage" class="ghost-button" type="button" @click="goTo(isStatusEditor ? '/merchants' : '/taxonomy')">返回管理页</button>
      <button v-else-if="isProductPage" class="ghost-button" type="button" @click="clearProductFilters">重置筛选</button>
    </template>
    <template #hero-panel>
      <div class="hero-panel"><span class="hero-panel__label">平台健康度</span><div><p class="hero-panel__value">{{ enabledStores }}/{{ merchants.length }} 店铺启用</p><p class="hero-panel__note">{{ products.length }} 件商品 · {{ successfulOrders.length }} 笔成功订单 · 成交 ¥{{ formatPrice(grossMerchandiseValue) }}</p></div></div>
    </template>

    <div class="workspace">
      <p v-if="error" class="error-banner">{{ error }}</p>

      <section v-if="isTaxonomyPage" class="taxonomy-diagnostic-strip" aria-label="导购支持概览">
        <div class="taxonomy-diagnostic-strip__lead">
          <span class="section-kicker">AGENT READINESS</span>
          <strong>导购支持范围</strong>
        </div>
        <dl class="taxonomy-diagnostic-metrics">
          <div><dt>已定义品类</dt><dd>{{ categories.length }}</dd></div>
          <div><dt>有候选品类</dt><dd>{{ categoriesWithAgentCandidates }}</dd></div>
          <div><dt>可推荐商品</dt><dd>{{ totalAgentCandidateProducts }}</dd></div>
          <div><dt>必填槽位</dt><dd>{{ totalRequiredSlots }}</dd></div>
        </dl>
      </section>

      <section v-if="isTaxonomyPage" class="section-panel">
        <div class="section-heading"><div><span class="section-kicker">SUPPORT DIRECTORY</span><h2>支持品类与槽位</h2><p>品类、在库供给和必填需求槽位集中展示，用于判断当前导购是否具备推荐条件。</p></div><span class="section-count">{{ categoryLevelOnes.length }} 个一级分类 · {{ categories.length }} 个二级分类</span></div>
        <div class="taxonomy-filter" aria-label="支持目录筛选">
          <label class="form-field taxonomy-filter__search">搜索品类或槽位<input v-model="taxonomyQuery" class="input" placeholder="例如：耳机、HEADPHONES、connectivity" /></label>
          <label class="form-field">一级分类<select v-model="taxonomyLevelOneFilter" class="select"><option value="">全部一级分类</option><option v-for="levelOne in categoryLevelOnes" :key="levelOne.id" :value="levelOne.id">{{ levelOne.code }}</option></select></label>
        </div>
        <p v-if="loading" class="empty-state">正在加载品类结构…</p>
        <p v-else-if="!taxonomyGroups.length" class="empty-state">{{ hasTaxonomyFilter ? '未找到匹配的品类或槽位。' : '还没有分类，请先新增一级分类。' }}</p>
        <div v-else class="taxonomy-list">
          <article v-for="group in taxonomyGroups" :key="group.levelOne.id" class="taxonomy-group">
            <div class="taxonomy-heading"><div><span class="badge">一级分类</span><h3>{{ group.levelOne.code }}</h3></div><button v-if="!group.categories.length" class="danger-button small-button" type="button" @click="deleteCategoryLevelOne(group.levelOne)">删除一级分类</button></div>
            <p v-if="!group.categories.length" class="muted">暂无二级分类。</p>
            <div v-else class="taxonomy-children">
              <article v-for="category in group.categories" :key="category.id" class="taxonomy-child">
                <div class="taxonomy-child__heading">
                  <div><span class="badge">二级品类</span><h3>{{ categoryDisplayName(category.categoryL2) }}</h3></div>
                  <dl class="taxonomy-readiness" aria-label="品类供给状态">
                    <div><dt>商品</dt><dd>{{ getCategorySupport(category).productCount }}</dd></div>
                    <div><dt>可推荐</dt><dd :class="{ 'taxonomy-readiness__value--empty': getCategorySupport(category).agentCandidateCount === 0 }">{{ getCategorySupport(category).agentCandidateCount }}</dd></div>
                    <div><dt>必填槽位</dt><dd>{{ getCategorySupport(category).requiredSlotCount }}</dd></div>
                    <div><dt>选填槽位</dt><dd>{{ getCategorySupport(category).optionalSlotCount }}</dd></div>
                  </dl>
                  <div class="section-actions"><button class="ghost-button small-button" type="button" @click="browseCategoryProducts(category)">查看商品</button><button class="danger-button small-button" type="button" @click="deleteCategory(category)">删除二级分类</button></div>
                </div>
                <div class="slot-list"><span v-for="slot in category.slots" :key="slot.id" class="slot-chip" :class="{ 'slot-chip--optional': !slot.isRequired }">{{ slot.key }} · {{ slot.isRequired ? '必填' : '选填' }} · {{ slot.enumValues.join(' / ') }}<button class="ghost-button small-button" type="button" @click="openSlotEditor(slot)">编辑</button><button class="danger-button small-button" type="button" @click="deleteSlot(slot)">删除</button></span><span v-if="!category.slots.length" class="muted">暂无槽位。</span></div>
              </article>
            </div>
          </article>
        </div>
      </section>

      <section v-else-if="isProductPage" class="section-panel">
        <div class="section-heading"><div><span class="section-kicker">GLOBAL CATALOG</span><h2>全局商品浏览</h2><p>未选择范围时展示全部商品；勾选商家或店铺即可进行单选、多选组合筛选。</p></div><span class="section-count">{{ visibleProducts.length }} / {{ products.length }} 件</span></div>
        <div class="scope-filter-grid">
          <section class="selection-pane"><div class="selection-pane__heading"><strong>按商家筛选</strong><div class="section-actions"><button class="ghost-button small-button" type="button" @click="selectAllMerchants">全选</button><button class="ghost-button small-button" type="button" @click="clearProductScope">清空</button></div></div><div class="checkbox-list"><label v-for="merchant in merchantGroups" :key="merchant.ownerUserId" class="selection-option"><input v-model="selectedMerchantKeys" type="checkbox" :value="merchant.ownerUserId" /><span><strong>{{ merchant.ownerDisplayName }}</strong><small>{{ merchant.stores.length }} 家店铺</small></span></label></div></section>
          <section class="selection-pane"><div class="selection-pane__heading"><strong>按店铺筛选</strong><span class="muted">可多选</span></div><div class="checkbox-list"><label v-for="store in availableStores" :key="store.id" class="selection-option"><input v-model="selectedStoreIds" type="checkbox" :value="store.id" /><span><strong>{{ store.name }}</strong><small>{{ store.productCount }} 件商品 · {{ store.isEnabled ? '启用' : '禁用' }}</small></span></label><span v-if="!availableStores.length" class="muted">没有匹配的店铺。</span></div></section>
        </div>
        <div class="filter-toolbar filter-toolbar--platform" aria-label="商品条件筛选"><label class="form-field filter-toolbar__search">搜索商品<input v-model="productQuery" class="input" placeholder="商品名、SKU、品牌或店铺" /></label><label class="form-field">状态<select v-model="productStatus" class="select"><option value="">全部状态</option><option value="on_sale">在售</option><option value="draft">草稿</option><option value="off_sale">已下架</option></select></label><label class="form-field">品类<select v-model="productCategory" class="select"><option value="">全部品类</option><option v-for="category in categories" :key="category.id" :value="category.categoryL2">{{ categoryLabel(category.categoryL2) }}</option></select></label><label class="form-field">库存<select v-model="productStock" class="select"><option value="all">全部库存</option><option value="available">有库存</option><option value="low">低库存（≤10）</option><option value="out">缺货</option></select></label></div>
        <p v-if="loading" class="empty-state">正在加载全局商品…</p>
        <p v-else-if="!visibleProducts.length" class="empty-state">当前范围没有符合条件的商品。</p>
        <div v-else class="table-wrap"><table class="data-table"><thead><tr><th>商品</th><th>商家 / 店铺</th><th>标准品类</th><th>价格</th><th>库存</th><th>状态</th></tr></thead><tbody><tr v-for="product in visibleProducts" :key="product.id" class="product-row" tabindex="0" @click="openProduct(product)"><td><strong>{{ product.name }}</strong><br /><span class="muted">{{ product.sku }} · {{ product.brand || '无品牌' }}</span></td><td>{{ product.merchantName }}</td><td>{{ categoryLabel(product.categoryL2) }}</td><td class="order-total">¥{{ formatPrice(product.price) }}</td><td>{{ product.stock }}</td><td><span class="badge" :class="{ 'badge--disabled': product.status !== 'on_sale' }">{{ product.status === 'on_sale' ? '在售' : product.status === 'draft' ? '草稿' : '已下架' }}</span></td></tr></tbody></table></div>
      </section>

      <section v-else-if="isMerchantPage" class="section-panel">
        <div class="section-heading"><div><span class="section-kicker">MERCHANT GOVERNANCE</span><h2>商家与店铺</h2><p>每个店铺的启停状态都会即时影响用户端和 Agent 可见的供给范围。</p></div></div>
        <div class="merchant-group-list"><section v-for="merchant in merchantGroups" :key="merchant.ownerUserId" class="merchant-group"><div class="merchant-group__heading"><div><span class="badge">{{ merchant.stores.length }} 家店铺</span><h3>{{ merchant.ownerDisplayName }}</h3></div><span class="muted">{{ merchant.stores.reduce((total, store) => total + store.productCount, 0) }} 件商品</span></div><div class="store-grid"><article v-for="store in merchant.stores" :key="store.id" class="store-card"><span class="badge" :class="{ 'badge--disabled': !store.isEnabled }">{{ store.isEnabled ? '已启用' : '已禁用' }}</span><h3>{{ store.name }}</h3><p>{{ store.description || '暂无店铺介绍。' }}</p><p v-if="store.disabledReason" class="reason">禁用原因：{{ store.disabledReason }}</p><div class="card-footer"><span class="muted">{{ store.productCount }} 件商品</span><button :class="store.isEnabled ? 'danger-button' : 'secondary-button'" class="small-button" type="button" @click="openStatusEditor(store)">{{ store.isEnabled ? '禁用店铺' : '恢复启用' }}</button></div></article></div></section></div>
      </section>

      <section v-else-if="isOrdersPage" class="section-panel">
        <div class="section-heading"><div><span class="section-kicker">PLATFORM ORDERS</span><h2>全平台订单</h2><p>从结果看交易状态，关注待确认和失败订单。</p></div><label class="form-field compact-field">订单状态<select v-model="orderStatus" class="select"><option value="">全部状态</option><option value="pending">待确认</option><option value="success">已完成</option><option value="fail">已取消</option></select></label></div>
        <p v-if="!visibleOrders.length" class="empty-state">当前没有匹配的订单。</p>
        <div v-else class="table-wrap"><table class="data-table"><thead><tr><th>商品</th><th>商家</th><th>用户</th><th>金额</th><th>状态</th><th>失败原因</th><th>时间</th></tr></thead><tbody><tr v-for="order in visibleOrders" :key="order.id"><td>{{ order.productSnapshot.name }}</td><td>{{ order.merchantSnapshot.name }}</td><td>{{ order.userId.slice(0, 8) }}…</td><td class="order-total">¥{{ formatPrice(order.totalAmount) }}</td><td><span class="badge" :class="`badge--${order.status}`">{{ order.status === 'pending' ? '待确认' : order.status === 'success' ? '已完成' : '已取消' }}</span></td><td>{{ order.failureReason || '—' }}</td><td><time :datetime="order.createdAt">{{ formatDateTime(order.createdAt) }}</time></td></tr></tbody></table></div>
      </section>

      <section v-else-if="isLevelOneEditor" class="section-panel operation-page"><div class="operation-page__intro"><span class="section-kicker">LEVEL ONE CATEGORY</span><h2>新增一级品类</h2><p>一级品类是二级品类与商品属性的顶层组织方式。</p></div><form class="form-grid operation-form" @submit.prevent="createCategoryLevelOne"><label class="form-field form-field--wide">一级分类编码<input v-model="categoryL1Form.code" class="input" placeholder="ELECTRONICS" required /></label><div class="form-actions form-field--full"><button class="ghost-button" type="button" @click="goTo('/taxonomy')">取消</button><button class="primary-button" type="submit" :disabled="categorySaving">{{ categorySaving ? '创建中…' : '创建一级品类' }}</button></div></form></section>

      <section v-else-if="isLevelTwoEditor" class="section-panel operation-page"><div class="operation-page__intro"><span class="section-kicker">LEVEL TWO CATEGORY</span><h2>新增二级品类</h2><p>二级品类将直接用于商品归类、筛选和 Agent 的推荐条件。</p></div><form class="form-grid operation-form" @submit.prevent="createCategory"><label class="form-field">关联一级分类<select v-model="categoryForm.categoryL1Id" class="select" required><option value="" disabled>请选择一级分类</option><option v-for="item in categoryLevelOnes" :key="item.id" :value="item.id">{{ item.code }}</option></select></label><label class="form-field">二级分类编码<input v-model="categoryForm.categoryL2" class="input" placeholder="HEADPHONES" required /></label><div class="form-actions form-field--full"><button class="ghost-button" type="button" @click="goTo('/taxonomy')">取消</button><button class="primary-button" type="submit" :disabled="categorySaving || !categoryLevelOnes.length">{{ categorySaving ? '创建中…' : '创建二级品类' }}</button></div></form></section>

      <section v-else-if="isSlotEditor" class="section-panel operation-page"><div class="operation-page__intro"><span class="section-kicker">PRODUCT SLOT</span><h2>{{ editingSlot ? '编辑槽位' : '新增槽位' }}</h2><p>槽位的可选值会成为商品资料的标准化参数，并帮助 Agent 澄清用户需求。</p></div><form class="form-grid operation-form" @submit.prevent="saveSlot"><label class="form-field">所属一级分类<select v-model="slotForm.categoryL1Id" class="select" :disabled="Boolean(editingSlot)" required><option value="" disabled>请选择一级分类</option><option v-for="item in categoryLevelOnes" :key="item.id" :value="item.id">{{ item.code }}</option></select></label><label class="form-field">所属二级分类<select v-model="slotForm.categoryId" class="select" :disabled="Boolean(editingSlot) || !slotForm.categoryL1Id" required><option value="" disabled>请选择二级分类</option><option v-for="category in slotCategories" :key="category.id" :value="category.id">{{ category.categoryL2 }}</option></select></label><label class="form-field">槽位 Key<input v-model="slotForm.key" class="input" :disabled="Boolean(editingSlot)" placeholder="connectivity" required /></label><label class="form-field">是否必填<select v-model="slotForm.isRequired" class="select"><option :value="true">必填</option><option :value="false">选填</option></select></label><label class="form-field form-field--wide">枚举值（逗号分隔）<input v-model="slotForm.enumValues" class="input" placeholder="bluetooth, wired" required /></label><div class="form-actions form-field--full"><button class="ghost-button" type="button" @click="goTo('/taxonomy')">取消</button><button class="primary-button" type="submit" :disabled="categorySaving || !slotCategories.length">{{ categorySaving ? '保存中…' : editingSlot ? '保存修改' : '创建槽位' }}</button></div></form></section>

      <section v-else-if="isStatusEditor && statusStore" class="section-panel operation-page"><div class="operation-page__intro"><span class="section-kicker">STORE STATUS</span><h2>{{ statusStore.isEnabled ? '禁用店铺' : '恢复店铺' }}</h2><p>{{ statusStore.name }} {{ statusStore.isEnabled ? '被禁用后，其商品会从用户端和 Agent 候选中移除。' : '恢复后会按商品自身状态重新参与供给。' }}</p></div><form class="form-stack operation-form" @submit.prevent="saveStoreStatus"><label v-if="statusStore.isEnabled" class="form-field">禁用原因<textarea v-model="disabledReason" class="textarea" required /></label><p v-else class="empty-state">确认恢复后，该店铺在售且有库存的商品将重新可被用户浏览。</p><div class="form-actions"><button class="ghost-button" type="button" @click="goTo('/merchants')">取消</button><button :class="statusStore.isEnabled ? 'danger-button' : 'primary-button'" type="submit" :disabled="statusSaving">{{ statusSaving ? '保存中…' : statusStore.isEnabled ? '确认禁用' : '确认恢复' }}</button></div></form></section>
    </div>
    <ProductDetailModal v-if="selectedProduct && isProductPage" :product="selectedProduct" @close="closeProductDetails" />
  </AppShell>
</template>
