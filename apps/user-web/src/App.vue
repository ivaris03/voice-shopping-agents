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
const audioInputs = ref<AudioInputOption[]>([])
const selectedAudioInputId = ref(localStorage.getItem('voice-shopping-audio-input') ?? '')
const activeAudioInputLabel = ref('')
const utterance = ref('')
const loading = ref(true)
const error = ref('')
const flowStatus = ref('正在连接导购…')
const messages = ref<ChatMessage[]>([
  { role: 'assistant', text: '你好，我是声选导购。告诉我想买什么，我每次会问一到两个必要问题。' },
])
const isRecording = ref(false)
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
let recordingTurnId = ''
let asrReady = false
let stopRequested = false
let pendingPcmFrames: ArrayBuffer[] = []
let pendingAsrStart: PendingAsrStart | null = null
const pendingSpeechByTurn = new Map<string, string>()
const mutedSpeechTurnIds = new Set<string>()
let isBargingIn = false
let latestVoiceTurnId = ''

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
  }
  if (event.type === 'flow.error') {
    error.value = String(event.payload.message ?? 'Agent 处理失败')
    flowStatus.value = '处理失败，请重试'
  }
  if (event.type === 'order.updated') void loadData()
}

function connectText(): Promise<void> {
  if (textSocket?.readyState === WebSocket.OPEN) return Promise.resolve()
  if (textSocket?.readyState === WebSocket.CONNECTING && textConnectPromise) return textConnectPromise
  const socket = new WebSocket(`${textWsBaseUrl}/${sessionId}?userId=${customerId}`)
  textSocket = socket
  textConnectPromise = new Promise((resolve, reject) => {
    socket.onopen = () => {
      textConnectPromise = null
      flowStatus.value = '导购已就绪'
      resolve()
    }
    socket.onerror = () => {
      textConnectPromise = null
      reject(new Error('文本连接失败'))
    }
    socket.onclose = () => {
      if (textSocket === socket) textSocket = null
      textConnectPromise = null
      flowStatus.value = '连接已断开，发送或录音时会自动重连'
    }
    socket.onmessage = (message) => {
      const event = JSON.parse(String(message.data)) as ApiEvent<Record<string, unknown>>
      if (event.type !== 'session.connected') handleEvent(event)
    }
  })
  return textConnectPromise
}

function connectAudio(): Promise<void> {
  if (audioSocket?.readyState === WebSocket.OPEN) return Promise.resolve()
  if (audioSocket?.readyState === WebSocket.CONNECTING && audioConnectPromise) return audioConnectPromise
  const socket = new WebSocket(`${audioWsBaseUrl}/${sessionId}?userId=${customerId}`)
  audioSocket = socket
  socket.binaryType = 'blob'
  audioConnectPromise = new Promise((resolve, reject) => {
    socket.onopen = () => {
      audioConnectPromise = null
      resolve()
    }
    socket.onerror = () => {
      audioConnectPromise = null
      reject(new Error('音频连接失败'))
    }
    socket.onclose = () => {
      if (audioSocket === socket) audioSocket = null
      audioConnectPromise = null
      if (pendingAsrStart) {
        window.clearTimeout(pendingAsrStart.timer)
        pendingAsrStart.reject(new Error('ASR 连接已断开'))
        pendingAsrStart = null
      }
      if (isRecording.value) cleanupRecording()
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
      const event = JSON.parse(String(message.data)) as {
        type: string
        turnId?: string
        payload?: Record<string, unknown>
      }
      if (event.type === 'asr.completed') {
        const transcript = String(event.payload?.transcript ?? '')
        if (transcript) {
          const liveMessage = messages.value.find(
            (item) => item.role === 'user' && item.turnId === event.turnId && item.streaming,
          )
          if (liveMessage) {
            liveMessage.text = transcript
            liveMessage.streaming = false
          } else {
            messages.value.push({ role: 'user', text: transcript, turnId: event.turnId })
          }
          flowStatus.value = 'Agent 正在理解与筛选…'
          error.value = ''
        }
        if (event.turnId === latestVoiceTurnId) isBargingIn = false
      }
      if (event.type === 'asr.sentence') {
        const sentence = String(event.payload?.transcript ?? '')
        if (!sentence) return
        const liveMessage = messages.value.find(
          (item) => item.role === 'user' && item.turnId === event.turnId && item.streaming,
        )
        if (liveMessage) {
          liveMessage.text += sentence
        } else {
          messages.value.push({ role: 'user', text: sentence, turnId: event.turnId, streaming: true })
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
        const metrics = (event.payload?.clientMetrics ?? {}) as Record<string, unknown>
        const receivedBytes = Number(event.payload?.receivedBytes ?? 0)
        const peak = Number(metrics.peak ?? 0)
        const durationMs = Number(metrics.durationMs ?? 0)
        let messageText = String(event.payload?.message ?? '语音识别失败')
        if (!receivedBytes) messageText = '后端没有收到麦克风音频，请检查 Chrome 麦克风权限'
        else if (peak < 0.003) messageText = 'Chrome 麦克风输入接近静音，请检查当前输入设备或系统音量'
        else if (durationMs && durationMs < 800) messageText = '录音时间太短，请说完后再点击停止录音'
        const pending = pendingAsrStart
        if (pending && event.turnId === pending.turnId) {
          window.clearTimeout(pending.timer)
          pending.reject(new Error(messageText))
          pendingAsrStart = null
        }
        error.value = messageText
        flowStatus.value = '语音识别失败，请重试'
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
    }
  })
  return audioConnectPromise
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
  isBargingIn = true
  latestVoiceTurnId = ''
  stopAssistantSpeech()
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
  void Promise.all([loadData(), connectText(), connectAudio(), refreshAudioInputs()]).catch(() => undefined)
  navigator.mediaDevices?.addEventListener?.('devicechange', handleAudioDeviceChange)
})
onBeforeUnmount(() => {
  stopAssistantSpeech()
  textSocket?.close()
  audioSocket?.close()
  mediaStream?.getTracks().forEach((track) => track.stop())
  navigator.mediaDevices?.removeEventListener?.('devicechange', handleAudioDeviceChange)
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
          <p style="color: rgba(255,255,255,.68); line-height: 1.7">支持推荐、对比、查询和二次确认下单；每次追问一到两个缺失条件。</p>
          <div class="voice-action-row">
            <button
              class="mic-button"
              :class="{ 'mic-button--active': isRecording }"
              type="button"
              :aria-label="isRecording ? '停止录音' : '开始录音'"
              @click="isRecording ? stopVoice() : startVoice()"
            >{{ isRecording ? '■' : '●' }}</button>
            <button
              class="voice-pause-button"
              type="button"
              :disabled="!isAssistantSpeaking"
              :aria-label="isAssistantSpeechPaused ? '继续朗读' : '暂停朗读'"
              @click="toggleAssistantSpeechPause"
            >{{ isAssistantSpeechPaused ? '继续朗读' : '暂停朗读' }}</button>
          </div>
          <div class="voice-status"><span class="status-dot"></span>{{ flowStatus }}</div>
          <div class="voice-input-row">
            <input v-model="utterance" class="input" aria-label="导购消息" placeholder="例如：我想买一双通勤穿的鞋" @keyup.enter="sendUtterance" />
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
