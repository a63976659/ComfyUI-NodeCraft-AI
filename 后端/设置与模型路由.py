"""设置与模型路由模块

包含：
- GET  /ai-coder/settings              获取设置
- POST /ai-coder/settings              保存设置
- GET  /ai-coder/model-capabilities    模型能力
- POST /ai-coder/unload-model          卸载本地模型
- POST /ai-coder/reset-model-status    重置模型状态
- GET  /ai-coder/local-models          扫描本地模型
"""
import asyncio

from aiohttp import web

from .文件读写操作 import load_settings, save_settings
from .日志配置 import 获取日志器
from .系统环境映射 import get_default_llm_path
from .路由公共 import (
    _error_response,
    _rate_limiter,
    _success_response,
    _分页参数,
    _分页响应,
    _记忆管理器,
    llm_client,
    local_model_client,
)

logger = 获取日志器("设置与模型路由")


# ─── 文件夹选择对话框 ────────────────────────────────────────
# 桌面版/便携版的 Python 不带 tkinter，回退链：
#   tkinter → PowerShell WinForms（Windows）→ osascript（macOS）→ zenity/kdialog（Linux）
# 全部不可用时返回明确错误，前端可引导用户改用网页版文件夹浏览器

_对话框不可用提示 = (
    "当前环境缺少图形对话框组件（桌面版/便携版 Python 不带 tkinter），"
    "请使用页面内的文件夹浏览器或手动输入路径"
)


def _tkinter选择文件夹(initial_dir: str):
    """tkinter 实现。返回 (路径, 是否可用)：tkinter 缺失/初始化失败时可用=False"""
    try:
        import tkinter as tk
        from tkinter import filedialog
    except Exception as e:
        logger.info(f"tkinter 不可用，将尝试系统原生对话框: {e}")
        return "", False

    # 容器变量传递子线程结果与异常（线程内异常不会传播到主线程，必须显式捕获）
    selected = {"path": "", "ok": True}

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
        except Exception as e:
            # tkinter 存在但初始化失败（如无显示环境），标记不可用以便回退
            logger.info(f"tkinter 对话框初始化失败，将尝试系统原生对话框: {e}")
            selected["ok"] = False
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
    return selected["path"], selected["ok"]


def _系统原生选择文件夹(initial_dir: str):
    """系统原生对话框。返回 (路径, 是否可用)"""
    import subprocess
    import sys

    if sys.platform == "win32":
        # PowerShell WinForms：UTF-8 输出防中文路径乱码；TopMost 宿主窗体防对话框
        # 被压后台；-STA 为 WinForms 必需；CREATE_NO_WINDOW 防控制台闪现
        初始目录 = (initial_dir or "").replace("'", "''")
        ps脚本 = (
            "[Console]::OutputEncoding=[System.Text.Encoding]::UTF8;"
            "Add-Type -AssemblyName System.Windows.Forms;"
            "$f=New-Object System.Windows.Forms.Form;"
            "$f.TopMost=$true;$f.ShowInTaskbar=$false;"
            "$d=New-Object System.Windows.Forms.FolderBrowserDialog;"
            "$d.Description='选择本地模型文件夹';"
            f"$d.SelectedPath='{初始目录}';"
            "if($d.ShowDialog($f) -eq [System.Windows.Forms.DialogResult]::OK)"
            "{[Console]::Out.Write($d.SelectedPath)}"
        )
        try:
            result = subprocess.run(
                ["powershell", "-NoProfile", "-NonInteractive", "-STA", "-Command", ps脚本],
                capture_output=True, timeout=300,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            # 用户取消时退出码为 0 且输出为空；非 0 说明 PowerShell 自身失败
            # （如 Add-Type 被策略禁用），应标记不可用而非当作取消静默返回
            if result.returncode != 0:
                stderr = result.stderr.decode("utf-8", errors="replace").strip()
                logger.warning(f"PowerShell 文件夹对话框执行失败(退出码 {result.returncode}): {stderr}")
                return "", False
            return result.stdout.decode("utf-8", errors="replace").strip(), True
        except Exception as e:
            logger.warning(f"PowerShell 文件夹对话框不可用: {e}")
            return "", False

    if sys.platform == "darwin":
        脚本 = 'POSIX path of (choose folder with prompt "选择本地模型文件夹")'
        try:
            result = subprocess.run(
                ["osascript", "-e", 脚本], capture_output=True, timeout=300,
            )
            # 用户取消时 osascript 退出码非 0，视为取消而非不可用
            return result.stdout.decode("utf-8", errors="replace").strip(), True
        except Exception as e:
            logger.warning(f"osascript 文件夹对话框不可用: {e}")
            return "", False

    # Linux：zenity → kdialog
    import shutil
    for 工具, 参数 in (("zenity", ["--file-selection", "--directory", "--title=选择本地模型文件夹"]),
                     ("kdialog", ["--getexistingdirectory", initial_dir or "."])):
        if not shutil.which(工具):
            continue
        try:
            result = subprocess.run([工具] + 参数, capture_output=True, timeout=300)
            return result.stdout.decode("utf-8", errors="replace").strip(), True
        except Exception as e:
            logger.warning(f"{工具} 文件夹对话框不可用: {e}")
    return "", False


def _弹出文件夹选择对话框(initial_dir: str):
    """按回退链弹出文件夹选择对话框。

    返回 (路径, 错误信息)：选中时路径非空；用户取消时两者均空；
    全部实现不可用时错误信息非空。
    """
    path, 可用 = _tkinter选择文件夹(initial_dir)
    if 可用:
        return path, ""
    path, 可用 = _系统原生选择文件夹(initial_dir)
    if 可用:
        return path, ""
    return "", _对话框不可用提示


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

        # 阻塞调用放入线程池，避免阻塞 aiohttp 事件循环
        path, 错误信息 = await asyncio.to_thread(_弹出文件夹选择对话框, initial_dir)
        if 错误信息:
            # 三态之一：对话框不可用（区别于用户取消），前端据此引导改用网页版浏览器
            return web.json_response({"success": False, "path": "", "error": 错误信息})
        return web.json_response({"success": True, "path": path or "", "cancelled": not path})
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
        # 本地 API Key 直连模式，不再做 API 权限校验
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

        # 本地模式下，用 local_model_name 兜底（API 模式用 model_name）
        if model_source == "local" and not model_name:
            model_name = settings.get("local_model_name", "") or model_name

        supports_vision = False
        if model_source == "api" and model_name:
            # API 模型：复用模型客户端的检测逻辑
            try:
                if llm_client is not None:
                    llm_client.当前模型名 = model_name
                    supports_vision = llm_client._supports_vision()
            except Exception:
                supports_vision = False
        elif model_source == "local":
            # 本地模型：检测当前加载的模型是否为多模态视觉模型
            # 传入 model_name 以支持离线检测（模型未加载时读 config.json）
            try:
                if local_model_client is not None:
                    supports_vision = local_model_client._supports_vision(model_name)
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
            # "plugin" 类型删除整个插件记忆节点，不需要 index
            if 记忆类型 != "plugin" and index is None:
                return _error_response("删除单条记忆需要 index 和 记忆类型 参数", 400)
            if 记忆类型 is None:
                return _error_response("删除单条记忆需要 index 和 记忆类型 参数", 400)
            try:
                index = int(index) if index is not None else -1
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
