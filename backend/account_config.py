"""Startup snapshot for newly provisioned remote Linux accounts.

Call load_account_config() once from the FastAPI lifespan, after dotenv has
been loaded and before starting sync workers. Environment changes take effect
only on the next explicit load (normally the next backend start).
"""
from dataclasses import dataclass, field
import os


@dataclass(frozen=True)
class _AccountConfig:
    initial_password: str = field(repr=False)


_config = None


def load_account_config() -> None:
    """Validate and atomically replace the snapshot; never return/log secrets."""
    global _config
    password = os.environ.get("ACCOUNT_INITIAL_PASSWORD", "Qwe123!@#")
    # chpasswd's line-oriented protocol cannot safely carry these separators.
    # Do not strip or shell-escape: spaces, quotes and Unicode are passwords too.
    if not password or any(char in password for char in "\r\n\x00:"):
        raise ValueError("ACCOUNT_INITIAL_PASSWORD must be nonempty and contain no CR, LF, NUL or colon")
    _config = _AccountConfig(password)


def get_initial_password() -> str:
    """Read only the startup snapshot; never reload environment during SSH work."""
    if _config is None:
        raise RuntimeError("Account configuration has not been loaded at startup")
    return _config.initial_password
