"""Snapshot builder for VastCtl Cloud sync.

Builds privacy-safe snapshots of instance state for cloud telemetry.

PRIVACY CONSTRAINTS:
- Never include: .env contents, SSH host/port, Jupyter tokens
- Only include: safe metadata like cost/hr, runtime, gpu type, status, labels
"""

from __future__ import annotations
from typing import Dict, Any, List, TYPE_CHECKING
from datetime import datetime, timezone
from pathlib import Path

from .identity import get_or_create_installation_id
from . import __version__

if TYPE_CHECKING:
    from .instance import Instance


def sanitize_instance(inst: "Instance") -> Dict[str, Any]:
    """Convert instance to privacy-safe dict for cloud sync.

    Excludes sensitive data:
    - ssh_host, ssh_port (network info)
    - jupyter_token, jupyter_url (auth credentials)
    - Any environment variables

    Args:
        inst: Instance object

    Returns:
        Dict with safe metadata only
    """
    # Safe fields only
    data = {
        "name": inst.name,
        "vast_id": inst.vast_id,
        "status": inst.status,
        "gpu_type": inst.gpu_type,
        "gpu_count": inst.gpu_count,
        "disk_gb": inst.disk_gb,
        "price_per_hour": inst.price_per_hour,
        "bandwidth_mbps": getattr(inst, "bandwidth_mbps", None),
        "current_cost_estimate": inst.current_cost,
        "project": inst.project,
    }

    # Safe datetime fields
    if hasattr(inst, "created_at") and inst.created_at:
        data["created_at"] = (
            inst.created_at.isoformat()
            if isinstance(inst.created_at, datetime)
            else str(inst.created_at)
        )

    if hasattr(inst, "started_at") and inst.started_at:
        data["started_at"] = (
            inst.started_at.isoformat()
            if isinstance(inst.started_at, datetime)
            else str(inst.started_at)
        )

    if hasattr(inst, "last_accessed") and inst.last_accessed:
        data["last_accessed"] = (
            inst.last_accessed.isoformat()
            if isinstance(inst.last_accessed, datetime)
            else str(inst.last_accessed)
        )

    return data


def build_snapshot(
    config_dir: Path,
    instances: List["Instance"],
) -> Dict[str, Any]:
    """Build a complete snapshot for cloud sync.

    Args:
        config_dir: Path to config directory (for installation_id)
        instances: List of Instance objects

    Returns:
        Dict containing:
        - installation_id: Unique machine identifier
        - ts: ISO timestamp
        - instances: List of sanitized instance data
        - client: CLI version info
        - summary: Aggregate stats
    """
    installation_id = get_or_create_installation_id(config_dir)

    # Build sanitized instance list
    sanitized_instances: List[Dict[str, Any]] = []
    total_cost_per_hour = 0.0
    running_count = 0
    stopped_count = 0

    for inst in instances:
        sanitized = sanitize_instance(inst)
        sanitized_instances.append(sanitized)

        # Aggregate stats
        if inst.status == "running":
            running_count += 1
            if inst.price_per_hour:
                total_cost_per_hour += inst.price_per_hour
        elif inst.status == "stopped":
            stopped_count += 1

    return {
        "installation_id": installation_id,
        "ts": datetime.now(timezone.utc).isoformat(),
        "instances": sanitized_instances,
        "client": {
            "name": "vastctl",
            "version": __version__,
        },
        "summary": {
            "total_instances": len(sanitized_instances),
            "running_instances": running_count,
            "stopped_instances": stopped_count,
            "total_cost_per_hour": round(total_cost_per_hour, 4),
        },
    }


def build_event_snapshot(
    config_dir: Path,
    instances: List["Instance"],
    event_type: str,
    instance_name: str = None,
    details: Dict[str, Any] = None,
) -> Dict[str, Any]:
    """Build a snapshot with event context for specific actions.

    Args:
        config_dir: Path to config directory (for installation_id)
        instances: List of Instance objects
        event_type: Type of event (start, stop, kill, etc.)
        instance_name: Name of affected instance (optional)
        details: Additional event details (optional, must be privacy-safe)

    Returns:
        Snapshot dict with event context
    """
    snapshot = build_snapshot(config_dir, instances)

    snapshot["event"] = {
        "type": event_type,
        "instance_name": instance_name,
        "details": details or {},
    }

    return snapshot
