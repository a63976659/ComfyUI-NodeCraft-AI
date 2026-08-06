"""市场与打包路由模块

包含：
- 插件打包：/ai-coder/package-plugin、/ai-coder/packages、/ai-coder/packages/download/{filename}
- 模型市场：/ai-coder/model-market/* 系列
- 模板市场：/ai-coder/templates、/ai-coder/templates/create
"""
import asyncio
from pathlib import Path

from aiohttp import web

from .插件打包 import PluginPackager
from .日志配置 import 获取日志器
from .模型市场 import ModelMarket, ModelScopeMarket
from .模板市场 import TemplateMarket
from .系统环境映射 import get_custom_nodes_path, get_default_llm_path
from .路由公共 import (
    _error_response,
    _rate_limiter,
    _success_response,
    _分页参数,
    _分页响应,
    服务器内部错误,
)

logger = 获取日志器("市场与打包路由")


# ─── 单例实例 ────────────────────────────────────────────

_model_market = ModelMarket(get_default_llm_path())
_modelscope_market = ModelScopeMarket(get_default_llm_path())


def _get_market(source: str):
    """根据 source 参数获取对应的市场实例"""
    if source == "modelscope":
        return _modelscope_market
    return _model_market  # 默认 HuggingFace

# 插件打包器
_packages_dir = Path(__file__).parent.parent / "数据" / "打包文件"
_plugin_packager = PluginPackager(_packages_dir)

_template_market = TemplateMarket(get_custom_nodes_path())


# ─── 插件打包 ────────────────────────────────────────────────

async def package_plugin(request):
    """打包插件为 zip 文件"""
    try:
        client_ip = request.remote or "unknown"
        if not _rate_limiter.is_allowed(client_ip):
            return _error_response("请求过于频繁，请稍后重试", 429)

        data = await request.json()
        plugin_name = data.get("plugin_name", "")
        options = data.get("options", {})

        if not plugin_name:
            return _error_response("缺少 plugin_name 参数")

        # 解析插件路径
        plugin_path = Path(plugin_name)
        if not plugin_path.is_absolute():
            plugin_path = get_custom_nodes_path() / plugin_name

        if not plugin_path.exists():
            return _error_response(f"插件路径不存在: {plugin_name}", 404)

        result = await _plugin_packager.package_plugin(plugin_path, options)
        if result["success"]:
            return _success_response(result, result["message"])
        else:
            return _error_response(result["message"])
    except Exception as e:
        logger.error(f"插件打包异常: {e}", exc_info=True)
        return web.json_response({"success": False, "error": 服务器内部错误}, status=500)


async def list_packages(request):
    """列出已打包的文件（支持分页：?page=1&page_size=20，最大 page_size=100）"""
    try:
        packages = await asyncio.to_thread(_plugin_packager.list_packages) or []
        page, page_size, paginated = _分页参数(request)
        if paginated:
            payload = _分页响应(packages, page, page_size, True, list_key="data",
                              extra={"success": True, "message": "ok"})
            return web.json_response(payload)
        return _success_response(packages)
    except Exception as e:
        logger.error(f"列出打包文件异常: {e}", exc_info=True)
        return web.json_response({"success": False, "error": 服务器内部错误}, status=500)


async def download_package(request):
    """下载打包文件（返回文件流）"""
    try:
        filename = request.match_info['filename']

        # 安全检查：防止路径穿越
        if '..' in filename or '/' in filename or '\\' in filename:
            return _error_response("非法文件名", 403)

        if not filename.endswith('.zip'):
            return _error_response("仅支持下载 zip 文件", 400)

        file_path = _packages_dir / filename
        if not file_path.exists():
            return _error_response("文件不存在", 404)

        # 二次验证：确保解析后的路径仍在 packages 目录内
        if not file_path.resolve().parent == _packages_dir.resolve():
            return _error_response("非法文件路径", 403)

        return web.FileResponse(file_path, headers={
            'Content-Disposition': f'attachment; filename="{filename}"'
        })
    except Exception as e:
        logger.error(f"下载打包文件异常: {e}", exc_info=True)
        return web.json_response({"success": False, "error": 服务器内部错误}, status=500)


# ─── 模型市场 ──────────────────────────────────────────────────

async def model_market_search(request):
    """搜索模型（支持 HuggingFace / ModelScope）"""
    try:
        query = request.query.get('q', '')
        task = request.query.get('task', 'text-generation')
        limit = int(request.query.get('limit', '10'))
        source = request.query.get('source', 'huggingface')
        if not query:
            return _error_response("缺少搜索关键词 q")
        market = _get_market(source)
        results = await market.search_models(query, task=task, limit=limit)
        return _success_response(results)
    except (asyncio.TimeoutError, TimeoutError):
        logger.warning(
            f"模型市场搜索超时（source={request.query.get('source', 'huggingface')}, "
            f"q={request.query.get('q', '')}）"
        )
        return web.json_response(
            {"success": False, "error": "模型市场服务暂时不可用，请稍后重试", "data": None},
            status=502
        )
    except Exception as e:
        logger.error(f"模型市场搜索异常: {e}", exc_info=True)
        return _error_response(服务器内部错误, 500)


async def model_market_info(request):
    """获取模型详细信息（支持 HuggingFace / ModelScope）"""
    try:
        model_id = request.match_info['model_id']
        source = request.query.get('source', 'huggingface')
        market = _get_market(source)
        info = await market.get_model_info(model_id)
        if info is None:
            return _error_response("模型不存在或无法获取信息", 404)
        return _success_response(info)
    except Exception as e:
        logger.error(f"获取模型信息异常: {e}", exc_info=True)
        return _error_response(服务器内部错误, 500)


async def model_market_download(request):
    """下载模型到本地（后台任务，支持 HuggingFace / ModelScope）"""
    try:
        client_ip = request.remote or "unknown"
        if not _rate_limiter.is_allowed(client_ip):
            return _error_response("请求过于频繁，请稍后重试", 429)

        data = await request.json()
        model_id = data.get('model_id', '')
        source = data.get('source', 'huggingface')
        if not model_id:
            return _error_response("缺少 model_id")

        market = _get_market(source)
        # 启动后台下载任务
        asyncio.create_task(market.download_model(model_id))
        return _success_response(
            {"model_id": model_id, "source": source, "status": "downloading"},
            message="下载任务已启动",
        )
    except Exception as e:
        logger.error(f"启动模型下载异常: {e}", exc_info=True)
        return _error_response(服务器内部错误, 500)


async def model_market_local(request):
    """列出已下载的模型（支持分页：?page=1&page_size=20，最大 page_size=100）"""
    try:
        models = await asyncio.to_thread(_model_market.list_local_models) or []
        page, page_size, paginated = _分页参数(request)
        if paginated:
            payload = _分页响应(models, page, page_size, True, list_key="data",
                              extra={"success": True, "message": "ok"})
            return web.json_response(payload)
        return _success_response(models)
    except Exception as e:
        logger.error(f"获取本地模型列表异常: {e}", exc_info=True)
        return _error_response(服务器内部错误, 500)


async def model_market_download_status(request):
    """获取下载进度"""
    try:
        model_id = request.query.get('model_id', '')
        if not model_id:
            return _error_response("缺少 model_id")
        status = _model_market.get_download_status(model_id)
        return _success_response(status)
    except Exception as e:
        logger.error(f"获取下载状态异常: {e}", exc_info=True)
        return _error_response(服务器内部错误, 500)


# ─── 模板市场 ─────────────────────────────────────────────────

async def list_templates(request):
    """获取所有可用模板列表（支持分页：?page=1&page_size=20，最大 page_size=100）"""
    try:
        templates = await asyncio.to_thread(_template_market.list_templates) or []
        page, page_size, paginated = _分页参数(request)
        payload = _分页响应(templates, page, page_size, paginated, list_key="templates",
                          extra={"success": True})
        return web.json_response(payload)
    except Exception as e:
        logger.error(f"获取模板列表异常: {e}", exc_info=True)
        return web.json_response({"success": False, "error": 服务器内部错误}, status=500)


async def create_from_template(request):
    """从模板创建新项目"""
    try:
        client_ip = request.remote or "unknown"
        if not _rate_limiter.is_allowed(client_ip):
            return _error_response("请求过于频繁，请稍后重试", 429)

        data = await request.json()
        template_id = data.get("template_id", "")
        project_name = data.get("project_name", "").strip()
        entry_type = data.get("entry_type", "canvas")
        options = data.get("options", [])
        custom_names = data.get("custom_names", {})

        if not template_id:
            return _error_response("缺少 template_id 参数")
        if not project_name:
            return _error_response("缺少 project_name 参数")

        # P1-1：同步文件复制/写入放入线程池
        result = await asyncio.to_thread(
            _template_market.create_from_template, template_id, project_name, entry_type, options, custom_names
        )
        status = 200 if result["success"] else 400
        return web.json_response(result, status=status)
    except Exception as e:
        logger.error(f"从模板创建异常: {e}", exc_info=True)
        return web.json_response({"success": False, "error": 服务器内部错误}, status=500)


def register_市场与打包路由(routes):
    """注册市场与打包相关端点"""
    # 插件打包
    routes.post('/ai-coder/package-plugin')(package_plugin)
    routes.get('/ai-coder/packages')(list_packages)
    routes.get('/ai-coder/packages/download/{filename}')(download_package)

    # 模型市场
    routes.get('/ai-coder/model-market/search')(model_market_search)
    routes.get('/ai-coder/model-market/info/{model_id:.+}')(model_market_info)
    routes.post('/ai-coder/model-market/download')(model_market_download)
    routes.get('/ai-coder/model-market/local')(model_market_local)
    routes.get('/ai-coder/model-market/download-status')(model_market_download_status)

    # 模板市场
    routes.get('/ai-coder/templates')(list_templates)
    routes.post('/ai-coder/templates/create')(create_from_template)
