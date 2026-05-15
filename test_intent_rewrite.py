#!/usr/bin/env python3
"""测试意图分类和查询改写"""
import sys
sys.path.insert(0, '/app/feishu_ops')

from intent_classifier import IntentClassifier
from query_rewriter import QueryRewriter

# 测试意图分类
ic = IntentClassifier()
queries_intent = [
    '你好',
    '帮我重启一下容器',
    '登录不上节点怎么办',
]
print('=== 意图分类测试 ===')
for q in queries_intent:
    r = ic.classify(q)
    print(f'{q:30} -> {r["intent"].value:10} (conf={r["confidence"]:.2f})')

# 测试查询改写
qr = QueryRewriter()
queries_rewrite = [
    '登不上去',
    '容器起不来',
    '网不通',
]
print('\n=== 查询改写测试 ===')
for q in queries_rewrite:
    r = qr.rewrite_with_explanation(q)
    print(f'{q:20} -> {r["rewritten"]:20} ({r["explanation"]})')
