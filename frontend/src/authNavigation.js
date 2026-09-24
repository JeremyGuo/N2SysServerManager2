import { subscribeUnauthorized, reportError } from './api.js'
import { drafts } from './drafts.js'

export const isPublicAuthRoute = route => ['Login', 'Register'].includes(route?.name)
// Strict allowlist of SPA destinations. Reject scheme-relative, encoded paths,
// controls, backslashes and arbitrary query/hash data (including nested redirects).
export function safeRedirect(value) {
  if (typeof value !== 'string' || !/^\/(?:|servers|apply|devices|management|profile\/[1-9]\d*|server\/[1-9]\d*)$/.test(value)) return '/'
  return value
}
export function createUnauthorizedHandler(router, store = drafts) {
  let redirecting = false
  return async error => {
    const route = router.currentRoute.value
    if (error.status !== 401 || error.suppressed || isPublicAuthRoute(route) || redirecting) return
    redirecting = true
    try {
      store.expire()
      await router.replace({ name: 'Login', query: { expired: '1', redirect: safeRedirect(route.path) } })
    } catch (cause) { reportError(cause, 'Session expiry navigation') }
    finally { redirecting = false }
  }
}
export function installAuthNavigation(router) {
  return subscribeUnauthorized(createUnauthorizedHandler(router))
}
