export const loginPath = '/login'

function appBasePath() {
  if (typeof window === 'undefined') return ''
  if (window.location.pathname === '/merchant' || window.location.pathname.startsWith('/merchant/')) return '/merchant'
  if (window.location.pathname === '/platform' || window.location.pathname.startsWith('/platform/')) return '/platform'
  return ''
}

export function moveToLoginPath() {
  if (typeof window === 'undefined') return
  const path = `${appBasePath()}${loginPath}`
  if (window.location.pathname === path && !window.location.search && !window.location.hash) return
  window.history.replaceState(null, '', path)
}

export function leaveLoginPath() {
  if (typeof window === 'undefined') return
  const basePath = appBasePath()
  if (window.location.pathname !== `${basePath}${loginPath}`) return
  window.history.replaceState(null, '', `${basePath || ''}/`)
}
