"""
模型客户端共享工具 — API 和本地模型客户端通用的辅助函数

包含：
- thinking 标签流式剥离
- 工具执行日志格式化
- 参数哈希与重复检测
- 错误分类工具
"""
import hashlib
import json
import sys
import threading
from pathlib import Path

_plugin_root = str(Path(__file__).parent.parent.resolve())
if _plugin_root not in sys.path:
    sys.path.insert(0, _plugin_root)
from 后端.日志配置 import 获取日志器

logger = 获取日志器("模型客户端工具")

# 流式 thinking 标签剥离支持
# 长标签优先，避免 <think> 误匹配 <thinking> 的前缀
_THINK_OPEN_TAGS = ("<thinking>", "<think>")
_THINK_CLOSE_TAGS = ("</thinking>", "</think>")


def _strip_thinking_stream(delta: str, buffer: str, in_thinking: bool):
    """处理流式文本中跨 chunk 的 thinking 标签剥离

    支持 <thinking>...</thinking>（标准）和 <think>...</think>（Qwen 变体）两种标签。
    标签可能跨 chunk 出现（如 chunk 以 <think 结尾），通过 buffer 暂存部分标签前缀。

    Args:
        delta: 本轮接收到的文本片段
        buffer: 上一轮残留的缓冲区（可能包含部分标签前缀）
        in_thinking: 是否处于 thinking 块内

    Returns:
        (text_to_yield, new_buffer, new_in_thinking)
    """
    text = buffer + delta
    result = ""
    pos = 0
    new_buffer = ""

    while pos < len(text):
        tags = _THINK_CLOSE_TAGS if in_thinking else _THINK_OPEN_TAGS

        # 寻找最近的标签（长标签优先，避免 <think> 误匹配 <thinking>）
        found_tag = None
        found_pos = len(text)
        for tag in tags:
            idx = text.find(tag, pos)
            if idx != -1 and idx < found_pos:
                found_pos = idx
                found_tag = tag

        if found_tag is None:
            # 未找到完整标签，检查末尾是否有部分标签前缀
            remaining = text[pos:]
            max_partial = 0
            for tag in tags:
                for i in range(min(len(tag) - 1, len(remaining)), 0, -1):
                    if remaining.endswith(tag[:i]):
                        if i > max_partial:
                            max_partial = i
                        break

            if max_partial > 0:
                if not in_thinking:
                    result += remaining[:-max_partial]
                new_buffer = remaining[-max_partial:]
            else:
                if not in_thinking:
                    result += remaining
                new_buffer = ""
            break
        else:
            if not in_thinking:
                result += text[pos:found_pos]
            pos = found_pos + len(found_tag)
            in_thinking = not in_thinking

    return result, new_buffer, in_thinking


def _tool_log_summary(tool_name: str, tool_args: dict) -> str:
    """生成工具调用的简洁日志摘要，只显示操作意图，不输出 content/patch 等大字段"""
    try:
        if tool_name == "batch_edit":
            ops = tool_args.get("operations", [])
            parts = [f"{op.get('action', '?')} {op.get('file_path', '?')}" for op in ops[:5]]
            suffix = f" ...+{len(ops)-5}个" if len(ops) > 5 else ""
            return f"[{', '.join(parts)}{suffix}]"
        elif tool_name == "write_plugin_file":
            fp = tool_args.get("file_path", "?")
            size = len(tool_args.get("content", ""))
            return f"file={fp} ({size}字符)"
        elif tool_name == "edit_file":
            fp = tool_args.get("file_path", "?")
            patch_len = len(tool_args.get("patch", ""))
            return f"file={fp} (patch {patch_len}字符)"
        elif tool_name == "read_plugin_file":
            fp = tool_args.get("file_path", "?")
            offset = tool_args.get("offset", "")
            return f"file={fp}" + (f" offset={offset}" if offset else "")
        elif tool_name == "list_plugin_files":
            return f"path={tool_args.get('path', '.')}"
        elif tool_name == "grep_plugin_files":
            p = tool_args.get("pattern", "?")
            g = tool_args.get("glob", "")
            return f"pattern={p}" + (f" glob={g}" if g else "")
        elif tool_name == "find_plugin_files":
            return f"glob={tool_args.get('glob_pattern', '?')}"
        elif tool_name == "rename_plugin_file":
            return f"{tool_args.get('source_path', '?')} → {tool_args.get('dest_path', '?')}"
        elif tool_name == "check_python_syntax":
            return f"file={tool_args.get('file_path', '?')}"
        elif tool_name == "execute_command":
            cmd = tool_args.get("command", "?")
            return f"{cmd[:80]}"
        elif tool_name == "search_plugin_file":
            fp = tool_args.get("file_path", "?")
            p = tool_args.get("pattern", "?")
            return f"file={fp} pattern={p}"
        else:
            # 未知工具：只显示 key 名
            return f"keys={list(tool_args.keys())}"
    except Exception:
        return ""


def _计算参数哈希(tool_name: str, tool_args: dict) -> str:
    """对工具名+参数做 MD5 哈希（取前8位），用于去重标识"""
    raw = f"{tool_name}|{json.dumps(tool_args, sort_keys=True, ensure_ascii=False)}"
    return hashlib.md5(raw.encode('utf-8')).hexdigest()[:8]


# 不纳入去重的工具（每次执行结果可能不同）
_免去重工具集 = {"execute_command"}


def _是否合理重复(tool_name: str, tool_args: dict, 已修改文件集: set) -> bool:
    """判断重复调用是否合理（不应计入去重）

    合理场景：
    - execute_command：构建/测试命令每次结果可能不同
    - read_plugin_file 在中间有 write/edit 修改过同一文件后的重新读取
    """
    if tool_name in _免去重工具集:
        return True
    # 读文件时，若该文件在上次读取后被修改过，则允许重复读取
    if tool_name == "read_plugin_file":
        file_path = tool_args.get("file_path", "")
        if file_path and file_path in 已修改文件集:
            return True
    return False


# ===== 同文件累计读取配额（坑 98 同源：压缩失忆→重复重读）=====
# args_hash 去重只能识别「完全相同参数」，模型换个行号区间就能绕过（实测
# 单个 1006 行文件被分段重读 72 次，而 args_hash 重复率仅 10%）。故补一道
# 按文件路径（忽略行号）的累计配额，参考 Claude Code 的 Read 文件跟踪与
# Cline 的 FileContextTracker：按路径而非参数追踪，文件被写入后重置。
_文件读取软上限 = 8   # 达到后注入警告 + 已读区间清单，仍执行
_文件读取硬上限 = 12  # 达到后拒绝执行，只回已读区间清单与改用 search 的指引


def _记录文件读取(文件读取计数: dict, file_path: str, tool_args: dict):
    """记录一次成功的文件读取（按路径累计次数，并另存已读行区间用于提示）"""
    if not file_path:
        return
    记录 = 文件读取计数.setdefault(file_path, {"次数": 0, "区间": []})
    记录["次数"] += 1
    起 = tool_args.get("start_line")
    止 = tool_args.get("end_line")
    区间 = f"{起 or 1}-{止}" if (起 or 止) else "全文"
    if 区间 not in 记录["区间"]:
        记录["区间"].append(区间)


def _文件读取配额检查(tool_name: str, tool_args: dict,
                    文件读取计数: dict, 已修改文件集: set) -> tuple:
    """检查同一文件的累计读取次数是否超配额（忽略行号差异）

    只约束 read_plugin_file：search_plugin_file 属探索行为、结果体积小，
    纳入硬拦截容易误伤正常的关键字定位。

    Returns:
        (处置, 提示文本)，处置 ∈ {"放行", "警告", "拒绝"}
        - 放行：正常执行
        - 警告：注入提示后仍执行
        - 拒绝：不执行，直接把提示作为工具结果回给模型
    """
    if tool_name != "read_plugin_file":
        return "放行", ""
    file_path = tool_args.get("file_path", "")
    # 文件刚被 write/edit 改过 → 重读合理（与 _是否合理重复 同语义）
    if not file_path or file_path in 已修改文件集:
        return "放行", ""
    记录 = 文件读取计数.get(file_path)
    if not 记录 or 记录["次数"] < _文件读取软上限:
        return "放行", ""

    次数 = 记录["次数"]
    已读区间 = "、".join(记录["区间"][:20])
    if 次数 >= _文件读取硬上限:
        return "拒绝", (
            f"[系统拦截] 文件 {file_path} 本次任务内已读取 {次数} 次，"
            f"已覆盖区间：{已读区间}。为避免重复空转，本次读取未执行。\n"
            f"请基于上文已获取的内容继续推进；若确实缺少某段内容，"
            f"请改用 search_plugin_file 用关键字精确检索，"
            f"而不是继续分段重读整个文件。"
        )
    return "警告", (
        f"[系统提示] 文件 {file_path} 本次任务内已读取 {次数} 次，"
        f"已覆盖区间：{已读区间}。若所需内容已在上文，请勿继续重读；"
        f"达到 {_文件读取硬上限} 次后将被系统拦截。"
    )


def _处理工具错误(错误恢复器, error_msg, info, 工具重试计数, 工具名重试计数, max_retries,
                工具错误类型计数=None, pitfall_threshold=2, max_same_tool_failures=5):
    """统一工具错误处理：分析错误原因、追踪重试次数、检测重复失败死循环。

    改进：
    - 字符串错误也能被分析（旧版只检测 Exception 类型）
    - 按 (tool_name, file_path) 跨轮次追踪，防止同操作无限重试
    - 按 (tool_name, error_type) 追踪，达到阈值时自动创建踩坑记录
    - 连续失败达到上限时自动标记为最终失败

    Args:
        错误恢复器: 错误恢复器实例
        error_msg: 错误消息字符串
        info: 工具调用信息字典 {"id": ..., "name": ..., "args": ...}
        工具重试计数: tool_call_id -> 重试次数 的字典
        工具名重试计数: (tool_name|file_path) -> 跨轮次失败次数 的字典
        max_retries: 单轮最大重试次数
        工具错误类型计数: (tool_name|error_type) -> 失败次数 的字典（可选）
        pitfall_threshold: 同错误类型失败次数阈值，达到后创建踩坑记录
        max_same_tool_failures: 同工具+同文件最大连续失败次数
    """
    error_info = 错误恢复器.分析错误(error_msg, info['name'], info['args'])
    # 按 tool_call_id 追踪（单轮内重试）
    attempt = 工具重试计数.get(info['id'], 0) + 1
    工具重试计数[info['id']] = attempt
    # 按 (tool_name, file_path) 跨轮次追踪（防止死循环）
    文件路径 = info['args'].get('file_path', '') if isinstance(info.get('args'), dict) else ''
    工具文件键 = f"{info['name']}|{文件路径}"
    同名失败次数 = 工具名重试计数.get(工具文件键, 0) + 1
    工具名重试计数[工具文件键] = 同名失败次数

    # 按 (tool_name, error_type) 追踪：达到阈值时自动创建踩坑记录（不阻塞重试流程）
    if 工具错误类型计数 is not None:
        错误类型键 = f"{info['name']}|{error_info['error_type']}"
        错误类型次数 = 工具错误类型计数.get(错误类型键, 0) + 1
        工具错误类型计数[错误类型键] = 错误类型次数
        if 错误类型次数 >= pitfall_threshold:
            try:
                threading.Thread(
                    target=错误恢复器.持久化错误模式,
                    args=(error_info, info['name'], info['args'], error_msg),
                    daemon=True,
                ).start()
            except Exception:
                pass

    if 同名失败次数 >= max_same_tool_failures:
        # 最终失败 → 持久化到踩坑记录（异步线程，不阻塞主流程）
        try:
            threading.Thread(
                target=错误恢复器.持久化错误模式,
                args=(error_info, info['name'], info['args'], error_msg),
                daemon=True,
            ).start()
        except Exception:
            pass
        return f"工具调用最终失败（已连续失败{同名失败次数}次）: {error_info['user_message']}"
    if attempt < max_retries and error_info['retry_recommended']:
        return 错误恢复器.格式化错误反馈(error_info, attempt, max_retries)
    # 重试耗尽 → 持久化到踩坑记录（异步线程，不阻塞主流程）
    try:
        threading.Thread(
            target=错误恢复器.持久化错误模式,
            args=(error_info, info['name'], info['args'], error_msg),
            daemon=True,
        ).start()
    except Exception:
        pass
    return f"工具调用最终失败: {error_info['user_message']}"
