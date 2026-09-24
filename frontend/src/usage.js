export const MAX_SELECTION = 50
export function usageBlockedReason(device) {
  if (device.gateway) return '网关账号由系统维护，不能申请'
  if (device.usage?.my_usage?.status === 'active') return '您已登记使用，可确认仍在使用或结束登记'
  if (device.usage?.my_usage?.status === 'pending') return '您的申请待审批，可取消申请'
  // pending_count includes OTHER users and legacy applications. It cannot tell us
  // whether this user has a legacy request. The API enforces that conflict (409).
  return ''
}
export function validateApplication(serverIds, reason, devices) {
  if (!serverIds.length) return '请至少选择一台机器'
  if (serverIds.length > MAX_SELECTION) return '每次最多选择 50 台机器'
  if (!reason.trim()) return '请填写申请用途 / 原因'
  if (Array.from(reason).length > 500) return '申请原因不得超过 500 字'
  if (new Set(serverIds).size !== serverIds.length) return '机器选择重复，请重新选择'
  if (serverIds.some(id => !devices.some(d => d.id === id && !usageBlockedReason(d)))) return '所选机器不可申请，请检查当前使用登记或刷新列表'
  return ''
}
export function usageStatus(status) {
  return ({ active: '使用登记中', pending: '待审批', ended: '已结束登记', cancelled: '已取消', rejected: '已拒绝' })[status] || status || '无使用登记'
}

export function canRequestSudo(device) {
  return device?.gateway === false && device.usage?.my_usage?.status === 'active'
    && device.usage.has_account === true && device.usage.has_sudo === false
}
export function validateSudoReason(reason) {
  if (!reason.trim()) return '请填写 sudo 权限用途 / 原因'
  if (Array.from(reason.trim()).length > 500) return 'sudo 申请原因不得超过 500 字'
  return ''
}
