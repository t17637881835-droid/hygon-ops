"""飞书事件解析"""
import json
import re
from dataclasses import dataclass
from typing import Dict, Optional, Set


@dataclass
class ParsedFeishuEvent:
    action: str
    message_id: str
    user_id: str
    chat_id: str
    content: str
    event_type: str = ""
    reason: str = ""
    token: str = ""
    open_message_id: str = ""
    short_id: int = 0       # owner 指令中的短序号
    urgent: bool = False    # 是否包含紧急关键词


OWNER_CANCEL_PREFIX = "取消 "
URGENT_KEYWORDS = (
    "紧急", "特急", "十火急", "线上挂", "宕机", "崩了", "故障",
    "线上事故", "P0", "p0", "asap", "ASAP", "马上", "立马",
)
FORWARD_PATTERN = re.compile(r"^/reply\s+(\d+)\s+(.+)$", re.DOTALL)
SKIP_PATTERN = re.compile(r"^/skip\s+(\d+)\s*$")
AUTO_PATTERN = re.compile(r"^/auto\s+(\d+)\s*$")
RESOURCE_OWNER_PATTERN = re.compile(r"^/(approve|reject|grant)\s+R\d+(\s+.*)?$", re.IGNORECASE | re.DOTALL)
NODE_OWNER_PATTERN = re.compile(r"^\s*@[^/]*\s*/node\s+(ping|status)\s*(\S+)?\s*$", re.IGNORECASE)
KB_OWNER_PATTERN = re.compile(r"^\s*(?:@\S+\s+)?/kb(?:\s+.*)?$", re.IGNORECASE | re.DOTALL)


def _is_urgent(content: str) -> bool:
    return any(kw in content for kw in URGENT_KEYWORDS)


def parse_feishu_event(
    payload: Dict,
    owner_user_ids: Optional[Set[str]] = None,
    bot_user_ids: Optional[Set[str]] = None,
) -> ParsedFeishuEvent:
    owner_user_ids = owner_user_ids or set()
    bot_user_ids = bot_user_ids or set()

    if "challenge" in payload:
        return ParsedFeishuEvent(action="challenge", content=str(payload.get("challenge", "")))

    header = payload.get("header", {}) or {}
    event_type = header.get("event_type", payload.get("type", ""))
    if event_type and event_type != "im.message.receive_v1" and event_type != "card.action.trigger":
        return ParsedFeishuEvent(action="ignore", message_id="", user_id="", chat_id="", content="", event_type=event_type, reason="unsupported_event_type")

    event = payload.get("event", {}) or {}

    # 处理卡片交互事件
    if event_type == "card.action.trigger":
        operator = event.get("operator", {}) or {}
        action = event.get("action", {}) or {}
        action_value = action.get("value", {})
        form_value = action.get("form_value", {})
        context = event.get("context", {}) or {}
        token = event.get("token", "")
        # 将 action_value 和 form_value 合并
        combined_value = {**action_value, **form_value}
        return ParsedFeishuEvent(
            action="card_interaction",
            message_id=header.get("event_id", ""),
            user_id=operator.get("open_id", ""),
            chat_id=context.get("open_chat_id", operator.get("open_id", "")),
            content=json.dumps(combined_value),
            event_type=event_type,
            reason="card_button_click",
            token=token,
            open_message_id=context.get("open_message_id", ""),
        )

    sender = event.get("sender", {}) or {}
    sender_id = sender.get("sender_id", {}) or {}
    user_id = sender_id.get("user_id") or sender_id.get("open_id") or sender.get("sender_id", "")

    message = event.get("message", {}) or {}
    message_id = message.get("message_id", "")
    chat_id = message.get("chat_id", "")
    message_type = message.get("message_type", "")
    content = _extract_text(message.get("content", ""), message_type)

    if user_id in bot_user_ids:
        return ParsedFeishuEvent(action="ignore", message_id=message_id, user_id=user_id, chat_id=chat_id, content=content, event_type=event_type, reason="bot_message")

    if user_id in owner_user_ids:
        # 私聊指令：/reply N 内容  → 转发内容到短序号 N 对应的会话
        m = FORWARD_PATTERN.match(content.strip())
        if m:
            return ParsedFeishuEvent(
                action="forward", message_id=message_id, user_id=user_id, chat_id=chat_id,
                content=m.group(2).strip(), event_type=event_type,
                reason="owner_forward_command", short_id=int(m.group(1)),
            )
        # 私聊指令：/skip N  → 取消指定短序号的自动回复
        m = SKIP_PATTERN.match(content.strip())
        if m:
            return ParsedFeishuEvent(
                action="cancel_by_short_id", message_id=message_id, user_id=user_id, chat_id=chat_id,
                content=content, event_type=event_type,
                reason="owner_skip_command", short_id=int(m.group(1)),
            )
        # 私聊指令：/auto N  → 立即触发短序号 N 的自动回复
        m = AUTO_PATTERN.match(content.strip())
        if m:
            return ParsedFeishuEvent(
                action="trigger_auto", message_id=message_id, user_id=user_id, chat_id=chat_id,
                content=content, event_type=event_type,
                reason="owner_auto_command", short_id=int(m.group(1)),
            )
        if RESOURCE_OWNER_PATTERN.match(content.strip()):
            return ParsedFeishuEvent(
                action="resource_owner_command", message_id=message_id, user_id=user_id, chat_id=chat_id,
                content=content.strip(), event_type=event_type, reason="resource_owner_command",
            )
        # 支持 @xxx /ping 格式（飞书 @机器人时会把 @ 信息包含在消息内容里）
        if re.search(r"^\s*@[^/]*\s*/ping\s*$", content, re.IGNORECASE):
            return ParsedFeishuEvent(
                action="ping", message_id=message_id, user_id=user_id, chat_id=chat_id,
                content=content.strip(), event_type=event_type, reason="owner_ping_command",
            )
        if NODE_OWNER_PATTERN.match(content.strip()):
            return ParsedFeishuEvent(
                action="node_owner_command", message_id=message_id, user_id=user_id, chat_id=chat_id,
                content=content.strip(), event_type=event_type, reason="node_owner_command",
            )
        if KB_OWNER_PATTERN.match(content.strip()):
            return ParsedFeishuEvent(
                action="kb_owner_command", message_id=message_id, user_id=user_id, chat_id=chat_id,
                content=content.strip(), event_type=event_type, reason="kb_owner_command",
            )
        # 支持 @xxx /apply_card 格式
        if re.search(r"^\s*(?:@\S+\s*)?(?:/?apply_card|申请节点)\s*$", content, re.IGNORECASE):
            return ParsedFeishuEvent(
                action="apply_card", message_id=message_id, user_id=user_id, chat_id=chat_id,
                content=content.strip(), event_type=event_type, reason="apply_card_command",
            )
        # 兼容：“取消 {target_chat_id}”指令取消指定会话的自动回复
        if content.startswith(OWNER_CANCEL_PREFIX):
            target_chat_id = content[len(OWNER_CANCEL_PREFIX):].strip()
            if target_chat_id:
                return ParsedFeishuEvent(action="cancel", message_id=message_id, user_id=user_id, chat_id=target_chat_id, content=content, event_type=event_type, reason="owner_cancel_command")
        # 群聊场景：owner 在同一个群中回复即取消该会话的自动回复
        return ParsedFeishuEvent(action="cancel", message_id=message_id, user_id=user_id, chat_id=chat_id, content=content, event_type=event_type, reason="owner_replied")

    if not message_id or not chat_id or not content:
        return ParsedFeishuEvent(action="ignore", message_id=message_id, user_id=user_id, chat_id=chat_id, content=content, event_type=event_type, reason="empty_message")

    # 普通用户也可以使用 /apply_card 命令
    if re.search(r"^\s*(?:@\S+\s*)?(?:/?apply_card|申请节点)\s*$", content, re.IGNORECASE):
        return ParsedFeishuEvent(
            action="apply_card", message_id=message_id, user_id=user_id, chat_id=chat_id,
            content=content.strip(), event_type=event_type, reason="apply_card_command",
        )

    return ParsedFeishuEvent(
        action="enqueue", message_id=message_id, user_id=user_id, chat_id=chat_id,
        content=content, event_type=event_type, urgent=_is_urgent(content),
    )


def _extract_text(raw_content: str, message_type: str) -> str:
    if not raw_content:
        return ""
    if message_type and message_type != "text":
        return ""
    try:
        data = json.loads(raw_content)
        text = data.get("text", "")
        if isinstance(text, str):
            return text.strip()
    except Exception:
        return raw_content.strip()
    return ""
