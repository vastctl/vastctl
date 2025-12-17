"""Command modules for VastLab CLI."""

from .instances import (
    start, stop, kill, list_cmd, status, use, refresh,
    connect, restart_jupyter, ssh, run, remove, search, search_cpu
)
from .transfer import cp, sftp, backup, restore, backups, sync_files
from .cloud import login, logout, whoami, sync_cloud
from .profiles import profiles_group
from .config import config_group
from .env import env_group

__all__ = [
    # Instance commands
    "start",
    "stop",
    "kill",
    "list_cmd",
    "status",
    "use",
    "refresh",
    "connect",
    "restart_jupyter",
    "ssh",
    "run",
    "remove",
    "search",
    "search_cpu",
    # Transfer commands
    "cp",
    "sftp",
    "backup",
    "restore",
    "backups",
    "sync_files",
    # Cloud commands
    "login",
    "logout",
    "whoami",
    "sync_cloud",
    # Profile commands
    "profiles_group",
    # Config commands
    "config_group",
    # Env commands
    "env_group",
]
