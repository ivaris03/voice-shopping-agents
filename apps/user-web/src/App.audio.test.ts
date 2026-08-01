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

describe('assistant reply audio coordination', () => {
  const speak = vi.fn()
  const cancel = vi.fn()

  beforeEach(() => {
    FakeWebSocket.instances = []
    localStorage.clear()
    speak.mockClear()
    cancel.mockClear()

    vi.stubGlobal('WebSocket', FakeWebSocket)
    vi.stubGlobal('SpeechSynthesisUtterance', FakeSpeechSynthesisUtterance)
    Object.defineProperty(window, 'speechSynthesis', {
      configurable: true,
      value: { cancel, speak },
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
})
