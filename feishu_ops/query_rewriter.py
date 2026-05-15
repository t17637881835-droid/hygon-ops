"""查询改写模块 - 口语化表达转规范问题"""
import re
from typing import List, Dict


class QueryRewriter:
    """基于规则的查询改写器"""

    # 口语化模式 → 规范表达
    COLLOQUIAL_PATTERNS = [
        # 登录相关
        (r"登不进去", "登录失败"),
        (r"登不上去", "登录失败"),
        (r"登不上", "登录失败"),
        (r"连不上", "连接失败"),
        (r"连不上.*节点", "SSH 登录节点失败"),
        (r"连不上.*跳板", "SSH 连接跳板机失败"),
        (r"连不上.*跳转", "SSH 连接跳转机失败"),
        (r"连不上.*服务器", "SSH 连接服务器失败"),
        
        # 容器相关
        (r"容器起不来", "容器启动失败"),
        (r"容器跑不起来", "容器启动失败"),
        (r"docker 起不来", "Docker 服务启动失败"),
        (r"docker 跑不起来", "Docker 服务启动失败"),
        (r"容器挂了", "容器异常退出"),
        (r"容器死掉了", "容器异常退出"),
        
        # GPU 相关
        (r"显卡没反应", "GPU 无响应"),
        (r"显卡不工作", "GPU 无法使用"),
        (r"gpu 没反应", "GPU 无响应"),
        (r"gpu 不工作", "GPU 无法使用"),
        (r"GPU 挂了", "GPU 异常"),
        (r"gpu 挂了", "GPU 异常"),
        
        # 网络相关
        (r"网不通", "网络连接失败"),
        (r"网络不通", "网络连接失败"),
        (r"上不了网", "无法访问外网"),
        (r"连不上网", "无法访问外网"),
        (r"ping 不通", "网络 ping 失败"),
        
        # 环境相关
        (r"环境没配好", "环境配置问题"),
        (r"环境不对", "环境配置问题"),
        (r"环境坏了", "环境配置问题"),
        (r"conda 用不了", "conda 环境异常"),
        (r"module 用不了", "module 加载失败"),
        
        # 通用
        (r"怎么.*啊", lambda m: m.group(0).replace("啊", "").replace("怎么", "如何")),
        (r"怎么办", "如何解决"),
        (r"咋办", "如何解决"),
        (r"啥情况", "什么原因"),
        (r"怎么弄", "如何操作"),
    ]

    def rewrite(self, query: str) -> str:
        """改写查询"""
        original = query
        rewritten = query

        # 1. 应用口语化模式替换
        for pattern, replacement in self.COLLOQUIAL_PATTERNS:
            if callable(replacement):
                rewritten = re.sub(pattern, replacement, rewritten, flags=re.IGNORECASE)
            else:
                rewritten = re.sub(pattern, replacement, rewritten, flags=re.IGNORECASE)

        # 2. 如果改写后和原文不同，返回改写版本
        if rewritten != original:
            return rewritten

        # 3. 否则返回原文
        return original

    def rewrite_with_explanation(self, query: str) -> Dict[str, str]:
        """改写查询并返回解释"""
        original = query
        rewritten = self.rewrite(query)
        
        explanation = ""
        if rewritten != original:
            explanation = f"已将「{original}」改写为「{rewritten}」"
        
        return {
            "original": original,
            "rewritten": rewritten,
            "explanation": explanation,
        }
