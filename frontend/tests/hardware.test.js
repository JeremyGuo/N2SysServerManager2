import test from 'node:test'
import assert from 'node:assert/strict'
import { formatBytes, formatSpeed, displayValue, displayTime, hardwareSection, sectionSummary, hardwareSearchText, deviceMatches, compactSectionSummary } from '../src/hardware.js'

const snapshot = (data, status = 'ok') => ({ data, status, error: null, collected_at: '2026-09-22T00:00:00Z', checked_at: '2026-09-23T00:00:00Z' })
const inventory = {
  cpu: snapshot({ model: 'AMD EPYC 9654', sockets: 2, cores: 192, threads: 384 }),
  memory: snapshot({ total_bytes: 2 ** 40, available_bytes: null }),
  gpus: snapshot([{ name: 'RTX 4090', vendor: 'NVIDIA', memory_total_bytes: null, pci_address: '01:00.0' }]),
  disks: snapshot([{ name: 'nvme0n1', model: 'Samsung', size_bytes: 2 * 2 ** 40, type: 'disk', rotational: false }]),
  network: snapshot([{ name: 'eth0', mac: 'aa:bb:cc:dd:ee:ff', speed_mbps: 100000, state: 'up', pci_address: '02:00.0', error: null }]),
}

test('bytes use binary units while speed uses decimal Mbps/Gbps; null is never zero', () => {
  assert.equal(formatBytes(2 ** 30), '1 GiB')
  assert.equal(formatBytes(1.5 * 2 ** 40), '1.5 TiB')
  assert.equal(formatBytes(32 * 2 ** 20), '32 MiB')
  assert.equal(formatBytes(0), '0 B')
  assert.equal(formatSpeed(25000), '25 Gbps')
  assert.equal(formatSpeed(100), '100 Mbps')
  assert.equal(formatSpeed(0), '0 Mbps')
  for (const value of [null, undefined, '', '123', NaN, Infinity, -1]) {
    assert.equal(formatBytes(value), '未知')
    assert.equal(formatSpeed(value), '速率未知')
  }
  assert.equal(displayValue(0), '0')
  assert.equal(displayValue(null), '未知')
})
test('all failed, unsupported, unknown sections preserve stale data AND last-success/check timestamps', () => {
  for (const status of ['error', 'unsupported', 'unknown', 'bad-status']) {
    const hardware = { cpu: { ...snapshot({ model: 'old CPU', cores: null }, status), error: 'SSH timeout' } }
    const section = hardwareSection(hardware, 'cpu')
    assert.equal(section.warning, true)
    assert.equal(section.stale, true)
    assert.match(section.label, /陈旧或不完整数据/)
    assert.doesNotMatch(section.label, /^采集成功/)
    assert.equal(section.error, 'SSH timeout')
    assert.equal(section.collected_at, '2026-09-22T00:00:00Z')
    assert.equal(section.checked_at, '2026-09-23T00:00:00Z')
    assert.match(sectionSummary('cpu', section), /old CPU/)
    assert.match(sectionSummary('cpu', section), /未知 核/)
    assert.equal(hardware.cpu.status, status)
  }
})
test('missing sections and null measurements remain unknown, not zero devices or free resources', () => {
  for (const hardware of [undefined, null, {}]) {
    const section = hardwareSection(hardware, 'memory')
    assert.equal(section.status, 'unknown')
    assert.equal(section.data, null)
    assert.equal(section.stale, false)
    assert.match(sectionSummary('memory', section), /未知/)
  }
  assert.match(sectionSummary('gpus', hardwareSection(inventory, 'gpus')), /显存 未知/)
  assert.match(sectionSummary('memory', hardwareSection(inventory, 'memory')), /采集时可用 未知/)
  assert.match(sectionSummary('disks', hardwareSection(inventory, 'disks')), /原始块设备容量，非文件系统可用空间/)
  assert.match(sectionSummary('gpus', hardwareSection({ gpus: snapshot([], 'error') }, 'gpus')), /未知/)
  assert.equal(sectionSummary('gpus', hardwareSection({ gpus: snapshot([]) }, 'gpus')), '该次采集未报告设备')
})
test('successful snapshots still show non-realtime qualifier; missing success timestamp is warned', () => {
  assert.match(hardwareSection(inventory, 'cpu').label, /快照，非实时/)
  const section = hardwareSection({ cpu: { data: { model: 'CPU' }, status: 'ok' } }, 'cpu')
  assert.equal(section.warning, true)
  assert.match(section.label, /成功时间未知/)
  assert.equal(displayTime(null), '未知')
  assert.equal(displayTime('2026-09-23T08:00:00+08:00'), '2026-09-23T08:00:00+08:00')
})
test('NIC per-interface errors and unknown speed stay visible in summaries', () => {
  const section = hardwareSection({ network: snapshot([{ name: 'eth1', speed_mbps: null, error: 'ethtool unavailable' }]) }, 'network')
  const text = sectionSummary('network', section)
  assert.match(text, /速率未知/)
  assert.match(text, /接口错误：ethtool unavailable/)
})
test('hardware filtering covers every section, formatted sizes/speeds, host, tags and stale retained data', () => {
  const device = { host: 'lab-box', os: 'Debian', tags: ['research', { tag: 'ML' }], hardware: inventory }
  for (const query of ['EPYC', '1 TiB', '4090', 'nvidia', 'Samsung', '2 TiB', '100 Gbps', '02:00.0', 'aa:bb', 'lab-box', 'DEBIAN', 'research', 'ml']) {
    assert.equal(deviceMatches(device, query), true, query)
  }
  assert.equal(deviceMatches(device, 'not-there'), false)
  assert.equal(deviceMatches({}, 'not-there'), false)
  assert.ok(hardwareSearchText({ ...inventory, cpu: { ...inventory.cpu, status: 'error' } }).includes('epyc'))
})

test('backend-marked aged success and retained empty snapshots are explicitly stale', () => {
  const old = hardwareSection({ cpu: { ...inventory.cpu, stale: true } }, 'cpu')
  assert.equal(old.stale, true)
  assert.equal(old.warning, true)
  assert.match(old.label, /陈旧或不完整数据/)
  const empty = hardwareSection({ gpus: snapshot([], 'error') }, 'gpus')
  assert.equal(empty.stale, true)
  assert.match(empty.label, /陈旧或不完整数据/)
})

test('compact inventory summaries bound device lists but never hide the presence of NIC errors', () => {
  const section = hardwareSection({ network: snapshot([
    { name: 'eth0', speed_mbps: 1000 }, { name: 'eth1', speed_mbps: 1000 },
    { name: 'eth2', speed_mbps: null, error: 'speed read failed' },
  ]) }, 'network')
  const summary = compactSectionSummary('network', section)
  assert.match(summary, /另 1 项（展开查看）/)
  assert.match(summary, /1 个接口报告错误/)
  assert.match(sectionSummary('network', section), /eth2.*speed read failed/)
  assert.equal(section.data.length, 3)
})

test('uncollected stale flag never falsely claims a retained successful result', () => {
  const section = hardwareSection({ cpu: { status: 'unknown', data: null, stale: true, collected_at: null } }, 'cpu')
  assert.match(section.label, /无可用的成功采集数据/)
  assert.doesNotMatch(section.label, /保留上次成功结果/)
})
