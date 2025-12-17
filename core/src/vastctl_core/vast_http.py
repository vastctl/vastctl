"""HTTP transport layer for Vast.ai API"""

from __future__ import annotations
from typing import Any, Optional, Dict
import httpx
import logging
import time

logger = logging.getLogger(__name__)


class VastApiError(RuntimeError):
    """Exception raised when Vast.ai API returns an error."""

    def __init__(self, status_code: int, message: str, payload: Any = None):
        super().__init__(message)
        self.status_code = status_code
        self.payload = payload

    def __str__(self) -> str:
        return f"VastApiError({self.status_code}): {super().__str__()}"


class VastHttp:
    """Low-level HTTP client for Vast.ai API with consistent error handling."""

    DEFAULT_BASE_URL = "https://console.vast.ai/api/v0"

    def __init__(
        self,
        api_key: str,
        base_url: str = DEFAULT_BASE_URL,
        timeout_s: float = 30.0,
        retries: int = 3,
        rate_limit_s: float = 1.2,
    ):
        if not api_key:
            raise ValueError("API key is required")

        self.base_url = base_url.rstrip("/")
        self.retries = retries
        self.rate_limit_s = rate_limit_s
        self._last_request_time: float = 0.0

        # Configure transport with retries
        transport = httpx.HTTPTransport(retries=retries)

        self.client = httpx.Client(
            base_url=self.base_url,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
            timeout=httpx.Timeout(timeout_s),
            transport=transport,
        )

        logger.debug(f"VastHttp initialized with base_url={self.base_url}")

    def close(self) -> None:
        """Close the HTTP client."""
        self.client.close()

    def __enter__(self) -> "VastHttp":
        return self

    def __exit__(self, *args) -> None:
        self.close()

    def request(
        self,
        method: str,
        path: str,
        *,
        params: Optional[Dict[str, Any]] = None,
        json: Optional[Dict[str, Any]] = None,
    ) -> Any:
        """
        Make an HTTP request to the Vast.ai API.

        Args:
            method: HTTP method (GET, POST, PUT, DELETE)
            path: API endpoint path (e.g., "/instances/")
            params: Query parameters
            json: JSON body payload

        Returns:
            Parsed JSON response or None for empty responses

        Raises:
            VastApiError: On HTTP 4xx/5xx responses
            httpx.RequestError: On network/connection errors
        """
        # Ensure path starts with /
        if not path.startswith("/"):
            path = f"/{path}"

        # Rate limiting: wait if last request was too recent
        if self.rate_limit_s > 0:
            elapsed = time.time() - self._last_request_time
            if elapsed < self.rate_limit_s:
                sleep_time = self.rate_limit_s - elapsed
                logger.debug(f"Rate limiting: sleeping {sleep_time:.2f}s")
                time.sleep(sleep_time)

        logger.debug(f"API Request: {method} {path}")
        self._last_request_time = time.time()

        # Retry logic for 429 rate limit errors
        max_retries = 5
        for attempt in range(max_retries):
            try:
                response = self.client.request(
                    method,
                    path,
                    params=params,
                    json=json,
                )
            except httpx.RequestError as e:
                logger.error(f"Request failed: {method} {path} - {e}")
                raise

            # Handle 429 rate limit with exponential backoff
            if response.status_code == 429:
                if attempt < max_retries - 1:
                    wait_time = (2 ** attempt) + 1  # 2, 3, 5, 9 seconds
                    logger.warning(f"Rate limited (429), retrying in {wait_time}s... ({attempt + 1}/{max_retries})")
                    time.sleep(wait_time)
                    continue
                # Fall through to error handling on last attempt

            # Handle error responses
            if response.status_code >= 400:
                error_msg = self._extract_error_message(response)
                logger.error(f"API Error: {method} {path} -> {response.status_code}: {error_msg}")
                raise VastApiError(
                    status_code=response.status_code,
                    message=f"{method} {path} failed: {error_msg}",
                    payload=self._safe_json(response),
                )

            # Success - break out of retry loop
            break

        # Handle empty responses (204 No Content, etc.)
        if not response.content:
            return None

        # Parse JSON response
        try:
            return response.json()
        except Exception:
            # Return raw text if not JSON
            return response.text

    def _extract_error_message(self, response: httpx.Response) -> str:
        """Extract error message from response."""
        try:
            data = response.json()
            # Vast.ai uses various keys for error messages
            return (
                data.get("msg") or
                data.get("error") or
                data.get("message") or
                data.get("detail") or
                response.text
            )
        except Exception:
            return response.text or f"HTTP {response.status_code}"

    def _safe_json(self, response: httpx.Response) -> Optional[Dict[str, Any]]:
        """Safely parse JSON from response."""
        try:
            return response.json()
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
