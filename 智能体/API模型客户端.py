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

logger = 获取日志器("API模型客户端")


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
                # 剥离临时字段，只保留 role/content
                converted.append({"role": msg.get("role"), "content": msg.get("content", "")})
        return converted

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

        base_url = settings.get("base_url", "https://api.openai.com/v1")
        model_name = settings.get("model_name", "qwen2.5-coder-32b-instruct")
        api_key = settings.get("api_key", "")
        temperature = settings.get("temperature", 0.2)
        max_tokens = settings.get("max_tokens", 4096)

        # 同步当前模型名用于 vision 能力检测
        self.当前模型名 = model_name

        headers = {"Content-Type": "application/json"}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"

        # 根据模型能力转换消息格式（vision 模型传递图片，其他模型剥除临时字段）
        prepared_history = self._prepare_messages(messages_history)
        complete_messages = [
            {"role": "system", "content": system_prompt},
            *prepared_history
        ]

        url = f"{base_url.rstrip('/')}/chat/completions"
        会话 = await self._获取会话()

        # OpenAI tools 格式封装
        tools_payload = None
        if tools:
            try:
                tools_payload = [{"type": "function", "function": tool} for tool in tools]
            except Exception as e:
                logger.warning(f"tools 参数转换失败，降级为普通对话: {e}")
                tools_payload = None

        最大工具循环次数 = 15
        工具不支持降级 = False
        最终内容 = ""

        for 工具轮次 in range(最大工具循环次数):
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
                        # --- P0: 致命错误分类（不重试）---
                        if resp.status in self._致命错误码:
                            self._性能.记录请求(time.time() - 开始时间, False)
                            return self._致命错误码[resp.status]

                        if resp.status == 200:
                            响应数据 = await resp.json()
                            break

                        # API 不支持 tools 参数 → 降级为普通对话重试
                        if resp.status == 400 and tools_payload and not 工具不支持降级:
                            try:
                                err_text_for_check = await resp.text()
                            except Exception:
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
                except Exception as e:
                    self._性能.记录请求(time.time() - 开始时间, False)
                    return f"[未知错误]: {str(e)}"

            # 触发了 tools 降级 → 重新进入本轮（不带 tools）
            if 工具不支持降级 and 响应数据 is None:
                continue

            if 响应数据 is None:
                self._性能.记录请求(time.time() - 开始时间, False)
                return "[错误] 多次重试均失败"

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
                for tc in tool_calls:
                    tc_id = tc.get("id", "")
                    fn = tc.get("function", {}) or {}
                    tool_name = fn.get("name", "")
                    args_str = fn.get("arguments", "") or "{}"
                    try:
                        tool_args = json.loads(args_str) if args_str else {}
                    except json.JSONDecodeError:
                        tool_args = {}
                    logger.info(f"工具调用: {tool_name} args={tool_args}")
                    try:
                        tool_result = await tool_executor(tool_name, tool_args)
                    except Exception as e:
                        tool_result = f"[工具执行错误]: {e}"
                    complete_messages.append({
                        "role": "tool",
                        "tool_call_id": tc_id,
                        "content": str(tool_result),
                    })
                # 进入下一轮（携带工具结果再次请求模型）
                continue

            # ===== 普通文本分支 =====
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
                            续写data = await 续写resp.json()
                            续写content = 续写data["choices"][0]["message"]["content"]
                            续写content = re.sub(r'<thinking>[\s\S]*?</thinking>', '', 续写content).strip()
                            content = content + 续写content
                except Exception as e:
                    logger.exception(f"续写失败: {e}")

            self._性能.记录请求(time.time() - 开始时间, True)
            return content

        # 工具调用循环达到上限
        logger.warning(f"工具调用循环达到上限（{最大工具循环次数} 轮）")
        self._性能.记录请求(time.time() - 开始时间, True)
        return 最终内容 or "[错误] 工具调用循环达到上限"

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
        base_url = settings.get("base_url", "https://api.openai.com/v1")
        model_name = settings.get("model_name", "qwen2.5-coder-32b-instruct")
        api_key = settings.get("api_key", "")
        temperature = settings.get("temperature", 0.2)
        max_tokens = settings.get("max_tokens", 4096)

        # 同步当前模型名用于 vision 能力检测
        self.当前模型名 = model_name

        headers = {"Content-Type": "application/json"}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"

        # 根据模型能力转换消息格式（vision 模型传递图片，其他模型剥除临时字段）
        prepared_messages = self._prepare_messages(messages)

        # OpenAI tools 格式封装
        tools_payload = None
        if tools:
            try:
                tools_payload = [{"type": "function", "function": tool} for tool in tools]
            except Exception as e:
                logger.warning(f"tools 参数转换失败，降级为普通流式: {e}")
                tools_payload = None

        url = f"{base_url.rstrip('/')}/chat/completions"
        会话 = await self._获取会话()

        最大工具循环次数 = 15
        工具不支持降级 = False

        for 工具轮次 in range(最大工具循环次数):
            payload = {
                "model": model_name,
                "messages": prepared_messages,
                "temperature": temperature,
                "max_tokens": max_tokens,
                "stream": True
            }
            if tools_payload and not 工具不支持降级:
                payload["tools"] = tools_payload

            tool_calls_accumulator = {}  # {index: {"id":..., "function":{"name":..., "arguments":...}}}
            finish_reason_final = None
            assistant_content_buffer = ""
            done_flag = False

            try:
                async with 会话.post(url, json=payload, headers=headers,
                                    timeout=aiohttp.ClientTimeout(total=180)) as resp:
                    # 致命错误检查
                    if resp.status in self._致命错误码:
                        yield self._致命错误码[resp.status]
                        return

                    if resp.status != 200:
                        # API 不支持 tools 参数 → 降级重试
                        if resp.status == 400 and tools_payload and not 工具不支持降级:
                            try:
                                err_text_for_check = await resp.text()
                            except Exception:
                                err_text_for_check = ""
                            if "tool" in err_text_for_check.lower():
                                logger.warning("API 不支持 tools 参数，降级为普通流式")
                                工具不支持降级 = True
                                continue
                        error_text = await resp.text()
                        yield f"[API 错误 {resp.status}]: {error_text[:300]}"
                        return

                    # 逐行读取 SSE 流
                    buffer = ""
                    async for raw_chunk in resp.content.iter_any():
                        if done_flag:
                            break
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
                                    break
                                try:
                                    data_json = json.loads(data_str)
                                    choice = data_json.get('choices', [{}])[0]
                                    delta = choice.get('delta', {}) or {}
                                    fr = choice.get('finish_reason')
                                    if fr:
                                        finish_reason_final = fr
                                    content = delta.get('content', '')
                                    if content:
                                        assistant_content_buffer += content
                                        # 剥离 thinking 标签片段（流式中可能跨chunk）
                                        yield content
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
                yield "\n\n[错误: 流式响应超时(180秒)]"
                return
            except (aiohttp.ClientError, Exception) as e:
                yield f"\n\n[连接错误]: {str(e)}"
                return

            # ===== 本轮流结束，判断是否需要执行工具 =====
            if finish_reason_final == "tool_calls" and tool_executor and tool_calls_accumulator:
                tool_calls_list = [tool_calls_accumulator[i] for i in sorted(tool_calls_accumulator.keys())]
                # 保留 assistant 工具调用消息到上下文
                prepared_messages.append({
                    "role": "assistant",
                    "content": assistant_content_buffer,
                    "tool_calls": tool_calls_list,
                })
                for tc in tool_calls_list:
                    tc_id = tc.get("id", "")
                    fn = tc.get("function", {}) or {}
                    tool_name = fn.get("name", "")
                    args_str = fn.get("arguments", "") or "{}"
                    try:
                        tool_args = json.loads(args_str) if args_str else {}
                    except json.JSONDecodeError:
                        tool_args = {}
                    logger.info(f"工具调用(流式): {tool_name} args={tool_args}")
                    try:
                        tool_result = await tool_executor(tool_name, tool_args)
                    except Exception as e:
                        tool_result = f"[工具执行错误]: {e}"
                    prepared_messages.append({
                        "role": "tool",
                        "tool_call_id": tc_id,
                        "content": str(tool_result),
                    })
                # 进入下一轮流式请求，继续 yield 后续文本
                continue

            # 正常结束
            return

        # 工具循环耗尽
        yield f"\n\n[提示] 工具调用循环达到上限（{最大工具循环次数} 轮）"

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
