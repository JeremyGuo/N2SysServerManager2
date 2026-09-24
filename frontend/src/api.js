// Fetch-compatible API client. Never record request bodies, credentials, or query values.
const listeners = new Set()
const unauthorizedListeners = new Set()
export function subscribeUnauthorized(listener) {
  unauthorizedListeners.add(listener)
  return () => unauthorizedListeners.delete(listener)
}
function emitUnauthorized(error) {
  for (const listener of unauthorizedListeners) {
    // Navigation observers must never change fetch rejection/Response semantics.
    try { Promise.resolve(listener(error)).catch(cause => reportError(cause, 'Authentication navigation')) }
    catch (cause) { reportError(cause, 'Authentication navigation') }
  }
}
const errors = []
const reported = new WeakSet()
let nextId = 1
const DEFAULT_TIMEOUT = 30000

function clean(value, max = 2000) {
  return String(value ?? '').replace(/[\u0000-\u0008\u000b-\u001f]/g, '').slice(0, max)
}
function requestPath(input) {
  try {
    const url = new URL(typeof input === 'string' || input instanceof URL ? input : input.url,
      globalThis.location?.origin || 'http://localhost')
    return url.pathname + (url.search ? '?[query omitted]' : '')
  } catch { return '[unknown URL]' }
}
function detailMessage(detail) {
  if (typeof detail === 'string') return clean(detail)
  if (Array.isArray(detail)) return detail.map(item => {
    if (typeof item === 'string') return clean(item)
    const field = Array.isArray(item?.loc) ? item.loc.map(part => clean(part, 100)).join('.') : ''
    // FastAPI validation errors can contain input/passwords: only use location + message.
    return [field, typeof item?.msg === 'string' ? clean(item.msg) : 'Invalid value'].filter(Boolean).join(': ')
  }).join('; ')
  if (detail && typeof detail.message === 'string') return clean(detail.message)
  return ''
}
function emit() { for (const listener of listeners) listener([...errors]) }
export function subscribeErrors(listener) {
  listeners.add(listener)
  listener([...errors])
  return () => listeners.delete(listener)
}
export function dismissError(id) {
  const index = errors.findIndex(error => error.id === id)
  if (index !== -1) { errors.splice(index, 1); emit() }
}

/** Report once per Error instance; identical active notifications share an occurrence count.
 * context may be a string label, or { context, method, url, status, requestId }.
 */
export function reportError(error, context = {}) {
  if (error && typeof error === 'object') {
    if (reported.has(error) || error.suppressed) return error
    reported.add(error)
  }
  const extra = typeof context === 'string' ? { context } : context
  const record = {
    message: clean(error?.message || (typeof error === 'string' ? error : 'Unexpected application error')),
    context: clean(extra.context || error?.context || '', 200),
    method: clean(extra.method || error?.method || '', 20),
    url: extra.url ? requestPath(extra.url) : (error?.url || ''),
    status: extra.status || error?.status || null,
    requestId: clean(extra.requestId || error?.requestId || '', 200),
  }
  const key = JSON.stringify({ ...record, requestId: '' })
  const existing = errors.find(entry => entry.key === key)
  if (existing) { existing.count += 1; existing.requestId = record.requestId || existing.requestId }
  else errors.push({ ...record, key, id: nextId++, count: 1 })
  emit()
  return error
}

export class ApiError extends Error {
  constructor(message, context = {}, cause) {
    super(message, cause ? { cause } : undefined)
    this.name = 'ApiError'
    Object.assign(this, context)
  }
}

/** apiFetch(input, { ...RequestInit, timeoutMs = 30000, suppressStatuses = [] })
 * Resolves to a native Response only on 2xx. HTTP/network/timeout failures reject.
 * response.json() (and clones) report/reject invalid JSON; 204 callers must not parse JSON.
 * suppressStatuses suppresses UI only, never rejection (expected public /user/me 401).
 */
export async function apiFetch(input, options = {}) {
  const { timeoutMs = DEFAULT_TIMEOUT, suppressStatuses = [], ...init } = options
  const context = { method: (init.method || input?.method || 'GET').toUpperCase(), url: requestPath(input) }
  const controller = new AbortController()
  const externalSignal = init.signal || input?.signal
  let timedOut = false
  // AbortSignal.any preserves fetch's cancellation behavior while consuming a response.
  const supportsAny = typeof AbortSignal.any === 'function'
  const signal = externalSignal && supportsAny ? AbortSignal.any([controller.signal, externalSignal]) : controller.signal
  const abort = () => controller.abort(externalSignal?.reason)
  if (externalSignal && !supportsAny) {
    if (externalSignal.aborted) abort()
    else externalSignal.addEventListener('abort', abort, { once: true })
  }
  let timer
  let timeoutReject
  const timeout = new Promise((_, reject) => { timeoutReject = reject })
  const startTimer = () => {
    if (timeoutMs > 0) timer = setTimeout(() => {
      timedOut = true
      controller.abort()
      timeoutReject(new Error('Request timeout'))
    }, timeoutMs)
  }
  const cleanup = () => {
    clearTimeout(timer)
    if (!supportsAny) externalSignal?.removeEventListener('abort', abort)
  }
  const failure = cause => {
    const message = timedOut ? `Request timed out after ${timeoutMs / 1000}s. The server may be unavailable; check its status before retrying a change.`
      : signal.aborted ? 'Request was cancelled before completion.'
      : `Network request failed${cause?.message ? ': ' + clean(cause.message, 300) : ''}. Check your connection and whether the server is reachable.`
    const error = new ApiError(message, context, cause)
    reportError(error)
    return error
  }
  startTimer()
  try {
    const response = await Promise.race([globalThis.fetch(input, { ...init, signal }), timeout])
    context.status = response.status
    context.requestId = response.headers.get('x-request-id') || response.headers.get('request-id') || ''
    if (!response.ok) {
      let payload
      const text = await Promise.race([response.text(), timeout])
      try { payload = JSON.parse(text) } catch { /* HTML/proxy error pages are not useful or safe to display. */ }
      context.requestId ||= typeof payload?.request_id === 'string' ? payload.request_id : ''
      const detail = detailMessage(payload?.detail) || detailMessage(payload?.message) || detailMessage(payload?.msg)
      const hints = { 401: 'Sign in again to continue.', 403: 'Your account does not have permission for this action.', 404: 'The requested resource was not found.', 409: 'The request conflicts with the current state.', 422: 'The submitted values were not accepted.', 500: 'The server encountered an internal error.', 502: 'The upstream server is unavailable.', 503: 'The service is unavailable.' }
      const error = new ApiError(detail || hints[response.status] || response.statusText || 'Request failed', context)
      error.suppressed = suppressStatuses.includes(response.status)
      if (!error.suppressed) {
        reportError(error)
        if (response.status === 401) emitUnauthorized(error)
      }
      throw error
    }
    cleanup()
    return protectResponse(response, context, timeoutMs, controller)
  } catch (cause) {
    if (cause instanceof ApiError) throw cause
    throw failure(cause)
  } finally { cleanup() }
}

function protectResponse(response, context, timeoutMs, controller) {
  const json = response.json.bind(response)
  const clone = response.clone.bind(response)
  response.json = async () => {
    let timer
    let timedOut = false
    try {
      const timeout = new Promise((_, reject) => {
        if (timeoutMs > 0) timer = setTimeout(() => {
          timedOut = true
          controller.abort()
          reject(new Error('Response timeout'))
        }, timeoutMs)
      })
      return await Promise.race([json(), timeout])
    } catch (cause) {
      // SyntaxError messages may quote response contents; do not expose those contents.
      const message = timedOut ? `Response timed out after ${timeoutMs / 1000}s while reading JSON.`
        : cause instanceof SyntaxError ? 'The server returned invalid or empty JSON; expected a JSON response.'
        : 'Unable to read the JSON response. The connection may have been interrupted or the response was already consumed.'
      const error = new ApiError(message, context, cause)
      reportError(error)
      throw error
    } finally { clearTimeout(timer) }
  }
  response.clone = () => protectResponse(clone(), context, timeoutMs, controller)
  return response
}

export function installGlobalErrorHandlers(app) {
  app.config.errorHandler = (error, _instance, info) => reportError(error, { context: `Vue: ${info}` })
  globalThis.addEventListener?.('unhandledrejection', event => {
    reportError(event.reason, 'Unhandled asynchronous operation')
    event.preventDefault()
  })
  globalThis.addEventListener?.('error', event => {
    reportError(event.error || new Error(event.message || 'A required application resource could not be loaded'), 'Application')
  })
}
