import test, { afterEach } from 'node:test'
import assert from 'node:assert/strict'
import { apiFetch, reportError, subscribeErrors, dismissError, installGlobalErrorHandlers } from '../src/api.js'
const originalFetch = globalThis.fetch
let errors = []
subscribeErrors(value => { errors = value })
afterEach(() => { globalThis.fetch = originalFetch; [...errors].forEach(error => dismissError(error.id)) })

test('returns a native Response with intact success JSON and clones', async () => {
  globalThis.fetch = async () => new Response('{"ok":true}', { headers: { 'x-request-id': 'ok-1' } })
  const response = await apiFetch('/api/test')
  assert.ok(response instanceof Response)
  assert.deepEqual(await response.clone().json(), { ok: true })
  assert.deepEqual(await response.json(), { ok: true })
  assert.equal(errors.length, 0)
})
test('HTTP errors show FastAPI string detail, method, sanitized URL and request ID; never body', async () => {
  globalThis.fetch = async () => new Response(JSON.stringify({ detail: 'Permission denied' }), { status: 403, headers: { 'X-Request-ID': 'req-123' } })
  await assert.rejects(apiFetch('/api/action?token=top-secret', { method: 'POST', body: '{"password":"private"}' }), error => {
    assert.equal(error.status, 403)
    assert.equal(error.requestId, 'req-123')
    reportError(error, 'Later catch')
    return true
  })
  assert.equal(errors.length, 1)
  assert.equal(errors[0].message, 'Permission denied')
  assert.equal(errors[0].method, 'POST')
  assert.equal(errors[0].url, '/api/action?[query omitted]')
  assert.doesNotMatch(JSON.stringify(errors), /top-secret|private|password/)
})
test('FastAPI validation arrays show field and cause but no input or ctx', async () => {
  globalThis.fetch = async () => new Response(JSON.stringify({ detail: [{ loc: ['body', 'mail'], msg: 'Invalid email', input: 'private-input', ctx: { password: 'secret' } }], request_id: 'body-request-id' }), { status: 422 })
  await assert.rejects(apiFetch('/api/register'))
  assert.equal(errors[0].message, 'body.mail: Invalid email')
  assert.equal(errors[0].requestId, 'body-request-id')
  assert.doesNotMatch(JSON.stringify(errors), /private-input|secret/)
})
test('HTML/empty HTTP errors use useful status fallback, not markup', async () => {
  globalThis.fetch = async () => new Response('<html>internal private trace</html>', { status: 502 })
  await assert.rejects(apiFetch('/api/test'))
  assert.match(errors[0].message, /upstream server/i)
  assert.doesNotMatch(JSON.stringify(errors), /trace|html/)
})
test('invalid success JSON and cloned JSON report once with request context', async () => {
  globalThis.fetch = async () => new Response('private non-json content', { headers: { 'x-request-id': 'parse-1' } })
  const response = await apiFetch('/api/json')
  await assert.rejects(response.clone().json(), error => { reportError(error); return true })
  assert.equal(errors.length, 1)
  assert.match(errors[0].message, /invalid or empty JSON/)
  assert.equal(errors[0].requestId, 'parse-1')
  assert.equal(errors[0].url, '/api/json')
  assert.doesNotMatch(JSON.stringify(errors), /private non-json/)
})
test('204 succeeds without forcing JSON parsing', async () => {
  globalThis.fetch = async () => new Response(null, { status: 204 })
  assert.equal((await apiFetch('/api/account/1/sudo', { method: 'PUT' })).status, 204)
  assert.equal(errors.length, 0)
})
test('network errors reject and persist until dismissed', async () => {
  globalThis.fetch = async () => { throw new TypeError('Failed to fetch') }
  await assert.rejects(apiFetch('/api/offline'), /Network request failed/)
  assert.equal(errors.length, 1)
  await new Promise(resolve => setTimeout(resolve, 5))
  assert.equal(errors.length, 1)
  dismissError(errors[0].id)
  assert.equal(errors.length, 0)
})
test('timeout aborts transport and rejects even if transport ignores signal', async () => {
  let signal
  globalThis.fetch = async (_url, options) => { signal = options.signal; return new Promise(() => {}) }
  await assert.rejects(apiFetch('/api/slow', { timeoutMs: 5 }), /timed out/)
  assert.equal(signal.aborted, true)
  assert.equal(errors.length, 1)
})
test('JSON body timeout rejects instead of leaving loading stuck', async () => {
  globalThis.fetch = async () => new Response(new ReadableStream({ start() {} }))
  const response = await apiFetch('/api/slow-json', { timeoutMs: 5 })
  await assert.rejects(response.json(), /Response timed out/)
  assert.equal(errors.length, 1)
})
test('explicit expected 401 suppression still rejects, but 403 is never suppressed by it', async () => {
  globalThis.fetch = async () => new Response('{"detail":"Not authenticated"}', { status: 401 })
  await assert.rejects(apiFetch('/api/user/me', { suppressStatuses: [401] }), error => { reportError(error); return error.suppressed })
  assert.equal(errors.length, 0)
  globalThis.fetch = async () => new Response(null, { status: 403 })
  await assert.rejects(apiFetch('/api/user/me', { suppressStatuses: [401] }))
  assert.equal(errors.length, 1)
})
test('equivalent errors deduplicate while retaining latest request ID and recurrence count', async () => {
  let id = 0
  globalThis.fetch = async () => new Response('{"detail":"Unavailable"}', { status: 503, headers: { 'x-request-id': String(++id) } })
  await assert.rejects(apiFetch('/api/test'))
  await assert.rejects(apiFetch('/api/test'))
  assert.equal(errors.length, 1)
  assert.equal(errors[0].count, 2)
  assert.equal(errors[0].requestId, '2')
})
test('Vue handler reports unexpected errors and deduplicates propagated API errors', async () => {
  const app = { config: {} }
  installGlobalErrorHandlers(app)
  const error = new Error('Render failed')
  app.config.errorHandler(error, null, 'render')
  app.config.errorHandler(error, null, 'render')
  assert.equal(errors.length, 1)
  assert.match(errors[0].context, /Vue: render/)
})
test('caller cancellation is reported and reaches response consumption after headers', async () => {
  const controller = new AbortController()
  let transportSignal
  globalThis.fetch = async (_url, options) => {
    transportSignal = options.signal
    return new Response(new ReadableStream({ start(stream) {
      options.signal.addEventListener('abort', () => stream.error(new DOMException('Aborted', 'AbortError')))
    } }))
  }
  const response = await apiFetch('/api/cancel-json', { signal: controller.signal })
  controller.abort()
  assert.equal(transportSignal.aborted, true)
  await assert.rejects(response.json())
  assert.equal(errors.length, 1)
})

test('only unsuppressed 401 emits authentication events; subscribers never replace API rejection', async () => {
  const { subscribeUnauthorized } = await import('../src/api.js')
  const observed = []
  const unsubscribe = subscribeUnauthorized(error => observed.push(error))
  globalThis.fetch = async () => new Response('{"detail":"Expired"}', { status: 401 })
  await assert.rejects(apiFetch('/api/user/me', { suppressStatuses: [401] }))
  assert.equal(observed.length, 0)
  await assert.rejects(apiFetch('/api/private'), error => error === observed[0] && error.status === 401)
  globalThis.fetch = async () => new Response(null, { status: 403 })
  await assert.rejects(apiFetch('/api/private'))
  assert.equal(observed.length, 1)
  unsubscribe()
  const stop = subscribeUnauthorized(() => { throw new Error('observer failure') })
  globalThis.fetch = async () => new Response(null, { status: 401 })
  await assert.rejects(apiFetch('/api/private'), error => error.status === 401)
  stop()
})
