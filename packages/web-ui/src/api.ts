type RuntimeImportMeta = ImportMeta & {
  env?: Record<string, string | undefined>
}

const runtimeEnv = (import.meta as RuntimeImportMeta).env

export const apiBaseUrl = runtimeEnv?.VITE_API_BASE_URL ?? 'http://localhost:8000/api/v1'
export const textWsBaseUrl = runtimeEnv?.VITE_TEXT_WS_URL ?? 'ws://localhost:8000/ws/text'
export const audioWsBaseUrl = runtimeEnv?.VITE_AUDIO_WS_URL ?? 'ws://localhost:8000/ws/audio'
export const merchantWebUrl = runtimeEnv?.VITE_MERCHANT_WEB_URL ?? 'http://localhost:5174/'
export const platformWebUrl = runtimeEnv?.VITE_PLATFORM_WEB_URL ?? 'http://localhost:5175/'

export async function requestJson<T>(path: string, init: RequestInit = {}): Promise<T> {
  const headers = new Headers(init.headers)
  if (init.body && !headers.has('Content-Type')) headers.set('Content-Type', 'application/json')
  const response = await fetch(`${apiBaseUrl}${path}`, { ...init, headers })
  if (!response.ok) {
    const body = (await response.json().catch(() => null)) as { detail?: string } | null
    throw new Error(body?.detail ?? `请求失败（${response.status}）`)
  }
  if (response.status === 204) return undefined as T
  return (await response.json()) as T
}
