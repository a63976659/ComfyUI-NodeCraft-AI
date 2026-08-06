"""文件路由模块

包含纯文件 CRUD 操作：
- POST /ai-coder/read-file                  读取文件
- POST /ai-coder/write-file                 写入文件
- POST /ai-coder/edit-file                  增量编辑文件（unified diff 补丁，前端编辑器专用）
- POST /ai-coder/apply-patch                应用 unified diff 补丁（增量编辑）

其他路由已拆分至：
- 代码审查路由.py    (code-review, benchmark)
- 插件浏览路由.py    (create-folder, browse, plugins, visualization)
"""
import asyncio
from pathlib import Path

from aiohttp import web

from .日志配置 import 获取日志器
from .系统环境映射 import get_custom_nodes_path
from .路由公共 import (
    _error_response,
    _rate_limiter,
    _检查上传大小,
    服务器内部错误,
    路径在允许范围内,
)

# re-export: 保持向后兼容（其他模块可能通过 文件路由 导入这些符号）
from .代码审查路由 import handle_code_review  # noqa: F401
from .插件浏览路由 import (  # noqa: F401
    handle_analyze_plugin,
    handle_browse_folder,
    handle_create_folder,
    handle_get_local_plugins,
    handle_get_plugin_files,
    handle_visualization_analyze,
    handle_visualization_load,
)

logger = 获取日志器("文件路由")


# ─── 可视化持久化与 AI 分析 ───────────────────

def _resolve_plugin_path(plugin_name: str):
    """将用户传入的插件路径解析为绝对路径（相对路径基于 custom_nodes）。

    传入绝对路径时必须位于白名单三根之内，否则返回 None，由调用方返回 403；
    避免绕过 browse-folder 白名单直接用绝对路径读写任意位置。
    """
    plugin_path = Path(plugin_name)
    if not plugin_path.is_absolute():
        return get_custom_nodes_path() / plugin_name
    if not 路径在允许范围内(plugin_path):
        return None
    return plugin_path


# ─── 插件文件操作 API ────────────────────────────────────

async def handle_read_plugin_file(request):
    """读取插件内指定文件内容"""
    try:
        client_ip = request.remote or "unknown"
        if not _rate_limiter.is_allowed(client_ip):
            return _error_response("请求过于频繁，请稍后重试", 429)

        data = await request.json()
        plugin_name = data.get("plugin_path", "")
        file_path = data.get("file_path", "")

        if not plugin_name or not file_path:
            return web.json_response({"success": False, "error": "缺少 plugin_path 或 file_path 参数"}, status=400)

        # 解析插件路径
        plugin_path = _resolve_plugin_path(plugin_name)
        if plugin_path is None:
            return _error_response("访问路径超出允许范围", 403)

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
        return web.json_response({"success": False, "error": 服务器内部错误}, status=500)


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

        data = await request.json()
        plugin_name = data.get("plugin_path", "")
        file_path = data.get("file_path", "")
        content = data.get("content", "")

        if not plugin_name or not file_path:
            return web.json_response({"success": False, "error": "缺少 plugin_path 或 file_path 参数"}, status=400)

        # 解析插件路径
        plugin_path = _resolve_plugin_path(plugin_name)
        if plugin_path is None:
            return _error_response("访问路径超出允许范围", 403)

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
        return web.json_response({"success": False, "error": 服务器内部错误}, status=500)


async def handle_apply_patch(request):
    """应用 unified diff 补丁到插件内的文件（增量编辑）"""
    try:
        # H4: 在读取请求体前预校验上传大小
        大小检查 = await _检查上传大小(request)
        if 大小检查:
            return 大小检查

        client_ip = request.remote or "unknown"
        if not _rate_limiter.is_allowed(client_ip):
            return _error_response("请求过于频繁，请稍后重试", 429)

        data = await request.json()
        plugin_name = data.get("plugin_path", "")
        file_path = data.get("file_path", "")
        patch_content = data.get("patch_content", "")

        if not plugin_name or not file_path:
            return web.json_response({"success": False, "error": "缺少 plugin_path 或 file_path 参数"}, status=400)

        if not patch_content.strip():
            return web.json_response({"success": False, "error": "patch_content 不能为空"}, status=400)

        # 解析插件路径
        plugin_path = _resolve_plugin_path(plugin_name)
        if plugin_path is None:
            return _error_response("访问路径超出允许范围", 403)

        # 安全校验：确保文件路径在插件目录内
        target_file = (plugin_path / file_path).resolve()
        try:
            target_file.relative_to(plugin_path.resolve())
        except ValueError:
            return web.json_response({"success": False, "error": "安全错误：文件路径超出插件目录范围"}, status=400)

        if not target_file.exists():
            return web.json_response({"success": False, "error": f"文件不存在: {file_path}"}, status=404)

        from .文件读写操作 import apply_patch
        # P1-1：同步补丁应用（含文件 I/O）放入线程池
        try:
            content = await asyncio.to_thread(apply_patch, target_file, patch_content)
            return web.json_response({"success": True, "content": content})
        except (ValueError, FileNotFoundError) as e:
            return web.json_response({"success": False, "error": f"补丁应用失败: {str(e)}"}, status=400)
    except Exception as e:
        logger.error(f"应用补丁异常: {e}", exc_info=True)
        return web.json_response({"success": False, "error": 服务器内部错误}, status=500)


async def handle_edit_file(request):
    """增量编辑文件（unified diff 补丁，前端编辑器专用）"""
    try:
        # H4: 在读取请求体前预校验上传大小
        大小检查 = await _检查上传大小(request)
        if 大小检查:
            return 大小检查

        client_ip = request.remote or "unknown"
        if not _rate_limiter.is_allowed(client_ip):
            return _error_response("请求过于频繁，请稍后重试", 429)

        data = await request.json()
        plugin_name = data.get("plugin_path", "")
        file_path = data.get("file_path", "")
        patch = data.get("patch", "")

        if not file_path or not patch.strip():
            return _error_response("缺少 file_path 或 patch 参数", 400)

        # 解析插件路径
        plugin_path = _resolve_plugin_path(plugin_name)
        if plugin_path is None:
            return _error_response("访问路径超出允许范围", 403)

        # 安全校验：确保文件路径在插件目录内
        target_file = (plugin_path / file_path).resolve()
        try:
            target_file.relative_to(plugin_path.resolve())
        except ValueError:
            return web.json_response({"success": False, "error": "安全错误：文件路径超出插件目录范围"}, status=400)

        if not target_file.exists():
            return web.json_response({"success": False, "error": f"文件不存在: {file_path}"}, status=404)

        from .文件读写操作 import apply_patch
        # P1-1：同步补丁应用（含文件 I/O）放入线程池
        try:
            content = await asyncio.to_thread(apply_patch, target_file, patch)
            return web.json_response({
                "success": True,
                "message": f"增量修改成功: {file_path}",
                "details": content
            }, status=200)
        except (ValueError, FileNotFoundError) as e:
            return web.json_response({"success": False, "error": f"补丁应用失败: {str(e)}"}, status=400)
    except Exception as e:
        logger.error(f"增量编辑文件异常: {e}", exc_info=True)
        return web.json_response({"success": False, "error": 服务器内部错误}, status=500)


def register_文件路由(routes):
    """注册文件操作端点（纯文件 CRUD）"""
    routes.post("/ai-coder/read-file")(handle_read_plugin_file)
    routes.post("/ai-coder/write-file")(handle_write_plugin_file)
    routes.post("/ai-coder/apply-patch")(handle_apply_patch)
    routes.post("/ai-coder/edit-file")(handle_edit_file)
