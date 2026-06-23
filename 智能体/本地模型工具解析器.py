"""本地模型工具调用解析器

解析本地模型输出中的 <tool_call> 标记，提取工具调用指令。
采用 XML 标记格式：<tool_call>{"name":"...", "arguments":{...}}</tool_call>
"""

import re
import json
import logging

from .JSON修复工具 import _修复截断JSON

logger = logging.getLogger("nodecraft_ai.工具解析器")

# 匹配 <tool_call>...</tool_call> 的正则
TOOL_CALL_PATTERN = re.compile(r'<tool_call>\s*(.*?)\s*</tool_call>', re.DOTALL)

# 匹配本地模型可能输出的进度提示，如 [正在执行: xxx]
_PROGRESS_PATTERN = re.compile(r'\[正在执行:[^\]]*\]', re.DOTALL)


def extract_tool_calls(text: str) -> list[dict]:
    """提取所有 <tool_call>...</tool_call> 中的工具调用
    
    返回列表，每个元素为 {"name": "工具名", "arguments": {...}}
    JSON 解析失败时尝试使用 JSON修复工具 的三层修复策略
    """
    if not text:
        return []

    calls = []
    for raw_match in TOOL_CALL_PATTERN.findall(text):
        parsed = None

        # 第一层：直接解析
        try:
            parsed = json.loads(raw_match)
        except (json.JSONDecodeError, ValueError):
            pass

        # 第二层：使用 JSON 修复工具
        if parsed is None:
            repaired = _修复截断JSON(raw_match)
            if repaired is not None:
                try:
                    parsed = json.loads(repaired)
                except (json.JSONDecodeError, ValueError):
                    logger.warning(
                        "JSON 修复工具返回的内容仍无法解析: %s", repaired[:200]
                    )

        if parsed is None:
            logger.warning("无法解析 tool_call 中的 JSON: %s", raw_match[:200])
            continue

        if not isinstance(parsed, dict):
            logger.warning("tool_call JSON 解析结果不是对象: %s", type(parsed))
            continue

        if "name" not in parsed:
            logger.warning("tool_call 缺少必需的 'name' 字段: %s", parsed)
            continue

        call = {
            "name": parsed["name"],
            "arguments": parsed.get("arguments", {}),
        }

        # 统一 arguments 为 dict
        if call["arguments"] is None:
            call["arguments"] = {}
        elif not isinstance(call["arguments"], dict):
            logger.warning(
                "工具 '%s' 的 arguments 不是对象，已忽略: %s",
                call["name"],
                type(call["arguments"]),
            )
            call["arguments"] = {}

        calls.append(call)

    return calls


def separate_content_and_calls(text: str) -> tuple[str, list[dict]]:
    """分离普通文本内容和工具调用
    
    返回 (纯文本内容, 工具调用列表)
    纯文本为移除所有 tool_call 标记后的内容
    """
    if not text:
        return "", []

    calls = extract_tool_calls(text)
    cleaned = TOOL_CALL_PATTERN.sub('', text)
    cleaned = _clean_text(cleaned)
    return cleaned, calls


def strip_tool_calls(text: str) -> str:
    """移除文本中的 tool_call 标记，返回纯用户可见内容
    
    清理多余空行，保持格式整洁
    """
    if not text:
        return ""

    cleaned = TOOL_CALL_PATTERN.sub('', text)
    cleaned = _PROGRESS_PATTERN.sub('', cleaned)
    cleaned = _clean_text(cleaned)
    return cleaned


def has_tool_calls(text: str) -> bool:
    """快速检测文本中是否包含工具调用标记"""
    if not text:
        return False
    return '<tool_call>' in text


def format_tool_result(tool_name: str, result: str, success: bool = True) -> str:
    """格式化工具执行结果，用于反馈给模型
    
    格式：[工具结果: tool_name] 结果内容
    """
    if success:
        return f"\n[工具结果: {tool_name}]\n{result}\n"
    return f"\n[工具执行失败: {tool_name}]\n{result}\n"


def _clean_text(text: str) -> str:
    """清理文本中的多余空行与首尾空白"""
    if not text:
        return ""

    # 将连续 3 个及以上换行压缩为 2 个
    cleaned = re.sub(r'\n{3,}', '\n\n', text)
    return cleaned.strip()
