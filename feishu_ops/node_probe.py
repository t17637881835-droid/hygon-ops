"""Safe local node probing helpers."""
import platform
import subprocess
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable, List


@dataclass(frozen=True)
class LocalProbeResult:
    node: str
    success: bool
    output: str
    error: str = ""


class LocalNodeProbe:
    def __init__(self, command_runner: Callable = None, timeout_seconds: int = 5):
        self.command_runner = command_runner or subprocess.run
        self.timeout_seconds = timeout_seconds

    def ping(self, node: str = "local") -> LocalProbeResult:
        if _normalize_node(node) != "local":
            return LocalProbeResult(node=node, success=False, output="", error="only local node is supported")
        return LocalProbeResult(
            node="local",
            success=True,
            output=(
                "pong\n"
                f"node: local\n"
                f"hostname: {platform.node()}\n"
                f"time: {datetime.now(timezone.utc).isoformat()}"
            ),
        )

    def status(self, node: str = "local") -> LocalProbeResult:
        if _normalize_node(node) != "local":
            return LocalProbeResult(node=node, success=False, output="", error="only local node is supported")
        commands = [
            ("hostname", ["hostname"]),
            ("date", ["date", "-Is"]),
            ("uptime", ["uptime"]),
        ]
        lines: List[str] = ["node: local"]
        for label, command in commands:
            result = self._run(command)
            if not result.success:
                return result
            lines.append(f"{label}: {result.output.strip()}")
        return LocalProbeResult(node="local", success=True, output="\n".join(lines))

    def _run(self, command: List[str]) -> LocalProbeResult:
        try:
            completed = self.command_runner(
                command,
                capture_output=True,
                text=True,
                timeout=self.timeout_seconds,
                check=False,
            )
            if completed.returncode != 0:
                return LocalProbeResult(
                    node="local",
                    success=False,
                    output=completed.stdout or "",
                    error=(completed.stderr or f"exit_code={completed.returncode}").strip(),
                )
            return LocalProbeResult(node="local", success=True, output=completed.stdout or "")
        except subprocess.TimeoutExpired:
            return LocalProbeResult(node="local", success=False, output="", error="command timed out")


def format_probe_result(result: LocalProbeResult) -> str:
    if result.success:
        return "✅ 节点握手成功\n" + result.output
    return f"❌ 节点握手失败\nnode: {result.node}\nerror: {result.error}"


def _normalize_node(value: str) -> str:
    return (value or "local").strip().lower()
