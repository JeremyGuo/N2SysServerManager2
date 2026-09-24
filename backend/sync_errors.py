"""Controlled diagnostic messages; never expose raw SSH stderr or commands."""


class SyncCommandError(RuntimeError):
    def __init__(self, operation, exit_status, stderr=""):
        text = (stderr or "").lower()
        if "password is required" in text or "a terminal is required" in text:
            cause = "远端 sudo 需要密码/终端，请为同步服务账号配置所需命令的非交互 sudo 权限"
        elif "not in the sudoers" in text or "not allowed" in text or "permission denied" in text:
            cause = "远端权限不足，请检查同步账号的 sudo 权限及文件访问权限"
        elif "not found" in text or "no such file" in text:
            cause = "远端缺少所需命令、文件或账号，请检查系统工具及账号配置"
        elif "already exists" in text:
            cause = "远端对象已存在，请检查同名账号或目录冲突"
        else:
            cause = "远端命令返回失败，请检查对应系统工具、账号和非交互 sudo 权限"
        self.safe_message = f"{operation}失败（退出码 {exit_status}）：{cause}。"
        super().__init__(self.safe_message)


def command_failure(command, result, collection=False):
    # operation is selected only from constants; the actual command is never shown.
    operations = {
        "useradd": "创建 Linux 账号", "usermod": "更新账号登录/权限状态",
        "getent": "查询系统账号/用户组", "gpasswd": "移除 sudo 用户组",
        "tee": "写入 SSH 公钥", "install": "创建 SSH 目录", "chown": "设置文件所有者",
        "chmod": "设置 SSH 文件权限", "cat": "读取系统/公钥文件", "uname": "采集内核版本",
        "lspci": "采集 PCI 网卡信息", "ls -1": "读取网卡接口", "last -F": "采集登录记录",
    }
    operation = next((label for token, label in operations.items() if token in command), "采集服务器信息" if collection else "同步 Linux 账号")
    return SyncCommandError(operation, result.exit_status, result.stderr)
