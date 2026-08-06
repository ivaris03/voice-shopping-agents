<script setup lang="ts">
import {
  AppShell,
  LoginGate,
  ProductDetailModal,
  audioWsBaseUrl,
  clearAccessToken,
  formatCategoryLabel,
  getAccessToken,
  merchantWebUrl,
  platformWebUrl,
  requestJson,
  textWsBaseUrl,
  type ApiEvent,
  type Category,
  type ItemsResponse,
  type Merchant,
  type Order,
  type Product,
} from '@voice-shopping/web-ui'
import { computed, nextTick, onBeforeUnmount, ref, watch } from 'vue'
import SupportedCategoriesModal from './SupportedCategoriesModal.vue'

interface RecommendationCard {
  productId: string
  merchantId: string
  merchantName?: string
  sku?: string
  name: string
  categoryL1?: string
  categoryL2?: string
  brand?: string
  description?: string
  price: number
  stock: number
  imageUrl?: string
  imageUrls?: string[]
  status?: Product['status']
  createdAt?: string
  updatedAt?: string
  sellingPoints: string[]
  attributes?: Product['attributes']
  reason?: string
  reasonIsFallback?: boolean
  matchScore: number
}

interface ChatMessage {
  role: 'user' | 'assistant'
  text: string
  turnId?: string
  streaming?: boolean
}

interface PendingAsrStart {
  turnId: string
  resolve: () => void
  reject: (reason: Error) => void
  timer: number
}

interface IncomingAudioSegment {
  turnId: string
  text: string
  fallback: boolean
  suppressed: boolean
  chunks: Blob[]
}

interface QueuedAudioSegment {
  turnId: string
  text: string
  fallback: boolean
  audio?: Blob
}

interface AudioInputOption {
  deviceId: string
  label: string
  labelKnown: boolean
}

const navItems = [
  { label: '语音导购', href: '#/voice' },
  { label: '逛商品', href: '#/browse' },
  { label: '我的订单', href: '#/orders' },
]
const workspaceLinks = [
  {
    label: '商家工作台',
    description: '管理店铺、商品和库存',
    href: merchantWebUrl,
  },
  {
    label: '平台控制台',
    description: '维护品类、商家和全量订单',
    href: platformWebUrl,
  },
]
const appReady = ref(false)
let appStarted = false
// A page close explicitly finalizes the session, so a new page instance must
// never reuse that terminal session ID.
const sessionId = crypto.randomUUID()
const REALTIME_CONNECT_TIMEOUT_MS = 8_000
const TURN_TIMEOUT_MS = 60_000

const currentRoute = ref('/voice')
const isVoicePage = computed(() => currentRoute.value === '/voice')
const isBrowsePage = computed(() => currentRoute.value === '/browse')
const isOrdersPage = computed(() => currentRoute.value === '/orders')
const activeNavHref = computed(() => `#${currentRoute.value}`)
const pageEyebrow = computed(() => {
  if (isBrowsePage.value) return '声选导购 · 商品浏览'
  if (isOrdersPage.value) return '声选导购 · 我的订单'
  return '声选导购 · 用户端'
})
const pageHeadline = computed(() => {
  if (isBrowsePage.value) return '慢慢逛，也能快速找到对的。'
  if (isOrdersPage.value) return '每一笔订单，都清清楚楚。'
  return '开口说需求，轻松买到对的。'
})
const pageDescription = computed(() => {
  if (isBrowsePage.value) return '按品类浏览当前在售商品，打开详情后可以直接生成待确认订单。'
  if (isOrdersPage.value) return '在这里确认待处理订单，并回看每一次购买记录。'
  return '说出预算、场景和偏好，导购会帮你澄清需求、推荐商品并确认下单。'
})

function scrollPageToTop(behavior: ScrollBehavior) {
  if (navigator.userAgent.includes('jsdom')) return
  window.scrollTo({ top: 0, behavior })
}

function syncRoute() {
  const route = window.location.hash.replace(/^#/, '') || '/voice'
  if (!['/voice', '/browse', '/orders'].includes(route)) {
    window.location.hash = '#/voice'
    return
  }
  currentRoute.value = route
  scrollPageToTop('auto')
}

function goTo(route: '/voice' | '/browse' | '/orders') {
  if (currentRoute.value === route) {
    scrollPageToTop('smooth')
    return
  }
  window.location.hash = `#${route}`
}

const merchants = ref<Merchant[]>([])
const products = ref<Product[]>([])
const orders = ref<Order[]>([])
const recommendations = ref<RecommendationCard[]>([])
const selectedProduct = ref<Product | null>(null)
const supportedCategories = ref<Category[]>([])
const isSupportedCategoriesDialogOpen = ref(false)
const orderRequestsInFlight = ref(new Set<string>())
const selectedCategory = ref('')
const audioInputs = ref<AudioInputOption[]>([])
const selectedAudioInputId = ref(localStorage.getItem('voice-shopping-audio-input') ?? '')
const activeAudioInputLabel = ref('')
const utterance = ref('')
const isTurnInFlight = ref(false)
const loading = ref(true)
const error = ref('')
const flowStatus = ref('正在连接导购…')
const messages = ref<ChatMessage[]>([
  { role: 'assistant', text: '你好，我是声选导购。告诉我想买什么，我每次会问一到两个必要问题。' },
])
const conversationElement = ref<HTMLElement | null>(null)
let conversationScrollPending = false

function scrollConversationToBottom() {
  if (conversationScrollPending) return
  conversationScrollPending = true
  void nextTick(() => {
    conversationScrollPending = false
    const element = conversationElement.value
    if (!element) return
    element.scrollTop = element.scrollHeight
  })
}

watch(
  messages,
  () => scrollConversationToBottom(),
  { deep: true, flush: 'post' },
)

const isRecording = ref(false)
const isVoiceStarting = ref(false)
const isAssistantSpeaking = ref(false)
const isAssistantSpeechPaused = ref(false)
let textSocket: WebSocket | null = null
let audioSocket: WebSocket | null = null
let textConnectPromise: Promise<void> | null = null
let audioConnectPromise: Promise<void> | null = null
let mediaStream: MediaStream | null = null
let audioContext: AudioContext | null = null
let audioSource: MediaStreamAudioSourceNode | null = null
let audioProcessor: ScriptProcessorNode | null = null
let audioGain: GainNode | null = null
let activeAssistantAudio: HTMLAudioElement | null = null
let activeAssistantAudioUrl: string | null = null
let activeAssistantSpeech: SpeechSynthesisUtterance | null = null
let incomingAudioSegment: IncomingAudioSegment | null = null
const audioQueue: QueuedAudioSegment[] = []
let capturedAudioBytes = 0
let capturedAudioPeak = 0
let recordingStartedAt = 0
let lastAudioLevelUpdateAt = 0
let sessionCloseSent = false
let recordingTurnId = ''
let asrReady = false
let stopRequested = false
let pendingPcmFrames: ArrayBuffer[] = []
let pendingAsrStart: PendingAsrStart | null = null
const pendingSpeechByTurn = new Map<string, string>()
const mutedSpeechTurnIds = new Set<string>()
const recommendationReasonsByTurn = new Map<string, Map<string, string>>()
let recommendationTurnId = ''
let isBargingIn = false
let latestVoiceTurnId = ''
const orderIdempotencyKeys = new Map<string, string>()
let activeTurnId = ''
let activeTurnSource: 'text' | 'voice' | null = null
let activeTurnTimeout = 0
let retryableTextTurn: { id: string; text: string } | null = null

const categories = computed(() => [...new Set(products.value.map((item) => item.categoryL2))])
const visibleProducts = computed(() =>
  selectedCategory.value
    ? products.value.filter((item) => item.categoryL2 === selectedCategory.value)
    : products.value,
)
const successfulOrders = computed(() => orders.value.filter((order) => order.status === 'success').length)
const pendingOrders = computed(() => orders.value.filter((order) => order.status === 'pending').length)

const orderStatusLabels: Record<Order['status'], string> = {
  pending: '待确认',
  success: '已完成',
  fail: '已取消',
}
const quickPrompts = ['通勤降噪耳机，预算一千以内', '适合日常跑步的鞋', '送给朋友的口红']
const workflowNodeLabels: Record<string, string> = {
  intent_agent: '意图识别正在进行',
  clarification_agent: '需求澄清正在进行',
  recommendation_agent: '商品召回与推荐正在进行',
  order_node: '订单处理节点运行中',
  emotional_agent: '回复生成正在进行',
  compliance_node: '合规检查与安全发布中',
}

function categoryLabel(value: string) {
  return formatCategoryLabel(value)
}

function orderStatusLabel(value: Order['status']) {
  return orderStatusLabels[value]
}

function isOrderRequestInFlight(productId: string) {
  return orderRequestsInFlight.value.has(productId)
}

function setOrderRequestInFlight(productId: string, inFlight: boolean) {
  const next = new Set(orderRequestsInFlight.value)
  if (inFlight) next.add(productId)
  else next.delete(productId)
  orderRequestsInFlight.value = next
}

function orderIdempotencyKey(productId: string) {
  const existing = orderIdempotencyKeys.get(productId)
  if (existing) return existing
  const key = `web-catalog-${crypto.randomUUID()}`
  orderIdempotencyKeys.set(productId, key)
  return key
}

function startTextTurn(text: string) {
  const pending = retryableTextTurn && retryableTextTurn.text === text
    ? retryableTextTurn
    : { id: crypto.randomUUID(), text }
  retryableTextTurn = pending
  activeTurnId = pending.id
  activeTurnSource = 'text'
  isTurnInFlight.value = true
  armTurnTimeout(pending.id)
  return pending
}

function startVoiceTurn(turnId: string) {
  activeTurnId = turnId
  activeTurnSource = 'voice'
  isTurnInFlight.value = true
  armTurnTimeout(turnId)
}

function armTurnTimeout(turnId: string) {
  if (activeTurnTimeout) window.clearTimeout(activeTurnTimeout)
  activeTurnTimeout = window.setTimeout(() => {
    if (activeTurnId !== turnId) return
    error.value = '导购处理超时，请重试'
    flowStatus.value = '导购处理超时，请重试'
    finishTurn(turnId, true)
  }, TURN_TIMEOUT_MS)
}

function setLiveAsrTranscript(turnId: string, transcript: string, streaming: boolean) {
  if (!transcript || !turnId) return
  const messageForTurn = messages.value.find(
    (item) => item.role === 'user' && item.turnId === turnId,
  )
  if (messageForTurn) {
    // Ignore a late hypothesis after the final result has been rendered.
    if (streaming && messageForTurn.streaming === false) return
    messageForTurn.text = transcript
    messageForTurn.streaming = streaming
  } else {
    messages.value.push({ role: 'user', text: transcript, turnId, streaming })
  }
}

function finishTurn(turnId: string, retryable = false) {
  if (activeTurnId !== turnId) return
  if (activeTurnTimeout) {
    window.clearTimeout(activeTurnTimeout)
    activeTurnTimeout = 0
  }
  const source = activeTurnSource
  activeTurnId = ''
  activeTurnSource = null
  isTurnInFlight.value = false
  if (source === 'text' && !retryable) retryableTextTurn = null
  if (source === 'text' && retryable && retryableTextTurn?.id === turnId && !utterance.value.trim()) {
    utterance.value = retryableTextTurn.text
  }
}

function formatPrice(value: string | number) {
  return Number(value).toLocaleString('zh-CN', { minimumFractionDigits: 2, maximumFractionDigits: 2 })
}

function formatDateTime(value: string) {
  const date = new Date(value)
  return Number.isNaN(date.getTime()) ? '—' : date.toLocaleString('zh-CN', { hour12: false })
}

function sendQuickPrompt(text: string) {
  if (isTurnInFlight.value) return
  utterance.value = text
  void sendUtterance()
}

function handleImageError(event: Event) {
  const image = event.currentTarget
  if (!(image instanceof HTMLImageElement)) return
  image.hidden = true
}

async function loadData() {
  loading.value = true
  error.value = ''
  try {
    const [merchantData, productData, orderData, categoryData] = await Promise.all([
      requestJson<ItemsResponse<Merchant>>('/catalog/merchants'),
      requestJson<ItemsResponse<Product>>('/catalog/products'),
      requestJson<ItemsResponse<Order>>('/orders/mine'),
      requestJson<ItemsResponse<Category>>('/catalog/categories'),
    ])
    merchants.value = merchantData.items
    products.value = productData.items
    orders.value = orderData.items
    supportedCategories.value = categoryData.items
  } catch (reason) {
    error.value = reason instanceof Error ? reason.message : '加载失败'
  } finally {
    loading.value = false
  }
}

async function refreshAudioInputs(requestPermission = false) {
  error.value = ''
  try {
    if (requestPermission) {
      const permissionStream = await navigator.mediaDevices.getUserMedia({ audio: true })
      permissionStream.getTracks().forEach((track) => track.stop())
    }
    if (!navigator.mediaDevices?.enumerateDevices) return
    const devices = await navigator.mediaDevices.enumerateDevices()
    audioInputs.value = devices
      .filter(
        (device) =>
          device.kind === 'audioinput' &&
          device.deviceId !== 'default' &&
          device.deviceId !== 'communications',
      )
      .map((device, index) => ({
        deviceId: device.deviceId,
        label: device.label || `麦克风 ${index + 1}`,
        labelKnown: Boolean(device.label),
      }))
    if (
      selectedAudioInputId.value &&
      !audioInputs.value.some((device) => device.deviceId === selectedAudioInputId.value)
    ) {
      selectedAudioInputId.value = ''
      localStorage.removeItem('voice-shopping-audio-input')
    }
    const isVirtualInput = (device: AudioInputOption) =>
      /(todesk|virtual|stereo mix|立体声混音|vb-audio|voicemeeter|loopback|cable)/i.test(
        device.label,
      )
    const selectedInput = audioInputs.value.find(
      (device) => device.deviceId === selectedAudioInputId.value,
    )
    const preferredInput = audioInputs.value.find(
      (device) => device.labelKnown && !isVirtualInput(device),
    )
    if (preferredInput && (!selectedInput || isVirtualInput(selectedInput))) {
      selectedAudioInputId.value = preferredInput.deviceId
      localStorage.setItem('voice-shopping-audio-input', preferredInput.deviceId)
    }
  } catch (reason) {
    error.value = reason instanceof Error ? reason.message : '无法读取麦克风列表'
  }
}

function handleAudioDeviceChange() {
  void refreshAudioInputs()
}

function pumpAudioQueue() {
  if (activeAssistantAudio || activeAssistantSpeech) return
  const next = audioQueue.shift()
  if (!next) {
    isAssistantSpeaking.value = false
    isAssistantSpeechPaused.value = false
    return
  }

  if (next.fallback) {
    if (!('speechSynthesis' in window)) {
      pumpAudioQueue()
      return
    }
    const speech = new SpeechSynthesisUtterance(next.text)
    speech.lang = 'zh-CN'
    activeAssistantSpeech = speech
    isAssistantSpeaking.value = true
    isAssistantSpeechPaused.value = false
    const finishSpeech = () => {
      if (activeAssistantSpeech !== speech) return
      activeAssistantSpeech = null
      isAssistantSpeaking.value = false
      isAssistantSpeechPaused.value = false
      pumpAudioQueue()
    }
    speech.onend = finishSpeech
    speech.onerror = finishSpeech
    window.speechSynthesis.speak(speech)
    return
  }

  if (!next.audio) {
    pumpAudioQueue()
    return
  }
  const url = URL.createObjectURL(next.audio)
  const audio = new Audio(url)
  activeAssistantAudio = audio
  activeAssistantAudioUrl = url
  isAssistantSpeaking.value = true
  isAssistantSpeechPaused.value = false
  let finished = false
  const finishAudio = () => {
    if (finished) return
    finished = true
    if (activeAssistantAudio === audio) {
      activeAssistantAudio = null
      if (activeAssistantAudioUrl === url) activeAssistantAudioUrl = null
      isAssistantSpeaking.value = false
      isAssistantSpeechPaused.value = false
    }
    URL.revokeObjectURL(url)
    pumpAudioQueue()
  }
  audio.onended = finishAudio
  audio.onerror = finishAudio
  void audio.play().catch(finishAudio)
}

function speak(text: string, turnId = '') {
  if (!text) return
  audioQueue.push({ turnId, text, fallback: true })
  pumpAudioQueue()
}

function stopAssistantSpeech() {
  if ('speechSynthesis' in window) window.speechSynthesis.cancel()
  if (incomingAudioSegment?.turnId) mutedSpeechTurnIds.add(incomingAudioSegment.turnId)
  for (const segment of audioQueue) {
    if (segment.turnId) mutedSpeechTurnIds.add(segment.turnId)
  }
  activeAssistantSpeech = null
  isAssistantSpeaking.value = false
  isAssistantSpeechPaused.value = false
  for (const turnId of pendingSpeechByTurn.keys()) mutedSpeechTurnIds.add(turnId)
  pendingSpeechByTurn.clear()
  audioQueue.length = 0
  incomingAudioSegment = null
  if (activeAssistantAudio) {
    activeAssistantAudio.pause()
    activeAssistantAudio.removeAttribute('src')
    activeAssistantAudio.load()
    activeAssistantAudio = null
  }
  if (activeAssistantAudioUrl) URL.revokeObjectURL(activeAssistantAudioUrl)
  activeAssistantAudioUrl = null
}

function toggleAssistantSpeechPause() {
  if (!isAssistantSpeaking.value) return
  if (isAssistantSpeechPaused.value) {
    if (activeAssistantAudio) {
      void activeAssistantAudio.play().catch(() => {
        if (activeAssistantAudio) stopAssistantSpeech()
      })
    } else if ('speechSynthesis' in window) {
      window.speechSynthesis.resume()
    }
    isAssistantSpeechPaused.value = false
    return
  }
  if (activeAssistantAudio) activeAssistantAudio.pause()
  else if ('speechSynthesis' in window) window.speechSynthesis.pause()
  isAssistantSpeechPaused.value = true
}

function fallbackRecommendationReason(card: RecommendationCard) {
  const sellingPoint = card.sellingPoints.find((point) => point.trim())
  return sellingPoint
    ? `已匹配你的需求：${sellingPoint}。`
    : '已按你的需求和当前筛选条件为你挑选。'
}

function formatMatchScore(value: number) {
  return Math.min(100, Math.round(value * 100))
}

function reasonDeltasForTurn(turnId: string) {
  let reasons = recommendationReasonsByTurn.get(turnId)
  if (!reasons) {
    reasons = new Map<string, string>()
    recommendationReasonsByTurn.set(turnId, reasons)
  }
  return reasons
}

function handleEvent(event: ApiEvent<Record<string, unknown>>) {
  if (event.type === 'flow.status') {
    const status = String(event.payload.status ?? '')
    if (status === 'processing') {
      const nodeName = String(event.payload.node ?? '')
      flowStatus.value = workflowNodeLabels[nodeName] || '智能导购正在理解与筛选…'
    } else if (status === 'completed') {
      flowStatus.value = '可以继续说'
    }
  }
  if (event.type === 'recommendation.cards') {
    const cards = event.payload.productCards as RecommendationCard[]
    const keepCurrentReasons = recommendationTurnId === event.turnId
    const currentCards = new Map(recommendations.value.map((card) => [card.productId, card]))
    const streamedReasons = reasonDeltasForTurn(event.turnId)
    recommendations.value = cards.map((card) => {
      const streamedReason = streamedReasons.get(card.productId)
      const currentCard = keepCurrentReasons ? currentCards.get(card.productId) : undefined
      const suppliedReason = String(card.reason ?? '').trim()
      if (streamedReason) return { ...card, reason: streamedReason, reasonIsFallback: false }
      if (currentCard?.reason) {
        return {
          ...card,
          reason: currentCard.reason,
          reasonIsFallback: currentCard.reasonIsFallback,
        }
      }
      if (suppliedReason) return { ...card, reason: suppliedReason, reasonIsFallback: false }
      return { ...card, reason: fallbackRecommendationReason(card), reasonIsFallback: true }
    })
    recommendationTurnId = event.turnId
    for (const turnId of recommendationReasonsByTurn.keys()) {
      if (turnId !== event.turnId) recommendationReasonsByTurn.delete(turnId)
    }
  }
  if (event.type === 'text.delta' && event.payload.scope === 'reason') {
    const productId = String(event.payload.productId ?? '')
    const delta = String(event.payload.delta ?? '')
    if (!productId || !delta) return
    const streamedReasons = reasonDeltasForTurn(event.turnId)
    const reason = `${streamedReasons.get(productId) ?? ''}${delta}`
    streamedReasons.set(productId, reason)
    const card = recommendations.value.find(
      (item) => recommendationTurnId === event.turnId && item.productId === productId,
    )
    if (card) {
      card.reason = reason
      card.reasonIsFallback = false
    }
  }
  if (event.type === 'text.delta' && event.payload.scope === 'speech') {
    const delta = String(event.payload.delta ?? '')
    if (!delta) return
    const liveMessage = messages.value.find(
      (item) => item.role === 'assistant' && item.turnId === event.turnId && item.streaming,
    )
    if (liveMessage) liveMessage.text += delta
    else messages.value.push({ role: 'assistant', text: delta, turnId: event.turnId, streaming: true })
  }
  if (event.type === 'text.completed') {
    const text = String(event.payload.text ?? '')
    const liveMessage = messages.value.find(
      (item) => item.role === 'assistant' && item.turnId === event.turnId && item.streaming,
    )
    if (liveMessage) {
      liveMessage.text = text
      liveMessage.streaming = false
    } else {
      messages.value.push({ role: 'assistant', text, turnId: event.turnId })
    }
    const suppressSpeech = isBargingIn && event.turnId !== latestVoiceTurnId
    if (suppressSpeech) mutedSpeechTurnIds.add(event.turnId)
    if (!suppressSpeech && audioSocket?.readyState === WebSocket.OPEN) {
      pendingSpeechByTurn.set(event.turnId, text)
    } else if (!suppressSpeech) {
      speak(text)
    }
    finishTurn(event.turnId)
  }
  if (event.type === 'flow.error') {
    error.value = String(event.payload.message ?? '智能导购处理失败')
    flowStatus.value = '处理失败，请重试'
    finishTurn(event.turnId === 'unknown' ? activeTurnId : event.turnId, true)
  }
  if (event.type === 'order.updated') void loadData()
}

// Browsers may close a WebSocket without firing `error`; settle every
// connection attempt so recording never waits on an orphaned Promise.
function connectText(): Promise<void> {
  if (textSocket?.readyState === WebSocket.OPEN) return Promise.resolve()
  if (textSocket?.readyState === WebSocket.CONNECTING && textConnectPromise) return textConnectPromise
  if (textSocket && textSocket.readyState !== WebSocket.CLOSED) {
    textSocket.close()
  }
  const token = getAccessToken()
  if (!token) return Promise.reject(new Error('登录状态已失效，请重新登录'))
  const socket = new WebSocket(`${textWsBaseUrl}/${sessionId}?token=${encodeURIComponent(token)}`)
  textSocket = socket
  let settled = false
  let opened = false
  let connectTimer = 0
  let resolveConnection!: () => void
  let rejectConnection!: (reason: Error) => void
  const connection = new Promise<void>((resolve, reject) => {
    resolveConnection = resolve
    rejectConnection = reject
  })
  textConnectPromise = connection
  const clearConnectTimer = () => {
    if (connectTimer) {
      window.clearTimeout(connectTimer)
      connectTimer = 0
    }
  }
  const failConnection = (message: string) => {
    if (settled) return
    settled = true
    clearConnectTimer()
    if (textSocket === socket) textSocket = null
    if (textConnectPromise === connection) textConnectPromise = null
    rejectConnection(new Error(message))
  }
  connectTimer = window.setTimeout(() => {
    failConnection('文本连接超时，请检查导购服务')
    try {
      socket.close()
    } catch {
      // The socket may already have closed while the timeout callback ran.
    }
  }, REALTIME_CONNECT_TIMEOUT_MS)
  socket.onopen = () => {
    if (settled) return
    settled = true
    opened = true
    clearConnectTimer()
    if (textConnectPromise === connection) textConnectPromise = null
    flowStatus.value = '导购已就绪'
    resolveConnection()
  }
  socket.onerror = () => {
    failConnection('文本连接失败')
  }
  socket.onclose = () => {
    if (!opened) failConnection('文本连接已断开')
    if (textSocket !== socket) return
    if (textSocket === socket) textSocket = null
    if (textConnectPromise === connection) textConnectPromise = null
    if (activeTurnSource === 'text') finishTurn(activeTurnId, true)
    if (opened) flowStatus.value = '连接已断开，发送或录音时会自动重连'
  }
  socket.onmessage = (message) => {
    const event = JSON.parse(String(message.data)) as ApiEvent<Record<string, unknown>>
    if (event.type !== 'session.connected') handleEvent(event)
  }
  return connection
}

function connectAudio(): Promise<void> {
  if (audioSocket?.readyState === WebSocket.OPEN) return Promise.resolve()
  if (audioSocket?.readyState === WebSocket.CONNECTING && audioConnectPromise) return audioConnectPromise
  if (audioSocket && audioSocket.readyState !== WebSocket.CLOSED) {
    audioSocket.close()
  }
  const token = getAccessToken()
  if (!token) return Promise.reject(new Error('登录状态已失效，请重新登录'))
  const socket = new WebSocket(`${audioWsBaseUrl}/${sessionId}?token=${encodeURIComponent(token)}`)
  audioSocket = socket
  socket.binaryType = 'blob'
  let settled = false
  let opened = false
  let connectTimer = 0
  let resolveConnection!: () => void
  let rejectConnection!: (reason: Error) => void
  const connection = new Promise<void>((resolve, reject) => {
    resolveConnection = resolve
    rejectConnection = reject
  })
  audioConnectPromise = connection
  const clearConnectTimer = () => {
    if (connectTimer) {
      window.clearTimeout(connectTimer)
      connectTimer = 0
    }
  }
  const failConnection = (message: string) => {
    if (settled) return
    settled = true
    clearConnectTimer()
    if (audioSocket === socket) audioSocket = null
    if (audioConnectPromise === connection) audioConnectPromise = null
    rejectConnection(new Error(message))
  }
  connectTimer = window.setTimeout(() => {
    failConnection('音频连接超时，请检查导购服务')
    try {
      socket.close()
    } catch {
      // The socket may already have closed while the timeout callback ran.
    }
  }, REALTIME_CONNECT_TIMEOUT_MS)
  socket.onopen = () => {
    if (settled) return
    settled = true
    opened = true
    clearConnectTimer()
    if (audioConnectPromise === connection) audioConnectPromise = null
    resolveConnection()
  }
  socket.onerror = () => {
    failConnection('音频连接失败')
  }
  socket.onclose = () => {
    if (!opened) failConnection('音频连接已断开')
    if (audioSocket !== socket) return
    if (audioSocket === socket) audioSocket = null
    if (audioConnectPromise === connection) audioConnectPromise = null
    const voiceWasActive = isVoiceStarting.value || isRecording.value || activeTurnSource === 'voice'
    if (pendingAsrStart) {
      window.clearTimeout(pendingAsrStart.timer)
      pendingAsrStart.reject(new Error('ASR 连接已断开'))
      pendingAsrStart = null
    }
    if (isRecording.value) cleanupRecording()
    if (activeTurnSource === 'voice') {
      finishTurn(activeTurnId, true)
    }
    if (voiceWasActive) {
      error.value = '音频连接已断开，请重新录音'
      flowStatus.value = '语音识别失败，请重试'
    }
    for (const text of pendingSpeechByTurn.values()) speak(text)
    pendingSpeechByTurn.clear()
    incomingAudioSegment = null
    pendingPcmFrames = []
    asrReady = false
    stopRequested = false
  }
  socket.onmessage = (message) => {
    if (message.data instanceof Blob) {
      if (incomingAudioSegment && !incomingAudioSegment.fallback && !incomingAudioSegment.suppressed) {
        incomingAudioSegment.chunks.push(message.data)
      }
      return
    }
    const event = JSON.parse(String(message.data)) as ApiEvent<Record<string, unknown>>
    if (event.type === 'asr.completed') {
      const transcript = String(event.payload?.transcript ?? '')
      if (transcript && event.turnId) {
        setLiveAsrTranscript(event.turnId, transcript, false)
        flowStatus.value = '智能导购正在理解与筛选…'
        error.value = ''
      }
      if (event.turnId === latestVoiceTurnId) isBargingIn = false
    }
    if (event.type === 'asr.partial') {
      const transcript = String(event.payload?.transcript ?? '')
      if (!transcript || !event.turnId) return
      setLiveAsrTranscript(event.turnId, transcript, true)
      flowStatus.value = '正在聆听，实时转写中…'
    }
    if (event.type === 'asr.sentence') {
      const sentence = String(event.payload?.transcript ?? '')
      if (!sentence || !event.turnId) return
      const fullTranscript = String(event.payload?.fullTranscript ?? '')
      if (fullTranscript) {
        setLiveAsrTranscript(event.turnId, fullTranscript, true)
        flowStatus.value = '正在聆听，已实时转写…'
        return
      }
      const liveMessage = messages.value.find(
        (item) => item.role === 'user' && item.turnId === event.turnId && item.streaming,
      )
      if (liveMessage) {
        liveMessage.text += sentence
      } else {
        setLiveAsrTranscript(event.turnId, sentence, true)
      }
      flowStatus.value = '正在聆听，已实时转写…'
    }
    if (event.type === 'asr.started') {
      const pending = pendingAsrStart
      if (pending && event.turnId === pending.turnId) {
        window.clearTimeout(pending.timer)
        pending.resolve()
        pendingAsrStart = null
      }
    }
    if (event.type === 'audio.error') {
      const payload = event.payload ?? {}
      const metrics = (payload.clientMetrics ?? {}) as Record<string, unknown>
      const stage = String(payload.stage ?? '')
      const hasReceivedBytes = Object.prototype.hasOwnProperty.call(payload, 'receivedBytes')
      const receivedBytes = hasReceivedBytes ? Number(payload.receivedBytes) : null
      const hasPeak = Object.prototype.hasOwnProperty.call(metrics, 'peak')
      const hasDuration = Object.prototype.hasOwnProperty.call(metrics, 'durationMs')
      const peak = Number(metrics.peak ?? 0)
      const durationMs = Number(metrics.durationMs ?? 0)
      let messageText = String(payload.message ?? '语音识别失败')
      // Only ASR capture failures should turn transport metrics into
      // microphone advice. Workflow/session errors may arrive on this same
      // socket after a valid transcript has already been emitted.
      const isCaptureError = !stage || stage === 'asr'
      if (isCaptureError && receivedBytes !== null && receivedBytes <= 0) {
        messageText = '后端没有收到麦克风音频，请检查 Chrome 麦克风权限'
      } else if (isCaptureError && receivedBytes !== null && hasPeak && peak < 0.003) {
        messageText = 'Chrome 麦克风输入接近静音，请检查当前输入设备或系统音量'
      } else if (isCaptureError && hasDuration && durationMs > 0 && durationMs < 800) {
        messageText = '录音时间太短，请说完后再点击停止录音'
      }
      const pending = pendingAsrStart
      if (pending && event.turnId === pending.turnId) {
        window.clearTimeout(pending.timer)
        pending.reject(new Error(messageText))
        pendingAsrStart = null
      }
      error.value = messageText
      flowStatus.value = stage === 'workflow' ? '导购处理失败，请重试' : '语音识别失败，请重试'
      if (event.turnId) finishTurn(event.turnId)
    }
    if (event.type === 'audio.start') {
      const pendingText = event.turnId ? pendingSpeechByTurn.get(event.turnId) : undefined
      if (event.turnId) pendingSpeechByTurn.delete(event.turnId)
      const turnId = event.turnId ?? ''
      const text = String(event.payload?.text ?? pendingText ?? '')
      const fallback = event.payload?.fallback === true
      const suppressed = Boolean(turnId && mutedSpeechTurnIds.has(turnId))
      incomingAudioSegment = {
        turnId,
        text,
        fallback,
        suppressed,
        chunks: [],
      }
      if (suppressed) return
      if (fallback) {
        speak(text, turnId)
      } else if ('speechSynthesis' in window) {
        window.speechSynthesis.cancel()
        activeAssistantSpeech = null
        isAssistantSpeaking.value = false
        isAssistantSpeechPaused.value = false
      }
    }
    if (event.type === 'audio.end') {
      const segment = incomingAudioSegment
      incomingAudioSegment = null
      if (segment && !segment.suppressed && !segment.fallback && segment.chunks.length) {
        audioQueue.push({
          turnId: segment.turnId,
          text: segment.text,
          fallback: false,
          audio: new Blob(segment.chunks, { type: 'audio/wav' }),
        })
        pumpAudioQueue()
      }
    }
    if (event.type === 'audio.done' && event.turnId) {
      pendingSpeechByTurn.delete(event.turnId)
    }
  }
  return connection
}

function startServerAsr(turnId: string): Promise<void> {
  if (audioSocket?.readyState !== WebSocket.OPEN) return Promise.reject(new Error('音频连接未就绪'))
  flowStatus.value = '正在连接 ASR 模型…'
  return new Promise((resolve, reject) => {
    const timer = window.setTimeout(() => {
      if (pendingAsrStart?.turnId !== turnId) return
      pendingAsrStart = null
      reject(new Error('ASR 模型启动超时'))
    }, 8_000)
    pendingAsrStart = { turnId, resolve, reject, timer }
    audioSocket?.send(JSON.stringify({ type: 'audio.start', turnId }))
  })
}

function cleanupRecording(cancelServer = false) {
  if (cancelServer && recordingTurnId && audioSocket?.readyState === WebSocket.OPEN) {
    audioSocket.send(JSON.stringify({ type: 'audio.cancel', turnId: recordingTurnId }))
  }
  audioProcessor?.disconnect()
  audioSource?.disconnect()
  audioGain?.disconnect()
  void audioContext?.close()
  audioProcessor = null
  audioSource = null
  audioGain = null
  audioContext = null
  mediaStream?.getTracks().forEach((track) => track.stop())
  mediaStream = null
  isRecording.value = false
}

function flushPendingPcmFrames() {
  if (audioSocket?.readyState !== WebSocket.OPEN) return
  for (const frame of pendingPcmFrames) audioSocket.send(frame)
  pendingPcmFrames = []
}

function commitVoiceTurn(turnId: string) {
  if (audioSocket?.readyState !== WebSocket.OPEN) {
    error.value = '音频连接已断开，请重新录音'
    flowStatus.value = '语音识别失败，请重试'
    recordingTurnId = ''
    return
  }
  flushPendingPcmFrames()
  const durationMs = Math.max(0, Math.round(performance.now() - recordingStartedAt))
  flowStatus.value = 'ASR 正在转写…'
  if (!capturedAudioBytes) {
    error.value = '未采集到麦克风音频，请检查 Chrome 的麦克风权限'
  } else if (capturedAudioPeak < 0.003) {
    error.value = 'Chrome 麦克风输入接近静音，请检查当前输入设备或系统音量'
  }
  startVoiceTurn(turnId)
  try {
    audioSocket.send(
      JSON.stringify({
        type: 'audio.commit',
        turnId,
        clientMetrics: {
          capturedBytes: capturedAudioBytes,
          peak: Number(capturedAudioPeak.toFixed(6)),
          durationMs,
          inputLabel: activeAudioInputLabel.value,
          selectionMode: selectedAudioInputId.value ? 'explicit' : 'default',
        },
      }),
    )
  } catch (reason) {
    finishTurn(turnId)
    error.value = reason instanceof Error ? reason.message : '语音提交失败'
    flowStatus.value = '语音识别失败，请重试'
  }
  recordingTurnId = ''
  asrReady = false
  stopRequested = false
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
  if (isTurnInFlight.value) return
  const text = utterance.value.trim()
  if (!text) return
  error.value = ''
  const pending = startTextTurn(text)
  try {
    await connectText()
    if (textSocket?.readyState !== WebSocket.OPEN) throw new Error('文本连接未就绪')
    if (!messages.value.some((message) => message.role === 'user' && message.turnId === pending.id)) {
      messages.value.push({ role: 'user', text, turnId: pending.id })
    }
    recommendations.value = []
    textSocket.send(JSON.stringify({ type: 'turn.submit', turnId: pending.id, utterance: text }))
    utterance.value = ''
  } catch (reason) {
    finishTurn(pending.id, true)
    error.value = reason instanceof Error ? reason.message : '发送失败'
  }
}

async function startVoice() {
  if (isTurnInFlight.value || isVoiceStarting.value || isRecording.value) return
  error.value = ''
  isBargingIn = true
  latestVoiceTurnId = ''
  stopAssistantSpeech()
  isVoiceStarting.value = true
  flowStatus.value = '正在连接语音通道…'
  try {
    await Promise.all([connectText(), connectAudio()])
    const audioConstraints: MediaTrackConstraints = {
      echoCancellation: true,
      noiseSuppression: true,
      autoGainControl: true,
    }
    if (selectedAudioInputId.value) audioConstraints.deviceId = { exact: selectedAudioInputId.value }
    mediaStream = await navigator.mediaDevices.getUserMedia({ audio: audioConstraints })
    activeAudioInputLabel.value = mediaStream.getAudioTracks()[0]?.label || '系统默认麦克风'
    void refreshAudioInputs()
    audioContext = new AudioContext()
    if (audioContext.state === 'suspended') await audioContext.resume()
    audioSource = audioContext.createMediaStreamSource(mediaStream)
    audioProcessor = audioContext.createScriptProcessor(4096, 1, 1)
    audioGain = audioContext.createGain()
    audioGain.gain.value = 0
    capturedAudioBytes = 0
    capturedAudioPeak = 0
    recordingStartedAt = performance.now()
    lastAudioLevelUpdateAt = 0
    pendingPcmFrames = []
    asrReady = false
    stopRequested = false
    const turnId = crypto.randomUUID()
    recordingTurnId = turnId
    latestVoiceTurnId = turnId
    audioProcessor.onaudioprocess = (event) => {
      if (!audioContext || recordingTurnId !== turnId) return
      const input = event.inputBuffer.getChannelData(0)
      let chunkPeak = 0
      for (const sample of input) chunkPeak = Math.max(chunkPeak, Math.abs(sample))
      capturedAudioPeak = Math.max(capturedAudioPeak, chunkPeak)
      const now = performance.now()
      if (asrReady && now - lastAudioLevelUpdateAt >= 250) {
        const levelText = chunkPeak >= 0.003 ? '有声音' : '输入较低'
        flowStatus.value = `正在聆听 · ${activeAudioInputLabel.value} · ${levelText}`
        lastAudioLevelUpdateAt = now
      }
      const pcm = encodePcm16(input, audioContext.sampleRate)
      capturedAudioBytes += pcm.byteLength
      if (asrReady && audioSocket?.readyState === WebSocket.OPEN) audioSocket.send(pcm)
      else pendingPcmFrames.push(pcm)
    }
    audioSource.connect(audioProcessor)
    audioProcessor.connect(audioGain)
    audioGain.connect(audioContext.destination)
    isRecording.value = true
    isVoiceStarting.value = false
    flowStatus.value = '正在聆听，ASR 模型连接中…'
    await startServerAsr(turnId)
    if (recordingTurnId !== turnId) return
    asrReady = true
    flushPendingPcmFrames()
    if (stopRequested) commitVoiceTurn(turnId)
    else flowStatus.value = '正在聆听…'
  } catch (reason) {
    error.value = reason instanceof Error ? reason.message : '无法使用麦克风'
    flowStatus.value = '语音通道未就绪'
    cleanupRecording(true)
    recordingTurnId = ''
    isBargingIn = false
  } finally {
    isVoiceStarting.value = false
  }
}

function stopVoice() {
  const turnId = recordingTurnId
  cleanupRecording()
  if (!turnId) {
    error.value = '音频连接已断开，请重新录音'
    flowStatus.value = '语音识别失败，请重试'
    recordingTurnId = ''
    return
  }
  stopRequested = true
  flowStatus.value = asrReady ? 'ASR 正在转写…' : '正在提交已缓存的语音…'
  if (asrReady) commitVoiceTurn(turnId)
}

async function reportClick(productId: string) {
  await requestJson('/catalog/behaviors', {
    method: 'POST',
    body: JSON.stringify({ productId, eventType: 'click' }),
  }).catch(() => undefined)
}

function recommendationToProduct(card: RecommendationCard): Product {
  const loadedProduct = products.value.find((product) => product.id === card.productId)
  if (loadedProduct) return loadedProduct
  return {
    id: card.productId,
    merchantId: card.merchantId,
    merchantName: card.merchantName,
    sku: card.sku || '推荐商品',
    name: card.name,
    categoryL1: card.categoryL1 || '',
    categoryL2: card.categoryL2 || '推荐商品',
    brand: card.brand,
    description: card.description || '',
    price: String(card.price),
    stock: card.stock,
    attributes: card.attributes ?? {},
    sellingPoints: card.sellingPoints,
    imageUrls: card.imageUrls?.length ? card.imageUrls : card.imageUrl ? [card.imageUrl] : [],
    status: card.status ?? 'on_sale',
    createdAt: card.createdAt ?? '',
    updatedAt: card.updatedAt ?? '',
  }
}

function openProduct(product: Product) {
  selectedProduct.value = product
  void reportClick(product.id)
}

function openRecommendation(card: RecommendationCard) {
  openProduct(recommendationToProduct(card))
}

function closeProductDetails() {
  selectedProduct.value = null
}

function buySelectedProduct() {
  if (!selectedProduct.value) return
  void buyProduct(selectedProduct.value.id)
}

async function buyProduct(productId: string) {
  if (isOrderRequestInFlight(productId)) return
  setOrderRequestInFlight(productId, true)
  error.value = ''
  try {
    await requestJson<Order>('/orders', {
      method: 'POST',
      body: JSON.stringify({
        productId,
        quantity: 1,
        idempotencyKey: orderIdempotencyKey(productId),
      }),
    })
    await loadData()
    closeProductDetails()
    goTo('/orders')
    orderIdempotencyKeys.delete(productId)
  } catch (reason) {
    error.value = reason instanceof Error ? reason.message : '创建订单失败'
  } finally {
    setOrderRequestInFlight(productId, false)
  }
}

async function updateOrder(order: Order, action: 'confirm' | 'cancel') {
  try {
    await requestJson<Order>(`/orders/${order.id}/${action}`, {
      method: 'POST',
    })
    await loadData()
  } catch (reason) {
    error.value = reason instanceof Error ? reason.message : '订单操作失败'
  }
}

function notifySessionClosed() {
  if (!appReady.value || sessionCloseSent) return
  sessionCloseSent = true
  localStorage.removeItem('voice-shopping-session')
  void requestJson(`/sessions/${sessionId}/close`, {
    method: 'POST',
    body: JSON.stringify({ reason: 'page_closed' }),
    keepalive: true,
  }).catch(() => undefined)
}

async function startApp() {
  if (appStarted) return
  appStarted = true
  appReady.value = true
  await nextTick()
  syncRoute()
  void Promise.all([loadData(), connectText(), connectAudio(), refreshAudioInputs()]).catch((reason) => {
    if (error.value) return
    error.value = reason instanceof Error ? reason.message : '导购连接失败'
    flowStatus.value = '导购连接失败，请重试'
  })
  window.addEventListener('pagehide', notifySessionClosed)
  window.addEventListener('hashchange', syncRoute)
  navigator.mediaDevices?.addEventListener?.('devicechange', handleAudioDeviceChange)
}

function signOut() {
  appReady.value = false
  clearAccessToken()
  window.location.reload()
}
onBeforeUnmount(() => {
  notifySessionClosed()
  window.removeEventListener('pagehide', notifySessionClosed)
  window.removeEventListener('hashchange', syncRoute)
  stopAssistantSpeech()
  textSocket?.close()
  audioSocket?.close()
  mediaStream?.getTracks().forEach((track) => track.stop())
  navigator.mediaDevices?.removeEventListener?.('devicechange', handleAudioDeviceChange)
})
</script>

<template>
  <LoginGate v-if="!appReady" required-role="customer" workspace-name="用户工作台" @authenticated="startApp" />
  <AppShell
    v-else
    :eyebrow="pageEyebrow"
    title="声选"
    :description="pageDescription"
    :nav-items="navItems"
    :active-nav-href="activeNavHref"
    :hero-compact="true"
    :workspace-links="workspaceLinks"
    action-label="退出登录"
    @action="signOut"
  >
    <template #headline>{{ pageHeadline }}</template>
    <template #hero-panel>
      <div class="hero-panel">
        <span class="hero-panel__label">{{ isVoicePage ? '实时供给概览' : isBrowsePage ? '当前可浏览供给' : '订单状态概览' }}</span>
        <div>
          <p class="hero-panel__value">
            {{ isOrdersPage ? `${orders.length} 笔订单 · ${pendingOrders} 笔待确认` : `${merchants.length} 家店 · ${products.length} 件商品` }}
          </p>
          <p class="hero-panel__note">{{ isOrdersPage ? `已完成 ${successfulOrders} 笔订单，确认前会再次校验价格和库存。` : '已完成的点击和成交会持续更新你的偏好画像。' }}</p>
          <button
            v-if="!isOrdersPage"
            class="supported-categories-trigger"
            type="button"
            @click="isSupportedCategoriesDialogOpen = true"
          >查看支持品类</button>
        </div>
      </div>
    </template>

    <div class="workspace">
      <p v-if="error" class="error-banner">{{ error }}</p>

      <section v-if="isVoicePage" class="section-panel voice-console">
        <div class="voice-controls">
          <div class="voice-heading-row">
            <div>
              <p class="eyebrow">实时语音导购</p>
              <h2>把需求说给我听</h2>
            </div>
            <span class="voice-live-badge"><span class="status-dot"></span>在线</span>
          </div>
          <p class="voice-description">支持推荐、对比、查询和二次确认下单；每次只追问一到两个必要条件。</p>
          <div class="voice-action-row">
            <button
              class="mic-button"
              :class="{ 'mic-button--active': isRecording }"
              type="button"
              :disabled="isTurnInFlight || isVoiceStarting"
              :aria-label="isRecording ? '停止录音' : '开始录音'"
              @click="isRecording ? stopVoice() : startVoice()"
            >
              <span class="mic-button__title">{{ isRecording ? '停止' : isVoiceStarting ? '连接中' : '开始说' }}</span>
              <span class="mic-button__caption">{{ isRecording ? '提交本轮' : isVoiceStarting ? '请稍候' : '语音输入' }}</span>
            </button>
            <div class="voice-action-copy">
              <strong>{{ isRecording ? '正在听你说' : isVoiceStarting ? '正在连接语音' : '点击开始说' }}</strong>
              <span>{{ isRecording ? '说完后再次点击，提交这一轮需求' : isVoiceStarting ? '正在准备麦克风，请稍候' : '也可以在下方直接输入文字' }}</span>
            </div>
            <button
              class="voice-pause-button"
              type="button"
              :disabled="!isAssistantSpeaking"
              :aria-label="isAssistantSpeechPaused ? '继续朗读' : '暂停朗读'"
              @click="toggleAssistantSpeechPause"
            >{{ isAssistantSpeechPaused ? '继续朗读' : '暂停朗读' }}</button>
          </div>
          <div class="voice-status" aria-live="polite"><span class="status-dot"></span>{{ flowStatus }}</div>
          <div class="voice-input-row">
            <input v-model="utterance" class="input" aria-label="导购消息" placeholder="例如：我想买一双通勤穿的鞋" :disabled="isTurnInFlight" @keyup.enter="sendUtterance" />
            <button class="primary-button voice-send-button" type="button" :disabled="isTurnInFlight" @click="sendUtterance">{{ isTurnInFlight ? '处理中...' : '发送需求' }}</button>
          </div>
          <div class="voice-examples" aria-label="快速开始">
            <span class="voice-examples__label">试着说</span>
            <button v-for="prompt in quickPrompts" :key="prompt" class="voice-example" type="button" :disabled="isTurnInFlight" @click="sendQuickPrompt(prompt)">{{ prompt }}</button>
          </div>
        </div>
        <div ref="conversationElement" class="conversation" aria-live="polite">
          <div class="conversation-header">
            <div>
              <strong>对话记录</strong>
              <span>文字和语音会在这里同步</span>
            </div>
            <span v-if="messages.length > 1" class="conversation-count">{{ messages.length }} 条</span>
          </div>
          <div class="conversation-list">
            <div v-for="(message, index) in messages" :key="index" class="message" :class="`message--${message.role}`">
              <span class="message__role">{{ message.role === 'user' ? '你' : '声选导购' }}</span>
              <span>{{ message.text }}</span>
            </div>
          </div>
        </div>
      </section>

      <section v-if="isVoicePage && recommendations.length" class="section-panel">
        <div class="section-heading"><div><span class="section-kicker">个性化推荐</span><h2>为你精排的商品</h2><p>先看匹配度，再打开详情或生成待确认订单。</p></div><span class="section-count">{{ recommendations.length }} 个推荐</span></div>
        <div class="product-grid">
          <article
            v-for="card in recommendations"
            :key="card.productId"
            class="product-card product-card--recommendation"
          >
            <button class="product-card__details" type="button" :aria-label="`查看${card.name}详情`" @click="openRecommendation(card)">
              <span class="product-visual" aria-hidden="true">
                <img v-if="card.imageUrl" :src="card.imageUrl" alt="" loading="lazy" @error="handleImageError" />
                <span class="product-visual__fallback">{{ card.name.slice(0, 1) }}</span>
              </span>
              <span class="product-meta"><span class="badge">匹配 {{ formatMatchScore(card.matchScore) }}%</span><span class="muted">{{ card.merchantName || '声选店铺' }}</span></span>
              <span class="product-card-title">{{ card.name }}</span>
              <span class="reason">{{ card.reason || '正在生成专属推荐理由…' }}</span>
            </button>
            <div class="product-card-footer"><span class="product-card-availability">有货 · {{ card.stock }} 件</span><span class="price">¥{{ formatPrice(card.price) }}</span><button class="primary-button small-button" type="button" :disabled="isOrderRequestInFlight(card.productId)" @click="buyProduct(card.productId)">{{ isOrderRequestInFlight(card.productId) ? '创建中...' : '生成待确认订单' }}</button></div>
          </article>
        </div>
      </section>

      <section v-if="isBrowsePage" class="section-panel">
        <div class="section-heading">
          <div><span class="section-kicker">商品浏览</span><h2>在售商品</h2><p>只展示启用店铺中有库存的商品，先逛逛再让导购帮你挑。</p></div>
          <span class="section-count">{{ visibleProducts.length }} / {{ products.length }} 件</span>
        </div>
        <div class="category-filter-row" aria-label="商品分类">
          <button class="filter-chip" :class="{ 'filter-chip--active': !selectedCategory }" type="button" :aria-pressed="!selectedCategory" @click="selectedCategory = ''">全部 <span>{{ products.length }}</span></button>
          <button v-for="category in categories" :key="category" class="filter-chip" :class="{ 'filter-chip--active': selectedCategory === category }" type="button" :aria-pressed="selectedCategory === category" @click="selectedCategory = category">{{ categoryLabel(category) }} <span>{{ products.filter((item) => item.categoryL2 === category).length }}</span></button>
        </div>
        <p v-if="loading" class="empty-state">正在加载商品…</p>
        <p v-else-if="!visibleProducts.length" class="empty-state">这个分类暂时没有可售商品，换个分类试试。</p>
        <div v-else class="product-grid">
          <article
            v-for="product in visibleProducts"
            :key="product.id"
            class="product-card"
          >
            <button class="product-card__details" type="button" :aria-label="`查看${product.name}详情`" @click="openProduct(product)">
              <span class="product-visual" aria-hidden="true">
                <img v-if="product.imageUrls?.length" :src="product.imageUrls[0]" alt="" loading="lazy" @error="handleImageError" />
                <span class="product-visual__fallback">{{ product.name.slice(0, 1) }}</span>
              </span>
              <span class="product-meta"><span class="badge">{{ categoryLabel(product.categoryL2) }}</span><span class="product-stock">有货 · {{ product.stock }} 件</span></span>
              <span class="product-card-title">{{ product.name }}</span>
              <span class="product-card-merchant">{{ product.brand || product.merchantName || '声选店铺' }}</span>
              <span class="product-card-description">{{ product.description }}</span>
            </button>
            <div class="product-card-footer"><span class="price">¥{{ formatPrice(product.price) }}</span><button class="secondary-button small-button" type="button" :disabled="isOrderRequestInFlight(product.id)" @click="buyProduct(product.id)">{{ isOrderRequestInFlight(product.id) ? '创建中...' : '购买' }}</button></div>
          </article>
        </div>
      </section>

      <section v-if="isOrdersPage" class="section-panel">
        <div class="section-heading"><div><span class="section-kicker">订单管理</span><h2>我的订单</h2><p>待确认订单将在十五分钟后失效，确认前会再次校验价格和库存。</p></div><span v-if="pendingOrders" class="badge badge--pending">{{ pendingOrders }} 笔待确认</span></div>
        <p v-if="!orders.length" class="empty-state">还没有订单，先去逛逛商品或开始语音导购吧。</p>
        <div v-else class="table-wrap">
          <table class="data-table">
            <thead><tr><th>商品</th><th>店铺</th><th>金额</th><th>状态</th><th>创建时间</th><th>操作</th></tr></thead>
            <tbody>
              <tr v-for="order in orders" :key="order.id">
                <td><strong>{{ order.productSnapshot.name }}</strong></td><td>{{ order.merchantSnapshot.name }}</td><td class="order-total">¥{{ formatPrice(order.totalAmount) }}</td>
                <td><span class="badge" :class="`badge--${order.status}`">{{ orderStatusLabel(order.status) }}</span></td>
                <td><time :datetime="order.createdAt">{{ formatDateTime(order.createdAt) }}</time></td>
                <td><div v-if="order.status === 'pending'" class="section-actions"><button class="secondary-button small-button" @click="updateOrder(order, 'confirm')">确认</button><button class="danger-button small-button" @click="updateOrder(order, 'cancel')">取消</button></div><span v-else class="muted">—</span></td>
              </tr>
            </tbody>
          </table>
        </div>
      </section>
    </div>
    <ProductDetailModal
      v-if="selectedProduct"
      :product="selectedProduct"
      :action-label="isOrderRequestInFlight(selectedProduct.id) ? '创建中...' : '生成待确认订单'"
      :action-disabled="isOrderRequestInFlight(selectedProduct.id)"
      @close="closeProductDetails"
      @action="buySelectedProduct"
    />
    <SupportedCategoriesModal
      v-if="isSupportedCategoriesDialogOpen"
      :categories="supportedCategories"
      @close="isSupportedCategoriesDialogOpen = false"
    />
  </AppShell>
</template>
