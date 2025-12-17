"""Vast.ai REST API client for VastLab - Pure HTTP implementation"""

import time
from typing import Any, Dict, List, Optional, Tuple
import logging

from .vast_http import VastHttp, VastApiError

logger = logging.getLogger(__name__)

# Re-export for convenience
__all__ = ["VastAPI", "VastApiError"]


class VastAPI:
    """REST-only interface to Vast.ai API.

    All mutating operations raise exceptions on failure rather than returning bool.
    Use verified methods (destroy_instance_verified, etc.) for parity-safe operations.
    """

    DEFAULT_BASE_URL = "https://console.vast.ai/api/v0"

    def __init__(
        self,
        api_key: str,
        base_url: str = DEFAULT_BASE_URL,
        timeout_s: float = 30.0
    ):
        if not api_key:
            raise ValueError("Missing Vast API key")

        self.http = VastHttp(
            api_key=api_key,
            base_url=base_url,
            timeout_s=timeout_s
        )
        logger.debug(f"VastAPI initialized with base_url={base_url}")

    def close(self) -> None:
        """Close the HTTP client."""
        self.http.close()

    def __enter__(self) -> "VastAPI":
        return self

    def __exit__(self, *args) -> None:
        self.close()

    # =========================================================================
    # Instance Management
    # =========================================================================

    def show_instances(self) -> List[Dict[str, Any]]:
        """Get all instances.

        Returns:
            List of instance dictionaries
        """
        data = self.http.get("/instances/")

        # Normalize response shape
        if isinstance(data, dict) and "instances" in data:
            return data["instances"]
        if isinstance(data, list):
            return data
        return []

    def get_instance(self, instance_id: int) -> Optional[Dict[str, Any]]:
        """Get specific instance by ID.

        Args:
            instance_id: Vast instance ID

        Returns:
            Instance dict or None if not found
        """
        for inst in self.show_instances():
            if inst.get("id") == instance_id:
                return inst
        return None

    def start_instance(self, instance_id: int) -> None:
        """Start a stopped instance.

        Args:
            instance_id: Vast instance ID

        Raises:
            VastApiError: On API error
        """
        self.http.put(f"/instances/{instance_id}/", json={"state": "running"})
        logger.info(f"Start request sent for instance {instance_id}")

    def stop_instance(self, instance_id: int) -> None:
        """Stop a running instance.

        Args:
            instance_id: Vast instance ID

        Raises:
            VastApiError: On API error
        """
        self.http.put(f"/instances/{instance_id}/", json={"state": "stopped"})
        logger.info(f"Stop request sent for instance {instance_id}")

    def destroy_instance(self, instance_id: int) -> None:
        """Destroy an instance.

        Note: This sends the destroy request but does not verify completion.
        Use destroy_instance_verified() for parity-safe behavior.

        Args:
            instance_id: Vast instance ID

        Raises:
            VastApiError: On API error
        """
        self.http.delete(f"/instances/{instance_id}/")
        logger.info(f"Destroy request sent for instance {instance_id}")

    # =========================================================================
    # Polling / Verification Helpers
    # =========================================================================

    def wait_for_instance(
        self,
        instance_id: int,
        timeout: int = 300,
        poll_s: int = 5
    ) -> Dict[str, Any]:
        """Wait for instance to reach running state.

        Args:
            instance_id: Vast instance ID
            timeout: Maximum wait time in seconds
            poll_s: Polling interval in seconds

        Returns:
            Instance dict when running

        Raises:
            TimeoutError: If instance doesn't start within timeout
        """
        start = time.time()
        while time.time() - start < timeout:
            inst = self.get_instance(instance_id)
            if inst and inst.get("actual_status") == "running":
                logger.info(f"Instance {instance_id} is now running")
                return inst
            time.sleep(poll_s)

        raise TimeoutError(f"Instance {instance_id} did not reach running within {timeout}s")

    def wait_until_stopped(
        self,
        instance_id: int,
        timeout: int = 180,
        poll_s: int = 5
    ) -> None:
        """Wait for instance to reach stopped state.

        Args:
            instance_id: Vast instance ID
            timeout: Maximum wait time in seconds
            poll_s: Polling interval in seconds

        Raises:
            TimeoutError: If instance doesn't stop within timeout
        """
        start = time.time()
        while time.time() - start < timeout:
            inst = self.get_instance(instance_id)
            if inst and inst.get("actual_status") == "stopped":
                logger.info(f"Instance {instance_id} is now stopped")
                return
            time.sleep(poll_s)

        raise TimeoutError(f"Instance {instance_id} did not stop within {timeout}s")

    def wait_until_gone(
        self,
        instance_id: int,
        timeout: int = 180,
        poll_s: int = 5
    ) -> None:
        """Wait for instance to be completely removed.

        Args:
            instance_id: Vast instance ID
            timeout: Maximum wait time in seconds
            poll_s: Polling interval in seconds

        Raises:
            TimeoutError: If instance still exists after timeout
        """
        start = time.time()
        while time.time() - start < timeout:
            if self.get_instance(instance_id) is None:
                logger.info(f"Instance {instance_id} is gone")
                return
            time.sleep(poll_s)

        raise TimeoutError(f"Instance {instance_id} still present after {timeout}s")

    # =========================================================================
    # Verified Mutation Methods (Parity-Safe)
    # =========================================================================

    def destroy_instance_verified(
        self,
        instance_id: int,
        timeout: int = 180,
        poll_s: int = 5
    ) -> None:
        """Destroy instance and verify it's gone.

        This is the parity-safe version - only returns when the instance
        is confirmed removed from Vast.ai.

        Args:
            instance_id: Vast instance ID
            timeout: Maximum wait time in seconds
            poll_s: Polling interval in seconds

        Raises:
            VastApiError: On API error
            TimeoutError: If instance not removed within timeout
        """
        self.destroy_instance(instance_id)
        self.wait_until_gone(instance_id, timeout=timeout, poll_s=poll_s)

    def stop_instance_verified(
        self,
        instance_id: int,
        timeout: int = 180,
        poll_s: int = 5
    ) -> None:
        """Stop instance and verify it's stopped.

        Args:
            instance_id: Vast instance ID
            timeout: Maximum wait time in seconds
            poll_s: Polling interval in seconds

        Raises:
            VastApiError: On API error
            TimeoutError: If instance not stopped within timeout
        """
        self.stop_instance(instance_id)
        self.wait_until_stopped(instance_id, timeout=timeout, poll_s=poll_s)

    def start_instance_verified(
        self,
        instance_id: int,
        timeout: int = 300,
        poll_s: int = 5
    ) -> Dict[str, Any]:
        """Start instance and verify it's running.

        Args:
            instance_id: Vast instance ID
            timeout: Maximum wait time in seconds
            poll_s: Polling interval in seconds

        Returns:
            Instance dict when running

        Raises:
            VastApiError: On API error
            TimeoutError: If instance not running within timeout
        """
        self.start_instance(instance_id)
        return self.wait_for_instance(instance_id, timeout=timeout, poll_s=poll_s)

    # =========================================================================
    # SSH Key Management
    # =========================================================================

    def attach_ssh_key(self, instance_id: int, ssh_key: str) -> None:
        """Attach SSH public key to an instance.

        Args:
            instance_id: Vast instance ID
            ssh_key: SSH public key string (contents of .pub file)

        Raises:
            VastApiError: On API error
        """
        self.http.post(f"/instances/{instance_id}/ssh/", json={"ssh_key": ssh_key})
        logger.info(f"SSH key attached to instance {instance_id}")

    def get_ssh_info(self, instance_id: int) -> Tuple[str, int]:
        """Get SSH connection info for instance.

        Args:
            instance_id: Vast instance ID

        Returns:
            Tuple of (host, port)

        Raises:
            ValueError: If instance not found or no SSH info available
        """
        inst = self.get_instance(instance_id)
        if not inst:
            raise ValueError(f"Instance {instance_id} not found")

        # Try SSH proxy first
        if inst.get("ssh_host") and inst.get("ssh_port"):
            return inst["ssh_host"], int(inst["ssh_port"])

        # Try direct connection
        if inst.get("public_ipaddr") and inst.get("direct_port_start", -1) > 0:
            return inst["public_ipaddr"], int(inst["direct_port_start"])

        raise ValueError(f"No SSH connection info for instance {instance_id}")

    # =========================================================================
    # Search Offers
    # =========================================================================

    def search_offers(
        self,
        gpu_type: str,
        num_gpus: int,
        min_bandwidth: Optional[float] = None,
        max_price: Optional[float] = None,
        disk_gb: int = 40
    ) -> List[Dict[str, Any]]:
        """Search for GPU offers.

        Args:
            gpu_type: GPU type (e.g., "A100", "H100", "RTX4090")
            num_gpus: Number of GPUs required
            min_bandwidth: Minimum bandwidth in Mbps
            max_price: Maximum price per hour
            disk_gb: Minimum disk space in GB

        Returns:
            List of matching offers sorted by price
        """
        # Handle GPU variants
        gpu_variants = self._get_gpu_variants(gpu_type)

        all_offers = []
        seen_ids = set()

        for gpu_variant in gpu_variants:
            offers = self._search_offers_single(
                gpu_variant=gpu_variant,
                num_gpus=num_gpus,
                min_bandwidth=min_bandwidth,
                max_price=max_price,
                disk_gb=disk_gb
            )

            for offer in offers:
                offer_id = offer.get("id")
                if offer_id and offer_id not in seen_ids:
                    seen_ids.add(offer_id)
                    all_offers.append(offer)

        # Sort by price
        all_offers.sort(key=lambda x: x.get("dph_total", x.get("dph", 999)))
        return all_offers

    def _get_gpu_variants(self, gpu_type: str) -> List[str]:
        """Get all variant names for a GPU type."""
        gpu_upper = gpu_type.upper()

        # Map user input to actual Vast API gpu_name values
        # API uses names like "RTX 3090", "H100 SXM", "H200 NVL"
        variants_map = {
            "A100": ["A100", "A100 SXM", "A100 PCIE", "A100-SXM4-80GB", "A100-PCIE-40GB"],
            "H200": ["H200", "H200 SXM", "H200 NVL"],
            "H100": ["H100", "H100 SXM", "H100 PCIE", "H100 NVL"],
            "L40S": ["L40S"],
            "RTX5090": ["RTX 5090"],
            "RTX 5090": ["RTX 5090"],
            "RTX5080": ["RTX 5080"],
            "RTX 5080": ["RTX 5080"],
            "RTX5070TI": ["RTX 5070 Ti"],
            "RTX 5070 TI": ["RTX 5070 Ti"],
            "RTX5070": ["RTX 5070"],
            "RTX 5070": ["RTX 5070"],
            "RTX4090": ["RTX 4090"],
            "RTX 4090": ["RTX 4090"],
            "RTX4080S": ["RTX 4080S"],
            "RTX 4080S": ["RTX 4080S"],
            "RTX4080": ["RTX 4080"],
            "RTX 4080": ["RTX 4080"],
            "RTX4070TI": ["RTX 4070 Ti", "RTX 4070S Ti"],
            "RTX 4070 TI": ["RTX 4070 Ti", "RTX 4070S Ti"],
            "RTX4070": ["RTX 4070", "RTX 4070S"],
            "RTX 4070": ["RTX 4070", "RTX 4070S"],
            "RTX3090": ["RTX 3090"],
            "RTX 3090": ["RTX 3090"],
        }

        # Normalize input (remove spaces, uppercase)
        key = gpu_upper.replace(" ", "")
        return variants_map.get(key, variants_map.get(gpu_upper, [gpu_type]))

    def _search_offers_single(
        self,
        gpu_variant: str,
        num_gpus: int,
        min_bandwidth: Optional[float],
        max_price: Optional[float],
        disk_gb: int
    ) -> List[Dict[str, Any]]:
        """Search offers for a single GPU variant."""
        # Build search parameters for POST endpoint
        search_params = {
            "verified": {"eq": True},
            "rentable": {"eq": True},
            "gpu_name": {"eq": gpu_variant},
            "num_gpus": {"eq": num_gpus},
            "disk_space": {"gte": disk_gb}
        }

        if min_bandwidth:
            search_params["inet_down"] = {"gte": min_bandwidth}

        if max_price:
            search_params["dph_total"] = {"lte": max_price}

        try:
            data = self.http.post("/bundles/", json=search_params)

            # Normalize response
            if isinstance(data, dict) and "offers" in data:
                return data["offers"]
            if isinstance(data, list):
                return data
            return []

        except VastApiError as e:
            logger.warning(f"Search failed for {gpu_variant}: {e}")
            return []

    def search_cpu_offers(
        self,
        min_cpus: int = 4,
        min_ram_gb: int = 16,
        max_price: Optional[float] = None,
        disk_gb: int = 40
    ) -> List[Dict[str, Any]]:
        """Search for CPU-only offers (no GPU required).

        Args:
            min_cpus: Minimum number of CPU cores
            min_ram_gb: Minimum RAM in GB
            max_price: Maximum price per hour
            disk_gb: Minimum disk space in GB

        Returns:
            List of matching offers sorted by price
        """
        # Build search parameters for POST endpoint (same format as GPU search)
        # Note: cpu_ram is in MB in Vast API
        min_ram_mb = min_ram_gb * 1024
        search_params = {
            "verified": {"eq": True},
            "rentable": {"eq": True},
            "cpu_cores": {"gte": min_cpus},
            "cpu_ram": {"gte": min_ram_mb},
            "disk_space": {"gte": disk_gb},
        }

        if max_price:
            search_params["dph_total"] = {"lte": max_price}

        try:
            data = self.http.post("/bundles/", json=search_params)

            # Normalize and filter
            offers = []
            if isinstance(data, dict) and "offers" in data:
                offers = data["offers"]
            elif isinstance(data, list):
                offers = data

            # Additional filter for CPU requirements (API may return partial matches)
            filtered = []
            for offer in offers:
                cpu_cores = offer.get("cpu_cores", 0) or offer.get("cpu_cores_effective", 0)
                cpu_ram = offer.get("cpu_ram", 0)
                if cpu_cores >= min_cpus and cpu_ram >= min_ram_mb:
                    filtered.append(offer)

            # Sort by price
            filtered.sort(key=lambda x: x.get("dph_total", x.get("dph", 999)))
            return filtered

        except VastApiError as e:
            logger.error(f"CPU search failed: {e}")
            return []

    # =========================================================================
    # Create Instance
    # =========================================================================

    def create_instance(
        self,
        offer_id: int,
        disk_gb: int = 40,
        image: str = "pytorch/pytorch:2.2.2-cuda12.1-cudnn8-runtime",
        onstart_cmd: str = "",
        label: str = ""
    ) -> Dict[str, Any]:
        """Create a new instance.

        Args:
            offer_id: ID of the offer to accept
            disk_gb: Disk space in GB
            image: Docker image to use
            onstart_cmd: Command to run on startup
            label: Instance label

        Returns:
            API response with new_contract ID

        Raises:
            VastApiError: On API error
        """
        payload = {
            "client_id": "me",
            "image": image,
            "disk": disk_gb,
            "onstart": onstart_cmd,
            "runtype": "ssh",
            "label": label,
        }

        result = self.http.put(f"/asks/{offer_id}/", json=payload)
        logger.info(f"Instance created: {result}")
        return result
