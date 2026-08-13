// @vitest-environment jsdom

import { flushPromises, mount } from '@vue/test-utils'
import { afterEach, beforeEach, describe, expect, it } from 'vitest'

import { LoginGate, clearAccessToken, leaveLoginPath, moveToLoginPath } from '@voice-shopping/web-ui'

describe('login routing', () => {
  beforeEach(() => {
    clearAccessToken()
    window.history.replaceState(null, '', '/#/orders')
  })

  afterEach(() => {
    window.history.replaceState(null, '', '/')
  })

  it('replaces a protected hash URL with /login when no session exists', async () => {
    const wrapper = mount(LoginGate, {
      props: { requiredRole: 'customer', workspaceName: '用户工作台' },
    })

    await flushPromises()

    expect(window.location.pathname).toBe('/login')
    expect(window.location.hash).toBe('')
    wrapper.unmount()
  })

  it('clears the login path before entering a workspace', () => {
    moveToLoginPath()
    leaveLoginPath()

    expect(window.location.pathname).toBe('/')
    expect(window.location.hash).toBe('')
  })

  it.each(['/merchant/', '/platform/'])('keeps the %s workspace prefix while routing to login', (basePath) => {
    window.history.replaceState(null, '', `${basePath}#/catalog`)

    moveToLoginPath()

    expect(window.location.pathname).toBe(`${basePath.slice(0, -1)}/login`)
    expect(window.location.hash).toBe('')

    leaveLoginPath()

    expect(window.location.pathname).toBe(basePath)
  })
})
