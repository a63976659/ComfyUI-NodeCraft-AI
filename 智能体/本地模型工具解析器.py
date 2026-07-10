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


# 匹配 <function=name> 变体格式（模型偶发的非标准输出）
# 例如：<function=read_plugin_file> 或 <function=read_plugin_file>{...}</function>
_FUNCTION_TAG_PATTERN = re.compile(
    r'<function=([\w]+)>\s*(?:</arguments:\s*)?([\{\[].*[\}\]])?\s*(?:</function>)?',
    re.DOTALL
)


def _parse_function_tag_fallback(raw: str) -> dict | None:
    """兜底解析 <function=xxx> 变体格式（模型非标准输出）

    支持以下格式变体：
    - `<function=read_plugin_file>`
    - `<function=read_plugin_file> </arguments: {} }`
    - `<function=write_plugin_file>{"file_path": "x.py", "content": "..."}</function>`
    - `<function=read_plugin_file", "arguments": {"file_path": "x.py"}}` （混合格式）

    返回解析后的 dict 或 None（解析失败时）。
    """
    m = _FUNCTION_TAG_PATTERN.search(raw)
    if m:
        func_name = m.group(1).strip()
        if func_name:
            args_raw = m.group(2)
            arguments = {}
            if not args_raw:
                remaining = raw[m.end():].strip()
                if remaining:
                    json_m = re.search(r'(\{[^{}]*(?:\{[^{}]*\}[^{}]*)*\})', remaining)
                    if json_m:
                        args_raw = json_m.group(1)
            if args_raw:
                args_raw = args_raw.strip()
                try:
                    parsed_args = json.loads(args_raw)
                    if isinstance(parsed_args, dict):
                        arguments = parsed_args
                except (json.JSONDecodeError, ValueError):
                    logger.debug("<function=> 变体中 arguments 解析失败，使用空参数: %s", args_raw[:100])
            else:
                # JSON 未找到，尝试 XML 参数格式：<parameter=key>value</parameter>
                # Qwen3.5 在 <function=xxx> 标签后常输出此格式
                _rem = raw[m.end():].strip()
                _xml_args = {}
                for _pm in re.finditer(r'<parameter=([\w]+)\s*>(.*?)</parameter\s*>', _rem, re.DOTALL):
                    _xml_args[_pm.group(1)] = _pm.group(2)
                if _xml_args:
                    arguments = _xml_args
                    logger.info(
                        "[兜底解析] <function=%s> 内提取到 XML 参数: %s",
                        func_name, list(_xml_args.keys()),
                    )
            logger.warning(
                "[兜底解析] 检测到非标准 <function=%s> 格式，已转换为 JSON 工具调用。"
                " 请检查系统提示是否正确注入。",
                func_name,
            )
            return {"name": func_name, "arguments": arguments}

    # 混合格式降级：<function=tool_name" 后跟部分 JSON（无 > 闭合）
    # 例如：<function=read_plugin_file", "arguments": {"file_path": "__init__.py"}}
    _mixed_m = re.search(r'<function=([\w]+)"', raw)
    if _mixed_m:
        func_name = _mixed_m.group(1)
        json_start = raw.find('{', _mixed_m.end())
        if json_start >= 0:
            json_part = raw[json_start:]
            try:
                arguments = json.loads(json_part)
                if isinstance(arguments, dict):
                    logger.warning("[兜底解析] 混合格式 <function=%s\"...>，已从尾部提取JSON参数", func_name)
                    return {"name": func_name, "arguments": arguments}
            except (json.JSONDecodeError, ValueError):
                pass
        logger.warning("[兜底解析] 混合格式 <function=%s\"...>，未找到有效JSON，使用空参数", func_name)
        return {"name": func_name, "arguments": {}}

    # XML 参数格式降级: function=name + parameter=key/value
    _xml_func = re.search(r'<function=\"?([\w]+)\"?\s*>', raw)
    if _xml_func:
        _xml_name = _xml_func.group(1)
        _xml_args = {}
        for _pm in re.finditer(r'<parameter=([\w]+)\s*>(.*?)</parameter\s*>', raw[_xml_func.end():], re.DOTALL):
            _xml_args[_pm.group(1)] = _pm.group(2)
        if _xml_args:
            logger.warning("[兜底解析] XML参数格式 <function=%s>，提取参数: %s", _xml_name, list(_xml_args.keys()))
            return {"name": _xml_name, "arguments": _xml_args}
        logger.warning("[兜底解析] XML格式 <function=%s> 未提取到参数", _xml_name)
        return {"name": _xml_name, "arguments": {}}

    return None


def extract_tool_calls(text: str) -> list[dict]:
    """提取所有工具调用，支持多种格式
    
    返回列表，每个元素为 {"name": "工具名", "arguments": {...}}
    修复策略：
    1. 标准格式：<tool_call>JSON</tool_call> -> 直接解析
    2. 畸形 JSON：<tool_call> 内 JSON 修复工具 -> 修复后解析  
    3. 非标准格式：<tool_call> 内 <function=xxx> -> 兜底解析
    4. 裸 <function=xxx> 格式（未包裹 <tool_call>） -> 直接兜底解析
    """
    if not text:
        return []

    calls = []
    matched_ranges = []  # 记录已处理的匹配内容位置，避免重复处理

    # 第一阶段：处理标准 <tool_call>...</tool_call> 包裹格式
    for m in TOOL_CALL_PATTERN.finditer(text):
        matched_ranges.append((m.start(), m.end()))
        raw_match = m.group(1)  # 标签内容
        parsed = None

        # 层级1：直接解析 JSON
        try:
            parsed = json.loads(raw_match)
        except (json.JSONDecodeError, ValueError):
            pass

        # 层级2：使用 JSON 修复工具
        if parsed is None:
            repaired = _修复截断JSON(raw_match)
            if repaired is not None:
                try:
                    parsed = json.loads(repaired)
                except (json.JSONDecodeError, ValueError):
                    logger.warning(
                        "JSON 修复工具返回的内容仍无法解析: %s", repaired[:200]
                    )

        # 层级3：兜底解析 <function=xxx> 变体格式
        if parsed is None:
            parsed = _parse_function_tag_fallback(raw_match)

        if parsed is None:
            logger.warning("无法解析 tool_call 中的 JSON: %s", raw_match[:200])
            continue

        call = _构建工具调用(parsed)
        if call:
            calls.append(call)

    # 第二阶段：处理裸 <function=xxx> 格式（未被 <tool_call> 包裹）
    if '<function=' in text:
        for m in _FUNCTION_TAG_PATTERN.finditer(text):
            # 检查是否已在某个 <tool_call>...</tool_call> 区间内
            pos = m.start()
            already_handled = any(start <= pos < end for start, end in matched_ranges)
            if already_handled:
                continue

            func_name = m.group(1).strip()
            if not func_name:
                continue

            args_raw = m.group(2)
            arguments = {}
            if not args_raw:
                # 二级提取：从标签后的文本中查找 JSON 对象
                remaining = text[m.end():].strip()
                if remaining:
                    json_m = re.search(r'(\{[^{}]*(?:\{[^{}]*\}[^{}]*)*\})', remaining)
                    if json_m:
                        args_raw = json_m.group(1)
            if args_raw:
                try:
                    parsed_args = json.loads(args_raw.strip())
                    if isinstance(parsed_args, dict):
                        arguments = parsed_args
                except (json.JSONDecodeError, ValueError):
                    pass

            logger.warning(
                "[兜底解析] 检测到裸 <function=%s> 格式（未被<tool_call>包裹），已转换为工具调用",
                func_name,
            )
            calls.append({"name": func_name, "arguments": arguments})

    return calls


# 已知工具名列表（用于从参数推断工具名）
_KNOWN_TOOLS = {'list_plugin_files', 'read_plugin_file', 'write_plugin_file', 'update_readme'}


def _推断工具名(arguments: dict) -> str | None:
    """从参数键推断工具名（小模型可能只输出 arguments 而漏掉 name）

    推断规则：
    - 有 content 键 → write_plugin_file
    - 有 file_path 键 → read_plugin_file
    - 无参数或空 dict → list_plugin_files
    """
    if not arguments:
        return 'list_plugin_files'
    keys = set(arguments.keys())
    if 'content' in keys:
        return 'write_plugin_file'
    if 'changelog_entry' in keys:
        return 'update_readme'
    if 'file_path' in keys:
        return 'read_plugin_file'
    return None


def _构建工具调用(parsed: dict) -> dict | None:
    """验证并构建工具调用字典，返回 None 表示验证失败

    容错处理：
    - 若 parsed 缺少 'name' 但包含合法参数，尝试从参数推断工具名
    - 若 parsed 包含 'name' 但值是工具名（非 function 字段），直接使用
    """
    if not isinstance(parsed, dict):
        logger.warning("tool_call JSON 解析结果不是对象: %s", type(parsed))
        return None

    # 情况1：有 name 字段 → 正常路径
    if 'name' in parsed:
        name = parsed['name']
        # name 可能是一个 dict（模型偶发输出嵌套），取其中的值
        if isinstance(name, dict):
            name = name.get('name', name.get('function', ''))
        if not isinstance(name, str) or not name.strip():
            logger.warning("tool_call 'name' 字段为空或类型异常: %s", parsed)
            return None
        call = {
            "name": name.strip(),
            "arguments": parsed.get("arguments", {}),
        }
    else:
        # 情况2：缺少 name → 尝试从参数推断（小模型常见偏差）
        # 检查是否有 'function' 字段作为 name 的替代
        alt_name = parsed.get('function') or parsed.get('tool') or parsed.get('tool_name')
        if isinstance(alt_name, str) and alt_name.strip():
            inferred = alt_name.strip()
            arguments = parsed.get("arguments", {})
            if not isinstance(arguments, dict):
                # 整个 dict 可能都是 arguments（name 被省略了）
                arguments = {k: v for k, v in parsed.items() if k not in ('function', 'tool', 'tool_name')}
            call = {"name": inferred, "arguments": arguments}
            logger.warning(
                "[推断工具名] tool_call 缺少 'name'，从 '%s' 字段推断为 '%s'",
                alt_name, inferred,
            )
        else:
            # 最后尝试：整个 dict 可能就是 arguments，从键推断工具名
            inferred = _推断工具名(parsed)
            if inferred:
                call = {"name": inferred, "arguments": dict(parsed)}
                logger.warning(
                    "[推断工具名] tool_call 缺少 'name'，从参数键推断为 '%s'。原始内容: %s",
                    inferred, str(parsed)[:200],
                )
            else:
                logger.warning("tool_call 缺少 'name' 字段且无法推断: %s", parsed)
                return None

    # 统一校验 arguments
    if call["arguments"] is None:
        call["arguments"] = {}
    elif not isinstance(call["arguments"], dict):
        logger.warning(
            "工具 '%s' 的 arguments 不是对象，已忽略: %s",
            call["name"],
            type(call["arguments"]),
        )
        call["arguments"] = {}
    return call


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
    """移除文本中的工具调用标记，返回纯用户可见内容
    
    同时清理标准 <tool_call> 和非标准 <function=xxx> 格式
    清理多余空行，保持格式整洁
    """
    if not text:
        return ""

    cleaned = TOOL_CALL_PATTERN.sub('', text)
    # 清除裸 <function=xxx>...</function> 格式
    cleaned = _FUNCTION_TAG_PATTERN.sub('', cleaned)
    cleaned = _PROGRESS_PATTERN.sub('', cleaned)
    cleaned = _clean_text(cleaned)
    return cleaned


def has_tool_calls(text: str) -> bool:
    """快速检测文本中是否包含工具调用标记
    
    同时检测标准 <tool_call> 和非标准 <function=xxx> 格式
    """
    if not text:
        return False
    return '<tool_call>' in text or '<function=' in text


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
