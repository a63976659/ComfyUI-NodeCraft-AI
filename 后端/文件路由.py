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
- POST /ai-coder/edit-file                  增量编辑文件（unified diff 补丁，前端编辑器专用）
- POST /ai-coder/apply-patch                应用 unified diff 补丁（增量编辑）
- POST /ai-coder/code-review               AI 代码审查（扫描源码→LLM→结构化报告）
"""
import asyncio
from pathlib import Path

from aiohttp import web

from .路由公共 import (
    _error_response, _rate_limiter, llm_client, local_model_client,
    tool_router, _分页参数, _分页响应, _检查上传大小, 服务器内部错误,
)
from .文件读写操作 import create_plugin_scaffold, load_settings
from .系统环境映射 import get_custom_nodes_path, get_default_llm_path
from .日志配置 import 获取日志器
from .token计费 import 本地扣减NCA余额, 获取token对应账户, TOKENS_PER_CREDIT
from .计费代理 import 检查会员资格

logger = 获取日志器("文件路由")

# Windows 保留字集合（用于插件名/文件夹名校验）
_reserved_names = {"con", "prn", "aux", "nul",
                   "com1", "com2", "com3", "com4", "com5", "com6", "com7", "com8", "com9",
                   "lpt1", "lpt2", "lpt3", "lpt4", "lpt5", "lpt6", "lpt7", "lpt8", "lpt9"}


# ─── 文件操作 ─────────────────────────────────────────────

async def handle_create_folder(request):
    """创建插件脚手架目录"""
    try:
        client_ip = request.remote or "unknown"
        if not _rate_limiter.is_allowed(client_ip):
            return _error_response("请求过于频繁，请稍后重试", 429)

        # 非会员拦截：非会员禁止使用任何功能
        membership_block = await 检查会员资格(request)
        if membership_block:
            return membership_block

        data = await request.json()
        plugin_name = data.get("plugin_name", "").strip()

        # C4: 插件名长度限制
        if len(plugin_name) > 128:
            return _error_response("插件名称长度不能超过128个字符")

        # C4: Windows 保留字检查
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

        # 非会员拦截：非会员禁止使用任何功能
        membership_block = await 检查会员资格(request)
        if membership_block:
            return membership_block

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

        from .文件读写操作 import scan_plugin_file_tree
        # P1-1：目录递归扫描属同步 I/O，放入线程池
        tree = await asyncio.to_thread(scan_plugin_file_tree, str(plugin_path))
        return web.json_response({"success": True, "tree": tree, "plugin_path": str(plugin_path)})
    except Exception as e:
        logger.error(f"获取插件文件树异常: {e}", exc_info=True)
        return web.json_response({"success": False, "error": 服务器内部错误}, status=500)


async def handle_read_plugin_file(request):
    """读取插件内指定文件内容"""
    try:
        client_ip = request.remote or "unknown"
        if not _rate_limiter.is_allowed(client_ip):
            return _error_response("请求过于频繁，请稍后重试", 429)

        # 非会员拦截：非会员禁止使用任何功能
        membership_block = await 检查会员资格(request)
        if membership_block:
            return membership_block

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

        # 非会员拦截：非会员禁止使用任何功能
        membership_block = await 检查会员资格(request)
        if membership_block:
            return membership_block

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

        # 非会员拦截：非会员禁止使用任何功能
        membership_block = await 检查会员资格(request)
        if membership_block:
            return membership_block

        data = await request.json()
        plugin_name = data.get("plugin_path", "")
        file_path = data.get("file_path", "")
        patch_content = data.get("patch_content", "")

        if not plugin_name or not file_path:
            return web.json_response({"success": False, "error": "缺少 plugin_path 或 file_path 参数"}, status=400)

        if not patch_content.strip():
            return web.json_response({"success": False, "error": "patch_content 不能为空"}, status=400)

        # 解析插件路径
        plugin_path = Path(plugin_name)
        if not plugin_path.is_absolute():
            plugin_path = get_custom_nodes_path() / plugin_name

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

        # 非会员拦截：非会员禁止使用任何功能
        membership_block = await 检查会员资格(request)
        if membership_block:
            return membership_block

        data = await request.json()
        plugin_name = data.get("plugin_path", "")
        file_path = data.get("file_path", "")
        patch = data.get("patch", "")

        if not file_path or not patch.strip():
            return _error_response("缺少 file_path 或 patch 参数", 400)

        # 解析插件路径
        plugin_path = Path(plugin_name)
        if not plugin_path.is_absolute():
            plugin_path = get_custom_nodes_path() / plugin_name

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


# ─── AI 代码审查 ─────────────────────────────────────────────

def _筛选审查文件(all_files: list, review_depth: str) -> list:
    """根据审查深度筛选需要审查的文件列表"""
    if review_depth == "quick":
        # 快速模式：__init__.py 和根目录 .py 文件
        result = []
        for f in all_files:
            if f.endswith("__init__.py"):
                result.append(f)
            elif f.endswith(".py") and "/" not in f:
                result.append(f)
        return result
    elif review_depth == "deep":
        # 深度模式：所有 .py 和 .js 文件
        return [f for f in all_files if f.endswith(".py") or f.endswith(".js")]
    else:
        # 标准模式：所有 .py 文件
        return [f for f in all_files if f.endswith(".py")]


def _构建审查提示词(file_summaries: list, review_knowledge: str, review_depth: str):
    """构建代码审查专用提示词，返回 (system_prompt, user_message)"""
    depth_desc = {
        "quick": "快速审查（仅核心文件）",
        "standard": "标准审查（所有 Python 文件）",
        "deep": "深度审查（所有 Python 和 JavaScript 文件）",
    }.get(review_depth, "标准审查")

    system_prompt = (
        "你是一位 ComfyUI 插件代码审查专家。请对以下插件代码进行"
        + depth_desc
        + "。\n\n"
        "## 审查标准\n"
        + (review_knowledge or "参考通用代码质量审查清单")
        + "\n\n"
        "## 审查维度\n"
        "1. 安全性（security）：输入验证、路径遍历防护、代码注入防护、敏感信息处理\n"
        "2. 性能（performance）：异步 I/O、内存管理、GPU 利用率\n"
        "3. 代码规范（style）：命名规范、类型提示、文档字符串\n"
        "4. ComfyUI 节点规范（comfyui）：INPUT_TYPES/RETURN_TYPES/Function 对齐、节点注册、IS_CHANGED 实现\n"
        "5. 可维护性（maintenance）：模块化、错误处理、日志使用、配置管理\n\n"
        "## 输出格式\n"
        "请严格输出以下 JSON 格式的审查报告（不要包含其他文本）：\n\n"
        "```json\n"
        "{\n"
        '    "summary": "总体评价（2-3句话概括代码质量）",\n'
        '    "score": 85,\n'
        '    "issues": [\n'
        '        {\n'
        '            "severity": "high",\n'
        '            "category": "security",\n'
        '            "file": "node.py",\n'
        '            "line": 42,\n'
        '            "description": "问题描述",\n'
        '            "suggestion": "改进建议"\n'
        '        }\n'
        '    ],\n'
        '    "strengths": ["代码优点1", "代码优点2"]\n'
        "}\n"
        "```\n\n"
        "注意：\n"
        "- score 范围 0-100，90+优秀，70-89良好，60-69及格，60以下不合格\n"
        "- issues 按 severity 排序（high > medium > low）\n"
        "- 如果没有发现问题，issues 为空数组\n"
        "- strengths 至少列出 1 个优点\n"
        "- line 字段尽量给出大致行号，无法确定时填 0\n"
    )

    code_parts = []
    for f in file_summaries:
        ext = "javascript" if f["path"].endswith(".js") else "python"
        code_parts.append(
            f"### 文件: {f['path']} ({f['size']} 字符)\n```{ext}\n{f['content']}\n```"
        )

    user_message = (
        f"## 待审查插件代码\n\n"
        f"共 {len(file_summaries)} 个文件：\n\n"
        + "\n\n".join(code_parts)
        + "\n\n请按照审查标准对以上代码进行全面审查，输出 JSON 格式的审查报告。"
    )
    return system_prompt, user_message


def _解析审查结果(reply: str) -> dict:
    """解析 LLM 返回的审查结果，提取 JSON"""
    import json as _json
    import re as _re

    if not reply:
        return {"summary": "审查结果为空", "score": 0, "issues": [], "strengths": []}

    # 1. 尝试直接 JSON 解析
    try:
        return _json.loads(reply)
    except _json.JSONDecodeError:
        pass

    # 2. 尝试从代码块中提取 JSON
    json_match = _re.search(r'```(?:json)?\s*(\{[\s\S]*?\})\s*```', reply)
    if json_match:
        try:
            return _json.loads(json_match.group(1))
        except _json.JSONDecodeError:
            pass

    # 3. 尝试从文本中提取最外层 JSON 对象
    brace_start = reply.find("{")
    brace_end = reply.rfind("}")
    if brace_start != -1 and brace_end != -1 and brace_end > brace_start:
        try:
            return _json.loads(reply[brace_start:brace_end + 1])
        except _json.JSONDecodeError:
            pass

    # 4. 降级：返回原始文本作为摘要
    return {
        "summary": reply[:500],
        "score": 0,
        "issues": [],
        "strengths": [],
        "raw_response": reply,
    }


async def handle_code_review(request):
    """AI 代码审查：扫描插件源码 → 构建审查提示词 → 调用 LLM → 返回结构化审查报告"""
    try:
        client_ip = request.remote or "unknown"
        if not _rate_limiter.is_allowed(client_ip):
            return _error_response("请求过于频繁，请稍后重试", 429)

        # 非会员拦截：非会员禁止使用任何功能
        membership_block = await 检查会员资格(request)
        if membership_block:
            return membership_block

        data = await request.json()
        plugin_name = (data.get("plugin_path") or "").strip()
        review_depth = (data.get("review_depth") or "standard").strip()

        if not plugin_name:
            return web.json_response({"success": False, "error": "缺少 plugin_path 参数"}, status=400)

        if review_depth not in ("quick", "standard", "deep"):
            review_depth = "standard"

        plugin_path = _resolve_plugin_path(plugin_name)
        if not plugin_path.exists():
            return web.json_response({"success": False, "error": f"插件路径不存在: {plugin_path}"}, status=404)

        # 1. 扫描文件列表
        from .插件分析器 import scan_directory
        all_files = await asyncio.to_thread(scan_directory, plugin_path)

        review_files = _筛选审查文件(all_files, review_depth)
        if not review_files:
            return web.json_response({
                "success": False,
                "error": f"未找到可审查的文件（深度: {review_depth}）",
            }, status=400)

        # 2. 读取源码
        max_chars = 6000 if review_depth == "deep" else 10000
        file_summaries = []
        for rel_path in review_files:
            full_path = plugin_path / rel_path
            try:
                content = await asyncio.to_thread(
                    full_path.read_text, encoding="utf-8", errors="ignore"
                )
                if len(content) > max_chars:
                    content = content[:max_chars] + f"\n...[文件已截断，原始长度 {len(content)} 字符]"
                file_summaries.append({
                    "path": rel_path,
                    "content": content,
                    "size": len(content),
                })
            except Exception as e:
                logger.warning(f"读取审查文件 {rel_path} 失败: {e}")

        if not file_summaries:
            return web.json_response({"success": False, "error": "所有文件读取失败"}, status=500)

        # 3. RAG 检索审查清单知识库
        # 审查深度映射到知识库检索条数：quick=1 / standard=3 / deep=5
        review_knowledge = ""
        if tool_router is not None:
            知识库_top_k = {"quick": 1, "standard": 3, "deep": 5}.get(review_depth, 3)
            review_knowledge = tool_router.retrieve_knowledge(
                "代码质量审查 安全性 性能 代码规范 ComfyUI节点规范 可维护性 输入验证 路径遍历",
                active_tab="optimize",
                top_k=知识库_top_k,
            )

        # 4. 构建审查提示词
        system_prompt, user_message = _构建审查提示词(file_summaries, review_knowledge, review_depth)

        # 5. 加载设置并调用 LLM
        settings = await asyncio.to_thread(load_settings)
        model_source = settings.get("model_source", "api")
        messages = [{"role": "user", "content": user_message}]

        if model_source == "local" and local_model_client is not None:
            local_model_name = settings.get("local_model_name", "")
            local_path = settings.get("local_path", "")
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
                return web.json_response({
                    "success": False,
                    "error": "未检测到可用的本地模型，请在设置中配置模型路径",
                }, status=400)
            try:
                if local_model_client.当前模型名 != Path(local_path).name:
                    await local_model_client.异步加载模型(local_path)
            except Exception as e:
                return web.json_response({
                    "success": False,
                    "error": f"模型加载失败: {str(e)}",
                }, status=500)
            reply = await local_model_client.generate_response(system_prompt, messages)
        else:
            reply = await llm_client.generate_response(system_prompt, messages)

            # 计费信息记录（云端已处理扣费）
            try:
                云端计费 = getattr(llm_client, "云端计费", None)
                if 云端计费:
                    cost_credits = 云端计费.get("cost", 0)
                    if cost_credits > 0:
                        logger.info(f"[审查计费] 扣费={cost_credits}积分")
            except Exception as _billing_err:
                logger.warning(f"[审查计费] 计费信息读取失败(不影响审查): {_billing_err}")

        # 6. 解析审查结果
        review_result = _解析审查结果(reply)

        # 附加审查元信息
        review_result["review_depth"] = review_depth
        review_result["files_reviewed"] = len(file_summaries)
        review_result["file_list"] = [f["path"] for f in file_summaries]

        # 云端知识库检索失败时附加降级提醒字段（正常时不新增字段，保持向后兼容）
        if tool_router is not None and getattr(tool_router, "kb_retrieval_failed", False):
            from 智能体.工具路由器 import kb_failure_message
            _kb_reason = getattr(tool_router, "kb_failure_reason", "error") or "error"
            review_result["kb_retrieval_status"] = "degraded"
            review_result["kb_message"] = kb_failure_message(_kb_reason)

        return web.json_response({"success": True, "review": review_result})
    except Exception as e:
        logger.error(f"代码审查异常: {e}", exc_info=True)
        return web.json_response({"success": False, "error": 服务器内部错误}, status=500)


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
    routes.post("/ai-coder/apply-patch")(handle_apply_patch)
    routes.post("/ai-coder/edit-file")(handle_edit_file)
    routes.post("/ai-coder/code-review")(handle_code_review)
