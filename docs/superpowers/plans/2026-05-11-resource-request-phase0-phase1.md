# Resource Request Phase 0/1 Implementation Plan

> **Superseded:** This plan was written for the earlier LDAP group authorization assumption. The active authorization redesign is `docs/superpowers/plans/2026-05-11-resource-request-sshuser-redesign.md`, and the canonical design is `docs/superpowers/specs/2026-05-11-resource-request-design.md`. Any LDAP snippets below are historical and must not be used for new implementation.

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a stable Phase 0/1 resource request workflow for Feishu private chat: users submit structured resource requests, the system matches a configured resource pool, shows Prometheus-backed status, lets the owner approve/reject/defer, and generates LDAP authorization advice without mutating LDAP.

**Architecture:** Keep ordinary private-message auto-reply separate from resource requests. Add focused resource modules under `feishu_ops/`, store requests and grant plans in SQLite, load resource pools from a config file, and route `/apply` plus owner resource commands from the existing Feishu webhook. LDAP mutation remains disabled and out of scope for this plan.

**Tech Stack:** Python 3.10, FastAPI, SQLite, requests, PyYAML, APScheduler, existing Feishu sender/parser infrastructure, existing unittest/pytest test style.

---

## Scope

This plan implements:

- Phase 0 service hardening needed by the resource workflow.
- Phase 1 resource request MVP.
- Prometheus read-only integration with graceful fallback.
- LDAP authorization advice only.

This plan does not implement:

- Real LDAP add/remove operations.
- Automatic approval.
- Active SSH session termination.
- Feishu interactive cards.

## Current confirmed model

```text
request -> resource pool -> one LDAP group -> SSH access -> timed revocation
```

Users already have Linux/LDAP accounts. A resource pool owns exactly one LDAP group. In Phase 1 the bot only tells the owner which LDAP group should be used; it never modifies LDAP.

## Preflight commands

Run from:

```powershell
C:\Users\Admin\.claude\skills\haiguang-ops
```

- [ ] Check Python:

```powershell
python --version
```

Expected: Python 3.10+.

- [ ] Check tests:

```powershell
python -m pytest tests -q
```

Expected: existing tests pass or skip optional dependency tests as they do today.

- [ ] Check Git before using any commit step:

```powershell
git --version
```

Expected: a valid Git version. If it prints `git: 'D:\Git\cmd\git.exe' is not a git command`, repair the local Git/PATH installation before running the commit steps in this plan.

## File structure

### Create

- `config/resource_pools.example.yml`: example resource pool mapping for operators.
- `feishu_ops/resource_config.py`: resource pool dataclasses, YAML loading, validation, and LDAP group whitelist.
- `feishu_ops/resource_request_parser.py`: detect and parse `/apply` resource request messages.
- `feishu_ops/resource_request_store.py`: SQLite storage for resource requests, grant plans, and resource audit records.
- `feishu_ops/resource_priority.py`: deterministic priority scoring with human-readable reasons.
- `feishu_ops/resource_prometheus.py`: read-only Prometheus client and normalized pool status model.
- `feishu_ops/resource_approval.py`: parse owner resource commands and format owner/user messages.
- `tests/test_resource_config.py`: config loader tests.
- `tests/test_resource_request_parser.py`: `/apply` parser tests.
- `tests/test_resource_request_store.py`: SQLite store and state transition tests.
- `tests/test_resource_priority.py`: priority scoring tests.
- `tests/test_resource_prometheus.py`: Prometheus success/failure tests.
- `tests/test_resource_approval.py`: owner command parsing and message formatting tests.
- `docs/RESOURCE_REQUEST.md`: operator-facing resource request workflow document.

### Modify

- `docker/requirements.txt`: add `PyYAML`.
- `.env.example`: add resource request, Prometheus, and LDAP-disabled settings.
- `docker/docker-compose.yml`: pass resource request environment variables.
- `feishu_ops/config.py`: add `ResourceRequestConfig` to `Config`.
- `feishu_ops/config_check.py`: validate resource request config and resource pools.
- `feishu_ops/feishu_event_parser.py`: route owner resource commands before generic owner-cancel behavior.
- `feishu_ops/main.py`: route `/apply` and owner resource commands; enhance `/health`.
- `tests/test_config_check.py`: cover new config validation.
- `tests/test_p0_behaviors.py`: cover webhook routing integration for resource requests.

---

## Task 1: Add dependency and resource configuration model

**Files:**

- Modify: `docker/requirements.txt`
- Create: `config/resource_pools.example.yml`
- Create: `feishu_ops/resource_config.py`
- Test: `tests/test_resource_config.py`

- [ ] **Step 1: Add failing config loader tests**

Create `tests/test_resource_config.py`:

```python
import tempfile
import textwrap
import unittest
from pathlib import Path


class ResourceConfigTests(unittest.TestCase):
    def test_load_resource_pools_builds_whitelist(self):
        from resource_config import load_resource_pools

        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "resource_pools.yml"
            path.write_text(textwrap.dedent("""
                resource_pools:
                  - pool_id: k100_train
                    name: K100-训练池
                    description: 通用训练池
                    resource_type: K100
                    ldap_group: dcu_k100_train_users
                    nodes: [node01, node02]
                    total_devices: 16
                    default_grant_hours: 72
                    max_grant_hours: 168
                    min_free_devices_for_auto_suggest: 4
                    enabled: true
                    prometheus:
                      labels:
                        pool: k100_train
                        accelerator: k100
            """), encoding="utf-8")

            config = load_resource_pools(str(path))

        self.assertEqual(len(config.pools), 1)
        self.assertEqual(config.pools[0].pool_id, "k100_train")
        self.assertEqual(config.pools[0].ldap_group, "dcu_k100_train_users")
        self.assertIn("dcu_k100_train_users", config.allowed_ldap_groups)

    def test_duplicate_pool_id_is_rejected(self):
        from resource_config import ResourceConfigError, load_resource_pools

        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "resource_pools.yml"
            path.write_text(textwrap.dedent("""
                resource_pools:
                  - pool_id: k100_train
                    name: A
                    resource_type: K100
                    ldap_group: group_a
                    nodes: [node01]
                    total_devices: 8
                    default_grant_hours: 24
                    max_grant_hours: 72
                    enabled: true
                  - pool_id: k100_train
                    name: B
                    resource_type: K100
                    ldap_group: group_b
                    nodes: [node02]
                    total_devices: 8
                    default_grant_hours: 24
                    max_grant_hours: 72
                    enabled: true
            """), encoding="utf-8")

            with self.assertRaises(ResourceConfigError):
                load_resource_pools(str(path))

    def test_default_hours_cannot_exceed_max_hours(self):
        from resource_config import ResourceConfigError, load_resource_pools

        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "resource_pools.yml"
            path.write_text(textwrap.dedent("""
                resource_pools:
                  - pool_id: bad_pool
                    name: Bad
                    resource_type: K100
                    ldap_group: group_bad
                    nodes: [node01]
                    total_devices: 8
                    default_grant_hours: 100
                    max_grant_hours: 72
                    enabled: true
            """), encoding="utf-8")

            with self.assertRaises(ResourceConfigError):
                load_resource_pools(str(path))


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run config tests and verify failure**

Run:

```powershell
python -m pytest tests/test_resource_config.py -q
```

Expected: FAIL with `ModuleNotFoundError: No module named 'resource_config'`.

- [ ] **Step 3: Add PyYAML dependency**

Append to `docker/requirements.txt`:

```text
PyYAML>=6.0.1
```

- [ ] **Step 4: Create example resource pool config**

Create `config/resource_pools.example.yml`:

```yaml
resource_pools:
  - pool_id: k100_train
    name: K100-训练池
    description: 通用 K100 训练资源池
    resource_type: K100
    ldap_group: dcu_k100_train_users
    nodes:
      - node01
      - node02
      - node03
      - node04
    total_devices: 32
    default_grant_hours: 72
    max_grant_hours: 168
    min_free_devices_for_auto_suggest: 4
    enabled: true
    prometheus:
      labels:
        pool: k100_train
        accelerator: k100

  - pool_id: z100_infer
    name: Z100-推理池
    description: Z100 推理测试资源池
    resource_type: Z100
    ldap_group: dcu_z100_infer_users
    nodes:
      - node11
      - node12
    total_devices: 16
    default_grant_hours: 24
    max_grant_hours: 72
    min_free_devices_for_auto_suggest: 2
    enabled: true
    prometheus:
      labels:
        pool: z100_infer
        accelerator: z100
```

- [ ] **Step 5: Implement resource config loader**

Create `feishu_ops/resource_config.py`:

```python
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
    ldap_group: str
    nodes: List[str]
    total_devices: int
    default_grant_hours: int
    max_grant_hours: int
    description: str = ""
    min_free_devices_for_auto_suggest: int = 0
    enabled: bool = True
    prometheus_labels: Dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class ResourcePoolsConfig:
    pools: List[ResourcePool]
    allowed_ldap_groups: Set[str]

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
        allowed_ldap_groups={pool.ldap_group for pool in pools},
    )


def _parse_pool(item: Dict) -> ResourcePool:
    prometheus = item.get("prometheus") or {}
    labels = prometheus.get("labels") or {}
    return ResourcePool(
        pool_id=str(item.get("pool_id", "")).strip(),
        name=str(item.get("name", "")).strip(),
        description=str(item.get("description", "")).strip(),
        resource_type=str(item.get("resource_type", "")).strip(),
        ldap_group=str(item.get("ldap_group", "")).strip(),
        nodes=[str(node).strip() for node in item.get("nodes", []) if str(node).strip()],
        total_devices=int(item.get("total_devices", 0)),
        default_grant_hours=int(item.get("default_grant_hours", 0)),
        max_grant_hours=int(item.get("max_grant_hours", 0)),
        min_free_devices_for_auto_suggest=int(item.get("min_free_devices_for_auto_suggest", 0)),
        enabled=bool(item.get("enabled", True)),
        prometheus_labels={str(k): str(v) for k, v in labels.items()},
    )


def _validate_pools(pools: List[ResourcePool]) -> None:
    pool_ids: Set[str] = set()
    ldap_groups: Set[str] = set()
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
        if not pool.ldap_group:
            raise ResourceConfigError(f"ldap_group is required for pool {pool.pool_id}")
        if pool.ldap_group in ldap_groups:
            raise ResourceConfigError(f"duplicate ldap_group: {pool.ldap_group}")
        ldap_groups.add(pool.ldap_group)

        if not pool.nodes:
            raise ResourceConfigError(f"nodes must not be empty for pool {pool.pool_id}")
        if pool.total_devices <= 0:
            raise ResourceConfigError(f"total_devices must be positive for pool {pool.pool_id}")
        if pool.max_grant_hours <= 0:
            raise ResourceConfigError(f"max_grant_hours must be positive for pool {pool.pool_id}")
        if pool.default_grant_hours <= 0:
            raise ResourceConfigError(f"default_grant_hours must be positive for pool {pool.pool_id}")
        if pool.default_grant_hours > pool.max_grant_hours:
            raise ResourceConfigError(f"default_grant_hours exceeds max_grant_hours for pool {pool.pool_id}")
```

- [ ] **Step 6: Run config tests and verify pass**

Run:

```powershell
python -m pytest tests/test_resource_config.py -q
```

Expected: PASS.

- [ ] **Step 7: Commit**

```powershell
git add docker/requirements.txt config/resource_pools.example.yml feishu_ops/resource_config.py tests/test_resource_config.py
git commit -m "feat: add resource pool config loader"
```

---

## Task 2: Add resource request environment config and Phase 0 checks

**Files:**

- Modify: `feishu_ops/config.py`
- Modify: `.env.example`
- Modify: `docker/docker-compose.yml`
- Modify: `feishu_ops/config_check.py`
- Modify: `tests/test_config_check.py`

- [ ] **Step 1: Add failing config tests**

Append to `tests/test_config_check.py`:

```python
    def test_resource_pool_config_missing_is_reported_when_enabled(self):
        from config_check import validate_env

        result = validate_env({
            "FEISHU_APP_ID": "app",
            "FEISHU_APP_SECRET": "secret",
            "ANTHROPIC_API_KEY": "key",
            "RESOURCE_REQUEST_ENABLED": "true",
            "RESOURCE_POOLS_CONFIG_PATH": "C:/path/does/not/exist.yml",
        })

        self.assertGreater(len(result["errors"]), 0)
        self.assertTrue(any("RESOURCE_POOLS_CONFIG_PATH" in item for item in result["errors"]))

    def test_ldap_grant_enabled_requires_ldap_settings(self):
        from config_check import validate_env

        result = validate_env({
            "FEISHU_APP_ID": "app",
            "FEISHU_APP_SECRET": "secret",
            "ANTHROPIC_API_KEY": "key",
            "RESOURCE_REQUEST_ENABLED": "true",
            "LDAP_GRANT_ENABLED": "true",
        })

        self.assertTrue(any("LDAP_URL" in item for item in result["errors"]))
        self.assertTrue(any("LDAP_BIND_DN" in item for item in result["errors"]))
        self.assertTrue(any("LDAP_BIND_PASSWORD" in item for item in result["errors"]))
```

If `tests/test_config_check.py` uses pytest functions instead of a class, add equivalent top-level functions with `assert`.

- [ ] **Step 2: Run tests and verify failure**

Run:

```powershell
python -m pytest tests/test_config_check.py -q
```

Expected: FAIL because resource request environment validation does not exist.

- [ ] **Step 3: Add `ResourceRequestConfig`**

Modify `feishu_ops/config.py` by adding this dataclass below `SkillConfig`:

```python
@dataclass
class ResourceRequestConfig:
    enabled: bool = False
    pools_config_path: str = "./config/resource_pools.yml"
    prometheus_url: str = ""
    prometheus_timeout_seconds: int = 5
    ldap_grant_enabled: bool = False
    ldap_url: str = ""
    ldap_bind_dn: str = ""
    ldap_bind_password: str = ""
    ldap_base_dn: str = ""
    ldap_user_base_dn: str = ""
    ldap_group_base_dn: str = ""
    default_grant_hours: int = 24
    max_grant_hours: int = 168
    grant_confirm_required: bool = True
    expire_check_interval_minutes: int = 5
    expire_remind_hours: int = 2
```

Add a `resource_request: ResourceRequestConfig` field to `Config`.

Add this helper near the top of `config.py`:

```python
def _env_bool(name: str, default: str = "false") -> bool:
    return os.getenv(name, default).strip().lower() in {"1", "true", "yes", "on"}
```

Add this construction inside `Config.from_env()`:

```python
resource_request=ResourceRequestConfig(
    enabled=_env_bool("RESOURCE_REQUEST_ENABLED", "false"),
    pools_config_path=os.getenv("RESOURCE_POOLS_CONFIG_PATH", "./config/resource_pools.yml"),
    prometheus_url=os.getenv("PROMETHEUS_URL", ""),
    prometheus_timeout_seconds=int(os.getenv("PROMETHEUS_TIMEOUT_SECONDS", "5")),
    ldap_grant_enabled=_env_bool("LDAP_GRANT_ENABLED", "false"),
    ldap_url=os.getenv("LDAP_URL", ""),
    ldap_bind_dn=os.getenv("LDAP_BIND_DN", ""),
    ldap_bind_password=os.getenv("LDAP_BIND_PASSWORD", ""),
    ldap_base_dn=os.getenv("LDAP_BASE_DN", ""),
    ldap_user_base_dn=os.getenv("LDAP_USER_BASE_DN", ""),
    ldap_group_base_dn=os.getenv("LDAP_GROUP_BASE_DN", ""),
    default_grant_hours=int(os.getenv("RESOURCE_DEFAULT_GRANT_HOURS", "24")),
    max_grant_hours=int(os.getenv("RESOURCE_MAX_GRANT_HOURS", "168")),
    grant_confirm_required=_env_bool("RESOURCE_GRANT_CONFIRM_REQUIRED", "true"),
    expire_check_interval_minutes=int(os.getenv("RESOURCE_EXPIRE_CHECK_INTERVAL_MINUTES", "5")),
    expire_remind_hours=int(os.getenv("RESOURCE_EXPIRE_REMIND_HOURS", "2")),
)
```

- [ ] **Step 4: Update `.env.example`**

Append:

```env
# Resource request workflow
RESOURCE_REQUEST_ENABLED=false
RESOURCE_POOLS_CONFIG_PATH=./config/resource_pools.yml
PROMETHEUS_URL=http://prometheus:9090
PROMETHEUS_TIMEOUT_SECONDS=5

# LDAP grant is disabled in Phase 1. Enable only after Phase 2 validation.
LDAP_GRANT_ENABLED=false
LDAP_URL=ldap://ldap.example.com
LDAP_BIND_DN=cn=resource-bot,ou=service,dc=example,dc=com
LDAP_BIND_PASSWORD=
LDAP_BASE_DN=dc=example,dc=com
LDAP_USER_BASE_DN=ou=users,dc=example,dc=com
LDAP_GROUP_BASE_DN=ou=groups,dc=example,dc=com

RESOURCE_DEFAULT_GRANT_HOURS=24
RESOURCE_MAX_GRANT_HOURS=168
RESOURCE_GRANT_CONFIRM_REQUIRED=true
RESOURCE_EXPIRE_CHECK_INTERVAL_MINUTES=5
RESOURCE_EXPIRE_REMIND_HOURS=2
```

- [ ] **Step 5: Update Docker Compose environment**

Add these entries to `docker/docker-compose.yml` service environment:

```yaml
      - RESOURCE_REQUEST_ENABLED=${RESOURCE_REQUEST_ENABLED:-false}
      - RESOURCE_POOLS_CONFIG_PATH=${RESOURCE_POOLS_CONFIG_PATH:-./config/resource_pools.yml}
      - PROMETHEUS_URL=${PROMETHEUS_URL:-}
      - PROMETHEUS_TIMEOUT_SECONDS=${PROMETHEUS_TIMEOUT_SECONDS:-5}
      - LDAP_GRANT_ENABLED=${LDAP_GRANT_ENABLED:-false}
      - LDAP_URL=${LDAP_URL:-}
      - LDAP_BIND_DN=${LDAP_BIND_DN:-}
      - LDAP_BIND_PASSWORD=${LDAP_BIND_PASSWORD:-}
      - LDAP_BASE_DN=${LDAP_BASE_DN:-}
      - LDAP_USER_BASE_DN=${LDAP_USER_BASE_DN:-}
      - LDAP_GROUP_BASE_DN=${LDAP_GROUP_BASE_DN:-}
      - RESOURCE_DEFAULT_GRANT_HOURS=${RESOURCE_DEFAULT_GRANT_HOURS:-24}
      - RESOURCE_MAX_GRANT_HOURS=${RESOURCE_MAX_GRANT_HOURS:-168}
      - RESOURCE_GRANT_CONFIRM_REQUIRED=${RESOURCE_GRANT_CONFIRM_REQUIRED:-true}
      - RESOURCE_EXPIRE_CHECK_INTERVAL_MINUTES=${RESOURCE_EXPIRE_CHECK_INTERVAL_MINUTES:-5}
      - RESOURCE_EXPIRE_REMIND_HOURS=${RESOURCE_EXPIRE_REMIND_HOURS:-2}
```

- [ ] **Step 6: Enhance config check**

Modify `feishu_ops/config_check.py` so `validate_env(env=None)` uses the provided dict when passed and checks:

```python
resource_enabled = str(env.get("RESOURCE_REQUEST_ENABLED", "false")).lower() in {"1", "true", "yes", "on"}
if resource_enabled:
    pools_path = env.get("RESOURCE_POOLS_CONFIG_PATH", "./config/resource_pools.yml")
    if not pools_path:
        errors.append("RESOURCE_POOLS_CONFIG_PATH is required when RESOURCE_REQUEST_ENABLED=true")
    elif not Path(pools_path).exists():
        errors.append(f"RESOURCE_POOLS_CONFIG_PATH does not exist: {pools_path}")

ldap_enabled = str(env.get("LDAP_GRANT_ENABLED", "false")).lower() in {"1", "true", "yes", "on"}
if ldap_enabled:
    for name in ("LDAP_URL", "LDAP_BIND_DN", "LDAP_BIND_PASSWORD", "LDAP_BASE_DN", "LDAP_USER_BASE_DN", "LDAP_GROUP_BASE_DN"):
        if not env.get(name):
            errors.append(f"{name} is required when LDAP_GRANT_ENABLED=true")
```

Keep existing checks intact.

- [ ] **Step 7: Run config tests**

Run:

```powershell
python -m pytest tests/test_config_check.py -q
```

Expected: PASS.

- [ ] **Step 8: Commit**

```powershell
git add feishu_ops/config.py feishu_ops/config_check.py .env.example docker/docker-compose.yml tests/test_config_check.py
git commit -m "feat: add resource request configuration"
```

---

## Task 3: Implement `/apply` resource request parser

**Files:**

- Create: `feishu_ops/resource_request_parser.py`
- Test: `tests/test_resource_request_parser.py`

- [ ] **Step 1: Write parser tests**

Create `tests/test_resource_request_parser.py`:

```python
import unittest


class ResourceRequestParserTests(unittest.TestCase):
    def test_detects_apply_command(self):
        from resource_request_parser import is_resource_request

        self.assertTrue(is_resource_request("/apply\nLinux账号：zhangsan"))
        self.assertTrue(is_resource_request("申请资源 K100 4卡"))
        self.assertFalse(is_resource_request("我登录不上节点"))

    def test_parses_complete_apply_message(self):
        from resource_request_parser import parse_resource_request

        result = parse_resource_request("""
/apply
Linux账号：zhangsan
资源类型：K100
数量：4卡
使用时长：72小时
紧急程度：P1
项目：客户验收
用途：精度测试
截止时间：明天下午6点
是否接受排队：是
是否接受降配：否
""")

        self.assertTrue(result.valid)
        self.assertEqual(result.request.linux_username, "zhangsan")
        self.assertEqual(result.request.resource_type, "K100")
        self.assertEqual(result.request.resource_amount, 4)
        self.assertEqual(result.request.duration_hours, 72)
        self.assertEqual(result.request.urgency, "P1")
        self.assertEqual(result.request.project_name, "客户验收")
        self.assertEqual(result.request.reason, "精度测试")
        self.assertTrue(result.request.accept_queue)
        self.assertFalse(result.request.accept_downgrade)

    def test_reports_missing_fields(self):
        from resource_request_parser import parse_resource_request

        result = parse_resource_request("/apply\n资源类型：K100")

        self.assertFalse(result.valid)
        self.assertIn("linux_username", result.missing_fields)
        self.assertIn("resource_amount", result.missing_fields)
        self.assertIn("duration_hours", result.missing_fields)
        self.assertIn("urgency", result.missing_fields)
        self.assertIn("project_name_or_reason", result.missing_fields)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run parser tests and verify failure**

Run:

```powershell
python -m pytest tests/test_resource_request_parser.py -q
```

Expected: FAIL with missing module.

- [ ] **Step 3: Implement parser**

Create `feishu_ops/resource_request_parser.py`:

```python
"""Parse resource request messages from Feishu private chat."""
import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional


RESOURCE_INTENT_KEYWORDS = ("/apply", "申请资源", "我要申请资源", "我要资源", "需要资源")


@dataclass
class ParsedResourceRequest:
    linux_username: str = ""
    resource_type: str = ""
    resource_amount: int = 0
    duration_hours: int = 0
    urgency: str = ""
    project_name: str = ""
    reason: str = ""
    deadline: str = ""
    accept_queue: bool = False
    accept_downgrade: bool = False


@dataclass
class ResourceRequestParseResult:
    valid: bool
    request: ParsedResourceRequest = field(default_factory=ParsedResourceRequest)
    missing_fields: List[str] = field(default_factory=list)


def is_resource_request(content: str) -> bool:
    text = (content or "").strip()
    if not text:
        return False
    return any(keyword in text for keyword in RESOURCE_INTENT_KEYWORDS)


def parse_resource_request(content: str) -> ResourceRequestParseResult:
    fields = _parse_key_value_lines(content)
    request = ParsedResourceRequest(
        linux_username=_first(fields, "linux账号", "linux帐号", "账号", "用户"),
        resource_type=_first(fields, "资源类型", "类型", "卡型"),
        resource_amount=_parse_amount(_first(fields, "数量", "资源数量", "卡数")),
        duration_hours=_parse_duration_hours(_first(fields, "使用时长", "时长", "预计使用时长")),
        urgency=_normalize_urgency(_first(fields, "紧急程度", "优先级", "紧急度")),
        project_name=_first(fields, "项目", "项目名称"),
        reason=_first(fields, "用途", "原因", "任务说明"),
        deadline=_first(fields, "截止时间", "deadline"),
        accept_queue=_parse_bool(_first(fields, "是否接受排队", "接受排队")),
        accept_downgrade=_parse_bool(_first(fields, "是否接受降配", "接受降配")),
    )
    missing = _missing_fields(request)
    return ResourceRequestParseResult(valid=not missing, request=request, missing_fields=missing)


def _parse_key_value_lines(content: str) -> Dict[str, str]:
    result: Dict[str, str] = {}
    for raw_line in (content or "").splitlines():
        line = raw_line.strip()
        if not line or line == "/apply":
            continue
        if "：" in line:
            key, value = line.split("：", 1)
        elif ":" in line:
            key, value = line.split(":", 1)
        else:
            continue
        result[key.strip().lower()] = value.strip()
    return result


def _first(fields: Dict[str, str], *keys: str) -> str:
    for key in keys:
        value = fields.get(key.lower())
        if value:
            return value
    return ""


def _parse_amount(value: str) -> int:
    match = re.search(r"\d+", value or "")
    return int(match.group(0)) if match else 0


def _parse_duration_hours(value: str) -> int:
    match = re.search(r"\d+", value or "")
    if not match:
        return 0
    number = int(match.group(0))
    if "天" in value or "day" in value.lower():
        return number * 24
    return number


def _normalize_urgency(value: str) -> str:
    text = (value or "").strip().upper()
    return text if text in {"P0", "P1", "P2", "P3"} else ""


def _parse_bool(value: str) -> bool:
    return (value or "").strip().lower() in {"是", "yes", "true", "1", "接受", "可以"}


def _missing_fields(request: ParsedResourceRequest) -> List[str]:
    missing: List[str] = []
    if not request.linux_username:
        missing.append("linux_username")
    if not request.resource_type:
        missing.append("resource_type")
    if request.resource_amount <= 0:
        missing.append("resource_amount")
    if request.duration_hours <= 0:
        missing.append("duration_hours")
    if not request.urgency:
        missing.append("urgency")
    if not request.project_name and not request.reason:
        missing.append("project_name_or_reason")
    return missing
```

- [ ] **Step 4: Run parser tests**

Run:

```powershell
python -m pytest tests/test_resource_request_parser.py -q
```

Expected: PASS.

- [ ] **Step 5: Commit**

```powershell
git add feishu_ops/resource_request_parser.py tests/test_resource_request_parser.py
git commit -m "feat: parse resource request messages"
```

---

## Task 4: Implement SQLite resource request store

**Files:**

- Create: `feishu_ops/resource_request_store.py`
- Test: `tests/test_resource_request_store.py`

- [ ] **Step 1: Write store tests**

Create `tests/test_resource_request_store.py`:

```python
import tempfile
import unittest


class ResourceRequestStoreTests(unittest.TestCase):
    def test_create_request_assigns_request_code(self):
        from resource_request_store import ResourceRequestStore

        with tempfile.TemporaryDirectory() as tmpdir:
            store = ResourceRequestStore(f"{tmpdir}/resource.db")
            request = store.create_request(
                feishu_user_id="ou_user",
                linux_username="zhangsan",
                project_name="客户验收",
                resource_type="K100",
                resource_amount=4,
                duration_hours=72,
                urgency="P1",
                deadline="明天下午6点",
                reason="精度测试",
                accept_queue=True,
                accept_downgrade=False,
                matched_pool_id="k100_train",
                priority_score=115,
                priority_reasons=["P1: +70"],
            )

            loaded = store.get_request(request.request_code)

        self.assertEqual(request.request_code, "R1")
        self.assertEqual(loaded.linux_username, "zhangsan")
        self.assertEqual(loaded.status, "pending")

    def test_approve_request_changes_status(self):
        from resource_request_store import ResourceRequestStore

        with tempfile.TemporaryDirectory() as tmpdir:
            store = ResourceRequestStore(f"{tmpdir}/resource.db")
            request = store.create_request(
                feishu_user_id="ou_user",
                linux_username="zhangsan",
                project_name="客户验收",
                resource_type="K100",
                resource_amount=4,
                duration_hours=72,
                urgency="P1",
                deadline="",
                reason="精度测试",
                accept_queue=False,
                accept_downgrade=False,
                matched_pool_id="k100_train",
                priority_score=70,
                priority_reasons=["P1: +70"],
            )
            store.approve_request(request.request_code, approved_by="owner", duration_hours=48)
            loaded = store.get_request(request.request_code)

        self.assertEqual(loaded.status, "approved")
        self.assertEqual(loaded.duration_hours, 48)
        self.assertEqual(loaded.approved_by, "owner")

    def test_create_grant_plan_assigns_grant_code(self):
        from resource_request_store import ResourceRequestStore

        with tempfile.TemporaryDirectory() as tmpdir:
            store = ResourceRequestStore(f"{tmpdir}/resource.db")
            request = store.create_request(
                feishu_user_id="ou_user",
                linux_username="zhangsan",
                project_name="客户验收",
                resource_type="K100",
                resource_amount=4,
                duration_hours=72,
                urgency="P1",
                deadline="",
                reason="精度测试",
                accept_queue=False,
                accept_downgrade=False,
                matched_pool_id="k100_train",
                priority_score=70,
                priority_reasons=["P1: +70"],
            )
            grant = store.create_grant_plan(
                request_code=request.request_code,
                linux_username="zhangsan",
                pool_id="k100_train",
                ldap_group="dcu_k100_train_users",
                duration_hours=72,
                planned_by="owner",
            )
            loaded_request = store.get_request(request.request_code)

        self.assertEqual(grant.grant_code, "G1")
        self.assertEqual(grant.status, "planned")
        self.assertEqual(loaded_request.status, "planned")


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run store tests and verify failure**

Run:

```powershell
python -m pytest tests/test_resource_request_store.py -q
```

Expected: FAIL with missing module.

- [ ] **Step 3: Implement store dataclasses and schema**

Create `feishu_ops/resource_request_store.py` with:

```python
"""SQLite store for resource requests and grant plans."""
import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import List, Optional


@dataclass
class ResourceRequestRecord:
    request_code: str
    feishu_user_id: str
    linux_username: str
    project_name: str
    resource_type: str
    resource_amount: int
    duration_hours: int
    urgency: str
    deadline: str
    reason: str
    accept_queue: bool
    accept_downgrade: bool
    matched_pool_id: str
    priority_score: int
    priority_reasons: List[str]
    status: str
    approved_by: str = ""
    reject_reason: str = ""


@dataclass
class ResourceGrantRecord:
    grant_code: str
    request_code: str
    linux_username: str
    pool_id: str
    ldap_group: str
    valid_from: str
    valid_until: str
    status: str
    planned_by: str
    confirmed_by: str = ""
    last_error: str = ""


class ResourceRequestStore:
    def __init__(self, db_path: str):
        self.db_path = db_path
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _connect(self):
        return sqlite3.connect(self.db_path)

    def _init_db(self) -> None:
        conn = self._connect()
        try:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS resource_requests (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    request_code TEXT UNIQUE,
                    feishu_user_id TEXT NOT NULL,
                    linux_username TEXT NOT NULL,
                    project_name TEXT NOT NULL,
                    resource_type TEXT NOT NULL,
                    resource_amount INTEGER NOT NULL,
                    duration_hours INTEGER NOT NULL,
                    urgency TEXT NOT NULL,
                    deadline TEXT,
                    reason TEXT,
                    accept_queue INTEGER NOT NULL,
                    accept_downgrade INTEGER NOT NULL,
                    matched_pool_id TEXT NOT NULL,
                    priority_score INTEGER NOT NULL,
                    priority_reasons TEXT NOT NULL,
                    status TEXT NOT NULL,
                    approved_by TEXT,
                    approved_at TEXT,
                    reject_reason TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS resource_grants (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    grant_code TEXT UNIQUE,
                    request_code TEXT NOT NULL,
                    linux_username TEXT NOT NULL,
                    pool_id TEXT NOT NULL,
                    ldap_group TEXT NOT NULL,
                    valid_from TEXT NOT NULL,
                    valid_until TEXT NOT NULL,
                    status TEXT NOT NULL,
                    planned_by TEXT NOT NULL,
                    confirmed_by TEXT,
                    granted_at TEXT,
                    revoked_at TEXT,
                    last_error TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS resource_audit_logs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    event TEXT NOT NULL,
                    request_code TEXT,
                    grant_code TEXT,
                    actor_feishu_id TEXT,
                    linux_username TEXT,
                    pool_id TEXT,
                    ldap_group TEXT,
                    details TEXT,
                    created_at TEXT NOT NULL
                )
            """)
            conn.commit()
        finally:
            conn.close()
```

- [ ] **Step 4: Implement store operations**

Append these methods inside `ResourceRequestStore`:

```python
    def create_request(self, **kwargs) -> ResourceRequestRecord:
        now = _now()
        conn = self._connect()
        try:
            request_code = f"R{self._next_id(conn, 'resource_requests')}"
            conn.execute("""
                INSERT INTO resource_requests
                (request_code, feishu_user_id, linux_username, project_name, resource_type,
                 resource_amount, duration_hours, urgency, deadline, reason, accept_queue,
                 accept_downgrade, matched_pool_id, priority_score, priority_reasons, status,
                 created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                request_code,
                kwargs["feishu_user_id"], kwargs["linux_username"], kwargs["project_name"],
                kwargs["resource_type"], kwargs["resource_amount"], kwargs["duration_hours"],
                kwargs["urgency"], kwargs.get("deadline", ""), kwargs.get("reason", ""),
                1 if kwargs.get("accept_queue") else 0,
                1 if kwargs.get("accept_downgrade") else 0,
                kwargs["matched_pool_id"], kwargs["priority_score"],
                json.dumps(kwargs.get("priority_reasons", []), ensure_ascii=False),
                "pending", now, now,
            ))
            conn.commit()
        finally:
            conn.close()
        return self.get_request(request_code)

    def get_request(self, request_code: str) -> Optional[ResourceRequestRecord]:
        conn = self._connect()
        try:
            row = conn.execute("""
                SELECT request_code, feishu_user_id, linux_username, project_name, resource_type,
                       resource_amount, duration_hours, urgency, deadline, reason, accept_queue,
                       accept_downgrade, matched_pool_id, priority_score, priority_reasons, status,
                       COALESCE(approved_by, ''), COALESCE(reject_reason, '')
                FROM resource_requests WHERE request_code = ?
            """, (request_code,)).fetchone()
        finally:
            conn.close()
        return _request_from_row(row) if row else None

    def approve_request(self, request_code: str, approved_by: str, duration_hours: int) -> None:
        self._update_request_status(request_code, "approved", approved_by=approved_by, duration_hours=duration_hours)

    def reject_request(self, request_code: str, rejected_by: str, reason: str) -> None:
        now = _now()
        conn = self._connect()
        try:
            conn.execute("""
                UPDATE resource_requests
                SET status = 'rejected', approved_by = ?, reject_reason = ?, updated_at = ?
                WHERE request_code = ? AND status = 'pending'
            """, (rejected_by, reason, now, request_code))
            conn.commit()
        finally:
            conn.close()

    def create_grant_plan(self, request_code: str, linux_username: str, pool_id: str, ldap_group: str, duration_hours: int, planned_by: str) -> ResourceGrantRecord:
        now_dt = datetime.utcnow()
        valid_from = now_dt.isoformat()
        valid_until = (now_dt + timedelta(hours=duration_hours)).isoformat()
        now = now_dt.isoformat()
        conn = self._connect()
        try:
            grant_code = f"G{self._next_id(conn, 'resource_grants')}"
            conn.execute("""
                INSERT INTO resource_grants
                (grant_code, request_code, linux_username, pool_id, ldap_group, valid_from, valid_until,
                 status, planned_by, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, 'planned', ?, ?, ?)
            """, (grant_code, request_code, linux_username, pool_id, ldap_group, valid_from, valid_until, planned_by, now, now))
            conn.execute("""
                UPDATE resource_requests SET status = 'planned', updated_at = ? WHERE request_code = ?
            """, (now, request_code))
            conn.commit()
        finally:
            conn.close()
        return self.get_grant(grant_code)

    def get_grant(self, grant_code: str) -> Optional[ResourceGrantRecord]:
        conn = self._connect()
        try:
            row = conn.execute("""
                SELECT grant_code, request_code, linux_username, pool_id, ldap_group, valid_from,
                       valid_until, status, planned_by, COALESCE(confirmed_by, ''), COALESCE(last_error, '')
                FROM resource_grants WHERE grant_code = ?
            """, (grant_code,)).fetchone()
        finally:
            conn.close()
        return _grant_from_row(row) if row else None

    def list_pending_requests(self) -> List[ResourceRequestRecord]:
        conn = self._connect()
        try:
            rows = conn.execute("""
                SELECT request_code, feishu_user_id, linux_username, project_name, resource_type,
                       resource_amount, duration_hours, urgency, deadline, reason, accept_queue,
                       accept_downgrade, matched_pool_id, priority_score, priority_reasons, status,
                       COALESCE(approved_by, ''), COALESCE(reject_reason, '')
                FROM resource_requests WHERE status = 'pending' ORDER BY id
            """).fetchall()
        finally:
            conn.close()
        return [_request_from_row(row) for row in rows]

    def _update_request_status(self, request_code: str, status: str, approved_by: str = "", duration_hours: int = 0) -> None:
        now = _now()
        conn = self._connect()
        try:
            conn.execute("""
                UPDATE resource_requests
                SET status = ?, approved_by = ?, approved_at = ?, duration_hours = ?, updated_at = ?
                WHERE request_code = ? AND status = 'pending'
            """, (status, approved_by, now, duration_hours, now, request_code))
            conn.commit()
        finally:
            conn.close()

    def _next_id(self, conn, table_name: str) -> int:
        row = conn.execute(f"SELECT COUNT(*) FROM {table_name}").fetchone()
        return int(row[0]) + 1
```

Append helper functions at module level:

```python
def _now() -> str:
    return datetime.utcnow().isoformat()


def _request_from_row(row) -> ResourceRequestRecord:
    return ResourceRequestRecord(
        request_code=row[0], feishu_user_id=row[1], linux_username=row[2], project_name=row[3],
        resource_type=row[4], resource_amount=int(row[5]), duration_hours=int(row[6]), urgency=row[7],
        deadline=row[8] or "", reason=row[9] or "", accept_queue=bool(row[10]),
        accept_downgrade=bool(row[11]), matched_pool_id=row[12], priority_score=int(row[13]),
        priority_reasons=json.loads(row[14] or "[]"), status=row[15], approved_by=row[16] or "",
        reject_reason=row[17] or "",
    )


def _grant_from_row(row) -> ResourceGrantRecord:
    return ResourceGrantRecord(
        grant_code=row[0], request_code=row[1], linux_username=row[2], pool_id=row[3], ldap_group=row[4],
        valid_from=row[5], valid_until=row[6], status=row[7], planned_by=row[8], confirmed_by=row[9] or "",
        last_error=row[10] or "",
    )
```

- [ ] **Step 5: Run store tests**

Run:

```powershell
python -m pytest tests/test_resource_request_store.py -q
```

Expected: PASS.

- [ ] **Step 6: Commit**

```powershell
git add feishu_ops/resource_request_store.py tests/test_resource_request_store.py
git commit -m "feat: store resource requests in sqlite"
```

---

## Task 5: Implement priority scoring and resource pool matching

**Files:**

- Create: `feishu_ops/resource_priority.py`
- Create or extend: `feishu_ops/resource_pool.py`
- Test: `tests/test_resource_priority.py`

- [ ] **Step 1: Write scoring and matching tests**

Create `tests/test_resource_priority.py`:

```python
import unittest


class ResourcePriorityTests(unittest.TestCase):
    def test_scores_p1_customer_delivery(self):
        from resource_priority import score_resource_request

        score = score_resource_request(
            urgency="P1",
            deadline="明天下午6点",
            reason="客户验收前需要跑精度测试",
            pool_can_satisfy=True,
            pool_is_tight=False,
            accept_queue=True,
            accept_downgrade=False,
        )

        self.assertGreaterEqual(score.score, 110)
        self.assertIn("P1: +70", score.reasons)
        self.assertIn("客户交付/验收: +30", score.reasons)
        self.assertIn("资源池可满足: +10", score.reasons)

    def test_scores_pool_tight_penalty(self):
        from resource_priority import score_resource_request

        score = score_resource_request(
            urgency="P2",
            deadline="",
            reason="内部测试",
            pool_can_satisfy=False,
            pool_is_tight=True,
            accept_queue=False,
            accept_downgrade=False,
        )

        self.assertEqual(score.score, 30)
        self.assertIn("P2: +40", score.reasons)
        self.assertIn("内部测试: +10", score.reasons)
        self.assertIn("资源池紧张: -20", score.reasons)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run tests and verify failure**

Run:

```powershell
python -m pytest tests/test_resource_priority.py -q
```

Expected: FAIL with missing module.

- [ ] **Step 3: Implement scorer**

Create `feishu_ops/resource_priority.py`:

```python
"""Transparent resource request priority scoring."""
from dataclasses import dataclass
from typing import List


@dataclass(frozen=True)
class PriorityScore:
    score: int
    reasons: List[str]


def score_resource_request(
    urgency: str,
    deadline: str,
    reason: str,
    pool_can_satisfy: bool,
    pool_is_tight: bool,
    accept_queue: bool,
    accept_downgrade: bool,
) -> PriorityScore:
    total = 0
    reasons: List[str] = []

    urgency_points = {"P0": 100, "P1": 70, "P2": 40, "P3": 10}.get((urgency or "").upper(), 0)
    if urgency_points:
        total += urgency_points
        reasons.append(f"{urgency.upper()}: +{urgency_points}")

    if deadline:
        total += 40
        reasons.append("存在截止时间: +40")

    reason_text = reason or ""
    if any(keyword in reason_text for keyword in ("线上", "故障", "事故", "宕机")):
        total += 50
        reasons.append("生产故障/事故: +50")
    elif any(keyword in reason_text for keyword in ("客户", "交付", "验收")):
        total += 30
        reasons.append("客户交付/验收: +30")
    elif any(keyword in reason_text for keyword in ("测试", "验证")):
        total += 10
        reasons.append("内部测试: +10")

    if pool_can_satisfy:
        total += 10
        reasons.append("资源池可满足: +10")
    if pool_is_tight:
        total -= 20
        reasons.append("资源池紧张: -20")
    if accept_queue:
        total += 5
        reasons.append("接受排队: +5")
    if accept_downgrade:
        total += 5
        reasons.append("接受降配: +5")

    return PriorityScore(score=total, reasons=reasons)
```

- [ ] **Step 4: Implement resource pool matcher**

Create `feishu_ops/resource_pool.py`:

```python
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
```

- [ ] **Step 5: Run tests**

Run:

```powershell
python -m pytest tests/test_resource_priority.py tests/test_resource_config.py -q
```

Expected: PASS.

- [ ] **Step 6: Commit**

```powershell
git add feishu_ops/resource_priority.py feishu_ops/resource_pool.py tests/test_resource_priority.py
git commit -m "feat: score and match resource requests"
```

---

## Task 6: Implement read-only Prometheus resource status client

**Files:**

- Create: `feishu_ops/resource_prometheus.py`
- Test: `tests/test_resource_prometheus.py`

- [ ] **Step 1: Write Prometheus tests**

Create `tests/test_resource_prometheus.py`:

```python
import unittest
from unittest.mock import patch


class FakeResponse:
    def __init__(self, data, status_code=200):
        self._data = data
        self.status_code = status_code

    def json(self):
        return self._data


class ResourcePrometheusTests(unittest.TestCase):
    @patch("resource_prometheus.requests.get")
    def test_query_pool_status_success(self, mock_get):
        from resource_prometheus import PrometheusResourceClient

        mock_get.return_value = FakeResponse({"status": "success", "data": {"result": [{"value": [1, "6"]}]}})
        client = PrometheusResourceClient("http://prometheus:9090", timeout_seconds=1)

        status = client.get_pool_status(pool_id="k100_train", total_devices=32, labels={"pool": "k100_train"})

        self.assertEqual(status.pool_id, "k100_train")
        self.assertEqual(status.total_devices, 32)
        self.assertEqual(status.free_devices, 6)
        self.assertEqual(status.used_devices, 26)
        self.assertTrue(status.available)

    @patch("resource_prometheus.requests.get", side_effect=Exception("timeout"))
    def test_query_pool_status_failure_is_unavailable(self, mock_get):
        from resource_prometheus import PrometheusResourceClient

        client = PrometheusResourceClient("http://prometheus:9090", timeout_seconds=1)
        status = client.get_pool_status(pool_id="k100_train", total_devices=32, labels={"pool": "k100_train"})

        self.assertFalse(status.available)
        self.assertEqual(status.error, "timeout")


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run tests and verify failure**

Run:

```powershell
python -m pytest tests/test_resource_prometheus.py -q
```

Expected: FAIL with missing module.

- [ ] **Step 3: Implement Prometheus client**

Create `feishu_ops/resource_prometheus.py`:

```python
"""Read-only Prometheus client for resource pool status."""
from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict

import requests


@dataclass(frozen=True)
class ResourcePoolStatus:
    pool_id: str
    total_devices: int
    free_devices: int
    used_devices: int
    avg_utilization: float
    healthy_nodes: int
    unhealthy_nodes: int
    collected_at: str
    available: bool = True
    error: str = ""
    raw: Dict = field(default_factory=dict)


class PrometheusResourceClient:
    def __init__(self, prometheus_url: str, timeout_seconds: int = 5):
        self.prometheus_url = prometheus_url.rstrip("/")
        self.timeout_seconds = timeout_seconds

    def get_pool_status(self, pool_id: str, total_devices: int, labels: Dict[str, str]) -> ResourcePoolStatus:
        if not self.prometheus_url:
            return self._unavailable(pool_id, total_devices, "prometheus_url_not_configured")
        try:
            free_devices = int(float(self._query_value("pool_free_devices", labels)))
            used_devices = max(total_devices - free_devices, 0)
            return ResourcePoolStatus(
                pool_id=pool_id,
                total_devices=total_devices,
                free_devices=free_devices,
                used_devices=used_devices,
                avg_utilization=0.0,
                healthy_nodes=0,
                unhealthy_nodes=0,
                collected_at=datetime.utcnow().isoformat(),
                available=True,
                raw={"free_devices": free_devices},
            )
        except Exception as exc:
            return self._unavailable(pool_id, total_devices, str(exc))

    def _query_value(self, metric: str, labels: Dict[str, str]) -> float:
        query = self._build_query(metric, labels)
        response = requests.get(
            f"{self.prometheus_url}/api/v1/query",
            params={"query": query},
            timeout=self.timeout_seconds,
        )
        if response.status_code != 200:
            raise RuntimeError(f"prometheus_http_{response.status_code}")
        data = response.json()
        if data.get("status") != "success":
            raise RuntimeError("prometheus_query_failed")
        results = data.get("data", {}).get("result", [])
        if not results:
            return 0.0
        return float(results[0]["value"][1])

    def _build_query(self, metric: str, labels: Dict[str, str]) -> str:
        if not labels:
            return metric
        label_text = ",".join(f'{key}="{value}"' for key, value in sorted(labels.items()))
        return f"{metric}{{{label_text}}}"

    def _unavailable(self, pool_id: str, total_devices: int, error: str) -> ResourcePoolStatus:
        return ResourcePoolStatus(
            pool_id=pool_id,
            total_devices=total_devices,
            free_devices=0,
            used_devices=0,
            avg_utilization=0.0,
            healthy_nodes=0,
            unhealthy_nodes=0,
            collected_at=datetime.utcnow().isoformat(),
            available=False,
            error=error,
            raw={},
        )
```

- [ ] **Step 4: Run tests**

Run:

```powershell
python -m pytest tests/test_resource_prometheus.py -q
```

Expected: PASS.

- [ ] **Step 5: Commit**

```powershell
git add feishu_ops/resource_prometheus.py tests/test_resource_prometheus.py
git commit -m "feat: add prometheus resource status client"
```

---

## Task 7: Implement owner resource approval parser and message formatting

**Files:**

- Create: `feishu_ops/resource_approval.py`
- Test: `tests/test_resource_approval.py`

- [ ] **Step 1: Write approval tests**

Create `tests/test_resource_approval.py`:

```python
import unittest
from types import SimpleNamespace


class ResourceApprovalTests(unittest.TestCase):
    def test_parse_approve_command(self):
        from resource_approval import parse_owner_resource_command

        command = parse_owner_resource_command("/approve R12 72h")

        self.assertEqual(command.action, "approve")
        self.assertEqual(command.request_code, "R12")
        self.assertEqual(command.duration_hours, 72)

    def test_parse_reject_command(self):
        from resource_approval import parse_owner_resource_command

        command = parse_owner_resource_command("/reject R12 当前资源不足")

        self.assertEqual(command.action, "reject")
        self.assertEqual(command.request_code, "R12")
        self.assertEqual(command.reason, "当前资源不足")

    def test_formats_owner_notification(self):
        from resource_approval import format_owner_request_notification

        request = SimpleNamespace(
            request_code="R12",
            linux_username="zhangsan",
            project_name="客户验收",
            resource_amount=4,
            resource_type="K100",
            duration_hours=72,
            urgency="P1",
            priority_score=115,
            priority_reasons=["P1: +70", "客户交付/验收: +30"],
            matched_pool_id="k100_train",
        )
        pool = SimpleNamespace(name="K100-训练池", ldap_group="dcu_k100_train_users")
        status = SimpleNamespace(available=True, total_devices=32, free_devices=6, avg_utilization=0.62, unhealthy_nodes=1)

        text = format_owner_request_notification(request, pool, status)

        self.assertIn("资源申请 R12", text)
        self.assertIn("zhangsan", text)
        self.assertIn("dcu_k100_train_users", text)
        self.assertIn("/approve R12 72h", text)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run tests and verify failure**

Run:

```powershell
python -m pytest tests/test_resource_approval.py -q
```

Expected: FAIL with missing module.

- [ ] **Step 3: Implement approval parser and formatters**

Create `feishu_ops/resource_approval.py`:

```python
"""Owner resource approval command parsing and message formatting."""
import re
from dataclasses import dataclass


APPROVE_RE = re.compile(r"^/approve\s+(R\d+)\s+(\d+)h\s*$", re.IGNORECASE)
REJECT_RE = re.compile(r"^/reject\s+(R\d+)\s+(.+)$", re.IGNORECASE | re.DOTALL)
DEFER_RE = re.compile(r"^/defer\s+(R\d+)\s+(\d+)h\s*$", re.IGNORECASE)
DETAIL_RE = re.compile(r"^/detail\s+(R\d+)\s*$", re.IGNORECASE)
GRANT_RE = re.compile(r"^/grant\s+(R\d+)\s+confirm\s*$", re.IGNORECASE)
REVOKE_RE = re.compile(r"^/revoke\s+(R\d+)\s*$", re.IGNORECASE)
POOL_RE = re.compile(r"^/pool(?:\s+([a-zA-Z0-9_-]+))?\s*$", re.IGNORECASE)


@dataclass(frozen=True)
class OwnerResourceCommand:
    action: str
    request_code: str = ""
    duration_hours: int = 0
    reason: str = ""
    pool_id: str = ""


def is_owner_resource_command(content: str) -> bool:
    text = (content or "").strip()
    return text == "/queue" or any(regex.match(text) for regex in (APPROVE_RE, REJECT_RE, DEFER_RE, DETAIL_RE, GRANT_RE, REVOKE_RE, POOL_RE))


def parse_owner_resource_command(content: str) -> OwnerResourceCommand:
    text = (content or "").strip()
    if text == "/queue":
        return OwnerResourceCommand(action="queue")
    match = APPROVE_RE.match(text)
    if match:
        return OwnerResourceCommand(action="approve", request_code=match.group(1).upper(), duration_hours=int(match.group(2)))
    match = REJECT_RE.match(text)
    if match:
        return OwnerResourceCommand(action="reject", request_code=match.group(1).upper(), reason=match.group(2).strip())
    match = DEFER_RE.match(text)
    if match:
        return OwnerResourceCommand(action="defer", request_code=match.group(1).upper(), duration_hours=int(match.group(2)))
    match = DETAIL_RE.match(text)
    if match:
        return OwnerResourceCommand(action="detail", request_code=match.group(1).upper())
    match = GRANT_RE.match(text)
    if match:
        return OwnerResourceCommand(action="grant", request_code=match.group(1).upper())
    match = REVOKE_RE.match(text)
    if match:
        return OwnerResourceCommand(action="revoke", request_code=match.group(1).upper())
    match = POOL_RE.match(text)
    if match:
        return OwnerResourceCommand(action="pool", pool_id=match.group(1) or "")
    return OwnerResourceCommand(action="unknown")


def format_owner_request_notification(request, pool, status) -> str:
    reasons = "\n".join(f"- {item}" for item in request.priority_reasons)
    if getattr(status, "available", False):
        pool_status = (
            f"总卡数：{status.total_devices}\n"
            f"空闲卡：{status.free_devices}\n"
            f"平均利用率：{status.avg_utilization}\n"
            f"异常节点：{status.unhealthy_nodes}"
        )
    else:
        pool_status = f"资源池状态不可用：{getattr(status, 'error', 'unknown')}"
    return (
        f"📦 资源申请 {request.request_code}\n\n"
        f"申请人：{request.linux_username}\n"
        f"项目：{request.project_name}\n"
        f"申请资源：{request.resource_amount} × {request.resource_type}\n"
        f"使用时长：{request.duration_hours}h\n"
        f"紧急程度：{request.urgency}\n"
        f"优先级评分：{request.priority_score}\n\n"
        f"评分原因：\n{reasons}\n\n"
        f"推荐资源池：{pool.name}\n"
        f"LDAP组：{pool.ldap_group}\n\n"
        f"资源池状态：\n{pool_status}\n\n"
        f"操作：\n"
        f"/approve {request.request_code} {request.duration_hours}h\n"
        f"/reject {request.request_code} 原因\n"
        f"/defer {request.request_code} 4h\n"
        f"/detail {request.request_code}"
    )


def format_user_request_submitted(request_code: str) -> str:
    return f"✅ 资源申请已提交：{request_code}\n状态：待审批\n管理员会根据资源池状态和任务紧急程度处理。"


def format_missing_fields(missing_fields) -> str:
    field_names = {
        "linux_username": "Linux账号",
        "resource_type": "资源类型",
        "resource_amount": "资源数量",
        "duration_hours": "使用时长",
        "urgency": "紧急程度(P0/P1/P2/P3)",
        "project_name_or_reason": "项目或用途",
    }
    lines = [field_names.get(field, field) for field in missing_fields]
    return "请补充以下信息：\n" + "\n".join(f"{index + 1}. {name}" for index, name in enumerate(lines))
```

- [ ] **Step 4: Run approval tests**

Run:

```powershell
python -m pytest tests/test_resource_approval.py -q
```

Expected: PASS.

- [ ] **Step 5: Commit**

```powershell
git add feishu_ops/resource_approval.py tests/test_resource_approval.py
git commit -m "feat: parse resource approval commands"
```

---

## Task 8: Route resource commands through Feishu webhook

**Files:**

- Modify: `feishu_ops/feishu_event_parser.py`
- Modify: `feishu_ops/main.py`
- Test: `tests/test_p0_behaviors.py`

- [ ] **Step 1: Add parser regression tests**

Append to `tests/test_p0_behaviors.py`:

```python
    def test_owner_resource_command_is_not_treated_as_generic_cancel(self):
        from feishu_event_parser import parse_feishu_event

        payload = {
            "header": {"event_type": "im.message.receive_v1"},
            "event": {
                "sender": {"sender_id": {"open_id": "owner_1"}},
                "message": {
                    "message_id": "m1",
                    "chat_id": "owner_chat",
                    "message_type": "text",
                    "content": "{\"text\": \"/approve R12 72h\"}",
                },
            },
        }

        parsed = parse_feishu_event(payload, owner_user_ids={"owner_1"}, bot_user_ids=set())

        self.assertEqual(parsed.action, "resource_owner_command")
        self.assertEqual(parsed.content, "/approve R12 72h")
```

- [ ] **Step 2: Run targeted test and verify failure**

Run:

```powershell
python -m pytest tests/test_p0_behaviors.py::P0BehaviorTests::test_owner_resource_command_is_not_treated_as_generic_cancel -q
```

Expected: FAIL because parser currently treats unknown owner messages as `cancel`.

- [ ] **Step 3: Update Feishu event parser for owner resource commands**

Modify `feishu_ops/feishu_event_parser.py`:

Add near imports:

```python
RESOURCE_OWNER_PREFIXES = ("/queue", "/detail", "/approve", "/reject", "/defer", "/grant", "/revoke", "/pool")
```

Inside `if user_id in owner_user_ids:` before the generic owner cancel fallback, add:

```python
        if content.strip().startswith(RESOURCE_OWNER_PREFIXES):
            return ParsedFeishuEvent(
                action="resource_owner_command",
                message_id=message_id,
                user_id=user_id,
                chat_id=chat_id,
                content=content.strip(),
                event_type=event_type,
                reason="owner_resource_command",
            )
```

- [ ] **Step 4: Run targeted parser test**

Run:

```powershell
python -m pytest tests/test_p0_behaviors.py::P0BehaviorTests::test_owner_resource_command_is_not_treated_as_generic_cancel -q
```

Expected: PASS.

- [ ] **Step 5: Add webhook integration helper in `main.py`**

Modify `feishu_ops/main.py` imports:

```python
from resource_request_parser import is_resource_request, parse_resource_request
from resource_approval import (
    format_missing_fields,
    format_owner_request_notification,
    format_user_request_submitted,
    parse_owner_resource_command,
)
from resource_config import load_resource_pools
from resource_pool import match_resource_pool
from resource_priority import score_resource_request
from resource_prometheus import PrometheusResourceClient
from resource_request_store import ResourceRequestStore
```

Add guarded initialization after existing component initialization:

```python
resource_pools_config = None
resource_store = None
resource_prometheus_client = None
if config.resource_request.enabled:
    resource_pools_config = load_resource_pools(config.resource_request.pools_config_path)
    resource_store = ResourceRequestStore(config.skill.message_queue_db_path or "./data/message_queue.db")
    resource_prometheus_client = PrometheusResourceClient(
        config.resource_request.prometheus_url,
        timeout_seconds=config.resource_request.prometheus_timeout_seconds,
    )
```

- [ ] **Step 6: Add resource request handler functions in `main.py`**

Add below `health()` or above webhook helpers:

```python
def _resource_enabled() -> bool:
    return bool(config.resource_request.enabled and resource_pools_config and resource_store)


def _handle_user_resource_request(parsed):
    result = parse_resource_request(parsed.content)
    if not result.valid:
        feishu_sender.send_text(format_missing_fields(result.missing_fields), chat_id=parsed.chat_id)
        return {"status": "resource_request_incomplete", "missing_fields": result.missing_fields}

    pool = match_resource_pool(resource_pools_config, result.request.resource_type, result.request.resource_amount)
    if not pool:
        feishu_sender.send_text("未找到可匹配的资源池，请确认资源类型和数量。", chat_id=parsed.chat_id)
        return {"status": "resource_pool_not_found"}

    status = resource_prometheus_client.get_pool_status(pool.pool_id, pool.total_devices, pool.prometheus_labels)
    score = score_resource_request(
        urgency=result.request.urgency,
        deadline=result.request.deadline,
        reason=f"{result.request.project_name} {result.request.reason}",
        pool_can_satisfy=status.available and status.free_devices >= result.request.resource_amount,
        pool_is_tight=status.available and status.free_devices < pool.min_free_devices_for_auto_suggest,
        accept_queue=result.request.accept_queue,
        accept_downgrade=result.request.accept_downgrade,
    )
    record = resource_store.create_request(
        feishu_user_id=parsed.user_id,
        linux_username=result.request.linux_username,
        project_name=result.request.project_name or result.request.reason,
        resource_type=result.request.resource_type,
        resource_amount=result.request.resource_amount,
        duration_hours=result.request.duration_hours,
        urgency=result.request.urgency,
        deadline=result.request.deadline,
        reason=result.request.reason,
        accept_queue=result.request.accept_queue,
        accept_downgrade=result.request.accept_downgrade,
        matched_pool_id=pool.pool_id,
        priority_score=score.score,
        priority_reasons=score.reasons,
    )
    feishu_sender.send_text(format_user_request_submitted(record.request_code), chat_id=parsed.chat_id)
    owner_text = format_owner_request_notification(record, pool, status)
    for owner_id in _csv_to_set(config.feishu.owner_user_ids):
        feishu_sender.send_text(owner_text, chat_id=owner_id, receive_id_type="open_id")
    audit_logger.record(event="resource_request_created", request_code=record.request_code, user_id=parsed.user_id, pool_id=pool.pool_id)
    return {"status": "resource_request_created", "request_code": record.request_code}
```

- [ ] **Step 7: Route resource request before ordinary queue in webhook**

In `webhook()`, after `ignore` handling and before `message_queue.add(...)`, add:

```python
    if _resource_enabled() and is_resource_request(parsed.content):
        return _handle_user_resource_request(parsed)
```

- [ ] **Step 8: Add minimal owner command handler for Phase 1**

Add function in `main.py`:

```python
def _handle_owner_resource_command(parsed):
    command = parse_owner_resource_command(parsed.content)
    if command.action == "queue":
        pending = resource_store.list_pending_requests()
        if not pending:
            owner_notifier.confirm(parsed.user_id, "当前没有待审批资源申请。")
            return {"status": "resource_queue_empty"}
        text = "待审批资源申请：\n" + "\n".join(
            f"{item.request_code} {item.linux_username} {item.resource_amount}×{item.resource_type} {item.urgency}"
            for item in pending
        )
        owner_notifier.confirm(parsed.user_id, text)
        return {"status": "resource_queue"}

    if command.action == "approve":
        request = resource_store.get_request(command.request_code)
        if not request:
            owner_notifier.confirm(parsed.user_id, f"未找到资源申请 {command.request_code}")
            return {"status": "resource_request_not_found"}
        pool = resource_pools_config.get_pool(request.matched_pool_id)
        resource_store.approve_request(command.request_code, parsed.user_id, command.duration_hours)
        grant = resource_store.create_grant_plan(
            request_code=command.request_code,
            linux_username=request.linux_username,
            pool_id=pool.pool_id,
            ldap_group=pool.ldap_group,
            duration_hours=command.duration_hours,
            planned_by=parsed.user_id,
        )
        owner_notifier.confirm(
            parsed.user_id,
            f"✅ 已批准 {command.request_code}\n授权建议：将 {request.linux_username} 加入 LDAP 组 {pool.ldap_group}\n当前阶段不会自动修改 LDAP。\n授权计划：{grant.grant_code}",
        )
        audit_logger.record(event="resource_request_approved", request_code=command.request_code, owner_id=parsed.user_id, ldap_group=pool.ldap_group)
        return {"status": "resource_request_approved", "request_code": command.request_code}

    if command.action == "reject":
        resource_store.reject_request(command.request_code, parsed.user_id, command.reason)
        owner_notifier.confirm(parsed.user_id, f"✅ 已拒绝 {command.request_code}：{command.reason}")
        audit_logger.record(event="resource_request_rejected", request_code=command.request_code, owner_id=parsed.user_id, reason=command.reason)
        return {"status": "resource_request_rejected", "request_code": command.request_code}

    owner_notifier.confirm(parsed.user_id, f"资源命令暂不支持或格式错误：{parsed.content}")
    return {"status": "resource_command_unsupported"}
```

In `webhook()`, after challenge and before generic owner cancel handling, add:

```python
    if parsed.action == "resource_owner_command":
        if not _resource_enabled():
            owner_notifier.confirm(parsed.user_id, "资源申请功能未启用。")
            return {"status": "resource_request_disabled"}
        return _handle_owner_resource_command(parsed)
```

- [ ] **Step 9: Run P0 tests**

Run:

```powershell
python -m pytest tests/test_p0_behaviors.py -q
```

Expected: PASS.

- [ ] **Step 10: Commit**

```powershell
git add feishu_ops/feishu_event_parser.py feishu_ops/main.py tests/test_p0_behaviors.py
git commit -m "feat: route resource requests through webhook"
```

---

## Task 9: Enhance `/health` and docs for Phase 0 delivery

**Files:**

- Modify: `feishu_ops/main.py`
- Create: `docs/RESOURCE_REQUEST.md`
- Test: existing tests plus manual health check

- [ ] **Step 1: Enhance `/health` response**

Modify `health()` in `feishu_ops/main.py` to include resource workflow status:

```python
@app.get("/health")
async def health():
    """健康检查（完善版）"""
    return {
        "status": "healthy",
        "queue_size": message_queue.size(),
        "anthropic_configured": bool(config.anthropic.api_key),
        "feishu_bot_api_configured": bool(config.feishu.app_id and config.feishu.app_secret),
        "owner_user_ids_count": len(_csv_to_set(config.feishu.owner_user_ids)),
        "resource_request_enabled": bool(config.resource_request.enabled),
        "resource_pools_loaded": len(resource_pools_config.pools) if resource_pools_config else 0,
        "ldap_grant_enabled": bool(config.resource_request.ldap_grant_enabled),
        "prometheus_configured": bool(config.resource_request.prometheus_url),
    }
```

- [ ] **Step 2: Create resource request operations document**

Create `docs/RESOURCE_REQUEST.md`:

```markdown
# Resource Request Workflow

## Purpose

This workflow lets users request GPU/DCU resources through Feishu private chat. The system matches the request to a resource pool and notifies the owner for approval.

## Authorization model

```text
request -> resource pool -> target nodes -> node-local sshuser add/del -> timed revocation
```

Phase 1 does not execute node commands. It generates an `sshuser` authorization suggestion for the owner.

## User command

```text
/apply
Linux账号：zhangsan
资源类型：K100
数量：4卡
使用时长：72小时
紧急程度：P1
项目：客户验收
用途：精度测试
截止时间：明天下午6点
是否接受排队：是
是否接受降配：否
```

## Owner commands

```text
/queue
/detail R12
/approve R12 72h
/reject R12 原因
/defer R12 4h
/pool
/pool k100_train
```

## Phase 1 sshuser behavior

`SSHUSER_GRANT_ENABLED=false` and `SSHUSER_REMOTE_EXEC_ENABLED=false` are the required defaults. Approval creates an authorization plan and tells the owner which target nodes should receive `/public/bin/sshuser add <username>`. It does not execute `sshuser add` or `sshuser del`.

## Resource pool config

Copy `config/resource_pools.example.yml` to `config/resource_pools.yml` and edit pool IDs, target nodes, `sshuser_path`, and Prometheus labels for your environment.
```

- [ ] **Step 3: Run tests**

Run:

```powershell
python -m pytest tests -q
```

Expected: PASS or only known optional dependency skips.

- [ ] **Step 4: Commit**

```powershell
git add feishu_ops/main.py docs/RESOURCE_REQUEST.md
git commit -m "docs: document resource request workflow"
```

---

## Task 10: Final verification checklist

**Files:**

- No new source files unless a previous task exposed a concrete test failure.

- [ ] **Step 1: Run all unit tests**

```powershell
python -m pytest tests -q
```

Expected: PASS or only existing intentional skips for optional runtime dependencies.

- [ ] **Step 2: Run config check with resource request disabled**

```powershell
python feishu_ops\config_check.py
```

Expected: no resource-pool config error when `RESOURCE_REQUEST_ENABLED=false`.

- [ ] **Step 3: Run config check with resource request enabled and sample config copied**

```powershell
Copy-Item config\resource_pools.example.yml config\resource_pools.yml -Force
$env:RESOURCE_REQUEST_ENABLED="true"
$env:RESOURCE_POOLS_CONFIG_PATH="config/resource_pools.yml"
python feishu_ops\config_check.py
```

Expected: no resource pool path error.

- [ ] **Step 4: Verify LDAP remains disabled**

Check `.env.example` and deployment config contain:

```env
LDAP_GRANT_ENABLED=false
```

Expected: Phase 1 cannot mutate LDAP by default.

- [ ] **Step 5: Final commit**

```powershell
git status --short
git add .
git commit -m "feat: add resource request phase 1 workflow"
```

Expected: clean working tree after commit.

---

## Self-review

### Spec coverage

- Private-chat resource application: covered by Task 3 and Task 8.
- Resource pool mapping to one LDAP group: covered by Task 1 and Task 5.
- Prometheus read-only status: covered by Task 6.
- Owner approval/reject/defer command parsing: covered by Task 7 and Task 8. `/defer`, `/detail`, and `/pool` parsing is included; full handler behavior can be expanded after `/queue`, `/approve`, and `/reject` are stable.
- LDAP mutation disabled in Phase 1: covered by Task 2, Task 8, Task 9, and Task 10.
- Audit records: covered in Task 8 for create/approve/reject events.
- Health/config hardening: covered by Task 2 and Task 9.
- Docs: covered by Task 9.

### Placeholder scan

This plan contains no `TBD`, no unspecified code-generation steps, and no step that requires an engineer to invent a file path. Phase 2 LDAP mutation is explicitly out of scope for this plan.

### Type consistency

The plan consistently uses:

- `ResourcePool.pool_id`, `ResourcePool.ldap_group`, `ResourcePool.prometheus_labels`
- `ParsedResourceRequest.linux_username`, `resource_type`, `resource_amount`, `duration_hours`, `urgency`
- `ResourceRequestRecord.request_code`, `matched_pool_id`, `priority_reasons`
- `OwnerResourceCommand.action`, `request_code`, `duration_hours`, `reason`, `pool_id`

The webhook integration snippets refer only to objects defined in earlier tasks.
