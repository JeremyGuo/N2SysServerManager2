import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { safeRedirect, createUnauthorizedHandler } from '../src/authNavigation.js'
import { createDraftStore } from '../src/drafts.js'
import { ref, reactive, computed } from 'vue'
import { profileFields, validateProfileSave } from '../src/profileForm.js'
import { canRequestSudo, validateSudoReason, usageBlockedReason, MAX_SELECTION } from '../src/usage.js'

function view(name, expose, context = {}) {
  const text = readFileSync(new URL(`../src/views/${name}.vue`, import.meta.url), 'utf8')
  const script = text.match(/<script setup>([\s\S]*?)<\/script>/)[1].replace(/^import .*$/gm, '')
  const errors = [], messages = [], unmount = []
  const store = context.drafts || createDraftStore()
  const ctx = { ref, reactive, computed, onMounted: () => {}, onBeforeUnmount: fn => unmount.push(fn),
    drafts: store, profileFields, validateProfileSave, canRequestSudo, validateSudoReason, usageBlockedReason, MAX_SELECTION,
    reportError: error => errors.push(error), ElMessage: { success: message => messages.push(message) },
    useRoute: () => ({ params: { id: '7' }, query: {} }), useRouter: () => ({ replace: async () => {} }),
    safeRedirect, ...context }
  const state = new Function(...Object.keys(ctx), script + `\nreturn { ${expose} }`)(...Object.values(ctx))
  return { ...state, errors, messages, unmount: () => unmount.forEach(fn => fn()), store }
}
const good = data => new Response(JSON.stringify(data))

test('redirect allowlist blocks external, protocol-relative, encoded and untrusted targets', () => {
  for (const path of ['/', '/servers', '/apply', '/devices', '/management', '/profile/7', '/server/42']) assert.equal(safeRedirect(path), path)
  for (const path of ['https://evil.test', '//evil.test', '/\\evil.test', '/%2f%2fevil.test', '/%5cevil.test', '/login', '/register', '/unknown', '/apply?redirect=//evil.test', '/servers#evil', '/server/../login', '/server/0', '/server/1\n', ['//evil.test'], null, undefined]) assert.equal(safeRedirect(path), '/')
})
test('401 navigation deduplicates concurrent events and excludes public auth pages and 403', async () => {
  let finish
  const replacements = []
  const store = createDraftStore()
  const route = { name: 'Profile', path: '/profile/7', fullPath: '/profile/7?token=secret' }
  const router = { currentRoute: { value: route }, replace: target => { replacements.push(target); return new Promise(resolve => { finish = () => { router.currentRoute.value = { name: 'Login' }; resolve() } }) } }
  const on401 = createUnauthorizedHandler(router, store)
  const task = on401({ status: 401 })
  await on401({ status: 401 })
  assert.equal(replacements.length, 1)
  assert.deepEqual(replacements[0], { name: 'Login', query: { expired: '1', redirect: '/profile/7' } })
  finish(); await task
  await on401({ status: 401 })
  router.currentRoute.value = { name: 'Register' }; await on401({ status: 401 })
  router.currentRoute.value = route
  await on401({ status: 403 }); await on401({ status: 401, suppressed: true })
  assert.equal(replacements.length, 1)
  assert.equal(store.epoch, 1)
})
test('drafts allowlist non-secret fields, remain inaccessible during expiry, reject stale identity and discard for different users/logout', () => {
  const store = createDraftStore()
  store.confirmUser(7)
  const before = store.epoch
  store.save('profile', 7, { realname: 'draft', mail: 'mail', public_key: 'ssh-public', password: 'secret', old_password: 'secret', token: 'secret' })
  store.save('apply', 7, { selectedIds: [1, 2, 2, '3'], reason: 'compute', needSudo: true, token: 'secret' })
  assert.deepEqual(store.restore('profile', 7), { realname: 'draft', mail: 'mail', public_key: 'ssh-public' })
  assert.deepEqual(store.restore('apply', 7), { selectedIds: [1, 2], reason: 'compute', needSudo: true })
  store.expire()
  assert.equal(store.restore('profile', 7), null)
  assert.equal(store.confirmUser(7, before), false)
  store.save('profile', 7, { realname: 'stale overwrite' })
  store.confirmUser(7)
  assert.equal(store.restore('profile', 7).realname, 'draft')
  assert.equal(store.restore('profile', 8), null)
  store.confirmUser(8)
  assert.equal(store.restore('profile', 8), null)
  store.confirmUser(7)
  assert.equal(store.restore('profile', 7), null)
  store.save('profile', 7, { realname: 'new' }); store.logout(); store.confirmUser(7)
  assert.equal(store.restore('profile', 7), null)
})
test('Profile no-change and required-current-password messages are persistent and never POST', async () => {
  const calls = []
  const state = view('Profile', 'user, currentPassword, newPassword, confirmPassword, fetchProfile, saveChanges, saveMessage', { apiFetch: async url => { calls.push(url); return good(url === '/api/user/me' ? { id: 7, realname: 'original', mail: 'old', public_key: 'pub' } : []) } })
  await state.fetchProfile()
  await state.saveChanges()
  assert.equal(state.saveMessage.value, 'No changes to save.')
  state.user.realname = 'changed'
  await state.saveChanges()
  assert.match(state.saveMessage.value, /Current password is required/)
  state.newPassword.value = 'new'; state.confirmPassword.value = 'different'
  await state.saveChanges()
  assert.match(state.saveMessage.value, /do not match/)
  assert.ok(calls.every(url => url !== '/api/user/update'))
})
test('Profile expiry saves nonsecret fields before unmount, clears all passwords, restores only authenticated same user', async () => {
  const store = createDraftStore()
  let id = 7
  const context = { drafts: store, apiFetch: async url => good(url === '/api/user/me' ? { id, realname: 'original', mail: 'old', public_key: 'pub' } : []) }
  const expose = 'user, currentPassword, newPassword, confirmPassword, fetchProfile, draftRestored'
  const state = view('Profile', expose, context)
  await state.fetchProfile()
  Object.assign(state.user, { realname: 'draft name', mail: 'draft mail', public_key: 'draft pub' })
  state.currentPassword.value = state.newPassword.value = state.confirmPassword.value = 'secret'
  store.expire(); state.unmount()
  assert.equal(state.currentPassword.value, '')
  assert.equal(state.newPassword.value, '')
  assert.equal(state.confirmPassword.value, '')
  assert.equal(store.restore('profile', 7), null)
  const restored = view('Profile', expose, context)
  assert.equal(restored.user.realname, '')
  await restored.fetchProfile()
  assert.equal(restored.user.realname, 'draft name')
  assert.equal(restored.draftRestored.value, true)
  assert.equal(restored.currentPassword.value, '')
  store.expire(); restored.unmount(); id = 8
  const different = view('Profile', expose, context)
  await different.fetchProfile()
  assert.equal(different.user.realname, 'original')
  assert.equal(different.draftRestored.value, false)
})
test('Profile save failure preserves draft and inline error and does not reload', async () => {
  const calls = []
  const state = view('Profile', 'user, currentPassword, fetchProfile, saveChanges, saveMessage, saving', { apiFetch: async url => { calls.push(url); if (url === '/api/user/update') throw Object.assign(new Error('Incorrect current password'), { status: 403 }); return good(url === '/api/user/me' ? { id: 7, realname: 'original' } : []) } })
  await state.fetchProfile(); calls.length = 0
  state.user.realname = 'keep me'; state.currentPassword.value = 'wrong'
  await state.saveChanges()
  assert.deepEqual(calls, ['/api/user/update'])
  assert.equal(state.user.realname, 'keep me')
  assert.equal(state.saveMessage.value, 'Incorrect current password')
  assert.equal(state.saving.value, false)
  assert.equal(state.messages.length, 0)
})
test('ApplyMachines expiry preserves selection/reason/needSudo, revalidates selection after same-user authentication', async () => {
  const store = createDraftStore()
  const context = { drafts: store, apiFetch: async url => good(url === '/api/user/me' ? { id: 7 } : [{ id: 1, gateway: false, usage: {} }]) }
  const expose = 'selectedIds, reason, needSudo, fetchDevices, draftRestored'
  const state = view('ApplyMachines', expose, context)
  await state.fetchDevices()
  state.selectedIds.value = [1, 2]; state.reason.value = 'keep this'; state.needSudo.value = true
  store.expire(); state.unmount()
  const restored = view('ApplyMachines', expose, context)
  assert.equal(restored.reason.value, '')
  await restored.fetchDevices()
  assert.deepEqual(restored.selectedIds.value, [1])
  assert.equal(restored.reason.value, 'keep this')
  assert.equal(restored.needSudo.value, true)
  assert.equal(restored.draftRestored.value, true)
})
test('Login successful auth uses safe router replace, clears password; failed auth stays with error', async () => {
  for (const target of ['/apply', '//evil.test']) {
    const paths = [], calls = []
    const state = view('Login', 'user, signin, loading', { useRoute: () => ({ query: { redirect: target, expired: '1' } }), useRouter: () => ({ replace: async path => paths.push(path) }), apiFetch: async (url, options) => { calls.push({ url, options }); return url === '/api/auth/login' ? new Response(null, { status: 204 }) : good({ id: 7 }) } })
    state.user.password = 'secret'
    await state.signin()
    assert.deepEqual(paths, [target === '/apply' ? '/apply' : '/'])
    assert.deepEqual(calls.map(c => c.url), ['/api/auth/login', '/api/user/me'])
    assert.equal(state.user.password, '')
  }
  const paths = []
  const failed = view('Login', 'user, signin, loading', { useRouter: () => ({ replace: async path => paths.push(path) }), apiFetch: async () => { throw Object.assign(new Error('Wrong password'), { status: 401 }) } })
  failed.user.password = 'secret'; await failed.signin()
  assert.equal(failed.user.password, '')
  assert.equal(failed.loading.value, false)
  assert.deepEqual(paths, [])
  assert.equal(failed.errors.length, 1)
})
test('password reveal and expired banner are visible; no fetch override, reload or persistent draft storage', () => {
  const source = name => readFileSync(new URL(`../src/${name}`, import.meta.url), 'utf8')
  assert.match(source('views/Profile.vue'), /currentPassword" show-password/)
  assert.match(source('views/Profile.vue'), /Current password is required|current password is required/)
  assert.match(source('views/Login.vue'), /session has expired/)
  for (const path of ['views/Login.vue', 'App.vue', 'drafts.js', 'authNavigation.js']) assert.doesNotMatch(source(path), /window\.location|location\.reload|(?:local|session)Storage|window\.fetch\s*=/)
})

test('Management Reject confirms only verifying users, posts once, refreshes only success, keeps persistent 409 errors', async () => {
  for (const outcome of ['cancel', 'close', 'success', '409']) {
    const calls = [], dialogs = []
    const state = view('Management', 'currentUser, loading, rejectUser, rejectError, users', {
      ElMessageBox: { confirm: async (...args) => { dialogs.push(args); if (['cancel', 'close'].includes(outcome)) throw outcome } },
      apiFetch: async (url, options) => { calls.push({ url, options }); if (url.endsWith('/reject')) { if (outcome === '409') throw Object.assign(new Error('Registration has related account records'), { status: 409 }); return new Response(null, { status: 204 }) }; return good([]) }
    })
    state.currentUser.value = { id: 1, is_admin: true }
    const row = { id: 12, username: 'login-name', realname: 'Real Name', status: 'verifying' }
    state.users.value = [row]
    await state.rejectUser({ ...row, status: 'active' })
    await state.rejectUser({ ...row, status: 'inactive' })
    assert.equal(dialogs.length, 0)
    await Promise.all([state.rejectUser(row), state.rejectUser(row)])
    assert.equal(dialogs.length, 1)
    assert.match(dialogs[0][0], /Real Name/)
    assert.match(dialogs[0][0], /pending registration.*Active accounts cannot be deleted/)
    assert.equal(state.loading.value, false)
    if (['cancel', 'close'].includes(outcome)) { assert.deepEqual(calls, []); assert.equal(state.errors.length, 0) }
    else {
      assert.equal(calls[0].url, '/api/user/user/12/reject')
      assert.equal(calls[0].options.method, 'POST')
      assert.equal(calls[0].options.credentials, 'include')
      if (outcome === '409') {
        assert.equal(calls.length, 1)
        assert.equal(state.users.value.length, 1)
        assert.match(state.rejectError.value, /related account/)
        assert.equal(state.errors.length, 1)
      } else { assert.equal(calls.length, 2); assert.match(calls[1].url, /\/api\/user\/users/); assert.deepEqual(state.users.value, []) }
    }
    state.currentUser.value.is_admin = false
    await state.rejectUser(row)
    assert.equal(dialogs.length, 1)
  }
})
const sudoRow = () => ({ id: 1, host: 'lab', gateway: false, usage: { has_account: true, has_sudo: false, my_usage: { id: 61, status: 'active' }, my_sudo_request: null } })
const sudoExpose = 'devices, currentUser, authenticated, sudoDialog, sudoReason, sudoError, actionBusy, openSudo, submitSudo, cancelSudo'
function sudoReady(state, row) { state.currentUser.value = { id: 7 }; state.authenticated.value = true; state.devices.value = [row] }
test('sudo eligibility fails closed for unknown permission and excludes gateway, inactive usage and users without account', () => {
  assert.equal(canRequestSudo(sudoRow()), true)
  for (const status of ['pending', 'ended', 'rejected', 'cancelled']) { const row = sudoRow(); row.usage.my_usage.status = status; assert.equal(canRequestSudo(row), false) }
  for (const has_sudo of [true, undefined, null]) { const row = sudoRow(); row.usage.has_sudo = has_sudo; assert.equal(canRequestSudo(row), false) }
  const gateway = sudoRow(); gateway.gateway = true; assert.equal(canRequestSudo(gateway), false)
  const absent = sudoRow(); absent.usage.my_usage = null; assert.equal(canRequestSudo(absent), false)
  const noAccount = sudoRow(); noAccount.usage.has_account = false; assert.equal(canRequestSudo(noAccount), false)
  assert.match(validateSudoReason(' '), /原因/)
  assert.match(validateSudoReason('a'.repeat(501)), /500/)
  assert.equal(validateSudoReason('😀'.repeat(500)), '')
})
test('sudo upgrade uses own active usage id, reason only, deduplicates clicks without ending usage', async () => {
  const row = sudoRow(), calls = []
  let finish
  const state = view('ApplyMachines', sudoExpose, { apiFetch: async (url, options) => {
    calls.push({ url, options })
    if (url.endsWith('/sudo')) return new Promise(resolve => { finish = () => resolve(new Response(null, { status: 204 })) })
    return good(url === '/api/user/me' ? { id: 7 } : [{ ...row, usage: { ...row.usage, my_sudo_request: { id: 9, reason: 'need packages', create_date: '2026-09-24T01:00:00Z' } } }])
  } })
  sudoReady(state, row)
  state.openSudo(row)
  await state.submitSudo(); assert.equal(calls.length, 0); assert.match(state.sudoError.value, /原因/)
  state.sudoReason.value = '  need packages  '
  const pending = state.submitSudo(); await state.submitSudo()
  assert.equal(calls.length, 1)
  finish(); await pending
  assert.equal(calls[0].url, '/api/usage/61/sudo')
  assert.deepEqual(JSON.parse(calls[0].options.body), { reason: 'need packages' })
  assert.equal(calls[0].options.credentials, 'include')
  assert.equal(calls[0].options.method, 'POST')
  assert.equal(state.sudoDialog.value, false)
  assert.equal(state.sudoReason.value, '')
  assert.equal(state.devices.value[0].usage.my_usage.status, 'active')
  assert.equal(state.devices.value[0].usage.my_sudo_request.id, 9)
  assert.ok(calls.every(call => !/\/end|\/account\//.test(call.url)))
})
test('sudo failures keep dialog/reason/active usage and pending request; no refresh or success', async () => {
  for (const status of [409, 403, 500]) {
    const row = sudoRow(), calls = []
    const state = view('ApplyMachines', sudoExpose, { apiFetch: async url => { calls.push(url); throw Object.assign(new Error(`Failed ${status}`), { status }) } })
    sudoReady(state, row); state.openSudo(row); state.sudoReason.value = 'keep reason'
    await state.submitSudo()
    assert.deepEqual(calls, ['/api/usage/61/sudo'])
    assert.equal(state.sudoDialog.value, true)
    assert.equal(state.sudoReason.value, 'keep reason')
    assert.match(state.sudoError.value, /Failed/)
    assert.equal(state.actionBusy.value, null)
    assert.equal(row.usage.my_usage.status, 'active')
    assert.equal(state.messages.length, 0)
    row.usage.my_sudo_request = { id: 9, reason: 'pending' }
    await state.cancelSudo(row)
    assert.equal(calls.at(-1), '/api/usage/61/sudo/cancel')
    assert.equal(row.usage.my_sudo_request.id, 9)
    assert.equal(state.messages.length, 0)
  }
})
test('sudo cancel posts no body and refreshes pending state without touching usage/access permissions', async () => {
  const row = sudoRow(), calls = []
  row.usage.my_sudo_request = { id: 9, reason: 'packages', create_date: '2026-09-24T01:00:00Z' }
  const state = view('ApplyMachines', sudoExpose, { apiFetch: async (url, options) => { calls.push({ url, options }); return url.endsWith('/cancel') ? new Response(null, { status: 204 }) : good(url === '/api/user/me' ? { id: 7 } : [sudoRow()]) } })
  sudoReady(state, row)
  state.openSudo(row); assert.equal(state.sudoDialog.value, false)
  await state.cancelSudo(row)
  assert.equal(calls[0].url, '/api/usage/61/sudo/cancel')
  assert.equal(calls[0].options.method, 'POST')
  assert.equal(calls[0].options.body, undefined)
  assert.equal(state.devices.value[0].usage.my_usage.status, 'active')
  assert.equal(state.devices.value[0].usage.has_account, true)
  assert.equal(state.devices.value[0].usage.has_sudo, false)
  assert.equal(state.devices.value[0].usage.my_sudo_request, null)
})

test('ApplyMachines restored draft survives inventory failure, another expiry and retry', async () => {
  const store = createDraftStore()
  store.confirmUser(7)
  store.save('apply', 7, { selectedIds: [1], reason: 'retained reason', needSudo: true })
  store.expire()
  let failList = true
  const context = { drafts: store, apiFetch: async url => { if (url === '/api/usage/devices' && failList) throw new Error('Unavailable'); return good(url === '/api/user/me' ? { id: 7 } : [{ id: 1, gateway: false, usage: {} }]) } }
  const expose = 'selectedIds, reason, needSudo, fetchDevices, authenticated'
  const state = view('ApplyMachines', expose, context)
  await state.fetchDevices()
  assert.equal(state.reason.value, 'retained reason')
  store.expire()
  assert.equal(state.authenticated.value, false)
  state.unmount(); failList = false
  const restored = view('ApplyMachines', expose, context)
  await restored.fetchDevices()
  assert.equal(restored.reason.value, 'retained reason')
  assert.deepEqual(restored.selectedIds.value, [1])
})
test('late user/me success from before expiry cannot authenticate or overwrite restored drafts', async () => {
  for (const name of ['ApplyMachines', 'Profile']) {
    const store = createDraftStore()
    store.confirmUser(7)
    store.save('profile', 7, { realname: 'retained' })
    let finish
    const state = view(name, name === 'Profile' ? 'fetchProfile, currentUser' : 'fetchDevices, currentUser', { drafts: store, apiFetch: async () => new Promise(resolve => { finish = () => resolve(good({ id: 8, realname: 'other user' })) }) })
    const pending = name === 'Profile' ? state.fetchProfile() : state.fetchDevices()
    store.expire(); finish(); await pending
    assert.equal(state.currentUser.value.id, null)
    assert.equal(store.restore('profile', 7), null)
    store.confirmUser(7)
    assert.equal(store.restore('profile', 7).realname, 'retained')
  }
})

test('leaving Management while reject confirmation is open prevents the later POST', async () => {
  const calls = []
  let finish
  const state = view('Management', 'currentUser, rejectUser', {
    ElMessageBox: { confirm: () => new Promise(resolve => { finish = resolve }) },
    apiFetch: async url => { calls.push(url); return good([]) }
  })
  state.currentUser.value = { id: 1, is_admin: true }
  const pending = state.rejectUser({ id: 2, status: 'verifying', username: 'pending' })
  state.unmount(); finish(); await pending
  assert.deepEqual(calls, [])
})
test('Profile retry/account refresh does not discard an unsaved nonsecret edit', async () => {
  const state = view('Profile', 'user, fetchProfile, currentPassword', { apiFetch: async url => good(url === '/api/user/me' ? { id: 7, realname: 'original' } : []) })
  await state.fetchProfile()
  state.user.realname = 'keep this draft'
  await state.fetchProfile()
  assert.equal(state.user.realname, 'keep this draft')
  state.store.logout(); state.unmount(); state.store.confirmUser(7)
  assert.equal(state.store.restore('profile', 7), null)
})

test('Profile key revision survives expiry with the edit so a newer gateway key cannot be silently overwritten', async () => {
  const store = createDraftStore()
  let revision = 'old-revision'
  let publicKey = 'old-public'
  let submitted
  const context = { drafts: store, apiFetch: async (url, options) => {
    if (url === '/api/user/update') { submitted = JSON.parse(options.body); throw Object.assign(new Error('Key list changed'), { status: 409 }) }
    return good(url === '/api/user/me' ? { id: 7, realname: 'original', public_key: publicKey, public_key_revision: revision } : [])
  } }
  const expose = 'user, currentPassword, fetchProfile, saveChanges, saveMessage'
  const first = view('Profile', expose, context)
  await first.fetchProfile()
  first.user.public_key = 'old-public\nmanual-edit'
  store.expire(); first.unmount()
  revision = 'new-revision'; publicKey += '\ngateway-added'
  const second = view('Profile', expose, context)
  await second.fetchProfile()
  second.currentPassword.value = 'test-current'
  await second.saveChanges()
  assert.equal(submitted.public_key_revision, 'old-revision')
  assert.equal(second.user.public_key, 'old-public\nmanual-edit')
  assert.equal(second.saveMessage.value, 'Key list changed')
})
