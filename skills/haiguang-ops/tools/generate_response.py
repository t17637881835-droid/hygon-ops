"""回复生成工具"""
from typing import Dict, List, Optional

class ResponseGenerator:
    DIRECT_REPLY_TEMPLATE = """您好，根据您描述的问题「{question}」，可能是由于{reason}导致的。

解决方法：
{solution}

如果仍未解决，请联系运维人员。"""

    GUIDE_TEMPLATE = """您好，为了更快定位问题，请先检查：
{checks}

请将检查结果告诉我，我会进一步帮您处理。"""

    ESCALATE_TEMPLATE = """您好，您的问题已转交给运维人员处理，
请稍候，运维人员会尽快回复您。"""

    def generate_direct_reply(self, question: str, faq_item: Dict) -> str:
        """生成直接回复"""
        return self.DIRECT_REPLY_TEMPLATE.format(
            question=question,
            reason=faq_item.get("possible_reasons", "未知原因"),
            solution=faq_item.get("solution", "")
        )

    def generate_guide(self, checks: List[str]) -> str:
        """生成引导诊断回复"""
        checks_text = "\n".join(f"{i+1}. {check}" for i, check in enumerate(checks))
        return self.GUIDE_TEMPLATE.format(checks=checks_text)

    def generate_escalate(self) -> str:
        """生成转人工回复"""
        return self.ESCALATE_TEMPLATE

    def judge_complexity(self, faq_item: Dict, score: float) -> str:
        """判断问题复杂度
        - score >= 2: 简单问题，直接回复
        - score >= 1: 复杂问题，引导诊断
        - score < 1: 转人工
        """
        if score >= 2:
            return "direct"
        elif score >= 1:
            return "guide"
        else:
            return "escalate"