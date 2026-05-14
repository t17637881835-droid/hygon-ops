# Resource Request Phase 2 SSHUser Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement confirmed, audited `sshuser add/del` execution through a jump host with per-node grant tracking, conservative revocation, retries, and owner notifications.

**Architecture:** Keep `/approve` as plan-only. Add a focused per-node persistence layer, a structured `SshuserExecutor` interface, a fake executor for tests, a jump-host executor behind feature flags, and a service that owns all grant/revoke state transitions. Integrate service calls into owner commands while preserving advice-only behavior when remote execution is disabled.

**Tech Stack:** Python 3, FastAPI, SQLite, unittest, subprocess-based SSH execution, existing `ResourceRequestStore`, `FeishuSender`, and `AuditLogger`.

---

## File Structure

- Modify: `feishu_ops/resource_request_store.py`
  - Add `ResourceGrantNodeRecord`.
  - Add `resource_grant_nodes` table.
  - Add Phase 2 columns on `resource_grants`.
  - Add per-node create/list/update/query methods.
  - Add compare-and-set grant status methods.

- Create: `feishu_ops/sshuser_executor.py`
  - Define `AccessCheckResult`, `NodeCommandResult`, and `SshuserExecutor` protocol/base class.
  - Provide `FakeSshuserExecutor` for unit and integration tests.

- Create: `feishu_ops/sshuser_safety.py`
  - Validate Linux usernames, node names, and `sshuser_path`.
  - Parse `AllowUsers` output.

- Create: `feishu_ops/sshuser_grant_service.py`
  - Implement grant confirmation, grant retry, revoke retry, manual mark-done, status aggregation, and message formatting.

- Create: `feishu_ops/grant_reaper.py`
  - Implement expiry reminder scan and due revoke scan.

- Create: `feishu_ops/jump_host_executor.py`
  - Implement structured SSH command execution for `check_access`, `grant_access`, and `revoke_access`.

- Modify: `feishu_ops/resource_approval.py`
  - Parse `/grant G1 confirm`, `/grant G1 retry`, `/revoke G1 retry`, and `/revoke G1 mark-done node01,node02`.

- Modify: `feishu_ops/config.py`
  - Add jump-host, target-user, command timeout, retry, known_hosts, and parallelism config fields.

- Modify: `feishu_ops/config_check.py`
  - Validate remote execution config when enabled.

- Modify: `feishu_ops/main.py`
  - Instantiate executor/service when resource workflow is enabled.
  - Route owner commands to Phase 2 service only when both `SSHUSER_GRANT_ENABLED=true` and `SSHUSER_REMOTE_EXEC_ENABLED=true`.
  - Keep advice-only output otherwise.
  - Expand `/health` resource fields.

- Modify: `.env.example`
  - Add Phase 2 variables with safe defaults.

- Modify: `docker/docker-compose.yml`
  - Add Phase 2 environment variable pass-throughs.

- Test: `tests/test_resource_request_store.py`
- Test: `tests/test_sshuser_safety.py`
- Test: `tests/test_sshuser_grant_service.py`
- Test: `tests/test_grant_reaper.py`
- Test: `tests/test_resource_approval.py`
- Test: `tests/test_config_check.py`
- Test: `tests/test_resource_health.py`
- Test: `tests/test_resource_webhook_routing.py`

---

### Task 1: Add per-node grant persistence

**Files:**
- Modify: `feishu_ops/resource_request_store.py`
- Test: `tests/test_resource_request_store.py`

- [ ] **Step 1: Write failing store tests for node rows created with grant plans**

Append this test method to `ResourceRequestStoreTests` in `tests/test_resource_request_store.py`:

```python
    def test_create_grant_plan_creates_per_node_rows(self):
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
                target_nodes=["node01", "node02"],
                sshuser_path="/public/bin/sshuser",
                duration_hours=72,
                planned_by="owner",
            )
            nodes = store.list_grant_nodes(grant.grant_code)

        self.assertEqual([node.node for node in nodes], ["node01", "node02"])
        self.assertEqual([node.grant_status for node in nodes], ["planned", "planned"])
        self.assertEqual([node.revoke_status for node in nodes], ["not_due", "not_due"])
        self.assertEqual(nodes[0].access_check_status, "unchecked")
        self.assertEqual(nodes[0].linux_username, "zhangsan")
```

- [ ] **Step 2: Run the failing store test**

Run:

```powershell
python -m unittest tests.test_resource_request_store.ResourceRequestStoreTests.test_create_grant_plan_creates_per_node_rows
```

Expected: FAIL with `AttributeError: 'ResourceRequestStore' object has no attribute 'list_grant_nodes'`.

- [ ] **Step 3: Implement `ResourceGrantNodeRecord` and `resource_grant_nodes` schema**

In `feishu_ops/resource_request_store.py`, update imports:

```python
from typing import List, Optional
```

Add this dataclass after `ResourceGrantRecord`:

```python
@dataclass
class ResourceGrantNodeRecord:
    grant_code: str
    request_code: str
    linux_username: str
    pool_id: str
    node: str
    sshuser_path: str
    access_existed_before: bool
    access_check_status: str
    access_check_error: str
    grant_status: str
    grant_attempts: int
    grant_last_error: str
    granted_at: str
    revoke_status: str
    revoke_attempts: int
    revoke_last_error: str
    revoked_at: str
```

In `_init_db`, after the `resource_grants` `CREATE TABLE`, add:

```python
            conn.execute("""
                CREATE TABLE IF NOT EXISTS resource_grant_nodes (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    grant_code TEXT NOT NULL,
                    request_code TEXT NOT NULL,
                    linux_username TEXT NOT NULL,
                    pool_id TEXT NOT NULL,
                    node TEXT NOT NULL,
                    sshuser_path TEXT NOT NULL,
                    access_existed_before INTEGER NOT NULL DEFAULT 0,
                    access_check_status TEXT NOT NULL DEFAULT 'unchecked',
                    access_check_error TEXT,
                    grant_status TEXT NOT NULL DEFAULT 'planned',
                    grant_attempts INTEGER NOT NULL DEFAULT 0,
                    grant_last_error TEXT,
                    granted_at TEXT,
                    revoke_status TEXT NOT NULL DEFAULT 'not_due',
                    revoke_attempts INTEGER NOT NULL DEFAULT 0,
                    revoke_last_error TEXT,
                    revoked_at TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    UNIQUE(grant_code, node)
                )
            """)
```

Also add Phase 2 columns after existing `_ensure_column` calls:

```python
            _ensure_column(conn, "resource_grants", "grant_started_at", "TEXT")
            _ensure_column(conn, "resource_grants", "grant_finished_at", "TEXT")
            _ensure_column(conn, "resource_grants", "revoke_started_at", "TEXT")
            _ensure_column(conn, "resource_grants", "revoke_finished_at", "TEXT")
            _ensure_column(conn, "resource_grants", "expire_reminded_at", "TEXT")
```

- [ ] **Step 4: Insert node rows inside `create_grant_plan`**

In `create_grant_plan`, after the `INSERT INTO resource_grants` call and before updating `resource_requests`, add:

```python
            for node in target_nodes:
                conn.execute("""
                    INSERT OR IGNORE INTO resource_grant_nodes
                    (grant_code, request_code, linux_username, pool_id, node, sshuser_path,
                     access_existed_before, access_check_status, grant_status, revoke_status,
                     created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, 0, 'unchecked', 'planned', 'not_due', ?, ?)
                """, (grant_code, request_code, linux_username, pool_id, node, sshuser_path, now, now))
```

- [ ] **Step 5: Add `list_grant_nodes` and row mapper**

Add this method to `ResourceRequestStore` before `_update_request_status`:

```python
    def list_grant_nodes(self, grant_code: str) -> List[ResourceGrantNodeRecord]:
        conn = self._connect()
        try:
            rows = conn.execute("""
                SELECT grant_code, request_code, linux_username, pool_id, node, sshuser_path,
                       access_existed_before, access_check_status, COALESCE(access_check_error, ''),
                       grant_status, grant_attempts, COALESCE(grant_last_error, ''), COALESCE(granted_at, ''),
                       revoke_status, revoke_attempts, COALESCE(revoke_last_error, ''), COALESCE(revoked_at, '')
                FROM resource_grant_nodes
                WHERE grant_code = ?
                ORDER BY id
            """, (grant_code,)).fetchall()
        finally:
            conn.close()
        return [_grant_node_from_row(row) for row in rows]
```

Add this mapper near `_grant_from_row`:

```python
def _grant_node_from_row(row) -> ResourceGrantNodeRecord:
    return ResourceGrantNodeRecord(
        grant_code=row[0],
        request_code=row[1],
        linux_username=row[2],
        pool_id=row[3],
        node=row[4],
        sshuser_path=row[5],
        access_existed_before=bool(row[6]),
        access_check_status=row[7],
        access_check_error=row[8] or "",
        grant_status=row[9],
        grant_attempts=int(row[10]),
        grant_last_error=row[11] or "",
        granted_at=row[12] or "",
        revoke_status=row[13],
        revoke_attempts=int(row[14]),
        revoke_last_error=row[15] or "",
        revoked_at=row[16] or "",
    )
```

- [ ] **Step 6: Run the store tests**

Run:

```powershell
python -m unittest tests.test_resource_request_store -v
```

Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add feishu_ops/resource_request_store.py tests/test_resource_request_store.py
git commit -m "feat: add per-node grant storage"
```

---

### Task 2: Add store state transition helpers

**Files:**
- Modify: `feishu_ops/resource_request_store.py`
- Test: `tests/test_resource_request_store.py`

- [ ] **Step 1: Write failing tests for node updates and status claiming**

Append these test methods to `ResourceRequestStoreTests`:

```python
    def test_update_grant_node_grant_result(self):
        from resource_request_store import ResourceRequestStore

        with tempfile.TemporaryDirectory() as tmpdir:
            store = ResourceRequestStore(f"{tmpdir}/resource.db")
            grant = self._create_grant(store)
            store.update_grant_node_grant_result(
                grant.grant_code,
                "node01",
                access_existed_before=False,
                access_check_status="absent",
                grant_status="succeeded",
                grant_last_error="",
            )
            nodes = store.list_grant_nodes(grant.grant_code)

        node01 = [node for node in nodes if node.node == "node01"][0]
        self.assertEqual(node01.grant_status, "succeeded")
        self.assertEqual(node01.access_check_status, "absent")
        self.assertEqual(node01.grant_attempts, 1)
        self.assertTrue(node01.granted_at)

    def test_claim_grant_for_update_changes_status_once(self):
        from resource_request_store import ResourceRequestStore

        with tempfile.TemporaryDirectory() as tmpdir:
            store = ResourceRequestStore(f"{tmpdir}/resource.db")
            grant = self._create_grant(store)
            first = store.claim_grant_status(grant.grant_code, ["planned"], "granting", actor="owner")
            second = store.claim_grant_status(grant.grant_code, ["planned"], "granting", actor="owner")
            loaded = store.get_grant(grant.grant_code)

        self.assertTrue(first)
        self.assertFalse(second)
        self.assertEqual(loaded.status, "granting")
        self.assertEqual(loaded.confirmed_by, "owner")

    def _create_grant(self, store):
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
        return store.create_grant_plan(
            request_code=request.request_code,
            linux_username="zhangsan",
            pool_id="k100_train",
            target_nodes=["node01", "node02"],
            sshuser_path="/public/bin/sshuser",
            duration_hours=72,
            planned_by="owner",
        )
```

If `_create_grant` duplicates setup from earlier tests, keep it anyway for now; later cleanup can DRY it safely.

- [ ] **Step 2: Run failing transition tests**

Run:

```powershell
python -m unittest tests.test_resource_request_store.ResourceRequestStoreTests.test_update_grant_node_grant_result tests.test_resource_request_store.ResourceRequestStoreTests.test_claim_grant_for_update_changes_status_once -v
```

Expected: FAIL with missing methods.

- [ ] **Step 3: Implement store transition methods**

Add these methods to `ResourceRequestStore` before `_update_request_status`:

```python
    def claim_grant_status(self, grant_code: str, from_statuses: List[str], to_status: str, actor: str = "") -> bool:
        now = _now()
        placeholders = ", ".join("?" for _ in from_statuses)
        params = [to_status]
        assignments = "status = ?, updated_at = ?"
        params.append(now)
        if to_status == "granting":
            assignments += ", confirmed_by = ?, grant_started_at = ?"
            params.extend([actor, now])
        elif to_status == "revoking":
            assignments += ", revoke_started_at = ?"
            params.append(now)
        params.append(grant_code)
        params.extend(from_statuses)
        conn = self._connect()
        try:
            cursor = conn.execute(f"""
                UPDATE resource_grants
                SET {assignments}
                WHERE grant_code = ? AND status IN ({placeholders})
            """, params)
            conn.commit()
            return cursor.rowcount == 1
        finally:
            conn.close()

    def update_grant_status(self, grant_code: str, status: str, last_error: str = "") -> None:
        now = _now()
        assignments = "status = ?, last_error = ?, updated_at = ?"
        params = [status, last_error, now]
        if status in {"granted", "partial_granted", "grant_failed"}:
            assignments += ", grant_finished_at = ?"
            params.append(now)
        if status in {"revoked", "partial_revoked", "revoke_failed"}:
            assignments += ", revoke_finished_at = ?"
            params.append(now)
        params.append(grant_code)
        conn = self._connect()
        try:
            conn.execute(f"UPDATE resource_grants SET {assignments} WHERE grant_code = ?", params)
            conn.commit()
        finally:
            conn.close()

    def update_grant_node_grant_result(self, grant_code: str, node: str, access_existed_before: bool, access_check_status: str, grant_status: str, grant_last_error: str = "") -> None:
        now = _now()
        granted_at = now if grant_status in {"succeeded", "skipped_preexisting", "covered_by_active_grant"} else ""
        conn = self._connect()
        try:
            conn.execute("""
                UPDATE resource_grant_nodes
                SET access_existed_before = ?, access_check_status = ?, grant_status = ?,
                    grant_attempts = grant_attempts + 1, grant_last_error = ?, granted_at = ?, updated_at = ?
                WHERE grant_code = ? AND node = ?
            """, (
                1 if access_existed_before else 0,
                access_check_status,
                grant_status,
                grant_last_error,
                granted_at,
                now,
                grant_code,
                node,
            ))
            conn.commit()
        finally:
            conn.close()

    def update_grant_node_revoke_result(self, grant_code: str, node: str, revoke_status: str, revoke_last_error: str = "") -> None:
        now = _now()
        revoked_at = now if revoke_status in {"succeeded", "skipped_preexisting", "skipped_active_grant", "skipped_not_granted", "succeeded_manual"} else ""
        conn = self._connect()
        try:
            conn.execute("""
                UPDATE resource_grant_nodes
                SET revoke_status = ?, revoke_attempts = revoke_attempts + 1,
                    revoke_last_error = ?, revoked_at = ?, updated_at = ?
                WHERE grant_code = ? AND node = ?
            """, (revoke_status, revoke_last_error, revoked_at, now, grant_code, node))
            conn.commit()
        finally:
            conn.close()
```

- [ ] **Step 4: Run store tests**

Run:

```powershell
python -m unittest tests.test_resource_request_store -v
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add feishu_ops/resource_request_store.py tests/test_resource_request_store.py
git commit -m "feat: add grant state transitions"
```

---

### Task 3: Add SSHUser safety helpers

**Files:**
- Create: `feishu_ops/sshuser_safety.py`
- Test: `tests/test_sshuser_safety.py`

- [ ] **Step 1: Write failing safety tests**

Create `tests/test_sshuser_safety.py`:

```python
import sys
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
FEISHU_OPS = PROJECT_ROOT / "feishu_ops"
if str(FEISHU_OPS) not in sys.path:
    sys.path.insert(0, str(FEISHU_OPS))


class SshuserSafetyTests(unittest.TestCase):
    def tearDown(self):
        sys.modules.pop("sshuser_safety", None)

    def test_validate_linux_username_accepts_safe_names(self):
        from sshuser_safety import validate_linux_username

        self.assertEqual(validate_linux_username("zhangsan"), "zhangsan")
        self.assertEqual(validate_linux_username("_svc-user1"), "_svc-user1")

    def test_validate_linux_username_rejects_injection(self):
        from sshuser_safety import validate_linux_username, SshuserSafetyError

        for value in ["ZhangSan", "zhang san", "zhang;id", "$(id)", "../root", "张三"]:
            with self.subTest(value=value):
                with self.assertRaises(SshuserSafetyError):
                    validate_linux_username(value)

    def test_validate_node_accepts_only_allowed_safe_node(self):
        from sshuser_safety import validate_node, SshuserSafetyError

        self.assertEqual(validate_node("node01", {"node01", "node02"}), "node01")
        with self.assertRaises(SshuserSafetyError):
            validate_node("node03", {"node01", "node02"})
        with self.assertRaises(SshuserSafetyError):
            validate_node("node01;id", {"node01;id"})

    def test_validate_sshuser_path_requires_configured_absolute_sshuser(self):
        from sshuser_safety import validate_sshuser_path, SshuserSafetyError

        self.assertEqual(validate_sshuser_path("/public/bin/sshuser", "/public/bin/sshuser"), "/public/bin/sshuser")
        with self.assertRaises(SshuserSafetyError):
            validate_sshuser_path("/tmp/sshuser", "/public/bin/sshuser")
        with self.assertRaises(SshuserSafetyError):
            validate_sshuser_path("/public/bin/sshuser;id", "/public/bin/sshuser")

    def test_parse_allow_users(self):
        from sshuser_safety import parse_allow_users, SshuserSafetyError

        self.assertTrue(parse_allow_users("AllowUsers root zhangsan lisi\n", "zhangsan"))
        self.assertFalse(parse_allow_users("AllowUsers root lisi\n", "zhangsan"))
        with self.assertRaises(SshuserSafetyError):
            parse_allow_users("AllowUsers root\nAllowUsers zhangsan\n", "zhangsan")


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run failing safety tests**

Run:

```powershell
python -m unittest tests.test_sshuser_safety -v
```

Expected: FAIL with `ModuleNotFoundError: No module named 'sshuser_safety'`.

- [ ] **Step 3: Implement safety helpers**

Create `feishu_ops/sshuser_safety.py`:

```python
"""Safety validation and parsing helpers for node-local sshuser execution."""
import re
from pathlib import PurePosixPath
from typing import Iterable, Set


class SshuserSafetyError(ValueError):
    pass


_USERNAME_RE = re.compile(r"^[a-z_][a-z0-9_-]{0,31}$")
_NODE_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9._-]{0,127}$")
_PATH_RE = re.compile(r"^/[A-Za-z0-9_./-]+$")


def validate_linux_username(value: str) -> str:
    username = (value or "").strip()
    if not _USERNAME_RE.fullmatch(username):
        raise SshuserSafetyError(f"invalid linux username: {value!r}")
    return username


def validate_node(value: str, allowed_nodes: Iterable[str]) -> str:
    node = (value or "").strip()
    allowed: Set[str] = set(allowed_nodes)
    if node not in allowed:
        raise SshuserSafetyError(f"node is not allowed: {node}")
    if not _NODE_RE.fullmatch(node):
        raise SshuserSafetyError(f"invalid node name: {node!r}")
    return node


def validate_sshuser_path(value: str, configured_path: str) -> str:
    path = (value or "").strip()
    if path != configured_path:
        raise SshuserSafetyError("sshuser path does not match configured command path")
    if not _PATH_RE.fullmatch(path):
        raise SshuserSafetyError(f"invalid sshuser path: {path!r}")
    if PurePosixPath(path).name != "sshuser":
        raise SshuserSafetyError("sshuser path basename must be sshuser")
    return path


def parse_allow_users(output: str, linux_username: str) -> bool:
    username = validate_linux_username(linux_username)
    lines = [line.strip() for line in (output or "").splitlines() if line.strip().startswith("AllowUsers")]
    if len(lines) != 1:
        raise SshuserSafetyError(f"expected exactly one AllowUsers line, got {len(lines)}")
    parts = lines[0].split()
    if len(parts) < 2 or parts[0] != "AllowUsers":
        raise SshuserSafetyError("invalid AllowUsers line")
    return username in set(parts[1:])
```

- [ ] **Step 4: Run safety tests**

Run:

```powershell
python -m unittest tests.test_sshuser_safety -v
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add feishu_ops/sshuser_safety.py tests/test_sshuser_safety.py
git commit -m "feat: add sshuser safety helpers"
```

---

### Task 4: Add executor interface and fake executor

**Files:**
- Create: `feishu_ops/sshuser_executor.py`
- Test: `tests/test_sshuser_grant_service.py`

- [ ] **Step 1: Write a minimal failing fake executor test**

Create `tests/test_sshuser_grant_service.py` with only this initial test:

```python
import sys
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
FEISHU_OPS = PROJECT_ROOT / "feishu_ops"
if str(FEISHU_OPS) not in sys.path:
    sys.path.insert(0, str(FEISHU_OPS))


class SshuserGrantServiceTests(unittest.TestCase):
    def tearDown(self):
        for name in ["sshuser_executor", "sshuser_grant_service", "sshuser_safety", "resource_request_store"]:
            sys.modules.pop(name, None)

    def test_fake_executor_returns_configured_access_result(self):
        from sshuser_executor import FakeSshuserExecutor

        executor = FakeSshuserExecutor(access_by_node={"node01": True, "node02": False})

        self.assertTrue(executor.check_access("node01", "zhangsan", "/public/bin/sshuser").present)
        self.assertFalse(executor.check_access("node02", "zhangsan", "/public/bin/sshuser").present)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run failing fake executor test**

Run:

```powershell
python -m unittest tests.test_sshuser_grant_service.SshuserGrantServiceTests.test_fake_executor_returns_configured_access_result -v
```

Expected: FAIL with `ModuleNotFoundError: No module named 'sshuser_executor'`.

- [ ] **Step 3: Implement executor types and fake executor**

Create `feishu_ops/sshuser_executor.py`:

```python
"""Structured executor interfaces for sshuser access checks and mutations."""
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Dict, Optional


@dataclass
class AccessCheckResult:
    node: str
    present: bool
    success: bool = True
    stdout: str = ""
    stderr: str = ""
    error_type: str = ""
    error_message: str = ""


@dataclass
class NodeCommandResult:
    node: str
    operation: str
    success: bool
    exit_code: int = 0
    stdout: str = ""
    stderr: str = ""
    started_at: str = ""
    finished_at: str = ""
    duration_ms: int = 0
    attempt: int = 1
    error_type: str = ""
    error_message: str = ""


class SshuserExecutor:
    def check_access(self, node: str, linux_username: str, sshuser_path: str) -> AccessCheckResult:
        raise NotImplementedError

    def grant_access(self, node: str, linux_username: str, sshuser_path: str) -> NodeCommandResult:
        raise NotImplementedError

    def revoke_access(self, node: str, linux_username: str, sshuser_path: str) -> NodeCommandResult:
        raise NotImplementedError


class FakeSshuserExecutor(SshuserExecutor):
    def __init__(self, access_by_node: Optional[Dict[str, bool]] = None, grant_success_by_node: Optional[Dict[str, bool]] = None, revoke_success_by_node: Optional[Dict[str, bool]] = None):
        self.access_by_node = access_by_node or {}
        self.grant_success_by_node = grant_success_by_node or {}
        self.revoke_success_by_node = revoke_success_by_node or {}
        self.calls = []

    def check_access(self, node: str, linux_username: str, sshuser_path: str) -> AccessCheckResult:
        self.calls.append(("check", node, linux_username, sshuser_path))
        if node not in self.access_by_node:
            return AccessCheckResult(node=node, present=False, success=True, stdout="AllowUsers root\n")
        present = self.access_by_node[node]
        stdout = f"AllowUsers root {linux_username}\n" if present else "AllowUsers root\n"
        return AccessCheckResult(node=node, present=present, success=True, stdout=stdout)

    def grant_access(self, node: str, linux_username: str, sshuser_path: str) -> NodeCommandResult:
        self.calls.append(("add", node, linux_username, sshuser_path))
        return self._command_result(node, "add", self.grant_success_by_node.get(node, True))

    def revoke_access(self, node: str, linux_username: str, sshuser_path: str) -> NodeCommandResult:
        self.calls.append(("del", node, linux_username, sshuser_path))
        return self._command_result(node, "del", self.revoke_success_by_node.get(node, True))

    def _command_result(self, node: str, operation: str, success: bool) -> NodeCommandResult:
        now = datetime.now(UTC).isoformat()
        return NodeCommandResult(
            node=node,
            operation=operation,
            success=success,
            exit_code=0 if success else 1,
            stdout="ok" if success else "",
            stderr="" if success else "failed",
            started_at=now,
            finished_at=now,
            error_type="" if success else "nonzero_exit",
            error_message="" if success else "fake command failed",
        )
```

- [ ] **Step 4: Run fake executor test**

Run:

```powershell
python -m unittest tests.test_sshuser_grant_service.SshuserGrantServiceTests.test_fake_executor_returns_configured_access_result -v
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add feishu_ops/sshuser_executor.py tests/test_sshuser_grant_service.py
git commit -m "feat: add sshuser executor interface"
```

---

### Task 5: Implement grant confirmation service

**Files:**
- Create: `feishu_ops/sshuser_grant_service.py`
- Modify: `tests/test_sshuser_grant_service.py`

- [ ] **Step 1: Add failing grant service tests**

Append these tests and helper to `SshuserGrantServiceTests` in `tests/test_sshuser_grant_service.py`:

```python
    def test_confirm_grant_adds_absent_user_and_skips_preexisting(self):
        from resource_request_store import ResourceRequestStore
        from sshuser_executor import FakeSshuserExecutor
        from sshuser_grant_service import SshuserGrantService
        import tempfile

        with tempfile.TemporaryDirectory() as tmpdir:
            store = ResourceRequestStore(f"{tmpdir}/resource.db")
            grant = self._create_grant(store)
            executor = FakeSshuserExecutor(access_by_node={"node01": False, "node02": True})
            service = SshuserGrantService(
                store=store,
                executor=executor,
                allowed_nodes={"node01", "node02"},
                configured_sshuser_path="/public/bin/sshuser",
            )

            result = service.confirm_grant(grant.grant_code, actor="ou_owner")
            nodes = store.list_grant_nodes(grant.grant_code)
            loaded = store.get_grant(grant.grant_code)

        by_node = {node.node: node for node in nodes}
        self.assertEqual(result.status, "granted")
        self.assertEqual(loaded.status, "granted")
        self.assertEqual(by_node["node01"].grant_status, "succeeded")
        self.assertEqual(by_node["node02"].grant_status, "skipped_preexisting")
        self.assertIn(("add", "node01", "zhangsan", "/public/bin/sshuser"), executor.calls)
        self.assertNotIn(("add", "node02", "zhangsan", "/public/bin/sshuser"), executor.calls)

    def test_confirm_grant_records_partial_failure(self):
        from resource_request_store import ResourceRequestStore
        from sshuser_executor import FakeSshuserExecutor
        from sshuser_grant_service import SshuserGrantService
        import tempfile

        with tempfile.TemporaryDirectory() as tmpdir:
            store = ResourceRequestStore(f"{tmpdir}/resource.db")
            grant = self._create_grant(store)
            executor = FakeSshuserExecutor(
                access_by_node={"node01": False, "node02": False},
                grant_success_by_node={"node01": True, "node02": False},
            )
            service = SshuserGrantService(store, executor, {"node01", "node02"}, "/public/bin/sshuser")

            result = service.confirm_grant(grant.grant_code, actor="ou_owner")
            nodes = store.list_grant_nodes(grant.grant_code)

        by_node = {node.node: node for node in nodes}
        self.assertEqual(result.status, "partial_granted")
        self.assertEqual(by_node["node01"].grant_status, "succeeded")
        self.assertEqual(by_node["node02"].grant_status, "failed")

    def _create_grant(self, store):
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
        return store.create_grant_plan(
            request_code=request.request_code,
            linux_username="zhangsan",
            pool_id="k100_train",
            target_nodes=["node01", "node02"],
            sshuser_path="/public/bin/sshuser",
            duration_hours=72,
            planned_by="owner",
        )
```

- [ ] **Step 2: Run failing grant service tests**

Run:

```powershell
python -m unittest tests.test_sshuser_grant_service -v
```

Expected: FAIL with `ModuleNotFoundError: No module named 'sshuser_grant_service'`.

- [ ] **Step 3: Implement minimal grant service**

Create `feishu_ops/sshuser_grant_service.py`:

```python
"""Business service for confirmed sshuser grants and revocations."""
from dataclasses import dataclass
from typing import Iterable, List

from sshuser_safety import SshuserSafetyError, validate_linux_username, validate_node, validate_sshuser_path


@dataclass
class GrantServiceResult:
    grant_code: str
    request_code: str
    status: str
    owner_message: str
    requester_message: str


class SshuserGrantService:
    def __init__(self, store, executor, allowed_nodes: Iterable[str], configured_sshuser_path: str, audit_logger=None):
        self.store = store
        self.executor = executor
        self.allowed_nodes = set(allowed_nodes)
        self.configured_sshuser_path = configured_sshuser_path
        self.audit_logger = audit_logger

    def confirm_grant(self, grant_code: str, actor: str) -> GrantServiceResult:
        claimed = self.store.claim_grant_status(grant_code, ["planned", "grant_failed", "partial_granted"], "granting", actor=actor)
        if not claimed:
            grant = self.store.get_grant(grant_code)
            status = grant.status if grant else "not_found"
            return GrantServiceResult(grant_code, grant.request_code if grant else "", status, f"⚠️ 授权 {grant_code} 当前状态不可执行：{status}", "")

        grant = self.store.get_grant(grant_code)
        username = validate_linux_username(grant.linux_username)
        sshuser_path = validate_sshuser_path(grant.sshuser_path, self.configured_sshuser_path)

        for node_record in self.store.list_grant_nodes(grant_code):
            if node_record.grant_status in {"succeeded", "skipped_preexisting", "covered_by_active_grant"}:
                continue
            try:
                node = validate_node(node_record.node, self.allowed_nodes)
                check = self.executor.check_access(node, username, sshuser_path)
                if not check.success:
                    self.store.update_grant_node_grant_result(grant_code, node, False, "failed", "failed", check.error_message or check.stderr)
                    continue
                if check.present:
                    self.store.update_grant_node_grant_result(grant_code, node, True, "present", "skipped_preexisting", "")
                    continue
                result = self.executor.grant_access(node, username, sshuser_path)
                if result.success:
                    self.store.update_grant_node_grant_result(grant_code, node, False, "absent", "succeeded", "")
                else:
                    self.store.update_grant_node_grant_result(grant_code, node, False, "absent", "failed", result.error_message or result.stderr)
            except SshuserSafetyError as exc:
                self.store.update_grant_node_grant_result(grant_code, node_record.node, False, "failed", "failed", str(exc))

        status = self._aggregate_grant_status(self.store.list_grant_nodes(grant_code))
        self.store.update_grant_status(grant_code, status)
        grant = self.store.get_grant(grant_code)
        return GrantServiceResult(
            grant_code=grant_code,
            request_code=grant.request_code,
            status=status,
            owner_message=self.format_grant_owner_message(grant_code),
            requester_message=self.format_grant_requester_message(grant_code),
        )

    def _aggregate_grant_status(self, nodes: List) -> str:
        success_statuses = {"succeeded", "skipped_preexisting", "covered_by_active_grant"}
        successes = [node for node in nodes if node.grant_status in success_statuses]
        failures = [node for node in nodes if node.grant_status == "failed"]
        if successes and not failures:
            return "granted"
        if successes and failures:
            return "partial_granted"
        return "grant_failed"

    def format_grant_owner_message(self, grant_code: str) -> str:
        grant = self.store.get_grant(grant_code)
        lines = [f"授权执行结果：{grant.request_code} / {grant.grant_code}", f"用户：{grant.linux_username}", f"资源池：{grant.pool_id}", "节点："]
        for node in self.store.list_grant_nodes(grant_code):
            lines.append(f"- {node.node}: {node.grant_status}{('，' + node.grant_last_error) if node.grant_last_error else ''}")
        return "\n".join(lines)

    def format_grant_requester_message(self, grant_code: str) -> str:
        grant = self.store.get_grant(grant_code)
        nodes = [node.node for node in self.store.list_grant_nodes(grant_code) if node.grant_status in {"succeeded", "skipped_preexisting", "covered_by_active_grant"}]
        return f"资源申请已授权：{grant.request_code}\n资源池：{grant.pool_id}\n可登录节点：\n" + "\n".join(nodes)
```

- [ ] **Step 4: Run grant service tests**

Run:

```powershell
python -m unittest tests.test_sshuser_grant_service -v
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add feishu_ops/sshuser_grant_service.py tests/test_sshuser_grant_service.py
git commit -m "feat: confirm sshuser grants"
```

---

### Task 6: Add active overlap detection for grants and revokes

**Files:**
- Modify: `feishu_ops/resource_request_store.py`
- Modify: `feishu_ops/sshuser_grant_service.py`
- Modify: `tests/test_sshuser_grant_service.py`

- [ ] **Step 1: Add failing overlap test**

Append this test to `SshuserGrantServiceTests`:

```python
    def test_second_active_grant_is_covered_by_first_and_last_grant_revokes(self):
        from resource_request_store import ResourceRequestStore
        from sshuser_executor import FakeSshuserExecutor
        from sshuser_grant_service import SshuserGrantService
        import tempfile

        with tempfile.TemporaryDirectory() as tmpdir:
            store = ResourceRequestStore(f"{tmpdir}/resource.db")
            first = self._create_grant(store)
            second = self._create_grant(store)
            executor = FakeSshuserExecutor(access_by_node={"node01": False, "node02": False})
            service = SshuserGrantService(store, executor, {"node01", "node02"}, "/public/bin/sshuser")

            service.confirm_grant(first.grant_code, actor="ou_owner")
            executor.access_by_node = {"node01": True, "node02": True}
            service.confirm_grant(second.grant_code, actor="ou_owner")
            second_nodes = store.list_grant_nodes(second.grant_code)

        self.assertEqual({node.grant_status for node in second_nodes}, {"covered_by_active_grant"})
```

- [ ] **Step 2: Run failing overlap test**

Run:

```powershell
python -m unittest tests.test_sshuser_grant_service.SshuserGrantServiceTests.test_second_active_grant_is_covered_by_first_and_last_grant_revokes -v
```

Expected: FAIL because second grant nodes are `skipped_preexisting`.

- [ ] **Step 3: Add store query for active system grants**

Add this method to `ResourceRequestStore`:

```python
    def has_active_system_grant_for_node(self, linux_username: str, node: str, exclude_grant_code: str, now_iso: str) -> bool:
        conn = self._connect()
        try:
            row = conn.execute("""
                SELECT 1
                FROM resource_grant_nodes gn
                JOIN resource_grants g ON g.grant_code = gn.grant_code
                WHERE gn.linux_username = ?
                  AND gn.node = ?
                  AND gn.grant_code != ?
                  AND g.status IN ('granted', 'partial_granted', 'revoking')
                  AND g.valid_until > ?
                  AND gn.grant_status IN ('succeeded', 'covered_by_active_grant')
                LIMIT 1
            """, (linux_username, node, exclude_grant_code, now_iso)).fetchone()
        finally:
            conn.close()
        return row is not None
```

- [ ] **Step 4: Use active coverage in `confirm_grant`**

In `feishu_ops/sshuser_grant_service.py`, add import:

```python
from datetime import UTC, datetime
```

In `confirm_grant`, replace the `if check.present:` block with:

```python
                if check.present:
                    covered = self.store.has_active_system_grant_for_node(
                        username,
                        node,
                        exclude_grant_code=grant_code,
                        now_iso=datetime.now(UTC).isoformat(),
                    )
                    if covered:
                        self.store.update_grant_node_grant_result(grant_code, node, False, "present", "covered_by_active_grant", "")
                    else:
                        self.store.update_grant_node_grant_result(grant_code, node, True, "present", "skipped_preexisting", "")
                    continue
```

- [ ] **Step 5: Run grant service tests**

Run:

```powershell
python -m unittest tests.test_sshuser_grant_service -v
```

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add feishu_ops/resource_request_store.py feishu_ops/sshuser_grant_service.py tests/test_sshuser_grant_service.py
git commit -m "feat: track overlapping sshuser grants"
```

---

### Task 7: Implement conservative revoke logic

**Files:**
- Modify: `feishu_ops/sshuser_grant_service.py`
- Modify: `tests/test_sshuser_grant_service.py`

- [ ] **Step 1: Add failing revoke tests**

Append these tests to `SshuserGrantServiceTests`:

```python
    def test_revoke_skips_preexisting_and_deletes_system_added_access(self):
        from resource_request_store import ResourceRequestStore
        from sshuser_executor import FakeSshuserExecutor
        from sshuser_grant_service import SshuserGrantService
        import tempfile

        with tempfile.TemporaryDirectory() as tmpdir:
            store = ResourceRequestStore(f"{tmpdir}/resource.db")
            grant = self._create_grant(store)
            executor = FakeSshuserExecutor(access_by_node={"node01": False, "node02": True})
            service = SshuserGrantService(store, executor, {"node01", "node02"}, "/public/bin/sshuser")
            service.confirm_grant(grant.grant_code, actor="ou_owner")
            executor.access_by_node = {"node01": True, "node02": True}

            result = service.revoke_grant(grant.grant_code, actor="reaper")
            nodes = store.list_grant_nodes(grant.grant_code)

        by_node = {node.node: node for node in nodes}
        self.assertEqual(result.status, "revoked")
        self.assertEqual(by_node["node01"].revoke_status, "succeeded")
        self.assertEqual(by_node["node02"].revoke_status, "skipped_preexisting")
        self.assertIn(("del", "node01", "zhangsan", "/public/bin/sshuser"), executor.calls)
        self.assertNotIn(("del", "node02", "zhangsan", "/public/bin/sshuser"), executor.calls)

    def test_revoke_skips_when_other_active_grant_exists(self):
        from resource_request_store import ResourceRequestStore
        from sshuser_executor import FakeSshuserExecutor
        from sshuser_grant_service import SshuserGrantService
        import tempfile

        with tempfile.TemporaryDirectory() as tmpdir:
            store = ResourceRequestStore(f"{tmpdir}/resource.db")
            first = self._create_grant(store)
            second = self._create_grant(store)
            executor = FakeSshuserExecutor(access_by_node={"node01": False, "node02": False})
            service = SshuserGrantService(store, executor, {"node01", "node02"}, "/public/bin/sshuser")
            service.confirm_grant(first.grant_code, actor="ou_owner")
            executor.access_by_node = {"node01": True, "node02": True}
            service.confirm_grant(second.grant_code, actor="ou_owner")

            result = service.revoke_grant(first.grant_code, actor="reaper")
            nodes = store.list_grant_nodes(first.grant_code)

        self.assertEqual(result.status, "revoked")
        self.assertEqual({node.revoke_status for node in nodes}, {"skipped_active_grant"})
```

- [ ] **Step 2: Run failing revoke tests**

Run:

```powershell
python -m unittest tests.test_sshuser_grant_service.SshuserGrantServiceTests.test_revoke_skips_preexisting_and_deletes_system_added_access tests.test_sshuser_grant_service.SshuserGrantServiceTests.test_revoke_skips_when_other_active_grant_exists -v
```

Expected: FAIL with missing `revoke_grant`.

- [ ] **Step 3: Implement `revoke_grant` and revoke aggregation**

Add this method to `SshuserGrantService`:

```python
    def revoke_grant(self, grant_code: str, actor: str = "reaper") -> GrantServiceResult:
        claimed = self.store.claim_grant_status(
            grant_code,
            ["granted", "partial_granted", "partial_revoked", "revoke_failed"],
            "revoking",
            actor=actor,
        )
        if not claimed:
            grant = self.store.get_grant(grant_code)
            if grant and grant.status == "revoking":
                pass
            else:
                status = grant.status if grant else "not_found"
                return GrantServiceResult(grant_code, grant.request_code if grant else "", status, f"⚠️ 撤权 {grant_code} 当前状态不可执行：{status}", "")

        grant = self.store.get_grant(grant_code)
        username = validate_linux_username(grant.linux_username)
        sshuser_path = validate_sshuser_path(grant.sshuser_path, self.configured_sshuser_path)

        for node_record in self.store.list_grant_nodes(grant_code):
            if node_record.revoke_status in {"succeeded", "skipped_preexisting", "skipped_active_grant", "skipped_not_granted", "succeeded_manual"}:
                continue
            try:
                node = validate_node(node_record.node, self.allowed_nodes)
                if node_record.grant_status == "skipped_preexisting" or node_record.access_existed_before:
                    self.store.update_grant_node_revoke_result(grant_code, node, "skipped_preexisting", "")
                    continue
                if node_record.grant_status not in {"succeeded", "covered_by_active_grant"}:
                    self.store.update_grant_node_revoke_result(grant_code, node, "skipped_not_granted", "")
                    continue
                if self.store.has_active_system_grant_for_node(username, node, grant_code, datetime.now(UTC).isoformat()):
                    self.store.update_grant_node_revoke_result(grant_code, node, "skipped_active_grant", "")
                    continue
                check = self.executor.check_access(node, username, sshuser_path)
                if not check.success:
                    self.store.update_grant_node_revoke_result(grant_code, node, "failed", check.error_message or check.stderr)
                    continue
                if not check.present:
                    self.store.update_grant_node_revoke_result(grant_code, node, "succeeded", "already_absent")
                    continue
                result = self.executor.revoke_access(node, username, sshuser_path)
                if not result.success:
                    self.store.update_grant_node_revoke_result(grant_code, node, "failed", result.error_message or result.stderr)
                    continue
                self.store.update_grant_node_revoke_result(grant_code, node, "succeeded", "")
            except SshuserSafetyError as exc:
                self.store.update_grant_node_revoke_result(grant_code, node_record.node, "failed", str(exc))

        status = self._aggregate_revoke_status(self.store.list_grant_nodes(grant_code))
        self.store.update_grant_status(grant_code, status)
        grant = self.store.get_grant(grant_code)
        return GrantServiceResult(
            grant_code=grant_code,
            request_code=grant.request_code,
            status=status,
            owner_message=self.format_revoke_owner_message(grant_code),
            requester_message=f"资源申请已到期：{grant.request_code}\n本次申请对应的访问权限已回收。" if status == "revoked" else "",
        )

    def _aggregate_revoke_status(self, nodes: List) -> str:
        terminal_success = {"succeeded", "skipped_preexisting", "skipped_active_grant", "skipped_not_granted", "succeeded_manual"}
        successes = [node for node in nodes if node.revoke_status in terminal_success]
        failures = [node for node in nodes if node.revoke_status == "failed"]
        if successes and not failures:
            return "revoked"
        if successes and failures:
            return "partial_revoked"
        return "revoke_failed"

    def format_revoke_owner_message(self, grant_code: str) -> str:
        grant = self.store.get_grant(grant_code)
        lines = [f"撤权执行结果：{grant.request_code} / {grant.grant_code}", f"用户：{grant.linux_username}", f"资源池：{grant.pool_id}", "节点："]
        for node in self.store.list_grant_nodes(grant_code):
            lines.append(f"- {node.node}: {node.revoke_status}{('，' + node.revoke_last_error) if node.revoke_last_error else ''}")
        return "\n".join(lines)
```

- [ ] **Step 4: Run grant service tests**

Run:

```powershell
python -m unittest tests.test_sshuser_grant_service -v
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add feishu_ops/sshuser_grant_service.py tests/test_sshuser_grant_service.py
git commit -m "feat: revoke sshuser grants safely"
```

---

### Task 8: Add expiry reaper

**Files:**
- Modify: `feishu_ops/resource_request_store.py`
- Create: `feishu_ops/grant_reaper.py`
- Test: `tests/test_grant_reaper.py`

- [ ] **Step 1: Write failing reaper tests**

Create `tests/test_grant_reaper.py`:

```python
import sys
import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
FEISHU_OPS = PROJECT_ROOT / "feishu_ops"
if str(FEISHU_OPS) not in sys.path:
    sys.path.insert(0, str(FEISHU_OPS))


class GrantReaperTests(unittest.TestCase):
    def tearDown(self):
        for name in ["grant_reaper", "resource_request_store", "sshuser_executor", "sshuser_grant_service", "sshuser_safety"]:
            sys.modules.pop(name, None)

    def test_reaper_revokes_due_grant(self):
        from grant_reaper import GrantReaper
        from resource_request_store import ResourceRequestStore
        from sshuser_executor import FakeSshuserExecutor
        from sshuser_grant_service import SshuserGrantService

        with tempfile.TemporaryDirectory() as tmpdir:
            store = ResourceRequestStore(f"{tmpdir}/resource.db")
            grant = self._create_grant(store)
            executor = FakeSshuserExecutor(access_by_node={"node01": False, "node02": False})
            service = SshuserGrantService(store, executor, {"node01", "node02"}, "/public/bin/sshuser")
            service.confirm_grant(grant.grant_code, actor="ou_owner")
            store.force_grant_valid_until(grant.grant_code, (datetime.now(UTC) - timedelta(minutes=1)).isoformat())
            executor.access_by_node = {"node01": True, "node02": True}
            reaper = GrantReaper(store, service)

            results = reaper.revoke_due_grants()
            loaded = store.get_grant(grant.grant_code)

        self.assertEqual([result.status for result in results], ["revoked"])
        self.assertEqual(loaded.status, "revoked")

    def _create_grant(self, store):
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
        return store.create_grant_plan(
            request_code=request.request_code,
            linux_username="zhangsan",
            pool_id="k100_train",
            target_nodes=["node01", "node02"],
            sshuser_path="/public/bin/sshuser",
            duration_hours=72,
            planned_by="owner",
        )


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run failing reaper test**

Run:

```powershell
python -m unittest tests.test_grant_reaper -v
```

Expected: FAIL with `ModuleNotFoundError: No module named 'grant_reaper'` or missing store methods.

- [ ] **Step 3: Add due grant store methods**

Add these methods to `ResourceRequestStore`:

```python
    def list_due_grants(self, now_iso: str) -> List[ResourceGrantRecord]:
        conn = self._connect()
        try:
            rows = conn.execute("""
                SELECT grant_code, request_code, linux_username, pool_id, target_nodes, sshuser_path, valid_from,
                       valid_until, status, planned_by, COALESCE(confirmed_by, ''), COALESCE(last_error, '')
                FROM resource_grants
                WHERE valid_until <= ?
                  AND status IN ('granted', 'partial_granted', 'partial_revoked', 'revoke_failed')
                ORDER BY valid_until
            """, (now_iso,)).fetchall()
        finally:
            conn.close()
        return [_grant_from_row(row) for row in rows]

    def force_grant_valid_until(self, grant_code: str, valid_until: str) -> None:
        now = _now()
        conn = self._connect()
        try:
            conn.execute("UPDATE resource_grants SET valid_until = ?, updated_at = ? WHERE grant_code = ?", (valid_until, now, grant_code))
            conn.commit()
        finally:
            conn.close()
```

- [ ] **Step 4: Implement `GrantReaper`**

Create `feishu_ops/grant_reaper.py`:

```python
"""Expiry scanner for resource grants."""
from datetime import UTC, datetime
from typing import List


class GrantReaper:
    def __init__(self, store, grant_service):
        self.store = store
        self.grant_service = grant_service

    def revoke_due_grants(self) -> List:
        now_iso = datetime.now(UTC).isoformat()
        results = []
        for grant in self.store.list_due_grants(now_iso):
            results.append(self.grant_service.revoke_grant(grant.grant_code, actor="grant_reaper"))
        return results
```

- [ ] **Step 5: Run reaper tests**

Run:

```powershell
python -m unittest tests.test_grant_reaper -v
```

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add feishu_ops/resource_request_store.py feishu_ops/grant_reaper.py tests/test_grant_reaper.py
git commit -m "feat: revoke expired grants"
```

---

### Task 9: Extend owner command parsing

**Files:**
- Modify: `feishu_ops/resource_approval.py`
- Modify: `tests/test_resource_approval.py`

- [ ] **Step 1: Add failing parser tests**

Append these tests to `ResourceApprovalTests`:

```python
    def test_parse_grant_confirm_and_retry_commands(self):
        from resource_approval import parse_resource_owner_command

        confirm = parse_resource_owner_command("/grant G1 confirm")
        retry = parse_resource_owner_command("/grant G1 retry")

        self.assertEqual(confirm.action, "grant")
        self.assertEqual(confirm.grant_code, "G1")
        self.assertTrue(confirm.confirm)
        self.assertEqual(retry.action, "grant")
        self.assertEqual(retry.grant_code, "G1")
        self.assertEqual(retry.operation, "retry")

    def test_parse_revoke_retry_and_mark_done(self):
        from resource_approval import parse_resource_owner_command

        retry = parse_resource_owner_command("/revoke G1 retry")
        mark_done = parse_resource_owner_command("/revoke G1 mark-done node01,node02")

        self.assertEqual(retry.action, "revoke")
        self.assertEqual(retry.grant_code, "G1")
        self.assertEqual(retry.operation, "retry")
        self.assertEqual(mark_done.action, "revoke")
        self.assertEqual(mark_done.operation, "mark-done")
        self.assertEqual(mark_done.nodes, ["node01", "node02"])
```

- [ ] **Step 2: Run failing parser tests**

Run:

```powershell
python -m unittest tests.test_resource_approval.ResourceApprovalTests.test_parse_grant_confirm_and_retry_commands tests.test_resource_approval.ResourceApprovalTests.test_parse_revoke_retry_and_mark_done -v
```

Expected: FAIL because `grant_code`, `operation`, and `/revoke` parsing are missing.

- [ ] **Step 3: Update command dataclass and parser**

In `feishu_ops/resource_approval.py`, update `ResourceOwnerCommand`:

```python
@dataclass(frozen=True)
class ResourceOwnerCommand:
    action: str
    request_code: str = ""
    grant_code: str = ""
    duration_hours: int = 0
    reason: str = ""
    confirm: bool = False
    operation: str = ""
    nodes: List[str] = None
```

Update `RESOURCE_OWNER_COMMANDS`:

```python
RESOURCE_OWNER_COMMANDS = ("/approve", "/reject", "/grant", "/revoke")
```

Replace `parse_resource_owner_command` with:

```python
def parse_resource_owner_command(content: str) -> Optional[ResourceOwnerCommand]:
    parts = (content or "").strip().split(maxsplit=3)
    if not parts:
        return None
    action = parts[0].lower().lstrip("/")
    if action not in {"approve", "reject", "grant", "revoke"} or len(parts) < 2:
        return None
    code = parts[1].upper()
    rest = parts[2].strip() if len(parts) >= 3 else ""
    extra = parts[3].strip() if len(parts) >= 4 else ""
    if action == "approve":
        return ResourceOwnerCommand(action="approve", request_code=code, duration_hours=_parse_duration(rest), nodes=[])
    if action == "reject":
        reason = " ".join(item for item in [rest, extra] if item).strip()
        return ResourceOwnerCommand(action="reject", request_code=code, reason=reason, nodes=[])
    if action == "grant":
        operation = "retry" if rest.lower() == "retry" else "confirm" if rest.lower() == "confirm" else ""
        return ResourceOwnerCommand(action="grant", grant_code=code, confirm=operation == "confirm", operation=operation, nodes=[])
    if action == "revoke":
        nodes = [item.strip() for item in extra.split(",") if item.strip()] if rest.lower() == "mark-done" else []
        operation = rest.lower()
        return ResourceOwnerCommand(action="revoke", grant_code=code, operation=operation, nodes=nodes)
    return None
```

- [ ] **Step 4: Run approval tests**

Run:

```powershell
python -m unittest tests.test_resource_approval -v
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add feishu_ops/resource_approval.py tests/test_resource_approval.py
git commit -m "feat: parse sshuser grant commands"
```

---

### Task 10: Integrate confirmed grants into `main.py`

**Files:**
- Modify: `feishu_ops/main.py`
- Modify: `tests/test_resource_webhook_routing.py`

- [ ] **Step 1: Add failing webhook routing test for remote exec**

Append this test to `ResourceWebhookRoutingTests`:

```python
    def test_handle_grant_confirm_executes_phase2_service_when_remote_exec_enabled(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            self._configure_resource_env(tmpdir)
            os.environ["SSHUSER_GRANT_ENABLED"] = "true"
            os.environ["SSHUSER_REMOTE_EXEC_ENABLED"] = "true"
            self._install_fastapi_stub()
            main = importlib.import_module("main")
            parser = importlib.import_module("feishu_event_parser")
            sent = []
            main.feishu_sender.send_text = lambda content, chat_id=None, receive_id_type="chat_id": sent.append({
                "content": content,
                "chat_id": chat_id,
                "receive_id_type": receive_id_type,
            }) or True
            main.resource_request_store.create_request(
                feishu_user_id="ou_user",
                linux_username="zhangsan",
                project_name="客户验收",
                resource_type="K100",
                resource_amount=4,
                duration_hours=72,
                urgency="P1",
                deadline="",
                reason="精度测试",
                accept_queue=True,
                accept_downgrade=False,
                matched_pool_id="k100_train",
                priority_score=115,
                priority_reasons=["P1: +70"],
            )
            approve = parser.ParsedFeishuEvent(action="resource_owner_command", user_id="ou_owner", chat_id="oc_owner_bot", content="/approve R1 48h")
            main._handle_resource_owner_command(approve)
            confirm = parser.ParsedFeishuEvent(action="resource_owner_command", user_id="ou_owner", chat_id="oc_owner_bot", content="/grant G1 confirm")

            result = main._handle_resource_owner_command(confirm)
            grant = main.resource_request_store.get_grant("G1")

        self.assertEqual(result["status"], "resource_grant_confirmed")
        self.assertEqual(grant.status, "granted")
        self.assertTrue(any("授权执行结果" in call["content"] for call in sent))
```

- [ ] **Step 2: Run failing webhook test**

Run:

```powershell
python -m unittest tests.test_resource_webhook_routing.ResourceWebhookRoutingTests.test_handle_grant_confirm_executes_phase2_service_when_remote_exec_enabled -v
```

Expected: FAIL because `main.py` still returns Phase 1 disabled message.

- [ ] **Step 3: Instantiate fake executor/service in `main.py` for remote mode**

Add imports in `main.py`:

```python
from sshuser_executor import FakeSshuserExecutor
from sshuser_grant_service import SshuserGrantService
```

Add globals near resource globals:

```python
sshuser_executor = None
sshuser_grant_service = None
```

Inside `if config.resource_request.enabled:` after `resource_prometheus_client` initialization, add:

```python
    if config.resource_request.sshuser_grant_enabled and config.resource_request.sshuser_remote_exec_enabled:
        all_nodes = set()
        for pool in resource_pools_config.pools:
            all_nodes.update(pool.nodes)
        sshuser_executor = FakeSshuserExecutor()
        sshuser_grant_service = SshuserGrantService(
            store=resource_request_store,
            executor=sshuser_executor,
            allowed_nodes=all_nodes,
            configured_sshuser_path=config.resource_request.sshuser_command_path,
            audit_logger=audit_logger,
        )
```

This uses the fake executor temporarily. Task 14 replaces it with `JumpHostSshExecutor` when config is complete.

- [ ] **Step 4: Route `/grant G1 confirm` in `_handle_resource_owner_command`**

In `_handle_resource_owner_command`, handle `grant` and `revoke` actions before request lookup. Move the existing lines:

```python
    record = resource_request_store.get_request(command.request_code)
    if not record:
        owner_notifier.confirm(parsed.user_id, f"⚠️ 未找到申请 #{command.request_code}")
        return {"status": "not_found", "request_code": command.request_code}
```

so they appear after the new `grant`/`revoke` command blocks and before the existing `reject` block. This prevents `/grant G1 confirm` from being incorrectly looked up as request code `""`.

Add this `grant confirm` block before the moved request lookup:

```python
    if command.action == "grant" and command.confirm:
        if not (config.resource_request.sshuser_grant_enabled and config.resource_request.sshuser_remote_exec_enabled and sshuser_grant_service):
            owner_notifier.confirm(parsed.user_id, "⚠️ 当前为 advice-only 模式，请使用 /approve 生成 sshuser 授权建议")
            return {"status": "sshuser_grant_disabled"}
        result = sshuser_grant_service.confirm_grant(command.grant_code, actor=parsed.user_id)
        feishu_sender.send_text(result.owner_message, chat_id=parsed.user_id, receive_id_type="open_id")
        if result.requester_message:
            grant = resource_request_store.get_grant(command.grant_code)
            request = resource_request_store.get_request(grant.request_code) if grant else None
            if request:
                feishu_sender.send_text(result.requester_message, chat_id=request.feishu_user_id, receive_id_type="open_id")
        audit_logger.record(event="resource_grant_confirmed", grant_code=command.grant_code, owner_id=parsed.user_id, status=result.status)
        return {"status": "resource_grant_confirmed", "grant_code": command.grant_code, "grant_status": result.status}

    record = resource_request_store.get_request(command.request_code)
    if not record:
        owner_notifier.confirm(parsed.user_id, f"⚠️ 未找到申请 #{command.request_code}")
        return {"status": "not_found", "request_code": command.request_code}
```

- [ ] **Step 5: Run webhook routing tests**

Run:

```powershell
python -m unittest tests.test_resource_webhook_routing -v
```

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add feishu_ops/main.py tests/test_resource_webhook_routing.py
git commit -m "feat: wire confirmed sshuser grants"
```

---

### Task 11: Add revoke retry and manual mark-done commands

**Files:**
- Modify: `feishu_ops/resource_request_store.py`
- Modify: `feishu_ops/sshuser_grant_service.py`
- Modify: `feishu_ops/main.py`
- Modify: `tests/test_sshuser_grant_service.py`
- Modify: `tests/test_resource_webhook_routing.py`

- [ ] **Step 1: Add failing service test for mark-done**

Append this test to `SshuserGrantServiceTests`:

```python
    def test_mark_revoke_done_marks_only_selected_nodes(self):
        from resource_request_store import ResourceRequestStore
        from sshuser_executor import FakeSshuserExecutor
        from sshuser_grant_service import SshuserGrantService
        import tempfile

        with tempfile.TemporaryDirectory() as tmpdir:
            store = ResourceRequestStore(f"{tmpdir}/resource.db")
            grant = self._create_grant(store)
            executor = FakeSshuserExecutor(access_by_node={"node01": False, "node02": False}, revoke_success_by_node={"node01": False, "node02": False})
            service = SshuserGrantService(store, executor, {"node01", "node02"}, "/public/bin/sshuser")
            service.confirm_grant(grant.grant_code, actor="ou_owner")
            executor.access_by_node = {"node01": True, "node02": True}
            service.revoke_grant(grant.grant_code, actor="reaper")

            result = service.mark_revoke_done(grant.grant_code, ["node01"], actor="ou_owner")
            nodes = store.list_grant_nodes(grant.grant_code)

        by_node = {node.node: node for node in nodes}
        self.assertEqual(by_node["node01"].revoke_status, "succeeded_manual")
        self.assertEqual(by_node["node02"].revoke_status, "failed")
        self.assertEqual(result.status, "partial_revoked")
```

- [ ] **Step 2: Run failing mark-done test**

Run:

```powershell
python -m unittest tests.test_sshuser_grant_service.SshuserGrantServiceTests.test_mark_revoke_done_marks_only_selected_nodes -v
```

Expected: FAIL with missing `mark_revoke_done`.

- [ ] **Step 3: Add store method to set manual revoke status**

Add this method to `ResourceRequestStore`:

```python
    def mark_revoke_node_done(self, grant_code: str, node: str) -> None:
        now = _now()
        conn = self._connect()
        try:
            conn.execute("""
                UPDATE resource_grant_nodes
                SET revoke_status = 'succeeded_manual', revoked_at = ?, updated_at = ?
                WHERE grant_code = ? AND node = ?
            """, (now, now, grant_code, node))
            conn.commit()
        finally:
            conn.close()
```

- [ ] **Step 4: Add service retry wrappers and mark-done**

Add these methods to `SshuserGrantService`:

```python
    def retry_grant(self, grant_code: str, actor: str) -> GrantServiceResult:
        return self.confirm_grant(grant_code, actor)

    def retry_revoke(self, grant_code: str, actor: str) -> GrantServiceResult:
        return self.revoke_grant(grant_code, actor)

    def mark_revoke_done(self, grant_code: str, nodes: List[str], actor: str) -> GrantServiceResult:
        grant = self.store.get_grant(grant_code)
        valid_nodes = {node.node for node in self.store.list_grant_nodes(grant_code)}
        for node in nodes:
            safe_node = validate_node(node, valid_nodes)
            self.store.mark_revoke_node_done(grant_code, safe_node)
        status = self._aggregate_revoke_status(self.store.list_grant_nodes(grant_code))
        self.store.update_grant_status(grant_code, status)
        return GrantServiceResult(
            grant_code=grant_code,
            request_code=grant.request_code,
            status=status,
            owner_message=self.format_revoke_owner_message(grant_code),
            requester_message="",
        )
```

- [ ] **Step 5: Route `/grant G1 retry`, `/revoke G1 retry`, and `/revoke G1 mark-done ...` in `main.py`**

Insert these blocks in the same pre-request section created in Task 10: after the `grant confirm` block and before:

```python
    record = resource_request_store.get_request(command.request_code)
```

This keeps all grant-code based commands from being treated as request-code based `/approve` or `/reject` commands.

```python
    if command.action == "grant" and command.operation == "retry":
        if not sshuser_grant_service:
            owner_notifier.confirm(parsed.user_id, "⚠️ 当前未启用 sshuser 远程执行")
            return {"status": "sshuser_grant_disabled"}
        result = sshuser_grant_service.retry_grant(command.grant_code, actor=parsed.user_id)
        feishu_sender.send_text(result.owner_message, chat_id=parsed.user_id, receive_id_type="open_id")
        return {"status": "resource_grant_retry", "grant_code": command.grant_code, "grant_status": result.status}

    if command.action == "revoke" and command.operation == "retry":
        if not sshuser_grant_service:
            owner_notifier.confirm(parsed.user_id, "⚠️ 当前未启用 sshuser 远程执行")
            return {"status": "sshuser_grant_disabled"}
        result = sshuser_grant_service.retry_revoke(command.grant_code, actor=parsed.user_id)
        feishu_sender.send_text(result.owner_message, chat_id=parsed.user_id, receive_id_type="open_id")
        return {"status": "resource_revoke_retry", "grant_code": command.grant_code, "grant_status": result.status}

    if command.action == "revoke" and command.operation == "mark-done":
        if not sshuser_grant_service:
            owner_notifier.confirm(parsed.user_id, "⚠️ 当前未启用 sshuser 远程执行")
            return {"status": "sshuser_grant_disabled"}
        result = sshuser_grant_service.mark_revoke_done(command.grant_code, command.nodes, actor=parsed.user_id)
        feishu_sender.send_text(result.owner_message, chat_id=parsed.user_id, receive_id_type="open_id")
        audit_logger.record(event="resource_revoke_manual_mark_done", grant_code=command.grant_code, owner_id=parsed.user_id, nodes=command.nodes, status=result.status)
        return {"status": "resource_revoke_mark_done", "grant_code": command.grant_code, "grant_status": result.status}
```

- [ ] **Step 6: Run grant service and webhook tests**

Run:

```powershell
python -m unittest tests.test_sshuser_grant_service tests.test_resource_webhook_routing -v
```

Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add feishu_ops/resource_request_store.py feishu_ops/sshuser_grant_service.py feishu_ops/main.py tests/test_sshuser_grant_service.py tests/test_resource_webhook_routing.py
git commit -m "feat: add sshuser retry and manual revoke closure"
```

---

### Task 12: Add Phase 2 configuration and health reporting

**Files:**
- Modify: `feishu_ops/config.py`
- Modify: `feishu_ops/config_check.py`
- Modify: `feishu_ops/main.py`
- Modify: `.env.example`
- Modify: `docker/docker-compose.yml`
- Modify: `tests/test_config_check.py`
- Modify: `tests/test_resource_health.py`

- [ ] **Step 1: Add failing config validation test**

Append this test to `ConfigCheckTests`:

```python
    def test_remote_exec_enabled_requires_jump_host_settings(self):
        module = importlib.import_module("config_check")
        env = {
            "FEISHU_APP_ID": "app-id",
            "FEISHU_APP_SECRET": "secret",
            "ANTHROPIC_API_KEY": "key",
            "SSHUSER_GRANT_ENABLED": "true",
            "SSHUSER_REMOTE_EXEC_ENABLED": "true",
            "SSHUSER_COMMAND_PATH": "/public/bin/sshuser",
        }
        errors, warnings = module.validate_env(env)

        self.assertTrue(any("SSHUSER_JUMP_HOST" in item for item in errors))
        self.assertTrue(any("SSHUSER_JUMP_USER" in item for item in errors))
        self.assertTrue(any("SSHUSER_TARGET_USER" in item for item in errors))
```

- [ ] **Step 2: Add failing health test fields**

In `tests/test_resource_health.py`, update `tearDown` env cleanup list to include:

```python
"SSHUSER_REMOTE_EXEC_ENABLED",
"SSHUSER_JUMP_HOST",
"SSHUSER_SSH_KEY_PATH",
"SSHUSER_KNOWN_HOSTS_PATH",
```

Append this test:

```python
    def test_health_reports_phase2_remote_exec_readiness(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            pools_path = Path(tmpdir) / "resource_pools.yml"
            pools_path.write_text(textwrap.dedent("""
                resource_pools:
                  - pool_id: k100_train
                    name: K100-训练池
                    resource_type: K100
                    nodes: [node01]
                    sshuser_path: /public/bin/sshuser
                    total_devices: 8
                    default_grant_hours: 24
                    max_grant_hours: 72
                    enabled: true
            """), encoding="utf-8")
            os.environ["RESOURCE_REQUEST_ENABLED"] = "true"
            os.environ["RESOURCE_POOLS_CONFIG_PATH"] = str(pools_path)
            os.environ["RESOURCE_REQUEST_DB_PATH"] = str(Path(tmpdir) / "resource.db")
            os.environ["SSHUSER_GRANT_ENABLED"] = "true"
            os.environ["SSHUSER_REMOTE_EXEC_ENABLED"] = "true"
            os.environ["SSHUSER_JUMP_HOST"] = "jump.example"
            os.environ["SSHUSER_SSH_KEY_PATH"] = str(Path(tmpdir) / "id_rsa")
            os.environ["SSHUSER_KNOWN_HOSTS_PATH"] = str(Path(tmpdir) / "known_hosts")
            Path(os.environ["SSHUSER_SSH_KEY_PATH"]).write_text("key", encoding="utf-8")
            Path(os.environ["SSHUSER_KNOWN_HOSTS_PATH"]).write_text("jump ssh-rsa AAA", encoding="utf-8")
            self._install_runtime_stubs()
            main = importlib.import_module("main")

            payload = asyncio.run(main.health())

        resource = payload["resource_request"]
        self.assertEqual(resource["sshuser_remote_exec_enabled"], True)
        self.assertEqual(resource["mode"], "sshuser_mutation")
        self.assertEqual(resource["jump_host_configured"], True)
        self.assertEqual(resource["ssh_key_configured"], True)
        self.assertEqual(resource["known_hosts_configured"], True)
```

- [ ] **Step 3: Run failing config/health tests**

Run:

```powershell
python -m unittest tests.test_config_check tests.test_resource_health -v
```

Expected: FAIL because new config fields/checks are missing.

- [ ] **Step 4: Extend `ResourceRequestConfig` and env loading**

In `feishu_ops/config.py`, add fields to `ResourceRequestConfig`:

```python
    sshuser_jump_host: str = ""
    sshuser_jump_port: int = 22
    sshuser_jump_user: str = ""
    sshuser_ssh_key_path: str = ""
    sshuser_known_hosts_path: str = ""
    sshuser_target_user: str = ""
    sshuser_target_ssh_port: int = 22
    sshuser_command_timeout_seconds: int = 15
    sshuser_max_retries: int = 2
    sshuser_retry_backoff_seconds: int = 3
    sshuser_max_parallel_nodes: int = 1
```

In `from_env`, populate them:

```python
                sshuser_jump_host=os.getenv("SSHUSER_JUMP_HOST", ""),
                sshuser_jump_port=int(os.getenv("SSHUSER_JUMP_PORT", "22")),
                sshuser_jump_user=os.getenv("SSHUSER_JUMP_USER", ""),
                sshuser_ssh_key_path=os.getenv("SSHUSER_SSH_KEY_PATH", ""),
                sshuser_known_hosts_path=os.getenv("SSHUSER_KNOWN_HOSTS_PATH", ""),
                sshuser_target_user=os.getenv("SSHUSER_TARGET_USER", ""),
                sshuser_target_ssh_port=int(os.getenv("SSHUSER_TARGET_SSH_PORT", "22")),
                sshuser_command_timeout_seconds=int(os.getenv("SSHUSER_COMMAND_TIMEOUT_SECONDS", "15")),
                sshuser_max_retries=int(os.getenv("SSHUSER_MAX_RETRIES", "2")),
                sshuser_retry_backoff_seconds=int(os.getenv("SSHUSER_RETRY_BACKOFF_SECONDS", "3")),
                sshuser_max_parallel_nodes=int(os.getenv("SSHUSER_MAX_PARALLEL_NODES", "1")),
```

- [ ] **Step 5: Extend `config_check.py` remote validation**

Inside `if sshuser_enabled:`, after the existing warning branch, add:

```python
        if _env_bool(env, "SSHUSER_REMOTE_EXEC_ENABLED", "false"):
            required_remote = ["SSHUSER_JUMP_HOST", "SSHUSER_JUMP_USER", "SSHUSER_TARGET_USER"]
            for name in required_remote:
                if not env.get(name):
                    errors.append(f"{name} is required when SSHUSER_REMOTE_EXEC_ENABLED=true")
            if env.get("SSHUSER_COMMAND_PATH", "/public/bin/sshuser") != "/public/bin/sshuser":
                errors.append("SSHUSER_COMMAND_PATH must be /public/bin/sshuser when SSHUSER_REMOTE_EXEC_ENABLED=true")
            for name in ["SSHUSER_SSH_KEY_PATH", "SSHUSER_KNOWN_HOSTS_PATH"]:
                value = env.get(name)
                if not value:
                    errors.append(f"{name} is required when SSHUSER_REMOTE_EXEC_ENABLED=true")
                elif not Path(value).exists():
                    errors.append(f"{name} does not exist: {value}")
```

- [ ] **Step 6: Extend `_resource_health` in `main.py`**

Return these additional fields:

```python
        "sshuser_remote_exec_enabled": bool(config.resource_request.sshuser_remote_exec_enabled),
        "mode": "sshuser_mutation" if config.resource_request.sshuser_grant_enabled and config.resource_request.sshuser_remote_exec_enabled else "sshuser_advice_only",
        "jump_host_configured": bool(config.resource_request.sshuser_jump_host),
        "ssh_key_configured": bool(config.resource_request.sshuser_ssh_key_path),
        "known_hosts_configured": bool(config.resource_request.sshuser_known_hosts_path),
```

Keep existing fields.

- [ ] **Step 7: Update `.env.example`**

Replace lines 39-43 with:

```env
# Phase 1 默认不自动执行节点命令，只生成 sshuser 授权建议；Phase 2 需同时打开 GRANT 与 REMOTE_EXEC
SSHUSER_GRANT_ENABLED=false
SSHUSER_COMMAND_PATH=/public/bin/sshuser
SSHUSER_REMOTE_EXEC_ENABLED=false
SSHUSER_JUMP_HOST=
SSHUSER_JUMP_PORT=22
SSHUSER_JUMP_USER=resource_bot
SSHUSER_SSH_KEY_PATH=/app/secrets/resource_bot_id_rsa
SSHUSER_KNOWN_HOSTS_PATH=/app/secrets/known_hosts
SSHUSER_TARGET_USER=resource_exec
SSHUSER_TARGET_SSH_PORT=22
SSHUSER_CONNECT_TIMEOUT_SECONDS=5
SSHUSER_COMMAND_TIMEOUT_SECONDS=15
SSHUSER_MAX_RETRIES=2
SSHUSER_RETRY_BACKOFF_SECONDS=3
SSHUSER_MAX_PARALLEL_NODES=1
SSHUSER_EXECUTOR_TYPE=jump_host
```

- [ ] **Step 8: Update `docker/docker-compose.yml` environment**

Add these entries to the `haiguang-ops` service `environment:` section immediately after `SSHUSER_REMOTE_EXEC_ENABLED`:

```yaml
      - SSHUSER_JUMP_HOST=${SSHUSER_JUMP_HOST:-}
      - SSHUSER_JUMP_PORT=${SSHUSER_JUMP_PORT:-22}
      - SSHUSER_JUMP_USER=${SSHUSER_JUMP_USER:-resource_bot}
      - SSHUSER_SSH_KEY_PATH=${SSHUSER_SSH_KEY_PATH:-/app/secrets/resource_bot_id_rsa}
      - SSHUSER_KNOWN_HOSTS_PATH=${SSHUSER_KNOWN_HOSTS_PATH:-/app/secrets/known_hosts}
      - SSHUSER_TARGET_USER=${SSHUSER_TARGET_USER:-resource_exec}
      - SSHUSER_TARGET_SSH_PORT=${SSHUSER_TARGET_SSH_PORT:-22}
      - SSHUSER_COMMAND_TIMEOUT_SECONDS=${SSHUSER_COMMAND_TIMEOUT_SECONDS:-15}
      - SSHUSER_MAX_RETRIES=${SSHUSER_MAX_RETRIES:-2}
      - SSHUSER_RETRY_BACKOFF_SECONDS=${SSHUSER_RETRY_BACKOFF_SECONDS:-3}
      - SSHUSER_MAX_PARALLEL_NODES=${SSHUSER_MAX_PARALLEL_NODES:-1}
      - SSHUSER_EXECUTOR_TYPE=${SSHUSER_EXECUTOR_TYPE:-jump_host}
```

- [ ] **Step 9: Run config and health tests**

Run:

```powershell
python -m unittest tests.test_config_check tests.test_resource_health -v
```

Expected: PASS.

- [ ] **Step 10: Commit**

```bash
git add feishu_ops/config.py feishu_ops/config_check.py feishu_ops/main.py .env.example docker/docker-compose.yml tests/test_config_check.py tests/test_resource_health.py
git commit -m "feat: add sshuser remote execution config"
```

---

### Task 13: Implement jump-host executor

**Files:**
- Create: `feishu_ops/jump_host_executor.py`
- Test: `tests/test_jump_host_executor.py`

- [ ] **Step 1: Write failing command-construction tests**

Create `tests/test_jump_host_executor.py`:

```python
import sys
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
FEISHU_OPS = PROJECT_ROOT / "feishu_ops"
if str(FEISHU_OPS) not in sys.path:
    sys.path.insert(0, str(FEISHU_OPS))


class JumpHostExecutorTests(unittest.TestCase):
    def tearDown(self):
        sys.modules.pop("jump_host_executor", None)
        sys.modules.pop("sshuser_executor", None)

    def test_builds_nested_sshuser_add_command(self):
        from jump_host_executor import JumpHostSshExecutor

        executor = JumpHostSshExecutor(
            jump_host="jump.example",
            jump_port=22,
            jump_user="resource_bot",
            ssh_key_path="/app/secrets/id_rsa",
            known_hosts_path="/app/secrets/known_hosts",
            target_user="resource_exec",
            target_port=22,
            connect_timeout_seconds=5,
            command_timeout_seconds=15,
        )

        command = executor.build_command("node01", "add", "zhangsan", "/public/bin/sshuser")

        joined = " ".join(command)
        self.assertIn("BatchMode=yes", joined)
        self.assertIn("StrictHostKeyChecking=yes", joined)
        self.assertIn("resource_bot@jump.example", joined)
        self.assertIn("resource_exec@node01", joined)
        self.assertIn("sudo /public/bin/sshuser add zhangsan", joined)
        self.assertNotIn(";", joined)

    def test_rejects_unsafe_node_before_nested_shell_command(self):
        from jump_host_executor import JumpHostSshExecutor

        executor = JumpHostSshExecutor(
            jump_host="jump.example",
            jump_port=22,
            jump_user="resource_bot",
            ssh_key_path="/app/secrets/id_rsa",
            known_hosts_path="/app/secrets/known_hosts",
            target_user="resource_exec",
            target_port=22,
            connect_timeout_seconds=5,
            command_timeout_seconds=15,
        )

        with self.assertRaises(ValueError):
            executor.build_command("node01;id", "add", "zhangsan", "/public/bin/sshuser")


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run failing jump-host test**

Run:

```powershell
python -m unittest tests.test_jump_host_executor -v
```

Expected: FAIL with `ModuleNotFoundError: No module named 'jump_host_executor'`.

- [ ] **Step 3: Implement `JumpHostSshExecutor`**

Create `feishu_ops/jump_host_executor.py`:

```python
"""Jump-host based implementation of structured sshuser execution."""
import re
import shlex
import subprocess
from datetime import UTC, datetime

from sshuser_executor import AccessCheckResult, NodeCommandResult, SshuserExecutor
from sshuser_safety import parse_allow_users, validate_linux_username, validate_node, validate_sshuser_path


_HOST_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_PATH_RE = re.compile(r"^/[A-Za-z0-9_./-]+$")


def _validate_hostname(value: str, field_name: str) -> str:
    hostname = (value or "").strip()
    if not _HOST_RE.fullmatch(hostname):
        raise ValueError(f"invalid {field_name}: {value!r}")
    return hostname


def _validate_local_path(value: str, field_name: str) -> str:
    path = (value or "").strip()
    if not _PATH_RE.fullmatch(path):
        raise ValueError(f"invalid {field_name}: {value!r}")
    return path


def _validate_port(value: int, field_name: str) -> int:
    port = int(value)
    if port < 1 or port > 65535:
        raise ValueError(f"invalid {field_name}: {value!r}")
    return port


class JumpHostSshExecutor(SshuserExecutor):
    def __init__(self, jump_host: str, jump_port: int, jump_user: str, ssh_key_path: str, known_hosts_path: str, target_user: str, target_port: int, connect_timeout_seconds: int, command_timeout_seconds: int):
        self.jump_host = _validate_hostname(jump_host, "jump_host")
        self.jump_port = _validate_port(jump_port, "jump_port")
        self.jump_user = validate_linux_username(jump_user)
        self.ssh_key_path = _validate_local_path(ssh_key_path, "ssh_key_path")
        self.known_hosts_path = _validate_local_path(known_hosts_path, "known_hosts_path")
        self.target_user = validate_linux_username(target_user)
        self.target_port = _validate_port(target_port, "target_port")
        self.connect_timeout_seconds = connect_timeout_seconds
        self.command_timeout_seconds = command_timeout_seconds

    def build_command(self, node: str, operation: str, linux_username: str, sshuser_path: str):
        safe_node = validate_node(node, {node})
        username = validate_linux_username(linux_username)
        safe_path = validate_sshuser_path(sshuser_path, "/public/bin/sshuser")
        if operation not in {"add", "del", "check"}:
            raise ValueError(f"unsupported sshuser operation: {operation}")
        remote_command = "sudo grep -E '^AllowUsers[[:space:]]+' /etc/ssh/sshd_config" if operation == "check" else f"sudo {safe_path} {operation} {username}"
        target = f"{self.target_user}@{safe_node}"
        nested = [
            "ssh",
            "-p", str(self.target_port),
            "-o", "BatchMode=yes",
            "-o", "StrictHostKeyChecking=yes",
            "-o", f"UserKnownHostsFile={self.known_hosts_path}",
            "-o", f"ConnectTimeout={self.connect_timeout_seconds}",
            target,
            remote_command,
        ]
        return [
            "ssh",
            "-i", self.ssh_key_path,
            "-p", str(self.jump_port),
            "-o", "BatchMode=yes",
            "-o", "StrictHostKeyChecking=yes",
            "-o", f"UserKnownHostsFile={self.known_hosts_path}",
            "-o", f"ConnectTimeout={self.connect_timeout_seconds}",
            f"{self.jump_user}@{self.jump_host}",
            shlex.join(nested),
        ]

    def check_access(self, node: str, linux_username: str, sshuser_path: str) -> AccessCheckResult:
        result = self._run(node, "check", linux_username, sshuser_path)
        if not result.success:
            return AccessCheckResult(node=node, present=False, success=False, stdout=result.stdout, stderr=result.stderr, error_type=result.error_type, error_message=result.error_message)
        try:
            present = parse_allow_users(result.stdout, linux_username)
            return AccessCheckResult(node=node, present=present, success=True, stdout=result.stdout, stderr=result.stderr)
        except Exception as exc:
            return AccessCheckResult(node=node, present=False, success=False, stdout=result.stdout, stderr=result.stderr, error_type="access_check_parse_failed", error_message=str(exc))

    def grant_access(self, node: str, linux_username: str, sshuser_path: str) -> NodeCommandResult:
        return self._run(node, "add", linux_username, sshuser_path)

    def revoke_access(self, node: str, linux_username: str, sshuser_path: str) -> NodeCommandResult:
        return self._run(node, "del", linux_username, sshuser_path)

    def _run(self, node: str, operation: str, linux_username: str, sshuser_path: str) -> NodeCommandResult:
        started = datetime.now(UTC)
        command = self.build_command(node, operation, linux_username, sshuser_path)
        try:
            completed = subprocess.run(command, capture_output=True, text=True, timeout=self.command_timeout_seconds, check=False)
            finished = datetime.now(UTC)
            success = completed.returncode == 0
            return NodeCommandResult(
                node=node,
                operation=operation,
                success=success,
                exit_code=completed.returncode,
                stdout=completed.stdout,
                stderr=completed.stderr,
                started_at=started.isoformat(),
                finished_at=finished.isoformat(),
                duration_ms=int((finished - started).total_seconds() * 1000),
                error_type="" if success else "nonzero_exit",
                error_message="" if success else completed.stderr.strip(),
            )
        except subprocess.TimeoutExpired as exc:
            finished = datetime.now(UTC)
            return NodeCommandResult(
                node=node,
                operation=operation,
                success=False,
                exit_code=-1,
                stdout=exc.stdout or "",
                stderr=exc.stderr or "",
                started_at=started.isoformat(),
                finished_at=finished.isoformat(),
                duration_ms=int((finished - started).total_seconds() * 1000),
                error_type="timeout",
                error_message="command timed out",
            )
```

- [ ] **Step 4: Run jump-host tests**

Run:

```powershell
python -m unittest tests.test_jump_host_executor -v
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add feishu_ops/jump_host_executor.py tests/test_jump_host_executor.py
git commit -m "feat: add jump host sshuser executor"
```

---

### Task 14: Wire jump-host executor behind config flag

**Files:**
- Modify: `feishu_ops/main.py`
- Modify: `tests/test_resource_webhook_routing.py`

- [ ] **Step 1: Add test that main uses configured executor class when config present**

Append this assertion to the remote exec webhook test from Task 10 after importing `main`:

```python
            self.assertIsNotNone(main.sshuser_grant_service)
```

In the remote exec webhook test from Task 10, set the executor type before importing `main`:

```python
os.environ["SSHUSER_EXECUTOR_TYPE"] = "fake"
```

- [ ] **Step 2: Extend config with executor type**

In `ResourceRequestConfig`, add:

```python
    sshuser_executor_type: str = "jump_host"
```

In `from_env`, add:

```python
                sshuser_executor_type=os.getenv("SSHUSER_EXECUTOR_TYPE", "jump_host"),
```

- [ ] **Step 3: Wire executor selection in `main.py`**

Import:

```python
from jump_host_executor import JumpHostSshExecutor
```

Replace fake executor initialization with:

```python
        if config.resource_request.sshuser_executor_type == "fake":
            sshuser_executor = FakeSshuserExecutor()
        else:
            sshuser_executor = JumpHostSshExecutor(
                jump_host=config.resource_request.sshuser_jump_host,
                jump_port=config.resource_request.sshuser_jump_port,
                jump_user=config.resource_request.sshuser_jump_user,
                ssh_key_path=config.resource_request.sshuser_ssh_key_path,
                known_hosts_path=config.resource_request.sshuser_known_hosts_path,
                target_user=config.resource_request.sshuser_target_user,
                target_port=config.resource_request.sshuser_target_ssh_port,
                connect_timeout_seconds=config.resource_request.sshuser_connect_timeout_seconds,
                command_timeout_seconds=config.resource_request.sshuser_command_timeout_seconds,
            )
```

- [ ] **Step 4: Update test env cleanup**

In `tests/test_resource_webhook_routing.py` `tearDown`, add:

```python
"SSHUSER_GRANT_ENABLED",
"SSHUSER_REMOTE_EXEC_ENABLED",
"SSHUSER_EXECUTOR_TYPE",
```

- [ ] **Step 5: Run webhook tests**

Run:

```powershell
python -m unittest tests.test_resource_webhook_routing -v
```

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add feishu_ops/config.py feishu_ops/main.py tests/test_resource_webhook_routing.py
git commit -m "feat: wire jump host executor"
```

---

### Task 15: Final verification and documentation cross-check

**Files:**
- Review: `docs/superpowers/specs/2026-05-11-resource-request-phase2-sshuser-design.md`
- Review: `README.md`

- [ ] **Step 1: Run all Python tests**

Run:

```powershell
python -m unittest discover -s tests -p "test_*.py" -v
```

Expected: PASS.

- [ ] **Step 2: Run config check with advice-only defaults**

Run:

```powershell
python feishu_ops/config_check.py
```

Expected: no Python traceback. If required env vars are intentionally absent locally, expected output includes missing required env errors rather than import/runtime errors.

- [ ] **Step 3: Search for forbidden old authorization terms**

Run:

```powershell
Select-String -Path "feishu_ops\*.py","tests\*.py","docs\superpowers\specs\2026-05-11-resource-request-phase2-sshuser-design.md" -Pattern "LDAP|ldap|usermod" | ForEach-Object { "$($_.Path):$($_.LineNumber):$($_.Line.Trim())" }
```

Expected: no output except backward-compatible `ldap_group` schema handling if it remains in `resource_request_store.py`.

- [ ] **Step 4: Search for placeholders in new plan/spec/code**

Run:

```powershell
Select-String -Path "feishu_ops\sshuser_*.py","feishu_ops\jump_host_executor.py","feishu_ops\grant_reaper.py" -Pattern "TBD|TODO|implement later|pass #|NotImplemented" | ForEach-Object { "$($_.Path):$($_.LineNumber):$($_.Line.Trim())" }
```

Expected: no output except `raise NotImplementedError` in the abstract `SshuserExecutor` base class.

- [ ] **Step 5: Commit final docs if changed**

```bash
git add README.md docs/superpowers/specs/2026-05-11-resource-request-phase2-sshuser-design.md docs/superpowers/plans/2026-05-11-resource-request-phase2-sshuser-implementation.md
git commit -m "docs: finalize phase2 sshuser plan"
```
