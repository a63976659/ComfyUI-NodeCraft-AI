import sys
import asyncio
import logging
from pathlib import Path

_plugin_root = str(Path(__file__).parent.parent.resolve())
if _plugin_root not in sys.path:
    sys.path.insert(0, _plugin_root)
from 后端.文件读写操作 import load_settings
from .工具结果摘要器 import 摘要化工具结果

try:
    import tiktoken
    _TIKTOKEN_AVAILABLE = True
except ImportError:
    _TIKTOKEN_AVAILABLE = False

_logger = logging.getLogger("NodeCraftAI.记忆与上下文压缩")


# Qwen3.5 tokenizer 校准系数
# cl100k_base (GPT-4) vocab 100K，Qwen3.5 vocab 152K
# Qwen3.5 对中文编码效率更高，同等文本约少 20% tokens
_QWEN_CALIBRATION = 0.8


class ContextManager:
    """上下文压缩管理器 - 无状态工具类"""

    def __init__(self):
        if _TIKTOKEN_AVAILABLE:
            try:
                self.编码器 = tiktoken.get_encoding("cl100k_base")
            except (ValueError, OSError) as e:
                _logger.debug(f"tiktoken 初始化失败，使用估算模式: {e}")
                self.编码器 = None
        else:
            self.编码器 = None
        self._摘要缓存 = {}

    def get_token_count(self, text):
        """计算文本的 token 数量（已针对 Qwen3.5 校准）"""
        if self.编码器:
            raw = len(self.编码器.encode(text))
            return int(raw * _QWEN_CALIBRATION)
        # 备用估算：中文约 1.5 token/字（Qwen3.5），英文约 1 token/词
        return int(len(text) * 1.5)

    async def compress_history(self, messages: list, max_tokens: int = 4096, reserved_recent: int = 2,
                               llm_client=None, model_source: str = "api") -> list:
        """优先级感知的上下文压缩。

        压缩优先级（从高到低）：
        1. 最近 N 轮对话（user + assistant 对）— 完整保留
        2. 工具调用消息（role="tool" 和含 tool_calls 的 assistant）— 使用摘要版本
        3. 早期对话历史 — 按 token 预算分配，超限时调用 LLM 生成摘要

        Args:
            messages: 完整会话历史
            max_tokens: token 预算上限
            reserved_recent: 完整保留的最近轮次数（1轮 = 1组 user+assistant）
            llm_client: LLM 客户端实例（API 或本地模型客户端），用于生成摘要
            model_source: 模型来源标识（"api" 或 "local"）

        Returns:
            list: 压缩后的消息列表
        """
        if max_tokens is None:
            settings = load_settings()
            max_tokens = int(settings.get("max_tokens", 4096) * 0.7)

        if not messages:
            return []

        # Step 1: 分离最近 N 轮和早期历史
        recent_messages, early_messages = self._split_recent(messages, reserved_recent)

        # Step 2: 计算最近消息占用的 token
        recent_tokens = sum(self.get_token_count(str(m.get("content", ""))) for m in recent_messages)

        # Step 3: 剩余预算给早期历史
        remaining_budget = max_tokens - recent_tokens

        if remaining_budget <= 0:
            # 极端情况：最近消息已超预算，截断最近消息中最早的
            return self._truncate_recent(recent_messages, max_tokens)

        # Step 4: 处理早期历史 — 工具消息摘要化，普通消息保留
        compressed_early = await self._compress_early_messages(
            early_messages, remaining_budget,
            llm_client=llm_client, model_source=model_source
        )

        return compressed_early + recent_messages

    def _split_recent(self, messages, n_rounds):
        """从末尾倒推，找到最近 n_rounds 轮的 user+assistant 对。

        assistant 消息后可能跟着 tool 消息（同一轮工具调用的结果），这些也属于"最近轮"。

        Returns:
            (recent_messages, early_messages)
        """
        if n_rounds <= 0 or not messages:
            return [], list(messages)

        round_count = 0
        split_index = len(messages)

        for i in range(len(messages) - 1, -1, -1):
            if messages[i].get("role") == "user":
                round_count += 1
                if round_count == n_rounds:
                    split_index = i
                    break

        # 找不到足够的轮次，全部视为最近
        if round_count < n_rounds:
            return list(messages), []

        recent_messages = list(messages[split_index:])
        early_messages = list(messages[:split_index])
        return recent_messages, early_messages

    async def _compress_early_messages(self, messages, budget_tokens, llm_client=None, model_source="api"):
        """处理早期历史：工具消息摘要化，普通消息保留，从最新的开始保留。

        超出预算时停止，被截断的部分用 LLM 摘要（失败时降级为文本截断）。
        已有的摘要消息不参与压缩（防递归）。
        """
        if not messages:
            return []

        # 构建 tool_call_id -> 工具名 映射，用于工具消息摘要
        tool_call_map = {}
        for msg in messages:
            if msg.get("role") == "assistant" and msg.get("tool_calls"):
                for tc in msg["tool_calls"]:
                    if not isinstance(tc, dict):
                        continue
                    tid = tc.get("id")
                    func = tc.get("function", {})
                    name = func.get("name") if isinstance(func, dict) else None
                    if tid and name:
                        tool_call_map[tid] = name

        # 转换消息：工具摘要化、assistant 去除 tool_calls
        # 防递归：已有摘要消息直接保留，不参与压缩
        transformed = []
        preexisting_summaries = []
        for msg in messages:
            role = msg.get("role")
            content = msg.get("content", "")

            # 防递归：已有摘要消息直接保留
            if isinstance(content, str) and content.startswith("[历史对话摘要]"):
                preexisting_summaries.append({"role": role, "content": content})
                continue

            if role == "tool":
                msg_copy = dict(msg)
                if msg_copy.get("tool_call_id") in tool_call_map:
                    msg_copy["name"] = tool_call_map[msg_copy["tool_call_id"]]
                transformed.append(self._summarize_tool_message(msg_copy))
            elif role == "assistant" and msg.get("tool_calls"):
                transformed.append({
                    "role": "assistant",
                    "content": msg.get("content", "")
                })
            else:
                transformed.append({
                    "role": role,
                    "content": content
                })

        # 摘要消息占用的 token 从预算中扣除
        summary_budget = sum(self.get_token_count(str(m.get("content", ""))) for m in preexisting_summaries)
        effective_budget = budget_tokens - summary_budget

        # 从最新的早期消息开始保留
        kept = []
        current_tokens = 0
        for msg in reversed(transformed):
            msg_tokens = self.get_token_count(str(msg.get("content", "")))
            if current_tokens + msg_tokens > effective_budget:
                break
            kept.append(msg)
            current_tokens += msg_tokens

        # 如果有截断，将丢弃的消息合并为摘要而非直接丢弃
        dropped = len(transformed) - len(kept)
        if dropped > 0:
            # 被丢弃的是 transformed 中最前面的 dropped 条
            dropped_messages = transformed[:dropped]

            # 优先尝试 LLM 摘要，失败时降级为文本截断
            llm_summary = await self._生成LLM摘要(dropped_messages, llm_client, model_source)

            if llm_summary:
                summary_text = f"[历史对话摘要] {llm_summary}"
                summary_entries = None  # LLM 摘要是整体性的，无法拆分
            else:
                # 降级：文本截断
                summary_entries = self._generate_summary_entries(dropped_messages)
                summary_text = "[历史对话摘要] " + " ".join(summary_entries)

            summary_tokens = self.get_token_count(summary_text)

            # 文本截断模式：如果摘要后仍然超限，丢弃最早的摘要条目
            if summary_entries is not None:
                while len(summary_entries) > 1 and current_tokens + summary_tokens > effective_budget:
                    summary_entries.pop(0)
                    summary_text = "[历史对话摘要] " + " ".join(summary_entries)
                    summary_tokens = self.get_token_count(summary_text)

            # 如果仍超限，移除最旧的已保留消息为摘要腾出空间
            while kept and current_tokens + summary_tokens > effective_budget:
                removed = kept.pop()
                current_tokens -= self.get_token_count(str(removed.get("content", "")))

            # 极端情况：摘要仍超限，回退到简洁通知
            if current_tokens + summary_tokens > effective_budget:
                notice = f"[注意: 之前的 {dropped} 条对话已被压缩省略]"
                return preexisting_summaries + [{"role": "system", "content": notice}] + list(reversed(kept))

            return preexisting_summaries + [{"role": "system", "content": summary_text}] + list(reversed(kept))

        return preexisting_summaries + list(reversed(kept))

    def _summarize_tool_message(self, msg):
        """对 tool 消息进行摘要化。

        从消息中提取 tool_name（优先使用 msg.name，否则从 tool_call_id 推断）和 content，
        使用 摘要化工具结果 生成摘要。
        """
        content = str(msg.get("content", ""))
        tool_name = msg.get("name")
        if not tool_name:
            tool_name = msg.get("tool_call_id") or "tool"
        summarized = 摘要化工具结果(tool_name, content)
        return {"role": "tool", "content": summarized}

    def _generate_summary_entries(self, messages):
        """生成早期对话历史的摘要条目列表（轻量级，不调用 LLM）。

        每条 user/assistant 消息提取前 100 字符，保留角色和时间戳信息（如果有）。
        """
        entries = []
        for msg in messages:
            role = msg.get("role", "unknown")
            content = str(msg.get("content", "")).strip()
            if not content:
                continue

            # 截取前 100 字符
            snippet = content[:100]
            if len(content) > 100:
                snippet += "..."

            # 角色映射
            if role == "user":
                label = "用户"
            elif role == "assistant":
                label = "AI"
            elif role == "tool":
                label = "工具结果"
            elif role == "system":
                label = "系统"
            else:
                label = role

            # 保留时间戳信息（如果有）
            timestamp = msg.get("timestamp")
            if timestamp:
                entries.append(f"[{timestamp}] {label}: {snippet}")
            else:
                entries.append(f"{label}: {snippet}")

        return entries

    async def _生成LLM摘要(self, dropped_messages, llm_client, model_source):
        """调用 LLM 对被丢弃的消息生成摘要。失败时返回 None，降级为文本截断。"""
        if not llm_client or not dropped_messages:
            return None

        # 本地模型：检查是否已加载
        if model_source == "local":
            if not getattr(llm_client, '当前模型名', None):
                return None

        # 缓存检查
        cache_key = hash(tuple(
            (m.get("role", ""), m.get("content", "")[:200]) for m in dropped_messages
        ))
        if cache_key in self._摘要缓存:
            return self._摘要缓存[cache_key]

        try:
            # 构建摘要请求
            summary_prompt = (
                "请用简洁的中文总结以下对话的关键信息，"
                "包括讨论的主要问题、做出的决定、创建/修改的文件等。"
                "不超过200字。\n\n"
            )
            for msg in dropped_messages:
                role = msg.get("role", "")
                content = msg.get("content", "")[:500]
                summary_prompt += f"[{role}] {content}\n"

            messages = [{"role": "user", "content": summary_prompt}]
            system_prompt = "你是一个对话摘要助手。"

            # 调用 LLM（API 和本地模型客户端接口一致）
            result = await asyncio.wait_for(
                llm_client.generate_response(system_prompt, messages),
                timeout=15
            )

            # 检查结果是否为错误信息
            _error_prefixes = ("[错误", "[API 错误", "[连接错误", "[未知错误", "[推理错误", "[推理超时")
            if result and not any(result.strip().startswith(p) for p in _error_prefixes):
                result = result.strip()
                self._摘要缓存[cache_key] = result
                return result

            _logger.debug(f"[LLM摘要] LLM 返回错误或空结果，降级为文本截断: {str(result)[:100]}")
            return None
        except asyncio.TimeoutError:
            _logger.warning("[LLM摘要] 生成超时(15s)，降级为文本截断")
            return None
        except Exception as e:
            _logger.warning(f"[LLM摘要] 生成失败: {e}，降级为文本截断")
            return None

    def _truncate_recent(self, recent_messages, max_tokens):
        """极端情况：最近消息已超预算，从最早的开始截断。"""
        if not recent_messages:
            return []

        压缩结果 = []
        当前token数 = 0

        for msg in reversed(recent_messages):
            msg_tokens = self.get_token_count(str(msg.get("content", "")))
            if 当前token数 + msg_tokens > max_tokens:
                break
            压缩结果.insert(0, {
                "role": msg.get("role", "user"),
                "content": msg.get("content", "")
            })
            当前token数 += msg_tokens

        if len(压缩结果) < len(recent_messages):
            截断数 = len(recent_messages) - len(压缩结果)
            压缩结果.insert(0, {
                "role": "system",
                "content": (
                    f"[注意: 之前的 {截断数} 条对话已被压缩省略，以节省上下文空间。"
                    f"请基于当前可见的对话继续。]"
                )
            })

        return 压缩结果
