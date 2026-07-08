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

from .路由公共 import (
    _metrics_collector, local_model_client, llm_client, _记忆管理器,
)
from .文件读写操作 import save_session
from .系统环境映射 import get_default_llm_path
from .日志配置 import 获取日志器
from .聊天路由 import _build_chat_context, _build_tool_executor, _非流式云端记录消耗, _创建后台任务
from .token计费 import 本地扣减NCA余额, 获取token对应账户
from .token计费 import TOKENS_PER_CREDIT as _TOKENS_PER_CREDIT

logger = 获取日志器(__name__)


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

    # 认证：首条消息必须是 auth
    authenticated = False
    user_info = None

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

                # 认证消息
                if msg_type == "auth":
                    authenticated = True  # 单用户模式
                    await ws.send_json({"type": "auth_ok", "username": "default"})
                    continue

                # 聊天消息
                if msg_type == "chat":
                    session_id = data.get("session_id")
                    message = data.get("message", "")
                    await _handle_ws_chat(ws, request, session_id, message, user_info, data)

                # 心跳
                elif msg_type == "ping":
                    await ws.send_json({"type": "pong"})

            elif msg.type in (aiohttp.WSMsgType.ERROR, aiohttp.WSMsgType.CLOSE):
                break
    finally:
        _ws_connections.discard(ws)
        # 仅当未被清理任务移除时才递减连接数，避免重复递减
        if ws in _ws_manager:
            _ws_manager.移除连接(ws)
            _metrics_collector.decrement_connections(ws=True)

    return ws


async def _handle_ws_chat(ws, request, session_id, message, user_info, data=None):
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

            if effective_plugin_path:
                file_tools, _tool_executor = _build_tool_executor(effective_plugin_path)
                async for chunk in local_model_client.流式对话_with_tools(
                    complete_messages, settings, tools=file_tools, tool_executor=_tool_executor
                ):
                    if ws.closed:
                        break
                    # 区分工具执行状态和普通文本
                    if chunk.strip().startswith("[正在执行:") and chunk.strip().endswith("...]"): 
                        await ws.send_json({"type": "tool_executing", "content": chunk, "done": False})
                    else:
                        full_reply += chunk
                        await ws.send_json({"type": "chunk", "content": chunk, "done": False})
            else:
                async for chunk in local_model_client.流式对话(complete_messages, settings):
                    if ws.closed:
                        break
                    full_reply += chunk
                    await ws.send_json({"type": "chunk", "content": chunk, "done": False})
        else:
            # API 模式流式：如插件路径有效则启用 Function Calling
            plugin_path = ctx.get("plugin_path")
            file_tools, _tool_executor = _build_tool_executor(plugin_path)
            async for chunk in llm_client.流式对话(
                complete_messages, settings,
                tools=file_tools,
                tool_executor=_tool_executor,
            ):
                if ws.closed:
                    break
                full_reply += chunk
                await ws.send_json({"type": "chunk", "content": chunk, "done": False})

        if not ws.closed:
            await ws.send_json({"type": "done", "content": "", "done": True})

        # 计费信息：直接使用云端返回结果（云端单源可信）
        if model_source != "local":
            try:
                云端计费 = getattr(llm_client, "云端计费", None)
                if 云端计费:
                    cost_credits = 云端计费.get("cost", 0)
                    cloud_balance = 云端计费.get("balance", 0)
                    if not ws.closed:
                        await ws.send_json({
                            "type": "billing",
                            "cost": cost_credits,
                            "balance": cloud_balance,
                        })
                    logger.info(f"[WS计费] session={session_id}, 扣费={cost_credits}积分, 余额={cloud_balance}")
            except Exception as _billing_err:
                logger.warning(f"[WS计费] 处理失败(不影响对话): {_billing_err}")

        # 保存完整回复到会话
        if full_reply:
            import re
            clean_reply = re.sub(r'<thinking>[\s\S]*?</thinking>', '', full_reply).strip()
            ai_now = datetime.now().isoformat(timespec="seconds")
            ai_msg = {"role": "assistant", "content": clean_reply, "timestamp": ai_now}
            session_data["messages"].append(ai_msg)
            await asyncio.to_thread(save_session, session_data)

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
