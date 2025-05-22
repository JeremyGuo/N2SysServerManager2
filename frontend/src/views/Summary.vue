<template>
  <div>
    <el-table 
      ref="tableRef"
      :data="tableData" 
      :span-method="spanMethod" 
      :fit="true" 
      style="width: 90%; margin: auto;" 
      :default-sort="{ prop: 'host', order: 'ascending' }"
    >
      <!-- Grouped Host columns -->
      <el-table-column label="Host">
        <el-table-column prop="host" label="Host Name" sortable />
        <el-table-column prop="status" label="Server Status" :width="120"/>
        <el-table-column 
          prop="isGateway" 
          label="Is Gateway" 
          :width="120"
          :filters="gatewayFilters"
          :filter-method="filterGateway"
          :filtered-value="[false]"
        >
          <template #default="{ row }">
            {{ row.isGateway ? 'Yes' : 'No' }}
          </template>
        </el-table-column>
        <el-table-column prop="isMounted" label="Is Mounted" :width="120"/>
      </el-table-column>
      <!-- Other columns -->
      <el-table-column prop="user" label="User" />
      <el-table-column label="Sudo" :width="80">
        <template #default="{ row }">
          <el-switch
            v-model="row.sudo"
            @change="handleSudoChange(row)"
            :disabled="loading || !currentUser.isRoot"
          />
        </template>
      </el-table-column>
      <el-table-column prop="lastLogin" label="Last Login" />
      <el-table-column v-if="currentUser.isRoot" label="Actions">
        <template #default="{ row }">
          <el-button
            type="warning"
            size="small"
            @click="revokePermission(row)"
            :disabled="loading"
          >
            Revoke
          </el-button>
        </template>
      </el-table-column>
    </el-table>
  </div>
</template>

<script setup>
import { ref, computed, onMounted } from 'vue';
import { useRouter } from 'vue-router';

// current user context
const currentUser = ref({ isRoot: true });
// loading flag disables UI during updates
const loading = ref(false);

// sample servers with nested users
const servers = ref([
  {
    host: 'dl1', status: 'Online', isGateway: true, isMounted: true,
    users: [
      { id: 1, user: 'alice', sudo: true, lastLogin: '2025-05-18 08:15:00' },
      { id: 2, user: 'bob', sudo: false, lastLogin: '2025-05-17 16:42:10' }
    ]
  }
]);

// flatten servers->rows for table
const tableData = computed(() =>
  servers.value.flatMap(server =>
    server.users.map(u => ({
      host: server.host,
      status: server.status,
      isGateway: server.isGateway,
      isMounted: server.isMounted,
      account_id: u.id,
      user: u.user,
      sudo: u.sudo,
      lastLogin: u.lastLogin
    }))
  )
);

// filters for gateway column
const gatewayFilters = [
  { text: 'Gateway', value: true },
  { text: 'Not Gateway', value: false }
];

// filter method for gateway
function filterGateway(value, row) {
  return row.isGateway === value;
}

// add a ref to access the table's internal data after sort/filter
const tableRef = ref(null);

// merge rows when host-level fields are the same
function spanMethod({ row, column, rowIndex }) {
  const fields = ['host', 'status', 'isGateway', 'isMounted'];
  if (!fields.includes(column.property)) return;
  // use the table's rendered data (after sorting/filtering) if available
  const data = tableRef.value?.store.states.data.value || tableData.value;
  // skip if same as previous
  if (rowIndex > 0) {
    const prev = data[rowIndex - 1];
    if (fields.every(f => prev[f] === row[f])) {
      return { rowspan: 0, colspan: 0 };
    }
  }
  // count how many following rows match
  let rowSpan = 1;
  for (let i = rowIndex + 1; i < data.length; i++) {
    const next = data[i];
    if (fields.every(f => next[f] === row[f])) {
      rowSpan++;
    } else {
      break;
    }
  }
  return { rowspan: rowSpan, colspan: 1 };
}

// toggle sudo permission
async function handleSudoChange(row) {
  loading.value = true;
  try {
    const url = `/api/account/${row.account_id}/sudo`;
    const res = await fetch(url, { method: 'PUT', credentials: 'include' });
    if (!res.ok) {
        console.error('Failed to update sudo');
        row.sudo = !row.sudo; // revert change
    }
  } catch (e) {
    // handle error
    console.error(e);
    row.sudo = !row.sudo; // revert change
  } finally {
    loading.value = false;
  }
}

// revoke user permission
async function revokePermission(row) {
  loading.value = true;
  try {
    const url = `/api/account/${row.account_id}/revoke`;
    const res = await fetch(url, { method: 'PUT', credentials: 'include' });
    if (res.ok) {
      // delete this user from servers data
      const srv = servers.value.find(s => s.host === row.host);
      if (srv) {
        srv.users = srv.users.filter(u => u.id !== row.account_id);
      }
    } else {
      console.error('Revoke failed');
    }
  } catch (e) {
    console.error(e);
  } finally {
    loading.value = false;
  }
}

// fetch summary data on route enter
const router = useRouter();
async function fetchSummary() {
    console.log('Fetching summary data...');
    const res = await fetch('/api/summary/get', { credentials: 'include' });
    if (!res.ok) {
        router.push({ name: 'Login' });
        return;
    }
    const data = await res.json();
    servers.value = data;
}
onMounted(fetchSummary);
</script>
