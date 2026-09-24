"""Read-only Linux hardware snapshots over an existing async SSH connection.

No remote Python, privilege escalation, package installation, or writes. Each
section has an eight-second budget; individual commands have five seconds.
Errors are deliberately constant, never remote stderr or exception messages.
Partial GPU/network inventories may have status=error AND non-null data.
"""
import asyncio
import csv
import io
import json
import re
import shlex
from datetime import datetime, timezone
from decimal import Decimal

COMMAND_TIMEOUT = 5
SECTION_TIMEOUT = 8
CPU_COMMAND = "LC_ALL=C lscpu -J"
MEMORY_COMMAND = "cat /proc/meminfo"
DISK_COMMAND = "LC_ALL=C lsblk -b -J -d -o NAME,MODEL,SIZE,TYPE,ROTA"
PCI_COMMAND = "LC_ALL=C lspci -Dnn"
NVIDIA_COMMAND = "LC_ALL=C nvidia-smi --query-gpu=name,memory.total,pci.bus_id --format=csv,noheader,nounits"
NETWORK_COMMAND = "LC_ALL=C ls -1 -- /sys/class/net"

# Static script; every expanded path is quoted, including paths discovered by
# globbing. Successful enumeration, not absence of a driver, proves no PCI GPU.
GPU_SYSFS_COMMAND = r'''[ -d /sys/bus/pci/devices ] && [ -r /sys/bus/pci/devices ] && [ -x /sys/bus/pci/devices ] || exit 4
for p in /sys/bus/pci/devices/*; do
    [ -d "$p" ] || continue
    class=$(cat -- "$p/class" 2>/dev/null) || exit 1
    vendor=$(cat -- "$p/vendor" 2>/dev/null) || exit 1
    device=$(cat -- "$p/device" 2>/dev/null) || exit 1
    printf 'pci\t%s\t%s\t%s\t%s\n' "${p##*/}" "$vendor" "$device" "$class"
done
for p in /sys/class/drm/card[0-9]*; do
    [ -e "$p/device" ] || continue
    target=$(readlink -f -- "$p/device" 2>/dev/null) || continue
    vendor=$(cat -- "$p/device/vendor" 2>/dev/null) || continue
    device=$(cat -- "$p/device/device" 2>/dev/null) || continue
    vram=$(cat -- "$p/device/mem_info_vram_total" 2>/dev/null) || vram='?'
    printf 'drm\t%s\t%s\t%s\t%s\n' "${target##*/}" "$vendor" "$device" "$vram"
done'''


# Discover raw InfiniBand ports too: loading IPoIB is not a prerequisite.
INFINIBAND_COMMAND = r'''[ -d /sys/class/infiniband ] || exit 0
[ -r /sys/class/infiniband ] && [ -x /sys/class/infiniband ] || exit 1
for adapter in /sys/class/infiniband/*; do
    [ -d "$adapter" ] || continue
    target=$(readlink -f -- "$adapter/device" 2>/dev/null) || target='?'
    [ -r "$adapter/ports" ] && [ -x "$adapter/ports" ] || exit 1
    for port in "$adapter"/ports/*; do
        [ -d "$port" ] || continue
        rate=$(cat -- "$port/rate" 2>/dev/null) || rate='?'
        state=$(cat -- "$port/state" 2>/dev/null) || state='?'
        layer=$(cat -- "$port/link_layer" 2>/dev/null) || layer='?'
        printf 'port\t%s\t%s\t%s\t%s\t%s\t%s\n' "${adapter##*/}" "${target##*/}" "${port##*/}" "$rate" "$state" "$layer"
    done
done'''


class _Failure(Exception):
    def __init__(self, message, status="error"):
        self.message = message
        self.status = status
        super().__init__(message)


class _Partial:
    def __init__(self, data, error):
        self.data = data
        self.error = error


def _now():
    return datetime.now(timezone.utc).isoformat()


async def _run(conn, command, hint):
    try:
        result = await asyncio.wait_for(
            conn.run(command, timeout=COMMAND_TIMEOUT), COMMAND_TIMEOUT
        )
    except (asyncio.TimeoutError, TimeoutError):
        raise _Failure("Hardware query timed out; check host load and SSH connectivity.") from None
    except Exception:
        raise _Failure("Hardware query could not complete; check SSH connectivity and read permissions.") from None
    if result.exit_status != 0:
        status = "unsupported" if result.exit_status in (4, 127) else "error"
        raise _Failure(hint, status)
    if not isinstance(result.stdout, str):
        raise _Failure("Hardware query returned unreadable output; check host locale and utility versions.")
    return result.stdout


async def _attempt(conn, command, hint):
    try:
        return await _run(conn, command, hint)
    except _Failure as error:
        return error


def _int(value, *, positive=False):
    if isinstance(value, bool) or not re.fullmatch(r"[0-9]+", str(value).strip()):
        raise ValueError("invalid quantity")
    number = int(str(value).strip())
    if positive and number == 0:
        raise ValueError("zero quantity")
    return number


def _optional_int(value):
    if value is None or str(value).strip().lower() in ("", "-", "?", "unknown", "n/a", "[n/a]", "not available", "(null)"):
        return None
    return _int(value, positive=True)


def _text(value):
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError("invalid text")
    return value.strip() or None


async def _cpu(conn):
    raw = json.loads(await _run(conn, CPU_COMMAND, "CPU inventory unavailable; check lscpu availability and JSON support."))
    fields = {}

    def visit(rows):
        if not isinstance(rows, list):
            raise ValueError("invalid CPU inventory")
        for row in rows:
            fields[row["field"].strip().rstrip(":").lower()] = row.get("data")
            if "children" in row:
                visit(row["children"])

    visit(raw["lscpu"])
    model = _text(fields.get("model name"))
    sockets = _optional_int(fields.get("socket(s)"))
    cores = _optional_int(fields.get("core(s)"))
    per_socket = _optional_int(fields.get("core(s) per socket"))
    per_core = _optional_int(fields.get("thread(s) per core"))
    threads = _optional_int(fields.get("cpu(s)"))
    if cores is None and sockets is not None and per_socket is not None:
        cores = sockets * per_socket
    if threads is None and cores is not None and per_core is not None:
        threads = cores * per_core
    if not any((model, sockets, cores, threads)):
        raise ValueError("missing CPU fields")
    return {"model": model, "sockets": sockets, "cores": cores, "threads": threads}


async def _memory(conn):
    output = await _run(conn, MEMORY_COMMAND, "Memory inventory unavailable; check Linux procfs access and read permissions.")
    values = {}
    for line in output.splitlines():
        key, separator, value = line.partition(":")
        if key not in ("MemTotal", "MemAvailable"):
            continue
        parts = value.split()
        if not separator or len(parts) != 2 or parts[1] != "kB" or key in values:
            raise ValueError("invalid memory field")
        values[key] = _int(parts[0], positive=(key == "MemTotal")) * 1024
    total = values["MemTotal"]
    available = values.get("MemAvailable")
    if available is not None and available > total:
        raise ValueError("invalid memory range")
    return {"total_bytes": total, "available_bytes": available}


async def _disks(conn):
    raw = json.loads(await _run(conn, DISK_COMMAND, "Disk inventory unavailable; check lsblk availability, JSON support and read permissions."))
    disks = {}

    def visit(rows):
        if not isinstance(rows, list):
            raise ValueError("invalid disk inventory")
        for row in rows:
            name, kind = _text(row["name"]), _text(row["type"])
            if not name or not kind:
                raise ValueError("missing disk identity")
            if kind not in ("part", "loop", "rom", "ram", "zram") and not re.match(r"^(loop|ram|zram)[0-9]+$", name):
                size = _int(row["size"])
                rota = row.get("rota")
                if rota is not None:
                    if isinstance(rota, bool):
                        pass
                    elif str(rota).lower() in ("0", "1", "false", "true"):
                        rota = str(rota).lower() in ("1", "true")
                    else:
                        raise ValueError("invalid rotational flag")
                disks.setdefault(name, {"name": name, "model": _text(row.get("model")), "size_bytes": size, "type": kind, "rotational": rota})
            if "children" in row:
                visit(row["children"])

    visit(raw["blockdevices"])
    return list(disks.values())


def _pci_address(value):
    match = re.fullmatch(r"(?:(0*[0-9a-fA-F]{4}):)?([0-9a-fA-F]{2}:[0-9a-fA-F]{2}\.[0-7])", value.strip())
    if not match:
        raise ValueError("invalid PCI address")
    return f"{(match[1] or '0000')[-4:]}:{match[2]}".lower()


def _hex_id(value, digits):
    if not re.fullmatch(r"(?:0x)?[0-9a-fA-F]{%d}" % digits, value):
        raise ValueError("invalid PCI identifier")
    return value.lower().removeprefix("0x")


def _parse_pci(output):
    devices = {}
    for line in output.splitlines():
        if not line.strip():
            continue
        match = re.fullmatch(r"(\S+)\s+.+?\s+\[([0-9a-fA-F]{4})\]:\s+(.+)", line)
        if not match:
            raise ValueError("invalid PCI inventory")
        address = _pci_address(match[1])
        if not match[2].startswith("03"):
            continue
        identity = re.search(r"\[([0-9a-fA-F]{4}):([0-9a-fA-F]{4})\]", match[3])
        if not identity:
            raise ValueError("missing PCI identity")
        name = match[3][:identity.start()].strip()
        devices[address] = {"vendor": identity[1].lower(), "device": identity[2].lower(), "name": name or None}
    return devices


def _parse_gpu_sysfs(output):
    devices, drm = {}, {}
    for line in output.splitlines():
        fields = line.split("\t")
        if len(fields) != 5 or fields[0] not in ("pci", "drm"):
            raise ValueError("invalid graphics sysfs inventory")
        kind, address, vendor, device, extra = fields
        # Non-PCI DRM devices (e.g. platform framebuffers) cannot identify a PCI GPU.
        if kind == "drm":
            try:
                address = _pci_address(address)
            except ValueError:
                continue
        else:
            address = _pci_address(address)
        info = {"vendor": _hex_id(vendor, 4), "device": _hex_id(device, 4), "name": None}
        if kind == "pci":
            if _hex_id(extra, 6).startswith("03"):
                devices[address] = info
        else:
            try:
                info["memory"] = None if extra == "?" else _int(extra)
            except ValueError:
                # Bad telemetry must not erase otherwise valid PCI identity.
                info["memory"] = None
            drm[address] = info
    return devices, drm


def _parse_nvidia(output):
    devices = {}
    for row in csv.reader(io.StringIO(output), skipinitialspace=True, strict=True):
        if not row:
            continue
        # NVIDIA versions do not all quote embedded commas in product names.
        # The last two fields have strictly validated numeric/address types.
        if len(row) < 3:
            raise ValueError("invalid NVIDIA row")
        name = ", ".join(row[:-2]).strip()
        memory = row[-2].strip()
        address = _pci_address(row[-1])
        if not name or address in devices:
            raise ValueError("invalid NVIDIA identity")
        devices[address] = {
            "name": name, "vendor": "NVIDIA",
            "memory_total_bytes": None if memory.lower() in ("n/a", "[n/a]", "not supported", "[not supported]") else _int(memory) * 1024 ** 2,
            "pci_address": address,
        }
    return devices


async def _gpus(conn):
    pci, sysfs = await asyncio.gather(
        _attempt(conn, PCI_COMMAND, "PCI graphics inventory unavailable; check PCI utility availability and read permissions."),
        _attempt(conn, GPU_SYSFS_COMMAND, "PCI graphics inventory unavailable; check Linux PCI/DRM sysfs access."),
    )
    inventory = None
    drm = {}
    names = {}
    if not isinstance(pci, _Failure):
        try:
            names = _parse_pci(pci)
            inventory = dict(names)
        except (ValueError, TypeError):
            pci = _Failure("PCI graphics inventory is malformed; check PCI utility output and version.")
    if not isinstance(sysfs, _Failure):
        try:
            sys_devices, drm = _parse_gpu_sysfs(sysfs)
            # A bound DRM card also proves presence if PCI and DRM scans straddle
            # hotplug. Never turn a real DRM device into an empty GPU inventory.
            for address, info in drm.items():
                sys_devices.setdefault(address, info)
            # A complete source can fill gaps in the other source during hotplug.
            inventory = {**sys_devices, **(inventory or {})}
        except (ValueError, TypeError):
            sysfs = _Failure("Graphics sysfs inventory is malformed; check driver and sysfs values.")
    nvidia = {}
    problems = []
    needs_nvidia = inventory is None or any(info["vendor"] == "10de" for info in inventory.values())
    if needs_nvidia:
        output = await _attempt(conn, NVIDIA_COMMAND, "NVIDIA telemetry unavailable; check NVIDIA utility, driver health and device access.")
        if isinstance(output, _Failure):
            problems.append(output.message)
        else:
            try:
                nvidia = _parse_nvidia(output)
            except (ValueError, csv.Error):
                problems.append("NVIDIA telemetry is malformed; check driver and utility compatibility.")
    if inventory is None:
        if nvidia:
            return _Partial(list(nvidia.values()), "PCI inventory unavailable; additional graphics devices cannot be ruled out. Check PCI sysfs access or PCI utility availability.")
        status = "unsupported" if pci.status == sysfs.status == "unsupported" else "error"
        raise _Failure("Graphics inventory unavailable; check PCI sysfs access or PCI utility availability and GPU drivers. No absence of GPUs was inferred.", status)
    result = []
    for address, info in inventory.items():
        vendor = {"10de": "NVIDIA", "1002": "AMD", "8086": "Intel"}.get(info["vendor"], "PCI vendor " + info["vendor"])
        item = {"name": names.get(address, {}).get("name") or f"{vendor} device {info['device']}", "vendor": vendor, "memory_total_bytes": None, "pci_address": address}
        if vendor == "NVIDIA":
            if address in nvidia:
                item = nvidia.pop(address)
                if item["memory_total_bytes"] is None:
                    problems.append("NVIDIA memory telemetry unavailable; check driver support and device access.")
            elif not problems:
                problems.append("A detected NVIDIA GPU has no telemetry; check driver health and device access.")
        elif vendor == "AMD":
            telemetry = drm.get(address)
            if telemetry and telemetry["vendor"] == "1002" and telemetry["device"] == info["device"]:
                item["memory_total_bytes"] = telemetry["memory"]
            if item["memory_total_bytes"] is None:
                problems.append("AMD memory telemetry unavailable; check DRM driver support and sysfs read permissions.")
        result.append(item)
    result.extend(nvidia.values())
    return _Partial(result, " ".join(dict.fromkeys(problems))) if problems else result


def _network_detail_command(name):
    if not name or name in (".", "..") or any(char in name for char in ("/", "\x00", "\n", "\r", "\t")):
        raise ValueError("invalid interface name")
    # The complete untrusted path is shell-quoted once; all subsequent expansions
    # remain double-quoted. No interface name is ever interpolated as shell code.
    return "p=" + shlex.quote("/sys/class/net/" + name) + r'''
[ -d "$p" ] || exit 1
target=$(readlink -f -- "$p") || exit 1
case "$target" in /sys/devices/virtual/*) printf 'kind\tvirtual\n'; exit 0;; esac
printf 'kind\tphysical\n'
for field in type address operstate speed dev_port dev_id; do
    value=$(cat -- "$p/$field" 2>/dev/null) || value='?'
    printf '%s\t%s\n' "$field" "$value"
done
target=$(readlink -f -- "$p/device" 2>/dev/null) || target='?'
printf 'pci\t%s\n' "${target##*/}"
for adapter in "$p"/device/infiniband/*; do
    [ -d "$adapter/ports" ] || continue
    for port in "$adapter"/ports/*; do
        [ -d "$port" ] || continue
        rate=$(cat -- "$port/rate" 2>/dev/null) || rate='?'
        state=$(cat -- "$port/state" 2>/dev/null) || state='?'
        printf 'ib\t%s\t%s\t%s\n' "${port##*/}" "$rate" "$state"
    done
done'''


def _ib_speed(rate):
    match = re.fullmatch(r"([0-9]+(?:\.[0-9]+)?)\s*([GMK]?)b/sec(?:\s+\([^\r\n]*\))?", rate, re.IGNORECASE)
    if not match:
        raise ValueError("invalid InfiniBand rate")
    mbps = Decimal(match[1]) * {"G": Decimal(1000), "M": Decimal(1), "K": Decimal("0.001"), "": Decimal("0.000001")}[match[2].upper()]
    if mbps <= 0 or mbps > Decimal("1e12"):
        raise ValueError("invalid link rate")
    return int(mbps) if mbps == mbps.to_integral_value() else float(mbps)


def _ib_port(fields, ports):
    index = fields.get("dev_port", "?")
    if index != "?":
        index = _int(index)
        dev_id = fields.get("dev_id", "?")
        if re.fullmatch(r"0x[0-9a-fA-F]+", dev_id) and int(dev_id, 16) != index:
            raise ValueError("conflicting InfiniBand port mapping")
        desired = str(index + 1)
        matched = [port for port in ports if port[0] == desired]
    elif re.fullmatch(r"0x[0-9a-fA-F]+", fields.get("dev_id", "?")):
        desired = str(int(fields["dev_id"], 16) + 1)
        matched = [port for port in ports if port[0] == desired]
    else:
        matched = ports if len(ports) == 1 else []
    if len(matched) != 1:
        raise ValueError("ambiguous InfiniBand port")
    return matched[0]


def _parse_network(name, output, covered_ports=None):
    fields, ports = {}, []
    for line in output.splitlines():
        parts = line.split("\t")
        if parts[0] == "ib" and len(parts) == 4:
            ports.append(parts[1:])
        elif len(parts) == 2 and parts[0] not in fields:
            fields[parts[0]] = parts[1]
        else:
            raise ValueError("invalid network inventory")
    if fields.get("kind") == "virtual":
        return None
    if fields.get("kind") != "physical":
        raise ValueError("missing network identity")
    kind = fields.get("type")
    if kind not in ("1", "32", "?", None):
        return None
    item = {"name": name, "mac": None, "speed_mbps": None, "state": None, "pci_address": None, "error": None}
    errors = []
    mac = fields.get("address", "?")
    if re.fullmatch(r"[0-9a-fA-F]{2}(?::[0-9a-fA-F]{2}){5}(?:(?::[0-9a-fA-F]{2}){14})?", mac):
        item["mac"] = mac.lower()
    elif mac == "?":
        errors.append("Interface address unavailable; check driver sysfs read permissions.")
    else:
        errors.append("Interface address is malformed; check driver sysfs values.")
    state = fields.get("operstate", "?")
    if state in ("up", "down", "unknown", "dormant", "notpresent", "lowerlayerdown", "testing"):
        item["state"] = state
    elif state == "?":
        errors.append("Interface state unavailable; check driver sysfs read permissions.")
    else:
        errors.append("Interface state is malformed; check driver sysfs values.")
    try:
        item["pci_address"] = _pci_address(fields.get("pci", "?"))
    except ValueError:
        pass  # USB/platform NICs legitimately have no PCI address.
    selected_port = None
    if kind == "32":
        try:
            selected_port = _ib_port(fields, ports)
            if item["pci_address"] and covered_ports is not None:
                covered_ports.add((item["pci_address"], selected_port[0]))
        except ValueError:
            pass
    if item["state"] in ("down", "dormant", "notpresent", "lowerlayerdown", "testing"):
        errors.append("Interface is not up; link speed is unknown. Check carrier and port state.")
    elif kind == "32":
        try:
            if selected_port is None:
                raise ValueError("ambiguous InfiniBand port")
            if not re.fullmatch(r"4:\s*ACTIVE", selected_port[2], re.IGNORECASE):
                raise ValueError("inactive InfiniBand port")
            item["speed_mbps"] = _ib_speed(selected_port[1])
        except ValueError:
            errors.append("InfiniBand rate unavailable; check port mapping, active state and sysfs read permissions.")
    elif kind == "1":
        try:
            item["speed_mbps"] = _int(fields.get("speed", "?"), positive=True)
        except ValueError:
            errors.append("Ethernet speed unavailable; check carrier, driver support and sysfs read permissions.")
    else:
        errors.append("Interface type unavailable; check driver support and sysfs read permissions.")
    item["error"] = " ".join(errors) or None
    return item


def _parse_raw_ib(output, covered_ports):
    items = []
    seen = set()
    for line in output.splitlines():
        parts = line.split("\t")
        if len(parts) != 7 or parts[0] != "port":
            raise ValueError("invalid InfiniBand inventory")
        _, adapter, address, port, rate, state, layer = parts
        if layer == "Ethernet":
            continue  # RoCE is already represented by its Ethernet interface.
        if not adapter or any(char in adapter for char in ("/", "\x00")):
            raise ValueError("invalid InfiniBand identity")
        port = str(_int(port, positive=True))
        try:
            address = _pci_address(address)
        except ValueError:
            address = None
        identity = (address or adapter, port)
        if identity in covered_ports or identity in seen:
            continue
        seen.add(identity)
        item = {"name": f"{adapter}/port{port}", "mac": None, "speed_mbps": None,
                "state": None, "pci_address": address, "error": None}
        if re.fullmatch(r"4:\s*ACTIVE", state, re.IGNORECASE):
            item["state"] = "up"
            try:
                item["speed_mbps"] = _ib_speed(rate)
            except ValueError:
                item["error"] = "InfiniBand rate unavailable; check driver support and sysfs read permissions."
        else:
            item["state"] = "down" if re.fullmatch(r"[0123]:\s*.+", state) else "unknown"
            item["error"] = "InfiniBand port is not active; check carrier, port state and sysfs read permissions."
        if layer not in ("InfiniBand", "Ethernet"):
            item["error"] = "RDMA link layer unavailable; check driver support and sysfs read permissions."
        items.append(item)
    return items


async def _network(conn):
    output, ib_output = await asyncio.gather(
        _attempt(conn, NETWORK_COMMAND, "Network inventory unavailable; check Linux network sysfs access and read permissions."),
        _attempt(conn, INFINIBAND_COMMAND, "InfiniBand inventory unavailable; check RDMA driver sysfs access and read permissions."),
    )
    names = [] if isinstance(output, _Failure) else list(dict.fromkeys(name for name in output.splitlines() if name and name != "lo"))
    # Five other section queries can still be in flight. Stay below the usual
    # sshd MaxSessions=10 instead of bursting a channel for every interface.
    limit = asyncio.Semaphore(3)
    covered_ports = set()

    async def one(name):
        try:
            command = _network_detail_command(name)
            async with limit:
                detail = await _run(conn, command, "Interface telemetry unavailable; check driver sysfs access and read permissions.")
            return _parse_network(name, detail, covered_ports)
        except _Failure as error:
            message = error.message
        except Exception:
            message = "Interface telemetry is malformed; check interface naming and driver sysfs values."
        return {"name": name, "mac": None, "speed_mbps": None, "state": None, "pci_address": None, "error": message}

    items = [item for item in await asyncio.gather(*(one(name) for name in names)) if item is not None]
    if not isinstance(ib_output, _Failure):
        try:
            items.extend(_parse_raw_ib(ib_output, covered_ports))
        except (ValueError, TypeError):
            ib_output = _Failure("InfiniBand inventory is malformed; check driver sysfs values.")
    failures = [source for source in (output, ib_output) if isinstance(source, _Failure)]
    if isinstance(output, _Failure) and not items:
        raise output
    if not items and failures:
        raise failures[0]
    messages = [failure.message for failure in failures]
    if any(item["error"] for item in items):
        messages.append("Some interface telemetry is unavailable; inspect interface errors and check port state or driver/sysfs access.")
    return _Partial(items, " ".join(messages)) if messages else items


async def _section(conn, collector):
    checked = _now()
    try:
        value = await asyncio.wait_for(collector(conn), SECTION_TIMEOUT)
        if isinstance(value, _Partial):
            return {"status": "error", "data": value.data, "error": value.error, "collected_at": _now(), "checked_at": checked}
        return {"status": "ok", "data": value, "error": None, "collected_at": _now(), "checked_at": checked}
    except _Failure as error:
        status, message = error.status, error.message
    except (asyncio.TimeoutError, TimeoutError):
        status, message = "error", "Hardware section timed out; check host load and SSH connectivity."
    except Exception:
        status, message = "error", "Hardware inventory is malformed or incomplete; check system utility versions and driver/sysfs values."
    return {"status": status, "data": None, "error": message, "collected_at": None, "checked_at": checked}


async def collect_hardware(conn) -> dict:
    """Collect five independent sections; never open or close the caller's SSH.

    UTC ISO timestamps come from the collector's clock. Failed sections contain
    no fabricated values. Partial GPU/network results retain trustworthy items
    with error status and a collection timestamp; callers may persist these
    alongside the error, rather than treating their error as an empty inventory.
    Cancellation still propagates normally to the caller.
    """
    collectors = {"cpu": _cpu, "memory": _memory, "gpus": _gpus, "disks": _disks, "network": _network}
    results = await asyncio.gather(*(_section(conn, collector) for collector in collectors.values()))
    return dict(zip(collectors, results))
