<template>
  <el-container>
    <el-main>
      <el-card class="card-spacing">
        <el-form ref="formRef" :model="user" label-width="150px">
          <el-form-item label="Username" prop="username">
            <el-input v-model="user.username" disabled></el-input>
          </el-form-item>
          <el-form-item label="Account Name" prop="account_name">
            <el-input v-model="user.account_name" disabled></el-input>
          </el-form-item>
          <el-form-item label="Real Name" prop="realname">
            <el-input v-model="user.realname"></el-input>
          </el-form-item>
          <el-form-item label="Email" prop="mail">
            <el-input v-model="user.mail"></el-input>
          </el-form-item>
          <el-form-item label="Public Key">
            <el-input type="textarea" v-model="user.public_key" rows="5"></el-input>
          </el-form-item>
          <el-form-item label="Current Password">
            <el-input type="password" v-model="currentPassword"></el-input>
          </el-form-item>
          <el-form-item label="New Password" prop="new_password">
            <el-input type="password" v-model="newPassword"></el-input>
          </el-form-item>
          <el-form-item label="Confirm New Password" prop="confirm_password">
            <el-input type="password" v-model="confirmPassword"></el-input>
          </el-form-item>
          <el-form-item>
            <el-button type="primary" @click="saveChanges">Save Changes</el-button>
          </el-form-item>
        </el-form>
      </el-card>

      <el-card class="card-spacing">
        <h3>Pending Applications</h3>
        <el-table :data="applications" style="width: 100%">
          <el-table-column prop="host" label="Server Host"></el-table-column>
          <el-table-column label="Needs Admin">
            <template #default="{ row }">
              <el-switch v-model="row.need_sudo" disabled></el-switch>
            </template>
          </el-table-column>
          <el-table-column prop="create_date" label="Created At"></el-table-column>
        </el-table>
        <el-button type="primary" class="button-spacing" @click="showApplicationModal">Submit New Application</el-button>
      </el-card>

      <el-card class="card-spacing">
        <h3>Active Accounts</h3>
        <el-table :data="accounts" style="width: 100%">
          <el-table-column prop="host" label="Host"></el-table-column>
          <el-table-column prop="is_sudo" label="Admin Access">
            <template #default="{ row }">
              <el-switch
                v-model="row.is_sudo"
                :disabled="!currentUser.is_admin || (row.is_gateway && row.is_user_admin)"
                @change="onSwitchSudo(row, $event)" />
            </template>
          </el-table-column>
          <el-table-column prop="last_login_date" label="Last Login"></el-table-column>
          <el-table-column label="Actions">
            <template #default="{ row }">
              <el-button
                type="danger"
                size="mini"
                :disabled="row.is_gateway"
                @click="revokeAccount(row)">
                {{ row.is_gateway ? 'Cannot Revoke' : 'Revoke Access' }}
              </el-button>
            </template>
          </el-table-column>
        </el-table>
      </el-card>

      <el-dialog title="New Application" v-model="applicationModalVisible">
        <el-form :model="newApplication" label-width="160px">
          <el-form-item label="Server Host">
            <el-autocomplete
              v-model="newApplication.host"
              :fetch-suggestions="querySearch"
              placeholder="Search server"
              @select="handleSelect"
              class="full-width-input" />
          </el-form-item>
          <el-form-item label="Needs Admin">
            <el-switch v-model="newApplication.need_sudo" />
          </el-form-item>
        </el-form>
        <template #footer>
          <el-button @click="applicationModalVisible = false">Cancel</el-button>
          <el-button type="primary" @click="submitApplication">Submit</el-button>
        </template>
      </el-dialog>
    </el-main>
  </el-container>
</template>

<script setup>
import { ref, reactive, onMounted } from 'vue'
import { useRouter, useRoute } from 'vue-router'
import { ElMessage } from 'element-plus'

const router = useRouter()
const route = useRoute()
const formRef = ref(null)
const currentUser = ref({ id: route.params.id, is_admin: false })
const user = reactive({ username: '', account_name: '', realname: '', mail: '', public_key: '' })
const currentPassword = ref('')
const newPassword = ref('')
const confirmPassword = ref('')
const applications = ref([])
const accounts = ref([])
const applicationModalVisible = ref(false)
const newApplication = reactive({ host: '', server_id: null, need_sudo: false})
const serverList = ref([])

async function fetchProfile() {
  try {
    const [usrRes, appsRes, accRes, srvRes] = await Promise.all([
      fetch('/api/user/me', { credentials: 'include' }),
      fetch('/api/user/applications', { credentials: 'include' }),
      fetch('/api/user/accounts', { credentials: 'include' }),
      fetch('/api/summary/get', { credentials: 'include' })
    ])
    if (usrRes.ok) {
      const data = await usrRes.json()
      Object.assign(user, data)
      currentUser.value.is_admin = data.is_admin
    }
    if (appsRes.ok) applications.value = await appsRes.json()
    if (accRes.ok) accounts.value = await accRes.json()
    if (srvRes.ok) {
      const list = await srvRes.json()
      serverList.value = list    // keep full objects with id+host
    }
  } catch (err) {
    console.error(err)
  }
}

onMounted(fetchProfile)

function saveChanges() {
  if (newPassword.value !== confirmPassword.value) {
    ElMessage.error('New password and confirm password do not match')
    return
  }
  fetch('/api/user/update', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    credentials: 'include',
    body: JSON.stringify({
      id: currentUser.value.id,
      realname: user.realname,
      mail: user.mail,
      public_key: user.public_key,
      old_password: currentPassword.value,
      new_password: newPassword.value
    })
  }).then(res => {
    if (res.ok) {
      ElMessage.success('Changes saved successfully')
      fetchProfile()
    } else res.json().then(data => ElMessage.error(data.message))
  })
}

function showApplicationModal() {
  applicationModalVisible.value = true
}

function querySearch(query, cb) {
  const results = serverList.value
    .filter(s => s.host.toLowerCase().includes(query.toLowerCase()))
    .map(s => ({ value: s.host, id: s.id }))
  cb(results)
}

function handleSelect(item) {
  newApplication.host = item.value
  newApplication.server_id = item.id
}

function submitApplication() {
  fetch('/api/app/submit', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    credentials: 'include',
    body: JSON.stringify({
      uid: currentUser.value.id,
      server_id: newApplication.server_id,
      need_sudo: newApplication.need_sudo,
      password: newApplication.password
    })
  }).then(res => {
    if (res.ok) {
      applicationModalVisible.value = false
      fetchProfile()
    } else res.json().then(data => ElMessage.error(data.message))
  })
}

function revokeAccount(row) {
  fetch('/account/revoke', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    credentials: 'include',
    body: JSON.stringify({ account_id: row.id })
  }).then(res => {
    if (res.ok) fetchProfile()
    else res.json().then(data => ElMessage.error(data.message))
  })
}

function onSwitchSudo(row, value) {
  fetch('/account/sudo', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    credentials: 'include',
    body: JSON.stringify({ account_id: row.id, is_sudo: value })
  }).then(res => {
    if (res.ok) fetchProfile()
    else res.json().then(data => ElMessage.error(data.message))
  })
}

function gotoHome() {
  router.push({ name: 'Summary' })
}

function gotoProfile(id) {
  router.push({ name: 'Profile', params: { id } })
}

function logout() {
  router.push({ name: 'Login' })
}
</script>

<style scoped>
.card-spacing {
  margin-bottom: 20px;
}
.button-spacing {
  margin-top: 20px;
}
.full-width-input {
  width: 100%;
}
.el-header-button {
  font-size: 16px;
}
</style>
