"""向量检索模块 - 语义搜索增强"""
import hashlib
import json
import os
import re
import numpy as np
from pathlib import Path
from typing import List, Dict, Optional, Tuple
from threading import Lock

# 向量化库，支持多后端
SENTENCE_TRANSFORMERS_AVAILABLE = False
try:
    from sentence_transformers import SentenceTransformer
    SENTENCE_TRANSFORMERS_AVAILABLE = True
except ImportError:
    pass

try:
    import jieba
    JIEBA_AVAILABLE = True
except ImportError:
    JIEBA_AVAILABLE = False

STOPWORDS = {
    "的", "是", "在", "和", "了", "我", "你", "他", "她", "它",
    "这", "那", "有", "个", "们", "来", "去", "到", "为", "和",
    "与", "或", "但", "却", "也", "就", "都", "而", "及", "着",
    "一个", "什么", "怎么", "如何", "为什么"
}


def _split_md_by_h2(text: str) -> List[Tuple[str, str]]:
    """按 '## ' 二级标题切片，返回 [(title, body), ...]。一级 '#' 在顶部被忽略。"""
    chunks: List[Tuple[str, str]] = []
    current_title: Optional[str] = None
    current_body: List[str] = []
    for line in text.splitlines():
        m = re.match(r"^##\s+(.+?)\s*$", line)
        if m:
            if current_title is not None:
                chunks.append((current_title, "\n".join(current_body).strip()))
            current_title = m.group(1).strip()
            current_body = []
        elif current_title is not None:
            current_body.append(line)
    if current_title is not None:
        chunks.append((current_title, "\n".join(current_body).strip()))
    return chunks


def _md_keywords(text: str) -> List[str]:
    text = re.sub(r"[`*#\[\]\(\)\-_/\\\.、。，：:，]", " ", text or "")
    if JIEBA_AVAILABLE:
        words = jieba.lcut(text, cut_all=False)
    else:
        words = text.split()
    out = []
    seen = set()
    for w in words:
        w = w.strip()
        if not w or w in STOPWORDS or len(w) <= 1:
            continue
        if w in seen:
            continue
        seen.add(w)
        out.append(w)
    return out[:8]


def load_md_chunks(base_path: Path) -> List[Dict]:
    """扫描 base_path/faq/*.md 与 base_path/docs/*.md，按二级标题分片为 FAQ 条目。"""
    items: List[Dict] = []
    layouts = (
        ("faq", "faqmd", lambda stem: stem),
        ("docs", "doc", lambda stem: f"docs/{stem}"),
    )
    for sub, prefix, category_fn in layouts:
        d = base_path / sub
        if not d.is_dir():
            continue
        for md in sorted(d.glob("*.md")):
            try:
                text = md.read_text(encoding="utf-8", errors="ignore")
            except Exception:
                continue
            for i, (title, body) in enumerate(_split_md_by_h2(text), 1):
                if not title or not body:
                    continue
                question = re.sub(r"^Q\d+\s*[:：]\s*", "", title)
                items.append({
                    "id": f"{prefix}-{md.stem}-{i:02d}",
                    "question": question.strip(),
                    "solution": body.strip(),
                    "category": category_fn(md.stem),
                    "keywords": _md_keywords(question),
                    "_source_file": str(md.relative_to(base_path)),
                })
    return items


def _faq_signature(items: List[Dict]) -> str:
    """以 id+question 作为指纹，用于判定 embedding 缓存是否需要重建。"""
    payload = "|".join(f"{it.get('id','')}:{it.get('question','')}" for it in items)
    return hashlib.sha1(payload.encode("utf-8")).hexdigest()[:16]


class VectorStore:
    """知识库向量存储"""

    def __init__(self, base_path: str, model_name: str = "paraphrase-multilingual-MiniLM-L12-v2"):
        self.base_path = Path(base_path)
        self.faq_json = self.base_path / "faq.json"
        self.embedding_cache = self.base_path / ".embeddings.json"

        self.model = None
        self.faiss_index = None
        self.faq_items = []
        self.embeddings = None

        self._lock = Lock()

        # 尝试加载模型
        if SENTENCE_TRANSFORMERS_AVAILABLE:
            try:
                self.model = SentenceTransformer(model_name)
            except Exception:
                pass

        self._load_faq()

    def _load_faq(self):
        """加载 FAQ 数据：faq.json + faq/*.md + docs/*.md"""
        items: List[Dict] = []
        if self.faq_json.exists():
            with open(self.faq_json, "r", encoding="utf-8") as f:
                data = json.load(f)
                items = list(data.get("faq", []))
        items.extend(load_md_chunks(self.base_path))
        self.faq_items = items

    def _tokenize(self, text: str) -> List[str]:
        """中文分词"""
        if not text:
            return []
        if JIEBA_AVAILABLE:
            words = jieba.lcut(text, cut_all=False)
        else:
            words = text.split()
        return [w.strip() for w in words if w.strip() and w not in STOPWORDS and len(w) > 1]

    def build_index(self, force: bool = False) -> bool:
        """构建向量索引（含 faq.json + md 文档分片）"""
        if not self.model:
            return False

        if self.embeddings is not None and not force:
            return True

        with self._lock:
            signature = _faq_signature(self.faq_items)

            # 检查缓存：仅在签名匹配时复用
            if not force and self.embedding_cache.exists():
                try:
                    cache_data = json.loads(self.embedding_cache.read_text(encoding="utf-8"))
                    if cache_data.get("signature") == signature:
                        embeddings = np.array(cache_data["embeddings"])
                        if embeddings.shape[0] == len(self.faq_items):
                            self.embeddings = embeddings
                            return True
                except Exception:
                    pass

            if not self.faq_items:
                return False

            # 构建文本列表
            texts = []
            for item in self.faq_items:
                combined = f"问题: {item.get('question', '')} 关键词: {','.join(item.get('keywords', []))} 解决方案: {item.get('solution', '')}"
                texts.append(combined)

            # 批量编码
            self.embeddings = self.model.encode(texts, show_progress_bar=False)

            # 保存缓存（带签名）
            cache_data = {"signature": signature, "embeddings": self.embeddings.tolist()}
            self.embedding_cache.write_text(json.dumps(cache_data, ensure_ascii=False), encoding="utf-8")

            return True

    def search(self, query: str, top_k: int = 3) -> List[Tuple[Dict, float]]:
        """语义相似度搜索"""
        if not self.model or self.embeddings is None:
            return []

        try:
            query_embedding = self.model.encode([query])
            # 计算余弦相似度
            similarities = np.dot(self.embeddings, query_embedding[0]) / (
                np.linalg.norm(self.embeddings, axis=1) * np.linalg.norm(query_embedding[0])
            )

            # 取 top_k
            top_indices = np.argsort(similarities)[::-1][:top_k]
            results = []
            for idx in top_indices:
                if similarities[idx] > 0.3:  # 相似度阈值
                    results.append((self.faq_items[idx], float(similarities[idx])))
            return results
        except Exception:
            return []


class HybridSearch:
    """混合搜索：向量 + 关键词 + 同义词"""

    def __init__(self, base_path: str):
        self.base_path = Path(base_path)
        self.vector_store = VectorStore(base_path)
        self._load_faq()
        # 启动时主动构建向量索引，失败不阻塞（会降级成关键词分支）
        try:
            self.vector_store.build_index()
        except Exception:
            pass

        # 运维领域同义词
        self.SYNONYM_MAP = {
            "登录": ["登陆", "登入", "ssh登录", "ssh"],
            "容器": ["docker", "Docker", "容器化"],
            "GPU": ["gpu", "显卡", "nvidia"],
            "网络": ["网", "网络连接"],
            "存储": ["磁盘", "硬盘", "存储卷", "nfs"],
            "节点": ["机器", "服务器", "主机"],
            "驱动": ["驱动", "nvidia驱动"],
        }

    def _load_faq(self):
        # 复用 vector_store 加载结果（已含 faq.json + md 分片），避免重复扫盘
        self.faq_items = list(getattr(self.vector_store, "faq_items", []))

    def _keyword_score(self, query: str, item: Dict) -> float:
        """计算关键词匹配得分"""
        query_words = set(self.vector_store._tokenize(query))

        # 同义词展开
        expanded = set(query_words)
        for word in query_words:
            if word in self.SYNONYM_MAP:
                expanded.update(self.SYNONYM_MAP[word])

        item_keywords = set(item.get("keywords", []))
        question_words = set(self.vector_store._tokenize(item.get("question", "")))
        solution_words = set(self.vector_store._tokenize(item.get("solution", "")))

        score = 0
        for word in expanded:
            if word in item_keywords:
                score += 3  # 关键词命中权重高
            if word in question_words:
                score += 2
            if word in solution_words:
                score += 1
        return score

    def search(self, query: str, top_k: int = 3) -> List[Dict]:
        """混合搜索"""
        results = {}

        # 1. 向量搜索
        vector_results = self.vector_store.search(query, top_k)
        for item, sim_score in vector_results:
            item_id = item.get("id", "")
            results[item_id] = {
                **item,
                "vector_score": sim_score,
                "keyword_score": 0,
                "combined_score": sim_score * 0.7  # 向量权重 70%
            }

        # 2. 关键词搜索
        for item in self.faq_items:
            item_id = item.get("id", "")
            kw_score = self._keyword_score(query, item)
            if kw_score > 0:
                if item_id in results:
                    results[item_id]["keyword_score"] = kw_score
                    results[item_id]["combined_score"] += kw_score * 0.3  # 关键词权重 30%
                else:
                    results[item_id] = {
                        **item,
                        "vector_score": 0,
                        "keyword_score": kw_score,
                        "combined_score": kw_score * 0.5
                    }

        # 3. 排序返回
        sorted_results = sorted(results.values(), key=lambda x: x["combined_score"], reverse=True)
        return sorted_results[:top_k]
