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

async def sshAccountUnsudo(conn: asyncssh.SSHClientConnection, account: str) -> tuple[bool, str]:
    """
    Make the account unsudoable on the server: remove the account from the sudo group
    """
    result = await conn.run(f"sudo gpasswd -d {account} sudo", timeout=3)
    if result.exit_status != 0:
        err_result = result.stderr.strip()
        return False, err_result
    return True, None