import test from 'node:test'
import assert from 'node:assert/strict'
import {isInactiveAdmin, summaryRows} from '../src/summaryRows.js'
const now = Date.parse('2026-09-23T00:00:00Z')
const ago = days => new Date(now - days * 86400000).toISOString()
const users = [
  {id:1,user:'old admin',isAdmin:true,lastLogin:ago(31)},
  {id:2,user:'recent admin',isAdmin:true,lastLogin:ago(2)},
  {id:3,user:'sudo member',isAdmin:false,sudo:true,lastLogin:ago(100)},
  {id:4,user:'unknown admin',isAdmin:true,lastLogin:null},
]
const server = {id:1,host:'lab',users}

test('only system admins older than threshold are hidden', () => {
  assert.equal(isInactiveAdmin(users[0],30,now),true)
  assert.equal(isInactiveAdmin(users[1],30,now),false)
  assert.equal(isInactiveAdmin(users[2],30,now),false)
  assert.equal(isInactiveAdmin(users[3],30,now),false)
  assert.deepEqual(summaryRows([server],true,30,now).map(r=>r.account_id),[2,3,4])
})
test('show all and custom threshold retain accounts without mutating inventory', () => {
  assert.equal(summaryRows([server],false,30,now).length,4)
  assert.equal(summaryRows([server],true,90,now).length,4)
  assert.equal(server.users.length,4)
})
test('exact threshold and invalid/future timestamps stay visible', () => {
  for (const lastLogin of [ago(30),'invalid',ago(-2),null]) {
    assert.equal(isInactiveAdmin({isAdmin:true,lastLogin},30,now),false)
  }
})
test('empty and fully filtered servers remain visible with non-actionable placeholders', () => {
  const rows = summaryRows([{id:1,host:'empty',users:[]},{id:2,host:'hidden',users:[users[0]]}],true,30,now)
  assert.equal(rows.length,2)
  assert.deepEqual(rows.map(r=>r.server_id),[1,2])
  assert.ok(rows.every(r=>r.account_id===null))
})

test('default hiding wins over saved false but custom days are retained', async () => {
  const {summaryPreferences}=await import('../src/summaryRows.js')
  assert.deepEqual(summaryPreferences({hide:false,days:60}),{hide:true,days:60})
  assert.deepEqual(summaryPreferences(null),{hide:true,days:30})
  assert.deepEqual(summaryPreferences({hide:false,days:0}),{hide:true,days:30})
})
test('timezone-bearing timestamps compare the same instant; no-zone stays visible', async () => {
  const {activityTimestamp}=await import('../src/summaryRows.js')
  assert.equal(activityTimestamp('2026-09-23T08:00:00+08:00'),activityTimestamp('2026-09-23T00:00:00Z'))
  assert.ok(Number.isNaN(activityTimestamp('2026-09-23T00:00:00')))
  assert.equal(isInactiveAdmin({isAdmin:true,lastLogin:'2025-01-01T00:00:00'},30,now),false)
})

test('Linux account_name is propagated separately from real/display name and username, with placeholders', () => {
  const rows = summaryRows([{ id: 1, users: [{ id: 11, user: '张三', username: 'login-name', account_name: 'linux-name' }, { id: 12, user: 'Legacy' }] }, { id: 2, users: [] }], false, 30)
  assert.equal(rows[0].user, '张三')
  assert.equal(rows[0].username, 'login-name')
  assert.equal(rows[0].accountName, 'linux-name')
  assert.equal(rows[1].accountName, '--')
  assert.equal(rows[2].accountName, '--')
})
