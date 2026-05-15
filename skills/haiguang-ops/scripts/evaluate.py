"""知识库检索离线评测脚本"""
import sys
import json
from pathlib import Path
from typing import List, Dict, Tuple
from collections import Counter

sys.path.insert(0, str(Path(__file__).parent.parent / "tools"))
from vector_search import HybridSearch


# 标准测试集：20 个常见运维问题
TEST_QUERIES = [
    # login (4)
    ("登录不上节点怎么办", "login"),
    ("ssh 连不上跳转机", "login"),
    ("公钥登录失败 Permission denied", "login"),
    ("怎么重置 SSH 密钥", "login"),
    
    # container (4)
    ("docker 容器起不来", "container"),
    ("容器内网络不通", "container"),
    ("docker build 报错", "container"),
    ("怎么清理 docker 镜像缓存", "container"),
    
    # gpu (3)
    ("nvidia-smi 显示 No devices were found", "gpu"),
    ("GPU 利用率低怎么排查", "gpu"),
    ("驱动版本不匹配", "gpu"),
    
    # network (3)
    ("ping 不通外网", "network"),
    ("NCCL 通信超时", "network"),
    ("网卡配置文件在哪", "network"),
    
    # storage (3)
    ("磁盘空间满了", "storage"),
    ("NFS 挂载失败", "storage"),
    ("怎么查看磁盘 IO", "storage"),
    
    # env (3)
    ("conda activate 报错", "env"),
    ("module load 找不到模块", "env"),
    ("环境变量怎么设置永久生效", "env"),
]


def evaluate_retrieval(hs: HybridSearch, top_k: int = 3) -> Dict:
    """运行评测"""
    results = []
    
    for query, expected_category in TEST_QUERIES:
        hits = hs.search(query, top_k=top_k)
        top_hit = hits[0] if hits else None
        
        # 判断是否命中（category 匹配）
        hit_category = top_hit.get("category", "") if top_hit else ""
        category_match = hit_category == expected_category
        
        # 记录结果
        results.append({
            "query": query,
            "expected_category": expected_category,
            "top_id": top_hit.get("id", "") if top_hit else "",
            "top_category": hit_category,
            "category_match": category_match,
            "combined_score": top_hit.get("combined_score", 0.0) if top_hit else 0.0,
            "vector_score": top_hit.get("vector_score", 0.0) if top_hit else 0.0,
            "keyword_score": top_hit.get("keyword_score", 0.0) if top_hit else 0.0,
            "hits": hits,
        })
    
    # 统计
    total = len(results)
    category_hits = sum(1 for r in results if r["category_match"])
    category_hit_rate = category_hits / total if total > 0 else 0.0
    
    # 分数分布
    scores = [r["combined_score"] for r in results if r["combined_score"] > 0]
    score_stats = {
        "min": min(scores) if scores else 0.0,
        "max": max(scores) if scores else 0.0,
        "avg": sum(scores) / len(scores) if scores else 0.0,
    }
    
    # 错误案例
    errors = [r for r in results if not r["category_match"]]
    
    return {
        "total": total,
        "category_hits": category_hits,
        "category_hit_rate": category_hit_rate,
        "score_stats": score_stats,
        "results": results,
        "errors": errors,
    }


def print_report(report: Dict):
    """打印评测报告"""
    print("=" * 80)
    print("知识库检索评测报告")
    print("=" * 80)
    
    print(f"\n总体统计：")
    print(f"  总问题数: {report['total']}")
    print(f"  分类命中数: {report['category_hits']}")
    print(f"  分类命中率: {report['category_hit_rate']:.2%}")
    
    print(f"\n分数分布（combined_score > 0 的结果）：")
    print(f"  最小值: {report['score_stats']['min']:.4f}")
    print(f"  最大值: {report['score_stats']['max']:.4f}")
    print(f"  平均值: {report['score_stats']['avg']:.4f}")
    
    print(f"\n错误案例（分类不匹配）：")
    for err in report['errors'][:5]:
        print(f"  - {err['query']}")
        print(f"    期望: {err['expected_category']}, 实际: {err['top_category']}, score={err['combined_score']:.4f}")
    
    if len(report['errors']) > 5:
        print(f"  ... 还有 {len(report['errors']) - 5} 个错误案例")
    
    print(f"\n详细结果（前 10 条）：")
    print(f"  {'query':<30} {'top_id':<20} {'category_match':>12} {'score':>8}")
    print("-" * 80)
    for r in report['results'][:10]:
        mark = "✓" if r['category_match'] else "✗"
        print(f"  {r['query']:<30} {r['top_id']:<20} {mark:>12} {r['combined_score']:>8.4f}")


def main():
    kb_path = Path(__file__).parent.parent / "knowledge_base"
    print(f"知识库路径: {kb_path}")
    
    hs = HybridSearch(str(kb_path))
    report = evaluate_retrieval(hs, top_k=3)
    print_report(report)
    
    # 保存详细结果
    output = Path(__file__).parent / "evaluation_results.json"
    with open(output, "w", encoding="utf-8") as f:
        # 移除 hits 字段避免太大
        clean_results = [
            {k: v for k, v in r.items() if k != "hits"}
            for r in report["results"]
        ]
        json.dump({
            "summary": {
                "total": report["total"],
                "category_hits": report["category_hits"],
                "category_hit_rate": report["category_hit_rate"],
                "score_stats": report["score_stats"],
            },
            "results": clean_results,
        }, f, ensure_ascii=False, indent=2)
    print(f"\n详细结果已保存到: {output}")


if __name__ == "__main__":
    main()
