"""Owner approval command parsing and resource workflow message formatting."""
import re
from dataclasses import dataclass
from typing import List, Optional

from resource_request_store import ResourceRequestRecord


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


RESOURCE_OWNER_COMMANDS = ("/approve", "/reject", "/grant", "/revoke")


def is_resource_owner_command(content: str) -> bool:
    text = (content or "").strip().lower()
    return any(text.startswith(command) for command in RESOURCE_OWNER_COMMANDS)


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


def format_owner_request_notification(request: ResourceRequestRecord, pool_name: str, free_devices: Optional[int]) -> str:
    free_text = "未知" if free_devices is None else str(free_devices)
    reasons = "；".join(request.priority_reasons) if request.priority_reasons else "无"
    return (
        f"🧾 新资源申请 #{request.request_code}\n"
        f"申请人：{request.linux_username}（{request.feishu_user_id}）\n"
        f"项目：{request.project_name}\n"
        f"资源：{request.resource_type} x {request.resource_amount}，{request.duration_hours} 小时\n"
        f"紧急程度：{request.urgency}\n"
        f"截止时间：{request.deadline or '未填写'}\n"
        f"用途：{request.reason or '未填写'}\n"
        f"匹配资源池：{pool_name}（{request.matched_pool_id}），当前空闲：{free_text}\n"
        f"优先级评分：{request.priority_score}\n"
        f"评分原因：{reasons}\n\n"
        f"处理命令：\n"
        f"/approve {request.request_code} {request.duration_hours}h\n"
        f"/reject {request.request_code} 原因"
    )


def format_user_request_received(request_code: str, matched_pool_id: str, priority_score: int) -> str:
    return (
        f"已收到资源申请 #{request_code}。\n"
        f"系统已匹配资源池：{matched_pool_id}\n"
        f"当前优先级评分：{priority_score}\n"
        f"我会通知运维审批，审批后再反馈授权建议。"
    )


def format_missing_fields_prompt(missing_fields) -> str:
    missing = "、".join(missing_fields)
    return (
        f"资源申请信息不完整，缺少：{missing}\n"
        f"请按格式发送：\n"
        f"/apply\n"
        f"Linux账号：zhangsan\n"
        f"资源类型：K100\n"
        f"数量：4卡\n"
        f"使用时长：72小时\n"
        f"紧急程度：P1\n"
        f"项目：项目名称\n"
        f"用途：任务说明"
    )


def format_phase1_grant_advice(request_code: str, linux_username: str, pool_id: str, target_nodes: List[str], sshuser_path: str, duration_hours: int) -> str:
    add_commands = "\n".join(f"{node}: {sshuser_path} add {linux_username}" for node in target_nodes)
    del_commands = "\n".join(f"{node}: {sshuser_path} del {linux_username}" for node in target_nodes)
    nodes = "、".join(target_nodes)
    return (
        f"✅ 申请 #{request_code} 已审批。\n"
        f"Phase 1 安全模式：不会自动执行节点命令。\n"
        f"建议资源池：{pool_id}\n"
        f"目标节点：{nodes}\n"
        f"建议授权时长：{duration_hours} 小时\n"
        f"建议手动授权命令：\n"
        f"{add_commands}\n"
        f"到期后请手动撤销命令：\n"
        f"{del_commands}\n"
        f"撤销前请确认该用户不是节点上的既有长期权限用户。"
    )


def _parse_duration(text: str) -> int:
    match = re.search(r"\d+", text or "")
    return int(match.group(0)) if match else 0
