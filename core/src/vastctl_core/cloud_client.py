"""High-level client for VastLab Cloud API.

Provides business-level methods for:
- Token verification (whoami)
- Snapshot sync (telemetry)
- Profile management
"""

from __future__ import annotations
from dataclasses import dataclass
from typing import Any, Dict, List, Optional
from datetime import datetime, timezone
import logging

from .cloud_http import CloudHttp, CloudHttpConfig, CloudApiError
from .auth import AuthStore, load_token

logger = logging.getLogger(__name__)

# Re-export for convenience
__all__ = ["CloudClient", "CloudClientConfig", "CloudApiError"]


@dataclass
class CloudClientConfig:
    """Configuration for CloudClient."""
    base_url: str
    enabled: bool = True
    timeout_s: float = 20.0


class CloudClient:
    """High-level client for VastLab Cloud API.

    Provides methods for authentication, snapshots, and profiles.
    All methods that require authentication will raise CloudApiError(401)
    if not logged in.
    """

    def __init__(self, cfg: CloudClientConfig, auth_store: AuthStore):
        self.cfg = cfg
        self.auth_store = auth_store
        self._http = CloudHttp(
            CloudHttpConfig(base_url=cfg.base_url, timeout_s=cfg.timeout_s)
        )

    def close(self) -> None:
        """Close the HTTP client."""
        self._http.close()

    def __enter__(self) -> "CloudClient":
        return self

    def __exit__(self, *args) -> None:
        self.close()

    def _authed(self) -> CloudHttp:
        """Get an authenticated HTTP client.

        Returns:
            CloudHttp instance with auth token

        Raises:
            CloudApiError: If not logged in
        """
        token = load_token(self.auth_store)
        if not token:
            raise CloudApiError(401, "Not logged in. Run: vastctl login")
        return self._http.with_token(token)

    @property
    def is_enabled(self) -> bool:
        """Check if cloud features are enabled."""
        return self.cfg.enabled

    # ==========================================================================
    # Auth / Identity
    # ==========================================================================

    def verify_token(self) -> Dict[str, Any]:
        """Verify the current token and get user info.

        Returns:
            Dict with user/org/subscription info

        Raises:
            CloudApiError: If token is invalid or not logged in
        """
        return self._authed().post("/v1/auth/cli-tokens/verify")

    def whoami(self) -> Optional[Dict[str, Any]]:
        """Get current user info, or None if not logged in.

        Returns:
            Dict with user info, or None if not logged in or token invalid
        """
        try:
            return self.verify_token()
        except CloudApiError:
            return None

    # ==========================================================================
    # Snapshots (Telemetry)
    # ==========================================================================

    def push_snapshot(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """Push a snapshot to the cloud.

        Args:
            payload: Snapshot data (instances, installation_id, etc.)

        Returns:
            API response

        Raises:
            CloudApiError: On API error or if not logged in
        """
        if not self.cfg.enabled:
            logger.debug("Cloud sync disabled, skipping snapshot push")
            return {"skipped": True, "reason": "cloud.disabled"}

        # Ensure timestamp is set
        payload.setdefault("ts", datetime.now(timezone.utc).isoformat())

        return self._authed().post("/v1/cli/snapshots", json=payload)

    # ==========================================================================
    # Profiles
    # ==========================================================================

    def list_profiles(self) -> List[Dict[str, Any]]:
        """List available provisioning profiles.

        Returns:
            List of profile metadata dicts
        """
        data = self._authed().get("/v1/profiles")

        # Normalize response shape
        if isinstance(data, dict) and "profiles" in data:
            return data["profiles"]
        return data if isinstance(data, list) else []

    def get_profile(self, name: str) -> Dict[str, Any]:
        """Get a specific provisioning profile by name.

        Args:
            name: Profile name or slug

        Returns:
            Profile dict with pip/apt/torch/commands config
        """
        return self._authed().get(f"/v1/profiles/{name}")

    def publish_profile(self, profile: Dict[str, Any]) -> Dict[str, Any]:
        """Publish a new provisioning profile.

        Args:
            profile: Profile configuration dict

        Returns:
            Created profile metadata
        """
        return self._authed().post("/v1/profiles", json=profile)


