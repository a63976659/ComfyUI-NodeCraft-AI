"""文件路由模块

包含：
- POST /ai-coder/create-folder              创建插件脚手架
- GET  /ai-coder/local-plugins              扫描 custom_nodes
- POST /ai-coder/browse-folder              浏览子目录
- POST /ai-coder/analyze-plugin             分析插件依赖（向后兼容，不持久化）
- GET  /ai-coder/visualization/load         读取已持久化的可视化数据
- POST /ai-coder/visualization/analyze      执行可视化分析（文件/功能模式）并落盘
- POST /ai-coder/plugin-files               获取插件文件树
- POST /ai-coder/read-file                  读取文件
- POST /ai-coder/write-file                 写入文件
"""
import asyncio
from pathlib import Path

from aiohttp import web

from .路由公共 import (
    _check_auth, _error_response, _rate_limiter, llm_client, local_model_client,
    _分页参数, _分页响应, _检查上传大小,
)
from .文件读写操作 import create_plugin_scaffold
from .系统环境映射 import get_custom_nodes_path
from .日志配置 import 获取日志器

logger = 获取日志器("文件路由")

# M5: 错误消息脱敏 - 通用服务器错误文案
_服务器内部错误 = "服务器内部错误，请稍后重试"


# ─── 文件操作 ─────────────────────────────────────────────

async def handle_create_folder(request):
    """创建插件脚手架目录"""
    try:
        client_ip = request.remote or "unknown"
        if not _rate_limiter.is_allowed(client_ip):
            return _error_response("请求过于频繁，请稍后重试", 429)

        auth_error = await _check_auth(request)
        if auth_error:
            return auth_error

        data = await request.json()
        plugin_name = data.get("plugin_name", "").strip()

        # C4: 插件名长度限制
        if len(plugin_name) > 128:
            return _error_response("插件名称长度不能超过128个字符")

        # C4: Windows 保留字检查
        _reserved_names = {"con", "prn", "aux", "nul",
                           "com1", "com2", "com3", "com4", "com5", "com6", "com7", "com8", "com9",
                           "lpt1", "lpt2", "lpt3", "lpt4", "lpt5", "lpt6", "lpt7", "lpt8", "lpt9"}
        if plugin_name.lower().split(".")[0] in _reserved_names:
            return _error_response(f"插件名称不能使用Windows保留字: {plugin_name}")

        nodes_path = get_custom_nodes_path()
        # P1-1： create_plugin_scaffold 内部是同步文件 I/O，放入线程池
        success, message, path = await asyncio.to_thread(
            create_plugin_scaffold, nodes_path, plugin_name
        )
        return web.json_response({"success": success, "message": message, "path": path})
    except Exception as e:
        logger.error(f"创建插件脚手架异常: {e}", exc_info=True)
        return web.json_response({"success": False, "error": _服务器内部错误}, status=500)


# ─── 插件目录管理 ────────────────────────────────────────

async def handle_get_local_plugins(request):
    """扫描 custom_nodes 目录，返回所有插件目录名列表（支持分页：?page=1&page_size=20，最大 page_size=100）"""
    try:
        auth_error = await _check_auth(request)
        if auth_error:
            return auth_error
        nodes_path = get_custom_nodes_path()
        plugins = []
        if nodes_path.exists():
            for item in sorted(nodes_path.iterdir()):
                if item.is_dir() and not item.name.startswith('.'):
                    plugins.append(item.name)
        page, page_size, paginated = _分页参数(request)
        payload = _分页响应(plugins, page, page_size, paginated, list_key="plugins")
        return web.json_response(payload)
    except Exception as e:
        logger.error(f"扫描本地插件列表异常: {e}", exc_info=True)
        return web.json_response({"plugins": [], "error": _服务器内部错误}, status=500)


async def handle_browse_folder(request):
    """浏览指定目录下的子文件夹列表"""
    try:
        client_ip = request.remote or "unknown"
        if not _rate_limiter.is_allowed(client_ip):
            return _error_response("请求过于频繁，请稍后重试", 429)

        auth_error = await _check_auth(request)
        if auth_error:
            return auth_error

        data = await request.json()
        initial_dir = data.get("initial_dir", "")

        if not initial_dir:
            # 默认使用 custom_nodes 目录
            initial_dir = str(get_custom_nodes_path())

        # C3: 路径穿越防护 - 解析真实路径并检查是否在允许范围内
        target_path = Path(initial_dir).resolve()
        allowed_root = get_custom_nodes_path().resolve()
        try:
            if not target_path.is_relative_to(allowed_root):
                return _error_response("访问路径超出允许范围", 403)
        except (TypeError, ValueError):
            return _error_response("访问路径超出允许范围", 403)

        if not target_path.exists():
            return web.json_response({"status": "error", "error": f"路径不存在: {initial_dir}"}, status=400)

        if not target_path.is_dir():
            return web.json_response({"status": "error", "error": "指定路径不是目录"}, status=400)

        folders = []
        for item in sorted(target_path.iterdir()):
            if item.is_dir() and not item.name.startswith('.'):
                folders.append({
                    "name": item.name,
                    "path": str(item)
                })

        return web.json_response({
            "status": "success",
            "current_dir": str(target_path),
            "folders": folders
        })
    except Exception as e:
        logger.error(f"浏览子目录异常: {e}", exc_info=True)
        return web.json_response({"status": "error", "error": _服务器内部错误}, status=500)


# ─── 插件依赖分析 ─────────────────────────────────────

async def handle_analyze_plugin(request):
    """分析插件依赖关系，返回可视化 graphData"""
    try:
        client_ip = request.remote or "unknown"
        if not _rate_limiter.is_allowed(client_ip):
            return _error_response("请求过于频繁，请稍后重试", 429)

        auth_error = await _check_auth(request)
        if auth_error:
            return auth_error

        data = await request.json()
        plugin_name = data.get("plugin_path", "")

        if not plugin_name:
            return web.json_response({"status": "error", "error": "缺少 plugin_path 参数"}, status=400)

        # 解析路径：如果是相对路径，则相对于 custom_nodes
        plugin_path = Path(plugin_name)
        if not plugin_path.is_absolute():
            plugin_path = get_custom_nodes_path() / plugin_name

        if not plugin_path.exists():
            return web.json_response({"status": "error", "error": f"插件路径不存在: {plugin_path}"}, status=404)

        # 复用可视化分析逻辑，但不持久化（保持原有行为）
        from .可视化分析 import run_file_mode_analysis
        # P1-1：插件分析同步读取多个文件，放入线程池
        result = await asyncio.to_thread(run_file_mode_analysis, plugin_path, False)

        return web.json_response({"status": "success", "data": result})
    except Exception as e:
        logger.error(f"插件依赖分析异常: {e}", exc_info=True)
        return web.json_response({"status": "error", "error": _服务器内部错误}, status=500)


# ─── 可视化持久化与 AI 分析 ───────────────────

def _resolve_plugin_path(plugin_name: str) -> Path:
    """将用户传入的插件路径解析为绝对路径（相对路径基于 custom_nodes）"""
    plugin_path = Path(plugin_name)
    if not plugin_path.is_absolute():
        plugin_path = get_custom_nodes_path() / plugin_name
    return plugin_path


async def handle_visualization_load(request):
    """读取插件已持久化的可视化数据"""
    try:
        client_ip = request.remote or "unknown"
        if not _rate_limiter.is_allowed(client_ip):
            return _error_response("请求过于频繁，请稍后重试", 429)

        auth_error = await _check_auth(request)
        if auth_error:
            return auth_error

        plugin_name = request.query.get("plugin_path", "").strip()
        if not plugin_name:
            return web.json_response({"status": "error", "error": "缺少 plugin_path 参数"}, status=400)

        plugin_path = _resolve_plugin_path(plugin_name)
        if not plugin_path.exists():
            return web.json_response({"status": "error", "error": f"插件路径不存在: {plugin_path}"}, status=404)

        from .可视化分析 import load_visualization
        # P1-1：同步读取多个 JSON 文件，放入线程池
        data = await asyncio.to_thread(load_visualization, plugin_path)
        return web.json_response({"status": "success", "data": data})
    except Exception as e:
        logger.error(f"读取可视化持久化数据异常: {e}", exc_info=True)
        return web.json_response({"status": "error", "error": _服务器内部错误}, status=500)


async def handle_visualization_analyze(request):
    """执行可视化分析（文件模式 / 功能模式）并落盘"""
    try:
        client_ip = request.remote or "unknown"
        if not _rate_limiter.is_allowed(client_ip):
            return _error_response("请求过于频繁，请稍后重试", 429)

        auth_error = await _check_auth(request)
        if auth_error:
            return auth_error

        data = await request.json()
        plugin_name = (data.get("plugin_path") or "").strip()
        mode = (data.get("mode") or "").strip()

        if not plugin_name:
            return web.json_response({"status": "error", "error": "缺少 plugin_path 参数"}, status=400)
        if mode not in ("file", "function"):
            return web.json_response({"status": "error", "error": "mode 必须为 'file' 或 'function'"}, status=400)

        plugin_path = _resolve_plugin_path(plugin_name)
        if not plugin_path.exists():
            return web.json_response({"status": "error", "error": f"插件路径不存在: {plugin_path}"}, status=404)

        from .可视化分析 import (
            ensure_visualization_dir,
            run_file_mode_analysis,
            run_function_mode_analysis,
            load_meta,
        )

        # 提前创建 可视化/ 目录
        # P1-1： mkdir 属同步 I/O，放入线程池
        await asyncio.to_thread(ensure_visualization_dir, plugin_path)

        if mode == "file":
            # P1-1：文件模式分析含同步 I/O，放入线程池
            result = await asyncio.to_thread(run_file_mode_analysis, plugin_path, True)
        else:
            result = await run_function_mode_analysis(
                plugin_path,
                llm_client=llm_client,
                local_model_client=local_model_client,
                persist=True,
            )

        meta = await asyncio.to_thread(load_meta, plugin_path)
        return web.json_response({
            "status": "success",
            "mode": mode,
            "data": result,
            "meta": meta,
        })
    except Exception as e:
        logger.error(f"执行可视化分析异常: {e}", exc_info=True)
        return web.json_response({"status": "error", "error": _服务器内部错误}, status=500)


# ─── 插件文件操作 API ────────────────────────────────────

async def handle_get_plugin_files(request):
    """获取指定插件的文件树结构"""
    try:
        client_ip = request.remote or "unknown"
        if not _rate_limiter.is_allowed(client_ip):
            return _error_response("请求过于频繁，请稍后重试", 429)

        auth_error = await _check_auth(request)
        if auth_error:
            return auth_error

        data = await request.json()
        plugin_name = data.get("plugin_path", "")

        if not plugin_name:
            return web.json_response({"success": False, "error": "缺少 plugin_path 参数"}, status=400)

        # 解析路径
        plugin_path = Path(plugin_name)
        if not plugin_path.is_absolute():
            plugin_path = get_custom_nodes_path() / plugin_name

        if not plugin_path.exists():
            return web.json_response({"success": False, "error": f"插件路径不存在: {plugin_name}"}, status=404)

        from .文件读写操作 import scan_plugin_file_tree
        # P1-1：目录递归扫描属同步 I/O，放入线程池
        tree = await asyncio.to_thread(scan_plugin_file_tree, str(plugin_path))
        return web.json_response({"success": True, "tree": tree, "plugin_path": str(plugin_path)})
    except Exception as e:
        logger.error(f"获取插件文件树异常: {e}", exc_info=True)
        return web.json_response({"success": False, "error": _服务器内部错误}, status=500)


async def handle_read_plugin_file(request):
    """读取插件内指定文件内容"""
    try:
        client_ip = request.remote or "unknown"
        if not _rate_limiter.is_allowed(client_ip):
            return _error_response("请求过于频繁，请稍后重试", 429)

        auth_error = await _check_auth(request)
        if auth_error:
            return auth_error

        data = await request.json()
        plugin_name = data.get("plugin_path", "")
        file_path = data.get("file_path", "")

        if not plugin_name or not file_path:
            return web.json_response({"success": False, "error": "缺少 plugin_path 或 file_path 参数"}, status=400)

        # 解析插件路径
        plugin_path = Path(plugin_name)
        if not plugin_path.is_absolute():
            plugin_path = get_custom_nodes_path() / plugin_name

        from .文件读写操作 import read_plugin_file
        # P1-1：同步文件读取放入线程池
        success, result = await asyncio.to_thread(
            read_plugin_file, str(plugin_path), file_path
        )

        if success:
            return web.json_response({"success": True, "content": result})
        else:
            return web.json_response({"success": False, "error": result}, status=400)
    except Exception as e:
        logger.error(f"读取插件文件异常: {e}", exc_info=True)
        return web.json_response({"success": False, "error": _服务器内部错误}, status=500)


async def handle_write_plugin_file(request):
    """写入/修改插件内的文件（自动备份原文件）"""
    try:
        # H4: 在读取请求体前预校验上传大小，防止超大文件耗尽内存
        大小检查 = await _检查上传大小(request)
        if 大小检查:
            return 大小检查

        client_ip = request.remote or "unknown"
        if not _rate_limiter.is_allowed(client_ip):
            return _error_response("请求过于频繁，请稍后重试", 429)

        auth_error = await _check_auth(request)
        if auth_error:
            return auth_error

        data = await request.json()
        plugin_name = data.get("plugin_path", "")
        file_path = data.get("file_path", "")
        content = data.get("content", "")

        if not plugin_name or not file_path:
            return web.json_response({"success": False, "error": "缺少 plugin_path 或 file_path 参数"}, status=400)

        # 解析插件路径
        plugin_path = Path(plugin_name)
        if not plugin_path.is_absolute():
            plugin_path = get_custom_nodes_path() / plugin_name

        from .文件读写操作 import write_plugin_file
        # P1-1：同步文件写入（含备份）放入线程池
        success, message = await asyncio.to_thread(
            write_plugin_file, str(plugin_path), file_path, content
        )

        if success:
            return web.json_response({"success": True, "message": message})
        else:
            return web.json_response({"success": False, "error": message}, status=400)
    except Exception as e:
        logger.error(f"写入插件文件异常: {e}", exc_info=True)
        return web.json_response({"success": False, "error": _服务器内部错误}, status=500)


def register_文件路由(routes):
    """注册文件操作端点"""
    routes.post("/ai-coder/create-folder")(handle_create_folder)
    routes.get("/ai-coder/local-plugins")(handle_get_local_plugins)
    routes.post("/ai-coder/browse-folder")(handle_browse_folder)
    routes.post("/ai-coder/analyze-plugin")(handle_analyze_plugin)
    routes.get("/ai-coder/visualization/load")(handle_visualization_load)
    routes.post("/ai-coder/visualization/analyze")(handle_visualization_analyze)
    routes.post("/ai-coder/plugin-files")(handle_get_plugin_files)
    routes.post("/ai-coder/read-file")(handle_read_plugin_file)
    routes.post("/ai-coder/write-file")(handle_write_plugin_file)
