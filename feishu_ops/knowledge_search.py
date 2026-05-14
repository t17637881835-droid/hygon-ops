"""知识库搜索服务（统一检索入口）"""
from pathlib import Path
from typing import List, Dict, Optional

from knowledge_retriever import create_retriever, SearchResult


def _resolve_kb_path(base_path: str) -> str:
    """把 base_path 解析为真实存在的知识库目录。
    优先级：1) 绝对路径直接用；2) cwd 下；3) 项目内 skills/haiguang-ops/knowledge_base。
    """
    here = Path(__file__).resolve().parent
    fallback = here.parent / "skills" / "haiguang-ops" / "knowledge_base"

    candidates = []
    if base_path:
        p = Path(base_path)
        if p.is_absolute():
            candidates.append(p)
        else:
            stripped = base_path.lstrip("./")
            candidates.append(Path.cwd() / stripped)
            candidates.append(here.parent / stripped)
            candidates.append(here / stripped)
    candidates.append(fallback)

    for c in candidates:
        if (c / "faq.json").exists() or (c / "faq").exists() or (c / "docs").exists():
            return str(c)
    return str(fallback)


class KnowledgeSearchService:
    """
    统一知识库检索服务
    自动选择后端：RAGFlow（已配置时）> Local（默认）
    """

    def __init__(self, base_path: str = ""):
        self.base_path = _resolve_kb_path(base_path)
        self.retriever = create_retriever(base_path=self.base_path)
        self._log_retriever_type()

    def _log_retriever_type(self):
        try:
            healthy, status = self.retriever.health_check()
            print(f"[知识检索] 后端: {status} | 健康: {healthy}")
        except Exception:
            print("[知识检索] 后端: local (回退)")

    def search(self, question: str, limit: int = 5) -> List[Dict]:
        """
        搜索知识库，返回兼容格式
        当前返回格式兼容 SkillInvoker 的 _build_kb_context 预期
        """
        results = self.retriever.search(question, top_k=limit)
        return [r.to_dict() for r in results]

    def search_with_scores(self, question: str, limit: int = 5) -> List[SearchResult]:
        """带置信度信息的原始检索结果"""
        return self.retriever.search(question, top_k=limit)

    def get_by_category(self, category: str) -> List[Dict]:
        """按分类获取 FAQ"""
        results = self.retriever.search("", top_k=100, category=category)
        return [r.to_dict() for r in results if r.category == category]

    def get_categories(self) -> List[str]:
        return self.retriever.get_categories()

    def health_check(self) -> Dict:
        """检索服务健康状态"""
        healthy, status = self.retriever.health_check()
        return {"available": healthy, "backend": status}
