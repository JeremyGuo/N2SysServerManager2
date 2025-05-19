<template>
  <div>
    <h2>Server List</h2>
    <el-table :data="servers" style="width: 90%; margin: auto;">
      <el-table-column prop="host" label="Host">
        <template #default="{ row }">
          <el-link>{{ row.host }}</el-link>
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
      <el-table-column prop="os" label="OS"></el-table-column>
      <el-table-column prop="kernel" label="Kernel"></el-table-column>
    </el-table>
  </div>
</template>

<script setup>
import { ref, onMounted } from 'vue'

const servers = ref([])

async function fetchServers() {
  const res = await fetch('/api/server/list')
  if (res.ok) {
    servers.value = await res.json()
  } else {
    console.error('Failed to fetch servers:', res.status)
  }
}

onMounted(fetchServers)
</script>
