"""Transparent resource request priority scoring."""
from dataclasses import dataclass
from typing import List


@dataclass(frozen=True)
class PriorityScore:
    score: int
    reasons: List[str]


def score_resource_request(
    urgency: str,
    deadline: str,
    reason: str,
    pool_can_satisfy: bool,
    pool_is_tight: bool,
    accept_queue: bool,
    accept_downgrade: bool,
) -> PriorityScore:
    total = 0
    reasons: List[str] = []

    normalized_urgency = (urgency or "").upper()
    urgency_points = {"P0": 100, "P1": 70, "P2": 40, "P3": 10}.get(normalized_urgency, 0)
    if urgency_points:
        total += urgency_points
        reasons.append(f"{normalized_urgency}: +{urgency_points}")

    if deadline:
        total += 40
        reasons.append("存在截止时间: +40")

    reason_text = reason or ""
    if any(keyword in reason_text for keyword in ("线上", "故障", "事故", "宕机")):
        total += 50
        reasons.append("生产故障/事故: +50")
    elif any(keyword in reason_text for keyword in ("客户", "交付", "验收")):
        total += 30
        reasons.append("客户交付/验收: +30")
    elif any(keyword in reason_text for keyword in ("测试", "验证")):
        total += 10
        reasons.append("内部测试: +10")

    if pool_can_satisfy:
        total += 10
        reasons.append("资源池可满足: +10")
    if pool_is_tight:
        total -= 20
        reasons.append("资源池紧张: -20")
    if accept_queue:
        total += 5
        reasons.append("接受排队: +5")
    if accept_downgrade:
        total += 5
        reasons.append("接受降配: +5")

    return PriorityScore(score=total, reasons=reasons)
