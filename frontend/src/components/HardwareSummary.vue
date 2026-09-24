<template>
  <div class="hardware-summary">
    <div v-for="item in sections" :key="item.key" class="hardware-item">
      <strong>{{ item.label }}：</strong><span>{{ item.summary }}</span>
      <small :class="{ warning: item.section.warning }">
        {{ item.section.label }} · 所示数据采集时间：{{ displayTime(item.section.collected_at) }}
        <span v-if="item.section.error"> · 错误：{{ item.section.error }}</span>
      </small>
    </div>
  </div>
</template>
<script setup>
import { computed } from 'vue'
import { hardwareSections, hardwareSection, compactSectionSummary, displayTime } from '../hardware.js'
const props = defineProps({ hardware: { type: Object, default: () => ({}) } })
const sections = computed(() => hardwareSections.map(item => {
  const section = hardwareSection(props.hardware, item.key)
  return { ...item, section, summary: compactSectionSummary(item.key, section) }
}))
</script>
<style scoped>
.hardware-summary {font-size:12px;text-align:left;overflow-wrap:anywhere;}
.hardware-item + .hardware-item {margin-top:6px;}
small {display:block;color:#606266;font-size:11px;}
.warning {color:#9a5600;}
</style>
