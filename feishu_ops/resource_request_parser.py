"""Parse resource request messages from Feishu private chat."""
import re
from dataclasses import dataclass, field
from typing import Dict, List


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
