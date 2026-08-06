"""
工具调用错误恢复模块。
分析工具调用失败原因，生成结构化错误反馈，帮助 LLM 理解并修正。

学习捕获路径：当工具错误达到最终失败（重试耗尽）时，自动将错误模式
持久化到踩坑记录系统，供后续会话通过主动踩坑检索避免相同错误。
"""
import hashlib
import json
import logging
import re
import sys
import threading
import time
import uuid
from pathlib import Path
from typing import Optional

_plugin_root = str(Path(__file__).parent.parent.resolve())
if _plugin_root not in sys.path:
    sys.path.insert(0, _plugin_root)

logger = logging.getLogger("NodeCraftAI")


class 错误恢复器:
    """分析工具调用错误，生成 LLM 可理解的错误反馈"""

    # 错误模式分类：(正则, 错误类型代码, LLM可读说明)
    _错误模式 = [
        # 路径相关
        (r"FileNotFoundError|文件不存在|No such file", "FILE_NOT_FOUND",
         "文件不存在。请检查路径是否正确。提示：使用 list_files 工具查看目录结构，或确认插件路径。"),
        (r"PermissionError|权限", "PERMISSION_DENIED",
         "权限不足。可能是文件被其他程序占用。请关闭相关编辑器后重试。"),
        (r"IsADirectoryError|是目录", "PATH_IS_DIRECTORY",
         "路径指向的是目录而非文件。请指定具体的文件路径。"),

        # 补丁相关
        (r"补丁应用失败|context not found|上下文不匹配|patch", "PATCH_FAILED",
         "增量编辑补丁应用失败。可能原因：1) 文件内容已变化 2) 上下文行不匹配。"
         "建议：改用 write_plugin_file 工具全量写入文件。"),
        (r"补丁格式|invalid diff|malformed", "PATCH_FORMAT",
         "补丁格式不正确。请使用标准 unified diff 格式（--- /+++ /@@）。"),

        # JSON 相关
        (r"JSONDecodeError|json.*解析|invalid json", "JSON_ERROR",
         "JSON 解析失败。请确保输出有效的 JSON 格式。"),

        # 参数相关
        (r"missing.*parameter|缺少.*参数|required", "MISSING_PARAM",
         "缺少必需参数。请检查工具定义中的 required 字段。"),
        (r"type.*error|类型.*错误|invalid type", "TYPE_ERROR",
         "参数类型不正确。请检查工具定义中的参数类型要求。"),

        # 通用
        (r"already exists|已存在", "FILE_EXISTS",
         "文件已存在。如需覆盖，请使用 write 操作而非 create 操作。"),
    ]

    def 分析错误(self, error_message: str, tool_name: str, tool_args: dict) -> dict:
        """
        分析工具调用错误，返回结构化反馈。

        Returns:
            {
                "error_type": 错误类型代码,
                "user_message": LLM 可理解的错误说明,
                "suggestion": 建议的恢复操作,
                "retry_recommended": 是否建议重试,
                "alternative_tool": 建议的替代工具（如果有）
            }
        """
        result = {
            "error_type": "UNKNOWN",
            "user_message": error_message,
            "suggestion": "",
            "retry_recommended": True,
            "alternative_tool": None,
            "tool_name": tool_name,
        }

        # 匹配错误模式
        for pattern, error_type, message in self._错误模式:
            if re.search(pattern, error_message, re.IGNORECASE):
                result["error_type"] = error_type
                result["user_message"] = message
                break

        # 生成建议
        result["suggestion"] = self._生成建议(result["error_type"], tool_name, tool_args)

        # 判断是否建议重试
        result["retry_recommended"] = result["error_type"] not in ["PERMISSION_DENIED"]

        # 建议替代工具
        result["alternative_tool"] = self._建议替代工具(result["error_type"], tool_name)

        return result

    def _生成建议(self, error_type: str, tool_name: str, tool_args: dict) -> str:
        """根据错误类型生成具体建议"""
        suggestions = {
            "FILE_NOT_FOUND": "文件路径可能不正确。请先使用 list_files 工具查看目录结构，确认文件路径后再重试。",
            "PATCH_FAILED": "增量编辑失败。建议改用 write_plugin_file 工具全量写入文件内容。",
            "PATCH_FORMAT": (
                "请确保补丁使用标准 unified diff 格式。示例：\n"
                "```\n--- a/file.py\n+++ b/file.py\n"
                "@@ -1,3 +1,3 @@\n old line\n-old line\n+new line\n old line\n```"
            ),
            "FILE_EXISTS": "文件已存在。如需覆盖请使用 write 操作，如需修改请使用 edit 操作。",
            "JSON_ERROR": "请确保输出有效的 JSON。检查是否有尾随逗号、未闭合的括号等。",
            "MISSING_PARAM": "请检查工具定义中的 required 参数列表，确保所有必需参数都已提供。",
            "TYPE_ERROR": "请检查参数类型。file_path 应为字符串，content 应为字符串。",
            "PERMISSION_DENIED": "文件可能被其他程序占用。请关闭相关编辑器后重试。",
        }
        return suggestions.get(error_type, "请检查参数是否正确后重试。")

    def _建议替代工具(self, error_type: str, tool_name: str) -> Optional[str]:
        """建议替代工具"""
        alternatives = {
            "PATCH_FAILED": "write_plugin_file",
            "PATCH_FORMAT": "write_plugin_file",
            "FILE_EXISTS": "write_plugin_file",
        }
        return alternatives.get(error_type)

    # 已持久化的错误模式去重集合（进程级，线程安全）
    _已持久化键集: set = set()
    _持久化锁 = threading.Lock()

    def 持久化错误模式(self, error_info: dict, tool_name: str, tool_args: dict, error_msg: str):
        """将最终失败的工具错误持久化到踩坑记录，连接学习捕获路径。

        当工具错误达到最终失败（重试耗尽）时调用，将错误模式保存为
        踩坑记录 FAQ 条目，供后续会话通过主动踩坑检索避免相同错误。

        去重策略：基于 (error_type, tool_name, file_path) 生成哈希键，
        同一进程内同一错误模式只持久化一次。

        此方法设计为在线程池中异步调用，不阻塞主流程。
        """
        try:
            error_type = error_info.get("error_type", "UNKNOWN")
            file_path = ""
            if isinstance(tool_args, dict):
                file_path = tool_args.get("file_path", "")

            # 去重键：错误类型 + 工具名 + 文件路径
            去重键 = hashlib.md5(
                f"{error_type}|{tool_name}|{file_path}".encode()
            ).hexdigest()[:12]

            with self._持久化锁:
                if 去重键 in self._已持久化键集:
                    return  # 同一进程内已持久化
                self._已持久化键集.add(去重键)

            # 构建踩坑记录条目
            faq_dir = Path(_plugin_root) / "数据" / "踩坑记录"
            faq_dir.mkdir(parents=True, exist_ok=True)

            faq_id = str(uuid.uuid4())
            now = time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime())

            title = f"[自动捕获] {tool_name} - {error_type}"
            problem = f"工具 {tool_name} 调用时发生 {error_type} 错误。\n"
            if file_path:
                problem += f"文件路径: {file_path}\n"
            problem += f"错误信息: {error_msg[:500]}"

            solution = error_info.get("suggestion", "请检查参数是否正确。")
            if error_info.get("alternative_tool"):
                solution += f"\n替代方案: 使用 {error_info['alternative_tool']} 工具。"
            solution += "\n\n（此记录由系统自动捕获，来源：工具调用错误恢复器）"

            entry = {
                "id": faq_id,
                "title": title,
                "problem": problem,
                "solution": solution,
                "tags": ["自动捕获", error_type, tool_name],
                "created_at": now,
                "updated_at": now,
                "author": "system",
                "access_count": 0,
                "priority": 0.8,
            }

            # 原子写入：临时文件 → replace
            faq_file = faq_dir / f"{faq_id}.json"
            tmp_file = faq_file.with_suffix(".json.tmp")
            with open(tmp_file, "w", encoding="utf-8") as f:
                json.dump(entry, f, ensure_ascii=False, indent=2)
            tmp_file.replace(faq_file)

            # 刷新 FAQ 检索索引（延迟导入避免循环依赖）
            try:
                from 智能体.工具路由器 import invalidate_faq_index
                invalidate_faq_index()
            except Exception:
                pass

            logger.info(f"[学习捕获] 错误模式已持久化到踩坑记录: {title}")
        except Exception as e:
            logger.debug(f"[学习捕获] 错误模式持久化失败（忽略）: {e}")

    def 格式化错误反馈(self, error_info: dict, attempt: int, max_attempts: int) -> str:
        """格式化错误反馈，供注入 LLM 对话"""
        lines = [
            f"⚠️ 工具调用失败（第 {attempt}/{max_attempts} 次尝试）",
            f"工具: {error_info.get('tool_name', 'unknown')}",
            f"错误类型: {error_info['error_type']}",
            f"原因: {error_info['user_message']}",
        ]
        if error_info['suggestion']:
            lines.append(f"建议: {error_info['suggestion']}")
        if error_info['alternative_tool']:
            lines.append(f"替代方案: 请考虑使用 {error_info['alternative_tool']} 工具")
        if error_info['retry_recommended'] and attempt < max_attempts:
            lines.append(f"请修正后重试（剩余 {max_attempts - attempt} 次机会）")
        return "\n".join(lines)
