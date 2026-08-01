<script setup lang="ts">
import {
  AppShell,
  requestJson,
  type ItemsResponse,
  type Merchant,
  type Order,
  type Product,
} from '@voice-shopping/web-ui'
import { computed, onMounted, reactive, ref } from 'vue'

const navItems = [
  { label: '经营概览', href: '#overview' },
  { label: '我的店铺', href: '#stores' },
  { label: '商品管理', href: '#products' },
  { label: '本店订单', href: '#orders' },
]
const stores = ref<Merchant[]>([])
const products = ref<Product[]>([])
const orders = ref<Order[]>([])
const loading = ref(true)
const saving = ref(false)
const error = ref('')
const storeForm = reactive({ name: '', slug: '', description: '' })
const productForm = reactive({
  merchantId: '',
  sku: '',
  name: '',
  categoryL1: 'ELECTRONICS',
  categoryL2: 'HEADPHONES',
  brand: '',
  description: '',
  price: 0,
  stock: 0,
  sellingPoints: '',
  status: 'draft' as Product['status'],
})

const pendingOrders = computed(() => orders.value.filter((item) => item.status === 'pending').length)
const revenue = computed(() =>
  orders.value.filter((item) => item.status === 'success').reduce((sum, item) => sum + Number(item.totalAmount), 0),
)

async function loadData() {
  loading.value = true
  error.value = ''
  try {
    const [storeData, productData, orderData] = await Promise.all([
      requestJson<ItemsResponse<Merchant>>('/merchant/stores'),
      requestJson<ItemsResponse<Product>>('/merchant/products'),
      requestJson<ItemsResponse<Order>>('/merchant/orders'),
    ])
    stores.value = storeData.items
    products.value = productData.items
    orders.value = orderData.items
    if (!productForm.merchantId && stores.value[0]) productForm.merchantId = stores.value[0].id
  } catch (reason) {
    error.value = reason instanceof Error ? reason.message : '数据加载失败'
  } finally {
    loading.value = false
  }
}

async function createStore() {
  if (!storeForm.name || !storeForm.slug) return
  saving.value = true
  try {
    await requestJson<Merchant>('/merchant/stores', {
      method: 'POST',
      body: JSON.stringify(storeForm),
    })
    Object.assign(storeForm, { name: '', slug: '', description: '' })
    await loadData()
  } catch (reason) {
    error.value = reason instanceof Error ? reason.message : '创建失败'
  } finally {
    saving.value = false
  }
}

async function deleteStore(store: Merchant) {
  if (!window.confirm(`确认软删除店铺“${store.name}”及其商品吗？`)) return
  await requestJson(`/merchant/stores/${store.id}`, { method: 'DELETE' })
  await loadData()
}

async function createProduct() {
  if (!productForm.merchantId || !productForm.sku || !productForm.name) return
  saving.value = true
  try {
    await requestJson<Product>('/merchant/products', {
      method: 'POST',
      body: JSON.stringify({
        ...productForm,
        attributes: {},
        sellingPoints: productForm.sellingPoints.split('，').map((item) => item.trim()).filter(Boolean),
        imageUrls: [],
      }),
    })
    Object.assign(productForm, {
      sku: '', name: '', brand: '', description: '', price: 0, stock: 0, sellingPoints: '', status: 'draft',
    })
    await loadData()
  } catch (reason) {
    error.value = reason instanceof Error ? reason.message : '创建失败'
  } finally {
    saving.value = false
  }
}

async function toggleSale(product: Product) {
  const status: Product['status'] = product.status === 'on_sale' ? 'off_sale' : 'on_sale'
  await requestJson<Product>(`/merchant/products/${product.id}`, {
    method: 'PATCH',
    body: JSON.stringify({ status }),
  })
  await loadData()
}

async function editInventory(product: Product) {
  const priceInput = window.prompt('新价格', String(product.price))
  if (priceInput === null) return
  const stockInput = window.prompt('新库存', String(product.stock))
  if (stockInput === null) return
  await requestJson<Product>(`/merchant/products/${product.id}`, {
    method: 'PATCH',
    body: JSON.stringify({ price: Number(priceInput), stock: Number(stockInput) }),
  })
  await loadData()
}

async function deleteProduct(product: Product) {
  if (!window.confirm(`确认软删除“${product.name}”吗？`)) return
  await requestJson(`/merchant/products/${product.id}`, { method: 'DELETE' })
  await loadData()
}

onMounted(() => void loadData())
</script>

<template>
  <AppShell
    eyebrow="VOICE COMMERCE · MERCHANT"
    title="声选商家"
    description="维护自己的店铺、商品、价格和库存，只查看由当前商家供给产生的订单。"
    :nav-items="navItems"
    action-label="声动数码店主"
  >
    <template #headline>让好商品，<br />更容易被听见。</template>
    <template #hero-action><a class="primary-button" href="#products">管理商品</a></template>
    <template #hero-panel>
      <div class="hero-panel"><span class="hero-panel__label">当前账号数据边界</span><div><p class="hero-panel__value">{{ stores.length }} 家店铺</p><p class="hero-panel__note">所有写操作都由后端按 ownerUserId 复核，不可访问其他商家的供给。</p></div></div>
    </template>

    <div class="workspace">
      <p v-if="error" class="error-banner">{{ error }}</p>
      <section id="overview" class="stat-grid">
        <article class="stat-card"><span class="stat-label">店铺</span><span class="stat-value">{{ stores.length }}</span></article>
        <article class="stat-card"><span class="stat-label">商品</span><span class="stat-value">{{ products.length }}</span></article>
        <article class="stat-card"><span class="stat-label">待确认订单</span><span class="stat-value">{{ pendingOrders }}</span></article>
        <article class="stat-card"><span class="stat-label">成交金额</span><span class="stat-value">¥{{ revenue }}</span></article>
      </section>

      <section id="stores" class="section-panel">
        <div class="section-heading"><div><h2>我的店铺</h2><p>创建、查看和软删除当前账号拥有的店铺。</p></div></div>
        <form class="form-grid" @submit.prevent="createStore">
          <label class="form-field">店铺名称<input v-model="storeForm.name" class="input" required /></label>
          <label class="form-field">英文标识<input v-model="storeForm.slug" class="input" pattern="[a-z0-9]+(?:-[a-z0-9]+)*" required /></label>
          <label class="form-field form-field--wide">简介<input v-model="storeForm.description" class="input" /></label>
          <button class="primary-button" type="submit" :disabled="saving">新增店铺</button>
        </form>
        <div class="store-grid" style="margin-top: 18px">
          <article v-for="store in stores" :key="store.id" class="store-card">
            <span class="badge" :class="{ 'badge--disabled': !store.isEnabled }">{{ store.isEnabled ? '营业中' : '已禁用' }}</span>
            <h3>{{ store.name }}</h3><p>{{ store.description }}</p>
            <div class="card-footer"><span class="muted">{{ store.productCount }} 件商品</span><button class="danger-button small-button" @click="deleteStore(store)">软删除</button></div>
          </article>
        </div>
      </section>

      <section id="products" class="section-panel">
        <div class="section-heading"><div><h2>商品与库存</h2><p>维护供 Agent 检索的商品事实，上架后才会出现在用户端。</p></div></div>
        <form class="form-grid" @submit.prevent="createProduct">
          <label class="form-field">所属店铺<select v-model="productForm.merchantId" class="select" required><option v-for="store in stores" :key="store.id" :value="store.id">{{ store.name }}</option></select></label>
          <label class="form-field">SKU<input v-model="productForm.sku" class="input" required /></label>
          <label class="form-field form-field--wide">商品名<input v-model="productForm.name" class="input" required /></label>
          <label class="form-field">一级品类<input v-model="productForm.categoryL1" class="input" required /></label>
          <label class="form-field">标准品类<input v-model="productForm.categoryL2" class="input" required /></label>
          <label class="form-field">品牌<input v-model="productForm.brand" class="input" /></label>
          <label class="form-field">状态<select v-model="productForm.status" class="select"><option value="draft">草稿</option><option value="on_sale">上架</option><option value="off_sale">下架</option></select></label>
          <label class="form-field">价格<input v-model.number="productForm.price" class="input" min="0" step="0.01" type="number" required /></label>
          <label class="form-field">库存<input v-model.number="productForm.stock" class="input" min="0" type="number" required /></label>
          <label class="form-field form-field--wide">卖点（逗号分隔）<input v-model="productForm.sellingPoints" class="input" /></label>
          <label class="form-field form-field--wide">描述<input v-model="productForm.description" class="input" /></label>
          <button class="primary-button" type="submit" :disabled="saving || !stores.length">新增商品</button>
        </form>
        <p v-if="loading" class="empty-state">正在加载…</p>
        <div v-else class="table-wrap" style="margin-top: 20px">
          <table class="data-table">
            <thead><tr><th>商品</th><th>店铺</th><th>价格</th><th>库存</th><th>状态</th><th>操作</th></tr></thead>
            <tbody><tr v-for="product in products" :key="product.id"><td><strong>{{ product.name }}</strong><br><span class="muted">{{ product.sku }}</span></td><td>{{ product.merchantName }}</td><td>¥{{ product.price }}</td><td>{{ product.stock }}</td><td><span class="badge" :class="{ 'badge--disabled': product.status !== 'on_sale' }">{{ product.status }}</span></td><td><div class="section-actions"><button class="ghost-button small-button" @click="editInventory(product)">价格/库存</button><button class="secondary-button small-button" @click="toggleSale(product)">{{ product.status === 'on_sale' ? '下架' : '上架' }}</button><button class="danger-button small-button" @click="deleteProduct(product)">删除</button></div></td></tr></tbody>
          </table>
        </div>
      </section>

      <section id="orders" class="section-panel">
        <div class="section-heading"><div><h2>本店订单</h2><p>仅展示当前账号所拥有店铺的订单。</p></div></div>
        <div class="table-wrap"><table class="data-table"><thead><tr><th>商品</th><th>金额</th><th>状态</th><th>失败原因</th><th>时间</th></tr></thead><tbody><tr v-for="order in orders" :key="order.id"><td>{{ order.productSnapshot.name }}</td><td>¥{{ order.totalAmount }}</td><td><span class="badge" :class="`badge--${order.status}`">{{ order.status }}</span></td><td>{{ order.failureReason || '—' }}</td><td>{{ new Date(order.createdAt).toLocaleString() }}</td></tr></tbody></table></div>
      </section>
    </div>
  </AppShell>
</template>
