<template>
  <div>
    <h2>Server List</h2>
    <div style="width:90%; margin:auto; margin-bottom:16px;">
      <el-input
        placeholder="Search by Manufacturer"
        v-model="searchTerm"
        style="width:200px; margin-right:8px;"
        @keyup.enter="searchInterfaces"
      />
      <el-button type="primary" @click="searchInterfaces">Search</el-button>
    </div>

    <div v-if="searchResults.length">
      <h3>Search Results</h3>
      <el-table :data="searchResults" style="width:90%; margin: 40px auto;">
        <el-table-column prop="host" label="Host">
          <template #default="{ row }">
            <router-link :to="{ name: 'ServerInfo', params: { id: row.server_id } }">
              {{ row.host }}
            </router-link>
          </template>
        </el-table-column>
        <el-table-column prop="manufacturer" label="Manufacturer" />
      </el-table>
    </div>

    <el-table
      :data="servers"
      :default-sort="{ prop: 'host', order: 'ascending' }"
      style="width: 90%; margin: auto;"
    >
      <el-table-column prop="host" label="Host" sortable>
        <template #default="{ row }">
          <!-- 跳转到 ServerInfo -->
          <router-link :to="{ name: 'ServerInfo', params: { id: row.id } }">
            {{ row.host }}
          </router-link>
        </template>
      </el-table-column>
      <el-table-column label="Tags">
        <template #default="{ row }">
          <el-tag v-for="tag in row.tags" :key="tag" size="small">
            {{ tag }}
          </el-tag>
        </template>
      </el-table-column>
      <el-table-column prop="gateway" label="Gateway">
        <template #default="{ row }">
          {{ row.gateway ? 'Yes' : 'No' }}
        </template>
      </el-table-column>
      <el-table-column label="Proxy">
        <template #default="{ row }">
          <span v-if="row.proxy">{{ row.proxy.host + ':' + row.proxy.port }}</span>
          <span v-else>N/A</span>
        </template>
      </el-table-column>
      <el-table-column prop="os" label="OS"></el-table-column>
      <el-table-column prop="kernel" label="Kernel"></el-table-column>
    </el-table>
  </div>
</template>

<script setup>
import { ref, onMounted } from 'vue'

const servers = ref([])
const searchTerm = ref('')
const searchResults = ref([])

async function fetchServers() {
  const res = await fetch('/api/server/list', { credentials: 'include' })
  if (res.ok) {
    servers.value = await res.json()
  } else {
    console.error('Failed to fetch servers:', res.status)
  }
}

async function searchInterfaces() {
  if (!searchTerm.value) {
    searchResults.value = []
    return
  }
  const res = await fetch(
    `/api/server/search?manufacturer=${encodeURIComponent(searchTerm.value)}`,
    { credentials: 'include' }
  )
  if (res.ok) {
    searchResults.value = await res.json()
  } else {
    console.error('Search failed:', res.status)
  }
}

onMounted(fetchServers)
</script>
