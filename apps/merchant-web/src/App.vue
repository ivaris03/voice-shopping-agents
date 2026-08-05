<script setup lang="ts">
import {
  AppShell,
  LoginGate,
  ProductDetailModal,
  clearAccessToken,
  formatCatalogAttributeLabel,
  formatCatalogAttributeValue,
  formatCategoryLabel,
  requestJson,
  type Category,
  type ItemsResponse,
  type Merchant,
  type Order,
  type Product,
} from '@voice-shopping/web-ui'
import { computed, nextTick, onBeforeUnmount, reactive, ref, watch } from 'vue'

type StockFilter = 'all' | 'available' | 'low' | 'out'

const navItems = [
  { label: '商品与店铺', href: '#/catalog' },
  { label: '本店订单', href: '#/orders' },
]

const appReady = ref(false)
let appStarted = false

const currentRoute = ref('/catalog')
const stores = ref<Merchant[]>([])
const products = ref<Product[]>([])
const orders = ref<Order[]>([])
const categories = ref<Category[]>([])
const loading = ref(true)
const saving = ref(false)
const error = ref('')
const selectedProduct = ref<Product | null>(null)

const productQuery = ref('')
const productStoreFilter = ref('')
const productStatusFilter = ref('')
const productCategoryFilter = ref('')
const stockFilter = ref<StockFilter>('all')

const storeForm = reactive({ name: '', slug: '', description: '', logoUrl: '', contactPhone: '' })
const productForm = reactive({
  merchantId: '',
  sku: '',
  name: '',
  categoryL1: '',
  categoryL2: '',
  brand: '',
  description: '',
  price: 0,
  stock: 0,
  sellingPoints: '',
  imageUrls: '',
  status: 'draft' as Product['status'],
})
const productAttributes = reactive<Record<string, string | number | boolean | null>>({})
let syncingProductForm = false

const isCatalogPage = computed(() => currentRoute.value === '/catalog')
const isOrdersPage = computed(() => currentRoute.value === '/orders')
const isStoreEditor = computed(() => /^\/stores\/(new|edit\/[^/]+)$/.test(currentRoute.value))
const isProductEditor = computed(() => /^\/products\/(new|edit\/[^/]+)$/.test(currentRoute.value))
const isOperationPage = computed(() => isStoreEditor.value || isProductEditor.value)
const activeNavHref = computed(() => (isOrdersPage.value ? '#/orders' : '#/catalog'))
const storeEditorId = computed(() => currentRoute.value.match(/^\/stores\/edit\/(.+)$/)?.[1] ?? '')
const productEditorId = computed(() => currentRoute.value.match(/^\/products\/edit\/(.+)$/)?.[1] ?? '')
const editingStore = computed(() => stores.value.find((store) => store.id === storeEditorId.value))
const editingProduct = computed(() => products.value.find((product) => product.id === productEditorId.value))
const selectedCategory = computed(() => categories.value.find((item) => item.categoryL2 === productForm.categoryL2))
const selectedSlots = computed(() => selectedCategory.value?.slots ?? [])

const visibleProducts = computed(() => {
  const query = productQuery.value.trim().toLowerCase()
  return products.value.filter((product) => {
    const matchesQuery = !query || `${product.name} ${product.sku} ${product.brand ?? ''}`.toLowerCase().includes(query)
    const matchesStore = !productStoreFilter.value || product.merchantId === productStoreFilter.value
    const matchesStatus = !productStatusFilter.value || product.status === productStatusFilter.value
    const matchesCategory = !productCategoryFilter.value || product.categoryL2 === productCategoryFilter.value
    const matchesStock = stockFilter.value === 'all'
      || (stockFilter.value === 'available' && product.stock > 0)
      || (stockFilter.value === 'low' && product.stock > 0 && product.stock <= 10)
      || (stockFilter.value === 'out' && product.stock <= 0)
    return matchesQuery && matchesStore && matchesStatus && matchesCategory && matchesStock
  })
})
const pendingOrders = computed(() => orders.value.filter((item) => item.status === 'pending').length)
const revenue = computed(() => orders.value.filter((item) => item.status === 'success').reduce((sum, item) => sum + Number(item.totalAmount), 0))
const pageHeadline = computed(() => {
  if (isStoreEditor.value) return editingStore.value ? '把店铺信息整理得更清楚。' : '开一家新店，让商品有自己的位置。'
  if (isProductEditor.value) return editingProduct.value ? '把商品事实维护到位。' : '新增一个值得被推荐的商品。'
  if (isOrdersPage.value) return '每一笔成交，都有迹可循。'
  return '让好商品，更容易被听见。'
})
const pageDescription = computed(() => {
  if (isOperationPage.value) return '这是独立的操作页面，保存后会回到商品与店铺工作台。'
  if (isOrdersPage.value) return '仅展示当前商家账号旗下店铺产生的订单与交易状态。'
  return '先看全量商品，再按店铺、状态、品类和库存快速定位需要处理的内容。'
})

function routeFromHash() {
  const route = window.location.hash.replace(/^#/, '') || '/catalog'
  const allowed = route === '/catalog' || route === '/orders'
    || /^\/stores\/(new|edit\/[^/]+)$/.test(route)
    || /^\/products\/(new|edit\/[^/]+)$/.test(route)
  if (!allowed) {
    window.location.hash = '#/catalog'
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

function formatPrice(value: string | number) {
  return Number(value).toLocaleString('zh-CN', { minimumFractionDigits: 2, maximumFractionDigits: 2 })
}

function formatDateTime(value: string) {
  const date = new Date(value)
  return Number.isNaN(date.getTime()) ? '—' : date.toLocaleString('zh-CN', { hour12: false })
}

function categoryLabel(value: string) {
  return formatCategoryLabel(value)
}

async function loadData() {
  loading.value = true
  error.value = ''
  try {
    const [storeData, productData, orderData, categoryData] = await Promise.all([
      requestJson<ItemsResponse<Merchant>>('/merchant/stores'),
      requestJson<ItemsResponse<Product>>('/merchant/products'),
      requestJson<ItemsResponse<Order>>('/merchant/orders'),
      requestJson<ItemsResponse<Category>>('/merchant/categories'),
    ])
    stores.value = storeData.items
    products.value = productData.items
    orders.value = orderData.items
    categories.value = categoryData.items
    if (!productForm.merchantId && stores.value[0]) productForm.merchantId = stores.value[0].id
    if (!productForm.categoryL2 && categories.value[0]) productForm.categoryL2 = categories.value[0].categoryL2
    if (productEditorId.value && !editingProduct.value) goTo('/catalog')
    if (storeEditorId.value && !editingStore.value) goTo('/catalog')
  } catch (reason) {
    error.value = reason instanceof Error ? reason.message : '数据加载失败'
  } finally {
    loading.value = false
  }
}

function resetStoreForm(store?: Merchant) {
  Object.assign(storeForm, {
    name: store?.name ?? '',
    slug: store?.slug ?? '',
    description: store?.description ?? '',
    logoUrl: store?.logoUrl ?? '',
    contactPhone: store?.contactPhone ?? '',
  })
}

function resetProductAttributes() {
  for (const key of Object.keys(productAttributes)) delete productAttributes[key]
  const category = selectedCategory.value
  if (!category) return
  productForm.categoryL1 = category.categoryL1
  for (const slot of category.slots) productAttributes[slot.key] = null
}

function resetProductForm(product?: Product) {
  syncingProductForm = true
  Object.assign(productForm, {
    merchantId: product?.merchantId ?? stores.value[0]?.id ?? '',
    sku: product?.sku ?? '',
    name: product?.name ?? '',
    categoryL1: product?.categoryL1 ?? '',
    categoryL2: product?.categoryL2 ?? categories.value[0]?.categoryL2 ?? '',
    brand: product?.brand ?? '',
    description: product?.description ?? '',
    price: Number(product?.price ?? 0),
    stock: Number(product?.stock ?? 0),
    sellingPoints: product?.sellingPoints?.join('，') ?? '',
    imageUrls: product?.imageUrls?.join('\n') ?? '',
    status: product?.status ?? 'draft',
  })
  for (const key of Object.keys(productAttributes)) delete productAttributes[key]
  for (const [key, value] of Object.entries(product?.attributes ?? {})) {
    productAttributes[key] = value as string | number | boolean | null
  }
  resetProductAttributes()
  if (product) {
    for (const [key, value] of Object.entries(product.attributes ?? {})) {
      productAttributes[key] = value as string | number | boolean | null
    }
  }
  syncingProductForm = false
}

watch(currentRoute, () => {
  if (isStoreEditor.value) resetStoreForm(editingStore.value)
  if (isProductEditor.value) resetProductForm(editingProduct.value)
})
watch([stores, categories], () => {
  if (isProductEditor.value) resetProductForm(editingProduct.value)
})
watch(() => productForm.categoryL2, () => {
  if (!syncingProductForm) resetProductAttributes()
})

function openStoreEditor(store?: Merchant) {
  goTo(store ? `/stores/edit/${store.id}` : '/stores/new')
}

function openProductEditor(product?: Product) {
  goTo(product ? `/products/edit/${product.id}` : '/products/new')
}

async function saveStore() {
  if (!storeForm.name.trim() || !storeForm.slug.trim()) return
  saving.value = true
  error.value = ''
  try {
    const payload = {
      name: storeForm.name.trim(),
      slug: storeForm.slug.trim(),
      description: storeForm.description.trim() || null,
      logoUrl: storeForm.logoUrl.trim() || null,
      contactPhone: storeForm.contactPhone.trim() || null,
    }
    if (editingStore.value) await requestJson(`/merchant/stores/${editingStore.value.id}`, { method: 'PATCH', body: JSON.stringify(payload) })
    else await requestJson('/merchant/stores', { method: 'POST', body: JSON.stringify(payload) })
    await loadData()
    resetStoreForm()
    goTo('/catalog')
  } catch (reason) {
    error.value = reason instanceof Error ? reason.message : '保存店铺失败'
  } finally {
    saving.value = false
  }
}

async function removeStore(store: Merchant) {
  if (!window.confirm(`确认软删除店铺“${store.name}”及其商品吗？`)) return
  try {
    await requestJson(`/merchant/stores/${store.id}`, { method: 'DELETE' })
    await loadData()
  } catch (reason) {
    error.value = reason instanceof Error ? reason.message : '删除店铺失败'
  }
}

async function saveProduct() {
  if (!productForm.merchantId || !productForm.sku.trim() || !productForm.name.trim() || !productForm.categoryL2) return
  saving.value = true
  error.value = ''
  try {
    const payload = {
      merchantId: productForm.merchantId,
      sku: productForm.sku.trim(),
      name: productForm.name.trim(),
      categoryL1: productForm.categoryL1 || selectedCategory.value?.categoryL1 || '',
      categoryL2: productForm.categoryL2,
      brand: productForm.brand.trim() || null,
      description: productForm.description.trim(),
      price: Number(productForm.price),
      stock: Number(productForm.stock),
      attributes: Object.fromEntries(Object.entries(productAttributes).filter(([, value]) => value !== '' && value !== null)),
      sellingPoints: productForm.sellingPoints.split(/[,，]/).map((item) => item.trim()).filter(Boolean),
      imageUrls: productForm.imageUrls.split(/\r?\n|[,，]/).map((item) => item.trim()).filter(Boolean),
      status: productForm.status,
    }
    if (editingProduct.value) await requestJson(`/merchant/products/${editingProduct.value.id}`, { method: 'PATCH', body: JSON.stringify(payload) })
    else await requestJson('/merchant/products', { method: 'POST', body: JSON.stringify(payload) })
    await loadData()
    resetProductForm()
    goTo('/catalog')
  } catch (reason) {
    error.value = reason instanceof Error ? reason.message : '保存商品失败'
  } finally {
    saving.value = false
  }
}

async function toggleSale(product: Product) {
  try {
    await requestJson(`/merchant/products/${product.id}`, {
      method: 'PATCH',
      body: JSON.stringify({ status: product.status === 'on_sale' ? 'off_sale' : 'on_sale' }),
    })
    await loadData()
  } catch (reason) {
    error.value = reason instanceof Error ? reason.message : '更新商品状态失败'
  }
}

async function deleteProduct(product: Product) {
  if (!window.confirm(`确认软删除“${product.name}”吗？`)) return
  try {
    await requestJson(`/merchant/products/${product.id}`, { method: 'DELETE' })
    await loadData()
  } catch (reason) {
    error.value = reason instanceof Error ? reason.message : '删除商品失败'
  }
}

function openProduct(product: Product) {
  selectedProduct.value = product
}

function closeProductDetails() {
  selectedProduct.value = null
}

async function startApp() {
  if (appStarted) return
  appStarted = true
  appReady.value = true
  await nextTick()
  routeFromHash()
  window.addEventListener('hashchange', routeFromHash)
  void loadData()
}

function signOut() {
  appReady.value = false
  clearAccessToken()
  window.location.reload()
}

onBeforeUnmount(() => window.removeEventListener('hashchange', routeFromHash))
</script>

<template>
  <LoginGate v-if="!appReady" required-role="merchant" workspace-name="商家工作台" @authenticated="startApp" />
  <AppShell
    v-else
    eyebrow="声选导购 · 商家端"
    title="声选商家"
    :description="pageDescription"
    :nav-items="navItems"
    :active-nav-href="activeNavHref"
    :hero-compact="true"
    action-label="退出登录"
    @action="signOut"
  >
    <template #headline>{{ pageHeadline }}</template>
    <template #hero-action>
      <div v-if="isCatalogPage" class="section-actions">
        <button class="primary-button" type="button" @click="openProductEditor()">新增商品</button>
        <button class="secondary-button" type="button" @click="openStoreEditor()">新增店铺</button>
      </div>
      <button v-else class="ghost-button" type="button" @click="goTo('/catalog')">返回工作台</button>
    </template>
    <template #hero-panel>
      <div class="hero-panel">
        <span class="hero-panel__label">当前账号数据边界</span>
        <div>
          <p class="hero-panel__value">{{ stores.length }} 家店 · {{ products.length }} 件商品</p>
          <p class="hero-panel__note">累计成交 ¥{{ formatPrice(revenue) }}；所有写操作都由后端按账号归属复核。</p>
        </div>
      </div>
    </template>

    <div class="workspace">
      <p v-if="error" class="error-banner">{{ error }}</p>

      <template v-if="isCatalogPage">
        <section class="section-panel">
          <div class="section-heading">
            <div><span class="section-kicker">商品管理</span><h2>全部商品</h2><p>先看完整商品清单，再按店铺和经营条件快速筛选。</p></div>
            <span class="section-count">{{ visibleProducts.length }} / {{ products.length }} 件</span>
          </div>
          <div class="filter-toolbar" aria-label="商品筛选">
            <label class="form-field filter-toolbar__search">搜索商品<input v-model="productQuery" class="input" placeholder="商品名、商品编码（SKU）或品牌" /></label>
            <label class="form-field">店铺<select v-model="productStoreFilter" class="select"><option value="">全部店铺</option><option v-for="store in stores" :key="store.id" :value="store.id">{{ store.name }}</option></select></label>
            <label class="form-field">状态<select v-model="productStatusFilter" class="select"><option value="">全部状态</option><option value="on_sale">在售</option><option value="draft">草稿</option><option value="off_sale">已下架</option></select></label>
            <label class="form-field">品类<select v-model="productCategoryFilter" class="select"><option value="">全部品类</option><option v-for="category in categories" :key="category.id" :value="category.categoryL2">{{ categoryLabel(category.categoryL2) }}</option></select></label>
            <label class="form-field">库存<select v-model="stockFilter" class="select"><option value="all">全部库存</option><option value="available">有库存</option><option value="low">低库存（≤10）</option><option value="out">缺货</option></select></label>
          </div>
          <p v-if="loading" class="empty-state">正在加载商品…</p>
          <p v-else-if="!visibleProducts.length" class="empty-state">没有符合条件的商品，试试放宽筛选条件。</p>
          <div v-else class="table-wrap">
            <table class="data-table">
              <thead><tr><th>商品</th><th>店铺</th><th>品类</th><th>价格</th><th>库存</th><th>状态</th><th>操作</th></tr></thead>
              <tbody>
                <tr v-for="product in visibleProducts" :key="product.id" class="product-row" tabindex="0" @click="openProduct(product)">
                  <td><strong>{{ product.name }}</strong><br /><span class="muted">{{ product.sku }} · {{ product.brand || '无品牌' }}</span></td>
                  <td>{{ product.merchantName }}</td><td>{{ categoryLabel(product.categoryL2) }}</td><td class="order-total">¥{{ formatPrice(product.price) }}</td><td>{{ product.stock }}</td>
                  <td><span class="badge" :class="{ 'badge--disabled': product.status !== 'on_sale' }">{{ product.status === 'on_sale' ? '在售' : product.status === 'draft' ? '草稿' : '已下架' }}</span></td>
                  <td><div class="section-actions"><button class="ghost-button small-button" type="button" @click.stop="openProductEditor(product)">编辑</button><button class="secondary-button small-button" type="button" @click.stop="toggleSale(product)">{{ product.status === 'on_sale' ? '下架' : '上架' }}</button><button class="danger-button small-button" type="button" @click.stop="deleteProduct(product)">删除</button></div></td>
                </tr>
              </tbody>
            </table>
          </div>
        </section>

        <section class="section-panel">
          <div class="section-heading"><div><span class="section-kicker">店铺管理</span><h2>我的店铺</h2><p>店铺是商品供给和订单边界，新增与修改都会在独立页面完成。</p></div><button class="secondary-button" type="button" @click="openStoreEditor()">新增店铺</button></div>
          <div v-if="!stores.length" class="empty-state">还没有店铺，先新增一家店铺。</div>
          <div v-else class="store-grid">
            <article v-for="store in stores" :key="store.id" class="store-card">
              <span class="badge" :class="{ 'badge--disabled': !store.isEnabled }">{{ store.isEnabled ? '营业中' : '已禁用' }}</span>
              <h3>{{ store.name }}</h3><p>{{ store.description || '暂未填写店铺简介。' }}</p>
              <div class="card-footer"><span class="muted">{{ store.productCount }} 件商品</span><div class="section-actions"><button class="ghost-button small-button" type="button" @click="openStoreEditor(store)">编辑店铺</button><button class="danger-button small-button" type="button" @click="removeStore(store)">删除</button></div></div>
            </article>
          </div>
        </section>
      </template>

      <section v-else-if="isOrdersPage" class="section-panel">
        <div class="section-heading"><div><span class="section-kicker">订单管理</span><h2>本店订单</h2><p>仅展示当前账号所拥有店铺的订单。</p></div><span class="badge badge--pending">{{ pendingOrders }} 笔待确认</span></div>
        <p v-if="!orders.length" class="empty-state">暂时还没有订单。</p>
        <div v-else class="table-wrap"><table class="data-table"><thead><tr><th>商品</th><th>金额</th><th>状态</th><th>失败原因</th><th>创建时间</th></tr></thead><tbody><tr v-for="order in orders" :key="order.id"><td><strong>{{ order.productSnapshot.name }}</strong></td><td class="order-total">¥{{ formatPrice(order.totalAmount) }}</td><td><span class="badge" :class="`badge--${order.status}`">{{ order.status === 'pending' ? '待确认' : order.status === 'success' ? '已完成' : '已取消' }}</span></td><td>{{ order.failureReason || '—' }}</td><td><time :datetime="order.createdAt">{{ formatDateTime(order.createdAt) }}</time></td></tr></tbody></table></div>
      </section>

      <section v-else-if="isStoreEditor" class="section-panel operation-page">
        <div class="operation-page__intro"><span class="section-kicker">店铺编辑</span><h2>{{ editingStore ? '编辑店铺' : '新增店铺' }}</h2><p>完善店铺信息后，商品和订单会自动归入该店铺。</p></div>
        <form class="form-grid operation-form" @submit.prevent="saveStore">
          <label class="form-field form-field--wide">店铺名称<input v-model="storeForm.name" class="input" required /></label>
          <label class="form-field">英文标识<input v-model="storeForm.slug" class="input" pattern="[a-z0-9]+(?:-[a-z0-9]+)*" required /></label>
          <label class="form-field">联系电话<input v-model="storeForm.contactPhone" class="input" /></label>
          <label class="form-field form-field--full">店铺简介<textarea v-model="storeForm.description" class="textarea" placeholder="让用户和智能导购快速理解你的店铺特色" /></label>
          <label class="form-field form-field--full">店铺标识图地址（Logo URL）<input v-model="storeForm.logoUrl" class="input" placeholder="例如：https://..." /></label>
          <div class="form-actions form-field--full"><button class="ghost-button" type="button" @click="goTo('/catalog')">取消</button><button class="primary-button" type="submit" :disabled="saving">{{ saving ? '保存中…' : editingStore ? '保存修改' : '创建店铺' }}</button></div>
        </form>
      </section>

      <section v-else-if="isProductEditor" class="section-panel operation-page">
        <div class="operation-page__intro"><span class="section-kicker">商品编辑</span><h2>{{ editingProduct ? '编辑商品' : '新增商品' }}</h2><p>商品信息会被智能导购用于检索、比较和推荐，请尽量填写完整。</p></div>
        <form class="form-grid operation-form" @submit.prevent="saveProduct">
          <label class="form-field">所属店铺<select v-model="productForm.merchantId" class="select" required><option value="" disabled>请选择店铺</option><option v-for="store in stores" :key="store.id" :value="store.id">{{ store.name }}</option></select></label>
          <label class="form-field">商品编码（SKU）<input v-model="productForm.sku" class="input" required /></label>
          <label class="form-field form-field--wide">商品名称<input v-model="productForm.name" class="input" required /></label>
          <label class="form-field">一级品类<input :value="categoryLabel(selectedCategory?.categoryL1 || productForm.categoryL1)" class="input" disabled /></label>
          <label class="form-field">二级品类<select v-model="productForm.categoryL2" class="select" required><option value="" disabled>请选择品类</option><option v-for="category in categories" :key="category.id" :value="category.categoryL2">{{ categoryLabel(category.categoryL1) }} / {{ categoryLabel(category.categoryL2) }}</option></select></label>
          <label class="form-field">品牌<input v-model="productForm.brand" class="input" /></label>
          <label class="form-field">状态<select v-model="productForm.status" class="select"><option value="draft">草稿</option><option value="on_sale">上架</option><option value="off_sale">下架</option></select></label>
          <label class="form-field">价格<input v-model.number="productForm.price" class="input" min="0" step="0.01" type="number" required /></label>
          <label class="form-field">库存<input v-model.number="productForm.stock" class="input" min="0" type="number" required /></label>
          <label class="form-field form-field--wide">卖点（逗号分隔）<input v-model="productForm.sellingPoints" class="input" /></label>
          <label class="form-field form-field--full">商品描述<textarea v-model="productForm.description" class="textarea" /></label>
          <label class="form-field form-field--full">图片地址（每行一个）<textarea v-model="productForm.imageUrls" class="textarea" placeholder="https://..." /></label>
          <div v-if="selectedSlots.length" class="slot-fields form-field--full"><div class="slot-fields__heading"><strong>{{ categoryLabel(selectedCategory?.categoryL2 || '') }} 商品槽位</strong><span class="muted">必填槽位会参与智能导购的需求澄清</span></div><div class="form-grid"><label v-for="slot in selectedSlots" :key="slot.key" class="form-field"><span>{{ formatCatalogAttributeLabel(slot.key) }} <b v-if="slot.isRequired" class="required-mark">必填</b><span v-else class="muted">选填</span></span><select v-model="productAttributes[slot.key]" class="select" :required="slot.isRequired"><option :value="null">请选择</option><option v-for="value in slot.enumValues" :key="String(value)" :value="value">{{ formatCatalogAttributeValue(value) }}</option></select></label></div></div>
          <div class="form-actions form-field--full"><button class="ghost-button" type="button" @click="goTo('/catalog')">取消</button><button class="primary-button" type="submit" :disabled="saving || !stores.length">{{ saving ? '保存中…' : editingProduct ? '保存修改' : '创建商品' }}</button></div>
        </form>
      </section>
    </div>
    <ProductDetailModal v-if="selectedProduct && isCatalogPage" :product="selectedProduct" @close="closeProductDetails" />
  </AppShell>
</template>
