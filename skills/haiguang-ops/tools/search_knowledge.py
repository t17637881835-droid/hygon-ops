"""知识库搜索工具"""
import json
from pathlib import Path
from typing import List, Dict

try:
    import jieba
    JIEBA_AVAILABLE = True
except ImportError:
    JIEBA_AVAILABLE = False

# 中文停用词表
STOPWORDS = {
    "的", "是", "在", "和", "了", "我", "你", "他", "她", "它",
    "这", "那", "有", "个", "们", "来", "去", "到", "为", "和",
    "与", "或", "但", "却", "也", "就", "都", "而", "及", "着",
    "一个", "什么", "怎么", "如何", "为什么", "能否", "可以"
}

# 运维领域同义词映射
SYNONYM_MAP = {
    "登录": ["登陆", "登入", "ssh登录", "ssh登陆"],
    "容器": ["docker", "Docker", "容器化"],
    "GPU": ["gpu", "显卡", "图形处理器", "nvidia"],
    "网络": ["网", "网卡", "网络连接"],
    "存储": ["磁盘", "硬盘", "存储卷", "nfs"],
    "节点": ["机器", "服务器", "主机"],
    "驱动": ["驱动", "显卡驱动", "nvidia驱动"],
}


class KnowledgeSearch:
    def __init__(self, base_path: str):
        self.base_path = Path(base_path)
        self.faq_json = self.base_path / "faq.json"
        self._load_faq()

    def _load_faq(self):
        items: List[Dict] = []
        if self.faq_json.exists():
            with open(self.faq_json, "r", encoding="utf-8") as f:
                data = json.load(f)
                items = list(data.get("faq", []))
        # 同样加载 faq/*.md 与 docs/*.md，让纯关键词兜底也能命中文档
        try:
            from vector_search import load_md_chunks
            items.extend(load_md_chunks(self.base_path))
        except Exception:
            pass
        self.faq_items = items

    def _tokenize(self, text: str) -> List[str]:
        """中英文混合分词"""
        if not text:
            return []

        if JIEBA_AVAILABLE:
            # 使用 jieba 精确模式分词
            words = jieba.lcut(text, cut_all=False)
        else:
            # 兜底：纯空格分词 + 按字符拆分中文
            words = text.split()
            chinese_chars = []
            for char in text:
                if '\u4e00' <= char <= '\u9fff':
                    chinese_chars.append(char)
            words.extend(chinese_chars)

        # 过滤停用词和短词
        result = []
        for w in words:
            w = w.strip()
            if w and w not in STOPWORDS and len(w) > 1:
                result.append(w)
        return result

    def _expand_synonyms(self, words: List[str]) -> List[str]:
        """同义词展开"""
        expanded = set(words)
        for word in words:
            if word in SYNONYM_MAP:
                expanded.update(SYNONYM_MAP[word])
            # 反向：如果词是某个词的同义词，也添加
            for key, synonyms in SYNONYM_MAP.items():
                if word in synonyms:
                    expanded.add(key)
        return list(expanded)

    def search_by_keywords(self, keywords: List[str], limit: int = 5) -> List[Dict]:
        """根据关键词搜索 FAQ（支持中文分词和同义词）"""
        if not keywords:
            return []

        # 展开同义词
        expanded_keywords = self._expand_synonyms(keywords)

        results = []
        for item in self.faq_items:
            # 获取 item 的关键词
            item_keywords = set(item.get("keywords", []))
            item_question = item.get("question", "")
            item_solution = item.get("solution", "")

            # 对问题标题和解决方案也进行分词
            question_words = set(self._tokenize(item_question))
            solution_words = set(self._tokenize(item_solution))

            # 合并所有可搜索的词
            searchable_words = item_keywords | question_words | solution_words

            # 计算匹配分数
            score = sum(1 for kw in expanded_keywords if kw in searchable_words)

            # 额外奖励：关键词精确命中
            score += sum(1 for kw in expanded_keywords if kw in item_keywords) * 2

            if score > 0:
                results.append({**item, "score": score, "match_words": [kw for kw in expanded_keywords if kw in searchable_words]})

        results.sort(key=lambda x: x["score"], reverse=True)
        return results[:limit]

    def search_by_question(self, question: str, limit: int = 3) -> List[Dict]:
        """根据问题描述搜索 FAQ"""
        keywords = self._tokenize(question)
        return self.search_by_keywords(keywords, limit)

    def get_by_category(self, category: str) -> List[Dict]:
        """按分类获取 FAQ"""
        return [item for item in self.faq_items if item.get("category") == category]

    def get_all_categories(self) -> List[str]:
        """获取所有分类"""
        return list(set(item.get("category", "") for item in self.faq_items))
