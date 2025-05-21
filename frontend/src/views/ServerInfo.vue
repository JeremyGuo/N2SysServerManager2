<template>
  <el-container>
    <el-main>
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
                  <el-button size="small" v-if="!show_new_server_tag" @click="showAddNewServerTag">+ New Tag</el-button>
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
                      <el-button type="primary" @click="saveIPMI">Save</el-button>
                    </el-col>
                  </el-row>
                </template>
                <template v-else>
                  {{ server.ipmi || 'N/A' }}
                </template>
              </el-form-item>
              <el-form-item v-if="currentUser.is_admin" label="Actions">
                <el-button type="primary" class="button-spacing" @click="refreshServer">Refresh Server Status</el-button>
              </el-form-item>
            </el-form>
          </el-col>
        </el-row>
      </el-card>

      <el-card class="card-spacing">
        <el-row>
          <el-col :span="24">
            <h2>Server Interfaces</h2>
            <el-divider />
            <el-table :data="server.interfaces" style="width: 100%">
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
                    <el-button size="small" v-if="!row.show_new_tag" @click="showAddNewTag(row)">+ New Tag</el-button>
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

      <el-dialog title="连接设置" v-model="showModal">
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
              />
            </el-form-item>
            <el-form-item label="Interface 名字">
              <el-autocomplete v-model="directConnection.interfaceName" :fetch-suggestions="hostInterQuerySearch" placeholder="Interface Name" />
            </el-form-item>
          </el-form>
        </div>
        <div v-if="connectionType === '交换机'" style="margin-top:20px;">
          <el-form>
            <el-form-item label="交换机名字">
              <el-autocomplete v-model="switchConnection.switchName" :fetch-suggestions="switchQuerySearch" placeholder="请输入交换机名字" @select="handleSelectSwitch" />
            </el-form-item>
            <el-form-item label="交换机端口号">
              <el-input v-model="switchConnection.switchPort" type="number" />
            </el-form-item>
          </el-form>
        </div>
        <template #footer>
          <el-button @click="showModal = false">取消</el-button>
          <el-button type="primary" @click="saveChanges">保存</el-button>
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
const currentUser = ref({ id: null, is_admin: false })
const server = reactive({
  id: null,
  host: '',
  port: '',
  is_gateway: false,
  server_status: '',
  is_separated_home: false,
  os_version: '',
  kernel_version: '',
  tags: [],
  ipmi: '',
  interfaces: []
})

const show_new_server_tag = ref(false)
const new_server_tag_value = ref('')
const new_tag_value = ref('')
const old_tag_row_obj = ref(null)

// 新增：连接对话框相关状态
const showModal = ref(false)
const connectionType = ref('断开')
const selectedInterfaceId = ref(null)
const directConnection = reactive({ host: '', interfaceName: '' })
const switchConnection = reactive({ switchName: '', switchPort: null, switchId: null })
const devices = ref([])
const switches = ref([])

// 新增：所有 Server 列表，用于 Host 联想
const allServers = ref([])

// 新增：记录目标服务器 ID 及其接口列表
const selectedServerId = ref(null)
const targetInterfaces = ref([])

// 新增：缓存每台交换机的 num_col 用于端口号计算
const serverSwitchCols = reactive(
  Object.fromEntries(switches.value.map(sw => [sw.id, sw.num_col]))
)

onMounted(async () => {
  // 1. 拉取当前用户，判断是否 admin
  const usrRes = await fetch('/api/user/me', { credentials: 'include' })
  if (usrRes.ok) {
    const u = await usrRes.json()
    currentUser.value.id = u.id
    currentUser.value.is_admin = u.is_admin
  }
  // 2. 拉取 Server 详情
  const id = route.params.id
  const res = await fetch(`/api/server/${id}`, { credentials: 'include' })
  if (res.ok) {
    const data = await res.json()
    Object.assign(server, data)
  } else {
    ElMessage.error('Failed to load server info')
    router.push({ name: 'Servers' })
  }

  // 拉取交换机及端口信息
  const devRes = await fetch('/api/link/devices', { credentials: 'include' })
  if (devRes.ok) {
    const data = await devRes.json()
    devices.value = data
    switches.value = data.map(sw => ({
      id: sw.id,
      value: sw.name,
      num_col: sw.num_col,
      num_row: sw.num_row, // store number of rows for port index calc
      ports: sw.ports
    }))
  }

  // 修改：拉取所有 Server 用于 Host 联想
  const listRes = await fetch('/api/server/list', { credentials: 'include' })
  if (listRes.ok) {
    allServers.value = await listRes.json()
  }
})

function removeServerTag(tag_id) {
  fetch('/api/server/tag/remove', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ tag_id }) })
    .then(r => {
      if (!r.ok) throw new Error()
      server.tags = server.tags.filter(t => t.id !== tag_id)
    })
    .catch(() => ElMessage.error('Failed to remove tag'))
}

function removeTag(tag_id) {
  fetch('/api/server/interface/tag/remove', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ tag_id }) })
    .then(r => {
      if (!r.ok) throw new Error()
      server.interfaces.forEach(rw => {
        rw.tags = rw.tags.filter(t => t.id !== tag_id)
      })
    })
    .catch(() => ElMessage.error('Failed to remove tag'))
}

function confirmServerTagInput() {
  const v = new_server_tag_value.value.trim()
  show_new_server_tag.value = false
  if (v) {
    fetch('/api/server/tag/add', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ server_id: route.params.id, tag: v }) })
      .then(r => {
        if (!r.ok) throw new Error()
        return r.json()
      })
      .then(data => {
        server.tags.push(data)
      })
      .catch(() => ElMessage.error('Failed to add tag'))
  }
}

function showAddNewServerTag() { new_server_tag_value.value = ''; show_new_server_tag.value = true }

function showAddNewTag(row) { if (old_tag_row_obj.value) confirmTagInput(old_tag_row_obj.value); new_tag_value.value = ''; row.show_new_tag = true; old_tag_row_obj.value = row }

function confirmTagInput(row) {
  row.show_new_tag = false
  const v = new_tag_value.value.trim()
  old_tag_row_obj.value = null
  if (v) {
    fetch('/api/server/interface/tag/add', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ interface_id: row.id, tag: v }) })
      .then(r => {
        if (!r.ok) throw new Error()
        return r.json()
      })
      .then(data => {
        row.tags.push(data)
      })
      .catch(() => ElMessage.error('Failed to add tag'))
  }
}

function refreshServer() { fetch('/api/server/refresh', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ server_id: route.params.id }) }).then(() => location.reload()) }

// 修改：Host 联想带回 id
function hostQuerySearch(query, cb) {
  cb(
    allServers.value
      .filter(s => s.host && s.host.toLowerCase().includes(query.toLowerCase()))
      .map(s => ({ id: s.id, value: s.host }))
  )
}

// 新增：选择 Host 后拉取该服务器详情，并缓存其接口
async function handleHostSelect(item) {
  selectedServerId.value = item.id
  directConnection.host = item.value
  const res = await fetch(`/api/server/${item.id}`, { credentials: 'include' })
  if (res.ok) {
    const data = await res.json()
    targetInterfaces.value = data.interfaces || []
  } else {
    ElMessage.error('Failed to load server interfaces')
    targetInterfaces.value = []
  }
}

// 修改：Interface 联想基于 targetInterfaces
function hostInterQuerySearch(query, cb) {
  cb(
    targetInterfaces.value
      .filter(i => i.interface && i.interface.toLowerCase().includes(query.toLowerCase()))
      .map(i => ({ value: i.interface, id: i.id }))
  )
}

// 覆盖：联想搜索交换机
function switchQuerySearch(query, cb) {
  cb(
    switches.value
      .filter(s => s.value.toLowerCase().includes(query.toLowerCase()))
      .map(s => ({ id: s.id, value: s.value }))
  )
}

// 新增：选择交换机时记录 ID
function handleSelectSwitch(item) {
  switchConnection.switchName = item.value
  switchConnection.switchId = item.id
}

// 新增：打开连接对话框
function showLinkToDialog(id) {
  selectedInterfaceId.value = id
  showModal.value = true
  connectionType.value = '断开'
  directConnection.host = ''
  directConnection.interfaceName = ''
  switchConnection.switchName = ''
  switchConnection.switchPort = null
  switchConnection.switchId = null
}

// 覆盖：保存连接逻辑
async function saveChanges() {
  try {
    if (connectionType.value === '断开') {
      await fetch('/api/link/interface/disconnect', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        credentials: 'include',
        body: JSON.stringify({ interface_id: selectedInterfaceId.value })
      })
      ElMessage.success('Disconnected')
    } else if (connectionType.value === '直连') {
      const target = targetInterfaces.value.find(
        i => i.interface === directConnection.interfaceName
      )
      if (!target) throw new Error('Interface not found')
      await fetch('/api/link/interface/connect', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        credentials: 'include',
        body: JSON.stringify({
          interface_a_id: selectedInterfaceId.value,
          interface_b_id: target.id
        })
      })
      ElMessage.success('Connected Directly')
    } else if (connectionType.value === '交换机') {
      if (!switchConnection.switchId || switchConnection.switchPort === null)
        throw new Error('Invalid switch or port')
      const sw = switches.value.find(s => s.id === switchConnection.switchId)
      console.log('Switch:', sw)
      const port = sw.ports.find(
        p => sw.num_row * p.phy_col + p.phy_row + 1 === Number(switchConnection.switchPort)
      )
      if (!port) throw new Error('Port not found')
      await fetch('/api/link/switch_port/interface/connect', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        credentials: 'include',
        body: JSON.stringify({
          switch_port_id: port.id,
          interface_id: selectedInterfaceId.value
        })
      })
      ElMessage.success('Connected to Switch')
    }
    showModal.value = false
    location.reload()
  } catch (e) {
    ElMessage.error(e.message || 'Failed to save connection')
  }
}

function saveIPMI() { fetch('/api/server/ipmi', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ server_id: route.params.id, ipmi: server.ipmi }) }).then(() => ElMessage.success('IPMI info saved')) }

function submitApplication() {}

function logout() { router.push({ name: 'Login' }) }
function gotoHome() { router.push({ name: 'Summary' }) }
function gotoProfile(id) { router.push({ name: 'Profile', params: { id } }) }
</script>

<style scoped>
.card-spacing { margin-bottom: 20px; }
.button-spacing { margin-top: 20px; }
.full-width-input { width: 100%; }
.el-header-button { font-size: 16px; }
</style>
