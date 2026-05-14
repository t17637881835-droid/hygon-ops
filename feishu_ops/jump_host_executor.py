"""Jump-host based implementation of structured sshuser execution."""
import re
import shlex
import subprocess
from datetime import datetime, timezone

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
        started = datetime.now(timezone.utc)
        command = self.build_command(node, operation, linux_username, sshuser_path)
        try:
            completed = subprocess.run(command, capture_output=True, text=True, timeout=self.command_timeout_seconds, check=False)
            finished = datetime.now(timezone.utc)
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
            finished = datetime.now(timezone.utc)
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
