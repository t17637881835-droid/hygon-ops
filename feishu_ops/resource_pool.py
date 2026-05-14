"""Resource pool matching logic."""
from typing import Optional

from resource_config import ResourcePool, ResourcePoolsConfig


def match_resource_pool(config: ResourcePoolsConfig, resource_type: str, resource_amount: int) -> Optional[ResourcePool]:
    requested_type = (resource_type or "").strip().lower()
    candidates = [
        pool for pool in config.enabled_pools()
        if pool.resource_type.lower() == requested_type and pool.total_devices >= resource_amount
    ]
    if not candidates:
        return None
    return sorted(candidates, key=lambda pool: (pool.total_devices, pool.pool_id))[0]
