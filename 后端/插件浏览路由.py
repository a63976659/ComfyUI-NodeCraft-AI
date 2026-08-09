"""插件浏览路由模块

包含：
- POST /ai-coder/create-folder              创建插件脚手架
- GET  /ai-coder/local-plugins              扫描 custom_nodes
- POST /ai-coder/browse-folder              浏览子目录
- POST /ai-coder/analyze-plugin             分析插件依赖（向后兼容，不持久化）
- GET  /ai-coder/visualization/load         读取已持久化的可视化数据
- POST /ai-coder/visualization/analyze      执行可视化分析（文件/功能模式）并落盘
- POST /ai-coder/plugin-files               获取插件文件树
- GET  /ai-coder/optimize-methods           列出知识库中的优化方法文档
"""
import asyncio
from pathlib import Path

from aiohttp import web

from .文件读写操作 import create_plugin_scaffold, scan_plugin_file_tree
from .日志配置 import 获取日志器
from .系统环境映射 import get_custom_nodes_path, get_plugin_root
from .路由公共 import (
    _error_response,
    _rate_limiter,
    _分页参数,
    _分页响应,
    llm_client,
    local_model_client,
    服务器内部错误,
    路径在允许范围内,
)

logger = 获取日志器("插件浏览路由")

# Windows 保留字集合（用于插件名/文件夹名校验）
_reserved_names = {"con", "prn", "aux", "nul",
                   "com1", "com2", "com3", "com4", "com5", "com6", "com7", "com8", "com9",
                   "lpt1", "lpt2", "lpt3", "lpt4", "lpt5", "lpt6", "lpt7", "lpt8", "lpt9"}


def _resolve_plugin_path(plugin_name: str) -> Path:
    """将用户传入的插件路径解析为绝对路径（相对路径基于 custom_nodes）"""
    plugin_path = Path(plugin_name)
    if not plugin_path.is_absolute():
        plugin_path = get_custom_nodes_path() / plugin_name
    return plugin_path


# ─── 文件操作 ─────────────────────────────────────────────

async def handle_create_folder(request):
    """创建插件脚手架目录"""
    try:
        client_ip = request.remote or "unknown"
        if not _rate_limiter.is_allowed(client_ip):
            return _error_response("请求过于频繁，请稍后重试", 429)

        data = await request.json()
        plugin_name = data.get("plugin_name", "").strip()
        # 界面语言（zh-CN/en）：决定脚手架目录名与 README 语言，缺省中文
        language = data.get("language", "zh-CN")

        # C4: 插件名长度限制
        if len(plugin_name) > 128:
            return _error_response("插件名称长度不能超过128个字符")

        # C4: Windows 保留字检查
        if plugin_name.lower().split(".")[0] in _reserved_names:
            return _error_response(f"插件名称不能使用Windows保留字: {plugin_name}")

        nodes_path = get_custom_nodes_path()
        # P1-1： create_plugin_scaffold 内部是同步文件 I/O，放入线程池
        success, message, path = await asyncio.to_thread(
            create_plugin_scaffold, nodes_path, plugin_name, language
        )
        return web.json_response({"success": success, "message": message, "path": path})
    except Exception as e:
        logger.error(f"创建插件脚手架异常: {e}", exc_info=True)
        return web.json_response({"success": False, "error": 服务器内部错误}, status=500)


# ─── 插件目录管理 ────────────────────────────────────────

async def handle_get_local_plugins(request):
    """扫描 custom_nodes 目录，返回所有插件目录名列表（支持分页：?page=1&page_size=20，最大 page_size=100）"""
    try:
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
        return web.json_response({"plugins": [], "error": 服务器内部错误}, status=500)


async def handle_browse_folder(request):
    """浏览指定目录下的子文件夹列表"""
    try:
        client_ip = request.remote or "unknown"
        if not _rate_limiter.is_allowed(client_ip):
            return _error_response("请求过于频繁，请稍后重试", 429)

        data = await request.json()
        initial_dir = data.get("initial_dir", "")

        if not initial_dir:
            # 默认使用 custom_nodes 目录
            initial_dir = str(get_custom_nodes_path())

        # C3: 路径穿越防护 - 解析真实路径并检查是否在允许范围内（白名单三根）
        target_path = Path(initial_dir).resolve()
        if not 路径在允许范围内(target_path):
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
        return web.json_response({"status": "error", "error": 服务器内部错误}, status=500)


# ─── 插件依赖分析 ─────────────────────────────────────

async def handle_analyze_plugin(request):
    """分析插件依赖关系，返回可视化 graphData"""
    try:
        client_ip = request.remote or "unknown"
        if not _rate_limiter.is_allowed(client_ip):
            return _error_response("请求过于频繁，请稍后重试", 429)

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
        return web.json_response({"status": "error", "error": 服务器内部错误}, status=500)


# ─── 可视化持久化与 AI 分析 ───────────────────

async def handle_visualization_load(request):
    """读取插件已持久化的可视化数据"""
    try:
        client_ip = request.remote or "unknown"
        if not _rate_limiter.is_allowed(client_ip):
            return _error_response("请求过于频繁，请稍后重试", 429)

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
        return web.json_response({"status": "error", "error": 服务器内部错误}, status=500)


async def handle_visualization_analyze(request):
    """执行可视化分析（文件模式 / 功能模式）并落盘"""
    try:
        client_ip = request.remote or "unknown"
        if not _rate_limiter.is_allowed(client_ip):
            return _error_response("请求过于频繁，请稍后重试", 429)

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
            load_meta,
            run_file_mode_analysis,
            run_function_mode_analysis,
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
        return web.json_response({"status": "error", "error": 服务器内部错误}, status=500)


# ─── 插件文件操作 API ────────────────────────────────────

async def handle_get_plugin_files(request):
    """获取指定插件的文件树结构"""
    try:
        client_ip = request.remote or "unknown"
        if not _rate_limiter.is_allowed(client_ip):
            return _error_response("请求过于频繁，请稍后重试", 429)

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

        # P1-1：目录递归扫描属同步 I/O，放入线程池
        tree = await asyncio.to_thread(scan_plugin_file_tree, str(plugin_path))
        return web.json_response({"success": True, "tree": tree, "plugin_path": str(plugin_path)})
    except Exception as e:
        logger.error(f"获取插件文件树异常: {e}", exc_info=True)
        return web.json_response({"success": False, "error": 服务器内部错误}, status=500)


# ─── 优化方法列表 ────────────────────────────────────────

# 不在优化面板欢迎页展示的文档（文件仍保留供 RAG 检索使用）
# 代码质量审查清单：与文件夹选择器的「代码审查」按钮功能重复，故不再作为快捷入口
# 优化验收与回归清单：优化任务的收尾流程文档，由智能体检索使用，不作为用户主动入口
_优化方法隐藏文档 = {"总览", "代码质量审查清单", "优化验收与回归清单"}

# 快捷入口按钮的展示名（文档名 → 简洁的目标式名称）
# 仅影响按钮文案；提示词仍使用文档原名（`name`），以保证 RAG 能命中对应知识库文档
_优化方法展示名 = {
    "重复代码率降低方法论": "降低重复代码",
    "项目规模分级优化方案": "按项目规模优化",
    "ComfyUI性能调优实战": "显存与推理加速",
    "性能基准与Profiling指南": "性能测试与瓶颈定位",
    "启动与导入优化": "启动与导入加速",
    "复杂度与大文件拆分": "拆分大文件",
}


def _扫描优化方法文档():
    """扫描 知识库/优化插件 目录，返回优化方法文档列表（排除隐藏文件与不展示的文档）"""
    kb_dir = get_plugin_root() / "知识库" / "优化插件"
    methods = []
    if kb_dir.exists():
        for f in sorted(kb_dir.rglob("*.md")):
            if f.name.startswith(".") or f.stem in _优化方法隐藏文档:
                continue
            category = f.parent.name if f.parent != kb_dir else ""
            methods.append({
                "name": f.stem,
                "display": _优化方法展示名.get(f.stem, f.stem),
                "category": category,
            })
    return methods


async def handle_get_optimize_methods(request):
    """列出知识库中的优化方法文档（供优化面板欢迎页快捷操作使用）"""
    try:
        # 目录递归扫描属同步 I/O，放入线程池
        methods = await asyncio.to_thread(_扫描优化方法文档)
        return web.json_response({"success": True, "methods": methods})
    except Exception as e:
        logger.error(f"获取优化方法列表异常: {e}", exc_info=True)
        return web.json_response({"success": False, "error": 服务器内部错误}, status=500)


def register_插件浏览路由(routes):
    """注册插件浏览与分析端点"""
    routes.post("/ai-coder/create-folder")(handle_create_folder)
    routes.get("/ai-coder/local-plugins")(handle_get_local_plugins)
    routes.post("/ai-coder/browse-folder")(handle_browse_folder)
    routes.post("/ai-coder/analyze-plugin")(handle_analyze_plugin)
    routes.get("/ai-coder/visualization/load")(handle_visualization_load)
    routes.post("/ai-coder/visualization/analyze")(handle_visualization_analyze)
    routes.post("/ai-coder/plugin-files")(handle_get_plugin_files)
    routes.get("/ai-coder/optimize-methods")(handle_get_optimize_methods)
