export function profileFields(user) {
  return { realname: user.realname ?? '', mail: user.mail ?? '', public_key: user.public_key ?? '' }
}
export function validateProfileSave(user, original, currentPassword, newPassword, confirmation) {
  if (newPassword !== confirmation) return 'New password and confirm password do not match'
  const next = profileFields(user)
  if (!newPassword && Object.keys(next).every(key => next[key] === original[key])) return 'No changes to save.'
  if (!currentPassword) return 'Current password is required to save changes.'
  return ''
}
