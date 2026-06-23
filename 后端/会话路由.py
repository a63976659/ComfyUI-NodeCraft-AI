"""会话路由模块

包含：
- GET  /ai-coder/sessions             获取会话列表
- POST /ai-coder/sessions             创建新会话
- DELETE /ai-coder/sessions/{id}      删除会话
- GET  /ai-coder/sessions/{id}/messages  获取会话消息
"""
import asyncio
import time
from datetime import datetime

from aiohttp import web

from .路由公共 import (
    _check_auth, _error_response, _rate_limiter, _metrics_collector,
    _分页参数, _分页响应,
)
from .文件读写操作 import (
    load_sessions_list, load_session, save_session, delete_session, create_session,
    _VALID_SESSION_TYPES,
)


async def handle_get_sessions(request):
    """获取所有会话列表（支持分页：?page=1&page_size=20，最大 page_size=100）

    支持按界面类型过滤：?type=develop|optimize|visualize（无效值则忽略过滤）。
    """
    _start = time.time()
    try:
        auth_error = await _check_auth(request)
        if auth_error:
            return auth_error
        # 从 query string 读取 type 参数，无效值传 None 即不过滤
        type_param = request.query.get('type', None)
        if type_param is not None and type_param not in _VALID_SESSION_TYPES:
            type_param = None
        sessions = await asyncio.to_thread(load_sessions_list, session_type=type_param)
        page, page_size, paginated = _分页参数(request)
        payload = _分页响应(sessions, page, page_size, paginated, list_key="sessions")
        return web.json_response(payload)
    except Exception as e:
        return web.json_response({"success": False, "error": str(e)}, status=500)
    finally:
        _metrics_collector.record_request("/ai-coder/sessions", (time.time() - _start) * 1000, 200)


async def handle_create_session(request):
    """创建新会话"""
    try:
        client_ip = request.remote or "unknown"
        if not _rate_limiter.is_allowed(client_ip):
            return _error_response("请求过于频繁，请稍后重试", 429)

        auth_error = await _check_auth(request)
        if auth_error:
            return auth_error

        data = await request.json()
        title = data.get("title", "新会话")

        if len(title) > 500:
            return _error_response("会话标题长度不能超过500个字符")
        plugin_folder = data.get("plugin_folder", "")

        # 读取 type 字段并校验，非法值默认 "develop"
        session_type = data.get("type", "develop")
        if session_type not in _VALID_SESSION_TYPES:
            session_type = "develop"

        # P1-1：同步 I/O 放入线程池
        session_data = await asyncio.to_thread(
            create_session,
            title=title,
            plugin_folder=plugin_folder,
            session_type=session_type,
        )
        return web.json_response({"session": session_data})
    except Exception as e:
        return web.json_response({"success": False, "error": str(e)}, status=500)


async def handle_delete_session(request):
    """删除指定会话及其消息记录文件，可选同时删除关联的插件文件夹"""
    try:
        auth_error = await _check_auth(request)
        if auth_error:
            return auth_error
        session_id = request.match_info["id"]

        # 解析可选请求体（DELETE 请求可能没有 body）
        delete_folder = False
        try:
            data = await request.json()
            delete_folder = bool(data.get("delete_folder", False))
        except Exception:
            pass  # 无 body 时跳过

        # P1-1：同步文件删除放入线程池
        await asyncio.to_thread(delete_session, session_id, delete_folder=delete_folder)
        return web.json_response({"success": True})
    except Exception as e:
        return web.json_response({"success": False, "error": str(e)}, status=500)


async def handle_update_session_title(request):
    """更新指定会话的标题"""
    try:
        client_ip = request.remote or "unknown"
        if not _rate_limiter.is_allowed(client_ip):
            return _error_response("请求过于频繁，请稍后重试", 429)

        auth_error = await _check_auth(request)
        if auth_error:
            return auth_error

        session_id = request.match_info["id"]
        data = await request.json()
        new_title = (data.get("title") or "").strip()

        if not new_title:
            return _error_response("会话标题不能为空")
        if len(new_title) > 500:
            return _error_response("会话标题长度不能超过500个字符")

        # P1-1：同步 I/O 放入线程池
        session_data = await asyncio.to_thread(load_session, session_id)
        if session_data is None:
            return web.json_response({"success": False, "error": "会话不存在"}, status=404)

        session_data["title"] = new_title
        session_data["updated_at"] = datetime.now().isoformat(timespec="seconds")
        # P1-1：同步 I/O 放入线程池
        await asyncio.to_thread(save_session, session_data)
        return web.json_response({"status": "success", "session": {
            "id": session_data.get("id"),
            "title": session_data.get("title"),
            "created_at": session_data.get("created_at", ""),
            "updated_at": session_data.get("updated_at", ""),
        }})
    except Exception as e:
        return web.json_response({"success": False, "error": str(e)}, status=500)


async def handle_get_messages(request):
    """获取指定会话的消息列表（支持分页：?page=1&page_size=20，最大 page_size=100）

    向后兼容：未传递 page/page_size 时返回完整 messages 列表。
    """
    try:
        auth_error = await _check_auth(request)
        if auth_error:
            return auth_error
        session_id = request.match_info["id"]
        # P1-1：同步 I/O 放入线程池
        session_data = await asyncio.to_thread(load_session, session_id)
        if session_data is None:
            return web.json_response({"success": False, "error": "会话不存在"}, status=404)
        messages = session_data.get("messages", [])
        page, page_size, paginated = _分页参数(request)
        payload = _分页响应(messages, page, page_size, paginated, list_key="messages")
        return web.json_response(payload)
    except Exception as e:
        return web.json_response({"success": False, "error": str(e)}, status=500)


def register_会话路由(routes):
    """注册会话管理端点"""
    routes.get("/ai-coder/sessions")(handle_get_sessions)
    routes.post("/ai-coder/sessions")(handle_create_session)
    routes.delete("/ai-coder/sessions/{id}")(handle_delete_session)
    routes.put("/ai-coder/sessions/{id}/title")(handle_update_session_title)
    routes.get("/ai-coder/sessions/{id}/messages")(handle_get_messages)
