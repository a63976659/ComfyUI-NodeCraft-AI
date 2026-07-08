"""设置与模型路由模块

包含：
- GET  /ai-coder/settings              获取设置
- POST /ai-coder/settings              保存设置
- GET  /ai-coder/model-capabilities    模型能力
- POST /ai-coder/unload-model          卸载本地模型
- POST /ai-coder/reset-model-status    重置模型状态
- GET  /ai-coder/local-models          扫描本地模型
"""
from aiohttp import web

import asyncio

from .路由公共 import (
    _error_response, _success_response, _rate_limiter,
    llm_client, local_model_client,
    _分页参数, _分页响应, _记忆管理器,
)
from .文件读写操作 import load_settings, save_settings
from .系统环境映射 import get_default_llm_path
from .日志配置 import 获取日志器

logger = 获取日志器("设置与模型路由")


# ─── 文件夹选择对话框 ────────────────────────────────────────

def _弹出文件夹选择对话框(initial_dir: str) -> str:
    """在独立线程中弹出 tkinter 文件夹选择对话框。

    返回所选文件夹的绝对路径；用户取消时返回空字符串。
    """
    try:
        import tkinter as tk
        from tkinter import filedialog
    except Exception as e:
        logger.warning(f"tkinter 不可用: {e}")
        return ""

    selected = {"path": ""}

    def _run():
        root = None
        try:
            root = tk.Tk()
            root.withdraw()
            try:
                root.attributes("-topmost", True)
            except Exception:
                pass
            try:
                root.update_idletasks()
            except Exception:
                pass
            folder = filedialog.askdirectory(
                title="选择本地模型文件夹",
                initialdir=initial_dir or None,
                mustexist=True,
            )
            if folder:
                selected["path"] = folder
        finally:
            try:
                if root is not None:
                    root.destroy()
            except Exception:
                pass

    import threading
    th = threading.Thread(target=_run, daemon=True)
    th.start()
    th.join(timeout=300)  # 最多等待 5 分钟，超时则视为取消
    return selected["path"]


async def handle_select_folder(request):
    """打开系统原生文件夹选择对话框，返回所选路径。

    仅服务端本机可用（ComfyUI 通常运行在用户本地）；远程访问时
    对话框会出现在服务器端，前端无法见到，故应自行禁用按钮。
    """
    try:
        client_ip = request.remote or "unknown"
        if not _rate_limiter.is_allowed(client_ip):
            return _error_response("请求过于频繁，请稍后重试", 429)

        try:
            data = await request.json()
        except Exception:
            data = {}
        initial_dir = (data or {}).get("initial_dir") or ""
        if not initial_dir:
            try:
                initial_dir = str(get_default_llm_path())
            except Exception:
                initial_dir = ""

        # tkinter 阻塞调用放入线程池，避免阻塞 aiohttp 事件循环
        path = await asyncio.to_thread(_弹出文件夹选择对话框, initial_dir)
        return web.json_response({"success": True, "path": path or ""})
    except Exception as e:
        logger.exception(f"文件夹选择失败: {e}")
        return web.json_response({"success": False, "path": "", "error": str(e)}, status=500)


# ─── 设置 ─────────────────────────────────────────────────

async def handle_get_settings(request):
    """获取当前设置（含 default_local_path）"""
    try:
        # P1-1：同步 I/O 放入线程池
        settings = await asyncio.to_thread(load_settings)
        settings["default_local_path"] = str(get_default_llm_path())
        return web.json_response(settings)
    except Exception as e:
        logger.exception(f"[诊断] 获取设置异常: {e}")
        return web.json_response({"success": False, "error": "服务器内部错误，请查看日志"}, status=500)


async def handle_save_settings(request):
    """保存设置"""
    try:
        client_ip = request.remote or "unknown"
        if not _rate_limiter.is_allowed(client_ip):
            return _error_response("请求过于频繁，请稍后重试", 429)

        data = await request.json()
        # 切换到 API 模型时校验会员权限（无进阶版/高级版会员不可用）
        # tier_info 为 None 表示无法确定（未识别账户或云端不可达）→ 降级放行
        # 免费体验模型：basic 会员也可使用，跳过 API 权限检查
        if data.get("model_source") == "api":
            try:
                from .token计费 import 是否免费模型
                _settings_model = data.get("model_name", "")
                _is_free = await 是否免费模型(_settings_model)
                if not _is_free:
                    from .计费代理 import 检查API权限
                    tier_info = await 检查API权限(request)
                    if tier_info is not None and not tier_info.get("api_available", False):
                        return web.json_response({
                            "success": False,
                            "code": "NO_API_PERMISSION",
                            "error": "您没有使用 API 模型的权限，请购买进阶版或高级版会员"
                        }, status=403)
            except Exception:
                pass  # 权限检查异常时不阻断，由后端聊天路由作最终防线
        # 移除前端附加的非持久化字段
        data.pop("default_local_path", None)
        # P1-1：同步 I/O 放入线程池
        await asyncio.to_thread(save_settings, data)
        return web.json_response({"success": True})
    except Exception as e:
        return web.json_response({"success": False, "error": str(e)}, status=500)


# ─── 模型管理 ─────────────────────────────────────────────

async def handle_model_capabilities(request):
    """返回当前配置模型的能力信息及模型注册表能力列表"""
    try:
        settings = await asyncio.to_thread(load_settings)
        # 允许前端以 query 参数临时覆盖（例如切换模型后立即查询但设置未保存场景）
        model_source = request.query.get("model_source") or settings.get("model_source", "api")
        model_name = request.query.get("model_name") or settings.get("model_name", "")

        supports_vision = False
        if model_source == "api" and model_name:
            # 复用模型客户端的检测逻辑
            try:
                if llm_client is not None:
                    llm_client.当前模型名 = model_name
                    supports_vision = llm_client._supports_vision()
            except Exception:
                supports_vision = False

        # 获取模型注册表能力列表
        from 智能体.模型能力注册表 import 模型能力注册表
        registry = 模型能力注册表()
        registry_models = registry.列出可用模型()

        return _success_response({
            "model_source": model_source,
            "model_name": model_name,
            "supports_vision": supports_vision,
            # 文本文件内容始终支持（后端会解码 base64 并内联到消息）
            "supports_file_content": True,
            # 模型注册表能力列表
            "available_models": registry_models,
        })
    except Exception as e:
        return _error_response(f"获取模型能力失败: {str(e)}", 500)


async def handle_unload_model(request):
    """卸载本地模型释放显存"""
    try:
        client_ip = request.remote or "unknown"
        if not _rate_limiter.is_allowed(client_ip):
            return _error_response("请求过于频繁，请稍后重试", 429)

        if local_model_client is not None:
            # P0-4: 使用异步卸载（与加载共享同一 asyncio.Lock）防止与并发加载发生竞态
            await local_model_client.异步卸载模型()
            return web.json_response({"success": True, "message": "本地模型已卸载，显存已释放"})
        return web.json_response({"success": False, "error": "模型客户端未初始化"})
    except Exception as e:
        return web.json_response({"success": False, "error": str(e)}, status=500)


async def handle_reset_model_status(request):
    """清除模型失败黑名单，允许重新加载"""
    try:
        client_ip = request.remote or "unknown"
        if not _rate_limiter.is_allowed(client_ip):
            return _error_response("请求过于频繁，请稍后重试", 429)

        data = await request.json()
        model_name = data.get("model_name", None)
        if local_model_client is not None:
            local_model_client.重置模型状态(model_name)
            return web.json_response({"success": True})
        return web.json_response({"success": False, "error": "模型客户端未初始化"})
    except Exception as e:
        return web.json_response({"success": False, "error": str(e)}, status=500)


# ─── 本地模型扫描 ────────────────────────────────────────

async def handle_get_local_models(request):
    """扫描本地 LLM 目录，返回可用模型列表（支持分页：?page=1&page_size=20，最大 page_size=100）"""
    try:
        llm_path = get_default_llm_path()
        models = []

        if llm_path.exists():
            for item in llm_path.iterdir():
                if item.is_dir():
                    # 检查是否是有效的模型目录（包含 config.json 或 .gguf 文件）
                    has_config = (item / "config.json").exists()
                    has_gguf = any(item.glob("*.gguf"))
                    if has_config or has_gguf:
                        # 市场下载的模型目录名为 author--model-name，只取末段显示
                        display_name = item.name.rsplit("--", 1)[-1] if "--" in item.name else item.name
                        models.append({
                            "name": display_name,
                            "type": "gguf" if has_gguf else "transformers",
                            "path": str(item)
                        })
                elif item.suffix == ".gguf":
                    # 单独的 GGUF 文件也列出
                    models.append({
                        "name": item.stem,
                        "type": "gguf",
                        "path": str(item)
                    })

        page, page_size, paginated = _分页参数(request)
        payload = _分页响应(models, page, page_size, paginated, list_key="models")
        return web.json_response(payload)
    except Exception as e:
        logger.exception(f"[诊断] 本地模型扫描异常: {e}")
        return web.json_response({"models": [], "error": "服务器内部错误，请查看日志"}, status=500)


# ─── 跨会话记忆管理 ─────────────────────────────────────

async def handle_get_memories(request):
    """获取跨会话记忆数据（可选 plugin_path 参数筛选插件记忆）"""
    try:
        if _记忆管理器 is None:
            return _error_response("记忆系统未初始化", 503)

        plugin_path = request.query.get("plugin_path") or None
        data = await asyncio.to_thread(_记忆管理器.获取记忆, plugin_path)
        return _success_response(data)
    except Exception as e:
        logger.exception(f"获取记忆数据异常: {e}")
        return _error_response(f"获取记忆失败: {str(e)}", 500)


async def handle_clear_memories(request):
    """清除跨会话记忆

    - 请求体携带 plugin_path 时：清除该插件的记忆
    - 不传 plugin_path 时：清除全局记忆
    - type=“item” 且携带 index 参数时：删除单条记忆
    """
    try:
        client_ip = request.remote or "unknown"
        if not _rate_limiter.is_allowed(client_ip):
            return _error_response("请求过于频繁，请稍后重试", 429)

        if _记忆管理器 is None:
            return _error_response("记忆系统未初始化", 503)

        try:
            data = await request.json()
        except Exception:
            data = {}

        plugin_path = (data or {}).get("plugin_path") or None

        # 单条删除模式
        if (data or {}).get("type") == "item":
            index = (data or {}).get("index")
            记忆类型 = (data or {}).get("记忆类型") or (data or {}).get("category")
            if index is None or 记忆类型 is None:
                return _error_response("删除单条记忆需要 index 和 记忆类型 参数", 400)
            try:
                index = int(index)
            except (TypeError, ValueError):
                return _error_response("index 必须为整数", 400)
            success = await asyncio.to_thread(
                _记忆管理器.删除单条记忆, 记忆类型, index, plugin_path
            )
            if success:
                return _success_response(message="记忆条目已删除")
            else:
                return _error_response("记忆条目不存在或已删除", 404)

        # 批量清除
        await asyncio.to_thread(_记忆管理器.清除记忆, plugin_path)
        if plugin_path:
            return _success_response(message=f"已清除插件记忆: {plugin_path}")
        else:
            return _success_response(message="已清除全局记忆")
    except Exception as e:
        logger.exception(f"清除记忆异常: {e}")
        return _error_response(f"清除记忆失败: {str(e)}", 500)


async def handle_add_memory(request):
    """添加或更新用户偏好

    请求体：{"key": "偏好名称", "value": "偏好内容"}
    key 已存在则更新，否则新增。
    """
    try:
        client_ip = request.remote or "unknown"
        if not _rate_limiter.is_allowed(client_ip):
            return _error_response("请求过于频繁，请稍后重试", 429)

        if _记忆管理器 is None:
            return _error_response("记忆系统未初始化", 503)

        try:
            data = await request.json()
        except Exception:
            data = {}

        key = ((data or {}).get("key") or "").strip()
        value = ((data or {}).get("value") or "").strip()
        if not key or not value:
            return _error_response("名称和内容不能为空", 400)

        await asyncio.to_thread(_记忆管理器.记录用户偏好, key, value)
        return _success_response(message="保存成功")
    except Exception as e:
        logger.exception(f"保存记忆异常: {e}")
        return _error_response(f"保存记忆失败: {str(e)}", 500)


async def handle_add_plugin_memory(request):
    """添加插件记忆上下文

    请求体：{"plugin_name": "插件名称", "content": "上下文内容"}
    插件不存在时自动创建，content 追加到上下文列表。
    """
    try:
        client_ip = request.remote or "unknown"
        if not _rate_limiter.is_allowed(client_ip):
            return _error_response("请求过于频繁，请稍后重试", 429)

        if _记忆管理器 is None:
            return _error_response("记忆系统未初始化", 503)

        try:
            data = await request.json()
        except Exception:
            data = {}

        plugin_name = ((data or {}).get("plugin_name") or "").strip()
        content = ((data or {}).get("content") or "").strip()
        if not plugin_name or not content:
            return _error_response("插件名称和内容不能为空", 400)

        await asyncio.to_thread(
            _记忆管理器.记录项目上下文, plugin_name, {"上下文": content}
        )
        return _success_response(message="保存成功")
    except Exception as e:
        logger.exception(f"保存插件记忆异常: {e}")
        return _error_response(f"保存插件记忆失败: {str(e)}", 500)


def register_设置与模型路由(routes):
    """注册设置与模型管理端点"""
    routes.get("/ai-coder/settings")(handle_get_settings)
    routes.post("/ai-coder/settings")(handle_save_settings)
    routes.get("/ai-coder/model-capabilities")(handle_model_capabilities)
    routes.post("/ai-coder/unload-model")(handle_unload_model)
    routes.post("/ai-coder/reset-model-status")(handle_reset_model_status)
    routes.get("/ai-coder/local-models")(handle_get_local_models)
    routes.post("/ai-coder/select-folder")(handle_select_folder)
    # 跨会话记忆管理
    routes.get("/ai-coder/memories")(handle_get_memories)
    routes.post("/ai-coder/memories")(handle_add_memory)
    routes.post("/ai-coder/memories/plugin")(handle_add_plugin_memory)
    routes.delete("/ai-coder/memories")(handle_clear_memories)
