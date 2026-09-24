// Require an explicit timezone; never guess the browser zone for API timestamps.
export function activityTimestamp(value) {
  if (typeof value !== 'string' || !/T.*(?:Z|[+-]\d{2}:\d{2})$/.test(value)) return NaN
  return Date.parse(value)
}

export function summaryPreferences(saved) {
  return {hide: true, days: Number.isInteger(saved?.days) && saved.days >= 1 && saved.days <= 3650 ? saved.days : 30}
}

export function formatActivity(value) {
  const timestamp = activityTimestamp(value)
  return Number.isFinite(timestamp) ? new Date(timestamp).toLocaleString() : '—'
}

export function isInactiveAdmin(user, days, now = Date.now()) {
  const timestamp = activityTimestamp(user.lastLogin)
  // Unknown dates must remain visible, never silently interpreted as stale.
  return user.isAdmin === true && Number.isFinite(timestamp) && now - timestamp > days * 86400000
}

export function summaryRows(servers, hide, days, now = Date.now()) {
  return servers.flatMap(server => {
    const users = server.users.filter(u => !hide || !isInactiveAdmin(u, days, now))
    const base = {
      server_id: server.id, host: server.host, status: server.status,
      isGateway: server.isGateway, isMounted: server.isMounted,
    }
    if (!users.length) return [{ ...base, account_id: null, accountName: '--', username: '--', user: server.users.length ? '所有账号已被筛选隐藏' : '暂无可登录账号', lastLogin: null }]
    return users.map(u => ({ ...base, account_id: u.id, user: u.user, username: u.username || '--', accountName: u.account_name || '--', sudo: u.sudo, isAdmin: u.isAdmin, lastLogin: u.lastLogin }))
  })
}
