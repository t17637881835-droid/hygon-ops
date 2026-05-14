"""Resource pool configuration loading and validation."""
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Set

import yaml


class ResourceConfigError(ValueError):
    pass


@dataclass(frozen=True)
class ResourcePool:
    pool_id: str
    name: str
    resource_type: str
    nodes: List[str]
    total_devices: int
    default_grant_hours: int
    max_grant_hours: int
    description: str = ""
    sshuser_path: str = "/public/bin/sshuser"
    min_free_devices_for_auto_suggest: int = 0
    enabled: bool = True
    prometheus_labels: Dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class ResourcePoolsConfig:
    pools: List[ResourcePool]
    allowed_nodes: Set[str]

    def enabled_pools(self) -> List[ResourcePool]:
        return [pool for pool in self.pools if pool.enabled]

    def get_pool(self, pool_id: str) -> ResourcePool:
        for pool in self.pools:
            if pool.pool_id == pool_id:
                return pool
        raise ResourceConfigError(f"unknown resource pool: {pool_id}")


def load_resource_pools(path: str) -> ResourcePoolsConfig:
    config_path = Path(path)
    if not config_path.exists():
        raise ResourceConfigError(f"resource pools config not found: {path}")

    data = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    raw_pools = data.get("resource_pools") or []
    if not isinstance(raw_pools, list) or not raw_pools:
        raise ResourceConfigError("resource_pools must be a non-empty list")

    pools = [_parse_pool(item) for item in raw_pools]
    _validate_pools(pools)
    return ResourcePoolsConfig(
        pools=pools,
        allowed_nodes={node for pool in pools for node in pool.nodes},
    )


def _parse_pool(item: Dict) -> ResourcePool:
    prometheus = item.get("prometheus") or {}
    labels = prometheus.get("labels") or {}
    return ResourcePool(
        pool_id=str(item.get("pool_id", "")).strip(),
        name=str(item.get("name", "")).strip(),
        description=str(item.get("description", "")).strip(),
        resource_type=str(item.get("resource_type", "")).strip(),
        nodes=[str(node).strip() for node in item.get("nodes", []) if str(node).strip()],
        total_devices=int(item.get("total_devices", 0)),
        default_grant_hours=int(item.get("default_grant_hours", 0)),
        max_grant_hours=int(item.get("max_grant_hours", 0)),
        sshuser_path=str(item.get("sshuser_path", "/public/bin/sshuser")).strip(),
        min_free_devices_for_auto_suggest=int(item.get("min_free_devices_for_auto_suggest", 0)),
        enabled=bool(item.get("enabled", True)),
        prometheus_labels={str(k): str(v) for k, v in labels.items()},
    )


def _validate_pools(pools: List[ResourcePool]) -> None:
    pool_ids: Set[str] = set()
    for pool in pools:
        if not pool.pool_id:
            raise ResourceConfigError("pool_id is required")
        if pool.pool_id in pool_ids:
            raise ResourceConfigError(f"duplicate pool_id: {pool.pool_id}")
        pool_ids.add(pool.pool_id)

        if not pool.name:
            raise ResourceConfigError(f"name is required for pool {pool.pool_id}")
        if not pool.resource_type:
            raise ResourceConfigError(f"resource_type is required for pool {pool.pool_id}")
        if not pool.nodes:
            raise ResourceConfigError(f"nodes must not be empty for pool {pool.pool_id}")
        if not pool.sshuser_path:
            raise ResourceConfigError(f"sshuser_path is required for pool {pool.pool_id}")
        if pool.total_devices <= 0:
            raise ResourceConfigError(f"total_devices must be positive for pool {pool.pool_id}")
        if pool.max_grant_hours <= 0:
            raise ResourceConfigError(f"max_grant_hours must be positive for pool {pool.pool_id}")
        if pool.default_grant_hours <= 0:
            raise ResourceConfigError(f"default_grant_hours must be positive for pool {pool.pool_id}")
        if pool.default_grant_hours > pool.max_grant_hours:
            raise ResourceConfigError(f"default_grant_hours exceeds max_grant_hours for pool {pool.pool_id}")
