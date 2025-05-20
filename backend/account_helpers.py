import asyncssh

async def sshAccountIsExists(conn: asyncssh.SSHClientConnection, account: str) -> bool:
    """
    Check if the account exists on the server.
    """
    result = await conn.run(f"getent passwd {account}")
    if result.exit_status == 0:
        return True
    return False

async def sshAccountCreate(conn: asyncssh.SSHClientConnection, account: str) -> tuple[bool, str]:
    """
    Create the account on the server.
    """
    result = await conn.run(f"sudo useradd {account} -m -d /home/{account}", timeout=3)
    if result.exit_status != 0:
        err_result = result.stderr.strip()
        return False, err_result
    # Change password to 123456
    result = await conn.run(f"echo \"{account}:123456\" | sudo chpasswd --crypt-method=SHA256", timeout=3)
    if result.exit_status != 0:
        err_result = result.stderr.strip()
        return False, err_result
    return True, None

async def sshAccountGetAuthorizedKeys(conn: asyncssh.SSHClientConnection, account: str) -> str:
    """
    Get the authorized keys for the account on the server.
    If /home/{account}/.ssh/authorized_keys does not exist, try to read /home/{account}/.ssh/authorized_keys.n2sysbackup
    """
    result = await conn.run(f"sudo cat /home/{account}/.ssh/authorized_keys", timeout=3)
    if result.exit_status != 0:
        err_result = result.stderr.strip()
        if "No such file or directory" in err_result:
            result = await conn.run(f"sudo cat /home/{account}/.ssh/authorized_keys.n2sysbackup", timeout=3)
            if result.exit_status != 0:
                return ""
            return result.stdout.strip()
        return ""
    return result.stdout.strip()

async def sshAccountIsEnabled(conn: asyncssh.SSHClientConnection, account: str) -> bool:
    """
    Check if the account is enabled on the server.
    The account is enabled if /home/{account}/.ssh/authorized_keys exists and default shell is not /bin/false or /usr/sbin/nologin
    """
    result = await conn.run(f"sudo cat /home/{account}/.ssh/authorized_keys", timeout=3)
    if result.exit_status != 0:
        err_result = result.stderr.strip()
        if "No such file or directory" in err_result:
            return False
        return False
    # Check if the shell is /bin/false or /usr/sbin/nologin
    result = await conn.run(f"sudo getent passwd {account}", timeout=3)
    if result.exit_status != 0:
        err_result = result.stderr.strip()
        return False
    ent = result.stdout.strip()
    if "/bin/false" in ent or "/usr/sbin/nologin" in ent:
        return False
    return True

async def sshAccountEnable(conn: asyncssh.SSHClientConnection, account: str, authorized_keys: str) -> tuple[bool, str]:
    """
    Enable the account on the server: write the authorized keys to /home/{account}/.ssh/authorized_keys
    """
    # Create the .ssh directory if it does not exist
    result = await conn.run(f"sudo mkdir -p /home/{account}/.ssh", timeout=3)
    if result.exit_status != 0:
        err_result = result.stderr.strip()
        return False, err_result
    # Write the authorized keys to /home/{account}/.ssh/authorized_keys
    result = await conn.run(f"echo \"{authorized_keys}\" | sudo tee /home/{account}/.ssh/authorized_keys", timeout=3)
    if result.exit_status != 0:
        err_result = result.stderr.strip()
        return False, err_result
    # Change the owner of the file to {account}:{account}
    result = await conn.run(f"sudo chown {account}:{account} /home/{account}/.ssh/authorized_keys", timeout=3)
    if result.exit_status != 0:
        err_result = result.stderr.strip()
        return False, err_result
    # Change the permissions of the file to 600
    result = await conn.run(f"sudo chmod 600 /home/{account}/.ssh/authorized_keys", timeout=3)
    if result.exit_status != 0:
        err_result = result.stderr.strip()
        return False, err_result
    # If shell is /bin/false or /usr/sbin/nologin, change it to /bin/bash: 1. Get the current shell
    result = await conn.run(f"sudo getent passwd {account}", timeout=3)
    if result.exit_status != 0:
        err_result = result.stderr.strip()
        return False, err_result
    # 2. Check if the shell is /bin/false or /usr/sbin/nologin
    ent = result.stdout.strip()
    if "/bin/false" in ent or "/usr/sbin/nologin" in ent:
        # 3. Change the shell to /bin/bash
        result = await conn.run(f"sudo usermod -s /bin/bash {account}", timeout=3)
        if result.exit_status != 0:
            err_result = result.stderr.strip()
            return False, err_result
    return True, None

async def sshAccountDisable(conn: asyncssh.SSHClientConnection, account: str) -> tuple[bool, str]:
    """
    Disable the account on the server: move the authorized keys to /home/{account}/.ssh/authorized_keys.n2sysbackup
    """
    # If /home/{account}/.ssh/authorized_keys does not exist, return True
    result = await conn.run(f"sudo cat /home/{account}/.ssh/authorized_keys", timeout=3)
    if result.exit_status != 0:
        err_result = result.stderr.strip()
        if "No such file or directory" in err_result:
            return True, None
        return False, err_result
    # Move the authorized keys to /home/{account}/.ssh/authorized_keys.n2sysbackup
    result = await conn.run(f"sudo mv /home/{account}/.ssh/authorized_keys /home/{account}/.ssh/authorized_keys.n2sysbackup", timeout=3)
    if result.exit_status != 0:
        err_result = result.stderr.strip()
        return False, err_result
    return True, None

async def sshAccountSudo(conn: asyncssh.SSHClientConnection, account: str) -> tuple[bool, str]:
    """
    Make the account sudoable on the server: add the account to the sudo group
    """
    result = await conn.run(f"sudo usermod -aG sudo {account}", timeout=3)
    if result.exit_status != 0:
        err_result = result.stderr.strip()
        return False, err_result
    return True, None

async def sshAccountIsSudo(conn: asyncssh.SSHClientConnection, account: str) -> bool:
    """
    Check if the account is sudoable on the server.
    The account is sudoable if it is in the sudo group
    Note: avoid using grep because A and AA will match A
    """
    result = await conn.run(f"sudo getent group sudo | cut -d: -f4", timeout=3)
    if result.exit_status != 0:
        err_result = result.stderr.strip()
        return False
    ent = result.stdout.strip()
    if account in ent.split(","):
        return True
    return False

async def sshAccountUnsudo(conn: asyncssh.SSHClientConnection, account: str) -> tuple[bool, str]:
    """
    Make the account unsudoable on the server: remove the account from the sudo group
    """
    result = await conn.run(f"sudo gpasswd -d {account} sudo", timeout=3)
    if result.exit_status != 0:
        err_result = result.stderr.strip()
        return False, err_result
    return True, None