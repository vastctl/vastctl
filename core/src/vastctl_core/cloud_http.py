"""HTTP transport layer for VastLab Cloud API.

Separate from vast_http.py to keep Vast.ai API and VastLab Cloud concerns isolated.
"""

from __future__ import annotations
from dataclasses import dataclass
from typing import Any, Optional, Dict
import httpx
import logging

logger = logging.getLogger(__name__)


class CloudApiError(RuntimeError):
    """Exception raised when VastLab Cloud API returns an error."""

    def __init__(self, status_code: int, message: str, payload: Any = None):
        super().__init__(message)
        self.status_code = status_code
        self.payload = payload

    def __str__(self) -> str:
        return f"CloudApiError({self.status_code}): {super().__str__()}"


@dataclass
class CloudHttpConfig:
    """Configuration for CloudHttp client."""
    base_url: str
    timeout_s: float = 20.0
    retries: int = 2


class CloudHttp:
    """Low-level HTTP client for VastLab Cloud API with consistent error handling."""

    def __init__(
        self,
        cfg: CloudHttpConfig,
        token: Optional[str] = None
    ):
        self.cfg = cfg
        self.token = token

        headers = {
            "Accept": "application/json",
            "Content-Type": "application/json",
        }
        if token:
            headers["Authorization"] = f"Bearer {token}"

        transport = httpx.HTTPTransport(retries=cfg.retries)

        self.client = httpx.Client(
            base_url=cfg.base_url.rstrip("/"),
            timeout=httpx.Timeout(cfg.timeout_s),
            headers=headers,
            transport=transport,
        )

        logger.debug(f"CloudHttp initialized with base_url={cfg.base_url}")

    def close(self) -> None:
        """Close the HTTP client."""
        self.client.close()

    def __enter__(self) -> "CloudHttp":
        return self

    def __exit__(self, *args) -> None:
        self.close()

    def with_token(self, token: Optional[str]) -> "CloudHttp":
        """Create a new client bound to a token.

        Args:
            token: Bearer token for authentication

        Returns:
            New CloudHttp instance with token set
        """
        return CloudHttp(self.cfg, token=token)

    def request(
        self,
        method: str,
        path: str,
        *,
        params: Optional[Dict[str, Any]] = None,
        json: Optional[Dict[str, Any]] = None,
    ) -> Any:
        """Make an HTTP request to the VastLab Cloud API.

        Args:
            method: HTTP method (GET, POST, PUT, DELETE)
            path: API endpoint path (e.g., "/v1/profiles")
            params: Query parameters
            json: JSON body payload

        Returns:
            Parsed JSON response or None for empty responses

        Raises:
            CloudApiError: On HTTP 4xx/5xx responses or network errors
        """
        if not path.startswith("/"):
            path = f"/{path}"

        logger.debug(f"Cloud API Request: {method} {path}")

        try:
            response = self.client.request(
                method,
                path,
                params=params,
                json=json,
            )
        except httpx.RequestError as e:
            logger.error(f"Cloud API network error: {method} {path} - {e}")
            raise CloudApiError(0, f"Network error: {e}") from e

        if response.status_code >= 400:
            msg = self._extract_error(response)
            payload = self._safe_json(response)
            logger.error(f"Cloud API Error: {method} {path} -> {response.status_code}: {msg}")
            raise CloudApiError(response.status_code, msg, payload=payload)

        if not response.content:
            return None

        try:
            return response.json()
        except Exception:
            return response.text

    def _extract_error(self, response: httpx.Response) -> str:
        """Extract error message from response."""
        try:
            data = response.json()
            return (
                data.get("message") or
                data.get("error") or
                data.get("msg") or
                data.get("detail") or
                response.text
            )
        except Exception:
            return response.text or f"HTTP {response.status_code}"

    def _safe_json(self, response: httpx.Response) -> Optional[Dict[str, Any]]:
        """Safely parse JSON from response."""
        try:
            v = response.json()
            return v if isinstance(v, dict) else {"data": v}
        except Exception:
            return None

    # Convenience methods
    def get(self, path: str, **kwargs) -> Any:
        return self.request("GET", path, **kwargs)

    def post(self, path: str, **kwargs) -> Any:
        return self.request("POST", path, **kwargs)

    def put(self, path: str, **kwargs) -> Any:
        return self.request("PUT", path, **kwargs)

    def delete(self, path: str, **kwargs) -> Any:
        return self.request("DELETE", path, **kwargs)
