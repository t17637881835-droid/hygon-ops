"""调用 Anthropic API 处理运维问题"""
import os
import time
from typing import Dict, Optional
from dataclasses import dataclass
import json

try:
    from anthropic import Anthropic
except ImportError:
    Anthropic = None

from config import get_config
from knowledge_search import KnowledgeSearchService
from intent_classifier import IntentClassifier, IntentType
from query_rewriter import QueryRewriter

# 重试配置
MAX_RETRIES = 3
RETRY_DELAY = 1.0  # 秒

# 熔断器配置
CIRCUIT_BREAKER_THRESHOLD = 5  # 连续失败次数阈值
CIRCUIT_BREAKER_RESET_TIME = 60  # 熔断器恢复时间（秒）


@dataclass
class InvocationResult:
    success: bool
    response: Optional[str] = None
    error: Optional[str] = None
    from_cache: bool = False


class CircuitBreaker:
    """简单的熔断器实现"""

    def __init__(self, threshold: int = CIRCUIT_BREAKER_THRESHOLD, reset_time: int = CIRCUIT_BREAKER_RESET_TIME):
        self.threshold = threshold
        self.reset_time = reset_time
        self.failures = 0
        self.last_failure_time: Optional[float] = None
        self.opened = False

    def record_failure(self):
        self.failures += 1
        self.last_failure_time = time.time()
        if self.failures >= self.threshold:
            self.opened = True

    def record_success(self):
        self.failures = 0
        self.opened = False

    def can_attempt(self) -> bool:
        if not self.opened:
            return True
        if self.last_failure_time and (time.time() - self.last_failure_time) > self.reset_time:
            self.opened = False
            self.failures = 0
            return True
        return False


class SkillInvoker:
    def __init__(
        self,
        skill_name: str = "haiguang-ops",
        knowledge_base_path: Optional[str] = None,
        knowledge_search: Optional[KnowledgeSearchService] = None,
    ):
        self.skill_name = skill_name
        self.config = get_config()

        # 动态计算 skill 路径：feishu_ops/ 与 skills/ 是同级目录
        self.skill_path = os.path.abspath(
            os.path.join(os.path.dirname(__file__), "..", "skills", skill_name)
        )

        # 优先使用外部注入的 KnowledgeSearchService，避免重复初始化向量模型
        if knowledge_search is not None:
            self.knowledge_search = knowledge_search
        else:
            kb_path = knowledge_base_path or self.config.skill.knowledge_base_path
            if kb_path.startswith("./"):
                kb_path = os.path.join(self.skill_path, kb_path.lstrip("./"))
            self.knowledge_search = KnowledgeSearchService(kb_path)

        # 初始化 Anthropic 客户端
        self.client = None
        self._init_anthropic_client()

        # 熔断器
        self.circuit_breaker = CircuitBreaker()

        # 意图分类器
        self.intent_classifier = IntentClassifier()

        # 查询改写器
        self.query_rewriter = QueryRewriter()

    def _init_anthropic_client(self):
        if Anthropic is None:
            return
        api_key = self.config.anthropic.api_key
        if not api_key:
            return
        self.client = Anthropic(api_key=api_key)

    def invoke(self, question: str, context: Optional[Dict] = None) -> Dict:
        """调用 skill 处理问题，带重试和熔断"""
        if not self.circuit_breaker.can_attempt():
            return {
                "success": False,
                "error": "服务暂时不可用（熔断器开启），请稍后重试"
            }

        # 0. 意图分类：非问答类直接返回
        intent_result = self.intent_classifier.classify(question)
        if intent_result["intent"] != IntentType.QUESTION:
            return {
                "success": True,
                "response": self._get_non_question_response(intent_result["intent"]),
                "intent": intent_result["intent"].value,
                "intent_reason": intent_result["reason"],
            }

        # 0.5 查询改写：口语化表达转规范问题
        rewrite_result = self.query_rewriter.rewrite_with_explanation(question)
        query_to_search = rewrite_result["rewritten"]

        # 1. 先从知识库检索相关内容
        kb_results = self.knowledge_search.search(query_to_search, limit=3)
        top = kb_results[0] if kb_results else {}
        top_score = float(top.get("score", 0.0) or 0.0)
        top_id = top.get("id", "") if kb_results else ""

        if not self._has_confident_result(kb_results):
            return {
                "success": True,
                "response": self.config.skill.busy_reply,
                "low_confidence": True,
                "score": top_score,
                "top_id": top_id,
            }

        # 1.5 高置信度快路：FAQ 打分足够高时格式化输出，不调 LLM
        if top_score >= self.config.skill.high_confidence_score:
            solution = top.get("solution", "").strip()
            if solution:
                response = self._format_kb_response(question, top)
                return {
                    "success": True,
                    "response": response,
                    "from_kb": True,
                    "score": top_score,
                    "top_id": top_id,
                }

        # 2. 中等置信度：调 LLM 润色
        kb_context = self._build_kb_context(kb_results)
        prompt = self._build_prompt(question, kb_context, context)

        # 3. 调用 API，带重试
        last_error = None
        for attempt in range(MAX_RETRIES):
            try:
                response = self._call_anthropic(prompt)
                self.circuit_breaker.record_success()
                return {
                    "success": True,
                    "response": response,
                    "from_llm": True,
                    "score": top_score,
                    "top_id": top_id,
                }
            except Exception as e:
                last_error = str(e)
                if attempt < MAX_RETRIES - 1:
                    time.sleep(RETRY_DELAY * (attempt + 1))

        self.circuit_breaker.record_failure()
        return {"success": False, "error": last_error, "score": top_score, "top_id": top_id}

    def _call_anthropic(self, prompt: str) -> str:
        """调用 Anthropic API"""
        if self.client is None:
            raise RuntimeError("Anthropic 客户端未初始化，请设置 ANTHROPIC_API_KEY 环境变量")

        resp = self.client.messages.create(
            model=self.config.anthropic.model,
            max_tokens=self.config.anthropic.max_tokens,
            messages=[{"role": "user", "content": prompt}]
        )
        return resp.content[0].text

    def _build_kb_context(self, kb_results: list) -> str:
        """构建知识库上下文"""
        if not kb_results:
            return "（知识库中未找到相关内容）"

        lines = ["参考知识库内容："]
        for i, item in enumerate(kb_results, 1):
            lines.append(f"\n{i}. 【{item.get('category', 'unknown')}】{item.get('question', '')}")
            lines.append(f"   解决方案：{item.get('solution', '')}")
        return "\n".join(lines)

    def _has_confident_result(self, kb_results: list) -> bool:
        if not kb_results:
            return False
        top_score = float(kb_results[0].get("score", 0.0) or 0.0)
        return top_score >= self.config.skill.min_confidence_score

    def _format_kb_response(self, question: str, faq_item: Dict) -> str:
        """高置信度 FAQ 格式化输出（激活 ResponseGenerator 模板逻辑）"""
        solution = faq_item.get("solution", "").strip()
        category = faq_item.get("category", "")
        faq_question = faq_item.get("question", "")
        lines = []
        lines.append(f"关于「{faq_question}」：\n")
        lines.append(solution)
        lines.append("\n如果仍未解决，请联系运维人员。")
        return "\n".join(lines)

    def _get_non_question_response(self, intent: IntentType) -> str:
        """非问答类意图的固定回复"""
        if intent == IntentType.CHAT:
            return "您好，我是运维助手，专注于海光 DCU 运维问题解答。如有运维相关问题，请随时提问。"
        elif intent == IntentType.COMMAND:
            return "收到您的请求，请联系运维人员处理（当前仅支持知识库问答，暂不支持直接执行操作）。"
        else:
            return "抱歉，我无法理解您的请求，请重新描述您的运维问题。"

    def _build_prompt(self, question: str, kb_context: str, context: Optional[Dict]) -> str:
        return f"""你是一个海光 DCU 运维助手。请根据知识库回答用户的运维问题。

{kb_context}

用户问题：{question}

回复要求：
- 如果知识库有相关内容：直接给出解决方案
- 如果需要更多信息：给出引导检查步骤
- 如果无法回答：返回"转人工处理"

请用简洁专业的语气回复："""
