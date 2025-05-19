<template>
  <div>
    <div style="width: 90%; margin: auto;">
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
        <el-table :data="pendingApps" style="width:100%">
          <el-table-column prop="username" label="User"/>
          <el-table-column prop="host" label="Host"/>
          <el-table-column prop="need_sudo" label="Needs Admin">
            <template #default="{ row }"><el-switch v-model="row.need_sudo" disabled/></template>
          </el-table-column>
          <el-table-column prop="create_date" label="Date"/>
          <el-table-column label="Actions">
            <template #default="{ row }">
              <el-button size="small" type="success" @click="approveApp(row.id)" :loading="loading">Approve</el-button>
              <el-button size="small" type="danger" @click="rejectApp(row.id)" :loading="loading">Reject</el-button>
            </template>
          </el-table-column>
        </el-table>
      </el-card>

      <el-card class="card-spacing">
        <h3>All Users</h3>
        <el-select v-model="userFilter" placeholder="Filter by status" @change="fetchUsers" style="width:200px;margin-bottom:10px;">
          <el-option label="All" value="all"/>
          <el-option label="Verifying" value="verifying"/>
          <el-option label="Active" value="active"/>
          <el-option label="Inactive" value="inactive"/>
        </el-select>
        <el-table :data="users" style="width:100%">
          <el-table-column prop="username" label="Username"/>
          <el-table-column prop="status" label="Status"/>
          <el-table-column prop="is_admin" label="Admin">
            <template #default="{ row }">
              <el-switch v-model="row.is_admin" disabled/>
            </template>
          </el-table-column>
          <el-table-column label="Actions">
            <template #default="{ row }">
              <el-button size="small" type="success" v-if="row.status==='verifying'"
                         @click="approveUser(row.id)" :loading="loading">Approve</el-button>
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
import { ref, onMounted } from 'vue'
import { useRouter } from 'vue-router'

const router = useRouter()
const loading = ref(false)
const newServer = ref({ host:'', port:22, isGateway:false })
const newSwitch = ref({ name:'', numRow:1, numCol:1 })
const switches = ref([])

const pendingApps = ref([])
const users = ref([])
const userFilter = ref('all')
const currentUser = ref({ id:null, is_admin:false })

async function fetchCurrentUser() {
  const res = await fetch('/api/user/me',{ credentials:'include' })
  if (res.ok) {
    const u = await res.json()
    currentUser.value.id = u.id
    currentUser.value.is_admin = u.is_admin
    if (!u.is_admin) router.push({ name:'Summary' })
  }
}

async function addServer() {
  loading.value = true
  try {
    await fetch('/api/server/add',{
      method:'POST',
      headers:{'Content-Type':'application/json'},
      credentials:'include',
      body:JSON.stringify(newServer.value)
    })
    newServer.value = { host:'', port:22, isGateway:false }
  } catch(e){ console.error(e) }
  loading.value = false
}

async function fetchPending() {
  const res = await fetch('/api/application/pendings',{ credentials:'include' })
  if(res.ok) pendingApps.value = await res.json()
}

async function approveApp(id){
  loading.value = true
  await fetch(`/api/application/${id}/approve`,{ method:'POST', credentials:'include' })
  await fetchPending()
  loading.value = false
}

async function rejectApp(id){
  loading.value = true
  await fetch(`/api/application/${id}/reject`,{ method:'POST', credentials:'include' })
  await fetchPending()
  loading.value = false
}

async function fetchUsers() {
  const res = await fetch(`/api/user/users?user_status=${userFilter.value}`, { credentials:'include' })
  if (res.ok) users.value = await res.json()
}

async function approveUser(id) {
  loading.value = true
  await fetch(`/api/user/user/${id}/approve`, { method:'POST', credentials:'include' })
  await fetchUsers()
  loading.value = false
}

async function revokeAdminUser(id) {
  loading.value = true
  await fetch(`/api/user/user/${id}/revoke-admin`, { method:'POST', credentials:'include' })
  await fetchUsers()
  loading.value = false
}

async function graduateUser(id) {
  loading.value = true
  await fetch(`/api/user/user/${id}/graduate`, { method:'POST', credentials:'include' })
  await fetchUsers()
  loading.value = false
}

async function grantAdminUser(id) {
  loading.value = true
  await fetch(`/api/user/user/${id}/grant-admin`, { method:'POST', credentials:'include' })
  await fetchUsers()
  loading.value = false
}

async function restoreUser(id) {
  loading.value = true
  await fetch(`/api/user/user/${id}/restore`, { method:'POST', credentials:'include' })
  await fetchUsers()
  loading.value = false
}

async function addSwitch() {
  loading.value = true
  try {
    await fetch('/api/switch/add', {
      method:'POST',
      headers:{ 'Content-Type':'application/json' },
      credentials:'include',
      body: JSON.stringify({
        name: newSwitch.value.name,
        num_row: newSwitch.value.numRow,
        num_col: newSwitch.value.numCol
      })
    })
    newSwitch.value = { name:'', numRow:1, numCol:1 }
    await fetchSwitches()
  } catch(e) { console.error(e) }
  loading.value = false
}

async function fetchSwitches() {
  const res = await fetch('/api/switch/list', { credentials:'include' })
  if (res.ok) switches.value = await res.json()
}

onMounted(async ()=>{
  await fetchCurrentUser()
  await fetchPending()
  await fetchUsers()
  await fetchSwitches()
})
</script>
