<template>
  <div>
    <h2>设备筛选 · Server List</h2>
    <router-link :to="{ name: 'ApplyMachines' }">申请机器 / 查看使用登记 →</router-link>
    <div class="filters">
      <el-input v-model="hostFilter" placeholder="筛选主机名 / 标签 / OS / 硬件" clearable style="width:260px" />
      <el-input v-model="searchTerm" placeholder="按网卡 Manufacturer 查询" clearable style="width:260px" @keyup.enter="searchInterfaces" />
      <el-button type="primary" :loading="searching" @click="searchInterfaces">查询网卡</el-button>
    </div>
    <section v-if="searched">
      <h3>网卡查询结果（{{ searchResults.length }}）</h3>
      <el-table :data="searchResults" style="width:90%;margin:20px auto;" empty-text="没有匹配的网卡，请尝试其他厂商或型号。">
        <el-table-column prop="host" label="Host"><template #default="{row}"><router-link :to="`/server/${row.server_id}`">{{ row.host }}</router-link></template></el-table-column>
        <el-table-column prop="manufacturer" label="Manufacturer" />
      </el-table>
    </section>
    <el-table :data="filteredServers" v-loading="loading" :default-sort="{prop:'host',order:'ascending'}" style="width:90%;margin:auto;" empty-text="暂无匹配设备">
      <el-table-column type="expand"><template #default="{row}"><HardwareDetails :hardware="row.hardware" /></template></el-table-column>
      <el-table-column prop="host" label="Host" sortable><template #default="{row}"><router-link :to="`/server/${row.id}`">{{ row.host }}</router-link></template></el-table-column>
      <el-table-column label="Tags"><template #default="{row}"><el-tag v-for="tag in row.tags" :key="tag" size="small">{{ tag }}</el-tag></template></el-table-column>
      <el-table-column prop="gateway" label="Gateway"><template #default="{row}">{{ row.gateway ? 'Yes' : 'No' }}</template></el-table-column>
      <el-table-column label="Proxy"><template #default="{row}">{{ row.proxy ? `${row.proxy.host}:${row.proxy.port}` : 'N/A' }}</template></el-table-column>
      <el-table-column prop="os" label="OS" />
      <el-table-column prop="kernel" label="Kernel" />
      <el-table-column label="硬件摘要（展开查看详情）" min-width="330"><template #default="{row}"><HardwareSummary :hardware="row.hardware" /></template></el-table-column>
    </el-table>
    <DeviceChat />
  </div>
</template>
<script setup>
import { ref, computed, onMounted } from 'vue'
import { apiFetch } from '../api'
import DeviceChat from '../components/DeviceChat.vue'
import HardwareSummary from '../components/HardwareSummary.vue'
import HardwareDetails from '../components/HardwareDetails.vue'
import { deviceMatches } from '../hardware.js'
const servers = ref([])
const hostFilter = ref('')
const searchTerm = ref('')
const searchResults = ref([])
const searched = ref(false)
const loading = ref(false)
const searching = ref(false)
const filteredServers = computed(() => servers.value.filter(s => deviceMatches(s, hostFilter.value)))
async function fetchServers() {
  loading.value = true
  try { servers.value = await (await apiFetch('/api/server/list')).json() }
  finally { loading.value = false }
}
async function searchInterfaces() {
  if (searching.value) return
  if (!searchTerm.value.trim()) { searchResults.value = []; searched.value = false; return }
  searching.value = true
  searched.value = false
  searchResults.value = []
  try {
    searchResults.value = await (await apiFetch(`/api/server/search?manufacturer=${encodeURIComponent(searchTerm.value.trim())}`)).json()
    searched.value = true
  } finally { searching.value = false }
}
onMounted(fetchServers)
</script>
<style scoped>.filters {width:90%;margin:16px auto;display:flex;flex-wrap:wrap;gap:8px;}</style>
