"""SQLite store for resource requests and grant plans."""
import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
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
    target_nodes: List[str]
    sshuser_path: str
    valid_from: str
    valid_until: str
    status: str
    planned_by: str
    confirmed_by: str = ""
    last_error: str = ""


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
                    target_nodes TEXT NOT NULL,
                    sshuser_path TEXT NOT NULL,
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
            conn.execute("""
                CREATE TABLE IF NOT EXISTS resource_audit_logs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    event TEXT NOT NULL,
                    request_code TEXT,
                    grant_code TEXT,
                    actor_feishu_id TEXT,
                    linux_username TEXT,
                    pool_id TEXT,
                    target_nodes TEXT,
                    details TEXT,
                    created_at TEXT NOT NULL
                )
            """)
            _ensure_column(conn, "resource_grants", "target_nodes", "TEXT NOT NULL DEFAULT '[]'")
            _ensure_column(conn, "resource_grants", "sshuser_path", "TEXT NOT NULL DEFAULT '/public/bin/sshuser'")
            _ensure_column(conn, "resource_grants", "grant_started_at", "TEXT")
            _ensure_column(conn, "resource_grants", "grant_finished_at", "TEXT")
            _ensure_column(conn, "resource_grants", "revoke_started_at", "TEXT")
            _ensure_column(conn, "resource_grants", "revoke_finished_at", "TEXT")
            _ensure_column(conn, "resource_grants", "expire_reminded_at", "TEXT")
            conn.commit()
        finally:
            conn.close()

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

    def create_grant_plan(self, request_code: str, linux_username: str, pool_id: str, target_nodes: List[str], sshuser_path: str, duration_hours: int, planned_by: str) -> ResourceGrantRecord:
        now_dt = datetime.now(timezone.utc)
        valid_from = now_dt.isoformat()
        valid_until = (now_dt + timedelta(hours=duration_hours)).isoformat()
        now = now_dt.isoformat()
        conn = self._connect()
        try:
            grant_code = f"G{self._next_id(conn, 'resource_grants')}"
            columns = [
                "grant_code",
                "request_code",
                "linux_username",
                "pool_id",
                "target_nodes",
                "sshuser_path",
                "valid_from",
                "valid_until",
                "status",
                "planned_by",
                "created_at",
                "updated_at",
            ]
            values = [
                grant_code,
                request_code,
                linux_username,
                pool_id,
                json.dumps(target_nodes, ensure_ascii=False),
                sshuser_path,
                valid_from,
                valid_until,
                "planned",
                planned_by,
                now,
                now,
            ]
            if _table_has_column(conn, "resource_grants", "ldap_group"):
                columns.insert(4, "ldap_group")
                values.insert(4, "")
            placeholders = ", ".join("?" for _ in columns)
            conn.execute(f"""
                INSERT INTO resource_grants ({", ".join(columns)})
                VALUES ({placeholders})
            """, values)
            for node in target_nodes:
                conn.execute("""
                    INSERT OR IGNORE INTO resource_grant_nodes
                    (grant_code, request_code, linux_username, pool_id, node, sshuser_path,
                     access_existed_before, access_check_status, grant_status, revoke_status,
                     created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, 0, 'unchecked', 'planned', 'not_due', ?, ?)
                """, (grant_code, request_code, linux_username, pool_id, node, sshuser_path, now, now))
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
                SELECT grant_code, request_code, linux_username, pool_id, target_nodes, sshuser_path, valid_from,
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


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _ensure_column(conn, table_name: str, column_name: str, definition: str) -> None:
    rows = conn.execute(f"PRAGMA table_info({table_name})").fetchall()
    if column_name not in {row[1] for row in rows}:
        conn.execute(f"ALTER TABLE {table_name} ADD COLUMN {column_name} {definition}")


def _table_has_column(conn, table_name: str, column_name: str) -> bool:
    rows = conn.execute(f"PRAGMA table_info({table_name})").fetchall()
    return column_name in {row[1] for row in rows}


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
        grant_code=row[0], request_code=row[1], linux_username=row[2], pool_id=row[3],
        target_nodes=json.loads(row[4] or "[]"), sshuser_path=row[5] or "/public/bin/sshuser",
        valid_from=row[6], valid_until=row[7], status=row[8], planned_by=row[9], confirmed_by=row[10] or "",
        last_error=row[11] or "",
    )


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
