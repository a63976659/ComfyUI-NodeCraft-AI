"""聊天路由模块

包含：
- POST /ai-coder/chat          非流式聊天
- POST /ai-coder/chat-stream   SSE 流式聊天
"""
import asyncio
import json
import re
import time
from datetime import datetime
from pathlib import Path

from aiohttp import web

# 高性能 JSON 序列化（可选，降级到标准 json）
try:
    import orjson

    def _serialize_chunk(obj):
        return orjson.dumps(obj, default=str).decode('utf-8')
except ImportError:
    def _serialize_chunk(obj):
        return json.dumps(obj, ensure_ascii=False)

from .会话缓存 import 会话缓存
from .并发控制 import (
    _创建后台任务,
    _递增会话版本,
)
from .聊天上下文 import (
    TAB_SYSTEM_PROMPTS,  # noqa: F401  保留 re-export：历史上由本模块对外暴露，防止外部引用断裂
    _TOOL_USAGE_GUIDE,  # noqa: F401  保留 re-export：历史上由本模块对外暴露，防止外部引用断裂
    _VALID_ACTIVE_TABS,
    _build_chat_context,
    _calculate_context_health,
    _注入用户决策前端标志,
    _注入规划上下文,
)
from .文件读写操作 import save_session
from .日志配置 import 获取日志器
from .详细日志 import 记录AI输出, 记录工具调用, 记录用户输入
from .系统环境映射 import get_default_llm_path
from .路由公共 import (
    _agent_available,
    _error_response,
    _metrics_collector,
    _rate_limiter,
    _会话锁,
    _幂等缓存,
    _检查上传大小,
    _检查附件类型,
    _记忆管理器,
    llm_client,
    local_model_client,
    tool_router,
)

logger = 获取日志器("聊天路由")


try:
    from 智能体.工具路由器 import 执行工具 as _执行工具
except Exception as _e:  # 保护性导入，失败时降级为 None
    logger.error(f"导入 工具路由器.执行工具 失败: {_e}")
    _执行工具 = None


def _提取推理token数(model_source):
    """提取最近一次 LLM 调用的实际 token 数（用于监控指标）。

    API 模式：读取客户端保存的 usage（优先 total_tokens，其次 completion_tokens）；
    本地模式：worker 未返回 token 计数，回退 0。
    """
    try:
        if model_source != "local" and llm_client is not None:
            _usage = getattr(llm_client, "上次usage", None)
            if isinstance(_usage, dict):
                return int(_usage.get("total_tokens") or _usage.get("completion_tokens") or 0)
    except Exception:
        pass
    return 0


# _resolve_plugin_path 已从 文件路由 模块导入（统一实现）


# ─── 推理进度事件（inference_start / thinking 心跳） ──────────

_心跳间隔秒 = 3.0


async def _带首块心跳(源生成器, 发送心跳, 心跳间隔=_心跳间隔秒):
    """包装模型流式生成器：首个 chunk 到达前每隔 心跳间隔 秒推送一次 thinking 心跳。

    实现说明：
    - 心跳与主流写入在同一协程内顺序执行（asyncio.wait 超时轮询首块任务），
      避免 asyncio.create_task 后台任务与主循环并发 write 同一 StreamResponse 的风险；
    - 首块任务在任何退出路径（正常首chunk/模型报错/客户端断开/上层取消）
      都会在 finally 中被 cancel，源生成器随后被显式 aclose；
    - thinking/reasoning 内容已被模型客户端剥离不产出 chunk（见 API模型客户端
      _strip_thinking_stream），因此心跳恰好覆盖「完全无输出」的推理等待期。

    Args:
        源生成器: 模型客户端的流式 async generator
        发送心跳: async 回调 (payload: dict) -> bool | None，负责按传输协议写出事件；
            返回 False 表示客户端失活（WS 路径复用 _安全发送），包装器据此提前
            结束心跳循环并中止流；SSE 路径回调返回 None，不受影响
    """
    迭代器 = 源生成器.__aiter__()
    首块任务 = asyncio.ensure_future(迭代器.__anext__())
    开始时间 = time.time()
    try:
        while not 首块任务.done():
            await asyncio.wait({首块任务}, timeout=心跳间隔)
            if 首块任务.done():
                break
            发送结果 = await 发送心跳({
                "type": "status",
                "status": "thinking",
                "elapsed": int(time.time() - 开始时间),
            })
            # 仅显式 False 视为客户端失活（SSE 回调返回 None，不能用 falsy 判断）
            if 发送结果 is False:
                return  # 中止流：finally 中取消首块任务并关闭源生成器
        try:
            首块 = 首块任务.result()  # 模型异常在此抛出，交由调用方既有错误路径处理
        except StopAsyncIteration:
            return  # 空流：无任何 chunk
        yield 首块
        async for chunk in 迭代器:
            yield chunk
    finally:
        if not 首块任务.done():
            首块任务.cancel()
            try:
                await 首块任务
            except BaseException:
                pass
        try:
            await 迭代器.aclose()
        except BaseException:
            pass


def _build_tool_executor(plugin_path, tool_tracker: list = None, active_tab: str = "develop"):
    """构建绑定 plugin_path 的 tool_executor 闭包；不可用时返回 (None, None)

    Args:
        plugin_path: 插件路径
        tool_tracker: 可选的工具调用追踪列表，传入后每次工具执行都会追加工具名
        active_tab: 当前 Tab，用于工具 schema 裁剪（visualize 仅下发只读工具）
    """
    if not plugin_path or _执行工具 is None or tool_router is None:
        return None, None
    try:
        file_tools = tool_router.get_file_tools(active_tab=active_tab)
    except Exception as e:
        logger.exception(f"获取文件工具定义失败: {e}")
        return None, None

    plugin_path_str = str(plugin_path)

    async def _tool_executor(tool_name: str, tool_args: dict) -> str:
        if tool_tracker is not None:
            tool_tracker.append(tool_name)
        _工具开始 = time.time()
        try:
            _结果 = await _执行工具(tool_name, tool_args, plugin_path_str)
            记录工具调用(tool_name, tool_args, _结果, 耗时秒=time.time() - _工具开始)
            return _结果
        except Exception as ex:
            _错误文本 = f"[工具执行异常]: {type(ex).__name__}: {ex}"
            记录工具调用(tool_name, tool_args, _错误文本, 耗时秒=time.time() - _工具开始, 是否异常=True)
            return _错误文本

    return file_tools, _tool_executor


# ─── 两个聊天 handler 的公共逻辑（预校验/上下文/保存回复） ─────

def _json错误(msg, status=400):
    """聊天接口历史错误格式：{success, error}（与 _error_response 的 message 格式不同）"""
    return web.json_response({"success": False, "error": msg}, status=status)


async def _解析聊天请求(request, 错误400=_json错误):
    """公共预校验与请求体解析：上传大小 → 限流 → 字段解析 → 附件类型。

    返回 (payload, None) 或 (None, 错误响应)；错误400 允许调用方定制 400 响应格式。
    """
    # H4: 在读取请求体前预校验上传大小（附件 base64 可能超大）
    大小检查 = await _检查上传大小(request)
    if 大小检查:
        return None, 大小检查

    client_ip = request.remote or "unknown"
    if not _rate_limiter.is_allowed(client_ip):
        return None, _error_response("请求过于频繁，请稍后重试", 429)

    data = await request.json()
    # 读取 activeTab，非法值降级为 develop
    active_tab = data.get("activeTab", "develop")
    if active_tab not in _VALID_ACTIVE_TABS:
        active_tab = "develop"

    # 编辑重生成：截断位置（保留 messages[0:truncate_at] 后追加新用户消息）
    truncate_at = data.get("truncate_at")
    if truncate_at is not None:
        try:
            truncate_at = int(truncate_at)
        except (TypeError, ValueError):
            return None, 错误400("truncate_at 必须为整数")

    payload = {
        "data": data,
        "user_message": data.get("message", ""),
        "session_id": data.get("session_id"),
        "attachments": data.get("attachments", []),
        "active_tab": active_tab,
        "truncate_at": truncate_at,
    }
    if not payload["session_id"]:
        return None, 错误400("缺少 session_id")

    # H4: 附件类型白名单校验
    附件类型错误 = await _检查附件类型(payload["attachments"])
    if 附件类型错误:
        return None, 附件类型错误
    # 详细日志：记录用户输入与上传附件（不阻断请求，失败仅降级）
    try:
        记录用户输入(payload["session_id"], active_tab, payload["user_message"], payload["attachments"])
    except Exception as _日志err:
        logger.debug(f"详细日志记录失败（忽略）: {_日志err}")
    return payload, None


async def _构建聊天上下文或错误(request, payload, 错误400=_json错误):
    """公共上下文构建（捕获 ValueError，按错误语义区分状态码）

    返回 (ctx, None) 或 (None, 错误响应)。
    """
    try:
        ctx = await _build_chat_context(
            request, payload["session_id"], payload["user_message"],
            attachments=payload["attachments"],
            plugin_context=payload["data"].get("plugin_context"),
            data=payload["data"],
            active_tab=payload["active_tab"],
            truncate_at=payload["truncate_at"],
        )
        return ctx, None
    except ValueError as e:
        msg = str(e)
        if "智能体模块" in msg:
            return None, _json错误(msg, 503)
        if msg == "会话不存在":
            return None, _json错误(msg, 404)
        return None, 错误400(msg)
    except Exception as e:
        logger.exception(f"构建聊天上下文失败: {e}")
        return None, _json错误(f"上下文构建失败: {e}", 500)


def _解析本地模型路径(settings, data):
    """解析本地模型路径：请求体 > settings > LLM 目录自动检测。

    返回 (local_path, local_model_name)，均可能为空字符串。
    """
    local_model_name = settings.get("local_model_name", "")
    local_path = settings.get("local_path", "")

    # 优先使用请求中传来的模型信息（解决竞态问题）
    if data.get("local_model_name"):
        local_model_name = data["local_model_name"]

    if not local_path and local_model_name:
        # 从 LLM 目录拼接路径
        local_path = str(get_default_llm_path() / local_model_name)

    # 如果还是没有路径，尝试自动检测第一个可用模型
    if not local_path:
        llm_dir = get_default_llm_path()
        if llm_dir.exists():
            for item in llm_dir.iterdir():
                if item.is_dir() and (item / "config.json").exists():
                    local_path = str(item)
                    logger.info(f"自动选择本地模型: {item.name}")
                    break
    return local_path, local_model_name


def _流式chunk事件(chunk, 支持思考=False):
    """区分工具执行状态/思考进度和普通文本（与 WebSocket路由 保持一致）。

    返回 (事件payload, 是否计入正文)。
    """
    s = chunk.strip()
    if s.startswith("[正在执行:") and s.endswith("...]"):
        return {"type": "tool_executing", "content": chunk, "done": False}, False
    if 支持思考 and s.startswith("[思考中:") and s.endswith("...]"):
        # 思考模型（K3等）的 reasoning 阶段进度，不计入正文
        return {"type": "thinking", "content": chunk, "done": False}, False
    if 支持思考 and s.startswith("[自动继续执行"):
        return {"type": "tool_executing", "content": chunk, "done": False}, False
    return {"content": chunk, "done": False}, True


def _规划结果payload(计划结果, questions):
    """构建 planning_result SSE 事件 payload（有/无追问两种场景共用）"""
    return {
        "type": "planning_result",
        "complexity": 计划结果["complexity"],
        "needs_frontend": 计划结果.get("needs_frontend"),
        "frontend_reason": 计划结果.get("frontend_reason", ""),
        "needs_ux_optimization": 计划结果.get("needs_ux_optimization"),
        "ux_reason": 计划结果.get("ux_reason", ""),
        "needs_interactive_ui": 计划结果.get("needs_interactive_ui"),
        "interactive_ui_reason": 计划结果.get("interactive_ui_reason", ""),
        "reference_categories": 计划结果.get("reference_categories", []),
        "plan_steps": 计划结果["plan_steps"],
        "risk_notes": 计划结果.get("risk_notes", []),
        "questions": questions,
    }


async def _保存回复并提取记忆(session_id, session_data, ai_reply, user_message,
                              plugin_path, tool_call_sequence, 任务名="跨会话记忆提取"):
    """将 AI 回复存入会话并异步提取跨会话记忆/检测重复工作模式（两个 handler 共用）"""
    ai_now = datetime.now().isoformat(timespec="seconds")
    ai_msg = {"role": "assistant", "content": ai_reply, "timestamp": ai_now}
    session_data["messages"].append(ai_msg)
    # P2: 保存前递增会话版本号
    _递增会话版本(session_data)
    # P1: 使用会话锁保护写入，防止并发冲突
    async with _会话锁.获取锁(session_id):
        await asyncio.to_thread(save_session, session_data)
    # 写穿透：保存成功后用最新数据回填缓存（而非清空）
    await 会话缓存.put(session_id, session_data)

    # 异步提取跨会话记忆（不阻塞响应）
    if _记忆管理器 is not None:
        _plugin_path_str = str(plugin_path) if plugin_path else None
        _创建后台任务(asyncio.to_thread(
            _记忆管理器.自动提取记忆,
            user_message, ai_reply, _plugin_path_str, tool_call_sequence
        ), name=任务名)

        # 检测重复工作模式，若匹配则存储提示到 session_data 供下一轮注入
        if tool_call_sequence and len(tool_call_sequence) >= 2:
            try:
                匹配结果 = await asyncio.to_thread(
                    _记忆管理器.检测重复工作模式, tool_call_sequence
                )
                if 匹配结果:
                    _hint = _记忆管理器.获取工作模式注入文本(匹配结果)
                    if _hint:
                        session_data["_pending_pattern_hint"] = _hint
                        async with _会话锁.获取锁(session_id):
                            await asyncio.to_thread(save_session, session_data)
                        await 会话缓存.put(session_id, session_data)
                        logger.info(
                            f"[工作模式] 检测到 {len(匹配结果)} 个重复模式，"
                            f"提示已存储待下一轮注入"
                        )
            except Exception as e:
                logger.debug(f"工作模式检测失败（忽略）: {e}")


# ─── 非流式聊天 ───────────────────────────────────────────────

async def handle_chat(request):
    """聊天接口：RAG 检索 → 构造提示词 → 压缩上下文 → 调用 LLM → 保存回复"""
    _start = time.time()
    response_status = 200
    try:
        payload, 错误响应 = await _解析聊天请求(request)
        if 错误响应 is not None:
            return 错误响应
        data = payload["data"]
        user_message = payload["user_message"]
        session_id = payload["session_id"]
        active_tab = payload["active_tab"]

        # M8: 幂等键消费——防止网络重试导致重复提交
        idempotency_key = request.headers.get("X-Idempotency-Key", "")
        cached = _幂等缓存.检查(f"chat:{session_id}", idempotency_key)
        if cached is not None:
            return web.json_response(cached)

        # 使用公共上下文构建（捕获 ValueError，按错误语义区分状态码）
        ctx, 错误响应 = await _构建聊天上下文或错误(request, payload)
        if 错误响应 is not None:
            return 错误响应

        session_data = ctx["session_data"]
        system_prompt = ctx["system_prompt"]
        history_to_send = ctx["history_to_send"]
        settings = ctx["settings"]
        model_source = ctx["model_source"]
        plugin_path = ctx.get("plugin_path")

        # API 模式：重置 usage，确保监控埋点基于本次调用
        if model_source == "api":
            llm_client.上次usage = None

        # 工具调用追踪列表（用于工作模式提取）
        _tool_call_sequence = []

        # 根据 model_source 决定调用方式
        if model_source == "local" and local_model_client is not None:
            # ─── 本地模型处理 ───
            local_path, local_model_name = _解析本地模型路径(settings, data)

            if not local_path:
                logger.error(f"本地模型路径为空! local_model_name='{local_model_name}', settings={settings}")
                return web.json_response(
                    {
                        "success": False,
                        "error": "未检测到可用的本地模型，请在设置中配置模型路径或将模型放入 models/LLM 目录",
                    },
                    status=400,
                )

            # 模型加载（增加错误处理）
            try:
                if local_model_client.当前模型名 != Path(local_path).name:
                    # P0-4: 使用异步加载（asyncio.Lock + 双重检查）防止并发重复加载
                    await local_model_client.异步加载模型(local_path)
            except ImportError as e:
                return web.json_response(
                    {
                        "success": False,
                        "error": f"缺少依赖库: {str(e)}。请安装 transformers 和 torch",
                    },
                    status=500,
                )
            except FileNotFoundError:
                return web.json_response({"success": False, "error": f"模型路径不存在: {local_path}"}, status=404)
            except RuntimeError as e:
                return web.json_response({"success": False, "error": str(e)}, status=503)
            except Exception as e:
                return web.json_response({"success": False, "error": f"模型加载失败: {str(e)}"}, status=500)

            logger.debug(f"[工具调用] 非流式分支检查 - plugin_path={plugin_path}")
            if plugin_path:
                file_tools, _tool_executor = _build_tool_executor(plugin_path, _tool_call_sequence, active_tab)
                reply_content = await local_model_client.generate_response_with_tools(
                    system_prompt, history_to_send, tools=file_tools, tool_executor=_tool_executor
                )
            else:
                reply_content = await local_model_client.generate_response(system_prompt, history_to_send)
        else:
            # API 模式：如插件路径有效则传递 tools/tool_executor，启用 Function Calling
            file_tools, _tool_executor = _build_tool_executor(plugin_path, _tool_call_sequence, active_tab)
            reply_content = await llm_client.generate_response(
                system_prompt, history_to_send,
                tools=file_tools,
                tool_executor=_tool_executor,
            )

        # 记录模型推理指标
        try:
            _metrics_collector.record_inference((time.time() - _start) * 1000, _提取推理token数(model_source))
        except Exception:
            pass

        # 详细日志：记录 AI 完整输出
        try:
            记录AI输出(session_id, reply_content, 耗时秒=time.time() - _start,
                       token数=_提取推理token数(model_source), 渠道="非流式")
        except Exception:
            pass

        # 9. 将 AI 回复存入会话并异步提取记忆
        await _保存回复并提取记忆(
            session_id, session_data, reply_content, user_message,
            plugin_path, _tool_call_sequence,
        )

        _result = {"status": "success", "reply": reply_content}
        _幂等缓存.记录(f"chat:{session_id}", idempotency_key, _result)
        return web.json_response(_result)
    except Exception as e:
        logger.error(f"聊天请求异常: {e}", exc_info=True)
        response_status = 500
        return web.json_response({"success": False, "error": "服务器内部错误，请查看日志"}, status=500)
    finally:
        _metrics_collector.record_request("/ai-coder/chat", (time.time() - _start) * 1000, response_status)


# ─── SSE 流式对话 ─────────────────────────────────────────────

async def handle_chat_stream(request):
    """流式对话接口（SSE）：RAG 检索 → 构造提示词 → 压缩上下文 → 流式调用 LLM → 保存回复"""
    _start = time.time()
    response_status = 200
    try:
        payload, 错误响应 = await _解析聊天请求(request, 错误400=_error_response)
        if 错误响应 is not None:
            return 错误响应
        data = payload["data"]
        user_message = payload["user_message"]
        session_id = payload["session_id"]
        active_tab = payload["active_tab"]

        # M8: 幂等键消费——防止网络重试导致重复提交
        idempotency_key = request.headers.get("X-Idempotency-Key", "")
        cached = _幂等缓存.检查(f"stream:{session_id}", idempotency_key)
        if cached is not None:
            # 缓存命中：以 SSE 格式返回缓存的完整回复
            cached_response = web.StreamResponse()
            cached_response.headers['Content-Type'] = 'text/event-stream'
            cached_response.headers['Cache-Control'] = 'no-cache'
            cached_response.headers['Connection'] = 'keep-alive'
            cached_response.headers['X-Accel-Buffering'] = 'no'
            await cached_response.prepare(request)
            chunk_data = _serialize_chunk({"content": cached, "done": False})
            await cached_response.write(f"data: {chunk_data}\n\n".encode('utf-8'))
            done_data = _serialize_chunk({"content": "", "done": True})
            await cached_response.write(f"data: {done_data}\n\n".encode('utf-8'))
            return cached_response

        # 与 handle_chat 行为对齐：智能体模块不可用时立即返回 503，避免进入流式响应后仍崩溃
        if not _agent_available:
            return web.json_response(
                {"success": False, "error": "智能体模块未加载，聊天功能不可用"},
                status=503,
            )

        # 使用公共上下文构建（捕获 ValueError，按错误语义区分状态码）
        ctx, 错误响应 = await _构建聊天上下文或错误(request, payload, 错误400=_error_response)
        if 错误响应 is not None:
            return 错误响应

        session_data = ctx["session_data"]
        complete_messages = ctx["complete_messages"]
        settings = ctx["settings"]
        model_source = ctx["model_source"]
        plugin_path = ctx.get("plugin_path")

        # API 模式：重置 usage，确保监控埋点基于本次调用
        if model_source == "api":
            llm_client.上次usage = None

        # 准备 SSE 响应
        response = web.StreamResponse()
        response.headers['Content-Type'] = 'text/event-stream'
        response.headers['Cache-Control'] = 'no-cache'
        response.headers['Connection'] = 'keep-alive'
        response.headers['X-Accel-Buffering'] = 'no'
        await response.prepare(request)

        full_reply = ""

        # 推送上下文健康度（流开始前）
        _health = _calculate_context_health(
            complete_messages,
            settings.get("model", ""),
            settings.get("max_tokens", 4096)
        )
        _health_data = _serialize_chunk({"type": "context_health", **_health})
        await response.write(f"data: {_health_data}\n\n".encode('utf-8'))

        # 知识库检索失败时推送一次 kb_status 降级提醒（仅失败时推送）
        if ctx.get("kb_retrieval_failed"):
            from 智能体.工具路由器 import kb_failure_message
            _kb_reason = ctx.get("kb_failure_reason", "error") or "error"
            _kb_status_data = _serialize_chunk({
                "type": "kb_status",
                "status": "degraded",
                "reason": _kb_reason,
                "message": kb_failure_message(_kb_reason),
            })
            await response.write(f"data: {_kb_status_data}\n\n".encode('utf-8'))

        # === 规划阶段（可选） ===
        设置中启用规划 = settings.get("enable_planning", True)
        用户明确跳过 = data.get("skip_planning", False)  # 确认后的二次请求
        用户选择 = data.get("user_choices")  # 上次规划的用户选择

        async def _安全推送规划事件(payload):
            """客户端已断开（transport 关闭中）时静默跳过推送，返回 False。

            防止降级/确认路径上向已关闭的 transport 写入引发
            Cannot write to closing transport（表现为外层 ERROR 堆栈）。
            """
            transport = response.transport
            if transport is None or transport.is_closing():
                return False
            try:
                await response.write(f"data: {_serialize_chunk(payload)}\n\n".encode('utf-8'))
                return True
            except (ConnectionError, RuntimeError):
                return False

        if (设置中启用规划 and not 用户明确跳过
                and model_source == "api" and active_tab in ("develop", "optimize")):
            try:
                from 智能体.任务规划器 import 检查规划配额, 生成执行计划, 记录规划, 预筛选

                # 检查是否还有规划配额
                if 检查规划配额(session_id):
                    预筛结果 = 预筛选(user_message, active_tab, len(session_data.get("messages", [])))

                    if 预筛结果["需要规划"]:
                        # 推送“规划中”状态；客户端已断开则放弃本次规划（后续写入必然全部失败）
                        if not await _安全推送规划事件({"type": "planning_start"}):
                            return response

                        # 调用 LLM 生成规划（独立提示词）。非流式 JSON 生成在思考型
                        # 模型上动辄十几秒，原 5s 超时会导致规划从未成功、静默降级
                        计划结果 = await asyncio.wait_for(
                            生成执行计划(user_message, plugin_path, llm_client, settings),
                            timeout=60.0
                        )

                        if 计划结果:
                            # 记录规划配额消耗
                            记录规划(session_id)
                            questions = 计划结果.get("questions") or []
                            # 复杂任务或需关键决策 → 停下征询确认；其余展示后继续执行
                            需要确认 = bool(questions) or 计划结果.get("complexity") == "complex"
                            if 需要确认:
                                # 推送计划+问题，结束流等待用户确认
                                if not await _安全推送规划事件(_规划结果payload(计划结果, questions)):
                                    return response
                                await _安全推送规划事件(
                                    {"type": "planning_done", "needs_confirmation": True, "done": True}
                                )
                                return response
                            else:
                                # 有计划但无需确认 → 推送计划信息（仅展示），继续执行
                                await _安全推送规划事件(_规划结果payload(计划结果, []))
                                # 将规划上下文注入系统提示词（指导后续执行）
                                _注入规划上下文(complete_messages, 计划结果)
                                # 不return，继续正常流式对话
            except asyncio.TimeoutError:
                logger.warning("[规划阶段] 超时(60s)，自动降级进入工具循环")
                # 向前端推送状态事件，告知规划已跳过（客户端已断开时静默跳过）
                await _安全推送规划事件({
                    "type": "status",
                    "status": "planning_skipped",
                    "message": "规划超时，已直接执行",
                })
            except Exception as e:
                logger.warning(f"[规划阶段] 异常({e})，自动降级进入工具循环")

        # 如果有用户选择（来自规划确认后的二次请求），注入到消息上下文
        if 用户选择:
            选择文本 = "\n".join(f"- {q}: {a}" for q, a in 用户选择.items())
            complete_messages[-1]["content"] += f"\n\n[用户已确认的决策]\n{选择文本}"

            # 如果用户选择中包含前端需求决策，注入到系统提示词
            _注入用户决策前端标志(complete_messages, 用户选择)

        # 工具调用追踪列表（用于工作模式提取）
        _tool_call_sequence = []

        # SSE 状态事件写出（inference_start 与 thinking 心跳共用，同协程顺序写入）
        async def _推送状态事件(payload):
            _status_data = _serialize_chunk(payload)
            await response.write(f"data: {_status_data}\n\n".encode('utf-8'))

        async def _推送推理开始(模型名):
            await _推送状态事件({
                "type": "status",
                "status": "inference_start",
                "model": 模型名 or "",
                "message": "模型推理中",
            })

        try:
            if model_source == "local" and local_model_client is not None:
                # 本地模型流式
                local_path, local_model_name = _解析本地模型路径(settings, data)

                if not local_path:
                    error_data = _serialize_chunk({"error": "未检测到可用的本地模型", "done": True})
                    await response.write(f"data: {error_data}\n\n".encode('utf-8'))
                    return response

                # 模型加载
                try:
                    if local_model_client.当前模型名 != Path(local_path).name:
                        # P0-4: 使用异步加载（asyncio.Lock + 双重检查）防止并发重复加载
                        await local_model_client.异步加载模型(local_path)
                except Exception as e:
                    error_data = _serialize_chunk({"error": f"模型加载失败: {str(e)}", "done": True})
                    await response.write(f"data: {error_data}\n\n".encode('utf-8'))
                    return response

                logger.debug(
                    f"[工具调用] 流式分支检查 - plugin_path={plugin_path}, "
                    f"exists={plugin_path.exists() if plugin_path else False}"
                )
                # 即将发起模型流式调用：推送推理开始事件（前端显示状态行）
                await _推送推理开始(Path(local_path).name)

                if plugin_path and plugin_path.exists():
                    file_tools, _tool_executor = _build_tool_executor(plugin_path, _tool_call_sequence, active_tab)
                    async for chunk in _带首块心跳(local_model_client.流式对话_with_tools(
                        complete_messages, settings, tools=file_tools, tool_executor=_tool_executor
                    ), _推送状态事件):
                        # 区分工具执行状态和普通文本（与 WebSocket路由 保持一致）
                        事件, 计入正文 = _流式chunk事件(chunk)
                        if 计入正文:
                            full_reply += chunk
                        chunk_data = _serialize_chunk(事件)
                        await response.write(f"data: {chunk_data}\n\n".encode('utf-8'))
                else:
                    async for chunk in _带首块心跳(
                        local_model_client.流式对话(complete_messages, settings), _推送状态事件
                    ):
                        full_reply += chunk
                        chunk_data = _serialize_chunk({"content": chunk, "done": False})
                        await response.write(f"data: {chunk_data}\n\n".encode('utf-8'))
            else:
                # API 模式流式：如插件路径有效则启用 Function Calling
                _total_chars = sum(len(str(m.get('content', ''))) for m in complete_messages)
                logger.debug(f"[聊天路由] 发送消息总字符数: {_total_chars}, 消息数: {len(complete_messages)}")
                if _total_chars > 50000:
                    logger.warning(f"[聊天路由] 消息总字符数({_total_chars})过大，可能超出模型上下文窗口")
                file_tools, _tool_executor = _build_tool_executor(plugin_path, _tool_call_sequence, active_tab)
                # 即将发起模型流式调用：推送推理开始事件（前端显示状态行）
                await _推送推理开始(settings.get("model", ""))
                async for chunk in _带首块心跳(llm_client.流式对话(
                    complete_messages, settings,
                    tools=file_tools,
                    tool_executor=_tool_executor,
                ), _推送状态事件):
                    # 区分工具执行状态/思考进度和普通文本（与本地模型流式路径保持一致）
                    事件, 计入正文 = _流式chunk事件(chunk, 支持思考=True)
                    if 计入正文:
                        full_reply += chunk
                    chunk_data = _serialize_chunk(事件)
                    await response.write(f"data: {chunk_data}\n\n".encode('utf-8'))

            # 记录模型推理指标
            try:
                _metrics_collector.record_inference((time.time() - _start) * 1000, _提取推理token数(model_source))
            except Exception:
                pass

            # 将 AI 回复存入会话并异步提取记忆
            if full_reply:
                clean_reply = re.sub(r'<thinking>[\s\S]*?</thinking>', '', full_reply).strip()
                # 详细日志：记录 AI 完整输出
                try:
                    记录AI输出(session_id, clean_reply, 耗时秒=time.time() - _start,
                               token数=_提取推理token数(model_source))
                except Exception:
                    pass
                await _保存回复并提取记忆(
                    session_id, session_data, clean_reply, user_message,
                    plugin_path, _tool_call_sequence, 任务名="跨会话记忆提取(流式)",
                )

            # 推送最终上下文健康度（含本轮回复）
            _final_messages = complete_messages + [{"role": "assistant", "content": full_reply}]
            _final_health = _calculate_context_health(
                _final_messages,
                settings.get("model", ""),
                settings.get("max_tokens", 4096)
            )
            _final_health_data = _serialize_chunk({"type": "context_health", **_final_health})
            await response.write(f"data: {_final_health_data}\n\n".encode('utf-8'))

            # 发送完成信号
            done_data = _serialize_chunk({"content": "", "done": True})
            await response.write(f"data: {done_data}\n\n".encode('utf-8'))

            # M8: 记录幂等缓存（流式响应缓存完整回复文本）
            if full_reply:
                _幂等缓存.记录(f"stream:{session_id}", idempotency_key, full_reply)

        except Exception as e:
            logger.error(f"[聊天路由] 流式生成异常: {e}", exc_info=True)
            # 先落库已产出的部分内容，避免长任务超时/断连后几百秒的产出全部丢失（坑 98）
            if full_reply:
                try:
                    clean_reply = re.sub(r'<thinking>[\s\S]*?</thinking>', '', full_reply).strip()
                    if clean_reply:
                        ai_now = datetime.now().isoformat(timespec="seconds")
                        session_data["messages"].append({
                            "role": "assistant",
                            "content": clean_reply + "\n\n[系统提示] 本次回复因异常中断，以上为已产出的部分内容。",
                            "timestamp": ai_now,
                        })
                        _递增会话版本(session_data)
                        async with _会话锁.获取锁(session_id):
                            await asyncio.to_thread(save_session, session_data)
                        await 会话缓存.put(session_id, session_data)
                        logger.info(f"[聊天路由] 异常中断，已保存部分回复({len(clean_reply)}字符)")
                except Exception as save_err:
                    logger.error(f"[聊天路由] 保存部分回复失败: {save_err}")
            error_data = _serialize_chunk({"error": str(e), "done": True})
            await response.write(f"data: {error_data}\n\n".encode('utf-8'))

        return response
    except Exception as e:
        response_status = 500
        return _error_response(str(e), 500)
    finally:
        _metrics_collector.record_request("/ai-coder/chat-stream", (time.time() - _start) * 1000, response_status)


def register_聊天路由(routes):
    """注册聊天端点"""
    routes.post("/ai-coder/chat")(handle_chat)
    routes.post("/ai-coder/chat-stream")(handle_chat_stream)
