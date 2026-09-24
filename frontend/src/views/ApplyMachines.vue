<template>
  <main class="apply-page">
    <h2>申请机器 · 使用登记</h2>
    <el-alert title="使用登记不是实时物理占用监控；没有登记不代表机器空闲。账号数量表示访问账号，不等于正在使用的人数。请结合采集时间并与使用者沟通。" type="info" :closable="false" show-icon />
    <el-alert v-if="loadFailed" title="未能加载最新列表，操作已暂停。请查看持久错误提示并重试；表单和选择已保留。" type="error" :closable="false" show-icon />
    <p v-if="!authenticated && !loading">请先 <router-link :to="{name:'Login'}">登录</router-link>，再申请机器。</p>
    <el-alert v-if="draftRestored" title="已恢复同一账号在本标签页的申请草稿；不可申请的机器已从选择中移除。" type="info" :closable="false" />
    <div class="toolbar">
      <el-input v-model="query" placeholder="筛选主机名 / 标签 / CPU / 内存 / GPU / 磁盘 / 网卡" aria-label="筛选机器" clearable />
      <el-button @click="fetchDevices" :loading="loading" :disabled="saving || actionBusy !== null">刷新列表</el-button>
      <router-link :to="{name:'Servers'}">设备列表</router-link>
      <router-link v-if="currentUser.id" :to="{name:'Profile',params:{id:currentUser.id}}">Profile · 账号与旧版申请</router-link>
    </div>
    <el-table :data="filteredDevices" row-key="id" v-loading="loading" style="width:100%" empty-text="暂无匹配设备" :default-sort="{prop:'host',order:'ascending'}">
      <el-table-column width="58" fixed>
        <template #header><el-checkbox :model-value="allVisibleSelected" :indeterminate="someVisibleSelected && !allVisibleSelected" :disabled="busy || !eligibleVisible.length" aria-label="选择当前筛选的可申请机器（最多50台）" @change="toggleVisible" /></template>
        <template #default="{row}"><el-checkbox :model-value="selectedIds.includes(row.id)" :disabled="busy || !!usageBlockedReason(row) || (!selectedIds.includes(row.id) && selectedIds.length >= MAX_SELECTION)" :aria-label="`选择 ${row.host}`" @change="toggleDevice(row, $event)" /></template>
      </el-table-column>
      <el-table-column type="expand"><template #default="{row}"><HardwareDetails :hardware="row.hardware" /></template></el-table-column>
      <el-table-column prop="host" label="主机 / 标签" min-width="155" sortable>
        <template #default="{row}">
          <router-link :to="{name:'ServerInfo',params:{id:row.id}}">{{ row.host }}</router-link>
          <div><el-tag v-if="row.gateway" type="warning" size="small">网关 · 不可申请</el-tag><el-tag v-for="tag in row.tags" :key="tag" size="small">{{ tag }}</el-tag></div>
          <small>设备状态：{{ row.status ?? '未知' }}（非使用状态）</small>
        </template>
      </el-table-column>
      <el-table-column label="硬件摘要（展开查看详情）" min-width="310"><template #default="{row}"><HardwareSummary :hardware="row.hardware" /></template></el-table-column>
      <el-table-column label="使用登记（非实时占用）" min-width="245">
        <template #default="{row}">
          <div v-if="!row.usage?.active_users?.length">暂无使用登记（不代表空闲）</div>
          <div v-for="person in row.usage?.active_users || []" :key="person.user_id" class="person">
            <strong>{{ person.realname || `用户 #${person.user_id}` }}</strong>
            <small>开始登记：{{ displayTime(person.started_at) }}</small>
            <small>最近确认仍在使用：{{ person.confirmed_at ? displayTime(person.confirmed_at) : '待使用者确认' }}</small>
          </div>
        </template>
      </el-table-column>
      <el-table-column label="待审批数" min-width="105"><template #default="{row}">{{ row.usage?.pending_count ?? '未知' }}<small>包含旧版申请</small></template></el-table-column>
      <el-table-column label="访问账号数" min-width="110"><template #default="{row}">{{ row.usage?.account_count ?? '未知' }}<small>不是使用人数</small></template></el-table-column>
      <el-table-column label="我的登记 / 操作" min-width="260">
        <template #default="{row}">
          <div>{{ usageStatus(row.usage?.my_usage?.status) }}</div>
          <small>访问账号：{{ row.usage?.has_account === true ? '已有（登记与权限分开）' : row.usage?.has_account === false ? '无' : '未知' }}</small>
          <template v-if="row.usage?.my_usage">
            <small>申请原因：{{ row.usage.my_usage.reason || '未填写' }}</small>
            <small>申请时间：{{ displayTime(row.usage.my_usage.requested_at) }}</small>
            <small v-if="row.usage.my_usage.confirmed_at">最近确认：{{ displayTime(row.usage.my_usage.confirmed_at) }}</small>
            <small v-if="row.usage.my_usage.ended_at">结束时间：{{ displayTime(row.usage.my_usage.ended_at) }}</small>
            <div class="actions" v-if="row.usage.my_usage.status === 'active'">
              <el-button size="small" :disabled="busy" @click="usageAction(row, 'confirm')">确认仍在使用</el-button>
              <el-button size="small" type="warning" :disabled="busy" @click="usageAction(row, 'end')">结束使用登记</el-button>
            </div>
            <el-button v-if="row.usage.my_usage.status === 'pending'" size="small" :disabled="busy" @click="usageAction(row, 'cancel')">取消待审批申请</el-button>
          </template>
          <div v-if="canRequestSudo(row)">
            <template v-if="row.usage.my_sudo_request">
              <el-tag type="warning">sudo 权限升级待审批（使用登记保持活跃）</el-tag>
              <small>sudo 申请原因：{{ row.usage.my_sudo_request.reason }}</small>
              <small>sudo 申请时间：{{ displayTime(row.usage.my_sudo_request.create_date) }}</small>
              <el-button size="small" :disabled="busy" @click="cancelSudo(row)">取消 sudo 申请</el-button>
            </template>
            <el-button v-else size="small" :disabled="busy" @click="openSudo(row)">申请 sudo 权限</el-button>
          </div>
          <small v-else-if="row.usage?.has_sudo === true">已有 sudo 权限</small>
          <small v-if="usageBlockedReason(row)">{{ usageBlockedReason(row) }}</small>
          <small v-if="!row.usage?.my_usage && row.usage?.pending_count > 0">有待审批申请（可能属于其他用户或旧版申请）。如您的旧申请尚未处理，请到 Profile 查看并联系管理员；重复申请会提示冲突。</small>
        </template>
      </el-table-column>
    </el-table>
    <el-dialog v-model="sudoDialog" title="申请 sudo 权限升级" width="min(560px, 95vw)" :close-on-click-modal="false" :close-on-press-escape="actionBusy === null" :show-close="actionBusy === null">
      <p>为 {{ sudoDevice?.host }} 申请 sudo 权限。不会结束当前使用登记，也不会立即改变访问权限；等待管理员审批。</p>
      <el-alert v-if="sudoError" :title="sudoError" type="error" :closable="false" show-icon />
      <el-form label-position="top" @submit.prevent="submitSudo">
        <el-form-item label="sudo 申请原因（必填，最多 500 字）" required>
          <el-input v-model="sudoReason" type="textarea" :rows="4" maxlength="500" show-word-limit :disabled="busy" aria-label="sudo 申请原因" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button :disabled="actionBusy !== null" @click="sudoDialog = false">保留使用登记，关闭</el-button>
        <el-button type="primary" :disabled="busy" :loading="actionBusy !== null" @click="submitSudo">提交 sudo 申请</el-button>
      </template>
    </el-dialog>
    <el-card class="application-form">
      <h3>批量申请 / 登记 · 已选 {{ selectedIds.length }} / 50 台</h3>
      <p>筛选不会清除已选机器。已有访问账号不等于已登记使用；审批与是否需要开通账号由后端判断。设备连接状态是历史采集结果，不阻止登记，也不保证当前能连接。</p>
      <div class="selected"><el-tag v-for="device in selectedDevices" :key="device.id" :closable="!busy" @close="toggleDevice(device,false)">{{ device.host }}</el-tag></div>
      <el-form label-position="top" :disabled="busy" @submit.prevent="submitApplication">
        <el-form-item label="申请用途 / 原因（必填，最多 500 字）" required>
          <el-input v-model="reason" type="textarea" :rows="3" maxlength="500" show-word-limit aria-label="申请原因" placeholder="说明工作内容、预计使用时间等，便于管理员审批" />
        </el-form-item>
        <el-form-item label="权限需求"><el-checkbox v-model="needSudo">需要 sudo 权限</el-checkbox></el-form-item>
        <el-button type="primary" :loading="saving" :disabled="busy || !selectedIds.length || !reason.trim()" @click="submitApplication">提交申请 / 使用登记</el-button>
      </el-form>
      <p>长任务请定期“确认仍在使用”；最近30天内的确认可避免仅因没有 SSH 登录活动被自动闲置回收。登记不代表账号已完成远端同步，请同时查看同步错误。</p>
      <p>“结束使用登记”只更新登记状态，不修改账号权限，不终止进程，也不撤销 SSH 访问。</p>
    </el-card>
  </main>
</template>
<script setup>
import { ref, computed, onMounted, onBeforeUnmount } from 'vue'
import { drafts } from '../drafts.js'
import { ElMessage, ElMessageBox } from 'element-plus'
import { apiFetch, reportError } from '../api.js'
import HardwareSummary from '../components/HardwareSummary.vue'
import HardwareDetails from '../components/HardwareDetails.vue'
import { deviceMatches, displayTime } from '../hardware.js'
import { MAX_SELECTION, usageBlockedReason, validateApplication, usageStatus, canRequestSudo, validateSudoReason } from '../usage.js'
const devices = ref([])
const currentUser = ref({ id: null })
const authenticated = ref(false)
const loading = ref(false)
const loadFailed = ref(false)
const saving = ref(false)
const actionBusy = ref(null)
const selectedIds = ref([])
const reason = ref('')
const needSudo = ref(false)
const query = ref('')
const draftRestored = ref(false)
const sudoDialog = ref(false)
const sudoDeviceId = ref(null)
const sudoReason = ref('')
const sudoError = ref('')
const sudoDevice = computed(() => devices.value.find(device => device.id === sudoDeviceId.value))
let disposed = false
let draftLoadedFor = null
function preserveDraft() {
  if (currentUser.value.id) drafts.save('apply', currentUser.value.id, { selectedIds: selectedIds.value, reason: reason.value, needSudo: needSudo.value })
}
const unsubscribeDraft = drafts.subscribe(event => {
  if (event === 'expiry') preserveDraft()
  authenticated.value = false
  sudoDialog.value = false
})
onBeforeUnmount(() => { preserveDraft(); disposed = true; unsubscribeDraft() })
const busy = computed(() => !authenticated.value || loading.value || loadFailed.value || saving.value || actionBusy.value !== null)
const filteredDevices = computed(() => devices.value.filter(device => deviceMatches(device, query.value)))
const selectedDevices = computed(() => devices.value.filter(device => selectedIds.value.includes(device.id)))
const eligibleVisible = computed(() => filteredDevices.value.filter(device => !usageBlockedReason(device)))
const allVisibleSelected = computed(() => eligibleVisible.value.length > 0 && eligibleVisible.value.every(device => selectedIds.value.includes(device.id)))
const someVisibleSelected = computed(() => eligibleVisible.value.some(device => selectedIds.value.includes(device.id)))
async function fetchDevices() {
  if (loading.value) return
  loading.value = true
  loadFailed.value = false
  const epoch = drafts.epoch
  try {
    // Identity comes from the authenticated session, never from route parameters.
    const me = await (await apiFetch('/api/user/me', { credentials: 'include' })).json()
    if (disposed || !drafts.confirmUser(me.id, epoch)) return
    currentUser.value = me
    authenticated.value = !!currentUser.value.id
    if (!authenticated.value) throw new Error('请登录后申请机器')
    // Restore after identity validation, even if the subsequent inventory fetch fails.
    if (draftLoadedFor !== me.id) {
      const restored = drafts.restore('apply', me.id)
      if (restored) {
        selectedIds.value = restored.selectedIds
        reason.value = restored.reason
        needSudo.value = restored.needSudo
        draftRestored.value = true
      } else if (draftLoadedFor !== null) {
        selectedIds.value = []; reason.value = ''; needSudo.value = false
      }
      draftLoadedFor = me.id
    }
    const data = await (await apiFetch('/api/usage/devices', { credentials: 'include' })).json()
    if (!Array.isArray(data)) throw new Error('机器列表响应格式错误，请联系管理员')
    if (disposed || epoch !== drafts.epoch) return
    devices.value = data
    selectedIds.value = selectedIds.value.filter(id => data.some(device => device.id === id && !usageBlockedReason(device)))
  } catch (error) {
    loadFailed.value = true
    if (error.status === 401) { authenticated.value = false; currentUser.value = { id: null } }
    reportError(error)
  } finally { loading.value = false }
}
function toggleDevice(device, checked) {
  if (busy.value || (checked && usageBlockedReason(device))) return
  if (!checked) { selectedIds.value = selectedIds.value.filter(id => id !== device.id); return }
  if (selectedIds.value.includes(device.id)) return
  if (selectedIds.value.length >= MAX_SELECTION) return reportError(new Error('每次最多选择 50 台机器'))
  selectedIds.value = [...selectedIds.value, device.id]
}
function toggleVisible(checked) {
  if (busy.value) return
  const visible = eligibleVisible.value.map(device => device.id)
  const next = checked ? [...new Set([...selectedIds.value, ...visible])] : selectedIds.value.filter(id => !visible.includes(id))
  if (next.length > MAX_SELECTION) return reportError(new Error('筛选结果超过 50 台，请缩小筛选范围或逐台选择'))
  selectedIds.value = next
}
async function submitApplication() {
  if (busy.value) return
  const error = validateApplication(selectedIds.value, reason.value, devices.value)
  if (error) return reportError(new Error(error))
  saving.value = true
  try {
    await apiFetch('/api/usage/apply', {
      method: 'POST', credentials: 'include', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ server_ids: selectedIds.value, need_sudo: needSudo.value, reason: reason.value.trim() }),
    })
    drafts.discard('apply', currentUser.value.id)
    draftRestored.value = false
    selectedIds.value = []
    reason.value = ''
    needSudo.value = false
    ElMessage.success('申请 / 使用登记已提交；请查看刷新后的登记状态。')
    await fetchDevices()
  } catch (error) { reportError(error) }
  finally { saving.value = false }
}
async function usageAction(device, action) {
  if (busy.value) return
  const usage = device.usage?.my_usage
  if (!usage || (action === 'cancel' ? usage.status !== 'pending' : !['end', 'confirm'].includes(action) || usage.status !== 'active')) return
  actionBusy.value = usage.id
  try {
    if (action === 'end') {
      try {
        await ElMessageBox.confirm('确定结束使用登记？此操作仅结束登记，账号权限保持不变；不会终止进程，不会撤销 SSH 访问。请自行检查并停止不再需要的任务。', '结束使用登记', { type: 'warning', confirmButtonText: '仅结束登记', cancelButtonText: '保留登记' })
      } catch (error) { if (error === 'cancel' || error === 'close') return; throw error }
    }
    await apiFetch(`/api/usage/${usage.id}/${action}`, { method: 'POST', credentials: 'include' })
    ElMessage.success(action === 'end' ? '使用登记已结束，账号权限未改变。' : action === 'confirm' ? '已记录您仍在使用。' : '待审批申请已取消。')
    await fetchDevices()
  } catch (error) { reportError(error) }
  finally { actionBusy.value = null }
}
function openSudo(device) {
  if (busy.value || !canRequestSudo(device) || device.usage.my_sudo_request) return
  if (sudoDeviceId.value !== device.id) sudoReason.value = ''
  sudoDeviceId.value = device.id
  sudoError.value = ''
  sudoDialog.value = true
}
async function submitSudo() {
  const device = sudoDevice.value
  if (busy.value || !sudoDialog.value || !canRequestSudo(device) || device.usage.my_sudo_request) return
  sudoError.value = validateSudoReason(sudoReason.value)
  if (sudoError.value) return
  actionBusy.value = device.usage.my_usage.id
  try {
    await apiFetch(`/api/usage/${device.usage.my_usage.id}/sudo`, {
      method: 'POST', credentials: 'include', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ reason: sudoReason.value.trim() })
    })
    sudoDialog.value = false
    sudoReason.value = ''
    ElMessage.success('sudo 权限升级已申请，当前使用登记保持活跃。')
    await fetchDevices()
  } catch (error) { sudoError.value = error.message; reportError(error) }
  finally { actionBusy.value = null }
}
async function cancelSudo(device) {
  if (busy.value || !canRequestSudo(device) || !device.usage.my_sudo_request) return
  actionBusy.value = device.usage.my_usage.id
  try {
    await apiFetch(`/api/usage/${device.usage.my_usage.id}/sudo/cancel`, { method: 'POST', credentials: 'include' })
    ElMessage.success('sudo 申请已取消，使用登记和账号权限未改变。')
    await fetchDevices()
  } catch (error) { reportError(error) }
  finally { actionBusy.value = null }
}
onMounted(fetchDevices)
</script>
<style scoped>
.apply-page {box-sizing:border-box;max-width:1900px;margin:0 auto;padding:0 24px 40px;text-align:left;}
.toolbar {display:flex;align-items:center;flex-wrap:wrap;gap:12px;margin:20px 0;}
.toolbar .el-input {width:460px;max-width:100%;}
small {display:block;color:#606266;font-size:12px;white-space:normal;overflow-wrap:anywhere;}
.person + .person {margin-top:10px;}
.actions {display:flex;flex-wrap:wrap;gap:6px;margin:8px 0;}
.actions .el-button {margin-left:0;}
.application-form {margin-top:20px;}
.application-form p {color:#606266;font-size:13px;}
.selected {display:flex;flex-wrap:wrap;gap:6px;margin-bottom:12px;}
.el-alert + .el-alert {margin-top:10px;}
</style>
