"""CLI context management for VastLab.

Provides a context object that holds references to core components
and is passed through Click commands via the pass decorator.
"""

import logging
from dataclasses import dataclass, field
from typing import Optional

from vastctl_core import Config, Registry, ConnectionManager, StorageManager
from vastctl_core.vast_api import VastAPI
from vastctl_core.cloud_client import CloudClient, CloudClientConfig
from vastctl_core.auth import AuthStore
from vastctl_core.snapshot import build_snapshot, build_event_snapshot

logger = logging.getLogger(__name__)


@dataclass
class CliContext:
    """Context object passed through Click commands.

    This replaces the old VastLabCLI class but lives in the CLI package,
    keeping core components as pure library code.
    """
    config: Config
    registry: Registry
    connection: ConnectionManager
    storage: StorageManager
    _api: Optional[VastAPI] = field(default=None, repr=False)
    _cloud: Optional[CloudClient] = field(default=None, repr=False)

    @classmethod
    def create(cls) -> "CliContext":
        """Create a new CLI context with default configuration."""
        config = Config()
        return cls(
            config=config,
            registry=Registry(config),
            connection=ConnectionManager(config),
            storage=StorageManager(config),
        )

    def get_api(self) -> VastAPI:
        """Get a VastAPI instance (creates new each time for context manager use)."""
        return VastAPI(
            api_key=self.config.api_key,
            base_url=self.config.vast_base_url,
            timeout_s=self.config.vast_timeout_seconds,
        )

    def get_cloud(self) -> CloudClient:
        """Get a CloudClient instance (creates new each time for context manager use)."""
        return CloudClient(
            CloudClientConfig(
                base_url=self.config.cloud_base_url,
                enabled=self.config.cloud_enabled,
                timeout_s=float(self.config.cloud_timeout_seconds),
            ),
            AuthStore(token_file=self.config.cloud_token_file),
        )

    def try_cloud_sync(self, silent: bool = True) -> bool:
        """Attempt to sync with cloud, non-fatal on failure.

        This is a helper for auto-sync hooks. It will never raise an exception
        and will only log errors if silent=False.

        Args:
            silent: If True, suppress all error output

        Returns:
            True if sync succeeded, False otherwise
        """
        # Check if auto_sync is enabled
        if not self.config.get("cloud.auto_sync", True):
            return False

        # Check if cloud is enabled
        if not self.config.cloud_enabled:
            return False

        try:
            with self.get_cloud() as cloud:
                if not cloud.is_enabled:
                    return False

                snapshot = build_snapshot(
                    self.config.config_dir,
                    self.registry.list(),
                )
                cloud.push_snapshot(snapshot)
                logger.debug("Auto-sync completed successfully")
                return True

        except Exception as e:
            if not silent:
                logger.warning(f"Auto-sync failed: {e}")
            return False

    def try_cloud_event_sync(
        self,
        event_type: str,
        instance_name: str = None,
        result: str = "success",
        details: dict = None,
        silent: bool = True,
    ) -> bool:
        """Attempt to sync with cloud including event context.

        Use this for event-aware syncing that reports the action outcome.

        Args:
            event_type: Type of event (start, stop, kill, etc.)
            instance_name: Name of affected instance (optional)
            result: Outcome of the action (success, timeout, error)
            details: Additional event details (optional)
            silent: If True, suppress all error output

        Returns:
            True if sync succeeded, False otherwise
        """
        # Check if auto_sync is enabled
        if not self.config.get("cloud.auto_sync", True):
            return False

        # Check if cloud is enabled
        if not self.config.cloud_enabled:
            return False

        try:
            with self.get_cloud() as cloud:
                if not cloud.is_enabled:
                    return False

                event_details = {"result": result}
                if details:
                    event_details.update(details)

                snapshot = build_event_snapshot(
                    self.config.config_dir,
                    self.registry.list(),
                    event_type=event_type,
                    instance_name=instance_name,
                    details=event_details,
                )
                cloud.push_snapshot(snapshot)
                logger.debug(f"Event sync completed: {event_type} ({result})")
                return True

        except Exception as e:
            if not silent:
                logger.warning(f"Event sync failed: {e}")
            return False
