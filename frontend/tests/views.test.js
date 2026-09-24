import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { ref, reactive } from 'vue'
import { createDraftStore } from '../src/drafts.js'
import { profileFields, validateProfileSave } from '../src/profileForm.js'
import { interfaceLabel, resolveInterface } from '../src/selection.js'

// Run the actual script-setup handlers with stubbed lifecycle/router/network boundaries.
// No DOM or extra test framework is needed for these state/mutation regression tests.
function view(name, exposed, apiFetch) {
  const text = readFileSync(new URL(`../src/views/${name}.vue`, import.meta.url), 'utf8')
  const script = text.match(/<script setup>([\s\S]*?)<\/script>/)[1].replace(/^import .*$/gm, '')
  const messages = []
  const route = { params: { id: '1' } }
  const messagesProxy = new Proxy({}, { get: (_, type) => message => messages.push({ type, message }) })
  const setup = new Function('onBeforeUnmount', 'drafts', 'profileFields', 'validateProfileSave', 'ref', 'reactive', 'watch', 'onMounted', 'useRoute', 'useRouter', 'ElMessage', 'apiFetch', 'reportError', 'interfaceLabel', 'resolveInterface', script + `\nreturn { ${exposed} }`)
  const state = setup(() => {}, createDraftStore(), profileFields, validateProfileSave, ref, reactive, () => {}, () => {}, () => route, () => ({ replace: async () => {} }), messagesProxy, apiFetch, () => {}, interfaceLabel, resolveInterface)
  return { ...state, messages, route }
}
const good = data => Promise.resolve(new Response(JSON.stringify(data)))

test('interface resolution never confuses eth1 with eth10 or duplicate PCI interfaces', () => {
  const interfaces = [{ id: 1, interface: 'eth1', pci_address: '01:00.0' }, { id: 2, interface: 'eth10', pci_address: '02:00.0' }, { id: 3, interface: 'eth1', pci_address: '03:00.0' }]
  assert.equal(resolveInterface(interfaces, 'eth1'), null)
  assert.equal(resolveInterface(interfaces, interfaceLabel(interfaces[1])).id, 2)
  assert.equal(resolveInterface(interfaces, interfaceLabel(interfaces[2]), 1), null)
  assert.equal(resolveInterface(interfaces, interfaceLabel(interfaces[2]), 3).id, 3)
  assert.equal(resolveInterface([{ id: 1, interface: 'eth1' }, { id: 2, interface: 'eth1' }], 'eth1'), null)
})
test('server and interface Enter+blur submit a tag once', async () => {
  const calls = []
  const state = view('ServerInfo', 'currentUser, server, show_new_server_tag, new_server_tag_value, confirmServerTagInput, old_tag_row_obj, new_tag_value, confirmTagInput', async (url, options) => { calls.push(url); return new Response(JSON.stringify({ id: calls.length, tag: JSON.parse(options.body).tag })) })
  state.currentUser.value.is_admin = true
  state.server.id = 1
  state.show_new_server_tag.value = true
  state.new_server_tag_value.value = 'gpu'
  await Promise.all([state.confirmServerTagInput(), state.confirmServerTagInput()])
  assert.equal(calls.length, 1)
  assert.equal(state.server.tags.length, 1)
  const row = reactive({ id: 12, tags: [], show_new_tag: true })
  state.old_tag_row_obj.value = row
  state.new_tag_value.value = 'uplink'
  await Promise.all([state.confirmTagInput(row), state.confirmTagInput(row)])
  assert.equal(calls.length, 2)
  assert.equal(row.tags.length, 1)
})
test('refresh POST queues work without reloading or claiming collection is complete', async () => {
  const calls = []
  const state = view('ServerInfo', 'currentUser, server, refreshServer, refreshQueued, refreshing', async (url, options) => { calls.push({ url, options }); return new Response(null, { status: 204 }) })
  state.currentUser.value.is_admin = true
  state.server.id = 7
  await state.refreshServer()
  await state.refreshServer()
  assert.equal(calls.length, 1)
  assert.equal(calls[0].url, '/api/server/refresh')
  assert.equal(calls[0].options.method, 'POST')
  assert.deepEqual(JSON.parse(calls[0].options.body), { server_id: 7 })
  assert.equal(state.refreshQueued.value, true)
  assert.equal(state.refreshing.value, false)
  assert.match(state.messages[0].message, /queued/)
})
test('failed device connection stays open and never reloads/succeeds; loading resets', async () => {
  let calls = 0
  const state = view('Devices', 'currentUser, selectedPort, showPortDialog, saving, savePortChanges', async () => { calls++; throw new Error('Permission denied') })
  state.currentUser.value.is_admin = true
  state.selectedPort.value = { id: 1 }
  state.showPortDialog.value = true
  await state.savePortChanges()
  assert.equal(calls, 1)
  assert.equal(state.showPortDialog.value, true)
  assert.equal(state.saving.value, false)
  assert.equal(state.messages.filter(m => m.type === 'success').length, 0)
})
test('host edits clear stale IDs and late autocomplete responses are ignored', async () => {
  let finish
  const state = view('Devices', 'showPortDialog, selectedServerId, selectedTargetId, targetInterfacesPort, clearHostPort, handleHostSelectPort', () => new Promise(resolve => { finish = resolve }))
  state.showPortDialog.value = true
  const pending = state.handleHostSelectPort({ id: 5, value: 'host-a' })
  state.selectedTargetId.value = 9
  state.clearHostPort()
  finish(new Response('{"interfaces":[{"id":9,"interface":"eth1"}]}'))
  await pending
  assert.equal(state.selectedServerId.value, null)
  assert.equal(state.selectedTargetId.value, null)
  assert.deepEqual(state.targetInterfacesPort.value, [])
})
test('Profile uses authenticated ID and correct admin-only account endpoints', async () => {
  const calls = []
  const state = view('Profile', 'currentUser, user, currentPassword, fetchProfile, saveChanges, revokeAccount, onSwitchSudo', async (url, options) => { calls.push({ url, options }); return good(url === '/api/user/me' ? { id: 8, is_admin: true } : []) })
  await state.fetchProfile()
  state.user.realname = 'Updated name'
  state.currentPassword.value = 'current-password'
  await state.saveChanges()
  const update = calls.find(call => call.url === '/api/user/update')
  assert.equal(JSON.parse(update.options.body).id, 8)
  await state.onSwitchSudo({ id: 3, is_sudo: false }, true)
  await state.revokeAccount({ id: 3 })
  assert.equal(calls.find(call => call.url === '/api/account/3/sudo').options.method, 'PUT')
  assert.equal(calls.find(call => call.url === '/api/account/3/revoke').options.method, 'PUT')
  state.currentUser.value.is_admin = false
  const count = calls.length
  await state.revokeAccount({ id: 3 })
  assert.equal(calls.length, count)
})
test('failed refresh and failed tag add never claim success or discard a draft', async () => {
  const state = view('ServerInfo', 'currentUser, server, refreshServer, refreshQueued, refreshing, show_new_server_tag, new_server_tag_value, confirmServerTagInput, addingServerTag', async () => { throw new Error('Server is unavailable') })
  state.currentUser.value.is_admin = true
  state.server.id = 1
  await assert.rejects(state.refreshServer())
  assert.equal(state.refreshQueued.value, false)
  assert.equal(state.refreshing.value, false)
  state.show_new_server_tag.value = true
  state.new_server_tag_value.value = 'keep this draft'
  await state.confirmServerTagInput()
  assert.equal(state.new_server_tag_value.value, 'keep this draft')
  assert.equal(state.show_new_server_tag.value, true)
  assert.equal(state.addingServerTag.value, false)
  assert.deepEqual(state.server.tags, [])
  assert.equal(state.messages.length, 0)
})
test('failed sudo toggle leaves displayed permission unchanged', async () => {
  const state = view('Profile', 'currentUser, onSwitchSudo, accountBusy', async () => { throw new Error('Forbidden') })
  state.currentUser.value.is_admin = true
  const account = { id: 3, is_sudo: false }
  await state.onSwitchSudo(account, true)
  assert.equal(account.is_sudo, false)
  assert.equal(state.accountBusy.value, null)
})
test('management failures release loading and prevent list refresh after rejected writes', async () => {
  const calls = []
  const state = view('Management', 'currentUser, loading, approveApp', async url => { calls.push(url); throw new Error('Denied') })
  state.currentUser.value.is_admin = true
  await assert.rejects(state.approveApp(12))
  assert.equal(state.loading.value, false)
  assert.deepEqual(calls, ['/api/app/12/approve'])
})
