"""Structured executor interfaces for sshuser access checks and mutations."""
from dataclasses import dataclass
from datetime import datetime, timezone
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
        now = datetime.now(timezone.utc).isoformat()
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
