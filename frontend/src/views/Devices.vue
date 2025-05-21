<template>
  <div>
    <h2>Device Connections</h2>
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
                    type="primary"
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
  <el-dialog title="Port Connection" v-model="showPortDialog">
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
          />
        </el-form-item>
        <el-form-item label="Interface">
          <el-autocomplete
            v-model="directConnectionPort.interfaceName"
            :fetch-suggestions="hostInterQuerySearchPort"
            placeholder="Interface (with PCI)"
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
      <el-button type="primary" @click="savePortChanges">保存</el-button>
    </template>
  </el-dialog>
</template>

<script setup>
import { ref, reactive, onMounted } from 'vue'
const switches = ref([])

// 全量服务器列表，用于联想
const allServers = ref([])

// 端口连接对话框相关状态
const showPortDialog = ref(false)
const connectionTypePort = ref('断开')
const selectedPort = ref(null)
const directConnectionPort = reactive({ host: '', interfaceName: '' })
const targetInterfacesPort = ref([])
// 新增：交换机连接状态
const switchConnectionPort = reactive({ switchName: '', switchPort: null, switchId: null })

onMounted(async () => {
  try {
    const res = await fetch('/api/link/devices', { credentials: 'include' })
    if (res.ok) {
      switches.value = await res.json()
    }
    // 拉取所有 Server 用于 Host 联想
    const sres = await fetch('/api/server/list', { credentials: 'include' })
    if (sres.ok) allServers.value = await sres.json()
  } catch (e) {
    console.error('Failed to load device data', e)
  }
})

// 联想搜索主机
function hostQuerySearchPort(query, cb) {
  cb(
    allServers.value
      .filter(s => s.host && s.host.toLowerCase().includes(query.toLowerCase()))
      .map(s => ({ id: s.id, value: s.host }))
  )
}
// 选择主机后拉取接口列表
async function handleHostSelectPort(item) {
  directConnectionPort.host = item.value
  const res = await fetch(`/api/server/${item.id}`, { credentials: 'include' })
  if (res.ok) {
    const data = await res.json()
    targetInterfacesPort.value = data.interfaces || []
  } else {
    targetInterfacesPort.value = []
  }
}
// 联想搜索接口，显示名称带 PCI
function hostInterQuerySearchPort(query, cb) {
  cb(
    targetInterfacesPort.value
      .filter(i => i.interface && i.pci_address && i.interface.toLowerCase().includes(query.toLowerCase()))
      .map(i => ({ id: i.id, value: `${i.interface} (PCI: ${i.pci_address})` }))
  )
}
// 联想搜索交换机
function switchQuerySearchPort(query, cb) {
  cb(
    switches.value
      .filter(s => s.name.toLowerCase().includes(query.toLowerCase()))
      .map(s => ({ id: s.id, value: s.name }))
  )
}
// 选择交换机
function handleSelectSwitchPort(item) {
  switchConnectionPort.switchName = item.value
  switchConnectionPort.switchId = item.id
}
// 打开端口对话框
function openPortDialog(sw, port) {
  selectedPort.value = port
  showPortDialog.value = true
  connectionTypePort.value = port.connected_to ? '断开' : '直连'
  directConnectionPort.host = ''
  directConnectionPort.interfaceName = ''
  targetInterfacesPort.value = []
  switchConnectionPort.switchName = ''
  switchConnectionPort.switchPort = null
  switchConnectionPort.switchId = null
}
// 保存端口连接变化
async function savePortChanges() {
  if (!selectedPort.value) return showPortDialog.value = false
  if (connectionTypePort.value === '直连') {
    const tgt = targetInterfacesPort.value.find(i => directConnectionPort.interfaceName.includes(i.interface))
    if (!tgt) return
    await fetch('/api/link/switch_port/interface/connect', {
      method: 'POST', credentials: 'include', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({ switch_port_id: selectedPort.value.id, interface_id: tgt.id })
    })
  } else if (connectionTypePort.value === '交换机') {
    if (!switchConnectionPort.switchId || switchConnectionPort.switchPort === null) {
      return ElMessage.error('请选择交换机和端口')
    }
    const sw = switches.value.find(s => s.id === switchConnectionPort.switchId)
    const port = sw.ports.find(
      p => p.phy_col * sw.num_row + p.phy_row + 1 === Number(switchConnectionPort.switchPort)
    )
    if (!port) return ElMessage.error('交换机端口未找到')
    await fetch('/api/link/switch_port/connect', {
      method: 'POST', credentials: 'include', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({ port_a_id: selectedPort.value.id, port_b_id: port.id })
    })
  } else {
    await fetch('/api/link/switch_port/disconnect', {
      method: 'POST', credentials: 'include', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({ switch_port_id: selectedPort.value.id })
    })
  }
  showPortDialog.value = false
  location.reload()
}

/**
 * Find port object for a given switch at row r and col c
 */
function getPort(sw, r, c) {
  return sw.ports.find(p => p.phy_row === r && p.phy_col === c)
}
// Get display label for a port button based on connection status
function portLabel(sw, r, c) {
  const port = getPort(sw, r, c)
  if (!port.connected_to) return 'Unconnected'
  const peer = port.connected_to
  if (peer.type === 'interface') {
    return `${peer.server_host}:${peer.name}`
  }
  return peer.name
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
