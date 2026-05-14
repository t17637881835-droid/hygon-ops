"""Read-only Prometheus integration for resource pool status."""
from dataclasses import dataclass
from typing import Callable, Optional

import requests

from resource_config import ResourcePool


@dataclass(frozen=True)
class PoolStatus:
    pool_id: str
    state: str
    total_devices: int
    free_devices: Optional[int] = None
    error: str = ""


class PrometheusResourceClient:
    def __init__(self, base_url: str, http_get: Callable = None, timeout_seconds: int = 5):
        self.base_url = (base_url or "").rstrip("/")
        self.http_get = http_get or requests.get
        self.timeout_seconds = timeout_seconds

    def get_pool_status(self, pool: ResourcePool) -> PoolStatus:
        if not self.base_url:
            return PoolStatus(
                pool_id=pool.pool_id,
                state="unknown",
                total_devices=pool.total_devices,
                error="PROMETHEUS_URL is empty",
            )
        try:
            response = self.http_get(
                f"{self.base_url}/api/v1/query",
                params={"query": self._build_free_devices_query(pool)},
                timeout=self.timeout_seconds,
            )
            response.raise_for_status()
            free_devices = self._parse_vector_value(response.json())
            if free_devices is None:
                return PoolStatus(
                    pool_id=pool.pool_id,
                    state="unknown",
                    total_devices=pool.total_devices,
                    error="Prometheus returned no resource data",
                )
            return PoolStatus(
                pool_id=pool.pool_id,
                state="ok",
                total_devices=pool.total_devices,
                free_devices=free_devices,
            )
        except Exception as exc:
            return PoolStatus(
                pool_id=pool.pool_id,
                state="unknown",
                total_devices=pool.total_devices,
                error=str(exc),
            )

    def _build_free_devices_query(self, pool: ResourcePool) -> str:
        labels = dict(pool.prometheus_labels or {})
        labels.setdefault("pool", pool.pool_id)
        label_filter = ",".join(f'{key}="{value}"' for key, value in sorted(labels.items()))
        return f"resource_pool_free_devices{{{label_filter}}}"

    def _parse_vector_value(self, payload) -> Optional[int]:
        if not isinstance(payload, dict) or payload.get("status") != "success":
            return None
        result = payload.get("data", {}).get("result") or []
        if not result:
            return None
        value = result[0].get("value") or []
        if len(value) < 2:
            return None
        return int(float(value[1]))
