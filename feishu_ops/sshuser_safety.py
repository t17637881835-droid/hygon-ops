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
