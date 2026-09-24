<template>
  <div v-loading="loading">
    <h2>Device Connections</h2>
    <DeviceChat />
    <el-alert v-if="!currentUser.is_admin" title="Read-only: administrator access is required to change connections." type="info" :closable="false" />
    <el-empty v-if="!loading && !switches.length" :description="loadFailed ? 'Device data could not be loaded. See the error details.' : 'No switches configured'" />
    <el-button v-if="loadFailed" @click="loadDevices">Retry</el-button>
    <div v-for="sw in switches" :key="sw.id" class="switch-card">
      <el-card shadow="never" class="card-spacing">
        <h3>{{ sw.name }} (ID: {{ sw.id }})</h3>
        <div class="grid-container"
             :style="{ 
               'grid-template-columns': 'repeat(' + sw.num_col + ', 1fr)',
               'grid-template-rows': 'repeat(' + sw.num_row + ', auto)'
             }">
          <template v-for="r in sw.num_row" :key="sw.id + '-row-' + r">
            <template v-for="c in sw.num_col" :key="sw.id + '-cell-' + r + '-' + c">
              <div class="grid-cell">
                <template v-if="getPort(sw, r-1, c-1)">
                  <el-button
                    :disabled="!currentUser.is_admin"
                    :type="portButtonType(sw, r-1, c-1)"
                    size="mini"
                    @click="openPortDialog(sw, getPort(sw, r-1, c-1))"
                  >
                    {{ portLabel(sw, r-1, c-1) }}
                  </el-button>
                  <div class="port-subtitle">Port {{ getPort(sw, r-1, c-1).name }}</div>
                </template>
                <template v-else>
                  <div class="port-empty">—</div>
                </template>
              </div>
            </template>
          </template>
        </div>
      </el-card>
    </div>
  </div>
  <!-- connection dialog -->
  <el-dialog title="Port Connection" v-model="showPortDialog" @closed="clearHostPort">
    <el-radio-group v-model="connectionTypePort">
      <el-radio-button label="断开" />
      <el-radio-button label="直连" />
      <el-radio-button label="交换机" />
    </el-radio-group>
    <div v-if="connectionTypePort === '直连'" style="margin-top:20px;">
      <el-form>
        <el-form-item label="Server">
          <el-autocomplete
            v-model="directConnectionPort.host"
            :fetch-suggestions="hostQuerySearchPort"
            placeholder="Server Hostname"
            @select="handleHostSelectPort"
            @input="clearHostPort"
          />
        </el-form-item>
        <el-form-item label="Interface">
          <el-autocomplete
            v-model="directConnectionPort.interfaceName"
            :fetch-suggestions="hostInterQuerySearchPort"
            placeholder="Interface (with PCI)"
            :disabled="!selectedServerId"
            @select="selectInterfacePort"
            @input="selectedTargetId = null"
          />
        </el-form-item>
      </el-form>
    </div>
    <div v-if="connectionTypePort === '交换机'" style="margin-top:20px;">
      <el-form>
        <el-form-item label="目标交换机">
          <el-autocomplete
            v-model="switchConnectionPort.switchName"
            :fetch-suggestions="switchQuerySearchPort"
            placeholder="Switch Name"
            @select="handleSelectSwitchPort"
            @input="clearSwitchPort"
          />
        </el-form-item>
        <el-form-item label="端口号">
          <el-input
            v-model="switchConnectionPort.switchPort"
            type="number"
          />
        </el-form-item>
      </el-form>
    </div>
    <template #footer>
      <el-button @click="showPortDialog = false">取消</el-button>
      <el-button type="primary" @click="savePortChanges" :loading="saving" :disabled="!currentUser.is_admin">保存</el-button>
    </template>
  </el-dialog>
</template>

<script setup>
import { ref, reactive, onMounted } from 'vue'
import { ElMessage } from 'element-plus'
import { apiFetch, reportError } from '../api.js'
import { interfaceLabel, resolveInterface } from '../selection.js'
import DeviceChat from '../components/DeviceChat.vue'
const switches = ref([])
const currentUser = ref({ id: null, is_admin: false })
const loading = ref(false)
const loadFailed = ref(false)
const saving = ref(false)
const allServers = ref([])
const showPortDialog = ref(false)
const connectionTypePort = ref('断开')
const selectedPort = ref(null)
const selectedServerId = ref(null)
const selectedTargetId = ref(null)
let hostRequest = 0
const directConnectionPort = reactive({ host: '', interfaceName: '' })
const targetInterfacesPort = ref([])
const switchConnectionPort = reactive({ switchName: '', switchPort: null, switchId: null })
async function loadDevices() {
  loading.value = true
  loadFailed.value = false
  try {
    switches.value = await (await apiFetch('/api/link/devices', { credentials: 'include' })).json()
  } catch (error) { loadFailed.value = true; throw error }
  finally { loading.value = false }
}
onMounted(async () => {
  await Promise.all([
    loadDevices(),
    apiFetch('/api/user/me', { credentials: 'include' }).then(r => r.json()).then(data => { currentUser.value = data }),
    apiFetch('/api/server/list', { credentials: 'include' }).then(r => r.json()).then(data => { allServers.value = data })
  ])
})
function hostQuerySearchPort(query, cb) {
  cb(allServers.value.filter(s => s.host?.toLowerCase().includes(query.toLowerCase())).map(s => ({ id: s.id, value: s.host })))
}
function clearHostPort() {
  hostRequest++
  selectedServerId.value = null
  selectedTargetId.value = null
  directConnectionPort.interfaceName = ''
  targetInterfacesPort.value = []
}
async function handleHostSelectPort(item) {
  clearHostPort()
  directConnectionPort.host = item.value
  selectedServerId.value = item.id
  const request = hostRequest
  const data = await (await apiFetch(`/api/server/${item.id}`, { credentials: 'include' })).json()
  if (request === hostRequest && showPortDialog.value && selectedServerId.value === item.id) targetInterfacesPort.value = data.interfaces || []
}
function hostInterQuerySearchPort(query, cb) {
  cb(targetInterfacesPort.value.filter(i => i.interface && interfaceLabel(i).toLowerCase().includes(query.toLowerCase()))
    .map(i => ({ id: i.id, value: interfaceLabel(i) })))
}
function selectInterfacePort(item) { selectedTargetId.value = item.id; directConnectionPort.interfaceName = item.value }
function switchQuerySearchPort(query, cb) {
  cb(switches.value.filter(s => s.name?.toLowerCase().includes(query.toLowerCase())).map(s => ({ id: s.id, value: s.name })))
}
function clearSwitchPort() { switchConnectionPort.switchId = null; switchConnectionPort.switchPort = null }
function handleSelectSwitchPort(item) { clearSwitchPort(); switchConnectionPort.switchName = item.value; switchConnectionPort.switchId = item.id }
function openPortDialog(sw, port) {
  if (!currentUser.value.is_admin || !port) return
  selectedPort.value = port
  showPortDialog.value = true
  connectionTypePort.value = port.connected_to ? '断开' : '直连'
  clearHostPort()
  directConnectionPort.host = ''
  Object.assign(switchConnectionPort, { switchName: '', switchPort: null, switchId: null })
}
async function savePortChanges() {
  if (!currentUser.value.is_admin || !selectedPort.value || saving.value) return
  saving.value = true
  try {
    let url, body
    if (connectionTypePort.value === '直连') {
      const host = allServers.value.find(s => s.id === selectedServerId.value && s.host === directConnectionPort.host)
      const target = resolveInterface(targetInterfacesPort.value, directConnectionPort.interfaceName, selectedTargetId.value)
      if (!host || !target) return ElMessage.warning('Select a server and an exact interface (including PCI) from the suggestions.')
      url = '/api/link/switch_port/interface/connect'
      body = { switch_port_id: selectedPort.value.id, interface_id: target.id }
    } else if (connectionTypePort.value === '交换机') {
      const sw = switches.value.find(s => s.id === switchConnectionPort.switchId && s.name === switchConnectionPort.switchName)
      const portNumber = Number(switchConnectionPort.switchPort)
      const port = Number.isInteger(portNumber) && portNumber > 0 && sw?.ports?.find(p => p.phy_col * sw.num_row + p.phy_row + 1 === portNumber)
      if (!port || port.id === selectedPort.value.id) return ElMessage.warning('请选择有效的目标交换机和不同的端口')
      url = '/api/link/switch_port/connect'
      body = { port_a_id: selectedPort.value.id, port_b_id: port.id }
    } else {
      url = '/api/link/switch_port/disconnect'
      body = { switch_port_id: selectedPort.value.id }
    }
    await apiFetch(url, { method: 'POST', credentials: 'include', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) })
    showPortDialog.value = false
    ElMessage.success('Connection updated')
    await loadDevices()
  } catch (error) { reportError(error) }
  finally { saving.value = false }
}
function getPort(sw, r, c) { return sw.ports?.find(p => p.phy_row === r && p.phy_col === c) }
function portLabel(sw, r, c) {
  const peer = getPort(sw, r, c)?.connected_to
  if (!peer) return 'Unconnected'
  return peer.type === 'interface' ? `${peer.server_host}:${peer.name}` : peer.name
}
function portButtonType(sw, r, c) {
  const peer = getPort(sw, r, c)?.connected_to
  return !peer ? 'default' : peer.type === 'switch_port' ? 'primary' : 'success'
}
</script>

<style scoped>
 .grid-container {
   display: grid;
   gap: 8px;
   overflow-x: auto; /* allow horizontal scroll */
 }
 .grid-cell {
   border: 1px solid #dcdfe6;
   padding: 8px;
   min-height: 60px;
   min-width: 150px; /* ensure enough space */
   font-size: 12px;
 }
 .port-info {
   line-height: 1.4;
 }
 .port-empty {
   color: #909399;
 }
 .card-spacing { margin-bottom: 20px; }
 .port-subtitle {
   font-size: 15px;
   color: #606266;
   margin-top: 4px;
 }
</style>
