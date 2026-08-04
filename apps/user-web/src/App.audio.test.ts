// @vitest-environment jsdom

import { flushPromises, mount } from '@vue/test-utils'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { nextTick } from 'vue'

import App from './App.vue'

class FakeWebSocket {
  static readonly OPEN = 1
  static instances: FakeWebSocket[] = []

  readonly url: string
  readyState = FakeWebSocket.OPEN
  binaryType = ''
  onopen: (() => void) | null = null
  onerror: (() => void) | null = null
  onclose: (() => void) | null = null
  onmessage: ((event: { data: string | Blob }) => void) | null = null

  constructor(url: string | URL) {
    this.url = String(url)
    FakeWebSocket.instances.push(this)
    queueMicrotask(() => this.onopen?.())
  }

  send = vi.fn()

  close() {
    this.readyState = 3
    this.onclose?.()
  }

  emitJson(message: unknown) {
    this.onmessage?.({ data: JSON.stringify(message) })
  }
}

class FakeSpeechSynthesisUtterance {
  lang = ''

  constructor(readonly text: string) {}
}

class FakeAudioContext {
  static processor: {
    onaudioprocess: ((event: { inputBuffer: { getChannelData: () => Float32Array } }) => void) | null
    connect: ReturnType<typeof vi.fn>
    disconnect: ReturnType<typeof vi.fn>
  } | null = null

  state = 'running'
  sampleRate = 48_000
  destination = {}
  close = vi.fn(async () => undefined)
  resume = vi.fn(async () => undefined)

  createMediaStreamSource() {
    return { connect: vi.fn(), disconnect: vi.fn() }
  }

  createScriptProcessor() {
    FakeAudioContext.processor = {
      onaudioprocess: null,
      connect: vi.fn(),
      disconnect: vi.fn(),
    }
    return FakeAudioContext.processor
  }

  createGain() {
    return { gain: { value: 1 }, connect: vi.fn(), disconnect: vi.fn() }
  }
}

function catalogProduct() {
  return {
    id: '20000000-0000-4000-8000-000000000001',
    merchantId: '10000000-0000-4000-8000-000000000001',
    merchantName: '声动数码',
    sku: 'HEADPHONE-A1',
    name: 'Sony WH-CH720N 无线降噪头戴耳机',
    categoryL1: 'ELECTRONICS',
    categoryL2: 'HEADPHONES',
    brand: 'Sony',
    description: '轻量头戴式主动降噪耳机。',
    price: 699,
    stock: 80,
    attributes: {},
    sellingPoints: [],
    imageUrls: [],
    status: 'on_sale' as const,
    createdAt: '',
    updatedAt: '',
  }
}

describe('assistant reply audio coordination', () => {
  const speak = vi.fn()
  const cancel = vi.fn()
  const pause = vi.fn()
  const resume = vi.fn()

  beforeEach(() => {
    FakeWebSocket.instances = []
    localStorage.clear()
    window.location.hash = '#/voice'
    speak.mockClear()
    cancel.mockClear()
    pause.mockClear()
    resume.mockClear()

    vi.stubGlobal('WebSocket', FakeWebSocket)
    vi.stubGlobal('SpeechSynthesisUtterance', FakeSpeechSynthesisUtterance)
    vi.stubGlobal('AudioContext', FakeAudioContext)
    Object.defineProperty(navigator, 'mediaDevices', {
      configurable: true,
      value: {
        enumerateDevices: vi.fn(async () => [
          { deviceId: 'mic-todesk', groupId: 'group-1', kind: 'audioinput', label: '麦克风 (ToDesk Virtual Audio)' },
          { deviceId: 'mic-realtek', groupId: 'group-2', kind: 'audioinput', label: '麦克风阵列 (Realtek(R) Audio)' },
        ]),
        getUserMedia: vi.fn(async () => {
          const track = { label: '麦克风阵列 (Realtek(R) Audio)', stop: vi.fn() }
          return { getAudioTracks: () => [track], getTracks: () => [track] }
        }),
        addEventListener: vi.fn(),
        removeEventListener: vi.fn(),
      },
    })
    Object.defineProperty(window, 'speechSynthesis', {
      configurable: true,
      value: { cancel, speak, pause, resume },
    })
    vi.stubGlobal(
      'fetch',
      vi.fn(async () =>
        new Response(JSON.stringify({ items: [] }), {
          status: 200,
          headers: { 'Content-Type': 'application/json' },
        }),
      ),
    )
  })

  afterEach(() => {
    vi.unstubAllGlobals()
  })

  it('does not start browser speech before the connected audio channel chooses its fallback', async () => {
    const wrapper = mount(App)
    await flushPromises()

    const textSocket = FakeWebSocket.instances.find((socket) => socket.url.includes('/ws/text/'))
    const audioSocket = FakeWebSocket.instances.find((socket) => socket.url.includes('/ws/audio/'))
    expect(textSocket).toBeDefined()
    expect(audioSocket).toBeDefined()

    const reply = '您好，为您推荐两款符合您通勤需求的降噪耳机。'
    textSocket?.emitJson({
      type: 'text.completed',
      turnId: 'turn-1',
      payload: { text: reply },
    })
    audioSocket?.emitJson({
      type: 'audio.start',
      turnId: 'turn-1',
      payload: { fallback: false, text: reply },
    })
    await flushPromises()

    expect(wrapper.text()).toContain(reply)
    expect(speak).not.toHaveBeenCalled()

    wrapper.unmount()
  })

  it('renders assistant speech deltas immediately and completes the same message', async () => {
    const wrapper = mount(App)
    await flushPromises()
    const textSocket = FakeWebSocket.instances.find((socket) => socket.url.includes('/ws/text/'))

    textSocket?.emitJson({
      type: 'text.delta',
      turnId: 'turn-streaming',
      payload: { scope: 'speech', delta: '正在为你筛选' },
    })
    await flushPromises()
    expect(wrapper.text()).toContain('正在为你筛选')

    textSocket?.emitJson({
      type: 'text.completed',
      turnId: 'turn-streaming',
      payload: { text: '正在为你筛选两款通勤耳机。' },
    })
    await flushPromises()

    expect(wrapper.findAll('.message--assistant')).toHaveLength(2)
    expect(wrapper.text()).toContain('正在为你筛选两款通勤耳机。')
    wrapper.unmount()
  })

  it('shows the backend workflow node while the turn is running', async () => {
    const wrapper = mount(App)
    await flushPromises()
    const textSocket = FakeWebSocket.instances.find((socket) => socket.url.includes('/ws/text/'))

    textSocket?.emitJson({
      type: 'flow.status',
      turnId: 'turn-clarification',
      payload: {
        status: 'processing',
        node: 'clarification_agent',
        label: '需求澄清 Agent 运行中',
      },
    })
    await flushPromises()

    expect(wrapper.text()).toContain('需求澄清正在进行')
    wrapper.unmount()
  })

  it('shows a factual fallback reason until the streamed recommendation reason arrives', async () => {
    const wrapper = mount(App)
    await flushPromises()
    const textSocket = FakeWebSocket.instances.find((socket) => socket.url.includes('/ws/text/'))
    const card = {
      productId: 'watch-1',
      merchantId: 'merchant-1',
      name: '测试机械腕表',
      price: 2280,
      stock: 10,
      sellingPoints: ['自动机械机芯'],
      matchScore: 0.8,
    }

    textSocket?.emitJson({
      type: 'recommendation.cards',
      turnId: 'turn-watch',
      payload: { productCards: [card] },
    })
    await flushPromises()
    expect(wrapper.text()).toContain('已匹配你的需求：自动机械机芯。')
    expect(wrapper.text()).not.toContain('正在生成专属推荐理由…')

    textSocket?.emitJson({
      type: 'text.delta',
      turnId: 'turn-watch',
      payload: { scope: 'reason', productId: card.productId, delta: '机械机芯符合你的偏好。' },
    })
    await flushPromises()
    expect(wrapper.text()).toContain('机械机芯符合你的偏好。')
    expect(wrapper.text()).not.toContain('已匹配你的需求：自动机械机芯。')
    wrapper.unmount()
  })

  it('uses browser speech only after the audio channel explicitly selects fallback', async () => {
    const wrapper = mount(App)
    await flushPromises()

    const textSocket = FakeWebSocket.instances.find((socket) => socket.url.includes('/ws/text/'))
    const audioSocket = FakeWebSocket.instances.find((socket) => socket.url.includes('/ws/audio/'))
    const reply = '您好，这是浏览器降级朗读。'

    textSocket?.emitJson({
      type: 'text.completed',
      turnId: 'turn-fallback',
      payload: { text: reply },
    })
    expect(speak).not.toHaveBeenCalled()

    audioSocket?.emitJson({
      type: 'audio.start',
      turnId: 'turn-fallback',
      payload: { fallback: true, text: reply },
    })
    await flushPromises()

    expect(speak).toHaveBeenCalledTimes(1)
    expect(speak.mock.calls[0]?.[0]).toMatchObject({ text: reply, lang: 'zh-CN' })

    wrapper.unmount()
  })

  it('interrupts the current reply as soon as the user starts recording', async () => {
    const wrapper = mount(App)
    await flushPromises()

    const audioSocket = FakeWebSocket.instances.find((socket) => socket.url.includes('/ws/audio/'))
    const reply = '这段回复会在用户开始说话时被打断。'
    audioSocket?.emitJson({
      type: 'audio.start',
      turnId: 'turn-interrupted',
      payload: { fallback: true, text: reply },
    })
    await flushPromises()
    expect(speak).toHaveBeenCalledTimes(1)

    await wrapper.get('[aria-label="开始录音"]').trigger('click')
    await flushPromises()
    expect(cancel).toHaveBeenCalled()

    audioSocket?.emitJson({
      type: 'audio.start',
      turnId: 'turn-interrupted',
      payload: { fallback: true, text: reply },
    })
    await flushPromises()
    expect(speak).toHaveBeenCalledTimes(1)

    wrapper.unmount()
  })

  it('pauses and resumes an active browser-speech reply', async () => {
    const wrapper = mount(App)
    await flushPromises()
    const audioSocket = FakeWebSocket.instances.find((socket) => socket.url.includes('/ws/audio/'))

    const pauseButton = wrapper.get('[aria-label="暂停朗读"]')
    expect((pauseButton.element as HTMLButtonElement).disabled).toBe(true)

    audioSocket?.emitJson({
      type: 'audio.start',
      turnId: 'turn-pause',
      payload: { fallback: true, text: '这段语音可以暂停。' },
    })
    await flushPromises()

    await wrapper.get('[aria-label="暂停朗读"]').trigger('click')
    expect(pause).toHaveBeenCalledTimes(1)
    await wrapper.get('[aria-label="继续朗读"]').trigger('click')
    expect(resume).toHaveBeenCalledTimes(1)

    wrapper.unmount()
  })

  it('falls back to browser speech when the audio channel is disconnected', async () => {
    const wrapper = mount(App)
    await flushPromises()

    const textSocket = FakeWebSocket.instances.find((socket) => socket.url.includes('/ws/text/'))
    const audioSocket = FakeWebSocket.instances.find((socket) => socket.url.includes('/ws/audio/'))
    const reply = '音频连接不可用时仍然要朗读。'
    audioSocket?.close()

    textSocket?.emitJson({
      type: 'text.completed',
      turnId: 'turn-disconnected',
      payload: { text: reply },
    })
    await flushPromises()

    expect(speak).toHaveBeenCalledTimes(1)
    expect(speak.mock.calls[0]?.[0]).toMatchObject({ text: reply, lang: 'zh-CN' })

    wrapper.unmount()
  })

  it('waits for the server ASR model and submits only captured PCM audio', async () => {
    const wrapper = mount(App)
    await flushPromises()

    const audioSocket = FakeWebSocket.instances.find((socket) => socket.url.includes('/ws/audio/'))
    const startButton = wrapper.get('[aria-label="开始录音"]')
    await startButton.trigger('click')
    await flushPromises()

    expect(navigator.mediaDevices.getUserMedia).toHaveBeenCalledWith({
      audio: expect.objectContaining({ deviceId: { exact: 'mic-realtek' } }),
    })

    const startMessage = JSON.parse(String(audioSocket?.send.mock.calls[0]?.[0])) as {
      type: string
      turnId: string
    }
    expect(startMessage.type).toBe('audio.start')
    expect(wrapper.text()).toContain('正在连接 ASR 模型')

    audioSocket?.emitJson({ type: 'asr.started', turnId: startMessage.turnId, payload: {} })
    await flushPromises()
    expect(wrapper.get('[aria-label="停止录音"]')).toBeTruthy()

    FakeAudioContext.processor?.onaudioprocess?.({
      inputBuffer: { getChannelData: () => new Float32Array(4096).fill(0.1) },
    })
    await wrapper.get('[aria-label="停止录音"]').trigger('click')

    const sentValues = audioSocket?.send.mock.calls.map((call) => call[0]) ?? []
    expect(sentValues.some((value) => value instanceof ArrayBuffer)).toBe(true)
    const commitMessage = sentValues
      .filter((value): value is string => typeof value === 'string')
      .map((value) => JSON.parse(value) as Record<string, unknown>)
      .find((value) => value.type === 'audio.commit')
    expect(commitMessage).toMatchObject({
      type: 'audio.commit',
      turnId: startMessage.turnId,
      clientMetrics: { capturedBytes: expect.any(Number), peak: 0.1, durationMs: expect.any(Number) },
    })
    expect(wrapper.text()).toContain('ASR 正在转写')

    wrapper.unmount()
  })

  it('shows sentence-final ASR results while recording and merges them into the final transcript', async () => {
    const wrapper = mount(App)
    await flushPromises()

    const audioSocket = FakeWebSocket.instances.find((socket) => socket.url.includes('/ws/audio/'))
    audioSocket?.emitJson({
      type: 'asr.sentence',
      turnId: 'voice-turn',
      payload: { transcript: '我想买一双通勤鞋。' },
    })
    await flushPromises()
    expect(wrapper.text()).toContain('我想买一双通勤鞋。')

    audioSocket?.emitJson({
      type: 'asr.completed',
      turnId: 'voice-turn',
      payload: { transcript: '我想买一双通勤鞋。预算五百元。' },
    })
    await flushPromises()

    expect(wrapper.findAll('.message--user')).toHaveLength(1)
    expect(wrapper.text()).toContain('我想买一双通勤鞋。预算五百元。')
    wrapper.unmount()
  })

  it('keeps downstream workflow errors separate from microphone diagnostics', async () => {
    const wrapper = mount(App)
    await flushPromises()

    const audioSocket = FakeWebSocket.instances.find((socket) => socket.url.includes('/ws/audio/'))
    audioSocket?.emitJson({
      type: 'asr.completed',
      turnId: 'voice-turn-workflow-error',
      payload: { transcript: '我想买一个电水壶。' },
    })
    audioSocket?.emitJson({
      type: 'audio.error',
      turnId: 'voice-turn-workflow-error',
      payload: {
        stage: 'workflow',
        message: '会话已关闭，无法继续操作',
        receivedBytes: 4096,
        clientMetrics: { peak: 0.12, durationMs: 1200 },
      },
    })
    await flushPromises()

    expect(wrapper.text()).toContain('会话已关闭，无法继续操作')
    expect(wrapper.text()).toContain('导购处理失败，请重试')
    expect(wrapper.text()).not.toContain('后端没有收到麦克风音频')
    wrapper.unmount()
  })

  it('uses the microphone diagnostic only for an explicit empty ASR capture', async () => {
    const wrapper = mount(App)
    await flushPromises()

    const audioSocket = FakeWebSocket.instances.find((socket) => socket.url.includes('/ws/audio/'))
    audioSocket?.emitJson({
      type: 'audio.error',
      turnId: 'voice-turn-empty-capture',
      payload: { stage: 'asr', message: 'ASR 未识别到有效语音', receivedBytes: 0, clientMetrics: {} },
    })
    await flushPromises()

    expect(wrapper.text()).toContain('后端没有收到麦克风音频，请检查 Chrome 麦克风权限')
    wrapper.unmount()
  })

  it('creates a catalog order without linking a browser-local session', async () => {
    window.location.hash = '#/browse'
    const product = catalogProduct()
    const fetchMock = vi.fn(async (input: RequestInfo | URL, _init?: RequestInit) => {
      if (String(input).endsWith('/catalog/products')) {
        return new Response(JSON.stringify({ items: [product] }), {
          status: 200,
          headers: { 'Content-Type': 'application/json' },
        })
      }
      return new Response(JSON.stringify({ items: [] }), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      })
    })
    vi.stubGlobal('fetch', fetchMock)

    const wrapper = mount(App)
    await flushPromises()

    const buyButton = wrapper.findAll('button').find((button) => button.text().trim() === '购买')
    if (!buyButton) throw new Error('Catalog buy button was not rendered')
    await buyButton.trigger('click')
    await flushPromises()

    const orderRequest = fetchMock.mock.calls.find(
      ([input, init]) => String(input).endsWith('/orders') && init?.method === 'POST',
    )
    if (!orderRequest) throw new Error('Catalog order request was not sent')
    const body = JSON.parse(String(orderRequest[1]?.body)) as Record<string, unknown>
    expect(body).toMatchObject({
      productId: product.id,
      quantity: 1,
      idempotencyKey: expect.stringMatching(/^web-catalog-/),
    })
    expect(body).not.toHaveProperty('sessionId')
    expect(body).not.toHaveProperty('sourceTurnId')
    wrapper.unmount()
  })

  it('blocks a second catalog checkout while the first request is in flight', async () => {
    window.location.hash = '#/browse'
    const product = catalogProduct()
    let resolveOrder: ((response: Response) => void) | undefined
    const fetchMock = vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input)
      if (url.endsWith('/catalog/products')) {
        return Promise.resolve(
          new Response(JSON.stringify({ items: [product] }), {
            status: 200,
            headers: { 'Content-Type': 'application/json' },
          }),
        )
      }
      if (url.endsWith('/orders') && init?.method === 'POST') {
        return new Promise<Response>((resolve) => {
          resolveOrder = resolve
        })
      }
      return Promise.resolve(
        new Response(JSON.stringify({ items: [] }), {
          status: 200,
          headers: { 'Content-Type': 'application/json' },
        }),
      )
    })
    vi.stubGlobal('fetch', fetchMock)

    const wrapper = mount(App)
    await flushPromises()
    const buyButton = wrapper.findAll('button').find((button) => button.text().trim() === '购买')
    if (!buyButton) throw new Error('Catalog buy button was not rendered')

    await buyButton.trigger('click')
    await flushPromises()
    await buyButton.trigger('click')
    await flushPromises()

    const orderRequests = fetchMock.mock.calls.filter(
      ([input, init]) => String(input).endsWith('/orders') && init?.method === 'POST',
    )
    expect(orderRequests).toHaveLength(1)
    expect((buyButton.element as HTMLButtonElement).disabled).toBe(true)

    resolveOrder?.(
      new Response(JSON.stringify({}), {
        status: 201,
        headers: { 'Content-Type': 'application/json' },
      }),
    )
    await flushPromises()
    await nextTick()
    expect(window.location.hash).toBe('#/orders')
    expect(wrapper.text()).toContain('我的订单')
    wrapper.unmount()
  })

  it('reuses a catalog idempotency key when the user retries a failed request', async () => {
    window.location.hash = '#/browse'
    const product = catalogProduct()
    let orderAttempts = 0
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input)
      if (url.endsWith('/catalog/products')) {
        return new Response(JSON.stringify({ items: [product] }), {
          status: 200,
          headers: { 'Content-Type': 'application/json' },
        })
      }
      if (url.endsWith('/orders') && init?.method === 'POST') {
        orderAttempts += 1
        if (orderAttempts === 1) {
          return new Response(JSON.stringify({ detail: '暂时无法创建订单' }), {
            status: 503,
            headers: { 'Content-Type': 'application/json' },
          })
        }
        return new Response(JSON.stringify({}), {
          status: 201,
          headers: { 'Content-Type': 'application/json' },
        })
      }
      return new Response(JSON.stringify({ items: [] }), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      })
    })
    vi.stubGlobal('fetch', fetchMock)

    const wrapper = mount(App)
    await flushPromises()
    const buyButton = wrapper.findAll('button').find((button) => button.text().trim() === '购买')
    if (!buyButton) throw new Error('Catalog buy button was not rendered')

    await buyButton.trigger('click')
    await flushPromises()
    await buyButton.trigger('click')
    await flushPromises()

    const orderRequests = fetchMock.mock.calls.filter(
      ([input, init]) => String(input).endsWith('/orders') && init?.method === 'POST',
    )
    expect(orderRequests).toHaveLength(2)
    const keys = orderRequests.map(([, init]) => {
      const body = JSON.parse(String(init?.body)) as Record<string, unknown>
      return body.idempotencyKey
    })
    expect(keys[0]).toBe(keys[1])
    wrapper.unmount()
  })

  it('blocks rapid text submissions until the current turn completes', async () => {
    const wrapper = mount(App)
    await flushPromises()
    const textSocket = FakeWebSocket.instances.find((socket) => socket.url.includes('/ws/text/'))
    const input = wrapper.get('[aria-label="导购消息"]')
    const sendButton = wrapper.get('.voice-send-button')

    await input.setValue('我想买一双通勤鞋')
    await sendButton.trigger('click')
    await flushPromises()
    await sendButton.trigger('click')
    await flushPromises()

    const submissions = textSocket?.send.mock.calls
      .map(([value]) => (typeof value === 'string' ? JSON.parse(value) as Record<string, unknown> : null))
      .filter((value): value is Record<string, unknown> => value?.type === 'turn.submit')
    expect(submissions).toHaveLength(1)
    expect((sendButton.element as HTMLButtonElement).disabled).toBe(true)

    const turnId = String(submissions?.[0]?.turnId)
    textSocket?.emitJson({
      type: 'text.completed',
      turnId,
      payload: { text: '好的，我来为你筛选。' },
    })
    await flushPromises()
    expect((sendButton.element as HTMLButtonElement).disabled).toBe(false)
    wrapper.unmount()
  })
})
