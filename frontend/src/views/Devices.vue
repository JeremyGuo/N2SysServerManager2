<template>
  <div>
    <h2>Device Connections</h2>
    <el-table :data="[]" style="width: 100%">
      <el-table-column prop="port" label="Port"></el-table-column>
      <el-table-column prop="connectedTo" label="Connected To"></el-table-column>
      <el-table-column prop="type" label="Type"></el-table-column>
    </el-table>
  </div>
</template>

<script setup>
// 后续添加数据获取和处理逻辑
</script>
