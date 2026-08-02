// @vitest-environment jsdom

import { flushPromises, mount } from '@vue/test-utils'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

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

describe('assistant reply audio coordination', () => {
  const speak = vi.fn()
  const cancel = vi.fn()
  const pause = vi.fn()
  const resume = vi.fn()

  beforeEach(() => {
    FakeWebSocket.instances = []
    localStorage.clear()
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
})
