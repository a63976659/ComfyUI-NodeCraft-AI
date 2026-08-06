import asyncio
import logging
import sys
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

# LLM 摘要缓存（模块级）：ContextManager 每请求新建实例，实例属性缓存无法跨请求复用。
# 同一会话多轮请求丢弃的早期消息高度重叠，模块级缓存可省去重复的 LLM 摘要调用。
# OrderedDict 实现 LRU，容量上限防内存泄漏。
from collections import OrderedDict
_LLM摘要缓存: "OrderedDict[int, str]" = OrderedDict()
_LLM摘要缓存上限 = 64


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

    def get_token_count(self, text):
        """计算文本的 token 数量（已针对 Qwen3.5 校准）"""
        if self.编码器:
            raw = len(self.编码器.encode(text))
            return int(raw * _QWEN_CALIBRATION)
        # 备用估算：与 API 客户端统一为 chars×0.75（中文实测约 0.6 token/字，偏保守取 0.75）
        return int(len(text) * 0.75)

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
            # 使用较大的默认预算（模拟模型上下文窗口 * 0.9），避免过早压缩导致智能体遗忘
            max_tokens = int(settings.get("context_window", 60000) * 0.9)

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

        # Step 4: 处理早期历史 — 工具消息摘要化，普通消息保留（外层超时保护）
        try:
            compressed_early = await asyncio.wait_for(
                self._compress_early_messages(
                    early_messages, remaining_budget,
                    llm_client=llm_client, model_source=model_source
                ),
                timeout=5.0
            )
        except asyncio.TimeoutError:
            _logger.warning(
                "[上下文压缩] _compress_early_messages 整体超时(5s)，降级为文本截断"
            )
            compressed_early = self._truncate_recent(early_messages, remaining_budget)

        result = compressed_early + recent_messages
        return self._确保工具配对完整(result)

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
        """处理早期历史：工具消息压缩为一行摘要（而非删除），普通消息保留，从最新的开始保留。

        压缩策略：
        - 最近 3 个工具调用轮次的 tool 消息完整保留
        - 更早的 tool 消息压缩为一行摘要：「[历史工具调用] 第N轮: tool_name(关键参数) → 结果状态」
        - 压缩后插入工具执行历史摘要头，确保 LLM 始终能看到“已经做过什么”

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

        # 提取工具调用记录（用于生成摘要头）
        工具调用记录 = self._提取工具调用记录(messages)

        # 确定每条消息所属的轮次，并找出最近 3 个含工具调用的轮次
        轮次映射 = {}  # message index -> round number
        当前轮次 = 0
        工具调用轮次集 = set()
        for idx, msg in enumerate(messages):
            if msg.get("role") == "user":
                当前轮次 += 1
            轮次映射[idx] = 当前轮次
            if msg.get("role") == "assistant" and msg.get("tool_calls"):
                工具调用轮次集.add(当前轮次)

        排序轮次 = sorted(工具调用轮次集)
        保留完整的轮次 = set(排序轮次[-3:]) if len(排序轮次) > 3 else set(排序轮次)

        # 转换消息：工具消息按轮次分别处理，assistant 去除 tool_calls
        # 防递归：已有摘要消息直接保留，不参与压缩
        transformed = []
        preexisting_summaries = []
        for idx, msg in enumerate(messages):
            role = msg.get("role")
            content = msg.get("content", "")
            msg_轮次 = 轮次映射.get(idx, 0)

            # 防递归：已有摘要消息直接保留（强制 user 角色，避免 system 触发 chat template 校验）
            if isinstance(content, str) and content.startswith("[历史对话摘要]"):
                preexisting_summaries.append({"role": "user", "content": content})
                continue

            if role == "tool":
                if msg_轮次 in 保留完整的轮次:
                    # 最近 3 轮工具消息：保留完整内容（使用摘要化但保留详情）
                    msg_copy = dict(msg)
                    if msg_copy.get("tool_call_id") in tool_call_map:
                        msg_copy["name"] = tool_call_map[msg_copy["tool_call_id"]]
                    transformed.append(self._summarize_tool_message(msg_copy))
                else:
                    # 早期工具消息：压缩为一行摘要（而非删除）
                    tool_name = tool_call_map.get(msg.get("tool_call_id", ""), "tool")
                    状态 = "失败" if any(kw in str(content) for kw in ("错误", "失败", "Error", "error")) else "成功"
                    参数摘要 = ""
                    for r in 工具调用记录:
                        if r["工具名"] == tool_name and r["轮次"] == msg_轮次:
                            参数摘要 = r["参数摘要"]
                            break
                    one_line = f"[历史工具调用] 第{msg_轮次}轮: {tool_name}({参数摘要}) → {状态}"
                    transformed.append({"role": "tool", "content": one_line,
                                        "tool_call_id": msg.get("tool_call_id", "")})
            elif role == "assistant" and msg.get("tool_calls"):
                transformed.append({
                    "role": "assistant",
                    "content": msg.get("content", ""),
                    "tool_calls": msg["tool_calls"]
                })
            else:
                transformed.append({
                    "role": role,
                    "content": content
                })

        # 摘要消息占用的 token 从预算中扣除
        summary_budget = sum(self.get_token_count(str(m.get("content", ""))) for m in preexisting_summaries)
        effective_budget = budget_tokens - summary_budget

        # 生成工具执行摘要头（占用预算）
        工具摘要文本 = self._生成工具执行摘要(工具调用记录)
        工具摘要消息 = None
        if 工具摘要文本:
            工具摘要消息 = {"role": "user", "content": 工具摘要文本}
            工具摘要tokens = self.get_token_count(工具摘要文本)
            effective_budget -= 工具摘要tokens

        # 从最新的早期消息开始保留（成对处理 assistant(tool_calls) 和 tool 消息）
        消息分组 = self._按工具调用分组(transformed)
        kept = []
        current_tokens = 0
        for group in reversed(消息分组):
            group_tokens = sum(self.get_token_count(str(m.get("content", ""))) for m in group)
            if current_tokens + group_tokens > effective_budget:
                break
            kept = group + kept  # 保持原始顺序
            current_tokens += group_tokens

        # 组装结果前缀
        result_prefix = list(preexisting_summaries)
        if 工具摘要消息:
            result_prefix.append(工具摘要消息)

        # 如果有截断，将丢弃的消息合并为摘要而非直接丢弃
        kept_count = len(kept)
        dropped = len(transformed) - kept_count
        if dropped > 0:
            # 被丢弃的是 transformed 中最前面的 dropped 条
            dropped_messages = transformed[:dropped]

            # 优先尝试 LLM 摘要，失败时降级为文本截断
            _logger.info(f"[上下文压缩] 丢弃 {dropped} 条早期消息，触发 LLM 摘要")
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

            # 如果仍超限，移除最旧的已保留消息为摘要腾出空间（成对移除）
            while kept and current_tokens + summary_tokens > effective_budget:
                removed = kept.pop(0)
                current_tokens -= self.get_token_count(str(removed.get("content", "")))
                # 如果移除的是 assistant(tool_calls)，同时移除对应的 tool 消息
                if removed.get("role") == "assistant" and removed.get("tool_calls"):
                    orphan_ids = {tc.get("id") for tc in removed["tool_calls"] if isinstance(tc, dict) and tc.get("id")}
                    while kept and kept[0].get("role") == "tool" and kept[0].get("tool_call_id") in orphan_ids:
                        orphan_removed = kept.pop(0)
                        current_tokens -= self.get_token_count(str(orphan_removed.get("content", "")))

            # 极端情况：摘要仍超限，回退到简洁通知
            if current_tokens + summary_tokens > effective_budget:
                notice = f"[注意: 之前的 {dropped} 条对话已被压缩省略]"
                return result_prefix + [{"role": "user", "content": notice}] + kept

            return result_prefix + [{"role": "user", "content": summary_text}] + kept

        return result_prefix + kept

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
        result = {"role": "tool", "content": summarized}
        if msg.get("tool_call_id"):
            result["tool_call_id"] = msg["tool_call_id"]
        return result

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

    def _提取工具调用记录(self, messages):
        """从消息列表中提取结构化的工具调用记录，按轮次分组。

        Returns:
            list of dict: [{"轮次": N, "工具名": name, "参数摘要": summary, "状态": status}, ...]
        """
        记录列表 = []
        当前轮次 = 0
        pending_calls = {}  # tool_call_id -> record

        for msg in messages:
            role = msg.get("role")
            if role == "user":
                当前轮次 += 1
            elif role == "assistant" and msg.get("tool_calls"):
                for tc in msg["tool_calls"]:
                    if not isinstance(tc, dict):
                        continue
                    tid = tc.get("id", "")
                    func = tc.get("function", {})
                    if not isinstance(func, dict):
                        continue
                    name = func.get("name", "unknown")
                    args = func.get("arguments", "")
                    参数摘要 = self._提取参数摘要(args)
                    record = {"轮次": 当前轮次, "工具名": name, "参数摘要": 参数摘要, "状态": "已调用"}
                    if tid:
                        pending_calls[tid] = record
                    记录列表.append(record)
            elif role == "tool":
                tid = msg.get("tool_call_id", "")
                content = str(msg.get("content", ""))[:200]
                if tid in pending_calls:
                    record = pending_calls[tid]
                    if any(kw in content for kw in ("错误", "失败", "Error", "error", "异常")):
                        record["状态"] = "失败"
                    else:
                        record["状态"] = "成功"

        return 记录列表

    def _提取参数摘要(self, args_str):
        """从工具参数 JSON 字符串中提取关键参数摘要（≤30字符）。"""
        if not args_str:
            return ""
        try:
            import json
            args = json.loads(args_str) if isinstance(args_str, str) else args_str
            if isinstance(args, dict):
                # 优先提取文件路径类参数
                for key in ("file_path", "path", "filename", "pattern"):
                    if key in args:
                        val = str(args[key])
                        return val[-30:] if len(val) > 30 else val
                # 否则取第一个参数的值
                for key, val in args.items():
                    val_str = str(val)[:25]
                    return f"{key}={val_str}"
            return str(args)[:30]
        except (json.JSONDecodeError, TypeError, ValueError):
            return args_str[:30] if isinstance(args_str, str) else ""

    def _生成工具执行摘要(self, 记录列表):
        """生成工具执行历史摘要文本。每条记录约 50-80 字符，不显著增加 token 占用。"""
        if not 记录列表:
            return ""

        摘要 = "## 已执行的工具操作：\n"
        for 记录 in 记录列表:
            摘要 += f"- 第{记录['轮次']}轮: {记录['工具名']}({记录['参数摘要']}) → {记录['状态']}\n"
        return 摘要.strip()

    async def _生成LLM摘要(self, dropped_messages, llm_client, model_source):
        """调用 LLM 对被丢弃的消息生成摘要。失败时返回 None，降级为文本截断。"""
        if not llm_client or not dropped_messages:
            return None

        # 本地模型：不使用 LLM 摘要，原因：
        # 1. 摘要调用会阻塞 worker（handle_generate），上下文压缩在流式对话之前执行，
        #    摘要超时取消后 worker 仍在处理，导致 stdout 管道出现两个 reader 线程的竞态
        # 2. 被取消 generate 的 "done" 响应可能被后续 stream 的 reader 误读，
        #    造成流式输出立即终止（0 token），前端表现为"无任何回复"
        # 3. 本地模型 IDLE_TTL 卸载后，客户端 当前模型名 仍为旧值，
        #    但 worker 已无模型，摘要调用会发送无效请求
        if model_source == "local":
            return None

        # 缓存检查（模块级 LRU，可跨请求复用：同会话多轮压缩丢弃的消息高度重叠）
        cache_key = hash(tuple(
            (m.get("role", ""), m.get("content", "")[:200]) for m in dropped_messages
        ))
        if cache_key in _LLM摘要缓存:
            _LLM摘要缓存.move_to_end(cache_key)
            _logger.info("[上下文压缩] LLM 摘要命中缓存，跳过 LLM 调用")
            return _LLM摘要缓存[cache_key]

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
                timeout=3
            )

            # 检查结果是否为错误信息
            _error_prefixes = ("[错误", "[API 错误", "[连接错误", "[未知错误", "[推理错误", "[推理超时")
            if result and not any(result.strip().startswith(p) for p in _error_prefixes):
                result = result.strip()
                _logger.info(f"[上下文压缩] LLM 摘要成功，摘要长度={len(result)}")
                _LLM摘要缓存[cache_key] = result
                _LLM摘要缓存.move_to_end(cache_key)
                while len(_LLM摘要缓存) > _LLM摘要缓存上限:
                    _LLM摘要缓存.popitem(last=False)
                return result

            _logger.debug(f"[LLM摘要] LLM 返回错误或空结果，降级为文本截断: {str(result)[:100]}")
            return None
        except asyncio.TimeoutError:
            _logger.warning("[LLM摘要] 生成超时(3s)，降级为文本截断")
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
            msg_copy = {
                "role": msg.get("role", "user"),
                "content": msg.get("content", "")
            }
            if msg.get("tool_calls"):
                msg_copy["tool_calls"] = msg["tool_calls"]
            if msg.get("tool_call_id"):
                msg_copy["tool_call_id"] = msg["tool_call_id"]
            压缩结果.insert(0, msg_copy)
            当前token数 += msg_tokens

        if len(压缩结果) < len(recent_messages):
            截断数 = len(recent_messages) - len(压缩结果)
            压缩结果.insert(0, {
                "role": "user",
                "content": (
                    f"[注意: 之前的 {截断数} 条对话已被压缩省略，以节省上下文空间。"
                    f"请基于当前可见的对话继续。]"
                )
            })

        return 压缩结果

    def _按工具调用分组(self, messages):
        """将消息列表按工具调用关系分组。

        每个分组是一个原子单元：
        - assistant(tool_calls) + 其后紧跟的对应 tool 消息
        - 或独立的单条消息

        Returns:
            list[list[dict]]: 分组后的消息列表
        """
        if not messages:
            return []

        分组结果 = []
        i = 0
        while i < len(messages):
            msg = messages[i]
            if msg.get("role") == "assistant" and msg.get("tool_calls"):
                # 收集该 assistant 消息及其对应的所有 tool 响应
                工具调用ids = {tc.get("id") for tc in msg["tool_calls"] if isinstance(tc, dict) and tc.get("id")}
                group = [msg]
                j = i + 1
                while (
                    j < len(messages)
                    and messages[j].get("role") == "tool"
                    and messages[j].get("tool_call_id") in 工具调用ids
                ):
                    group.append(messages[j])
                    j += 1
                分组结果.append(group)
                i = j
            else:
                分组结果.append([msg])
                i += 1

        return 分组结果

    def _确保工具配对完整(self, messages: list) -> list:
        """确保压缩后的消息列表中 tool_calls 和 tool 响应配对完整。

        防护网校验：
        - 移除孤立的 tool 消息（没有对应的 assistant(tool_calls)）
        - 为缺失响应的 tool_call 补充空消息
        """
        if not messages:
            return messages

        需要的ids = set()
        存在的ids = set()
        # tool_call_id -> 其所属 assistant 消息的索引
        工具调用所属 = {}

        for idx, msg in enumerate(messages):
            if msg.get("role") == "assistant" and msg.get("tool_calls"):
                for tc in msg["tool_calls"]:
                    if isinstance(tc, dict) and tc.get("id"):
                        需要的ids.add(tc["id"])
                        工具调用所属[tc["id"]] = idx
            elif msg.get("role") == "tool" and msg.get("tool_call_id"):
                存在的ids.add(msg["tool_call_id"])

        # 移除孤立的 tool 消息（没有对应 assistant）
        孤立ids = 存在的ids - 需要的ids
        if 孤立ids:
            _logger.warning(f"[压缩校验] 移除 {len(孤立ids)} 个孤立 tool 消息")
            messages = [m for m in messages if not (m.get("role") == "tool" and m.get("tool_call_id") in 孤立ids)]

        # 为缺失响应的 tool_call 补充空消息
        缺失ids = 需要的ids - 存在的ids
        if 缺失ids:
            _logger.warning(f"[压缩校验] 为 {len(缺失ids)} 个缺失响应的 tool_call 补充占位消息")
            # 按 assistant 消息位置分组插入，确保顺序正确
            # 找到每个缺失 id 对应的 assistant 位置，在其后紧跟的 tool 消息之后插入
            待插入 = []  # (insert_after_index, message)
            for missing_id in 缺失ids:
                assistant_idx = 工具调用所属.get(missing_id)
                if assistant_idx is not None:
                    # 找到该 assistant 后最后一个 tool 消息的位置
                    insert_pos = assistant_idx + 1
                    while insert_pos < len(messages) and messages[insert_pos].get("role") == "tool":
                        insert_pos += 1
                    待插入.append((insert_pos, {
                        "role": "tool",
                        "tool_call_id": missing_id,
                        "content": "[已压缩]"
                    }))

            # 按插入位置从后往前插入，避免索引偏移
            待插入.sort(key=lambda x: x[0], reverse=True)
            for pos, msg in 待插入:
                messages.insert(pos, msg)

        return messages
