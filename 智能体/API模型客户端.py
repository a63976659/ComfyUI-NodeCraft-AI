"""API 模型客户端（OpenAI 兼容）

负责 HTTP 调用、SSE 流式输出、致命错误分类、自动续写、性能统计。
JSON 修复函数从 智能体.JSON修复工具 导入。
共享数据类（性能统计）从 智能体.模型客户端 导入。
"""
import asyncio
import json
import re
import sys
import time
from pathlib import Path
from typing import Optional

import aiohttp

_plugin_root = str(Path(__file__).parent.parent.resolve())
if _plugin_root not in sys.path:
    sys.path.insert(0, _plugin_root)
from 后端.文件读写操作 import load_settings
from 后端.日志配置 import 获取日志器

from 智能体.JSON修复工具 import _修复截断JSON, _智能截断修复  # noqa: F401  re-export 兼容
from 智能体.模型客户端 import 性能统计
from 智能体.模型能力注册表 import 模型能力注册表
from 智能体.错误恢复器 import 错误恢复器

logger = 获取日志器("API模型客户端")

# 模型上下文窗口统一由 模型能力注册表 管理（单一数据源）
_model_registry = 模型能力注册表()

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
        else:
            # 未知工具：只显示 key 名
            return f"keys={list(tool_args.keys())}"
    except Exception:
        return ""


class AICoderClient:
    """OpenAI 兼容 API 客户端（支持续写、重试、JSON修复、流式输出）

    参考 Flying-Translation2.0 企业级实现：
    - 持久化 aiohttp 会话复用
    - 自动重试 2 次（可恢复错误）
    - 致命错误不重试（401/403/402）
    - 检测 finish_reason=length 自动续写
    - 剥离 <thinking> 标签
    - 120秒超时
    - 性能监控统计
    - SSE 流式对话（async generator）
    """

    # 致命错误状态码（不重试）
    _致命错误码 = {
        401: "[认证失败] 请检查 API 密钥是否正确",
        403: "[访问被拒绝] API 密钥无权访问该模型或服务",
        402: "[配额耗尽] API 账户余额不足，请充值后重试",
    }

    # 工具调用最大重试次数
    _max_tool_retries = 3

    # 已知支持多模态（vision）的 API 模型关键词
    _VISION_MODEL_KEYWORDS = (
        "gpt-4o", "gpt-4-turbo", "gpt-4-vision",
        "claude-3", "claude-3.5",
        "gemini-pro-vision", "gemini-1.5", "gemini-2",
        "qwen-vl", "qwen2-vl", "qwen2.5-vl",
        "glm-4v", "yi-vision",
    )

    def __init__(self):
        self._会话 = None
        self._性能 = 性能统计()
        self.当前模型名: Optional[str] = None  # 请求时从 settings 同步，用于 vision 能力检测
        self._错误恢复 = 错误恢复器()
        self.上次usage: Optional[dict] = None  # 最近一次请求的 token 用量信息
        self.云端计费: Optional[dict] = None  # 云端代理返回的计费信息（cost/balance）

    def _build_cloud_headers(self, settings: dict) -> dict:
        """构建云端请求的双认证 headers。

        ModelScope 平台采用双认证机制：
        - 网关层：Authorization: Bearer {modelscope_sdk_token}
          （SDK Token 用于通过 ModelScope 平台网关）
        - 业务层：X-Auth-Token: {ranking_token}
          （云端 Space 的 当前用户() 依赖从此 header 读取 ranking_token 完成业务认证，
           绕过网关对 Authorization 的拦截）

        注意：modelscope_sdk_token 在 settings.json 中加密存储，
        load_settings() 会自动解密（modelscope_sdk_token 在 _SENSITIVE_KEYS 中）。
        若 modelscope_sdk_token 为空，则不带 Authorization（网关可能允许公开访问）。
        """
        ranking_token = settings.get("ranking_token", "")
        modelscope_sdk_token = settings.get("modelscope_sdk_token", "")
        headers = {
            "Content-Type": "application/json",
            "X-Auth-Token": ranking_token,  # 业务认证：云端读取 ranking_token
        }
        if modelscope_sdk_token:
            # 网关认证：SDK Token 通过 ModelScope 平台网关
            headers["Authorization"] = f"Bearer {modelscope_sdk_token}"
        return headers

    def _refresh_headers(self) -> dict:
        """重新从设置文件读取最新 token 并构建认证 headers。

        用于工具调用循环中每轮刷新 token，防止长时间循环中 token 过期。
        与 _build_cloud_headers 的区别：本方法主动调用 load_settings() 读取最新设置。
        """
        fresh_settings = load_settings()
        return self._build_cloud_headers(fresh_settings)

    def _supports_vision(self) -> bool:
        """检测当前 API 模型是否支持视觉/多模态输入"""
        model_name = (self.当前模型名 or "").lower()
        if not model_name:
            return False
        return any(vm in model_name for vm in self._VISION_MODEL_KEYWORDS)

    def _prepare_messages(self, messages: list) -> list:
        """根据模型能力准备最终发送的消息列表

        - 支持 vision 且 消息含 _image_attachments 时，转为 OpenAI Vision multimodal 格式
        - 其它情况剥除 _image_attachments 临时字段（避免传递给 API）
        """
        支持视觉 = self._supports_vision()
        converted = []
        for msg in messages:
            if not isinstance(msg, dict):
                converted.append(msg)
                continue
            image_atts = msg.get("_image_attachments")
            if 支持视觉 and image_atts and msg.get("role") == "user":
                content_parts = [{"type": "text", "text": msg.get("content", "")}]
                for att in image_atts:
                    data_url = att.get("data") if isinstance(att, dict) else None
                    if data_url:
                        content_parts.append({
                            "type": "image_url",
                            "image_url": {"url": data_url, "detail": "auto"},
                        })
                converted.append({"role": msg["role"], "content": content_parts})
            else:
                # 剥离临时字段，保留标准字段；对 tool 消息做截断压缩
                role = msg.get("role")
                content = msg.get("content", "")
                new_msg = {"role": role, "content": content}
                # 保留 tool 消息的 tool_call_id（OpenAI 协议必需）
                if role == "tool" and "tool_call_id" in msg:
                    new_msg["tool_call_id"] = msg["tool_call_id"]
                # 截断过长的 tool 消息内容，减少 token 消耗
                if role == "tool" and len(content) > 2000:
                    new_msg["content"] = content[:2000] + f"\n...[内容已截断，原始长度 {len(content)} 字符]"
                converted.append(new_msg)
        return converted

    def _get_model_context_limit(self, model_name: str) -> int:
        """获取模型的上下文窗口大小（委托给模型能力注册表）"""
        return _model_registry.获取上下文窗口(model_name)

    def _estimate_tokens(self, messages: list) -> int:
        """估算消息列表的总token数"""
        total_chars = 0
        for msg in messages:
            content = msg.get("content", "")
            if content:
                total_chars += len(str(content))
            # tool_calls 也占 token
            if "tool_calls" in msg:
                total_chars += len(str(msg["tool_calls"]))
        # 粗略估算：中文约1.5 token/字，英文约1 token/字，取中间值
        return int(total_chars * 1.2)

    def _compress_tool_messages(self, messages: list) -> list:
        """压缩消息列表中的 tool 消息（使用摘要器）"""
        from .工具结果摘要器 import 摘要化工具结果

        result = []
        for msg in messages:
            if msg.get("role") == "tool":
                content = msg.get("content", "")
                if len(content) > 800:
                    # 从上下文推断 tool_name
                    tool_name = self._infer_tool_name(messages, msg)
                    compressed = 摘要化工具结果(tool_name, content, max_chars=800)
                    result.append({**msg, "content": compressed})
                else:
                    result.append(msg)
            else:
                result.append(msg)
        return result

    def _infer_tool_name(self, messages: list, tool_msg: dict) -> str:
        """从上下文推断 tool 消息对应的工具名"""
        tool_call_id = tool_msg.get("tool_call_id", "")
        if not tool_call_id:
            return "unknown"
        # 向前查找含有匹配 tool_call_id 的 assistant 消息
        for msg in messages:
            if msg.get("role") == "assistant" and "tool_calls" in msg:
                for tc in msg["tool_calls"]:
                    if tc.get("id") == tool_call_id:
                        return tc.get("function", {}).get("name", "unknown")
        return "unknown"

    def _drop_oldest_tool_pairs(self, messages: list) -> list:
        """删除最早的 tool_calls assistant + tool 消息对"""
        # 找到第一个含 tool_calls 的 assistant 消息
        for i, msg in enumerate(messages):
            if msg.get("role") == "assistant" and "tool_calls" in msg:
                # 找到它后面所有对应的 tool 消息
                end = i + 1
                while end < len(messages) and messages[end].get("role") == "tool":
                    end += 1
                # 删除 i 到 end 之间的所有消息
                logger.info(f"[动态窗口] 删除第 {i}-{end-1} 条消息（最早的工具调用对）")
                return messages[:i] + messages[end:]
        return messages

    async def _获取会话(self):
        """获取或创建持久的 aiohttp 会话"""
        if self._会话 is None or self._会话.closed:
            self._会话 = aiohttp.ClientSession()
        return self._会话

    async def generate_response(self, system_prompt, messages_history, tools=None, tool_executor=None):
        """调用 LLM API 生成回复（支持 OpenAI Function Calling 工具调用循环）

        特性：
        - 致命错误不重试（401/403/402）
        - 可恢复错误自动重试 2 次（5xx、网络错误）
        - 检测 finish_reason=length 自动续写
        - 检测 finish_reason=tool_calls 自动执行工具并继续对话（最多 5 轮）
        - 剥离 <thinking> 标签
        - 120秒超时
        - 性能统计

        Args:
            tools: OpenAI 风格的工具描述列表（function 字典数组），传 None 时行为与原版一致
            tool_executor: async 回调 (tool_name, tool_args) -> str，用于执行工具
        """
        开始时间 = time.time()
        settings = load_settings()
        self.云端计费 = None  # 重置云端计费信息
        self.上次usage = None  # 重置 token 用量信息（监控指标依赖此字段）

        model_name = settings.get("model_name", "deepseek-chat")
        temperature = settings.get("temperature", 0.2)
        max_tokens = settings.get("max_tokens", 4096)

        # 同步当前模型名用于 vision 能力检测
        self.当前模型名 = model_name

        # 云端代理模式：通过云端Space转发API请求
        cloud_url = settings.get("cloud_url", "").rstrip("/")
        ranking_token = settings.get("ranking_token", "")
        if not cloud_url:
            return "[错误] 未配置云端服务地址(cloud_url)，请在设置中填写"
        if not ranking_token:
            return "[错误] 未登录，请先登录后再使用API模型"

        # 双认证 headers：Authorization(网关 SDK Token) + X-Auth-Token(业务 ranking_token)
        headers = self._build_cloud_headers(settings)

        # 根据模型能力转换消息格式（vision 模型传递图片，其他模型剥除临时字段）
        prepared_history = self._prepare_messages(messages_history)
        complete_messages = [
            {"role": "system", "content": system_prompt},
            *prepared_history
        ]

        url = f"{cloud_url}/api/open/chat-stream"
        会话 = await self._获取会话()

        # OpenAI tools 格式封装
        tools_payload = None
        if tools:
            try:
                tools_payload = [{"type": "function", "function": tool} for tool in tools]
            except (TypeError, KeyError, ValueError) as e:
                logger.warning(f"tools 参数转换失败，降级为普通对话: {e}")
                tools_payload = None

        # 工具调用最大轮次可配置（默认25；-1/0/"无限" 表示不限制，兜底9999防死循环）
        _配置轮次 = settings.get("max_tool_rounds", 25)
        try:
            最大工具循环次数 = 9999 if _配置轮次 in (-1, 0, "无限", "unlimited") else int(_配置轮次)
        except (TypeError, ValueError):
            最大工具循环次数 = 25
        工具不支持降级 = False
        最终内容 = ""
        _工具重试计数 = {}  # tool_call_id -> 重试次数

        for 工具轮次 in range(最大工具循环次数):
            # ── 每轮刷新认证 headers（防止长时间工具循环中 token 过期）──
            # ranking_token 有效期约 15 分钟，多轮工具调用可能超过此时间，
            # 因此每轮重新从设置文件读取最新 token 构建 headers
            if 工具轮次 > 0:
                headers = self._refresh_headers()
                logger.debug(f"[Token刷新] 第{工具轮次+1}轮工具调用，已刷新认证 headers")

            # 动态窗口控制
            _estimated = self._estimate_tokens(complete_messages)
            _limit = self._get_model_context_limit(model_name)

            if _estimated > _limit * 0.85:
                complete_messages = self._compress_tool_messages(complete_messages)
                logger.warning(f"[动态窗口] token预估({_estimated})接近上限({_limit})，已压缩工具消息")
                _estimated = self._estimate_tokens(complete_messages)

            if _estimated > _limit * 0.95:
                complete_messages = self._drop_oldest_tool_pairs(complete_messages)
                logger.warning(f"[动态窗口] 极端压缩，删除最早工具消息对")

            payload = {
                "model": model_name,
                "messages": complete_messages,
                "temperature": temperature,
                "max_tokens": max_tokens
            }
            if tools_payload and not 工具不支持降级:
                payload["tools"] = tools_payload

            响应数据 = None
            # 内部重试 2 次（仅可恢复错误）
            for retry in range(2):
                try:
                    async with 会话.post(url, json=payload, headers=headers,
                                        timeout=aiohttp.ClientTimeout(total=120)) as resp:
                        # --- P0: 致命错误分类 ---
                        if resp.status in self._致命错误码:
                            # 403 特殊处理：解析服务端实际错误信息（如会员过期、账号封禁等）
                            if resp.status == 403:
                                server_msg = self._致命错误码[resp.status]
                                try:
                                    body_text = await resp.text()
                                    detail = json.loads(body_text).get("detail", "")
                                    if detail:
                                        server_msg = detail
                                    logger.warning(
                                        f"[403] 第{工具轮次+1}轮返回403: {server_msg[:300]}"
                                    )
                                except Exception:
                                    logger.warning(f"[403] 第{工具轮次+1}轮返回403（无法读取详情）")
                                # 刷新 token 后重试一次（仅在 token 过期导致 verify_ranking_token 失败时有效）
                                if retry < 1:
                                    headers = self._refresh_headers()
                                    await asyncio.sleep(1.5)
                                    continue
                                self._性能.记录请求(time.time() - 开始时间, False)
                                return server_msg
                            self._性能.记录请求(time.time() - 开始时间, False)
                            return self._致命错误码[resp.status]

                        if resp.status == 200:
                            # 云端代理返回 SSE 流，收集所有 chunk 拼接成响应
                            响应数据 = await self._收集云端流式响应(resp)
                            break

                        # API 不支持 tools 参数 → 降级为普通对话重试
                        if resp.status == 400 and tools_payload and not 工具不支持降级:
                            try:
                                err_text_for_check = await resp.text()
                            except (aiohttp.ClientError, UnicodeDecodeError) as e:
                                logger.debug(f"读取错误响应文本失败: {e}")
                                err_text_for_check = ""
                            if "tool" in err_text_for_check.lower():
                                logger.warning("API 不支持 tools 参数，降级为普通对话")
                                工具不支持降级 = True
                                break

                        # --- P0: 可恢复错误（5xx）走重试 ---
                        if resp.status >= 500 and retry < 1:
                            logger.warning(f"服务器错误 {resp.status}，重试...")
                            await asyncio.sleep(1)
                            continue
                        else:
                            error_text = await resp.text()
                            self._性能.记录请求(time.time() - 开始时间, False)
                            return f"[API 错误 {resp.status}]: {error_text[:300]}"

                except (aiohttp.ClientError, asyncio.TimeoutError) as e:
                    # 可恢复的网络错误，重试
                    if retry < 1:
                        logger.warning(f"网络错误，重试: {e}")
                        await asyncio.sleep(1)
                        continue
                    self._性能.记录请求(time.time() - 开始时间, False)
                    return f"[连接错误]: {str(e)}"
                except (json.JSONDecodeError, KeyError, TypeError, ValueError) as e:
                    self._性能.记录请求(time.time() - 开始时间, False)
                    return f"[未知错误]: {str(e)}"

            # 触发了 tools 降级 → 重新进入本轮（不带 tools）
            if 工具不支持降级 and 响应数据 is None:
                continue

            if 响应数据 is None:
                self._性能.记录请求(time.time() - 开始时间, False)
                return "[错误] 多次重试均失败"

            # 保存 usage 信息（非流式响应）
            _usage = 响应数据.get("usage")
            if _usage:
                self.上次usage = _usage

            choice = 响应数据["choices"][0]
            finish_reason = choice.get("finish_reason", "stop")
            message = choice.get("message", {}) or {}
            content = message.get("content") or ""
            tool_calls = message.get("tool_calls") or []

            # ===== 工具调用分支 =====
            if finish_reason == "tool_calls" and tool_executor and tool_calls:
                # 保留 assistant 工具调用消息到上下文（OpenAI 协议要求）
                complete_messages.append({
                    "role": "assistant",
                    "content": content,
                    "tool_calls": tool_calls,
                })
                # 并行执行所有工具调用
                _async_tasks = []
                _tc_infos = []
                for tc in tool_calls:
                    tc_id = tc.get("id", "")
                    fn = tc.get("function", {}) or {}
                    tool_name = fn.get("name", "")
                    args_str = fn.get("arguments", "") or "{}"
                    try:
                        tool_args = json.loads(args_str) if args_str else {}
                    except json.JSONDecodeError:
                        tool_args = {}
                    _tc_infos.append({"id": tc_id, "name": tool_name, "args": tool_args})
                    logger.info(f"工具调用(并行): {tool_name} args={tool_args}")
                    _async_tasks.append(tool_executor(tool_name, tool_args))

                # 并行等待所有结果
                _results = await asyncio.gather(*_async_tasks, return_exceptions=True)

                # 按顺序追加结果
                for info, result in zip(_tc_infos, _results):
                    if isinstance(result, Exception):
                        error_msg = f"{type(result).__name__}: {result}"
                        error_info = self._错误恢复.分析错误(error_msg, info["name"], info["args"])
                        attempt = _工具重试计数.get(info["id"], 0) + 1
                        _工具重试计数[info["id"]] = attempt
                        if attempt < self._max_tool_retries and error_info["retry_recommended"]:
                            result_str = self._错误恢复.格式化错误反馈(error_info, attempt, self._max_tool_retries)
                        else:
                            result_str = f"❌ 工具调用最终失败: {error_info['user_message']}"
                        logger.error(f"[工具执行] {info['name']} 失败(第{attempt}次): {error_msg}")
                    else:
                        result_str = str(result)
                    complete_messages.append({
                        "role": "tool",
                        "tool_call_id": info["id"],
                        "content": result_str,
                    })
                # 进入下一轮（携带工具结果再次请求模型）
                continue

            # ===== 普通文本分支 =====
            # 剥离 thinking 标签（Qwen3.5 变体 + 标准，与 generate_completion/流式方法一致）
            content = re.sub(r'lld[\s\S]*?ullets', '', content)
            content = re.sub(r'<thinking>[\s\S]*?</thinking>', '', content).strip()

            # P0: 截断续写
            if finish_reason == "length":
                logger.info("API 输出被截断，尝试续写...")
                try:
                    续写messages = complete_messages.copy()
                    续写messages.append({"role": "assistant", "content": content})
                    续写messages.append({"role": "user", "content": "你的输出被截断了，请从中断处继续完成，不要重复已输出的部分。"})
                    续写payload = {
                        "model": model_name,
                        "messages": 续写messages,
                        "temperature": temperature,
                        "max_tokens": max_tokens // 2
                    }
                    async with 会话.post(url, json=续写payload, headers=headers,
                                        timeout=aiohttp.ClientTimeout(total=120)) as 续写resp:
                        if 续写resp.status == 200:
                            续写data = await self._收集云端流式响应(续写resp)
                            续写content = 续写data["choices"][0]["message"]["content"]
                            续写content = re.sub(r'lld[\s\S]*?ullets', '', 续写content)
                            续写content = re.sub(r'<thinking>[\s\S]*?</thinking>', '', 续写content).strip()
                            content = content + 续写content
                except (aiohttp.ClientError, asyncio.TimeoutError, json.JSONDecodeError, KeyError) as e:
                    logger.exception(f"续写失败: {e}")

            self._性能.记录请求(time.time() - 开始时间, True)
            return content

        # 工具调用循环达到上限
        logger.warning(f"工具调用循环达到上限（{最大工具循环次数} 轮）")
        self._性能.记录请求(time.time() - 开始时间, True)
        return 最终内容 or "[错误] 工具调用循环达到上限"

    async def _收集云端流式响应(self, resp) -> dict:
        """收集云端代理返回的 OpenAI 格式 SSE 流，拼接为非流式响应格式。

        云端代理在 [DONE] 之后追加 billing 事件，本方法会继续读取以捕获计费信息。
        """
        content_buffer = ""
        tool_calls_acc = {}
        finish_reason = None
        usage = None
        buffer = ""
        done_flag = False
        _post_done_reads = 0  # [DONE] 后额外读取次数（用于捕获 billing 事件）

        async for raw_chunk in resp.content.iter_any():
            if done_flag:
                # [DONE] 后继续读取少量 chunk 以捕获 billing 事件
                if self.云端计费 is not None or _post_done_reads >= 8:
                    break
                _post_done_reads += 1
            buffer += raw_chunk.decode('utf-8', errors='ignore')
            while '\n' in buffer:
                line, buffer = buffer.split('\n', 1)
                line = line.strip()
                if not line or not line.startswith('data: '):
                    continue
                data_str = line[6:]
                if data_str.strip() == '[DONE]':
                    done_flag = True
                    # 不立即返回，继续处理缓冲区剩余行（billing 事件紧随 [DONE] 之后）
                    continue
                try:
                    data = json.loads(data_str)
                    # 捕获云端代理追加的 billing 事件（[DONE] 之后）
                    if data.get("type") == "billing":
                        self.云端计费 = data
                        done_flag = True
                        break
                    # [DONE] 后仅接收 billing 事件，跳过其他
                    if done_flag:
                        continue
                    if data.get("type") == "error":
                        continue
                    # usage
                    if data.get("usage"):
                        usage = data["usage"]
                    choices = data.get("choices", [])
                    if choices:
                        choice = choices[0]
                        delta = choice.get("delta", {}) or {}
                        fr = choice.get("finish_reason")
                        if fr:
                            finish_reason = fr
                        if delta.get("content"):
                            content_buffer += delta["content"]
                        # tool_calls
                        for tc in (delta.get("tool_calls") or []):
                            idx = tc.get("index", 0)
                            if idx not in tool_calls_acc:
                                tool_calls_acc[idx] = {"id": tc.get("id", ""), "type": "function", "function": {"name": "", "arguments": ""}}
                            if tc.get("id"):
                                tool_calls_acc[idx]["id"] = tc["id"]
                            fn = tc.get("function") or {}
                            if fn.get("name"):
                                tool_calls_acc[idx]["function"]["name"] += fn["name"]
                            if "arguments" in fn and fn["arguments"] is not None:
                                tool_calls_acc[idx]["function"]["arguments"] += fn["arguments"]
                except (json.JSONDecodeError, IndexError, KeyError):
                    continue

        # 构造非流式格式返回
        message = {"role": "assistant", "content": content_buffer}
        if tool_calls_acc:
            message["tool_calls"] = [tool_calls_acc[k] for k in sorted(tool_calls_acc.keys())]
        return {
            "choices": [{"message": message, "finish_reason": finish_reason or "stop"}],
            "usage": usage or {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
        }

    async def generate_completion(self, system_prompt, prefix, suffix=None, settings=None):
        """代码补全专用方法。
        :param system_prompt: 补全系统提示词
        :param prefix: 光标前的代码内容
        :param suffix: 光标后的代码内容（可选）
        :param settings: 设置字典，含 cloud_url, model_name, ranking_token 等
        :return: 补全文本字符串
        """
        try:
            if settings is None:
                settings = load_settings()

            model_name = settings.get("model_name", "qwen2.5-coder-32b-instruct")

            # 同步当前模型名用于 vision 能力检测
            self.当前模型名 = model_name
            self.云端计费 = None  # 重置云端计费信息（_收集云端流式响应 依赖此字段）
            self.上次usage = None  # 重置 token 用量信息（监控指标依赖此字段）

            # 云端代理模式：与主对话流程一致，通过云端Space转发API请求
            cloud_url = settings.get("cloud_url", "").rstrip("/")
            ranking_token = settings.get("ranking_token", "")
            if not cloud_url:
                return "[错误] 未配置云端服务地址"
            if not ranking_token:
                return "[错误] 未登录，请先登录后再使用API模型"

            # 双认证 headers：Authorization(网关 SDK Token) + X-Auth-Token(业务 ranking_token)
            headers = self._build_cloud_headers(settings)

            # 构建补全请求消息
            if suffix:
                user_content = f"<|prefix|>\n{prefix}\n<|cursor|>\n<|suffix|>\n{suffix}"
            else:
                user_content = f"<|prefix|>\n{prefix}\n<|cursor|>"

            messages = [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_content},
            ]

            # 云端 chat-stream 端点始终以流式转发到 LLM 并自动计费，
            # stop 序列等扩展字段不被云端透传，补全长度由 max_tokens 控制。
            payload = {
                "model": model_name,
                "messages": messages,
                "temperature": 0.1,
                "max_tokens": 100,
                "stream": True,
            }

            url = f"{cloud_url}/api/open/chat-stream"
            会话 = await self._获取会话()

            # 保持代码补全的快速响应特性：使用 completion_timeout（毫秒→秒），默认8秒
            _completion_timeout_ms = settings.get("completion_timeout", 8000)
            _total_timeout = max(_completion_timeout_ms / 1000, 3)  # 至少3秒

            async with 会话.post(url, json=payload, headers=headers,
                                timeout=aiohttp.ClientTimeout(total=_total_timeout)) as resp:
                if resp.status == 200:
                    # 云端代理返回 SSE 流，收集所有 chunk 拼接成非流式响应
                    响应数据 = await self._收集云端流式响应(resp)
                    self.上次usage = 响应数据.get("usage")  # 保存 token 用量（监控指标依赖此字段）
                    content = 响应数据["choices"][0]["message"]["content"] or ""
                    # 剥离 lld 标签（Qwen3.5 变体）
                    content = re.sub(r'lld[\s\S]*?ullets', '', content)
                    # 剥离 thinking 标签
                    content = re.sub(r'<thinking>[\s\S]*?</thinking>', '', content).strip()
                    return content
                else:
                    error_text = await resp.text()
                    logger.warning(f"[代码补全] 云端返回状态码 {resp.status}: {error_text[:300]}")
                    return ""

        except (aiohttp.ClientError, asyncio.TimeoutError, json.JSONDecodeError,
                KeyError, TypeError, ValueError, IndexError) as e:
            logger.warning(f"[代码补全] 请求失败，返回空字符串: {type(e).__name__}: {e}")
            return ""
        except Exception as e:
            logger.exception(f"[代码补全] 未预期异常: {e}")
            return ""

    async def 流式对话(self, messages: list, settings: dict, tools=None, tool_executor=None):
        """流式对话 - yield 每个 token chunk

        Args:
            messages: 完整的消息列表（含 system prompt）
            settings: 设置字典
            tools: OpenAI 风格的工具描述列表，传 None 时与原版行为一致
            tool_executor: async 回调 (tool_name, tool_args) -> str
        """
        model_source = settings.get("model_source", "api")
        if model_source == "local":
            # 本地模式不在此客户端处理，由外部分发
            yield "[错误] API客户端不支持本地模式流式对话"
        else:
            async for chunk in self._api_流式对话(messages, settings, tools=tools, tool_executor=tool_executor):
                yield chunk

    async def _api_流式对话(self, messages: list, settings: dict, tools=None, tool_executor=None):
        """API 模式流式对话 - OpenAI 兼容 SSE 格式

        在请求 body 中设置 stream: true，逐行解析 SSE data。
        支持 Function Calling：累积 delta 中的 tool_calls 片段，
        在 finish_reason=tool_calls 时执行工具并发起新的流式请求继续生成。
        """
        logger.debug(f"[流式对话] 准备发送, messages数={len(messages)}, 预估字符数={sum(len(str(m.get('content', ''))) for m in messages)}")
        self.云端计费 = None  # 重置云端计费信息
        self.上次usage = None  # 重置 token 用量信息（监控指标依赖此字段）
        # 多轮工具调用时，云端对每一轮请求分别计费；此处跨轮累积总成本并保留最后一轮的余额
        _累计计费成本 = 0.0
        _最新计费余额 = -1
        _捕获到任意计费 = False

        model_name = settings.get("model_name", "deepseek-chat")
        temperature = settings.get("temperature", 0.2)
        max_tokens = settings.get("max_tokens", 4096)

        # 工具模式需要更大的 token 预算（thinking推理 + 工具调用JSON + 正文输出）
        # DeepSeek等模型的 thinking token 会占用 max_tokens，4096 经常不够
        if tools and max_tokens < 8192:
            max_tokens = 8192

        # 同步当前模型名用于 vision 能力检测
        self.当前模型名 = model_name

        # 云端代理模式
        cloud_url = settings.get("cloud_url", "").rstrip("/")
        ranking_token = settings.get("ranking_token", "")
        if not cloud_url:
            yield "[错误] 未配置云端服务地址"
            return
        if not ranking_token:
            yield "[错误] 未登录，请先登录后再使用API模型"
            return

        # 双认证 headers：Authorization(网关 SDK Token) + X-Auth-Token(业务 ranking_token)
        headers = self._build_cloud_headers(settings)

        # 根据模型能力转换消息格式（vision 模型传递图片，其他模型剥除临时字段）
        prepared_messages = self._prepare_messages(messages)

        # OpenAI tools 格式封装
        tools_payload = None
        if tools:
            try:
                tools_payload = [{"type": "function", "function": tool} for tool in tools]
            except (TypeError, KeyError, ValueError) as e:
                logger.warning(f"tools 参数转换失败，降级为普通流式: {e}")
                tools_payload = None

        url = f"{cloud_url}/api/open/chat-stream"
        会话 = await self._获取会话()

        # 工具调用最大轮次可配置（默认25；-1/0/"无限" 表示不限制，兜底9999防死循环）
        _配置轮次 = settings.get("max_tool_rounds", 25)
        try:
            最大工具循环次数 = 9999 if _配置轮次 in (-1, 0, "无限", "unlimited") else int(_配置轮次)
        except (TypeError, ValueError):
            最大工具循环次数 = 25
        工具不支持降级 = False
        _工具重试计数 = {}  # tool_call_id -> 重试次数
        _截断续写次数 = 0      # token截断重试（finish_reason=="length"）
        _纯文本重提次数 = 0    # 模型输出文字不调工具时的强制重提
        _只读续跑次数 = 0      # 首轮只读不写时的续跑

        for 工具轮次 in range(最大工具循环次数):
            # ── 每轮刷新认证 headers（防止长时间工具循环中 token 过期）──
            if 工具轮次 > 0:
                headers = self._refresh_headers()
                logger.debug(f"[流式Token刷新] 第{工具轮次+1}轮工具调用，已刷新认证 headers")

            # 动态窗口控制
            _estimated = self._estimate_tokens(prepared_messages)
            _limit = self._get_model_context_limit(model_name)

            if _estimated > _limit * 0.85:
                prepared_messages = self._compress_tool_messages(prepared_messages)
                logger.warning(f"[动态窗口] token预估({_estimated})接近上限({_limit})，已压缩工具消息")
                _estimated = self._estimate_tokens(prepared_messages)

            if _estimated > _limit * 0.95:
                prepared_messages = self._drop_oldest_tool_pairs(prepared_messages)
                logger.warning(f"[动态窗口] 极端压缩，删除最早工具消息对")

            payload = {
                "model": model_name,
                "messages": prepared_messages,
                "temperature": temperature,
                "max_tokens": max_tokens,
                "stream": True,
                "stream_options": {"include_usage": True}
            }
            if tools_payload and not 工具不支持降级:
                payload["tools"] = tools_payload

            tool_calls_accumulator = {}  # {index: {"id":..., "function":{"name":..., "arguments":...}}}
            finish_reason_final = None
            assistant_content_buffer = ""
            done_flag = False
            thinking_buffer = ""
            in_thinking = False
            本轮billing捕获 = False  # 本轮是否已捕获到 billing 事件

            # 从 settings 获取超时配置（毫秒→秒），默认120秒总超时、10秒连接超时
            _chat_timeout_ms = settings.get("chat_timeout", 120000)
            _total_timeout = max(_chat_timeout_ms / 1000, 30)  # 至少30秒
            _connect_timeout = min(15, _total_timeout / 2)  # 连接超时15秒上限

            try:
                logger.debug(f"[流式对话] 正在连接 API: {url}, timeout={_total_timeout}s, 工具轮次={工具轮次}")
                async with 会话.post(url, json=payload, headers=headers,
                                    timeout=aiohttp.ClientTimeout(
                                        total=_total_timeout,
                                        sock_connect=_connect_timeout,
                                    )) as resp:
                    # 致命错误检查
                    if resp.status in self._致命错误码:
                        # 403 特殊处理：解析服务端实际错误信息，必要时刷新 token 重试
                        if resp.status == 403:
                            server_msg = self._致命错误码[resp.status]
                            try:
                                body_text = await resp.text()
                                detail = json.loads(body_text).get("detail", "")
                                if detail:
                                    server_msg = detail
                                logger.warning(
                                    f"[流式403] 第{工具轮次+1}轮返回403: {server_msg[:300]}"
                                )
                            except Exception:
                                logger.warning(f"[流式403] 第{工具轮次+1}轮返回403（无法读取详情）")
                            # 刷新 token 后重试一次
                            headers = self._refresh_headers()
                            await asyncio.sleep(1.5)
                            try:
                                async with 会话.post(url, json=payload, headers=headers,
                                                    timeout=aiohttp.ClientTimeout(
                                                        total=_total_timeout,
                                                        sock_connect=_connect_timeout,
                                                    )) as retry_resp:
                                    if retry_resp.status == 200:
                                        resp = retry_resp  # 替换为成功的响应，继续下面流程
                                    else:
                                        if retry_resp.status in self._致命错误码:
                                            # 尝试读取重试失败详情
                                            retry_msg = self._致命错误码[retry_resp.status]
                                            if retry_resp.status == 403:
                                                try:
                                                    retry_body = await retry_resp.text()
                                                    retry_detail = json.loads(retry_body).get("detail", "")
                                                    if retry_detail:
                                                        retry_msg = retry_detail
                                                except Exception:
                                                    pass
                                            yield retry_msg
                                        else:
                                            retry_text = ""
                                            try:
                                                retry_text = await retry_resp.text()
                                            except Exception:
                                                pass
                                            yield f"[API 错误 {retry_resp.status}]: {retry_text[:300]}"
                                        return
                            except Exception as e:
                                logger.warning(f"[流式403重试] 请求失败: {e}")
                                yield f"[连接错误]: 403重试失败 - {e}"
                                return
                            # 重试成功，跳过 yield，继续处理响应
                        else:
                            yield self._致命错误码[resp.status]
                            return

                    if resp.status != 200:
                        # API 不支持 tools 参数 → 降级重试
                        if resp.status == 400 and tools_payload and not 工具不支持降级:
                            try:
                                err_text_for_check = await resp.text()
                            except (aiohttp.ClientError, UnicodeDecodeError) as e:
                                logger.debug(f"读取错误响应文本失败: {e}")
                                err_text_for_check = ""
                            if "tool" in err_text_for_check.lower():
                                logger.warning("API 不支持 tools 参数，降级为普通流式")
                                工具不支持降级 = True
                                continue
                        error_text = await resp.text()
                        logger.warning(f"[流式对话] API 返回非200状态码: {resp.status}, 响应: {error_text[:300]}")
                        yield f"[API 错误 {resp.status}]: {error_text[:300]}"
                        return

                    # 逐行读取 SSE 流
                    buffer = ""
                    _post_done_reads = 0  # [DONE] 后额外读取次数（用于捕获 billing 事件）
                    # 云端在 [DONE] 之后先执行数据库计费再发送 billing 事件，存在网络延迟，
                    # 因此需放宽读取次数上限；并以"本轮是否已捕获"为准，避免多轮时被上一轮的计费值误判提前退出
                    async for raw_chunk in resp.content.iter_any():
                        if done_flag:
                            # [DONE] 后继续读取若干 chunk 以捕获紧随其后的 billing 事件
                            if 本轮billing捕获 or _post_done_reads >= 8:
                                break
                            _post_done_reads += 1
                        buffer += raw_chunk.decode('utf-8', errors='ignore')
                        while '\n' in buffer:
                            line, buffer = buffer.split('\n', 1)
                            line = line.strip()
                            if not line:
                                continue
                            if line.startswith('data: '):
                                data_str = line[6:]
                                if data_str.strip() == '[DONE]':
                                    done_flag = True
                                    # 不立即 break，继续处理缓冲区剩余行（billing 事件紧随 [DONE] 之后）
                                    continue
                                try:
                                    data_json = json.loads(data_str)
                                    # 捕获云端代理追加的 billing 事件（[DONE] 之后）
                                    if data_json.get("type") == "billing":
                                        _本轮cost = data_json.get("cost", 0) or 0
                                        _累计计费成本 += _本轮cost
                                        _最新计费余额 = data_json.get("balance", _最新计费余额)
                                        _捕获到任意计费 = True
                                        本轮billing捕获 = True
                                        # 跨轮累积总成本，余额取最后一轮
                                        self.云端计费 = {
                                            "type": "billing",
                                            "cost": _累计计费成本,
                                            "balance": _最新计费余额,
                                        }
                                        logger.debug(
                                            f"[流式计费] 第{工具轮次+1}轮捕获billing: 本轮cost={_本轮cost}, "
                                            f"累计cost={_累计计费成本}, balance={_最新计费余额}"
                                        )
                                        done_flag = True
                                        break
                                    # [DONE] 后仅接收 billing 事件，跳过其他
                                    if done_flag:
                                        continue
                                    if data_json.get("type") == "error":
                                        continue
                                    # 捕获最后一个chunk中的usage信息
                                    _chunk_usage = data_json.get("usage")
                                    if _chunk_usage:
                                        self.上次usage = _chunk_usage
                                    choice = data_json.get('choices', [{}])[0]
                                    delta = choice.get('delta', {}) or {}
                                    fr = choice.get('finish_reason')
                                    if fr:
                                        finish_reason_final = fr
                                    content = delta.get('content', '')
                                    if content:
                                        assistant_content_buffer += content
                                        # 剥离 thinking 标签片段（流式中可能跨chunk）
                                        text, thinking_buffer, in_thinking = _strip_thinking_stream(
                                            content, thinking_buffer, in_thinking
                                        )
                                        if text:
                                            yield text
                                    # 累积 tool_calls 片段
                                    delta_tool_calls = delta.get("tool_calls")
                                    if delta_tool_calls:
                                        for tc in delta_tool_calls:
                                            idx = tc.get("index", 0)
                                            if idx not in tool_calls_accumulator:
                                                tool_calls_accumulator[idx] = {
                                                    "id": tc.get("id", "") or "",
                                                    "type": "function",
                                                    "function": {"name": "", "arguments": ""},
                                                }
                                            if tc.get("id"):
                                                tool_calls_accumulator[idx]["id"] = tc["id"]
                                            fn = tc.get("function") or {}
                                            if fn.get("name"):
                                                tool_calls_accumulator[idx]["function"]["name"] += fn["name"]
                                            if "arguments" in fn and fn["arguments"] is not None:
                                                tool_calls_accumulator[idx]["function"]["arguments"] += fn["arguments"]
                                except (json.JSONDecodeError, IndexError, KeyError):
                                    continue

            except asyncio.TimeoutError:
                logger.error(f"[流式对话] API 请求超时 ({_total_timeout}s), url={url}")
                yield f"⚠ API 请求超时（{int(_total_timeout)}秒无响应），请检查网络连接或API服务状态"
                return
            except aiohttp.ClientError as e:
                logger.error(f"[流式对话] 网络连接失败: {e}")
                yield f"⚠ 网络连接失败: {e}"
                return
            except (json.JSONDecodeError, KeyError, IndexError) as e:
                logger.error(f"[流式对话] 连接异常: {type(e).__name__}: {e}")
                yield f"\n\n[连接错误]: {str(e)}"
                return

            # 流结束后刷新 thinking 缓冲区（非 thinking 状态下残留的文本需输出）
            if thinking_buffer and not in_thinking:
                yield thinking_buffer
                thinking_buffer = ""

            # 检测流式响应是否未产出任何内容
            if not assistant_content_buffer and not tool_calls_accumulator and finish_reason_final is None:
                logger.warning(f"[流式对话] 流式响应未产出任何内容, model={model_name}")

            # ===== 本轮流结束，判断是否需要执行工具 =====
            logger.info(f"[流式工具循环] 第{工具轮次+1}轮完成: finish_reason={finish_reason_final}, "
                        f"tool_calls={len(tool_calls_accumulator)}, content_len={len(assistant_content_buffer)}")
            logger.debug(f"[流式计费] 第{工具轮次+1}轮结束: 本轮billing捕获={本轮billing捕获}, "
                         f"post_done_reads={_post_done_reads}, 当前云端计费={self.云端计费}")

            if finish_reason_final == "tool_calls" and tool_executor and tool_calls_accumulator:
                tool_calls_list = [tool_calls_accumulator[i] for i in sorted(tool_calls_accumulator.keys())]
                # 保留 assistant 工具调用消息到上下文
                prepared_messages.append({
                    "role": "assistant",
                    "content": assistant_content_buffer,
                    "tool_calls": tool_calls_list,
                })
                # 并行执行所有工具调用
                _async_tasks = []
                _tc_infos = []
                for tc in tool_calls_list:
                    tc_id = tc.get("id", "")
                    fn = tc.get("function", {}) or {}
                    tool_name = fn.get("name", "")
                    args_str = fn.get("arguments", "") or "{}"
                    try:
                        tool_args = json.loads(args_str) if args_str else {}
                    except json.JSONDecodeError:
                        tool_args = {}
                    _tc_infos.append({"id": tc_id, "name": tool_name, "args": tool_args})
                    # 语义化日志：只显示操作摘要，不输出具体内容
                    logger.info(f"工具调用(流式并行): {tool_name} {_tool_log_summary(tool_name, tool_args)}")
                    _async_tasks.append(tool_executor(tool_name, tool_args))

                # 推送工具执行进度
                for tc_info in _tc_infos:
                    yield f"\n[正在执行: {tc_info['name']}...]\n"

                # 并行等待所有结果
                _results = await asyncio.gather(*_async_tasks, return_exceptions=True)

                # 按顺序追加结果
                for info, result in zip(_tc_infos, _results):
                    if isinstance(result, Exception):
                        error_msg = f"{type(result).__name__}: {result}"
                        error_info = self._错误恢复.分析错误(error_msg, info["name"], info["args"])
                        attempt = _工具重试计数.get(info["id"], 0) + 1
                        _工具重试计数[info["id"]] = attempt
                        if attempt < self._max_tool_retries and error_info["retry_recommended"]:
                            result_str = self._错误恢复.格式化错误反馈(error_info, attempt, self._max_tool_retries)
                        else:
                            result_str = f"❌ 工具调用最终失败: {error_info['user_message']}"
                        logger.error(f"[工具执行] {info['name']} 失败(第{attempt}次): {error_msg}")
                    else:
                        result_str = str(result)
                    prepared_messages.append({
                        "role": "tool",
                        "tool_call_id": info["id"],
                        "content": result_str,
                    })
                # 进入下一轮流式请求，继续 yield 后续文本
                continue

            # ===== finish_reason=length 处理：模型 token 耗尽被截断 =====
            # 典型场景：DeepSeek 等模型 thinking 占满了 max_tokens，没输出内容就被截断
            if (finish_reason_final == "length"
                and tool_executor
                and tools_payload
                and not 工具不支持降级
                and _截断续写次数 < 2):
                _截断续写次数 += 1
                # 如果有部分内容，保留到上下文
                if assistant_content_buffer.strip():
                    prepared_messages.append({
                        "role": "assistant",
                        "content": assistant_content_buffer,
                    })
                prepared_messages.append({
                    "role": "user",
                    "content": f"[系统提示] 你的响应被截断了（token不足）。请简洁地继续执行任务，直接调用工具完成修改，不要重复分析。"
                })
                logger.info(f"[流式工具循环] token截断重试({_截断续写次数}/2): finish_reason=length, content_len={len(assistant_content_buffer)}")
                yield "\n[自动继续执行...]\n"
                continue

            # ===== 纯文本响应自动重提（参考 OpenHands 响应分类器） =====
            # 条件：模型返回了纯文本（finish_reason != "tool_calls"），
            #       但工具可用且不是第一轮，说明模型应该调工具但没调
            if (finish_reason_final in ("stop", None)
                and tool_executor
                and tools_payload
                and not 工具不支持降级
                and 工具轮次 > 0  # 第一轮可能是模型在分析，允许
                and _纯文本重提次数 < 2
                and assistant_content_buffer.strip()):  # 确实有内容输出
                _纯文本重提次数 += 1
                # 保留模型的文本输出到上下文
                prepared_messages.append({
                    "role": "assistant",
                    "content": assistant_content_buffer,
                })
                # 注入强制执行指令
                prepared_messages.append({
                    "role": "user",
                    "content": f"[系统提醒 {_纯文本重提次数}/2] 你的响应没有调用任何工具。请现在立即使用 edit_file、write_plugin_file 或 batch_edit 工具执行你描述的修改。直接调用工具，不要再解释方案。"
                })
                logger.info(f"[流式工具循环] 纯文本自动重提({_纯文本重提次数}/2): 模型返回文本但未调用工具")
                yield "\n[自动继续执行...]\n"
                continue

            # 只读不写续跑（第一轮特殊处理：模型读了文件就停了）
            if (_只读续跑次数 < 1
                and 工具轮次 == 0  # 仅第一轮
                and tool_executor
                and tools_payload
                and not 工具不支持降级
                and self._should_auto_continue(prepared_messages)):
                _只读续跑次数 += 1
                prepared_messages.append({
                    "role": "user",
                    "content": f"[系统提醒 {_只读续跑次数}/1] 你只读取了文件但没有执行修改。请立即使用 edit_file 或 write_plugin_file 工具完成文件变更。"
                })
                logger.info(f"[流式工具循环] 只读不写续跑({_只读续跑次数}/1): 首轮只有读操作")
                yield "\n[自动继续执行...]\n"
                continue

            # 正常结束
            if 工具轮次 > 0:
                logger.info(f"[流式工具循环] 循环结束: 共执行{工具轮次+1}轮, 最终finish_reason={finish_reason_final}")
            return

        # 工具循环耗尽
        yield f"\n\n[提示] 工具调用循环达到上限（{最大工具循环次数} 轮），任务可能未完全完成。您可以继续对话让我完成剩余工作。"

    def _should_auto_continue(self, messages: list) -> bool:
        """检测是否需要自动续跑：有读操作但无写操作时触发"""
        has_read = False
        has_write = False
        for msg in messages:
            if msg.get("role") == "assistant" and msg.get("tool_calls"):
                for tc in msg["tool_calls"]:
                    name = tc.get("function", {}).get("name", "")
                    if name in ("read_plugin_file", "list_plugin_files"):
                        has_read = True
                    elif name in ("write_plugin_file", "edit_file", "batch_edit"):
                        has_write = True
        return has_read and not has_write

    def 获取性能报告(self) -> dict:
        """返回 API 调用性能统计"""
        return {
            "总请求数": self._性能.总请求数,
            "成功数": self._性能.成功数,
            "失败数": self._性能.失败数,
            "平均响应时间(秒)": round(self._性能.平均响应时间, 2),
            "最近请求数": len(self._性能._响应时间列表),
        }


__all__ = ["AICoderClient"]
