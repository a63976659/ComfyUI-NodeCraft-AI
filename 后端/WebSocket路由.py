"""WebSocket 路由模块

包含：
- GET /ai-coder/ws    WebSocket 实时通信端点
- _handle_ws_chat()   WebSocket 聊天消息处理
- 连接管理器           带超时清理的连接管理（P2-5）
"""
import asyncio
import json
import time
import weakref
from datetime import datetime
from pathlib import Path

import aiohttp
from aiohttp import web

from .会话缓存 import 会话缓存
from .并发控制 import _递增会话版本
from .文件读写操作 import save_session
from .日志配置 import 获取日志器
from .系统环境映射 import get_default_llm_path
from .聊天路由 import _build_chat_context, _build_tool_executor, _创建后台任务, _带首块心跳
from .路由公共 import (
    _metrics_collector,
    _会话锁,
    _记忆管理器,
    llm_client,
    local_model_client,
)

logger = 获取日志器(__name__)

# 流式/高频 send 的背压超时（秒）：慢客户端接收缓冲满时 send 会无限阻塞，
# 进而拖垮 LLM 流式消费；超过该时长视为客户端失活
_SEND_TIMEOUT = 5.0


async def _安全发送(ws, payload: dict, timeout: float = _SEND_TIMEOUT) -> bool:
    """带超时保护的 ws.send_json（背压保护）。

    超时视为客户端失活：记录日志、关闭该连接并返回 False，
    调用方据此跳出流式循环（已生成的 full_reply 仍会走保存会话逻辑，
    与现有 ws.closed 中断分支行为对齐）。
    """
    try:
        await asyncio.wait_for(ws.send_json(payload), timeout=timeout)
        return True
    except asyncio.TimeoutError:
        logger.warning(
            "WebSocket 发送超时(%ss)，客户端疑似失活，主动关闭连接", timeout
        )
        try:
            # close 本身会等待关闭握手，同样加超时防止二次阻塞
            await asyncio.wait_for(
                ws.close(code=1011, message="发送超时".encode("utf-8")),
                timeout=timeout,
            )
        except Exception:
            pass
        return False
    except (ConnectionResetError, RuntimeError) as e:
        # 连接已断开时 send 直接抛错，无需再关闭
        logger.debug("WebSocket 发送失败（连接已断开）: %s", e)
        return False


class 连接管理器:
    """WebSocket 连接管理器（P2-5：超时清理机制）

    - 记录每个连接最后活跃时间
    - 定期扫描，关闭空闲超时的僵尸连接
    - 防止连接堆积导致内存增长
    """

    IDLE_TIMEOUT = 300  # 5 分钟无消息视为僵尸连接
    SCAN_INTERVAL = 60  # 每 60 秒扫描一次

    def __init__(self):
        # 使用普通 dict 记录 ws -> 最后活跃时间戳；连接断开时显式移除
        self._connections = {}
        self._cleanup_task = None

    def 注册连接(self, ws):
        self._connections[ws] = time.time()
        if self._cleanup_task is None or self._cleanup_task.done():
            try:
                self._cleanup_task = asyncio.create_task(self._定期清理())
            except RuntimeError:
                # 没有运行中的事件循环时跳过（测试场景）
                self._cleanup_task = None

    def 更新活跃时间(self, ws):
        if ws in self._connections:
            self._connections[ws] = time.time()

    def 移除连接(self, ws):
        self._connections.pop(ws, None)

    def __contains__(self, ws):
        return ws in self._connections

    def __len__(self):
        return len(self._connections)

    def __iter__(self):
        return iter(list(self._connections.keys()))

    async def _定期清理(self):
        """每 SCAN_INTERVAL 秒扫描一次，关闭空闲超时连接"""
        while True:
            try:
                await asyncio.sleep(self.SCAN_INTERVAL)
                now = time.time()
                stale = [
                    ws for ws, t in list(self._connections.items())
                    if now - t > self.IDLE_TIMEOUT
                ]
                for ws in stale:
                    try:
                        await ws.close(code=1000, message=b"\xe7\xa9\xba\xe9\x97\xb2\xe8\xb6\x85\xe6\x97\xb6")
                    except Exception:
                        pass
                    self._connections.pop(ws, None)
                    try:
                        _metrics_collector.decrement_connections(ws=True)
                    except Exception:
                        pass
                    logger.warning(
                        "WebSocket 连接因空闲超时被关闭，超时阈值=%ss", self.IDLE_TIMEOUT
                    )
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.exception("WebSocket 清理任务异常: %s", e)


# 全局连接管理器实例
_ws_manager = 连接管理器()
# 兼容旧引用：保留 WeakSet 作为只读视图（部分模块可能引用）
_ws_connections = weakref.WeakSet()


async def websocket_handler(request):
    """WebSocket 实时通信端点"""
    ws = web.WebSocketResponse(heartbeat=30)
    await ws.prepare(request)

    _ws_connections.add(ws)
    _ws_manager.注册连接(ws)
    _metrics_collector.increment_connections(ws=True)

    try:
        async for msg in ws:
            # 每次收到消息都更新活跃时间，避免被误判为僵尸连接
            _ws_manager.更新活跃时间(ws)

            if msg.type == aiohttp.WSMsgType.TEXT:
                try:
                    data = json.loads(msg.data)
                except json.JSONDecodeError:
                    await ws.send_json({"type": "error", "message": "无效 JSON"})
                    continue

                msg_type = data.get("type")

                # 聊天消息
                if msg_type == "chat":
                    session_id = data.get("session_id")
                    message = data.get("message", "")
                    await _handle_ws_chat(ws, request, session_id, message, data)

                # 心跳（高频路径，加背压超时保护）
                elif msg_type == "ping":
                    await _安全发送(ws, {"type": "pong"})

            elif msg.type in (aiohttp.WSMsgType.ERROR, aiohttp.WSMsgType.CLOSE):
                break
    finally:
        _ws_connections.discard(ws)
        # 仅当未被清理任务移除时才递减连接数，避免重复递减
        if ws in _ws_manager:
            _ws_manager.移除连接(ws)
            _metrics_collector.decrement_connections(ws=True)

    return ws


async def _handle_ws_chat(ws, request, session_id, message, data=None):
    """处理 WebSocket 聊天消息"""
    if data is None:
        data = {}

    if not session_id:
        await ws.send_json({"type": "error", "message": "缺少 session_id"})
        return

    try:
        # 复用公共上下文构建
        active_tab = data.get("active_tab", "develop")
        ctx = await _build_chat_context(
            request, session_id, message,
            attachments=data.get("attachments", []),
            plugin_context=data.get("plugin_context"),
            data=data,
            active_tab=active_tab
        )

        session_data = ctx["session_data"]
        complete_messages = ctx["complete_messages"]
        settings = ctx["settings"]
        model_source = ctx["model_source"]

        full_reply = ""

        # WS 状态事件写出（inference_start 与 thinking 心跳共用，同协程顺序写入）
        # 统一走 _安全发送 背压保护；返回 False 表示客户端失活（_带首块心跳 据此中止流）
        async def _推送状态事件(payload):
            if ws.closed:
                return False
            return await _安全发送(ws, payload)

        async def _推送推理开始(模型名):
            return await _推送状态事件({
                "type": "status",
                "status": "inference_start",
                "model": 模型名 or "",
                "message": "模型推理中",
            })

        if model_source == "local" and local_model_client is not None:
            # 本地模型流式
            local_model_name = settings.get("local_model_name", "")
            local_path = settings.get("local_path", "")

            if data.get("local_model_name"):
                local_model_name = data["local_model_name"]

            if not local_path and local_model_name:
                local_path = str(get_default_llm_path() / local_model_name)

            if not local_path:
                llm_dir = get_default_llm_path()
                if llm_dir.exists():
                    for item in llm_dir.iterdir():
                        if item.is_dir() and (item / "config.json").exists():
                            local_path = str(item)
                            break

            if not local_path:
                await ws.send_json({"type": "error", "message": "未检测到可用的本地模型"})
                return

            try:
                if local_model_client.当前模型名 != Path(local_path).name:
                    # P0-4: 使用异步加载（asyncio.Lock + 双重检查）防止并发重复加载
                    await local_model_client.异步加载模型(local_path)
            except Exception as e:
                await ws.send_json({"type": "error", "message": f"模型加载失败: {str(e)}"})
                return

            # 检查是否有插件路径，决定是否启用工具调用
            plugin_path = ctx.get("plugin_path")
            effective_plugin_path = plugin_path if (plugin_path and plugin_path.exists()) else None

            # 即将发起模型流式调用：推送推理开始事件（发送失败即中止本次流式处理）
            if not await _推送推理开始(Path(local_path).name):
                return

            if effective_plugin_path:
                file_tools, _tool_executor = _build_tool_executor(effective_plugin_path, active_tab=active_tab)
                async for chunk in _带首块心跳(local_model_client.流式对话_with_tools(
                    complete_messages, settings, tools=file_tools, tool_executor=_tool_executor
                ), _推送状态事件):
                    if ws.closed:
                        break
                    # 区分工具执行状态和普通文本（发送均带背压超时，失败即中断流）
                    if chunk.strip().startswith("[正在执行:") and chunk.strip().endswith("...]"):
                        if not await _安全发送(ws, {"type": "tool_executing", "content": chunk, "done": False}):
                            break
                    else:
                        full_reply += chunk
                        if not await _安全发送(ws, {"type": "chunk", "content": chunk, "done": False}):
                            break
            else:
                async for chunk in _带首块心跳(
                    local_model_client.流式对话(complete_messages, settings), _推送状态事件
                ):
                    if ws.closed:
                        break
                    full_reply += chunk
                    if not await _安全发送(ws, {"type": "chunk", "content": chunk, "done": False}):
                        break
        else:
            # API 模式流式：如插件路径有效则启用 Function Calling
            plugin_path = ctx.get("plugin_path")
            file_tools, _tool_executor = _build_tool_executor(plugin_path, active_tab=active_tab)
            # 即将发起模型流式调用：推送推理开始事件（发送失败即中止本次流式处理）
            if not await _推送推理开始(settings.get("model", "")):
                return
            async for chunk in _带首块心跳(llm_client.流式对话(
                complete_messages, settings,
                tools=file_tools,
                tool_executor=_tool_executor,
            ), _推送状态事件):
                if ws.closed:
                    break
                # 工具执行/思考进度标记转为状态事件，不混入正文（与 SSE 路径保持一致）
                _chunk_s = chunk.strip()
                if _chunk_s.startswith("[正在执行:") and _chunk_s.endswith("...]"):
                    if not await _安全发送(ws, {"type": "tool_executing", "content": chunk, "done": False}):
                        break
                elif _chunk_s.startswith("[思考中:") and _chunk_s.endswith("...]"):
                    if not await _安全发送(ws, {"type": "thinking", "content": chunk, "done": False}):
                        break
                else:
                    full_reply += chunk
                    if not await _安全发送(ws, {"type": "chunk", "content": chunk, "done": False}):
                        break

        if not ws.closed:
            await _安全发送(ws, {"type": "done", "content": "", "done": True})

        # 本地 API Key 直连改造后不再发送 billing 事件（llm_client 也不再挂载 云端计费 字段）

        # 保存完整回复到会话
        if full_reply:
            import re
            clean_reply = re.sub(r'<thinking>[\s\S]*?</thinking>', '', full_reply).strip()
            ai_now = datetime.now().isoformat(timespec="seconds")
            ai_msg = {"role": "assistant", "content": clean_reply, "timestamp": ai_now}
            session_data["messages"].append(ai_msg)
            # P2: 保存前递增会话版本号（与聊天路由写法对齐，
            # 否则并发时缓存 put 可能被版本守卫拒绝导致回填失效）
            _递增会话版本(session_data)
            # P1: 使用会话锁保护写入，防止并发冲突
            async with _会话锁.获取锁(session_id):
                await asyncio.to_thread(save_session, session_data)
            # 写穿透：保存成功后用最新数据回填缓存，与 HTTP 聊天路由行为一致
            await 会话缓存.put(session_id, session_data)

            # 异步提取跨会话记忆（不阻塞响应）
            if _记忆管理器 is not None:
                _plugin_path = ctx.get("plugin_path")
                _plugin_path_str = str(_plugin_path) if _plugin_path else None
                _创建后台任务(asyncio.to_thread(
                    _记忆管理器.自动提取记忆,
                    message, clean_reply, _plugin_path_str
                ))

    except ValueError as e:
        if not ws.closed:
            await ws.send_json({"type": "error", "message": str(e)})
    except Exception as e:
        if not ws.closed:
            await ws.send_json({"type": "error", "message": str(e)})


def register_WebSocket路由(routes):
    """注册 WebSocket 端点"""
    routes.get('/ai-coder/ws')(websocket_handler)
