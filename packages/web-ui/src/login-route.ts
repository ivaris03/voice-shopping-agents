export const loginPath = '/login'

export function moveToLoginPath() {
  if (typeof window === 'undefined') return
  if (window.location.pathname === loginPath && !window.location.search && !window.location.hash) return
  window.history.replaceState(null, '', loginPath)
}

export function leaveLoginPath() {
  if (typeof window === 'undefined' || window.location.pathname !== loginPath) return
  window.history.replaceState(null, '', '/')
}
