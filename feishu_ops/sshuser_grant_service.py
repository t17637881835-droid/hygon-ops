"""Business service for confirmed sshuser grants and revocations."""
from dataclasses import dataclass
from datetime import datetime, timezone
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
                    covered = self.store.has_active_system_grant_for_node(
                        username,
                        node,
                        exclude_grant_code=grant_code,
                        now_iso=datetime.now(timezone.utc).isoformat(),
                    )
                    if covered:
                        self.store.update_grant_node_grant_result(grant_code, node, False, "present", "covered_by_active_grant", "")
                    else:
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
                if self.store.has_active_system_grant_for_node(username, node, grant_code, datetime.now(timezone.utc).isoformat()):
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
