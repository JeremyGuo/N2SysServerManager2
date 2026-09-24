<template>
  <section class="hardware-details">
    <h3>硬件配置 · 采集快照</h3>
    <p>以下不是实时资源监控。内存可用量仅指采集时；磁盘为原始块设备容量，不是文件系统可用空间，不能直接相加推断可用空间。未知值不代表 0。</p>
    <section v-for="item in sections" :key="item.key" class="hardware-section">
      <h4>{{ item.label }}</h4>
      <p :class="{ warning: item.section.warning }">
        {{ item.section.label }}<br />
        所示数据采集时间：{{ displayTime(item.section.collected_at) }} · 最近检查：{{ displayTime(item.section.checked_at) }}
        <span v-if="item.section.error" class="error">错误：{{ item.section.error }}</span>
      </p>
      <p v-if="item.key === 'cpu' || item.key === 'memory' || !Array.isArray(item.section.data) || !item.section.data.length">{{ sectionSummary(item.key, item.section) }}</p>
      <el-table v-else-if="item.key === 'gpus'" :data="item.section.data" size="small">
        <el-table-column label="GPU 名称"><template #default="{row}">{{ displayValue(row.name) }}</template></el-table-column>
        <el-table-column label="厂商"><template #default="{row}">{{ displayValue(row.vendor) }}</template></el-table-column>
        <el-table-column label="显存（未知≠0）"><template #default="{row}">{{ formatBytes(row.memory_total_bytes) }}</template></el-table-column>
        <el-table-column label="PCI"><template #default="{row}">{{ displayValue(row.pci_address) }}</template></el-table-column>
      </el-table>
      <el-table v-else-if="item.key === 'disks'" :data="item.section.data" size="small">
        <el-table-column label="块设备"><template #default="{row}">{{ displayValue(row.name) }}</template></el-table-column>
        <el-table-column label="型号"><template #default="{row}">{{ displayValue(row.model) }}</template></el-table-column>
        <el-table-column label="原始容量（非空闲空间）"><template #default="{row}">{{ formatBytes(row.size_bytes) }}</template></el-table-column>
        <el-table-column label="类型"><template #default="{row}">{{ displayValue(row.type) }}</template></el-table-column>
        <el-table-column label="旋转介质"><template #default="{row}">{{ row.rotational === true ? '是' : row.rotational === false ? '否' : '未知' }}</template></el-table-column>
      </el-table>
      <el-table v-else-if="item.key === 'network'" :data="item.section.data" size="small">
        <el-table-column label="网卡"><template #default="{row}">{{ displayValue(row.name) }}</template></el-table-column>
        <el-table-column label="MAC"><template #default="{row}">{{ displayValue(row.mac) }}</template></el-table-column>
        <el-table-column label="链路速率"><template #default="{row}">{{ formatSpeed(row.speed_mbps) }}</template></el-table-column>
        <el-table-column label="采集时状态"><template #default="{row}">{{ displayValue(row.state) }}</template></el-table-column>
        <el-table-column label="PCI"><template #default="{row}">{{ displayValue(row.pci_address) }}</template></el-table-column>
        <el-table-column label="接口错误"><template #default="{row}"><span :class="{warning: row.error}">{{ row.error || '未报告' }}</span></template></el-table-column>
      </el-table>
    </section>
  </section>
</template>
<script setup>
import { computed } from 'vue'
import { hardwareSections, hardwareSection, sectionSummary, displayTime, displayValue, formatBytes, formatSpeed } from '../hardware.js'
const props = defineProps({ hardware: { type: Object, default: () => ({}) } })
const sections = computed(() => hardwareSections.map(item => ({ ...item, section: hardwareSection(props.hardware, item.key) })))
</script>
<style scoped>
.hardware-details {text-align:left;padding:8px 20px;overflow-wrap:anywhere;}
.hardware-details > p, .hardware-section > p {font-size:13px;color:#606266;}
.hardware-section {margin:16px 0;}
h4 {margin-bottom:4px;}
.hardware-section .warning, .warning {color:#9a5600;}
.error {display:block;white-space:pre-wrap;}
</style>
