"""
knowledge_retriever.py — 知识检索抽象层
职责：统一封装知识库检索入口，支持多后端切换。
当前默认后端：LocalSearch（本地 JSON + 关键词 + 向量混合）
待接入后端：RAGFlowSearch（对接 rag_v14 检索逻辑）

切换方式：设置环境变量 KNOWLEDGE_RETRIEVER_TYPE=ragflow
"""

import os
import time
from abc import ABC, abstractmethod
from typing import List, Dict, Optional, Tuple
from dataclasses import dataclass


@dataclass
class SearchResult:
    """统一检索结果格式"""
    id: str
    question: str
    solution: str
    category: str
    keywords: List[str]
    combined_score: float = 0.0
    term_score: float = 0.0
    vector_score: float = 0.0
    source: str = "local"  # "local" | "ragflow"
    document: str = ""     # 来源文档名
    is_penalized: bool = False
    is_boosted: bool = False

    def to_dict(self) -> Dict:
        return {
            "id": self.id,
            "question": self.question,
            "solution": self.solution,
            "category": self.category,
            "keywords": self.keywords,
            "score": self.combined_score,
            "term_score": self.term_score,
            "vector_score": self.vector_score,
            "source": self.source,
            "document": self.document,
        }


class BaseRetriever(ABC):
    """检索后端抽象基类"""

    @abstractmethod
    def search(self, query: str, top_k: int = 5, category: str = "") -> List[SearchResult]:
        """检索知识库，返回统一格式结果"""
        pass

    @abstractmethod
    def get_categories(self) -> List[str]:
        """获取所有分类"""
        pass

    @abstractmethod
    def health_check(self) -> Tuple[bool, str]:
        """健康检查，返回 (是否健康, 状态描述)"""
        pass


class LocalRetriever(BaseRetriever):
    """
    本地知识库检索（当前默认后端）
    功能：jieba 分词 + 同义词展开 + 关键词匹配 + 向量相似度（可选）
    降级：sentence-transformers 不可用时，纯关键词兜底
    """

    def __init__(self, base_path: str):
        self.source = "local"
        self._init_backend(base_path)

    def _init_backend(self, base_path: str):
        """延迟初始化后端，失败不影响主流程"""
        from pathlib import Path
        import sys

        skill_tools_path = Path(__file__).parent.parent / "skills" / "haiguang-ops" / "tools"
        sys.path.insert(0, str(skill_tools_path))

        try:
            from vector_search import HybridSearch
            self._backend = HybridSearch(base_path)
            self._backend_type = "hybrid"
        except Exception:
            from search_knowledge import KnowledgeSearch
            self._backend = KnowledgeSearch(base_path)
            self._backend_type = "keyword"

    def search(self, query: str, top_k: int = 5, category: str = "") -> List[SearchResult]:
        if not hasattr(self, "_backend"):
            return []

        try:
            if self._backend_type == "hybrid":
                raw_results = self._backend.search(query, top_k)
            else:
                raw_results = self._backend.search_by_question(query, top_k)

            results = []
            for item in raw_results:
                results.append(SearchResult(
                    id=item.get("id", ""),
                    question=item.get("question", ""),
                    solution=item.get("solution", ""),
                    category=item.get("category", ""),
                    keywords=item.get("keywords", []),
                    combined_score=item.get("combined_score", item.get("score", 0.0)),
                    term_score=item.get("keyword_score", 0.0),
                    vector_score=item.get("vector_score", 0.0),
                    source="local",
                    document=item.get("category", ""),
                    is_penalized=item.get("_penalized", False),
                    is_boosted=item.get("_boosted", False),
                ))

            # 按分类过滤
            if category:
                results = [r for r in results if r.category == category]

            return results[:top_k]
        except Exception:
            return []

    def get_categories(self) -> List[str]:
        if hasattr(self, "_backend") and hasattr(self._backend, "vector_store"):
            return self._backend.vector_store.faq_items and list(
                set(item.get("category", "") for item in self._backend.vector_store.faq_items)
            ) or []
        return []

    def health_check(self) -> Tuple[bool, str]:
        if not hasattr(self, "_backend"):
            return False, "后端未初始化"
        backend_type = getattr(self, "_backend_type", "unknown")
        return True, f"local/{backend_type}"


class RAGFlowRetriever(BaseRetriever):
    """
    RAGFlow 检索后端（待接入）
    对接 rag_v14 检索逻辑，支持 bug_list / perf_table / tech_docs 分区路由
    当前为占位实现，环境变量配置完成后自动切换
    """

    def __init__(self, base_path: str = ""):
        self.source = "ragflow"
        self._config = self._load_ragflow_config()
        self._available = bool(self._config.get("api_url") and self._config.get("api_token"))

    def _load_ragflow_config(self) -> Dict:
        """从环境变量加载 RAGFlow 配置（复用 rag_v14 的配置方式）"""
        import os
        return {
            "api_url": os.environ.get("RAGFLOW_API_URL", ""),
            "api_token": os.environ.get("RAGFLOW_API_TOKEN", ""),
            "timeout": int(os.environ.get("RAGFLOW_TIMEOUT", "15")),
            "rerank_id": os.environ.get("RAGFLOW_RERANK_ID", ""),
            # 运维知识库 dataset_id（待配置）
            "dataset_id": os.environ.get("RAGFLOW_OPS_DATASET_ID", ""),
            # 各分区 dataset_id（预留扩展）
            "kb_router": {
                "bug_list":   os.environ.get("RAGFLOW_KB_BUG", ""),
                "perf_table": os.environ.get("RAGFLOW_KB_TABLE", ""),
                "tech_docs":   os.environ.get("RAGFLOW_KB_DOCS", ""),
                "ops_knowledge": os.environ.get("RAGFLOW_OPS_DATASET_ID", ""),
            }
        }

    def search(self, query: str, top_k: int = 5, category: str = "") -> List[SearchResult]:
        """RAGFlow 检索（待实现）"""
        if not self._available:
            return []

        # TODO: 对接 rag_v14 scripts/retriever.py 的 retrieve() 函数
        # 实现思路：
        #   1. 根据 category 路由到对应 dataset_id
        #   2. 调用 RAGFlow /api/v1/retrieval
        #   3. 复用 rag_v14 的评分逻辑（_compute_score / _apply_penalty / _apply_source_boost）
        #   4. 转换为统一 SearchResult 格式返回
        raise NotImplementedError("RAGFlow Retriever 待接入，请配置 RAGFLOW_API_URL 等环境变量")

    def get_categories(self) -> List[str]:
        return ["bug_list", "perf_table", "tech_docs", "ops_knowledge"]

    def health_check(self) -> Tuple[bool, str]:
        if not self._available:
            return False, "RAGFlow 未配置（RAGFLOW_API_URL / RAGFLOW_API_TOKEN 未设置）"
        # TODO: 实际调用 RAGFlow 健康检查接口
        return True, "ragflow/placeholder"


# ── 工厂函数：按类型创建检索后端 ─────────────────────────────────

_RETRIEVERS = {
    "local": LocalRetriever,
    "ragflow": RAGFlowRetriever,
}


def create_retriever(retriever_type: str = "", base_path: str = "") -> BaseRetriever:
    """
    创建知识检索后端
    优先级：
      1. 环境变量 KNOWLEDGE_RETRIEVER_TYPE 指定类型
      2. RAGFlow 已配置 → 自动使用 RAGFlowRetriever
      3. 默认 → LocalRetriever
    """
    if not retriever_type:
        retriever_type = os.environ.get("KNOWLEDGE_RETRIEVER_TYPE", "")

    # 自动检测：如果 RAGFlow 已配置且未显式指定 local，优先用 RAGFlow
    if not retriever_type:
        ragflow = RAGFlowRetriever("")
        if ragflow._available:
            retriever_type = "ragflow"
        else:
            retriever_type = "local"

    cls = _RETRIEVERS.get(retriever_type, LocalRetriever)
    return cls(base_path)
