"""Installation identity management for VastLab.

Provides a unique installation ID per machine for cloud sync and telemetry.
"""

from pathlib import Path
import uuid


def get_or_create_installation_id(config_dir: Path) -> str:
    """Get or create a unique installation ID for this machine.

    The installation ID is a UUID that persists across CLI invocations.
    It's used to identify this installation when syncing with the cloud.

    Args:
        config_dir: Path to the VastLab config directory

    Returns:
        UUID string identifying this installation
    """
    id_file = config_dir / "installation_id"

    if id_file.exists():
        return id_file.read_text().strip()

    # Generate new installation ID
    installation_id = str(uuid.uuid4())

    # Ensure config dir exists
    config_dir.mkdir(parents=True, exist_ok=True)

    # Write installation ID
    id_file.write_text(installation_id)

    return installation_id
