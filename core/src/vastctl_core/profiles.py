"""Provisioning profiles for VastLab.

Profiles are named overlays that can override parts of the base provisioning config.
They can be defined locally in config or pulled from the cloud and cached.

A profile can override:
- image (optional)
- provisioning.apt.packages
- provisioning.pip.packages
- provisioning.torch.mode
- provisioning.commands (extra bash commands)
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from .config import Config


def deep_merge(base: Dict[str, Any], override: Dict[str, Any]) -> Dict[str, Any]:
    """Deep merge two dictionaries.

    Creates a new dict with base values overridden by override values.
    For nested dicts, recursively merges instead of replacing.

    Args:
        base: Base dictionary
        override: Override values to merge in

    Returns:
        New merged dictionary (neither input is modified)
    """
    result = dict(base)
    for key, value in override.items():
        if isinstance(result.get(key), dict) and isinstance(value, dict):
            result[key] = deep_merge(result[key], value)
        else:
            result[key] = value
    return result


class ProfileStore:
    """Manages provisioning profiles from local config and cloud cache.

    Profiles are looked up in this order:
    1. Local profiles defined in config.yaml under 'profiles'
    2. Cloud-cached profiles from profiles_cache_path

    Example config.yaml:
        profiles:
          fast:
            description: "Jupyter only"
            provisioning:
              pip:
                packages: [jupyterlab, notebook]
              torch:
                mode: skip
    """

    def __init__(self, config: "Config"):
        self.config = config

    def _load_cloud_cache(self) -> Dict[str, Any]:
        """Load cloud profiles cache from disk.

        Returns:
            Dict with 'profiles' key containing cached profiles, or empty dict
        """
        cache_path = self.config.profiles_cache_path
        if not cache_path.exists():
            return {}
        try:
            return json.loads(cache_path.read_text()) or {}
        except Exception:
            return {}

    def save_cloud_cache(self, data: Dict[str, Any]) -> None:
        """Save cloud profiles cache to disk.

        Args:
            data: Dict with 'profiles' key to cache
        """
        cache_path = self.config.profiles_cache_path
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        cache_path.write_text(json.dumps(data, indent=2))

    def _get_local_profiles(self) -> Dict[str, Any]:
        """Get local profiles from config.

        Checks both `provisioning_profiles` (preferred) and legacy `profiles` namespace.
        Filters out non-dict values (like cache_path) from legacy namespace.

        Returns:
            Dict of profile name -> profile dict
        """
        # Preferred location: provisioning_profiles
        new_loc = self.config.get("provisioning_profiles", {}) or {}
        if new_loc:
            return {k: v for k, v in new_loc.items() if isinstance(v, dict)}

        # Legacy location: profiles (with filtering)
        legacy = self.config.get("profiles", {}) or {}
        return {k: v for k, v in legacy.items() if isinstance(v, dict)}

    def list_profiles(self) -> List[str]:
        """List all available profile names.

        Returns:
            Sorted list of profile names from both local and cloud cache
        """
        local_profiles = self._get_local_profiles()
        cloud = self._load_cloud_cache().get("profiles", {}) or {}

        return sorted(set(local_profiles.keys()) | set(cloud.keys()))

    def get_profile(self, name: str) -> Optional[Dict[str, Any]]:
        """Get a profile by name.

        Looks up in local config first, then cloud cache.

        Args:
            name: Profile name

        Returns:
            Profile dict or None if not found
        """
        # Check local profiles first
        local = self._get_local_profiles()
        local_profile = local.get(name)
        if local_profile:
            return local_profile

        # Check cloud cache
        cloud = self._load_cloud_cache().get("profiles", {}) or {}
        return cloud.get(name)

    def build_effective_provisioning(
        self, profile_name: Optional[str] = None
    ) -> Dict[str, Any]:
        """Build effective provisioning config with profile overrides.

        Starts with base provisioning from config, then deep-merges
        profile overrides if a profile is specified.

        Args:
            profile_name: Profile name to apply, or None for base config

        Returns:
            Merged provisioning dict

        Raises:
            KeyError: If profile_name is specified but not found
        """
        base = self.config.get("provisioning", {}) or {}

        if not profile_name:
            return dict(base)

        profile = self.get_profile(profile_name)
        if not profile:
            raise KeyError(f"Profile not found: {profile_name}")

        # Profiles can have pip/apt/torch at root level OR nested under 'provisioning'
        # Support both formats for flexibility
        overrides = profile.get("provisioning", {}) or {}

        # If no nested provisioning, use root-level keys (pip, apt, torch, etc.)
        if not overrides:
            overrides = {k: v for k, v in profile.items()
                        if k in ('pip', 'apt', 'torch', 'logging', 'mode', 'commands')}

        return deep_merge(base, overrides)

    def get_profile_image(self, profile_name: Optional[str] = None) -> Optional[str]:
        """Get the image override from a profile.

        Args:
            profile_name: Profile name to check

        Returns:
            Image string if profile specifies one, None otherwise
        """
        if not profile_name:
            return None

        profile = self.get_profile(profile_name)
        if not profile:
            return None

        return profile.get("image")

    def get_profile_description(self, profile_name: str) -> str:
        """Get the description of a profile.

        Args:
            profile_name: Profile name

        Returns:
            Description string or empty string if not found
        """
        profile = self.get_profile(profile_name)
        if not profile:
            return ""
        return profile.get("description", "")
