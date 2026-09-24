<template>
  <el-container>
    <el-main v-loading="loading">
      <el-alert v-if="loadFailed" title="Some server details could not be loaded. See the error details and retry." type="error" :closable="false" />
      <el-button v-if="loadFailed" @click="loadPage(route.params.id)">Retry</el-button>
      <el-alert v-if="refreshQueued" title="Refresh queued — background collection is not complete yet. Reload details later to check progress." type="info" :closable="false" />
      <el-button v-if="refreshQueued" @click="reloadDetails" :loading="loading">Reload details</el-button>
      <el-card class="card-spacing">
        <el-row>
          <el-col :span="24">
            <h2>Server Information</h2>
            <el-divider />
            <el-form label-width="150px">
              <el-form-item label="Server Host">
                <el-input v-model="server.host" disabled />
              </el-form-item>
              <el-form-item label="Server Port">
                <el-input v-model="server.port" disabled />
              </el-form-item>
              <el-form-item label="Server Gateway">
                <el-switch v-model="server.is_gateway" disabled />
              </el-form-item>
              <el-form-item label="Proxy Server">
                <template v-if="server.proxy_server">
                  {{ server.proxy_server.host + ':' + server.proxy_server.port }}
                </template>
                <template v-else>
                  N/A
                </template>
              </el-form-item>
              <el-form-item label="Server Status">
                {{ server.server_status }}
              </el-form-item>
              <el-form-item label="Mounted Home">
                <el-switch v-model="server.is_separated_home" disabled />
              </el-form-item>
              <el-form-item label="OS Version">
                {{ server.os_version }}
              </el-form-item>
              <el-form-item label="Kernel Version">
                {{ server.kernel_version }}
              </el-form-item>
              <el-form-item label="Tags">
                <template v-if="!currentUser.is_admin">
                  <el-tag v-for="tag in server.tags" :key="tag.id">{{ tag.tag }}</el-tag>
                </template>
                <template v-else>
                  <el-tag v-for="tag in server.tags" :key="tag.id" closable @close="removeServerTag(tag.id)">{{ tag.tag }}</el-tag>
                  <el-button size="small" v-if="!show_new_server_tag" @click="showAddNewServerTag" :disabled="addingServerTag || !server.id">+ New Tag</el-button>
                  <el-input v-else v-model="new_server_tag_value" size="small" style="width:80px" @keyup.enter="confirmServerTagInput" @blur="confirmServerTagInput" />
                </template>
              </el-form-item>
              <el-form-item label="IPMI Info">
                <template v-if="currentUser.is_admin">
                  <el-row>
                    <el-col :span="18">
                      <el-input v-model="server.ipmi" placeholder="IPMI Information" class="full-width-input" />
                    </el-col>
                    <el-col :span="6">
                      <el-button type="primary" @click="saveIPMI" :loading="savingIPMI" :disabled="!server.id">Save</el-button>
                    </el-col>
                  </el-row>
                </template>
                <template v-else>
                  {{ server.ipmi || 'N/A' }}
                </template>
              </el-form-item>
              <el-form-item v-if="currentUser.is_admin" label="Actions">
                <el-button type="primary" class="button-spacing" @click="refreshServer" :loading="refreshing" :disabled="refreshQueued || !server.id">{{ refreshQueued ? 'Refresh queued' : 'Refresh Server Status' }}</el-button>
              </el-form-item>
            </el-form>
          </el-col>
        </el-row>
      </el-card>

      <el-card class="card-spacing"><HardwareDetails :hardware="server.hardware" /></el-card>

      <el-card class="card-spacing">
        <el-row>
          <el-col :span="24">
            <h2>Server Interfaces</h2>
            <el-divider />
            <el-table :data="server.interfaces" empty-text="No interfaces reported" style="width: 100%">
              <el-table-column label="Interface Name">
                <template #default="{ row }">
                  <el-tooltip :content="row.manufacturer" placement="top">
                    <span>{{ row.interface }}</span>
                  </el-tooltip>
                </template>
              </el-table-column>
              <el-table-column label="Tags">
                <template #default="{ row }">
                  <template v-if="currentUser.is_admin">
                    <el-tag v-for="tag in row.tags" :key="tag.id" closable @close="removeTag(tag.id)">{{ tag.tag }}</el-tag>
                    <el-button size="small" v-if="!row.show_new_tag" @click="showAddNewTag(row)" :disabled="addingInterfaceTag">+ New Tag</el-button>
                    <el-input v-else v-model="new_tag_value" size="small" style="width:80px" @keyup.enter="confirmTagInput(row)" @blur="confirmTagInput(row)" />
                  </template>
                  <template v-else>
                    <el-tag v-for="tag in row.tags" :key="tag.id">{{ tag.tag }}</el-tag>
                  </template>
                </template>
              </el-table-column>
              <el-table-column prop="pci_address" label="PCI Address" />
              <el-table-column label="Connection">
                <template #default="{ row }">
                  <el-tag v-if="row.peer_interface" type="success">Server</el-tag>
                  <el-tag v-else-if="row.peer_switch" type="info">Switch</el-tag>
                  <el-tag v-else type="danger">Not Connected</el-tag>
                </template>
              </el-table-column>
              <el-table-column label="Peer Name">
                <template #default="{ row }">
                  <el-tag v-if="row.peer_interface" type="success">
                    {{ row.peer_interface.server_host }}:{{ row.peer_interface.interface }}
                  </el-tag>
                  <el-tag v-else-if="row.peer_switch" type="success">
                    {{ row.peer_switch.switch_name }} - Port {{ row.peer_switch.port_num }}
                  </el-tag>
                  <span v-else>N/A</span>
                </template>
              </el-table-column>
              <el-table-column label="Actions" v-if="currentUser.is_admin">
                <template #default="{ row }">
                  <el-button type="primary" @click="showLinkToDialog(row.id)">连接/断开</el-button>
                </template>
              </el-table-column>
            </el-table>
          </el-col>
        </el-row>
      </el-card>

      <el-dialog title="连接设置" v-model="showModal" @closed="clearHost">
        <el-radio-group v-model="connectionType">
          <el-radio-button label="断开" />
          <el-radio-button label="直连" />
          <el-radio-button label="交换机" />
        </el-radio-group>
        <div v-if="connectionType === '直连'" style="margin-top:20px;">
          <el-form>
            <el-form-item label="Host">
              <el-autocomplete
                v-model="directConnection.host"
                :fetch-suggestions="hostQuerySearch"
                placeholder="Server Hostname"
                @select="handleHostSelect"
                @input="clearHost"
              />
            </el-form-item>
            <el-form-item label="Interface 名字">
              <el-autocomplete v-model="directConnection.interfaceName" :fetch-suggestions="hostInterQuerySearch" placeholder="Interface (with PCI)" :disabled="!selectedServerId" @select="selectTarget" @input="selectedTargetId = null" />
            </el-form-item>
          </el-form>
        </div>
        <div v-if="connectionType === '交换机'" style="margin-top:20px;">
          <el-form>
            <el-form-item label="交换机名字">
              <el-autocomplete v-model="switchConnection.switchName" :fetch-suggestions="switchQuerySearch" placeholder="请输入交换机名字" @select="handleSelectSwitch" @input="clearSwitch" />
            </el-form-item>
            <el-form-item label="交换机端口号">
              <el-input v-model="switchConnection.switchPort" type="number" />
            </el-form-item>
          </el-form>
        </div>
        <template #footer>
          <el-button @click="showModal = false">取消</el-button>
          <el-button type="primary" @click="saveChanges" :loading="saving" :disabled="!currentUser.is_admin">保存</el-button>
        </template>
      </el-dialog>
    </el-main>
  </el-container>
</template>

<script setup>
import { ref, reactive, watch } from 'vue'
import { useRoute } from 'vue-router'
import { ElMessage } from 'element-plus'
import { apiFetch, reportError } from '../api.js'
import { interfaceLabel, resolveInterface } from '../selection.js'
import HardwareDetails from '../components/HardwareDetails.vue'
const route = useRoute()
const currentUser = ref({ id: null, is_admin: false })
const emptyServer = () => ({ id: null, host: '', port: '', proxy_server: null, is_gateway: false, server_status: '', is_separated_home: false, os_version: '', kernel_version: '', tags: [], ipmi: '', interfaces: [], hardware: {} })
const server = reactive(emptyServer())
const loading = ref(false)
const loadFailed = ref(false)
const saving = ref(false)
const savingIPMI = ref(false)
const refreshing = ref(false)
const refreshQueued = ref(false)
const show_new_server_tag = ref(false)
const new_server_tag_value = ref('')
const new_tag_value = ref('')
const old_tag_row_obj = ref(null)
const addingServerTag = ref(false)
const addingInterfaceTag = ref(false)
const showModal = ref(false)
const connectionType = ref('断开')
const selectedInterfaceId = ref(null)
const directConnection = reactive({ host: '', interfaceName: '' })
const switchConnection = reactive({ switchName: '', switchPort: null, switchId: null })
const switches = ref([])
const allServers = ref([])
const selectedServerId = ref(null)
const selectedTargetId = ref(null)
const targetInterfaces = ref([])
let hostRequest = 0
let pageRequest = 0
const post = (url, body) => apiFetch(url, { method: 'POST', credentials: 'include', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) })
async function loadServer(id = route.params.id) {
  const request = pageRequest
  const data = await (await apiFetch(`/api/server/${encodeURIComponent(id)}`, { credentials: 'include' })).json()
  if (request === pageRequest && String(route.params.id) === String(id)) Object.assign(server, data)
}
async function loadPage(id) {
  const request = ++pageRequest
  loading.value = true
  loadFailed.value = false
  Object.assign(server, emptyServer())
  showModal.value = false
  show_new_server_tag.value = false
  old_tag_row_obj.value = null
  new_server_tag_value.value = new_tag_value.value = ''
  refreshQueued.value = false
  selectedInterfaceId.value = null
  clearHost()
  try {
    await Promise.all([
      loadServer(id),
      apiFetch('/api/user/me', { credentials: 'include' }).then(r => r.json()).then(data => { if (request === pageRequest) currentUser.value = data }),
      apiFetch('/api/link/devices', { credentials: 'include' }).then(r => r.json()).then(data => { if (request === pageRequest) switches.value = data }),
      apiFetch('/api/server/list', { credentials: 'include' }).then(r => r.json()).then(data => { if (request === pageRequest) allServers.value = data })
    ])
  } catch (error) { if (request === pageRequest) loadFailed.value = true; reportError(error) }
  finally { if (request === pageRequest) loading.value = false }
}
watch(() => route.params.id, loadPage, { immediate: true })
async function removeServerTag(tag_id) {
  if (!currentUser.value.is_admin) return
  const id = server.id
  await post('/api/server/tag/remove', { tag_id })
  if (server.id === id) server.tags = server.tags.filter(t => t.id !== tag_id)
}
async function removeTag(tag_id) {
  if (!currentUser.value.is_admin) return
  const id = server.id
  await post('/api/server/interface/tag/remove', { tag_id })
  if (server.id === id) server.interfaces.forEach(row => { row.tags = row.tags.filter(t => t.id !== tag_id) })
}
async function confirmServerTagInput() {
  // Enter removes the input and emits blur too: consume the draft synchronously once.
  if (!currentUser.value.is_admin || !show_new_server_tag.value || addingServerTag.value) return
  const tag = new_server_tag_value.value.trim()
  const id = server.id
  show_new_server_tag.value = false
  new_server_tag_value.value = ''
  if (!tag) return
  addingServerTag.value = true
  try {
    const data = await (await post('/api/server/tag/add', { server_id: id, tag })).json()
    if (server.id === id) server.tags.push(data)
  } catch (error) {
    if (server.id === id) { new_server_tag_value.value = tag; show_new_server_tag.value = true }
    reportError(error)
  } finally { addingServerTag.value = false }
}
function showAddNewServerTag() {
  if (!addingServerTag.value) { new_server_tag_value.value = ''; show_new_server_tag.value = true }
}
async function showAddNewTag(row) {
  if (addingInterfaceTag.value) return
  if (old_tag_row_obj.value && old_tag_row_obj.value !== row) {
    await confirmTagInput(old_tag_row_obj.value)
    if (old_tag_row_obj.value) return // Keep a failed draft instead of silently discarding it.
  }
  new_tag_value.value = ''
  row.show_new_tag = true
  old_tag_row_obj.value = row
}
async function confirmTagInput(row) {
  if (!currentUser.value.is_admin || !row.show_new_tag || old_tag_row_obj.value !== row || addingInterfaceTag.value) return
  const tag = new_tag_value.value.trim()
  const id = server.id
  row.show_new_tag = false
  old_tag_row_obj.value = null
  new_tag_value.value = ''
  if (!tag) return
  addingInterfaceTag.value = true
  try {
    const data = await (await post('/api/server/interface/tag/add', { interface_id: row.id, tag })).json()
    if (server.id === id) row.tags.push(data)
  } catch (error) {
    if (server.id === id) { new_tag_value.value = tag; row.show_new_tag = true; old_tag_row_obj.value = row }
    reportError(error)
  } finally { addingInterfaceTag.value = false }
}
async function refreshServer() {
  if (!currentUser.value.is_admin || refreshing.value || refreshQueued.value || !server.id) return
  const id = server.id
  refreshing.value = true
  try {
    await post('/api/server/refresh', { server_id: id })
    if (server.id === id) refreshQueued.value = true
    ElMessage.success('Refresh queued. Collection runs in the background; reload details later to see the result.')
  } finally { refreshing.value = false }
}
async function reloadDetails() {
  loading.value = true
  try { await loadServer(); refreshQueued.value = false }
  finally { loading.value = false }
}
function hostQuerySearch(query, cb) {
  cb(allServers.value.filter(s => s.host?.toLowerCase().includes(query.toLowerCase())).map(s => ({ id: s.id, value: s.host })))
}
function clearHost() {
  hostRequest++
  selectedServerId.value = null
  selectedTargetId.value = null
  directConnection.interfaceName = ''
  targetInterfaces.value = []
}
async function handleHostSelect(item) {
  clearHost()
  selectedServerId.value = item.id
  directConnection.host = item.value
  const request = hostRequest
  const data = await (await apiFetch(`/api/server/${item.id}`, { credentials: 'include' })).json()
  if (request === hostRequest && showModal.value && selectedServerId.value === item.id) targetInterfaces.value = data.interfaces || []
}
function hostInterQuerySearch(query, cb) {
  cb(targetInterfaces.value.filter(i => i.interface && interfaceLabel(i).toLowerCase().includes(query.toLowerCase()))
    .map(i => ({ value: interfaceLabel(i), id: i.id })))
}
function selectTarget(item) { directConnection.interfaceName = item.value; selectedTargetId.value = item.id }
function switchQuerySearch(query, cb) {
  cb(switches.value.filter(s => s.name?.toLowerCase().includes(query.toLowerCase())).map(s => ({ id: s.id, value: s.name })))
}
function clearSwitch() { switchConnection.switchId = null; switchConnection.switchPort = null }
function handleSelectSwitch(item) { clearSwitch(); switchConnection.switchName = item.value; switchConnection.switchId = item.id }
function showLinkToDialog(id) {
  if (!currentUser.value.is_admin) return
  selectedInterfaceId.value = id
  showModal.value = true
  connectionType.value = '断开'
  clearHost()
  directConnection.host = ''
  Object.assign(switchConnection, { switchName: '', switchPort: null, switchId: null })
}
async function saveChanges() {
  if (!currentUser.value.is_admin || saving.value || !server.interfaces.some(i => i.id === selectedInterfaceId.value)) return
  const id = server.id
  saving.value = true
  try {
    let url, body
    if (connectionType.value === '断开') {
      url = '/api/link/interface/disconnect'
      body = { interface_id: selectedInterfaceId.value }
    } else if (connectionType.value === '直连') {
      const host = allServers.value.find(s => s.id === selectedServerId.value && s.host === directConnection.host)
      const target = resolveInterface(targetInterfaces.value, directConnection.interfaceName, selectedTargetId.value)
      if (!host || !target || target.id === selectedInterfaceId.value) return ElMessage.warning('Select a different, exact target interface (including PCI) from the suggestions.')
      url = '/api/link/interface/connect'
      body = { interface_a_id: selectedInterfaceId.value, interface_b_id: target.id }
    } else if (connectionType.value === '交换机') {
      const sw = switches.value.find(s => s.id === switchConnection.switchId && s.name === switchConnection.switchName)
      const number = Number(switchConnection.switchPort)
      const port = Number.isInteger(number) && number > 0 && sw?.ports?.find(p => sw.num_row * p.phy_col + p.phy_row + 1 === number)
      if (!port) return ElMessage.warning('Select a valid switch and port from the suggestions.')
      url = '/api/link/switch_port/interface/connect'
      body = { switch_port_id: port.id, interface_id: selectedInterfaceId.value }
    } else return
    await post(url, body)
    if (server.id === id) showModal.value = false
    ElMessage.success('Connection updated')
    await loadServer(id)
  } catch (error) { reportError(error) }
  finally { saving.value = false }
}
async function saveIPMI() {
  if (!currentUser.value.is_admin || savingIPMI.value || !server.id) return
  savingIPMI.value = true
  try { await post('/api/server/ipmi', { server_id: server.id, ipmi: server.ipmi }); ElMessage.success('IPMI info saved') }
  finally { savingIPMI.value = false }
}
</script>

<style scoped>
.card-spacing { margin-bottom: 20px; }
.button-spacing { margin-top: 20px; }
.full-width-input { width: 100%; }
.el-header-button { font-size: 16px; }
</style>
