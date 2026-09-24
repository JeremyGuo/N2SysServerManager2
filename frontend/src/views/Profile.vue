<template>
  <el-container>
    <el-main v-loading="loading">
      <el-alert v-if="loadFailed" title="Profile data could not be fully loaded. See the error details." type="error" :closable="false" />
      <el-button v-if="loadFailed" @click="fetchProfile">Retry</el-button>
      <el-card class="card-spacing">
        <el-alert v-if="saveMessage" :title="saveMessage" type="warning" :closable="false" show-icon />
        <el-alert v-if="draftRestored" title="Your non-secret draft was restored for this account. Re-enter your current password to save." type="info" :closable="false" />
        <el-form ref="formRef" :model="user" :disabled="!currentUser.id || saving" label-width="150px">
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
          <el-form-item label="Current Password" :error="saveMessage.includes('Current password') ? saveMessage : ''">
            <el-input type="password" v-model="currentPassword" show-password autocomplete="current-password" aria-label="Current password (required to save changes)"></el-input>
            <small>Your current password is required for every change, including profile details.</small>
          </el-form-item>
          <el-form-item label="New Password" prop="new_password">
            <el-input type="password" v-model="newPassword" show-password autocomplete="new-password"></el-input>
          </el-form-item>
          <el-form-item label="Confirm New Password" prop="confirm_password">
            <el-input type="password" v-model="confirmPassword" show-password autocomplete="new-password"></el-input>
          </el-form-item>
          <el-form-item>
            <el-button type="primary" @click="saveChanges" :loading="saving">Save Changes</el-button>
          </el-form-item>
        </el-form>
      </el-card>

      <el-card class="card-spacing">
        <h3>Pending Applications · 账号开通申请（含旧版）</h3>
        <p>新申请与使用登记请前往「申请机器」。旧版待审批申请仍由管理员处理；重复申请会提示冲突。</p>
        <el-table :data="applications" :empty-text="loadFailed ? 'Applications could not be loaded' : 'No pending applications'" style="width: 100%">
          <el-table-column prop="host" label="Server Host"></el-table-column>
          <el-table-column label="Needs Admin">
            <template #default="{ row }">
              <el-switch v-model="row.need_sudo" disabled></el-switch>
            </template>
          </el-table-column>
          <el-table-column label="申请原因"><template #default="{row}">{{ row.reason || '未填写（旧版申请）' }}</template></el-table-column>
          <el-table-column prop="create_date" label="Created At"></el-table-column>
        </el-table>
        <el-button type="primary" class="button-spacing" @click="router.push({ name: 'ApplyMachines' })" :disabled="!currentUser.id">申请机器 / 管理我的使用登记</el-button>
      </el-card>

      <el-card class="card-spacing">
        <h3>Active Accounts · 访问权限</h3>
        <p>访问账号不代表当前正在使用。结束使用登记不会撤销这里的 SSH / sudo 权限，也不会终止进程。</p>
        <el-table :data="accounts" :empty-text="loadFailed ? 'Accounts could not be loaded' : 'No active accounts'" style="width: 100%">
          <el-table-column prop="host" label="Host"></el-table-column>
          <el-table-column prop="is_sudo" label="Admin Access">
            <template #default="{ row }">
              <el-switch
                :model-value="row.is_sudo"
                :disabled="!currentUser.is_admin || accountBusy !== null || (row.is_gateway && row.is_user_admin)"
                @change="onSwitchSudo(row, $event)" />
            </template>
          </el-table-column>
          <el-table-column prop="last_login_date" label="Last Activity"><template #default="{row}"><span :title="row.last_login_date">{{ formatActivity(row.last_login_date) }}</span></template></el-table-column>
          <el-table-column label="Actions" v-if="currentUser.is_admin">
            <template #default="{ row }">
              <el-button
                type="danger"
                size="mini"
                :disabled="!currentUser.is_admin || row.is_gateway || accountBusy !== null"
                @click="revokeAccount(row)">
                {{ row.is_gateway ? 'Cannot Revoke' : 'Revoke Access' }}
              </el-button>
            </template>
          </el-table-column>
        </el-table>
      </el-card>
    </el-main>
  </el-container>
</template>

<script setup>
import { ref, reactive, onMounted, onBeforeUnmount } from 'vue'
import { drafts } from '../drafts.js'
import { profileFields, validateProfileSave } from '../profileForm.js'
import { useRouter, useRoute } from 'vue-router'
import { ElMessage } from 'element-plus'
import { apiFetch, reportError } from '../api.js'
import { formatActivity } from '../summaryRows.js'
const router = useRouter()
const route = useRoute()
const formRef = ref(null)
const currentUser = ref({ id: null, is_admin: false })
const user = reactive({ username: '', account_name: '', realname: '', mail: '', public_key: '' })
const currentPassword = ref('')
const newPassword = ref('')
const confirmPassword = ref('')
const applications = ref([])
const accounts = ref([])
const loading = ref(false)
const loadFailed = ref(false)
const saving = ref(false)
const accountBusy = ref(null)
const saveMessage = ref('')
const draftRestored = ref(false)
let original = profileFields(user)
let publicKeyRevision = null
let disposed = false
function clearPasswords() { currentPassword.value = newPassword.value = confirmPassword.value = '' }
function preserveDraft() {
  if (!currentUser.value.id) return
  const fields = profileFields(user)
  if (Object.keys(fields).some(key => fields[key] !== original[key])) drafts.save('profile', currentUser.value.id, { ...fields, public_key_revision: publicKeyRevision })
  else drafts.discard('profile', currentUser.value.id)
}
const unsubscribeDraft = drafts.subscribe(reason => {
  if (reason === 'expiry') preserveDraft()
  clearPasswords()
  currentUser.value = { id: null, is_admin: false }
})
onBeforeUnmount(() => {
  preserveDraft()
  clearPasswords()
  disposed = true
  unsubscribeDraft()
})
async function fetchProfile() {
  preserveDraft()
  loading.value = true
  loadFailed.value = false
  const epoch = drafts.epoch
  try {
    // The route is display-only: never trust it as the identity for an update/application.
    const data = await (await apiFetch('/api/user/me', { credentials: 'include' })).json()
    if (disposed || !drafts.confirmUser(data.id, epoch)) return
    Object.assign(user, profileFields(data), { username: data.username || '', account_name: data.account_name || '' })
    original = profileFields(data)
    publicKeyRevision = data.public_key_revision ?? null
    const restored = drafts.restore('profile', data.id)
    draftRestored.value = !!restored
    if (restored) {
      Object.assign(user, profileFields(restored))
      publicKeyRevision = restored.public_key_revision ?? publicKeyRevision
    }
    currentUser.value = { id: data.id, is_admin: data.is_admin === true }
    if (String(route.params.id) !== String(data.id)) await router.replace({ name: 'Profile', params: { id: data.id } })
    await Promise.all([
      apiFetch('/api/user/applications', { credentials: 'include' }).then(r => r.json()).then(data => { applications.value = data }),
      apiFetch('/api/user/accounts', { credentials: 'include' }).then(r => r.json()).then(data => { accounts.value = data })
    ])
  } catch (error) {
    loadFailed.value = true
    if (error.status === 401) currentUser.value = { id: null, is_admin: false }
    throw error
  } finally { loading.value = false }
}
onMounted(fetchProfile)
async function saveChanges() {
  if (saving.value || !currentUser.value.id) return
  saveMessage.value = validateProfileSave(user, original, currentPassword.value, newPassword.value, confirmPassword.value)
  if (saveMessage.value) return
  saving.value = true
  try {
    await apiFetch('/api/user/update', {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, credentials: 'include',
      body: JSON.stringify({ id: currentUser.value.id, realname: user.realname, mail: user.mail, public_key: user.public_key,
        old_password: currentPassword.value, new_password: newPassword.value, public_key_revision: publicKeyRevision })
    })
    clearPasswords()
    drafts.discard('profile', currentUser.value.id)
    draftRestored.value = false
    original = profileFields(user)
    ElMessage.success('Changes saved successfully')
    await fetchProfile()
  } catch (error) {
    saveMessage.value = error.message || 'Changes could not be saved. See the error details.'
    reportError(error)
  } finally { saving.value = false }
}
async function revokeAccount(row) {
  if (!currentUser.value.is_admin || row.is_gateway || accountBusy.value !== null) return
  accountBusy.value = row.id
  try {
    await apiFetch(`/api/account/${row.id}/revoke`, { method: 'PUT', credentials: 'include' })
    await fetchProfile()
  } finally { accountBusy.value = null }
}
async function onSwitchSudo(row, value) {
  if (!currentUser.value.is_admin || (row.is_gateway && row.is_user_admin) || accountBusy.value !== null) return
  accountBusy.value = row.id
  try {
    await apiFetch(`/api/account/${row.id}/sudo`, { method: 'PUT', credentials: 'include' })
    row.is_sudo = value
    await fetchProfile()
  } catch (error) { reportError(error) }
  finally { accountBusy.value = null }
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
