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
import { computed, onMounted, reactive, ref } from 'vue'

const navItems = [
  { label: '平台概览', href: '#overview' },
  { label: '品类管理', href: '#categories' },
  { label: '商家治理', href: '#merchants' },
  { label: '店铺总览', href: '#stores' },
  { label: '商品总览', href: '#products' },
  { label: '全量订单', href: '#orders' },
]

interface MerchantGroup {
  ownerUserId: string
  ownerDisplayName: string
  stores: Merchant[]
}

const merchants = ref<Merchant[]>([])
const products = ref<Product[]>([])
const orders = ref<Order[]>([])
const categories = ref<Category[]>([])
const categoryLevelOnes = ref<CategoryLevelOne[]>([])
const categorySaving = ref(false)
const categoryL1Form = reactive({ code: '' })
const categoryForm = reactive({ categoryL1Id: '', categoryL2: '' })
const slotForm = reactive({ categoryId: '', key: '', isRequired: true, enumValues: '' })
const error = ref('')
const selectedProduct = ref<Product | null>(null)
const selectedMerchantKey = ref('')
const selectedStoreId = ref('')
const productQuery = ref('')
const orderStatus = ref('')
const merchantGroups = computed<MerchantGroup[]>(() => {
  const groups = new Map<string, MerchantGroup>()
  for (const store of merchants.value) {
    const existing = groups.get(store.ownerUserId)
    if (existing) {
      existing.stores.push(store)
      continue
    }
    groups.set(store.ownerUserId, {
      ownerUserId: store.ownerUserId,
      ownerDisplayName: store.ownerDisplayName || `商家账号 ${store.ownerUserId.slice(0, 8)}…`,
      stores: [store],
    })
  }
  return [...groups.values()]
})
const selectedMerchant = computed(() => merchantGroups.value.find((merchant) => merchant.ownerUserId === selectedMerchantKey.value))
const selectedStore = computed(() => selectedMerchant.value?.stores.find((store) => store.id === selectedStoreId.value))
const enabledStores = computed(() => merchants.value.filter((item) => item.isEnabled).length)
const successfulOrders = computed(() => orders.value.filter((item) => item.status === 'success'))
const grossMerchandiseValue = computed(() => successfulOrders.value.reduce((sum, item) => sum + Number(item.totalAmount), 0))
const visibleProducts = computed(() => {
  if (!selectedStoreId.value) return []
  const query = productQuery.value.trim().toLowerCase()
  const storeProducts = products.value.filter((item) => item.merchantId === selectedStoreId.value)
  if (!query) return storeProducts
  return storeProducts.filter((item) => `${item.name} ${item.brand ?? ''} ${item.merchantName ?? ''}`.toLowerCase().includes(query))
})
const visibleOrders = computed(() =>
  orderStatus.value ? orders.value.filter((item) => item.status === orderStatus.value) : orders.value,
)
async function loadData() {
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
    if (selectedMerchantKey.value && !merchantGroups.value.some((merchant) => merchant.ownerUserId === selectedMerchantKey.value)) {
      selectedMerchantKey.value = ''
      selectedStoreId.value = ''
      selectedProduct.value = null
    } else if (selectedStoreId.value && !selectedMerchant.value?.stores.some((store) => store.id === selectedStoreId.value)) {
      selectedStoreId.value = ''
      selectedProduct.value = null
    }
    if (!categoryForm.categoryL1Id && categoryLevelOnes.value[0]) {
      categoryForm.categoryL1Id = categoryLevelOnes.value[0].id
    }
    if (!slotForm.categoryId && categories.value[0]) slotForm.categoryId = categories.value[0].id
  } catch (reason) {
    error.value = reason instanceof Error ? reason.message : '平台数据加载失败'
  }
}

function parseEnumValues(value: string): Array<string | number | boolean> {
  return [...new Set(value.split(/[,，]+/).map((item) => item.trim()).filter(Boolean))].map((item) => {
    if (item === 'true') return true
    if (item === 'false') return false
    const number = Number(item)
    return item !== '' && Number.isFinite(number) ? number : item
  })
}

async function createCategoryLevelOne() {
  if (!categoryL1Form.code.trim()) return
  categorySaving.value = true
  try {
    await requestJson('/platform/category-level-ones', {
      method: 'POST',
      body: JSON.stringify({ code: categoryL1Form.code.trim() }),
    })
    categoryL1Form.code = ''
    await loadData()
  } catch (failure) {
    error.value = failure instanceof Error ? failure.message : '一级分类创建失败'
  } finally { categorySaving.value = false }
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

async function createCategory() {
  if (!categoryForm.categoryL1Id || !categoryForm.categoryL2.trim()) return
  categorySaving.value = true
  try {
    await requestJson('/platform/categories', {
      method: 'POST',
      body: JSON.stringify({
        categoryL1Id: categoryForm.categoryL1Id,
        categoryL2: categoryForm.categoryL2.trim(),
      }),
    })
    categoryForm.categoryL2 = ''
    await loadData()
  } catch (failure) {
    error.value = failure instanceof Error ? failure.message : '分类创建失败'
  } finally { categorySaving.value = false }
}

async function createSlot() {
  const enumValues = parseEnumValues(slotForm.enumValues)
  if (!slotForm.categoryId || !slotForm.key.trim() || !enumValues.length) return
  categorySaving.value = true
  try {
    await requestJson(`/platform/categories/${slotForm.categoryId}/slots`, {
      method: 'POST',
      body: JSON.stringify({
        key: slotForm.key.trim(),
        isRequired: slotForm.isRequired,
        enumValues,
      }),
    })
    Object.assign(slotForm, { key: '', isRequired: true, enumValues: '' })
    await loadData()
  } catch (failure) {
    error.value = failure instanceof Error ? failure.message : '槽位创建失败'
  } finally { categorySaving.value = false }
}

async function editSlot(slot: CategorySlot) {
  const values = window.prompt('枚举值（逗号分隔，至少一个）', slot.enumValues.join(', '))
  if (values === null) return
  const enumValues = parseEnumValues(values)
  if (!enumValues.length) {
    error.value = '槽位必须至少保留一个枚举值'
    return
  }
  await requestJson(`/platform/category-slots/${slot.id}`, {
    method: 'PATCH',
    body: JSON.stringify({ enumValues }),
  })
  await loadData()
}

async function deleteSlot(slot: CategorySlot) {
  if (!window.confirm(`确认删除槽位“${slot.key}”吗？`)) return
  await requestJson(`/platform/category-slots/${slot.id}`, { method: 'DELETE' })
  await loadData()
}

async function deleteCategory(category: Category) {
  if (!window.confirm(`确认删除二级分类“${category.categoryL2}”吗？`)) return
  await requestJson(`/platform/categories/${category.id}`, { method: 'DELETE' })
  await loadData()
}

async function toggleMerchant(merchant: Merchant) {
  let reason: string | undefined
  if (merchant.isEnabled) {
    const value = window.prompt(`请输入禁用“${merchant.name}”的原因`, '平台人工审核')
    if (!value?.trim()) return
    reason = value.trim()
  }
  try {
    await requestJson<Merchant>(`/platform/merchants/${merchant.id}/status`, {
      method: 'PATCH',
      body: JSON.stringify({ isEnabled: !merchant.isEnabled, disabledReason: reason }),
    })
    await loadData()
  } catch (failure) {
    error.value = failure instanceof Error ? failure.message : '商家状态更新失败'
  }
}

function openProduct(product: Product) {
  selectedProduct.value = product
}

function handleProductKeydown(event: KeyboardEvent, product: Product) {
  if (event.key !== 'Enter' && event.key !== ' ') return
  event.preventDefault()
  openProduct(product)
}

function closeProductDetails() {
  selectedProduct.value = null
}

function selectMerchant(merchant: MerchantGroup) {
  selectedMerchantKey.value = merchant.ownerUserId
  selectedStoreId.value = ''
  selectedProduct.value = null
  productQuery.value = ''
  document.querySelector('#stores')?.scrollIntoView({ behavior: 'smooth', block: 'start' })
}

function handleMerchantKeydown(event: KeyboardEvent, merchant: MerchantGroup) {
  if (event.key !== 'Enter' && event.key !== ' ') return
  event.preventDefault()
  selectMerchant(merchant)
}

function selectStore(store: Merchant) {
  selectedStoreId.value = store.id
  selectedProduct.value = null
  productQuery.value = ''
  document.querySelector('#products')?.scrollIntoView({ behavior: 'smooth', block: 'start' })
}

function handleStoreKeydown(event: KeyboardEvent, store: Merchant) {
  if (event.key !== 'Enter' && event.key !== ' ') return
  event.preventDefault()
  selectStore(store)
}

function clearSelectedMerchant() {
  selectedMerchantKey.value = ''
  selectedStoreId.value = ''
  selectedProduct.value = null
  productQuery.value = ''
}

function clearSelectedStore() {
  selectedStoreId.value = ''
  selectedProduct.value = null
  productQuery.value = ''
}

onMounted(() => void loadData())
</script>

<template>
  <AppShell
    eyebrow="VOICE COMMERCE · PLATFORM"
    title="声选平台"
    description="集中查看全量商家、商品和订单，以商家启停状态实时控制用户端供给。"
    :nav-items="navItems"
    action-label="平台管理员"
  >
    <template #headline>看清平台全局，<br />守住交易边界。</template>
    <template #hero-action><a class="primary-button" href="#overview">打开运营总览</a></template>
    <template #hero-panel><div class="hero-panel"><span class="hero-panel__label">平台健康度</span><div><p class="hero-panel__value">{{ enabledStores }}/{{ merchants.length }} 店铺启用</p><p class="hero-panel__note">店铺禁用后，其在售商品会立即从用户浏览和 Agent 推荐候选中移除。</p></div></div></template>

    <div class="workspace">
      <p v-if="error" class="error-banner">{{ error }}</p>
      <section id="overview" class="stat-grid">
        <article class="stat-card"><span class="stat-label">全部商家</span><span class="stat-value">{{ merchantGroups.length }}</span></article>
        <article class="stat-card"><span class="stat-label">全部商品</span><span class="stat-value">{{ products.length }}</span></article>
        <article class="stat-card"><span class="stat-label">成功订单</span><span class="stat-value">{{ successfulOrders.length }}</span></article>
        <article class="stat-card"><span class="stat-label">平台成交额</span><span class="stat-value">¥{{ grossMerchandiseValue }}</span></article>
      </section>

      <section id="categories" class="section-panel">
        <div class="section-heading"><div><h2>品类与槽位</h2><p>先创建一级分类，再创建关联的二级分类；每个槽位都必须配置枚举值。</p></div></div>
        <h3>1. 创建一级分类</h3>
        <form class="form-grid" @submit.prevent="createCategoryLevelOne">
          <label class="form-field form-field--wide">一级分类编码<input v-model="categoryL1Form.code" class="input" placeholder="ELECTRONICS" required /></label>
          <button class="primary-button" type="submit" :disabled="categorySaving">新增一级分类</button>
        </form>
        <div class="slot-list">
          <span v-for="item in categoryLevelOnes" :key="item.id" class="slot-chip">
            {{ item.code }}
            <button class="danger-button small-button" type="button" @click="deleteCategoryLevelOne(item)">删除</button>
          </span>
        </div>
        <h3>2. 创建二级分类</h3>
        <form class="form-grid" @submit.prevent="createCategory">
          <label class="form-field">关联一级分类<select v-model="categoryForm.categoryL1Id" class="select" required><option value="" disabled>请选择一级分类</option><option v-for="item in categoryLevelOnes" :key="item.id" :value="item.id">{{ item.code }}</option></select></label>
          <label class="form-field">二级分类<input v-model="categoryForm.categoryL2" class="input" placeholder="HEADPHONES" required /></label>
          <button class="primary-button" type="submit" :disabled="categorySaving || !categoryLevelOnes.length">新增二级分类</button>
        </form>
        <h3>3. 创建槽位</h3>
        <form class="form-grid" @submit.prevent="createSlot">
          <label class="form-field">所属二级分类<select v-model="slotForm.categoryId" class="select" required><option value="" disabled>请选择二级分类</option><option v-for="category in categories" :key="category.id" :value="category.id">{{ category.categoryL1 }} / {{ category.categoryL2 }}</option></select></label>
          <label class="form-field">槽位 Key<input v-model="slotForm.key" class="input" placeholder="connectivity" required /></label>
          <label class="form-field">是否必填<select v-model="slotForm.isRequired" class="select"><option :value="true">必填</option><option :value="false">选填</option></select></label>
          <label class="form-field form-field--wide">枚举值（逗号分隔）<input v-model="slotForm.enumValues" class="input" placeholder="bluetooth, wired" required /></label>
          <button class="primary-button" type="submit" :disabled="categorySaving || !categories.length">新增槽位</button>
        </form>
        <div class="taxonomy-list">
          <article v-for="category in categories" :key="category.id" class="taxonomy-group">
            <div class="taxonomy-heading">
              <div><span class="badge">一级 · {{ category.categoryL1 }}</span><h3>{{ category.categoryL2 }}</h3></div>
              <div class="section-actions"><button class="danger-button small-button" @click="deleteCategory(category)">删除二级分类</button></div>
            </div>
            <div class="slot-list">
              <span v-for="slot in category.slots" :key="slot.id" class="slot-chip" :class="{ 'slot-chip--optional': !slot.isRequired }">
                {{ slot.key }} · {{ slot.isRequired ? '必填' : '选填' }} · {{ slot.enumValues.join(' / ') }}
                <button class="ghost-button small-button" type="button" @click="editSlot(slot)">编辑</button>
                <button class="danger-button small-button" type="button" @click="deleteSlot(slot)">删除</button>
              </span>
              <span v-if="!category.slots.length" class="muted">暂未配置槽位，请先创建带枚举值的槽位</span>
            </div>
          </article>
        </div>
      </section>

      <section id="merchants" class="section-panel">
        <div class="section-heading"><div><h2>商家治理</h2><p>先选择商家，再进入该商家下的店铺；禁用必须记录原因。</p></div></div>
        <div class="store-grid">
          <article v-for="merchant in merchantGroups" :key="merchant.ownerUserId" class="store-card selectable-card" :class="{ 'store-card--selected': selectedMerchantKey === merchant.ownerUserId }" role="button" tabindex="0" :aria-pressed="selectedMerchantKey === merchant.ownerUserId" :aria-label="`查看${merchant.ownerDisplayName}的店铺`" @click="selectMerchant(merchant)" @keydown="handleMerchantKeydown($event, merchant)">
            <span class="badge">{{ merchant.stores.length }} 家店铺</span>
            <h3>{{ merchant.ownerDisplayName }}</h3><p>商家账号下的店铺与商品供给</p>
            <div class="card-footer"><span class="muted">{{ merchant.stores.reduce((count, store) => count + store.productCount, 0) }} 件商品</span><span class="select-hint">点击查看店铺</span></div>
          </article>
        </div>
      </section>

      <section id="stores" class="section-panel">
        <template v-if="selectedMerchant">
          <div class="section-heading"><div><h2>{{ selectedMerchant.ownerDisplayName }} · 店铺</h2><p>请选择一个店铺，查看该店铺下的商品情况。</p></div><button class="ghost-button" type="button" @click="clearSelectedMerchant">返回商家</button></div>
          <div class="store-grid">
            <article v-for="store in selectedMerchant.stores" :key="store.id" class="store-card selectable-card" :class="{ 'store-card--selected': selectedStoreId === store.id }" role="button" tabindex="0" :aria-pressed="selectedStoreId === store.id" :aria-label="`查看${store.name}商品`" @click="selectStore(store)" @keydown="handleStoreKeydown($event, store)">
              <span class="badge" :class="{ 'badge--disabled': !store.isEnabled }">{{ store.isEnabled ? '已启用' : '已禁用' }}</span>
              <h3>{{ store.name }}</h3><p>{{ store.description }}</p>
              <p v-if="store.disabledReason" class="reason">禁用原因：{{ store.disabledReason }}</p>
              <div class="card-footer"><span class="muted">{{ store.productCount }} 件商品</span><div class="section-actions"><span class="select-hint">点击查看商品</span><button :class="store.isEnabled ? 'danger-button' : 'secondary-button'" class="small-button" @click.stop="toggleMerchant(store)">{{ store.isEnabled ? '禁用店铺' : '恢复启用' }}</button></div></div>
            </article>
          </div>
        </template>
        <p v-else class="empty-state">请先点击上方“商家治理”中的商家，再选择店铺。</p>
      </section>

      <section id="products" class="section-panel">
        <div class="section-heading"><div><h2>{{ selectedStore ? `${selectedStore.name} · 商品总览` : '商品总览' }}</h2><p>{{ selectedStore ? '当前仅展示所选店铺的商品；点击商品行可查看完整详情。' : '请依次选择商家和店铺，再查看商品详情。' }}</p></div><div v-if="selectedStore" class="section-actions"><input v-model="productQuery" class="input" style="width: 260px" placeholder="搜索当前店铺商品" /><button class="ghost-button" type="button" @click="clearSelectedStore">返回店铺</button></div></div>
        <p v-if="!selectedMerchant" class="empty-state">请先点击上方“商家治理”中的商家。</p>
        <p v-else-if="!selectedStore" class="empty-state">已选择商家，请在上方店铺列表中继续选择一个店铺。</p>
        <p v-else-if="!visibleProducts.length" class="empty-state">该店铺暂无匹配商品。</p>
        <div v-else class="table-wrap"><table class="data-table"><thead><tr><th>商品</th><th>店铺</th><th>标准品类</th><th>价格</th><th>库存</th><th>状态</th></tr></thead><tbody><tr v-for="product in visibleProducts" :key="product.id" class="product-row" tabindex="0" :aria-label="`查看${product.name}详情`" @click="openProduct(product)" @keydown="handleProductKeydown($event, product)"><td><strong>{{ product.name }}</strong><br><span class="muted">{{ product.brand || '无品牌' }}</span></td><td>{{ product.merchantName }}</td><td>{{ product.categoryL2 }}</td><td>¥{{ product.price }}</td><td>{{ product.stock }}</td><td><span class="badge" :class="{ 'badge--disabled': product.status !== 'on_sale' }">{{ product.status }}</span></td></tr></tbody></table></div>
      </section>

      <section id="orders" class="section-panel">
        <div class="section-heading"><div><h2>全平台订单</h2><p>订单状态固定为 pending、success 和 fail。</p></div><select v-model="orderStatus" class="select" style="width: auto"><option value="">全部状态</option><option value="pending">pending</option><option value="success">success</option><option value="fail">fail</option></select></div>
        <div class="table-wrap"><table class="data-table"><thead><tr><th>商品</th><th>商家</th><th>用户</th><th>金额</th><th>状态</th><th>失败原因</th><th>时间</th></tr></thead><tbody><tr v-for="order in visibleOrders" :key="order.id"><td>{{ order.productSnapshot.name }}</td><td>{{ order.merchantSnapshot.name }}</td><td>{{ order.userId.slice(0, 8) }}…</td><td>¥{{ order.totalAmount }}</td><td><span class="badge" :class="`badge--${order.status}`">{{ order.status }}</span></td><td>{{ order.failureReason || '—' }}</td><td>{{ new Date(order.createdAt).toLocaleString() }}</td></tr></tbody></table></div>
      </section>
      <ProductDetailModal v-if="selectedProduct" :product="selectedProduct" @close="closeProductDetails" />
    </div>
  </AppShell>
</template>
