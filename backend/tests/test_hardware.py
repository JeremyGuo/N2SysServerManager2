"""Mock-only hardware regressions: python3 -m pytest backend/tests/test_hardware.py."""
import asyncio
import json
from datetime import datetime, timezone
from pathlib import Path
import shlex
import sys
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import hardware_helpers as hardware


SECRET = "secret-password; unsafe raw stderr and command data"


def result(stdout="", status=0):
    return SimpleNamespace(stdout=stdout, stderr=SECRET, exit_status=status)


class FakeSSH:
    """Map full command strings; no real SSH or shell is ever invoked."""
    def __init__(self, responses=None):
        self.responses = dict(responses or {})
        self.calls = []

    async def run(self, command, **kwargs):
        self.calls.append((command, kwargs))
        response = self.responses.get(command, result(status=127))
        if isinstance(response, BaseException):
            raise response
        if callable(response):
            return await response()
        return response


def run(coroutine):
    return asyncio.run(coroutine)


def cpu(**fields):
    return result(json.dumps({"lscpu": [{"field": key + ":", "data": value} for key, value in fields.items()]}))


def disks(rows):
    return result(json.dumps({"blockdevices": rows}))


def interface(name, *, kind="physical", type="1", mac="a0:b1:c2:d3:e4:f5", state="up", speed="25000", pci="0000:04:00.0", dev_port="0", dev_id="0x0", ports=()):
    fields = {"kind": kind, "type": type, "address": mac, "operstate": state, "speed": speed, "pci": pci, "dev_port": dev_port, "dev_id": dev_id}
    output = "".join(f"{key}\t{value}\n" for key, value in fields.items())
    output += "".join("ib\t" + "\t".join(port) + "\n" for port in ports)
    return hardware._network_detail_command(name), result(output)


def baseline():
    return {
        hardware.CPU_COMMAND: cpu(**{"Model name": "AMD EPYC 7543 32-Core Processor", "Socket(s)": "2", "Core(s) per socket": "32", "Thread(s) per core": "2", "CPU(s)": "128"}),
        hardware.MEMORY_COMMAND: result("MemTotal:       264000000 kB\nMemFree:        12000000 kB\nMemAvailable:  123456789 kB\n"),
        hardware.DISK_COMMAND: disks([{"name": "nvme0n1", "model": " ACME NVMe ", "size": 2000000000000, "type": "disk", "rota": False}]),
        hardware.PCI_COMMAND: result("0000:04:00.0 Ethernet controller [0200]: Mellanox Technologies ConnectX-6 [15b3:101b]\n"),
        hardware.GPU_SYSFS_COMMAND: result("pci\t0000:04:00.0\t0x15b3\t0x101b\t0x020000\n"),
        hardware.NETWORK_COMMAND: result("lo\nenp4s0\n"),
        hardware.INFINIBAND_COMMAND: result(""),
        **dict([interface("enp4s0")]),
    }


def collect(responses=None):
    conn = FakeSSH(baseline() if responses is None else responses)
    return run(hardware.collect_hardware(conn)), conn


def test_realistic_all_sections_exact_shapes_and_timestamps():
    responses = baseline()
    responses[hardware.PCI_COMMAND] = result(
        "0000:21:00.0 3D controller [0302]: NVIDIA Corporation A100 [10de:20b0] (rev a1)\n"
        "0000:61:00.0 Display controller [0380]: Advanced Micro Devices, Inc. [AMD/ATI] Instinct MI210 [1002:740f] (rev 02)\n"
    )
    responses[hardware.GPU_SYSFS_COMMAND] = result(
        "pci\t0000:21:00.0\t0x10de\t0x20b0\t0x030200\n"
        "pci\t0000:61:00.0\t0x1002\t0x740f\t0x038000\n"
        "drm\t0000:61:00.0\t0x1002\t0x740f\t68719476736\n"
    )
    responses[hardware.NVIDIA_COMMAND] = result('"NVIDIA A100, SXM4", 81920, 00000000:21:00.0\n')
    responses[hardware.NETWORK_COMMAND] = result("lo\nenp4s0\nib0\nvethabc\n")
    responses.update([interface("ib0", type="32", mac="80:00:00:02:fe:80:00:00:00:00:00:00:02:c9:03:00:22:ab:cd:ef", speed="?", ports=[("1", "200 Gb/sec (4X HDR)", "4: ACTIVE")]), interface("vethabc", kind="virtual")])
    snapshot, conn = collect(responses)
    assert set(snapshot) == {"cpu", "memory", "gpus", "disks", "network"}
    for section in snapshot.values():
        assert set(section) == {"status", "data", "error", "collected_at", "checked_at"}
        assert section["status"] == "ok", section
        assert section["error"] is None
        checked, collected = (datetime.fromisoformat(section[key]) for key in ("checked_at", "collected_at"))
        assert checked.utcoffset() == collected.utcoffset() == timezone.utc.utcoffset(checked)
        assert checked <= collected <= datetime.now(timezone.utc)
    assert snapshot["cpu"]["data"] == {"model": "AMD EPYC 7543 32-Core Processor", "sockets": 2, "cores": 64, "threads": 128}
    assert snapshot["memory"]["data"] == {"total_bytes": 270336000000, "available_bytes": 126419751936}
    assert snapshot["gpus"]["data"] == [
        {"name": "NVIDIA A100, SXM4", "vendor": "NVIDIA", "memory_total_bytes": 85899345920, "pci_address": "0000:21:00.0"},
        {"name": "Advanced Micro Devices, Inc. [AMD/ATI] Instinct MI210", "vendor": "AMD", "memory_total_bytes": 68719476736, "pci_address": "0000:61:00.0"},
    ]
    assert snapshot["disks"]["data"] == [{"name": "nvme0n1", "model": "ACME NVMe", "size_bytes": 2000000000000, "type": "disk", "rotational": False}]
    assert [item["name"] for item in snapshot["network"]["data"]] == ["enp4s0", "ib0"]
    for item in snapshot["network"]["data"]:
        assert set(item) == {"name", "mac", "speed_mbps", "state", "pci_address", "error"}
        assert item["error"] is None
    assert snapshot["network"]["data"][1]["speed_mbps"] == 200000
    assert all(3 <= options["timeout"] <= 8 for _, options in conn.calls)
    assert all("sudo " not in command and "python" not in command for command, _ in conn.calls)


def test_partial_missing_tools_do_not_stop_other_sections_or_leak_stderr():
    responses = baseline()
    responses[hardware.CPU_COMMAND] = result(SECRET, status=127)
    responses[hardware.DISK_COMMAND] = OSError(SECRET)
    snapshot, _ = collect(responses)
    assert snapshot["cpu"]["status"] == "unsupported"
    assert snapshot["disks"]["status"] == "error"
    for key in ("memory", "gpus", "network"):
        assert snapshot[key]["status"] == "ok"
    for key in ("cpu", "disks"):
        assert snapshot[key]["data"] is None
        assert snapshot[key]["collected_at"] is None
        assert snapshot[key]["checked_at"]
        assert snapshot[key]["error"]
    assert SECRET not in json.dumps(snapshot)
    assert hardware.CPU_COMMAND not in snapshot["cpu"]["error"]


def test_all_unavailable_does_not_return_empty_inventories():
    snapshot, _ = collect({})
    for section in snapshot.values():
        assert section["status"] == "unsupported"
        assert section["data"] is None
        assert section["collected_at"] is None
        assert section["error"]


@pytest.mark.parametrize("fields,expected", [
    ({"Model name": "AArch64", "CPU(s)": "8"}, {"model": "AArch64", "sockets": None, "cores": None, "threads": 8}),
    ({"Model name": "Virtual CPU", "Socket(s)": "-", "Core(s) per socket": "4", "CPU(s)": "8"}, {"model": "Virtual CPU", "sockets": None, "cores": None, "threads": 8}),
    ({"Socket(s)": 2, "Core(s) per socket": 8, "Thread(s) per core": 2}, {"model": None, "sockets": 2, "cores": 16, "threads": 32}),
])
def test_cpu_missing_topology_is_not_fabricated(fields, expected):
    responses = baseline()
    responses[hardware.CPU_COMMAND] = cpu(**fields)
    snapshot, _ = collect(responses)
    assert snapshot["cpu"]["status"] == "ok"
    assert snapshot["cpu"]["data"] == expected


def test_cpu_nested_lscpu_json():
    responses = baseline()
    responses[hardware.CPU_COMMAND] = result(json.dumps({"lscpu": [{"field": "CPU(s):", "data": 16}, {"field": "Vendor ID:", "data": "GenuineIntel", "children": [{"field": "Model name:", "data": "Intel Xeon", "children": [{"field": "Socket(s):", "data": 1}, {"field": "Core(s) per socket:", "data": 8}]}]}]}))
    snapshot, _ = collect(responses)
    assert snapshot["cpu"]["data"] == {"model": "Intel Xeon", "sockets": 1, "cores": 8, "threads": 16}


@pytest.mark.parametrize("key,output", [
    ("cpu", result("not json " + SECRET)),
    ("cpu", cpu(**{"CPU(s)": "-8"})),
    ("cpu", cpu(**{"CPU(s)": "3.5"})),
    ("cpu", cpu(**{"CPU(s)": True})),
    ("memory", result("MemTotal: -3 kB\n")),
    ("memory", result("MemTotal: 0 kB\n")),
    ("memory", result("MemTotal: 1 MB\n")),
    ("memory", result("MemTotal: 8 kB\nMemAvailable: 9 kB\n")),
    ("memory", result("MemTotal: 8 kB\nMemAvailable: -1 kB\n")),
    ("memory", result("MemAvailable: 9 kB\n")),
    ("disks", disks([{"name": "sda", "size": -1, "type": "disk"}])),
    ("disks", disks([{"name": "sda", "size": "3.5", "type": "disk"}])),
    ("disks", disks([{"name": "sda", "size": 5, "type": "disk", "rota": "-1"}])),
])
def test_malformed_quantities_are_errors_not_fabricated_values(key, output):
    responses = baseline()
    responses[{"cpu": hardware.CPU_COMMAND, "memory": hardware.MEMORY_COMMAND, "disks": hardware.DISK_COMMAND}[key]] = output
    snapshot, _ = collect(responses)
    assert snapshot[key]["status"] == "error"
    assert snapshot[key]["data"] is None
    assert SECRET not in snapshot[key]["error"]
    assert all(section["status"] == "ok" for name, section in snapshot.items() if name != key)


def test_memory_old_kernel_has_unknown_available_not_free_estimate():
    responses = baseline()
    responses[hardware.MEMORY_COMMAND] = result("MemTotal: 8192 kB\nMemFree: 100 kB\n")
    snapshot, _ = collect(responses)
    assert snapshot["memory"]["data"] == {"total_bytes": 8388608, "available_bytes": None}


def test_memory_zero_available_is_valid():
    responses = baseline()
    responses[hardware.MEMORY_COMMAND] = result("MemTotal: 8192 kB\nMemAvailable: 0 kB\n")
    snapshot, _ = collect(responses)
    assert snapshot["memory"]["data"]["available_bytes"] == 0


def test_disk_partitions_loops_rom_excluded_logical_and_physical_deduplicated():
    logical = {"name": "vg-data", "model": None, "size": "1000", "type": "lvm", "rota": "0"}
    responses = baseline()
    responses[hardware.DISK_COMMAND] = disks([
        {"name": "sda", "model": "HDD", "size": "2000", "type": "disk", "rota": "1", "children": [{"name": "sda1", "size": "1500", "type": "part", "children": [logical]}]},
        {"name": "sdb", "size": "2000", "type": "disk", "rota": None, "children": [logical]},
        logical,
        {"name": "loop0", "size": "garbage", "type": "loop"},
        {"name": "sr0", "size": "garbage", "type": "rom"},
        {"name": "zram0", "size": "999", "type": "disk"},
        {"name": "nvme0n1p1", "size": "999", "type": "part"},
    ])
    snapshot, _ = collect(responses)
    data = snapshot["disks"]["data"]
    assert snapshot["disks"]["status"] == "ok"
    assert [item["name"] for item in data] == ["sda", "vg-data", "sdb"]
    assert [item["rotational"] for item in data] == [True, False, None]


@pytest.mark.parametrize("available_source", ["pci", "sysfs", "both"])
def test_no_gpu_requires_successful_pci_enumeration(available_source):
    responses = baseline()
    if available_source == "pci":
        responses[hardware.GPU_SYSFS_COMMAND] = result(status=127)
    elif available_source == "sysfs":
        responses[hardware.PCI_COMMAND] = result(status=127)
    snapshot, conn = collect(responses)
    assert snapshot["gpus"]["status"] == "ok"
    assert snapshot["gpus"]["data"] == []
    assert hardware.NVIDIA_COMMAND not in [command for command, _ in conn.calls]


def nvidia_responses():
    responses = baseline()
    responses[hardware.PCI_COMMAND] = result("0000:21:00.0 3D controller [0302]: NVIDIA Corporation A100 [10de:20b0] (rev a1)\n")
    responses[hardware.GPU_SYSFS_COMMAND] = result("pci\t0000:21:00.0\t0x10de\t0x20b0\t0x030200\n")
    return responses


@pytest.mark.parametrize("telemetry", [result(status=127), result(status=9), result(""), result("NVIDIA A100, -1, 00000000:21:00.0\n"), result("NVIDIA A100, 3.5, 00000000:21:00.0\n"), result("NVIDIA A100, N/A, 00000000:21:00.0\n"), result("NVIDIA A100, 40960, injected-address\n")])
def test_missing_or_bad_nvidia_driver_is_not_no_gpu(telemetry):
    responses = nvidia_responses()
    responses[hardware.NVIDIA_COMMAND] = telemetry
    snapshot, _ = collect(responses)
    section = snapshot["gpus"]
    assert section["status"] == "error"
    assert len(section["data"]) == 1
    assert section["data"][0]["vendor"] == "NVIDIA"
    assert section["data"][0]["memory_total_bytes"] is None
    assert section["error"]
    assert section["collected_at"]
    assert SECRET not in json.dumps(section)


@pytest.mark.parametrize("line", ['"NVIDIA A100, SXM4", 40960, 00000000:21:00.0\n', 'NVIDIA A100, SXM4, 40960, 00000000:21:00.0\n'])
def test_nvidia_csv_embedded_comma_quoted_and_unquoted(line):
    responses = nvidia_responses()
    responses[hardware.NVIDIA_COMMAND] = result(line)
    snapshot, _ = collect(responses)
    assert snapshot["gpus"]["status"] == "ok"
    assert snapshot["gpus"]["data"][0] == {"name": "NVIDIA A100, SXM4", "vendor": "NVIDIA", "memory_total_bytes": 42949672960, "pci_address": "0000:21:00.0"}


def test_gpu_telemetry_without_pci_inventory_is_explicit_partial():
    responses = baseline()
    responses[hardware.PCI_COMMAND] = responses[hardware.GPU_SYSFS_COMMAND] = result(status=127)
    responses[hardware.NVIDIA_COMMAND] = result("NVIDIA A100, 40960, 00000000:21:00.0\n")
    snapshot, _ = collect(responses)
    assert snapshot["gpus"]["status"] == "error"
    assert len(snapshot["gpus"]["data"]) == 1
    assert "additional" in snapshot["gpus"]["error"]


def test_empty_nvidia_output_does_not_prove_no_gpu():
    responses = baseline()
    responses[hardware.PCI_COMMAND] = responses[hardware.GPU_SYSFS_COMMAND] = result(status=127)
    responses[hardware.NVIDIA_COMMAND] = result("")
    snapshot, _ = collect(responses)
    assert snapshot["gpus"]["status"] == "unsupported"
    assert snapshot["gpus"]["data"] is None


@pytest.mark.parametrize("vram,expected_status", [("17179869184", "ok"), ("?", "error"), ("-1", "error"), ("1.5", "error")])
def test_amd_sysfs_vram_and_pci_name_fallback(vram, expected_status):
    responses = baseline()
    responses[hardware.PCI_COMMAND] = result("0000:61:00.0 VGA compatible controller [0300]: Advanced Micro Devices, Inc. [AMD/ATI] Navi 21 [1002:73bf] (rev c1)\n")
    responses[hardware.GPU_SYSFS_COMMAND] = result("pci\t0000:61:00.0\t0x1002\t0x73bf\t0x030000\n" + f"drm\t0000:61:00.0\t0x1002\t0x73bf\t{vram}\n")
    snapshot, _ = collect(responses)
    assert snapshot["gpus"]["status"] == expected_status
    assert snapshot["gpus"]["data"][0]["name"] == "Advanced Micro Devices, Inc. [AMD/ATI] Navi 21"
    assert snapshot["gpus"]["data"][0]["memory_total_bytes"] == (17179869184 if expected_status == "ok" else None)


def test_amd_sysfs_inventory_works_without_lspci():
    responses = baseline()
    responses[hardware.PCI_COMMAND] = result(status=127)
    responses[hardware.GPU_SYSFS_COMMAND] = result("pci\t0000:61:00.0\t0x1002\t0x740f\t0x030200\ndrm\t0000:61:00.0\t0x1002\t0x740f\t68719476736\n")
    snapshot, _ = collect(responses)
    assert snapshot["gpus"]["status"] == "ok"
    assert snapshot["gpus"]["data"][0]["name"] == "AMD device 740f"


def test_bad_pci_inventory_is_not_reported_as_no_devices():
    responses = baseline()
    responses[hardware.PCI_COMMAND] = result("malformed output")
    responses[hardware.GPU_SYSFS_COMMAND] = result("pci\tbroken\t-1\t?\t?\n")
    snapshot, _ = collect(responses)
    assert snapshot["gpus"]["status"] == "error"
    assert snapshot["gpus"]["data"] is None


@pytest.mark.parametrize("name", ["eth0;id", "eth$(id)", "eth`id`", "eth'quote", "eth\"quote", "eth&foo", "-eth0"])
def test_interface_paths_are_quoted_even_for_injected_names(name):
    responses = baseline()
    responses[hardware.NETWORK_COMMAND] = result(name + "\n")
    responses.update([interface(name)])
    snapshot, conn = collect(responses)
    assert snapshot["network"]["status"] == "ok"
    assert snapshot["network"]["data"][0]["name"] == name
    details = [command for command, _ in conn.calls if command.startswith("p=")]
    assert len(details) == 1
    assignment = details[0].splitlines()[0]
    assert shlex.split(assignment) == ["p=/sys/class/net/" + name]
    assert assignment == "p=" + shlex.quote("/sys/class/net/" + name)
    assert details[0].count(name) == (0 if "'" in name else 1)
    assert 'cat -- "$p/$field"' in details[0]
    assert 'readlink -f -- "$p"' in details[0]


@pytest.mark.parametrize("name", ["../escape", "eth\tbad", ".", "..", "eth\x00bad"])
def test_invalid_interface_paths_are_not_executed(name):
    responses = baseline()
    responses[hardware.NETWORK_COMMAND] = result(name + "\n")
    snapshot, conn = collect(responses)
    assert snapshot["network"]["status"] == "error"
    assert snapshot["network"]["data"][0]["error"]
    assert not any(command.startswith("p=") for command, _ in conn.calls)


@pytest.mark.parametrize("state,speed", [("down", "1000"), ("dormant", "1000"), ("up", "-1"), ("up", "0"), ("up", "?"), ("up", "garbage"), ("up", "3.5")])
def test_network_unknown_or_down_speed_null_and_error_per_item(state, speed):
    responses = baseline()
    responses[hardware.NETWORK_COMMAND] = result("enp4s0\nenp5s0\n")
    responses.update([interface("enp5s0", state=state, speed=speed)])
    snapshot, _ = collect(responses)
    section = snapshot["network"]
    assert section["status"] == "error"
    assert len(section["data"]) == 2
    assert section["data"][0]["speed_mbps"] == 25000
    assert section["data"][0]["error"] is None
    assert section["data"][1]["speed_mbps"] is None
    assert section["data"][1]["error"]


def test_one_interface_unreadable_preserves_other_interfaces():
    responses = baseline()
    responses[hardware.NETWORK_COMMAND] = result("enp4s0\nenp5s0\n")
    responses[hardware._network_detail_command("enp5s0")] = result(status=1)
    snapshot, _ = collect(responses)
    assert snapshot["network"]["status"] == "error"
    assert snapshot["network"]["data"][0]["speed_mbps"] == 25000
    assert snapshot["network"]["data"][1]["speed_mbps"] is None
    assert SECRET not in json.dumps(snapshot)


@pytest.mark.parametrize("ports,dev_port,expected", [
    ([("1", "100 Gb/sec (4X EDR)", "4: ACTIVE"), ("2", "400 Gb/sec (4X NDR)", "4: ACTIVE")], "1", 400000),
    ([("1", "2.5 Gb/sec (1X SDR)", "4: ACTIVE")], "?", 2500),
    ([("1", "100 Gb/sec (4X EDR)", "1: DOWN")], "0", None),
    ([("1", "-1 Gb/sec", "4: ACTIVE")], "0", None),
    ([("1", "0 Gb/sec", "4: ACTIVE")], "0", None),
    ([("1", "100 Gb/sec", "4: ACTIVE"), ("2", "200 Gb/sec", "4: ACTIVE")], "?", None),
])
def test_infiniband_rates_and_ambiguous_or_inactive_ports(ports, dev_port, expected):
    responses = baseline()
    responses[hardware.NETWORK_COMMAND] = result("ib0\n")
    responses.update([interface("ib0", type="32", speed="?", ports=ports, dev_port=dev_port, dev_id="?")])
    snapshot, _ = collect(responses)
    item = snapshot["network"]["data"][0]
    assert item["speed_mbps"] == expected
    assert snapshot["network"]["status"] == ("error" if expected is None else "ok")


def test_virtual_loopback_and_non_ethernet_interfaces_are_excluded():
    responses = baseline()
    responses[hardware.NETWORK_COMMAND] = result("lo\nbr0\ntun0\nenp4s0\nenp4s0\n")
    responses.update([interface("br0", kind="virtual"), interface("tun0", type="65534")])
    snapshot, conn = collect(responses)
    assert snapshot["network"]["status"] == "ok"
    assert len(snapshot["network"]["data"]) == 1
    assert sum(command == hardware._network_detail_command("enp4s0") for command, _ in conn.calls) == 1
    assert not any("/sys/class/net/lo\n" in command for command, _ in conn.calls)


def test_command_timeout_is_sanitized_and_other_sections_complete():
    responses = baseline()
    responses[hardware.CPU_COMMAND] = asyncio.TimeoutError(SECRET)
    snapshot, _ = collect(responses)
    assert snapshot["cpu"]["status"] == "error"
    assert "timed out" in snapshot["cpu"]["error"]
    assert SECRET not in json.dumps(snapshot)
    assert all(section["status"] == "ok" for key, section in snapshot.items() if key != "cpu")


def test_section_budget_cancels_hung_collection(monkeypatch):
    async def hang():
        await asyncio.sleep(1)
        return result()
    responses = baseline()
    responses[hardware.CPU_COMMAND] = hang
    monkeypatch.setattr(hardware, "SECTION_TIMEOUT", 0.02)
    snapshot, _ = collect(responses)
    assert snapshot["cpu"]["status"] == "error"
    assert "section timed out" in snapshot["cpu"]["error"]
    assert snapshot["memory"]["status"] == "ok"


def test_caller_cancellation_is_not_swallowed():
    async def hang():
        await asyncio.sleep(1)
        return result()

    async def cancel():
        conn = FakeSSH({hardware.CPU_COMMAND: hang})
        task = asyncio.create_task(hardware.collect_hardware(conn))
        await asyncio.sleep(0)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    run(cancel())


def test_raw_infiniband_ports_without_ipoib_are_collected_and_roce_skipped():
    responses = baseline()
    responses[hardware.INFINIBAND_COMMAND] = result(
        "port\tmlx5_0\t0000:41:00.0\t1\t400 Gb/sec (4X NDR)\t4: ACTIVE\tInfiniBand\n"
        "port\tmlx5_0\t0000:41:00.0\t2\t200 Gb/sec (4X HDR)\t1: DOWN\tInfiniBand\n"
        "port\tmlx5_1\t0000:42:00.0\t1\t100 Gb/sec (4X EDR)\t4: ACTIVE\tEthernet\n"
    )
    snapshot, _ = collect(responses)
    section = snapshot["network"]
    assert section["status"] == "error"
    assert [item["name"] for item in section["data"]] == ["enp4s0", "mlx5_0/port1", "mlx5_0/port2"]
    assert section["data"][1] == {"name": "mlx5_0/port1", "mac": None, "speed_mbps": 400000, "state": "up", "pci_address": "0000:41:00.0", "error": None}
    assert section["data"][2]["speed_mbps"] is None
    assert section["data"][2]["state"] == "down"
    assert section["data"][2]["error"]


def test_raw_infiniband_duplicate_ipoib_port_is_not_counted_twice():
    responses = baseline()
    responses[hardware.NETWORK_COMMAND] = result("ib0\n")
    responses.update([interface("ib0", type="32", pci="0000:41:00.0", ports=[("1", "200 Gb/sec (4X HDR)", "4: ACTIVE")])])
    responses[hardware.INFINIBAND_COMMAND] = result("port\tmlx5_0\t0000:41:00.0\t1\t200 Gb/sec (4X HDR)\t4: ACTIVE\tInfiniBand\n")
    snapshot, _ = collect(responses)
    assert snapshot["network"]["status"] == "ok"
    assert [item["name"] for item in snapshot["network"]["data"]] == ["ib0"]


def test_raw_infiniband_survives_missing_network_interface_inventory():
    responses = baseline()
    responses[hardware.NETWORK_COMMAND] = result(status=1)
    responses[hardware.INFINIBAND_COMMAND] = result("port\tmlx5_0\t0000:41:00.0\t1\t200 Gb/sec (4X HDR)\t4: ACTIVE\tInfiniBand\n")
    snapshot, _ = collect(responses)
    assert snapshot["network"]["status"] == "error"
    assert snapshot["network"]["data"][0]["name"] == "mlx5_0/port1"
    assert snapshot["network"]["data"][0]["speed_mbps"] == 200000


def test_bad_raw_infiniband_does_not_erase_ethernet():
    responses = baseline()
    responses[hardware.INFINIBAND_COMMAND] = result("port\tmlx5_0\t0000:41:00.0\t-1\t200 Gb/sec (4X HDR)\t4: ACTIVE\tInfiniBand\n")
    snapshot, _ = collect(responses)
    assert snapshot["network"]["status"] == "error"
    assert snapshot["network"]["data"][0]["name"] == "enp4s0"


def test_conflicting_infiniband_port_mapping_is_unknown_not_wrong_rate():
    responses = baseline()
    responses[hardware.NETWORK_COMMAND] = result("ib0\n")
    responses.update([interface("ib0", type="32", dev_port="0", dev_id="0x1", ports=[("1", "100 Gb/sec", "4: ACTIVE"), ("2", "200 Gb/sec", "4: ACTIVE")])])
    snapshot, _ = collect(responses)
    assert snapshot["network"]["status"] == "error"
    assert snapshot["network"]["data"][0]["speed_mbps"] is None


def test_bad_amd_memory_does_not_erase_detected_gpu_when_pci_utility_missing():
    responses = baseline()
    responses[hardware.PCI_COMMAND] = result(status=127)
    responses[hardware.GPU_SYSFS_COMMAND] = result("pci\t0000:61:00.0\t0x1002\t0x740f\t0x030200\ndrm\t0000:61:00.0\t0x1002\t0x740f\t-1\n")
    snapshot, _ = collect(responses)
    assert snapshot["gpus"]["status"] == "error"
    assert len(snapshot["gpus"]["data"]) == 1
    assert snapshot["gpus"]["data"][0]["vendor"] == "AMD"
    assert snapshot["gpus"]["data"][0]["memory_total_bytes"] is None


def test_shell_scripts_are_posix_syntax_safe_without_running_queries():
    import subprocess
    for command in (hardware.GPU_SYSFS_COMMAND, hardware.INFINIBAND_COMMAND, hardware._network_detail_command("eth'$(id);x")):
        parsed = subprocess.run(["/bin/sh", "-n"], input=command, text=True, capture_output=True, timeout=3)
        assert parsed.returncode == 0, parsed.stderr


def test_command_deadline_applies_even_if_connection_ignores_timeout(monkeypatch):
    async def hang():
        await asyncio.sleep(1)
        return result()
    responses = baseline()
    responses[hardware.CPU_COMMAND] = hang
    monkeypatch.setattr(hardware, "COMMAND_TIMEOUT", 0.02)
    snapshot, _ = collect(responses)
    assert snapshot["cpu"]["status"] == "error"
    assert "query timed out" in snapshot["cpu"]["error"]
    assert snapshot["memory"]["status"] == "ok"


def test_drm_presence_after_pci_scan_is_not_fake_empty_gpu_inventory():
    responses = baseline()
    responses[hardware.GPU_SYSFS_COMMAND] = result("drm\t0000:61:00.0\t0x1002\t0x740f\t68719476736\n")
    snapshot, _ = collect(responses)
    assert snapshot["gpus"]["status"] == "ok"
    assert snapshot["gpus"]["data"][0]["vendor"] == "AMD"
    assert snapshot["gpus"]["data"][0]["memory_total_bytes"] == 68719476736
