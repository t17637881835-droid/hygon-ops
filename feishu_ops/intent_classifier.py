"""意图分类模块 - 区分问答/闲聊/操作指令"""
from typing import Dict, Optional
from enum import Enum


class IntentType(Enum):
    """意图类型"""
    QUESTION = "question"  # 问答类（运维问题）
    CHAT = "chat"  # 闲聊类
    COMMAND = "command"  # 操作指令类


class IntentClassifier:
    """基于规则的意图分类器"""

    # 闲聊关键词
    CHAT_KEYWORDS = {
        "你好", "在吗", "谢谢", "感谢", "再见", "晚安", "早", "哈喽", "嗨",
        "测试", "test", "hello", "hi", "thanks", "bye",
    }

    # 操作指令关键词（要求执行某个动作）
    COMMAND_KEYWORDS = {
        "帮我", "请", "执行", "运行", "启动", "停止", "重启", "删除", "创建",
        "查看", "显示", "列出", "获取", "下载", "上传",
        "申请", "开通", "分配", "释放", "回收",
    }

    # 强运维问题标识（即使包含指令词，也是问答）
    QUESTION_MARKERS = {
        "怎么办", "怎么", "如何", "为什么", "是什么", "在哪", "什么", "哪里",
        "无法", "失败", "报错", "错误", "问题", "故障", "异常",
        "不能", "不可以", "不行", "没反应",
    }

    def classify(self, query: str) -> Dict[str, any]:
        """分类意图"""
        query_lower = query.lower()

        # 1. 先检查闲聊
        if any(kw in query for kw in self.CHAT_KEYWORDS):
            return {
                "intent": IntentType.CHAT,
                "confidence": 0.9,
                "reason": f"匹配闲聊关键词",
            }

        # 2. 检查是否为问答（强标识）
        if any(marker in query for marker in self.QUESTION_MARKERS):
            return {
                "intent": IntentType.QUESTION,
                "confidence": 0.85,
                "reason": f"匹配问答标识词",
            }

        # 3. 检查操作指令
        if any(kw in query for kw in self.COMMAND_KEYWORDS):
            return {
                "intent": IntentType.COMMAND,
                "confidence": 0.7,
                "reason": f"匹配操作指令关键词",
            }

        # 4. 默认为问答（运维场景下大多数是问答）
        return {
            "intent": IntentType.QUESTION,
            "confidence": 0.5,
            "reason": "默认归类为问答",
        }

    def should_answer_with_kb(self, query: str) -> bool:
        """判断是否应该用知识库回答"""
        result = self.classify(query)
        return result["intent"] == IntentType.QUESTION
