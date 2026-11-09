const defaultApiBase = import.meta.env.VITE_API_URL || (import.meta.env.DEV ? '' : 'http://127.0.0.1:8000')

export function useApiClient(token) {
  let refreshPromise = null

  function csrfToken() {
    return document.cookie.split('; ').find(value => value.startsWith('query_pilot_csrf='))?.split('=')[1] || ''
  }

  async function refreshAccess() {
    if (!refreshPromise) {
      refreshPromise = (async () => {
        try {
          const response = await fetch(`${defaultApiBase}/api/v1/auth/refresh`, { method: 'POST', credentials: 'include' })
          if (!response.ok) return false
          const data = await response.json()
          token.value = data.access_token
          return true
        } catch {
          return false
        } finally {
          refreshPromise = null
        }
      })()
    }
    return refreshPromise
  }

  async function authFetch(url, options = {}) {
    const method = (options.method || 'GET').toUpperCase()
    const send = () => fetch(url, {
      ...options,
      credentials: 'include',
      headers: {
        ...(options.headers || {}),
        Authorization: `Bearer ${token.value}`,
        ...(['POST', 'PUT', 'PATCH', 'DELETE'].includes(method) ? {'X-CSRF-Token': csrfToken()} : {})
      }
    })
    let response = await send()
    if (response.status === 401 && await refreshAccess()) response = await send()
    return response
  }

  return { api: defaultApiBase, authFetch, csrfToken, refreshAccess }
}
