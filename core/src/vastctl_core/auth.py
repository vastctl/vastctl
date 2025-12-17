"""Authentication and token management for VastCtl Cloud.

Supports:
- Keyring storage (macOS Keychain, Windows Credential Manager, Linux Secret Service)
- Fallback to file-based storage when keyring unavailable
- Environment variable override for CI/automation
"""

from __future__ import annotations
from dataclasses import dataclass
from typing import Optional
from pathlib import Path
import os
import stat
import logging

logger = logging.getLogger(__name__)

# Try to import keyring; gracefully handle if not available
try:
    import keyring
    KEYRING_AVAILABLE = True
except ImportError:
    keyring = None
    KEYRING_AVAILABLE = False

SERVICE = "vastctl"
TOKEN_KEY = "cloud_access_token"
ENV_VAR = "VASTCTL_CLOUD_TOKEN"


@dataclass
class AuthStore:
    """Configuration for token storage."""
    token_file: Optional[Path] = None

    def __post_init__(self):
        if isinstance(self.token_file, str):
            self.token_file = Path(self.token_file)


def save_token(token: str, store: AuthStore) -> None:
    """Save authentication token.

    Args:
        token: The bearer token to save
        store: AuthStore configuration

    Raises:
        ValueError: If token is empty
        RuntimeError: If no storage mechanism available
    """
    token = token.strip()
    if not token:
        raise ValueError("Empty token")

    if KEYRING_AVAILABLE:
        try:
            keyring.set_password(SERVICE, TOKEN_KEY, token)
            logger.info("Token saved to system keyring")
            return
        except Exception as e:
            logger.warning(f"Keyring save failed, falling back to file: {e}")

    if not store.token_file:
        raise RuntimeError("No keyring available and no token_file configured")

    # Ensure parent directory exists
    store.token_file.parent.mkdir(parents=True, exist_ok=True)

    # Write token with restricted permissions
    store.token_file.write_text(token)

    # Set file permissions to user-only (chmod 600)
    try:
        os.chmod(store.token_file, stat.S_IRUSR | stat.S_IWUSR)
    except Exception:
        pass  # Windows may not support chmod

    logger.info(f"Token saved to {store.token_file}")


def load_token(store: AuthStore) -> Optional[str]:
    """Load authentication token.

    Checks in order:
    1. Environment variable VASTCTL_CLOUD_TOKEN
    2. System keyring
    3. Token file

    Args:
        store: AuthStore configuration

    Returns:
        Token string or None if not found
    """
    # 1. Environment variable override (useful for CI)
    env_token = os.getenv(ENV_VAR)
    if env_token:
        logger.debug("Using token from environment variable")
        return env_token.strip()

    # 2. System keyring
    if KEYRING_AVAILABLE:
        try:
            token = keyring.get_password(SERVICE, TOKEN_KEY)
            if token:
                logger.debug("Using token from system keyring")
                return token.strip()
        except Exception as e:
            logger.debug(f"Keyring read failed: {e}")

    # 3. Token file fallback
    if store.token_file and store.token_file.exists():
        try:
            token = store.token_file.read_text().strip()
            if token:
                logger.debug(f"Using token from {store.token_file}")
                return token
        except Exception as e:
            logger.warning(f"Failed to read token file: {e}")

    return None


def delete_token(store: AuthStore) -> None:
    """Delete stored authentication token.

    Removes from both keyring and file storage.

    Args:
        store: AuthStore configuration
    """
    # Remove from keyring
    if KEYRING_AVAILABLE:
        try:
            keyring.delete_password(SERVICE, TOKEN_KEY)
            logger.info("Token removed from system keyring")
        except Exception as e:
            logger.debug(f"Keyring delete (expected if empty): {e}")

    # Remove token file
    if store.token_file and store.token_file.exists():
        try:
            store.token_file.unlink()
            logger.info(f"Token file removed: {store.token_file}")
        except Exception as e:
            logger.warning(f"Failed to remove token file: {e}")


def is_logged_in(store: AuthStore) -> bool:
    """Check if a token is available.

    Args:
        store: AuthStore configuration

    Returns:
        True if a token is available
    """
    return load_token(store) is not None


def get_token_source(store: AuthStore) -> Optional[str]:
    """Get the source of the current token.

    Args:
        store: AuthStore configuration

    Returns:
        One of: "environment", "keyring", "file", or None if not found
    """
    if os.getenv(ENV_VAR):
        return "environment"

    if KEYRING_AVAILABLE:
        try:
            if keyring.get_password(SERVICE, TOKEN_KEY):
                return "keyring"
        except Exception:
            pass

    if store.token_file and store.token_file.exists():
        try:
            if store.token_file.read_text().strip():
                return "file"
        except Exception:
            pass

    return None
