import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
const source = path => readFileSync(new URL(`../src/${path}`, import.meta.url), 'utf8')

test('ApplyMachines route and session-gated menu have clear launchers in Servers and Profile', () => {
  assert.match(source('router/index.js'), /path: '\/apply',\s*name: 'ApplyMachines',\s*component: ApplyMachines/)
  assert.match(source('App.vue'), /v-if="currentUser.id" command="ApplyMachines"/)
  assert.match(source('views/Servers.vue'), /name: 'ApplyMachines'/)
  assert.match(source('views/Profile.vue'), /router.push\(\{ name: 'ApplyMachines' \}\)/)
  assert.doesNotMatch(source('views/Profile.vue'), /showApplicationModal|\/api\/app\/submit|newApplication/)
  assert.doesNotMatch(source('views/ApplyMachines.vue'), /route.params|user_id:|uid:/)
})
test('both inventory views and application view use shared hardware components', () => {
  for (const name of ['Servers', 'ApplyMachines']) {
    assert.match(source(`views/${name}.vue`), /<HardwareSummary :hardware="row.hardware"/)
    assert.match(source(`views/${name}.vue`), /<HardwareDetails :hardware="row.hardware"/)
    assert.match(source(`views/${name}.vue`), /deviceMatches/)
  }
  assert.match(source('views/ServerInfo.vue'), /<HardwareDetails :hardware="server.hardware"/)
})
test('summary/detail label every section with displayed snapshot collection and warning/error state', () => {
  for (const component of ['HardwareSummary', 'HardwareDetails']) {
    const text = source(`components/${component}.vue`)
    assert.match(text, /所示数据采集时间：.*collected_at/)
    assert.match(text, /item.section.label/)
    assert.match(text, /item.section.error/)
    assert.match(text, /item.section.warning/)
  }
  assert.match(source('components/HardwareDetails.vue'), /最近检查：.*checked_at/)
})
test('usage counts, legacy applications and approvals have distinct truthful labels', () => {
  const apply = source('views/ApplyMachines.vue')
  assert.match(apply, /暂无使用登记（不代表空闲）/)
  assert.match(apply, /最近确认仍在使用：.*confirmed_at/)
  assert.match(apply, /pending_count \?\? '未知'/)
  assert.match(apply, /account_count \?\? '未知'/)
  assert.match(apply, /my_usage && row.usage\?\.pending_count > 0/)
  assert.match(apply, /旧申请尚未处理.*Profile.*联系管理员/)
  assert.match(source('views/Management.vue'), /row.reason \|\| '未填写（旧版申请）'/)
})
