// Snapshot formatting shared by inventory, applications and server details.
// Missing measurements are unknown, never zero. A failed check may retain old data.
export const hardwareSections = [
  { key: 'cpu', label: 'CPU' }, { key: 'memory', label: '内存' },
  { key: 'gpus', label: 'GPU' }, { key: 'disks', label: '磁盘' }, { key: 'network', label: '网卡' },
]
const number = value => typeof value === 'number' && Number.isFinite(value) && value >= 0
const decimal = value => Number(value.toFixed(2)).toString()
export function formatBytes(value) {
  if (!number(value)) return '未知'
  if (value >= 2 ** 40) return `${decimal(value / 2 ** 40)} TiB`
  if (value >= 2 ** 30) return `${decimal(value / 2 ** 30)} GiB`
  if (value >= 2 ** 20) return `${decimal(value / 2 ** 20)} MiB`
  return `${decimal(value)} B`
}
export function formatSpeed(value) {
  if (!number(value)) return '速率未知'
  return value >= 1000 ? `${decimal(value / 1000)} Gbps` : `${decimal(value)} Mbps`
}
export function displayValue(value) { return value === null || value === undefined || value === '' ? '未知' : String(value) }
export function displayTime(value) {
  // Preserve the timezone supplied by the backend; do not silently reinterpret naive dates.
  return value ? String(value) : '未知'
}
export function hardwareSection(hardware, key) {
  const raw = hardware?.[key] || {}
  const status = ['ok', 'error', 'unsupported', 'unknown'].includes(raw.status) ? raw.status : 'unknown'
  const data = raw.data ?? null
  const hasData = data !== null && (!Array.isArray(data) || data.length > 0)
  const labels = { ok: '采集成功（快照，非实时）', error: '采集失败', unsupported: '不支持采集', unknown: '采集状态未知' }
  // The backend may additionally flag an old successful snapshot as stale.
  const stale = raw.stale === true || (status !== 'ok' && (hasData || !!raw.collected_at))
  return { status, data, hasData, stale, error: raw.error || null,
    collected_at: raw.collected_at ?? null, checked_at: raw.checked_at ?? null,
    warning: stale || status !== 'ok' || !raw.collected_at || !!raw.error,
    label: `${labels[status]}${stale ? (hasData || raw.collected_at ? ' · 陈旧或不完整数据（非当前完整结果）' : ' · 无可用的成功采集数据') : ''}${status === 'ok' && !raw.collected_at ? ' · 成功时间未知' : ''}`,
  }
}
export function sectionSummary(key, section) {
  const data = section.data
  if (data === null) return '未采集 / 未知'
  if (key === 'cpu') return `${displayValue(data.model)} · ${displayValue(data.sockets)} 插槽 / ${displayValue(data.cores)} 核 / ${displayValue(data.threads)} 线程`
  if (key === 'memory') return `总量 ${formatBytes(data.total_bytes)} · 采集时可用 ${formatBytes(data.available_bytes)}`
  if (!Array.isArray(data)) return '数据格式未知'
  if (!data.length) return section.status === 'ok' ? '该次采集未报告设备' : '未知（无可用设备数据）'
  if (key === 'gpus') return data.map(g => `${displayValue(g.name)} · 显存 ${formatBytes(g.memory_total_bytes)}`).join('；')
  if (key === 'disks') return data.map(d => `${displayValue(d.name)} ${displayValue(d.model)} ${formatBytes(d.size_bytes)} (${displayValue(d.type)})`).join('；') + ' · 原始块设备容量，非文件系统可用空间'
  if (key === 'network') return data.map(n => `${displayValue(n.name)} ${formatSpeed(n.speed_mbps)} ${displayValue(n.state)}${n.error ? ` · 接口错误：${n.error}` : ''}`).join('；')
  return '未知'
}
// Keep inventory rows compact; expanded details retain the complete device list.
export function compactSectionSummary(key, section, limit = 2) {
  const data = section.data
  if (!Array.isArray(data) || data.length <= limit) return sectionSummary(key, section)
  const summary = sectionSummary(key, { ...section, data: data.slice(0, limit) })
  const errors = key === 'network' ? data.filter(item => item.error).length : 0
  return `${summary}；另 ${data.length - limit} 项（展开查看）${errors ? ` · 共 ${errors} 个接口报告错误` : ''}`
}
function searchable(value) {
  if (value === null || value === undefined) return ''
  if (Array.isArray(value)) return value.map(searchable).join(' ')
  if (typeof value === 'object') return Object.values(value).map(searchable).join(' ')
  return String(value)
}
export function hardwareSearchText(hardware) {
  return hardwareSections.map(({ key }) => {
    const section = hardwareSection(hardware, key)
    return `${key} ${searchable(section.data)} ${sectionSummary(key, section)}`
  }).join(' ').toLowerCase()
}
export function deviceMatches(device, query) {
  const tags = (device.tags || []).map(tag => typeof tag === 'string' ? tag : tag.tag)
  return [device.host, device.os, ...tags, hardwareSearchText(device.hardware)].join(' ').toLowerCase().includes(query.trim().toLowerCase())
}
