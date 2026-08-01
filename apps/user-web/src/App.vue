<script setup lang="ts">
import {
  AppShell,
  audioWsBaseUrl,
  requestJson,
  textWsBaseUrl,
  type ApiEvent,
  type ItemsResponse,
  type Merchant,
  type Order,
  type Product,
} from '@voice-shopping/web-ui'
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'

interface RecommendationCard {
  productId: string
  merchantId: string
  merchantName?: string
  name: string
  brand?: string
  price: number
  stock: number
  imageUrl?: string
  sellingPoints: string[]
  reason?: string
  matchScore: number
}

interface ChatMessage {
  role: 'user' | 'assistant'
  text: string
}

interface SpeechResultEvent {
  results: { [index: number]: { [index: number]: { transcript: string } } }
}

interface SpeechRecognitionLike {
  lang: string
  interimResults: boolean
  onresult: ((event: SpeechResultEvent) => void) | null
  onerror: (() => void) | null
  start: () => void
  stop: () => void
}

const navItems = [
  { label: '语音导购', href: '#voice' },
  { label: '逛商品', href: '#products' },
  { label: '我的订单', href: '#orders' },
]
const customerId = '00000000-0000-4000-8000-000000000101'
const sessionId = localStorage.getItem('voice-shopping-session') ?? crypto.randomUUID()
localStorage.setItem('voice-shopping-session', sessionId)

const merchants = ref<Merchant[]>([])
const products = ref<Product[]>([])
const orders = ref<Order[]>([])
const recommendations = ref<RecommendationCard[]>([])
const selectedCategory = ref('')
const utterance = ref('我想买一副通勤用的降噪耳机，预算一千元以内')
const loading = ref(true)
const error = ref('')
const flowStatus = ref('正在连接导购…')
const messages = ref<ChatMessage[]>([
  { role: 'assistant', text: '你好，我是声选导购。告诉我想买什么，我会一次只问一个必要问题。' },
])
const isRecording = ref(false)
let textSocket: WebSocket | null = null
let audioSocket: WebSocket | null = null
let recognition: SpeechRecognitionLike | null = null
let mediaStream: MediaStream | null = null
let audioContext: AudioContext | null = null
let audioSource: MediaStreamAudioSourceNode | null = null
let audioProcessor: ScriptProcessorNode | null = null
let audioChunks: Blob[] = []
let audioFallbackActive = false
let locallySubmittedTranscript = ''
const pendingSpeechByTurn = new Map<string, string>()

const categories = computed(() => [...new Set(products.value.map((item) => item.categoryL2))])
const visibleProducts = computed(() =>
  selectedCategory.value
    ? products.value.filter((item) => item.categoryL2 === selectedCategory.value)
    : products.value.slice(0, 12),
)
const successfulOrders = computed(() => orders.value.filter((order) => order.status === 'success').length)

async function loadData() {
  loading.value = true
  error.value = ''
  try {
    const [merchantData, productData, orderData] = await Promise.all([
      requestJson<ItemsResponse<Merchant>>('/catalog/merchants'),
      requestJson<ItemsResponse<Product>>('/catalog/products'),
      requestJson<ItemsResponse<Order>>('/orders/mine', { headers: { 'X-User-ID': customerId } }),
    ])
    merchants.value = merchantData.items
    products.value = productData.items
    orders.value = orderData.items
  } catch (reason) {
    error.value = reason instanceof Error ? reason.message : '加载失败'
  } finally {
    loading.value = false
  }
}

function speak(text: string) {
  if (!('speechSynthesis' in window) || !text) return
  window.speechSynthesis.cancel()
  const speech = new SpeechSynthesisUtterance(text)
  speech.lang = 'zh-CN'
  window.speechSynthesis.speak(speech)
}

function handleEvent(event: ApiEvent<Record<string, unknown>>) {
  if (event.type === 'flow.status') {
    const status = String(event.payload.status ?? '')
    flowStatus.value = status === 'processing' ? 'Agent 正在理解与筛选…' : '可以继续说'
  }
  if (event.type === 'recommendation.cards') {
    const cards = event.payload.productCards as RecommendationCard[]
    recommendations.value = cards.map((card) => ({ ...card, reason: '' }))
  }
  if (event.type === 'text.delta' && event.payload.scope === 'reason') {
    const card = recommendations.value.find((item) => item.productId === event.payload.productId)
    if (card) card.reason = `${card.reason ?? ''}${String(event.payload.delta ?? '')}`
  }
  if (event.type === 'text.completed') {
    const text = String(event.payload.text ?? '')
    messages.value.push({ role: 'assistant', text })
    if (audioSocket?.readyState === WebSocket.OPEN) {
      pendingSpeechByTurn.set(event.turnId, text)
    } else {
      speak(text)
    }
  }
  if (event.type === 'order.updated') void loadData()
}

function connectText(): Promise<void> {
  if (textSocket?.readyState === WebSocket.OPEN) return Promise.resolve()
  return new Promise((resolve, reject) => {
    textSocket = new WebSocket(`${textWsBaseUrl}/${sessionId}?userId=${customerId}`)
    textSocket.onopen = () => {
      flowStatus.value = '导购已就绪'
      resolve()
    }
    textSocket.onerror = () => reject(new Error('文本连接失败'))
    textSocket.onclose = () => {
      flowStatus.value = '连接已断开，发送时会自动重连'
    }
    textSocket.onmessage = (message) => {
      const event = JSON.parse(String(message.data)) as ApiEvent<Record<string, unknown>>
      if (event.type !== 'session.connected') handleEvent(event)
    }
  })
}

function connectAudio(): Promise<void> {
  if (audioSocket?.readyState === WebSocket.OPEN) return Promise.resolve()
  return new Promise((resolve, reject) => {
    audioSocket = new WebSocket(`${audioWsBaseUrl}/${sessionId}?userId=${customerId}`)
    audioSocket.binaryType = 'blob'
    audioSocket.onopen = () => resolve()
    audioSocket.onerror = () => reject(new Error('音频连接失败'))
    audioSocket.onclose = () => {
      for (const text of pendingSpeechByTurn.values()) speak(text)
      pendingSpeechByTurn.clear()
      audioChunks = []
      audioFallbackActive = false
    }
    audioSocket.onmessage = (message) => {
      if (message.data instanceof Blob) {
        if (!audioFallbackActive) audioChunks.push(message.data)
        return
      }
      const event = JSON.parse(String(message.data)) as {
        type: string
        turnId?: string
        payload?: Record<string, unknown>
      }
      if (event.type === 'asr.completed') {
        const transcript = String(event.payload?.transcript ?? '')
        if (transcript && transcript !== locallySubmittedTranscript) messages.value.push({ role: 'user', text: transcript })
      }
      if (event.type === 'audio.start') {
        audioChunks = []
        audioFallbackActive = event.payload?.fallback === true
        const pendingText = event.turnId ? pendingSpeechByTurn.get(event.turnId) : undefined
        if (event.turnId) pendingSpeechByTurn.delete(event.turnId)
        if (audioFallbackActive) {
          speak(String(event.payload?.text ?? pendingText ?? ''))
        } else if ('speechSynthesis' in window) {
          window.speechSynthesis.cancel()
        }
      }
      if (event.type === 'audio.end') {
        if (!audioFallbackActive && audioChunks.length) {
          const url = URL.createObjectURL(new Blob(audioChunks, { type: 'audio/wav' }))
          const audio = new Audio(url)
          audio.onended = () => URL.revokeObjectURL(url)
          void audio.play().catch(() => URL.revokeObjectURL(url))
        }
        audioChunks = []
        audioFallbackActive = false
      }
    }
  })
}

function encodePcm16(input: Float32Array, sourceRate: number): ArrayBuffer {
  const ratio = sourceRate / 16000
  const size = Math.max(1, Math.floor(input.length / ratio))
  const buffer = new ArrayBuffer(size * 2)
  const view = new DataView(buffer)
  for (let index = 0; index < size; index += 1) {
    const sample = Math.max(-1, Math.min(1, input[Math.floor(index * ratio)] ?? 0))
    view.setInt16(index * 2, sample < 0 ? sample * 0x8000 : sample * 0x7fff, true)
  }
  return buffer
}

async function sendUtterance() {
  const text = utterance.value.trim()
  if (!text) return
  error.value = ''
  try {
    await connectText()
    const turnId = crypto.randomUUID()
    messages.value.push({ role: 'user', text })
    recommendations.value = []
    textSocket?.send(JSON.stringify({ type: 'turn.submit', turnId, utterance: text }))
    utterance.value = ''
  } catch (reason) {
    error.value = reason instanceof Error ? reason.message : '发送失败'
  }
}

async function startVoice() {
  error.value = ''
  try {
    await connectAudio()
    mediaStream = await navigator.mediaDevices.getUserMedia({ audio: true })
    audioContext = new AudioContext()
    audioSource = audioContext.createMediaStreamSource(mediaStream)
    audioProcessor = audioContext.createScriptProcessor(4096, 1, 1)
    audioProcessor.onaudioprocess = (event) => {
      if (audioSocket?.readyState !== WebSocket.OPEN || !audioContext) return
      audioSocket.send(encodePcm16(event.inputBuffer.getChannelData(0), audioContext.sampleRate))
    }
    audioSource.connect(audioProcessor)
    audioProcessor.connect(audioContext.destination)
    const recognitionConstructor = (
      window as Window & { webkitSpeechRecognition?: new () => SpeechRecognitionLike }
    ).webkitSpeechRecognition
    if (recognitionConstructor) {
      recognition = new recognitionConstructor()
      recognition.lang = 'zh-CN'
      recognition.interimResults = false
      recognition.onresult = (event) => {
        utterance.value = event.results[0][0].transcript
      }
      recognition.onerror = () => {
        flowStatus.value = '云端语音识别仍在继续…'
      }
      recognition.start()
    }
    audioSocket?.send(JSON.stringify({ type: 'audio.start', turnId: 'capturing' }))
    isRecording.value = true
    flowStatus.value = '正在聆听…'
  } catch (reason) {
    error.value = reason instanceof Error ? reason.message : '无法使用麦克风'
    mediaStream?.getTracks().forEach((track) => track.stop())
  }
}

function stopVoice() {
  recognition?.stop()
  audioProcessor?.disconnect()
  audioSource?.disconnect()
  void audioContext?.close()
  audioProcessor = null
  audioSource = null
  audioContext = null
  mediaStream?.getTracks().forEach((track) => track.stop())
  isRecording.value = false
  window.setTimeout(() => {
    const transcript = utterance.value.trim()
    if (audioSocket?.readyState !== WebSocket.OPEN) return
    const turnId = crypto.randomUUID()
    locallySubmittedTranscript = transcript
    if (transcript) messages.value.push({ role: 'user', text: transcript })
    audioSocket.send(JSON.stringify({ type: 'audio.commit', turnId, transcript }))
    utterance.value = ''
  }, 450)
}

async function reportClick(productId: string) {
  await requestJson('/catalog/behaviors', {
    method: 'POST',
    headers: { 'X-User-ID': customerId },
    body: JSON.stringify({ productId, eventType: 'click' }),
  }).catch(() => undefined)
}

async function buyProduct(productId: string) {
  error.value = ''
  try {
    await requestJson<Order>('/orders', {
      method: 'POST',
      headers: { 'X-User-ID': customerId },
      body: JSON.stringify({
        productId,
        quantity: 1,
        idempotencyKey: `web-${sessionId}-${crypto.randomUUID()}`,
      }),
    })
    await loadData()
    document.querySelector('#orders')?.scrollIntoView({ behavior: 'smooth' })
  } catch (reason) {
    error.value = reason instanceof Error ? reason.message : '创建订单失败'
  }
}

async function updateOrder(order: Order, action: 'confirm' | 'cancel') {
  try {
    await requestJson<Order>(`/orders/${order.id}/${action}`, {
      method: 'POST',
      headers: { 'X-User-ID': customerId },
    })
    await loadData()
  } catch (reason) {
    error.value = reason instanceof Error ? reason.message : '订单操作失败'
  }
}

onMounted(() => {
  void Promise.all([loadData(), connectText(), connectAudio()]).catch(() => undefined)
})
onBeforeUnmount(() => {
  textSocket?.close()
  audioSocket?.close()
  mediaStream?.getTracks().forEach((track) => track.stop())
})
</script>

<template>
  <AppShell
    eyebrow="VOICE COMMERCE · USER"
    title="声选"
    description="说出预算、场景和偏好，四个 Agent 会逐步澄清、精排商品并协助确认下单。"
    :nav-items="navItems"
    action-label="小林的账户"
  >
    <template #headline>开口说需求，<br />轻松买到对的。</template>
    <template #hero-action>
      <a class="primary-button" href="#voice">开始语音导购</a>
    </template>
    <template #hero-panel>
      <div class="hero-panel">
        <span class="hero-panel__label">实时供给概览</span>
        <div>
          <p class="hero-panel__value">{{ merchants.length }} 家店 · {{ products.length }} 件商品</p>
          <p class="hero-panel__note">已完成 {{ successfulOrders }} 笔订单，点击和成交会持续更新你的偏好画像。</p>
        </div>
      </div>
    </template>

    <div class="workspace">
      <p v-if="error" class="error-banner">{{ error }}</p>

      <section id="voice" class="section-panel voice-console">
        <div class="voice-controls">
          <p class="eyebrow">LIVE SHOPPING AGENT</p>
          <h2>把需求说给我听</h2>
          <p style="color: rgba(255,255,255,.68); line-height: 1.7">支持推荐、对比、查询和二次确认下单；每次只追问一个缺失条件。</p>
          <button
            class="mic-button"
            :class="{ 'mic-button--active': isRecording }"
            type="button"
            :aria-label="isRecording ? '停止录音' : '开始录音'"
            @click="isRecording ? stopVoice() : startVoice()"
          >{{ isRecording ? '■' : '●' }}</button>
          <div class="voice-status"><span class="status-dot"></span>{{ flowStatus }}</div>
          <div class="voice-input-row">
            <input v-model="utterance" class="input" aria-label="导购消息" @keyup.enter="sendUtterance" />
            <button class="primary-button" type="button" @click="sendUtterance">发送</button>
          </div>
        </div>
        <div class="conversation" aria-live="polite">
          <div v-for="(message, index) in messages" :key="index" class="message" :class="`message--${message.role}`">
            {{ message.text }}
          </div>
        </div>
      </section>

      <section v-if="recommendations.length" class="section-panel">
        <div class="section-heading"><div><h2>为你精排的商品</h2><p>商品卡先展示，推荐理由随后按商品流式填充。</p></div></div>
        <div class="product-grid">
          <article v-for="card in recommendations" :key="card.productId" class="product-card">
            <div class="product-visual">{{ card.name.slice(0, 1) }}</div>
            <div class="product-meta"><span class="badge">匹配 {{ Math.round(card.matchScore * 100) }}%</span><span class="muted">{{ card.merchantName }}</span></div>
            <h3>{{ card.name }}</h3>
            <p class="reason">{{ card.reason || '正在生成专属推荐理由…' }}</p>
            <div class="card-footer"><span class="price">¥{{ card.price }}</span><button class="primary-button small-button" @click="buyProduct(card.productId)">生成待确认订单</button></div>
          </article>
        </div>
      </section>

      <section id="products" class="section-panel">
        <div class="section-heading">
          <div><h2>在售商品</h2><p>只展示启用店铺中有库存的商品。</p></div>
          <select v-model="selectedCategory" class="select" style="width: auto"><option value="">精选品类</option><option v-for="category in categories" :key="category">{{ category }}</option></select>
        </div>
        <p v-if="loading" class="empty-state">正在加载商品…</p>
        <div v-else class="product-grid">
          <article v-for="product in visibleProducts" :key="product.id" class="product-card" @click="reportClick(product.id)">
            <div class="product-visual">{{ product.name.slice(0, 1) }}</div>
            <span class="badge">{{ product.categoryL2 }}</span>
            <h3>{{ product.name }}</h3>
            <p>{{ product.description }}</p>
            <div class="card-footer"><span class="price">¥{{ product.price }}</span><button class="secondary-button small-button" @click.stop="buyProduct(product.id)">购买</button></div>
          </article>
        </div>
      </section>

      <section id="orders" class="section-panel">
        <div class="section-heading"><div><h2>我的订单</h2><p>待确认订单将在十五分钟后失效。</p></div></div>
        <div class="table-wrap">
          <table class="data-table">
            <thead><tr><th>商品</th><th>店铺</th><th>金额</th><th>状态</th><th>创建时间</th><th>操作</th></tr></thead>
            <tbody>
              <tr v-for="order in orders" :key="order.id">
                <td>{{ order.productSnapshot.name }}</td><td>{{ order.merchantSnapshot.name }}</td><td>¥{{ order.totalAmount }}</td>
                <td><span class="badge" :class="`badge--${order.status}`">{{ order.status }}</span></td>
                <td>{{ new Date(order.createdAt).toLocaleString() }}</td>
                <td><div v-if="order.status === 'pending'" class="section-actions"><button class="secondary-button small-button" @click="updateOrder(order, 'confirm')">确认</button><button class="danger-button small-button" @click="updateOrder(order, 'cancel')">取消</button></div><span v-else class="muted">—</span></td>
              </tr>
            </tbody>
          </table>
        </div>
      </section>
    </div>
  </AppShell>
</template>
