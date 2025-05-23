import asyncssh
from logger import logger

async def sshServerGetKernel(conn: asyncssh.SSHClientConnection) -> str:
    """
    Get the kernel version of the server.
    """
    result = await conn.run("uname -r", timeout=3)
    if result.exit_status != 0:
        err_result = result.stderr.strip()
        return ""
    return result.stdout.strip()

async def sshServerGetRelease(conn: asyncssh.SSHClientConnection) -> str:
    """
    Get the release version of the server.
    Make it a one line string and shorten it.
    """
    result = await conn.run("cat /etc/*release | grep -i DISTRIB_DESCRIPTION", timeout=3)
    if result.exit_status != 0:
        err_result = result.stderr.strip()
        return ""
    release = str(result.stdout.strip().split("=")[1].strip().replace('"', ""))
    return release

async def sshServerGetIBNICs(conn: asyncssh.SSHClientConnection) -> list[dict]:
    """
    Get the infiniband controllers of the server from PCI.
    If it has corresponding interfaces, add them to the dictionary.
    """
    result = await conn.run("lspci -D | grep -i infiniband", timeout=3)
    if result.exit_status != 0:
        err_result = result.stderr.strip()
        return []
    nics = []
    for line in result.stdout.splitlines():
        parts = line.split()
        if len(parts) < 2:
            continue
        pci_address = parts[0]
        nic_name = " ".join(parts[1:])
        nics.append({"pci_address": pci_address, "nic_name": nic_name, "interface_name": None})
    # Iterate all interfaces under /sys/class/net
    result = await conn.run("ls /sys/class/net", timeout=3)
    if result.exit_status != 0:
        err_result = result.stderr.strip()
        return nics
    interfaces = result.stdout.splitlines()
    for interface in interfaces:
        # Get the PCI address of the interface
        result = await conn.run(f"readlink /sys/class/net/{interface}/device", timeout=3)
        if result.exit_status != 0:
            continue
        pci_address = result.stdout.strip().split("/")[-1]
        # Find the corresponding NIC
        for nic in nics:
            if nic["pci_address"] == pci_address:
                nic["interface_name"] = interface
                break
    return nics

async def sshServerGetNICs(conn: asyncssh.SSHClientConnection) -> list[dict]:
    """
    Get the ethernet controllers and infiniband controllers of the server from PCI.
    If it has corresponding interfaces, add them to the dictionary.
    """
    result = await conn.run("lspci -D | grep -i ethernet", timeout=3)
    if result.exit_status != 0:
        err_result = result.stderr.strip()
        return []
    nics = []
    for line in result.stdout.splitlines():
        parts = line.split()
        if len(parts) < 2:
            continue
        pci_address = parts[0]
        nic_name = " ".join(parts[1:])
        nics.append({"pci_address": pci_address, "nic_name": nic_name, "interface_name": None})
    # Iterate all interfaces under /sys/class/net
    result = await conn.run("ls /sys/class/net", timeout=3)
    if result.exit_status != 0:
        err_result = result.stderr.strip()
        return nics
    interfaces = result.stdout.splitlines()
    for interface in interfaces:
        # Get the PCI address of the interface
        result = await conn.run(f"readlink /sys/class/net/{interface}/device", timeout=3)
        if result.exit_status != 0:
            continue
        pci_address = result.stdout.strip().split("/")[-1]
        # Find the corresponding NIC
        for nic in nics:
            if nic["pci_address"] == pci_address:
                nic["interface_name"] = interface
                break
    return nics

async def sshServerGetAccountLoginDate(conn: asyncssh.SSHClientConnection, user: str) -> tuple[bool, str]:
    """
    Get the last login date of the server of all users
    """
    cmd = f"""{{ last -F -R "{user}" 2>/dev/null | head -1 | grep -q 'still logged in' && date '+%Y-%m-%d %H:%M:%S' || date -d "$(last -F -R "{user}" 2>/dev/null | head -1 | awk '{{print $10, $11, $12, $13}}')" '+%Y-%m-%d %H:%M:%S'; }} || echo '1970-01-01 00:00:00'"""
    result = await conn.run(cmd, timeout=6)
    if result.exit_status != 0:
        err_result = result.stderr.strip()
        return False, ""
    return True, result.stdout.strip()