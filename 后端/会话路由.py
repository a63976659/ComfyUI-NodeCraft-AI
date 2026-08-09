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

from .会话缓存 import 会话缓存
from .文件读写操作 import (
    _VALID_SESSION_TYPES,
    create_session,
    delete_session,
    load_session,
    load_sessions_list,
    load_settings,
    save_session,
)
from .路由公共 import (
    _error_response,
    _metrics_collector,
    _rate_limiter,
    _会话锁,
    _分页参数,
    _分页响应,
)

# 消息接口强制分页的默认页大小（无分页参数时默认返回最近 N 条）
_默认消息页大小 = 50


async def handle_get_sessions(request):
    """获取所有会话列表（支持分页：?page=1&page_size=20，最大 page_size=100）

    支持按界面类型过滤：?type=develop|optimize|visualize（无效值则忽略过滤）。
    """
    _start = time.time()
    try:
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
        # 会话已删除，缓存条目必须失效（数据不复存在，不适用写穿透）
        await 会话缓存.invalidate(session_id)
        return web.json_response({"success": True})
    except Exception as e:
        return web.json_response({"success": False, "error": str(e)}, status=500)


async def handle_update_session_title(request):
    """更新指定会话的标题"""
    try:
        client_ip = request.remote or "unknown"
        if not _rate_limiter.is_allowed(client_ip):
            return _error_response("请求过于频繁，请稍后重试", 429)

        session_id = request.match_info["id"]
        data = await request.json()
        new_title = (data.get("title") or "").strip()

        if not new_title:
            return _error_response("会话标题不能为空")
        if len(new_title) > 500:
            return _error_response("会话标题长度不能超过500个字符")

        # P1: 使用会话锁防止并发写入冲突
        async with _会话锁.获取锁(session_id):
            # P1-1：同步 I/O 放入线程池
            session_data = await asyncio.to_thread(load_session, session_id)
            if session_data is None:
                return web.json_response({"success": False, "error": "会话不存在"}, status=404)

            session_data["title"] = new_title
            session_data["updated_at"] = datetime.now().isoformat(timespec="seconds")
            # P1-1：同步 I/O 放入线程池
            await asyncio.to_thread(save_session, session_data)
        # 写穿透：保存成功后用最新数据回填缓存，避免标题修改后缓存长时间脏读
        await 会话缓存.put(session_id, session_data)

        return web.json_response({"status": "success", "session": {
            "id": session_data.get("id"),
            "title": session_data.get("title"),
            "created_at": session_data.get("created_at", ""),
            "updated_at": session_data.get("updated_at", ""),
        }})
    except Exception as e:
        return web.json_response({"success": False, "error": str(e)}, status=500)


async def handle_get_messages(request):
    """获取指定会话的消息列表

    - 窗口模式 ?recent=1：返回最近 _默认消息页大小 条（首屏性能优化）
    - 增量加载 ?after_index=N：返回索引大于 N 的消息（游标 = 消息在会话中的
      数组索引；消息无唯一 id，故用索引位置作游标），最多 page_size 条
    - 显式分页参数 ?page=1&page_size=20（最大 100）：保持原有按页切片行为
    - 无任何参数：向后兼容旧契约，全量返回 messages

    响应始终携带 total 与 start_index（返回片段首条消息在全量列表中的索引，
    供前端换算编辑重生成等需要服务端绝对索引的场景）。
    """
    try:
        session_id = request.match_info["id"]
        # P1-1：同步 I/O 放入线程池
        session_data = await asyncio.to_thread(load_session, session_id)
        if session_data is None:
            return web.json_response({"success": False, "error": "会话不存在"}, status=404)
        messages = session_data.get("messages", [])
        total = len(messages)

        # 窗口模式：显式 ?recent=1 时返回最近 N 条（首屏行为，由参数显式开启）
        if request.query.get("recent") == "1":
            page_size = _默认消息页大小
            start = max(0, total - page_size)
            total_pages = (total + page_size - 1) // page_size if total > 0 else 1
            # 上下文健康度：基于全量历史离线估算，供首屏初始化指示器
            # （此前仅流式聊天推送，会话加载/切换/刷新后指示器恒 0%）；
            # 计算失败不阻断消息接口，仅缺省该字段
            try:
                from .聊天上下文 import _calculate_context_health  # 延迟导入避免循环依赖
                _settings = await asyncio.to_thread(load_settings)
                context_health = await asyncio.to_thread(
                    _calculate_context_health,
                    messages,
                    _settings.get("model_name", ""),
                    _settings.get("max_tokens", 4096),
                )
            except Exception:
                context_health = None
            _payload = {
                "messages": messages[start:],
                "total": total,
                "page": total_pages,  # 语义：最近一页（最后一页）
                "page_size": page_size,
                "total_pages": total_pages,
                "start_index": start,
            }
            if context_health is not None:
                _payload["context_health"] = context_health
            return web.json_response(_payload)

        # 增量加载：返回 after_index 之后的消息
        if "after_index" in request.query:
            try:
                after_index = int(request.query.get("after_index"))
            except (TypeError, ValueError):
                return web.json_response(
                    {"success": False, "error": "after_index 必须为整数"}, status=400
                )
            _, page_size, _ = _分页参数(request, default_page_size=_默认消息页大小)
            start = max(0, after_index + 1)
            sliced = messages[start:start + page_size]
            return web.json_response({
                "messages": sliced,
                "total": total,
                "start_index": start,
                "has_more": start + len(sliced) < total,
            })

        page, page_size, paginated = _分页参数(request, default_page_size=_默认消息页大小)
        if paginated:
            # 显式分页：保持原有按页切片行为
            payload = _分页响应(messages, page, page_size, paginated, list_key="messages")
            payload["start_index"] = min((page - 1) * page_size, total)
            return web.json_response(payload)

        # 无任何参数：恢复旧 API 契约，全量返回 messages
        return web.json_response({
            "messages": messages,
            "total": total,
            "start_index": 0,
        })
    except Exception as e:
        return web.json_response({"success": False, "error": str(e)}, status=500)


def register_会话路由(routes):
    """注册会话管理端点"""
    routes.get("/ai-coder/sessions")(handle_get_sessions)
    routes.post("/ai-coder/sessions")(handle_create_session)
    routes.delete("/ai-coder/sessions/{id}")(handle_delete_session)
    routes.put("/ai-coder/sessions/{id}/title")(handle_update_session_title)
    routes.get("/ai-coder/sessions/{id}/messages")(handle_get_messages)
