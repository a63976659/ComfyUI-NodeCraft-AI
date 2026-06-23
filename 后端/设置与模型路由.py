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
    _check_auth, _error_response, _success_response, _rate_limiter,
    llm_client, local_model_client,
    _分页参数, _分页响应,
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

        auth_error = await _check_auth(request)
        if auth_error:
            return auth_error

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
        auth_error = await _check_auth(request)
        if auth_error:
            return auth_error
        # P1-1：同步 I/O 放入线程池
        settings = await asyncio.to_thread(load_settings)
        settings["default_local_path"] = str(get_default_llm_path())
        return web.json_response(settings)
    except Exception as e:
        return web.json_response({"success": False, "error": str(e)}, status=500)


async def handle_save_settings(request):
    """保存设置"""
    try:
        client_ip = request.remote or "unknown"
        if not _rate_limiter.is_allowed(client_ip):
            return _error_response("请求过于频繁，请稍后重试", 429)

        auth_error = await _check_auth(request)
        if auth_error:
            return auth_error

        data = await request.json()
        # 移除前端附加的非持久化字段
        data.pop("default_local_path", None)
        # P1-1：同步 I/O 放入线程池
        await asyncio.to_thread(save_settings, data)
        return web.json_response({"success": True})
    except Exception as e:
        return web.json_response({"success": False, "error": str(e)}, status=500)


# ─── 模型管理 ─────────────────────────────────────────────

async def handle_model_capabilities(request):
    """返回当前配置模型的能力信息（是否支持 vision 等）"""
    try:
        auth_error = await _check_auth(request)
        if auth_error:
            return auth_error

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

        return _success_response({
            "model_source": model_source,
            "model_name": model_name,
            "supports_vision": supports_vision,
            # 文本文件内容始终支持（后端会解码 base64 并内联到消息）
            "supports_file_content": True,
        })
    except Exception as e:
        return _error_response(f"获取模型能力失败: {str(e)}", 500)


async def handle_unload_model(request):
    """卸载本地模型释放显存"""
    try:
        client_ip = request.remote or "unknown"
        if not _rate_limiter.is_allowed(client_ip):
            return _error_response("请求过于频繁，请稍后重试", 429)

        auth_error = await _check_auth(request)
        if auth_error:
            return auth_error

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

        auth_error = await _check_auth(request)
        if auth_error:
            return auth_error

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
        auth_error = await _check_auth(request)
        if auth_error:
            return auth_error
        llm_path = get_default_llm_path()
        models = []

        if llm_path.exists():
            for item in llm_path.iterdir():
                if item.is_dir():
                    # 检查是否是有效的模型目录（包含 config.json 或 .gguf 文件）
                    has_config = (item / "config.json").exists()
                    has_gguf = any(item.glob("*.gguf"))
                    if has_config or has_gguf:
                        models.append({
                            "name": item.name,
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
        return web.json_response({"models": [], "error": str(e)}, status=200)


def register_设置与模型路由(routes):
    """注册设置与模型管理端点"""
    routes.get("/ai-coder/settings")(handle_get_settings)
    routes.post("/ai-coder/settings")(handle_save_settings)
    routes.get("/ai-coder/model-capabilities")(handle_model_capabilities)
    routes.post("/ai-coder/unload-model")(handle_unload_model)
    routes.post("/ai-coder/reset-model-status")(handle_reset_model_status)
    routes.get("/ai-coder/local-models")(handle_get_local_models)
    routes.post("/ai-coder/select-folder")(handle_select_folder)
