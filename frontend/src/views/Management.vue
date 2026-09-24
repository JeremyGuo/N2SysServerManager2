<template>
  <div>
    <el-alert v-if="!currentUser.is_admin" title="Administrator access required" type="info" :closable="false" />
    <div v-else style="width: 90%; margin: auto;" v-loading="loading">
      <h2>Management</h2>
      <el-card style="margin-bottom: 20px; margin-top: 20px;">
        <h3>Add New Server</h3>
        <el-form :model="newServer">
          <el-form-item label="Host">
            <el-input v-model="newServer.host"/>
          </el-form-item>
          <el-form-item label="Port">
            <el-input v-model="newServer.port" type="number"/>
          </el-form-item>
          <el-form-item label="Is Gateway">
            <el-switch v-model="newServer.isGateway"/>
          </el-form-item>
          <el-form-item label="Proxy Server">
            <el-select v-model="newServer.proxyServerId" placeholder="Select proxy server">
              <el-option label="None" :value="null"/>
              <el-option v-for="s in servers" :key="s.id" :label="`${s.host}:${s.port}`" :value="s.id"/>
            </el-select>
          </el-form-item>
          <el-form-item>
            <el-button type="primary" @click="addServer" :loading="loading">Add Server</el-button>
          </el-form-item>
        </el-form>
      </el-card>
      
      <el-card style="margin-bottom: 20px;">
        <h3>Add New Switch</h3>
        <el-form ref="form" :model="newSwitch" label-width="140px">
          <el-form-item label="Name">
            <el-input v-model="newSwitch.name"></el-input>
          </el-form-item>
          <el-form-item label="Number of Rows">
            <el-input v-model="newSwitch.numRow" type="number"></el-input>
          </el-form-item>
          <el-form-item label="Number of Columns">
            <el-input v-model="newSwitch.numCol" type="number"></el-input>
          </el-form-item>
          <el-form-item>
            <el-button type="primary" @click="addSwitch" :loading="loading">Add Switch</el-button>
          </el-form-item>
        </el-form>
      </el-card>

      <el-card v-if="currentUser.is_admin" class="card-spacing" style="margin-bottom: 20px;">
        <h3>Pending Applications</h3>
        <el-table :data="pendingApps" empty-text="No pending applications" style="width:100%">
          <el-table-column prop="realname" label="User"/>
          <el-table-column prop="host" label="Host"/>
          <el-table-column prop="kind" label="申请类型"><template #default="{ row }">{{ row.kind === 'sudo' ? 'sudo 权限升级（保留使用登记）' : '访问账号 / 使用登记' }}</template></el-table-column>
          <el-table-column prop="need_sudo" label="Needs Admin">
            <template #default="{ row }"><el-switch v-model="row.need_sudo" disabled/></template>
          </el-table-column>
          <el-table-column label="申请原因" min-width="200"><template #default="{ row }"><span style="white-space:pre-wrap;overflow-wrap:anywhere">{{ row.reason || '未填写（旧版申请）' }}</span></template></el-table-column>
          <el-table-column prop="create_date" label="Date"/>
          <el-table-column label="Actions">
            <template #default="{ row }">
              <el-button size="small" type="success" @click="approveApp(row.id)" :loading="loading">Approve</el-button>
              <el-button size="small" type="danger" @click="rejectApp(row.id)" :loading="loading">Reject</el-button>
            </template>
          </el-table-column>
        </el-table>
      </el-card>

      <el-card class="card-spacing" style="margin-bottom: 20px;">
        <h3>All Users</h3>
        <p>Reject removes a pending registration only. Active accounts cannot be deleted here.</p>
        <el-alert v-if="rejectError" :title="rejectError" type="error" :closable="false" show-icon />
        <el-select v-model="userFilter" placeholder="Filter by status" @change="fetchUsers" style="width:200px;margin-bottom:10px;">
          <el-option label="All" value="all"/>
          <el-option label="Verifying" value="verifying"/>
          <el-option label="Active" value="active"/>
          <el-option label="Inactive" value="inactive"/>
        </el-select>
        <el-table :data="users" empty-text="No users match this status" style="width:100%">
          <el-table-column prop="username" label="Username"/>
          <el-table-column prop="realname" label="Real Name"/>
          <el-table-column prop="status" label="Status"/>
          <el-table-column prop="is_admin" label="Admin">
            <template #default="{ row }">
              <el-switch v-model="row.is_admin" disabled/>
            </template>
          </el-table-column>
          <el-table-column label="Actions">
            <template #default="{ row }">
              <template v-if="row.status==='verifying'">
                <el-button size="small" type="success" @click="approveUser(row.id)" :loading="loading">Approve</el-button>
                <el-button size="small" type="danger" @click="rejectUser(row)" :disabled="loading">Reject Registration</el-button>
              </template>
              <el-button size="small" type="warning" v-else-if="row.status==='active' && row.is_admin"
                         @click="revokeAdminUser(row.id)" :disabled="currentUser.id===row.id" :loading="loading">
                Revoke Admin
              </el-button>
              <template v-else-if="row.status==='active' && !row.is_admin">
                <el-button size="small" type="primary"
                           @click="grantAdminUser(row.id)" :loading="loading">Make Admin</el-button>
                <el-button size="small" type="info"
                           @click="graduateUser(row.id)" :loading="loading">Graduate</el-button>
              </template>
              <el-button size="small" type="info" v-else-if="row.status==='inactive'"
                         @click="restoreUser(row.id)" :loading="loading">Restore</el-button>
            </template>
          </el-table-column>
        </el-table>
      </el-card>
    </div>
  </div>
</template>

<script setup>
import { ref, onMounted, onBeforeUnmount } from 'vue'
import { useRouter } from 'vue-router'
import { apiFetch, reportError } from '../api.js'
import { ElMessageBox } from 'element-plus'
const router = useRouter()
const loading = ref(false)
const newServer = ref({ host: '', port: 22, isGateway: false, proxyServerId: null })
const newSwitch = ref({ name: '', numRow: 1, numCol: 1 })
const switches = ref([])
const servers = ref([])
const pendingApps = ref([])
const users = ref([])
const userFilter = ref('all')
const currentUser = ref({ id: null, is_admin: false })
const rejectError = ref('')
let disposed = false
onBeforeUnmount(() => { disposed = true })
async function runAdmin(action) {
  if (loading.value || !currentUser.value.is_admin) return
  loading.value = true
  try { await action() } finally { loading.value = false }
}
const post = (url, body) => apiFetch(url, { method: 'POST', credentials: 'include', headers: { 'Content-Type': 'application/json' }, ...(body === undefined ? {} : { body: JSON.stringify(body) }) })
async function addServer() {
  await runAdmin(async () => {
    await post('/api/server/add', newServer.value)
    newServer.value = { host: '', port: 22, isGateway: false, proxyServerId: null }
    await fetchServers()
  })
}
async function fetchPending() { pendingApps.value = await (await apiFetch('/api/app/pendings', { credentials: 'include' })).json() }
async function approveApp(id) { await runAdmin(async () => { await post(`/api/app/${id}/approve`); await fetchPending() }) }
async function rejectApp(id) { await runAdmin(async () => { await post(`/api/app/${id}/reject`); await fetchPending() }) }
let userRequest = 0
async function fetchUsers() {
  const request = ++userRequest
  const data = await (await apiFetch(`/api/user/users?user_status=${encodeURIComponent(userFilter.value)}`, { credentials: 'include' })).json()
  if (request === userRequest) users.value = data
}
async function changeUser(id, action) { await runAdmin(async () => { await post(`/api/user/user/${id}/${action}`); await fetchUsers() }) }
async function rejectUser(row) {
  if (row.status !== 'verifying') return
  try {
    await runAdmin(async () => {
      try {
        await ElMessageBox.confirm(`Reject the pending registration for ${row.realname || row.username}? This removes only a pending registration. Active accounts cannot be deleted; registrations with related account records will be refused.`, 'Reject Registration', { type: 'warning', confirmButtonText: 'Reject registration', cancelButtonText: 'Keep registration' })
      } catch (error) { if (error === 'cancel' || error === 'close') return; throw error }
      if (disposed || !currentUser.value.is_admin || row.status !== 'verifying') return
      await post(`/api/user/user/${row.id}/reject`)
      rejectError.value = ''
      await fetchUsers()
    })
  } catch (error) { rejectError.value = error.message; reportError(error) }
}
const approveUser = id => changeUser(id, 'approve')
const revokeAdminUser = id => changeUser(id, 'revoke-admin')
const graduateUser = id => changeUser(id, 'graduate')
const grantAdminUser = id => changeUser(id, 'grant-admin')
const restoreUser = id => changeUser(id, 'restore')
async function addSwitch() {
  await runAdmin(async () => {
    await post('/api/switch/add', { name: newSwitch.value.name, num_row: Number(newSwitch.value.numRow), num_col: Number(newSwitch.value.numCol) })
    newSwitch.value = { name: '', numRow: 1, numCol: 1 }
    await fetchSwitches()
  })
}
async function fetchSwitches() { switches.value = await (await apiFetch('/api/switch/list', { credentials: 'include' })).json() }
async function fetchServers() { servers.value = await (await apiFetch('/api/server/list', { credentials: 'include' })).json() }
onMounted(async () => {
  loading.value = true
  try {
    currentUser.value = await (await apiFetch('/api/user/me', { credentials: 'include' })).json()
    if (!currentUser.value.is_admin) { await router.replace({ name: 'Summary' }); return }
    await Promise.all([fetchPending(), fetchUsers(), fetchSwitches(), fetchServers()])
  } finally { loading.value = false }
})
</script>
