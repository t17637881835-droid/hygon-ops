"""知识库运营接口（owner 私聊 /kb 命令）

提供运行时的 FAQ 维护能力，无需重启容器：
  /kb help                  — 命令列表
  /kb stats                 — 总条数、按 category 分组、向量索引、最近命中率
  /kb reload                — 重新加载 faq.json + faq/*.md + docs/*.md，重建向量索引
  /kb search <query>        — 调试检索：返回 top-3 (id, score, question)
  /kb show <id>             — 查看条目详情
  /kb add\n<KEY: VAL ...>   — 新增 FAQ（仅写入 faq.json，不动 md 文件）
  /kb del <id>              — 删除 faq.json 中的条目（不能删除 md 来源条目）

设计要点：
- 命中率统计放在内存（deque，最大 500 条），重启清零，足够运营观察。
- reload 重建 retriever 后端，HybridSearch 启动会自动调 build_index。
- add/del 使用原子写（tmp + os.replace），避免并发损坏。
- md 来源条目（id 以 faqmd- / doc- 开头）只读，避免误改文件。
"""
from __future__ import annotations

import json
import os
import re
import threading
import time
from collections import Counter, deque
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

from logger import get_logger

logger = get_logger("kb_admin")


_KB_PREFIX_RE = re.compile(r"^\s*(?:@\S+\s+)?/kb\b\s*", re.IGNORECASE)
_MD_SOURCE_PREFIXES = ("faqmd-", "doc-")
_ADD_FIELD_RE = re.compile(r"^\s*(id|question|category|keywords|solution|reasons|nodes)\s*[:：]\s*(.*)$", re.IGNORECASE)


def _strip_kb_prefix(content: str) -> str:
    """去掉 '@xxx /kb ' 前缀，返回剩余文本（保留多行）。"""
    return _KB_PREFIX_RE.sub("", content or "", count=1).strip()


class HitRecorder:
    """轻量命中率统计：仅内存。"""

    def __init__(self, capacity: int = 500):
        self._events = deque(maxlen=capacity)
        self._lock = threading.Lock()

    def record(self, kind: str, score: float = 0.0, source: str = "") -> None:
        with self._lock:
            self._events.append({
                "kind": kind,            # "kb_hit" | "llm_hit" | "miss" | "fail"
                "score": float(score or 0.0),
                "source": source,        # FAQ id / category / "llm"
                "ts": time.time(),
            })

    def snapshot(self) -> Dict[str, Any]:
        with self._lock:
            events = list(self._events)
        if not events:
            return {"total": 0}
        kinds = Counter(e["kind"] for e in events)
        scores = [e["score"] for e in events if e["score"] > 0]
        avg_score = sum(scores) / len(scores) if scores else 0.0
        return {
            "total": len(events),
            "kb_hit": kinds.get("kb_hit", 0),
            "llm_hit": kinds.get("llm_hit", 0),
            "miss": kinds.get("miss", 0),
            "fail": kinds.get("fail", 0),
            "avg_score": round(avg_score, 3),
            "first_ts": events[0]["ts"],
            "last_ts": events[-1]["ts"],
        }


class KBAdminService:
    """封装 /kb 命令的解析与执行。"""

    def __init__(self, knowledge_search, audit_logger=None):
        self.knowledge_search = knowledge_search
        self.audit_logger = audit_logger
        self.recorder = HitRecorder()
        self._lock = threading.RLock()

    # ── 对外：命中率记录入口 ─────────────────────────────────
    def record_hit(self, kind: str, score: float = 0.0, source: str = "") -> None:
        self.recorder.record(kind, score=score, source=source)

    # ── 对外：命令派发入口 ──────────────────────────────────
    def handle(self, content: str) -> str:
        body = _strip_kb_prefix(content)
        if not body:
            return self._cmd_help()
        sub, _, rest = body.partition("\n") if body.startswith(("add", "ADD")) is False else (body, "", "")
        # add 是多行命令，特殊处理：第一行 "add"，后续多行字段
        first_line = body.splitlines()[0].strip()
        rest_lines = "\n".join(body.splitlines()[1:]).strip()
        parts = first_line.split(maxsplit=1)
        sub = (parts[0] or "").lower()
        args = parts[1].strip() if len(parts) > 1 else ""

        try:
            if sub in ("", "help", "h", "?"):
                return self._cmd_help()
            if sub == "stats":
                return self._cmd_stats()
            if sub == "reload":
                return self._cmd_reload()
            if sub == "search":
                return self._cmd_search(args)
            if sub == "show":
                return self._cmd_show(args)
            if sub == "add":
                return self._cmd_add(rest_lines)
            if sub in ("del", "delete", "rm"):
                return self._cmd_del(args)
            return f"未知子命令: {sub}\n\n{self._cmd_help()}"
        except Exception as e:
            logger.exception("/kb 命令执行失败")
            return f"❌ 执行失败：{e}"

    # ── 子命令 ──────────────────────────────────────────────
    def _cmd_help(self) -> str:
        return (
            "知识库运营命令：\n"
            "  /kb stats            统计总条数、分类、最近命中率\n"
            "  /kb reload           热重载知识库（faq.json + md）\n"
            "  /kb search <query>   调试检索，返回 top-3\n"
            "  /kb show <id>        查看条目详情\n"
            "  /kb add              新增条目（多行）\n"
            "      id: gpu-100\n"
            "      question: 怎么排查 dcu-smi 报错\n"
            "      category: gpu\n"
            "      keywords: dcu-smi, 报错\n"
            "      solution: 多行内容...\n"
            "  /kb del <id>         删除 faq.json 中条目"
        )

    def _cmd_stats(self) -> str:
        items = self._all_items()
        total = len(items)
        by_cat = Counter(it.get("category", "") for it in items)
        by_source = Counter(self._source_of(it.get("id", "")) for it in items)
        snap = self.recorder.snapshot()

        backend_status = self._backend_status()

        cat_lines = "\n".join(f"  {c or '(无)'}: {n}" for c, n in sorted(by_cat.items(), key=lambda kv: -kv[1])[:8])
        src_lines = ", ".join(f"{k}={v}" for k, v in by_source.items())

        if snap.get("total", 0):
            recent = (
                f"最近 {snap['total']} 次自动回复："
                f"KB 直返={snap['kb_hit']} | LLM 润色={snap['llm_hit']} | "
                f"未命中={snap['miss']} | 失败={snap['fail']} | 平均 score={snap['avg_score']}"
            )
        else:
            recent = "最近无自动回复记录"

        return (
            f"📚 知识库统计\n"
            f"总条目数: {total}（{src_lines}）\n"
            f"后端: {backend_status}\n"
            f"\n按 category（top 8）:\n{cat_lines}\n"
            f"\n{recent}"
        )

    def _cmd_reload(self) -> str:
        with self._lock:
            try:
                from knowledge_retriever import create_retriever
                base_path = getattr(self.knowledge_search, "base_path", "")
                self.knowledge_search.retriever = create_retriever(base_path=base_path)
                items = self._all_items()
                healthy, status = self.knowledge_search.retriever.health_check()
                logger.info(f"知识库热重载完成: items={len(items)} backend={status}")
                if self.audit_logger:
                    self.audit_logger.record(event="kb_reload", items=len(items), backend=status)
                return f"✅ 已重载，共 {len(items)} 条 | 后端 {status}"
            except Exception as e:
                logger.exception("/kb reload 失败")
                return f"❌ 重载失败：{e}"

    def _cmd_search(self, query: str) -> str:
        if not query:
            return "用法: /kb search <query>"
        results = self.knowledge_search.search(query, limit=5) or []
        if not results:
            return f"无结果: {query!r}"
        lines = [f"🔎 检索 {query!r}（top {min(5, len(results))}）"]
        for r in results[:5]:
            score = float(r.get("score", 0.0) or 0.0)
            lines.append(
                f"  [{score:.3f}] {r.get('id','?')} · {r.get('category','?')} · "
                f"{(r.get('question') or '')[:60]}"
            )
        return "\n".join(lines)

    def _cmd_show(self, item_id: str) -> str:
        if not item_id:
            return "用法: /kb show <id>"
        for it in self._all_items():
            if it.get("id") == item_id:
                kw = ", ".join(it.get("keywords", []) or [])
                solution = it.get("solution", "") or ""
                src = self._source_of(item_id)
                return (
                    f"📄 {item_id}（来源: {src}）\n"
                    f"category: {it.get('category','')}\n"
                    f"question: {it.get('question','')}\n"
                    f"keywords: {kw}\n"
                    f"solution:\n{solution}"
                )
        return f"未找到条目: {item_id}"

    def _cmd_add(self, body: str) -> str:
        if not body.strip():
            return (
                "用法（多行）:\n"
                "/kb add\n"
                "id: gpu-100\n"
                "question: 标题\n"
                "category: gpu\n"
                "keywords: 关键词1, 关键词2\n"
                "solution: 多行解决方案"
            )
        fields = self._parse_kv_block(body)
        item_id = fields.get("id", "").strip()
        question = fields.get("question", "").strip()
        category = fields.get("category", "").strip()
        keywords = [k.strip() for k in re.split(r"[，,]", fields.get("keywords", "")) if k.strip()]
        solution = fields.get("solution", "").strip()

        if not item_id:
            return "❌ 缺少 id"
        if any(item_id.startswith(p) for p in _MD_SOURCE_PREFIXES):
            return f"❌ id 不能以 {'/'.join(_MD_SOURCE_PREFIXES)} 开头（这是 md 来源保留前缀）"
        if not question:
            return "❌ 缺少 question"
        if not solution:
            return "❌ 缺少 solution"

        with self._lock:
            data, faq_path = self._load_faq_json()
            faq_list = data.get("faq", [])
            if any((it.get("id") == item_id) for it in faq_list):
                return f"❌ id 已存在: {item_id}（用 /kb del 先删除，或换一个 id）"

            new_item = {
                "id": item_id,
                "question": question,
                "category": category,
                "keywords": keywords,
                "solution": solution,
            }
            for opt_key in ("possible_reasons", "related_nodes"):
                val = fields.get(opt_key) or fields.get({"possible_reasons": "reasons", "related_nodes": "nodes"}[opt_key])
                if val:
                    new_item[opt_key] = val.strip()

            faq_list.append(new_item)
            data["faq"] = faq_list
            self._atomic_write_json(faq_path, data)

        # 立即热重载，确保后续检索能命中新条目
        reload_msg = self._cmd_reload()
        if self.audit_logger:
            self.audit_logger.record(event="kb_add", item_id=item_id, category=category)
        return f"✅ 已新增 {item_id}\n{reload_msg}"

    def _cmd_del(self, item_id: str) -> str:
        if not item_id:
            return "用法: /kb del <id>"
        if any(item_id.startswith(p) for p in _MD_SOURCE_PREFIXES):
            return f"❌ {item_id} 来源于 md 文件，请直接编辑 knowledge_base/faq/*.md 或 docs/*.md"

        with self._lock:
            data, faq_path = self._load_faq_json()
            faq_list = data.get("faq", [])
            new_list = [it for it in faq_list if it.get("id") != item_id]
            if len(new_list) == len(faq_list):
                return f"未找到条目: {item_id}"
            data["faq"] = new_list
            self._atomic_write_json(faq_path, data)

        reload_msg = self._cmd_reload()
        if self.audit_logger:
            self.audit_logger.record(event="kb_del", item_id=item_id)
        return f"✅ 已删除 {item_id}\n{reload_msg}"

    # ── 工具方法 ────────────────────────────────────────────
    def _all_items(self) -> List[Dict]:
        backend = getattr(self.knowledge_search.retriever, "_backend", None)
        if backend is not None and hasattr(backend, "faq_items"):
            return list(backend.faq_items)
        return []

    def _backend_status(self) -> str:
        try:
            healthy, status = self.knowledge_search.retriever.health_check()
            return f"{status} ({'healthy' if healthy else 'unhealthy'})"
        except Exception:
            return "unknown"

    @staticmethod
    def _source_of(item_id: str) -> str:
        if item_id.startswith("faqmd-"):
            return "faq.md"
        if item_id.startswith("doc-"):
            return "doc.md"
        return "faq.json"

    @staticmethod
    def _parse_kv_block(body: str) -> Dict[str, str]:
        """解析多行 KEY: VAL 块。solution 以及未识别的 KEY 之后的所有行都归到当前 KEY。"""
        fields: Dict[str, str] = {}
        current_key: Optional[str] = None
        current_lines: List[str] = []

        def flush():
            nonlocal current_key, current_lines
            if current_key:
                fields[current_key] = "\n".join(current_lines).rstrip()
            current_key, current_lines = None, []

        for line in body.splitlines():
            m = _ADD_FIELD_RE.match(line)
            if m:
                flush()
                current_key = m.group(1).lower()
                current_lines = [m.group(2)] if m.group(2) else []
            else:
                if current_key is None:
                    continue  # 忽略未归属的内容
                current_lines.append(line)
        flush()
        return fields

    def _load_faq_json(self) -> Tuple[Dict, Path]:
        base = Path(getattr(self.knowledge_search, "base_path", "")) if getattr(self.knowledge_search, "base_path", "") else None
        if base is None:
            raise RuntimeError("KnowledgeSearchService 没有 base_path，无法定位 faq.json")
        faq_path = base / "faq.json"
        if faq_path.exists():
            with open(faq_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if not isinstance(data, dict) or "faq" not in data:
                raise RuntimeError(f"{faq_path} 格式异常：期望 {{'faq': [...]}}")
            return data, faq_path
        return {"faq": []}, faq_path

    @staticmethod
    def _atomic_write_json(path: Path, data: Dict) -> None:
        tmp = path.with_suffix(path.suffix + ".tmp")
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(tmp, path)
