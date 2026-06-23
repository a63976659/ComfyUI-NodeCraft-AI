"""JSON 截断修复工具

提供三层修复策略，用于从 LLM 输出中恢复被截断或包含杂项前后缀的 JSON。
本模块不依赖 torch / aiohttp，可被任何客户端复用。
"""
import json
import re
from typing import Optional


# ============================================================
#  P0: JSON 截断修复（三层策略）
# ============================================================

def _修复截断JSON(text: str) -> Optional[str]:
    """修复被截断的 JSON，三层修复策略

    层1: 精确提取（first { → last }）
    层2: 智能截断修复 - O(n) 单次扫描追踪括号深度，
         记录"最后完整顶层对象位置"和"未完成对象内最后完整二级字段位置"，
         优先保留更多数据
    层3: 压缩空白后重试层1+层2

    Returns:
        修复后的 JSON 字符串，或 None（无法修复）
    """
    if not text or '{' not in text:
        return None

    # --- 层1：精确提取（first { → last }）---
    第一个左括号 = text.find('{')
    最后一个右括号 = text.rfind('}')
    if 第一个左括号 != -1 and 最后一个右括号 > 第一个左括号:
        候选 = text[第一个左括号:最后一个右括号 + 1]
        try:
            json.loads(候选)
            return 候选
        except (json.JSONDecodeError, ValueError):
            pass

    # --- 层2：智能截断修复（O(n) 单次扫描追踪括号深度）---
    修复结果 = _智能截断修复(text)
    if 修复结果 is not None:
        return 修复结果

    # --- 层3：压缩空白后重试 ---
    压缩文本 = re.sub(r'\s+', ' ', text)
    if 压缩文本 != text:
        第一个左括号 = 压缩文本.find('{')
        最后一个右括号 = 压缩文本.rfind('}')
        if 第一个左括号 != -1 and 最后一个右括号 > 第一个左括号:
            候选 = 压缩文本[第一个左括号:最后一个右括号 + 1]
            try:
                json.loads(候选)
                return 候选
            except (json.JSONDecodeError, ValueError):
                pass
        修复结果 = _智能截断修复(压缩文本)
        if 修复结果 is not None:
            return 修复结果

    return None


def _智能截断修复(text: str) -> Optional[str]:
    """O(n) 单次扫描追踪括号深度，尝试在安全截断点关闭 JSON

    追踪：
    - 最后完整顶层对象位置
    - 未完成对象内最后完整二级字段位置
    优先保留更多数据（二级 > 顶层）
    """
    栈 = []
    在字符串中 = False
    转义中 = False
    最后顶层有效位置 = 0
    最后二级有效位置 = 0

    for i, c in enumerate(text):
        if 转义中:
            转义中 = False
            continue
        if c == '\\' and 在字符串中:
            转义中 = True
            continue
        if c == '"':
            在字符串中 = not 在字符串中
            continue
        if 在字符串中:
            continue

        # 不在字符串中，追踪括号深度
        if c == '{':
            栈.append('{')
        elif c == '[':
            栈.append('[')
        elif c == '}':
            if 栈 and 栈[-1] == '{':
                栈.pop()
                if not 栈:
                    # 根对象完整闭合
                    try:
                        json.loads(text[:i + 1])
                        return text[:i + 1]
                    except (json.JSONDecodeError, ValueError):
                        pass
                elif len(栈) == 1:
                    # 顶层节点值对象闭合
                    最后顶层有效位置 = i + 1
                    最后二级有效位置 = 0
                elif len(栈) == 2:
                    最后二级有效位置 = i + 1
        elif c == ']':
            if 栈 and 栈[-1] == '[':
                栈.pop()
                if len(栈) == 2:
                    最后二级有效位置 = i + 1
        elif c == ',':
            if len(栈) == 1:
                最后顶层有效位置 = i + 1
            elif len(栈) == 2:
                最后二级有效位置 = i + 1

    # JSON 未完整闭合（被截断），尝试恢复
    # 优先策略：先尝试保留未完成节点的已完成二级字段
    if 最后二级有效位置 > 最后顶层有效位置:
        修复文本 = text[:最后二级有效位置].rstrip()
        if 修复文本.endswith(','):
            修复文本 = 修复文本[:-1]
        修复文本 += '\n}\n}'
        try:
            json.loads(修复文本)
            return 修复文本
        except (json.JSONDecodeError, ValueError):
            pass

    # 回退：截断到最后一个完整的顶层节点
    if 最后顶层有效位置 > 0:
        修复文本 = text[:最后顶层有效位置].rstrip()
        if 修复文本.endswith(','):
            修复文本 = 修复文本[:-1]
        修复文本 += '\n}'
        try:
            json.loads(修复文本)
            return 修复文本
        except (json.JSONDecodeError, ValueError):
            pass

    return None


__all__ = ["_修复截断JSON", "_智能截断修复"]
