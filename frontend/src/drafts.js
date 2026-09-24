// Deliberately memory-only. Only explicitly allowlisted non-secret fields survive expiry.
export function createDraftStore() {
  let owner = null, authenticated = false, epoch = 0
  const values = new Map(), listeners = new Set()
  const identity = id => id == null ? null : String(id)
  function clear() { values.clear(); owner = null; authenticated = false }
  return {
    get epoch() { return epoch },
    confirmUser(id, expectedEpoch = epoch) {
      if (expectedEpoch !== epoch || id == null) return false
      const next = identity(id)
      if (owner !== next) values.clear()
      owner = next; authenticated = true
      return true
    },
    save(kind, id, data) {
      if (!authenticated || owner !== identity(id)) return
      if (kind === 'profile') values.set(kind, {
        realname: String(data.realname ?? ''), mail: String(data.mail ?? ''), public_key: String(data.public_key ?? ''),
        ...(typeof data.public_key_revision === 'string' ? { public_key_revision: data.public_key_revision } : {})
      })
      if (kind === 'apply') values.set(kind, {
        selectedIds: [...new Set((data.selectedIds || []).filter(Number.isInteger))].slice(0, 50),
        reason: String(data.reason ?? ''), needSudo: data.needSudo === true
      })
    },
    restore(kind, id) {
      if (!authenticated || owner !== identity(id) || !values.has(kind)) return null
      const value = values.get(kind)
      return { ...value, ...(value.selectedIds ? { selectedIds: [...value.selectedIds] } : {}) }
    },
    discard(kind, id) { if (owner === identity(id)) values.delete(kind) },
    subscribe(listener) { listeners.add(listener); return () => listeners.delete(listener) },
    expire() {
      for (const listener of listeners) listener('expiry')
      authenticated = false; epoch++
    },
    logout() {
      for (const listener of listeners) listener('logout')
      clear(); epoch++
    }
  }
}
export const drafts = createDraftStore()
