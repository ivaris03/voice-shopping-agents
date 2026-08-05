type RuntimeImportMeta = ImportMeta & {
  env?: Record<string, string | undefined>
}

const runtimeEnv = (import.meta as RuntimeImportMeta).env
const accessTokenStorageKey = 'voice-shopping-access-token'

export const apiBaseUrl = runtimeEnv?.VITE_API_BASE_URL ?? 'http://localhost:8000/api/v1'
export const textWsBaseUrl = runtimeEnv?.VITE_TEXT_WS_URL ?? 'ws://localhost:8000/ws/text'
export const audioWsBaseUrl = runtimeEnv?.VITE_AUDIO_WS_URL ?? 'ws://localhost:8000/ws/audio'
export const merchantWebUrl = runtimeEnv?.VITE_MERCHANT_WEB_URL ?? 'http://localhost:5174/'
export const platformWebUrl = runtimeEnv?.VITE_PLATFORM_WEB_URL ?? 'http://localhost:5175/'

export type UserRole = 'customer' | 'merchant' | 'platform'

export interface AuthenticatedUser {
  id: string
  email: string
  displayName: string
  role: UserRole
}

interface LoginResponse {
  accessToken: string
  tokenType: 'bearer'
  expiresIn: number
  user: AuthenticatedUser
}

let accessToken = readStoredAccessToken()

function readStoredAccessToken() {
  if (typeof window === 'undefined') return ''
  try {
    return window.sessionStorage.getItem(accessTokenStorageKey) ?? ''
  } catch {
    return ''
  }
}

export function getAccessToken() {
  return accessToken
}

export function clearAccessToken() {
  accessToken = ''
  if (typeof window === 'undefined') return
  try {
    window.sessionStorage.removeItem(accessTokenStorageKey)
  } catch {
    // Storage can be disabled by the browser; the in-memory value is already cleared.
  }
}

export function setAccessToken(token: string) {
  accessToken = token
  if (typeof window === 'undefined') return
  try {
    window.sessionStorage.setItem(accessTokenStorageKey, token)
  } catch {
    // The access token remains available until this page is closed.
  }
}

export async function requestJson<T>(path: string, init: RequestInit = {}): Promise<T> {
  const headers = new Headers(init.headers)
  if (accessToken && !headers.has('Authorization')) headers.set('Authorization', `Bearer ${accessToken}`)
  if (init.body && !headers.has('Content-Type')) headers.set('Content-Type', 'application/json')
  const response = await fetch(`${apiBaseUrl}${path}`, { ...init, headers })
  if (!response.ok) {
    const body = (await response.json().catch(() => null)) as { detail?: string } | null
    throw new Error(body?.detail ?? `请求失败（${response.status}）`)
  }
  if (response.status === 204) return undefined as T
  return (await response.json()) as T
}

export async function login(phone: string, password: string): Promise<AuthenticatedUser> {
  const response = await requestJson<LoginResponse>('/auth/login', {
    method: 'POST',
    body: JSON.stringify({ phone, password }),
  })
  setAccessToken(response.accessToken)
  return response.user
}

export function getCurrentUser() {
  return requestJson<AuthenticatedUser>('/auth/me')
}
