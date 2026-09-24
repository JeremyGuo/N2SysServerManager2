<template>
  <div>
    <section class="summary-tools">
      <el-switch v-model="hideInactiveAdmins" active-text="隐藏长期未登录的管理员账号" />
      <label>未登录天数：<el-input-number v-model="inactiveDays" :min="1" :max="3650" :precision="0" /></label>
      <span>已隐藏 {{ hiddenCount }} 个账号；仅影响此表，不撤销权限。</span>
      <el-button @click="hideInactiveAdmins = false">显示全部管理员</el-button>
      <el-button :loading="loading" @click="fetchSummary">刷新</el-button>
      <p>管理员指系统管理员（不是仅拥有某台服务器 sudo 的用户），按各服务器的最后活动时间判断（登录/退出/仍在线）；时间按浏览器时区显示。每次进入默认开启，显示全部仅本次有效。</p>
    </section>
    <el-alert v-for="error in syncErrors" :key="`${error.scope}-${error.id}`" type="error" :closable="false" show-icon
      :title="`后台同步失败 · ${error.scope} ${error.id ?? ''}`" :description="`${error.message}（${error.occurred_at}）`" class="sync-error" />
    <el-table ref="tableRef" :data="tableData" :span-method="spanMethod" style="width:90%;margin:auto;" :default-sort="{prop:'host',order:'ascending'}">
      <el-table-column label="Host">
        <el-table-column prop="host" label="Host Name" sortable>
          <template #default="{row}"><router-link :to="`/server/${row.server_id}`">{{ row.host }}</router-link></template>
        </el-table-column>
        <el-table-column prop="status" label="Server Status" width="150" />
        <el-table-column prop="isGateway" label="Is Gateway" width="130" :filters="gatewayFilters" :filter-method="filterGateway" :filtered-value="[false]">
          <template #default="{row}">{{ row.isGateway ? 'Yes' : 'No' }}</template>
        </el-table-column>
        <el-table-column prop="isMounted" label="Is Mounted" width="120" />
      </el-table-column>
      <el-table-column prop="user" label="User">
        <template #default="{row}">{{ row.user }} <el-tag v-if="row.isAdmin" size="small">管理员</el-tag></template>
      </el-table-column>
      <el-table-column prop="accountName" label="Linux Account Name" min-width="150" />
      <el-table-column label="Sudo" width="80">
        <template #default="{row}">
          <el-switch v-if="row.account_id" :model-value="row.sudo" @change="handleSudoChange(row)" :disabled="loading || !currentUser.is_admin || row.isGateway" />
          <span v-else>—</span>
        </template>
      </el-table-column>
      <el-table-column prop="lastLogin" label="Last Activity">
        <template #default="{row}"><span :style="getLoginStyle(row.lastLogin)" :title="row.lastLogin || '未知时间'">{{ formatActivity(row.lastLogin) }}</span></template>
      </el-table-column>
      <el-table-column v-if="currentUser.is_admin" label="Actions">
        <template #default="{row}"><el-button v-if="row.account_id" type="warning" size="small" @click="revokePermission(row)" :disabled="loading || row.isGateway">Revoke</el-button></template>
      </el-table-column>
    </el-table>
  </div>
</template>

<script setup>
import { ref, computed, onMounted, onUnmounted, watch } from 'vue'
import { apiFetch, reportError } from '../api'
import { isInactiveAdmin, summaryRows, summaryPreferences, activityTimestamp, formatActivity } from '../summaryRows'

const currentUser = ref({ is_admin: false })
const loading = ref(false)
const servers = ref([])
const syncErrors = ref([])
const hideInactiveAdmins = ref(true)
const inactiveDays = ref(30)
const now = ref(Date.now())
const tableRef = ref(null)
let timer
let disposed = false
let preferenceKey
const threshold = computed(() => inactiveDays.value || 30)
const hiddenCount = computed(() => hideInactiveAdmins.value ? servers.value.reduce((n, s) => n + s.users.filter(u => isInactiveAdmin(u, threshold.value, now.value)).length, 0) : 0)
const tableData = computed(() => summaryRows(servers.value, hideInactiveAdmins.value, threshold.value, now.value))
const gatewayFilters = [{ text: 'Gateway', value: true }, { text: 'Not Gateway', value: false }]
const filterGateway = (value, row) => row.isGateway === value

watch(inactiveDays, () => {
  if (!preferenceKey) return
  try { localStorage.setItem(preferenceKey, JSON.stringify({days: threshold.value})) }
  catch (error) { reportError(new Error('无法保存表格筛选偏好：浏览器存储不可用。')) }
})
function spanMethod({row, column, rowIndex}) {
  if (!['host', 'status', 'isGateway', 'isMounted'].includes(column.property)) return
  const data = tableRef.value?.store.states.data.value || tableData.value
  if (rowIndex > 0 && data[rowIndex - 1].server_id === row.server_id) return {rowspan:0, colspan:0}
  let count = 1
  while (rowIndex + count < data.length && data[rowIndex + count].server_id === row.server_id) count++
  return {rowspan:count, colspan:1}
}
function getLoginStyle(date) {
  if (!date || !Number.isFinite(activityTimestamp(date))) return {}
  const days = (now.value - activityTimestamp(date)) / 86400000
  return {color: days > 25 ? '#c45656' : days > 7 ? '#b88230' : '#529b2e'}
}
async function handleSudoChange(row) {
  loading.value = true
  try {
    await apiFetch(`/api/account/${row.account_id}/sudo`, {method:'PUT'})
    await fetchSummary()
  } finally { loading.value = false }
}
async function revokePermission(row) {
  loading.value = true
  try {
    await apiFetch(`/api/account/${row.account_id}/revoke`, {method:'PUT'})
    await fetchSummary()
  } finally { loading.value = false }
}
async function fetchSyncErrors() {
  if (!currentUser.value.is_admin) return
  syncErrors.value = await (await apiFetch('/api/summary/sync-errors')).json()
}
async function fetchSummary() {
  loading.value = true
  try {
    servers.value = await (await apiFetch('/api/summary/get')).json()
    now.value = Date.now()
    await fetchSyncErrors()
  } finally { loading.value = false }
}
onMounted(async () => {
  currentUser.value = await (await apiFetch('/api/user/me')).json()
  preferenceKey = `n2sys-summary-filter-${currentUser.value.id}`
  try {
    const saved = JSON.parse(localStorage.getItem(preferenceKey) || 'null')
    // Each visit defaults to hiding, even if older versions stored hide:false.
    const preferences = summaryPreferences(saved)
    hideInactiveAdmins.value = preferences.hide
    inactiveDays.value = preferences.days
  } catch { reportError(new Error('无法读取表格筛选偏好，已使用默认值。')) }
  await fetchSummary()
  if (disposed) return
  timer = setInterval(() => { now.value = Date.now(); fetchSyncErrors().catch(reportError) }, 30000)
})
onUnmounted(() => { disposed = true; clearInterval(timer) })
</script>
<style scoped>
.summary-tools {width:90%;margin:12px auto 20px;display:flex;gap:12px;flex-wrap:wrap;align-items:center;text-align:left;}
.summary-tools p {width:100%;margin:0;font-size:13px;color:#606266;}
.sync-error {width:90%;margin:8px auto;}
</style>
