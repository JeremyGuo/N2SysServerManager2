import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { ref, computed } from 'vue'
import { deviceMatches } from '../src/hardware.js'
import { createDraftStore } from '../src/drafts.js'
import { MAX_SELECTION, usageBlockedReason, validateApplication, canRequestSudo, validateSudoReason } from '../src/usage.js'

// Exercise the actual script-setup handlers, with only browser/API boundaries stubbed.
function view(fetch, confirm = async () => {}) {
  const source = readFileSync(new URL('../src/views/ApplyMachines.vue', import.meta.url), 'utf8')
  const script = source.match(/<script setup>([\s\S]*?)<\/script>/)[1].replace(/^import .*$/gm, '')
  const errors = [], messages = [], dialogs = []
  const context = { ref, computed, onMounted: () => {}, onBeforeUnmount: () => {}, drafts: createDraftStore(), canRequestSudo, validateSudoReason, apiFetch: fetch, reportError: error => errors.push(error),
    ElMessage: { success: message => messages.push(message) }, ElMessageBox: { confirm: async (...args) => { dialogs.push(args); return confirm(...args) } },
    deviceMatches, MAX_SELECTION, usageBlockedReason, validateApplication }
  const setup = new Function(...Object.keys(context), script + '\nreturn { devices, currentUser, authenticated, loading, loadFailed, saving, actionBusy, selectedIds, selectedDevices, reason, needSudo, query, busy, filteredDevices, eligibleVisible, allVisibleSelected, fetchDevices, toggleDevice, toggleVisible, submitApplication, usageAction }')
  return { ...setup(...Object.values(context)), errors, messages, dialogs }
}
const good = data => new Response(JSON.stringify(data))
const device = (id, extra = {}) => ({ id, host: `machine-${id}`, gateway: false, status: 'active', tags: [], hardware: {}, usage: { my_usage: null, has_account: false, pending_count: 0, active_users: [], account_count: 0 }, ...extra })
const ready = state => { state.authenticated.value = true; state.currentUser.value = { id: 7 }; state.devices.value = [device(1), device(2)] }

test('only own active/pending usage and gateways block selection; existing accounts and other/legacy pending do not', () => {
  assert.ok(usageBlockedReason(device(1, { gateway: true })))
  for (const status of ['active', 'pending']) assert.ok(usageBlockedReason(device(1, { usage: { my_usage: { id: 1, status } } })))
  for (const status of ['ended', 'cancelled', 'rejected']) assert.equal(usageBlockedReason(device(1, { usage: { my_usage: { id: 1, status } } })), '')
  assert.equal(usageBlockedReason(device(1, { usage: { my_usage: null, has_account: true, pending_count: 3 } })), '')
})
test('required reason, Unicode length, 50-device limit, duplicate/stale selection validation', () => {
  const list = Array.from({ length: 51 }, (_, index) => device(index + 1))
  assert.match(validateApplication([], 'reason', list), /至少/)
  assert.match(validateApplication([1], '  ', list), /原因/)
  assert.match(validateApplication([1], 'a'.repeat(501), list), /500/)
  assert.equal(validateApplication([1], '😀'.repeat(500), list), '')
  assert.match(validateApplication(list.map(d => d.id), 'reason', list), /50/)
  assert.equal(validateApplication(list.slice(0, 50).map(d => d.id), 'reason', list), '')
  assert.match(validateApplication([1, 1], 'reason', list), /重复/)
  assert.match(validateApplication([99], 'reason', list), /不可申请/)
})
test('load authenticates via session, fetches protected list with credentials and always releases loading', async () => {
  const calls = []
  const state = view(async (url, options) => { calls.push({ url, options }); return good(url === '/api/user/me' ? { id: 7 } : [device(1)]) })
  await state.fetchDevices()
  assert.deepEqual(calls.map(c => c.url), ['/api/user/me', '/api/usage/devices'])
  assert.ok(calls.every(c => c.options.credentials === 'include'))
  assert.equal(state.authenticated.value, true)
  assert.equal(state.devices.value.length, 1)
  assert.equal(state.loading.value, false)
  assert.equal(state.loadFailed.value, false)
})
test('unauthenticated or failed list cannot be mutated, but failed reload retains draft/selection', async () => {
  const calls = []
  const state = view(async url => { calls.push(url); throw Object.assign(new Error('Login required'), { status: 401 }) })
  ready(state)
  state.reason.value = 'keep reason'
  state.selectedIds.value = [1]
  await state.fetchDevices()
  await state.submitApplication()
  state.toggleDevice(device(2), true)
  assert.deepEqual(calls, ['/api/user/me'])
  assert.equal(state.authenticated.value, false)
  assert.equal(state.loading.value, false)
  assert.equal(state.loadFailed.value, true)
  assert.equal(state.reason.value, 'keep reason')
  assert.deepEqual(state.selectedIds.value, [1])
  assert.equal(state.errors.length, 1)
})
test('failed device-list fetch and invalid JSON release loading without discarding the last list', async () => {
  for (const result of [() => { throw new Error('503') }, () => new Response('{bad json'), () => good({ devices: [] })]) {
    const state = view(async url => url === '/api/user/me' ? good({ id: 7 }) : result())
    ready(state)
    await state.fetchDevices()
    assert.equal(state.devices.value.length, 2)
    assert.equal(state.loadFailed.value, true)
    assert.equal(state.loading.value, false)
    assert.equal(state.errors.length, 1)
  }
})
test('checkbox and select-all preserve hidden selection, ignore ineligible rows and enforce max50', () => {
  const state = view(async () => { throw new Error('unexpected fetch') })
  ready(state)
  state.devices.value.push(device(3, { gateway: true }))
  state.toggleDevice(state.devices.value[2], true)
  assert.deepEqual(state.selectedIds.value, [])
  state.toggleVisible(true)
  assert.deepEqual(state.selectedIds.value, [1, 2])
  state.query.value = 'machine-1'
  state.toggleVisible(false)
  assert.deepEqual(state.selectedIds.value, [2])
  state.toggleVisible(true)
  assert.deepEqual(state.selectedIds.value, [2, 1])
  state.query.value = ''
  state.devices.value = Array.from({ length: 51 }, (_, index) => device(index + 1))
  state.toggleVisible(true)
  assert.deepEqual(state.selectedIds.value, [2, 1])
  assert.match(state.errors.at(-1).message, /50/)
  state.selectedIds.value = state.devices.value.slice(0, 50).map(d => d.id)
  state.toggleDevice(device(51), true)
  assert.equal(state.selectedIds.value.length, 50)
})
test('failed application (including legacy-pending 409) does not refresh, clear input or change selection', async () => {
  for (const status of [409, 403, 500]) {
    const calls = []
    const state = view(async (url, options) => { calls.push({ url, options }); throw Object.assign(new Error('An existing application is pending; contact an administrator'), { status }) })
    ready(state)
    state.selectedIds.value = [1, 2]
    state.reason.value = 'model training'
    state.needSudo.value = true
    await state.submitApplication()
    assert.deepEqual(calls.map(c => c.url), ['/api/usage/apply'])
    assert.equal(calls[0].options.credentials, 'include')
    assert.equal(calls[0].options.method, 'POST')
    assert.deepEqual(JSON.parse(calls[0].options.body), { server_ids: [1, 2], need_sudo: true, reason: 'model training' })
    assert.deepEqual(state.selectedIds.value, [1, 2])
    assert.equal(state.reason.value, 'model training')
    assert.equal(state.needSudo.value, true)
    assert.equal(state.saving.value, false)
    assert.equal(state.messages.length, 0)
    assert.equal(state.errors.length, 1)
  }
})
test('successful application clears submitted form only after 2xx and then refreshes list', async () => {
  const calls = []
  const state = view(async (url, options) => {
    calls.push({ url, options })
    return url === '/api/usage/apply' ? new Response(null, { status: 204 }) : good(url === '/api/user/me' ? { id: 7 } : [device(1, { usage: { my_usage: { id: 41, status: 'active' } } })])
  })
  ready(state)
  state.selectedIds.value = [1]
  state.reason.value = '  experiment  '
  await state.submitApplication()
  assert.deepEqual(calls.map(c => c.url), ['/api/usage/apply', '/api/user/me', '/api/usage/devices'])
  assert.equal(JSON.parse(calls[0].options.body).reason, 'experiment')
  assert.deepEqual(state.selectedIds.value, [])
  assert.equal(state.reason.value, '')
  assert.equal(state.saving.value, false)
  assert.equal(state.devices.value[0].usage.my_usage.status, 'active')
  assert.equal(state.messages.length, 1)
})
test('duplicate clicks and validation failures never submit twice or post an invalid request', async () => {
  let finish
  const calls = []
  const state = view(async url => {
    calls.push(url)
    if (url === '/api/usage/apply') return new Promise(resolve => { finish = resolve })
    return good(url === '/api/user/me' ? { id: 7 } : [])
  })
  ready(state)
  await state.submitApplication()
  assert.deepEqual(calls, [])
  state.selectedIds.value = [1]
  state.reason.value = 'test'
  const pending = state.submitApplication()
  await state.submitApplication()
  assert.deepEqual(calls, ['/api/usage/apply'])
  finish(new Response(null, { status: 204 }))
  await pending
  assert.equal(calls.filter(c => c === '/api/usage/apply').length, 1)
})
test('end, confirm and cancel POST own usage id; end confirmation explains permissions, process and SSH retention', async () => {
  for (const action of ['end', 'confirm', 'cancel']) {
    const calls = []
    const state = view(async (url, options) => {
      calls.push({ url, options })
      return url.endsWith(`/${action}`) ? new Response(null, { status: 204 }) : good(url === '/api/user/me' ? { id: 7 } : [])
    })
    ready(state)
    const row = device(1, { usage: { has_account: true, my_usage: { id: 61, status: action === 'cancel' ? 'pending' : 'active' } } })
    await state.usageAction(row, action)
    assert.equal(calls[0].url, `/api/usage/61/${action}`)
    assert.equal(calls[0].options.method, 'POST')
    assert.equal(calls[0].options.credentials, 'include')
    assert.deepEqual(calls.map(c => c.url), [`/api/usage/61/${action}`, '/api/user/me', '/api/usage/devices'])
    assert.ok(calls.every(c => !c.url.includes('/account/')))
    assert.equal(row.usage.has_account, true)
    assert.equal(state.actionBusy.value, null)
    if (action === 'end') {
      assert.match(state.dialogs[0][0], /账号权限保持不变/)
      assert.match(state.dialogs[0][0], /不会终止进程/)
      assert.match(state.dialogs[0][0], /不会撤销 SSH/)
    } else assert.equal(state.dialogs.length, 0)
  }
})
test('cancelled end dialog and failed actions do not write/refresh/claim success; action loading resets', async () => {
  for (const cancellation of ['cancel', 'close']) {
    const calls = []
    const state = view(async url => { calls.push(url); return good([]) }, async () => { throw cancellation })
    ready(state)
    await state.usageAction(device(1, { usage: { my_usage: { id: 4, status: 'active' } } }), 'end')
    assert.deepEqual(calls, [])
    assert.equal(state.actionBusy.value, null)
    assert.equal(state.errors.length, 0)
  }
  const calls = []
  const state = view(async url => { calls.push(url); throw new Error('503') })
  ready(state)
  const row = device(1, { usage: { my_usage: { id: 4, status: 'active' } } })
  await state.usageAction(row, 'confirm')
  assert.deepEqual(calls, ['/api/usage/4/confirm'])
  assert.equal(row.usage.my_usage.status, 'active')
  assert.equal(state.actionBusy.value, null)
  assert.equal(state.messages.length, 0)
  assert.equal(state.errors.length, 1)
})
test('invalid action/state pair is not sent, including legacy my_usage=null', async () => {
  const calls = []
  const state = view(async url => { calls.push(url); return good([]) })
  ready(state)
  await state.usageAction(device(1), 'cancel')
  await state.usageAction(device(1, { usage: { my_usage: { id: 1, status: 'pending' } } }), 'end')
  await state.usageAction(device(1, { usage: { my_usage: { id: 1, status: 'active' } } }), 'cancel')
  await state.usageAction(device(1, { usage: { my_usage: { id: 1, status: 'active' } } }), 'revoke')
  assert.deepEqual(calls, [])
})
