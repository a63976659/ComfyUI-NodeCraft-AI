"""GitHub 路由模块

包含：
- POST /ai-coder/github-sync             同步插件到 GitHub
- POST /ai-coder/github-check-repo       检查仓库是否存在
- POST /ai-coder/github-test-connection  测试 Token 连接
"""
from pathlib import Path

import asyncio

from aiohttp import web

from .路由公共 import _error_response, _rate_limiter
from .文件读写操作 import load_settings
from .系统环境映射 import get_custom_nodes_path
from .GitHub同步 import (
    sync_to_github, check_repo_exists, test_github_connection, validate_repo_name,
)


async def github_sync(request):
    """同步插件代码到 GitHub 仓库"""
    try:
        client_ip = request.remote or "unknown"
        if not _rate_limiter.is_allowed(client_ip):
            return _error_response("请求过于频繁，请稍后重试", 429)

        data = await request.json()
        plugin_path = data.get("plugin_path", "")
        repo_name = data.get("repo_name", "")
        ignore_gitignore = data.get("ignore_gitignore", False)

        # 验证仓库名
        name_check = validate_repo_name(repo_name)
        if not name_check["valid"]:
            return web.json_response({"status": "error", "error": name_check["message"]}, status=400)

        # 解析插件路径 - 如果不是绝对路径，拼接 custom_nodes 目录
        resolved_path = Path(plugin_path)
        if not resolved_path.is_absolute():
            resolved_path = get_custom_nodes_path() / plugin_path

        if not resolved_path.exists():
            return web.json_response({"status": "error", "error": f"插件路径不存在: {plugin_path}"}, status=404)

        # 从 settings 获取 visibility
        # P1-1：同步 I/O 放入线程池
        settings = await asyncio.to_thread(load_settings)
        visibility = settings.get("github_visibility", "public")

        result = await sync_to_github(
            plugin_dir=str(resolved_path),
            repo_name=repo_name,
            ignore_gitignore=ignore_gitignore,
            visibility=visibility
        )

        if result["success"]:
            return web.json_response({
                "status": "success",
                "message": result["message"],
                "repo_url": result.get("repo_url", ""),
                "uploaded_count": result.get("uploaded_count", 0)
            })
        else:
            return web.json_response({
                "status": "error",
                "error": result.get("error", "未知错误")
            }, status=400)
    except Exception as e:
        return web.json_response({"status": "error", "error": str(e)}, status=500)


async def github_check_repo(request):
    """检查 GitHub 仓库是否存在"""
    try:
        client_ip = request.remote or "unknown"
        if not _rate_limiter.is_allowed(client_ip):
            return _error_response("请求过于频繁，请稍后重试", 429)

        data = await request.json()
        repo_name = data.get("repo_name", "")

        if not repo_name:
            return web.json_response({"status": "error", "error": "缺少 repo_name"}, status=400)

        # P1-1：同步 I/O 放入线程池
        settings = await asyncio.to_thread(load_settings)
        token = settings.get("github_token", "")
        username = settings.get("github_username", "")

        if not token or not username:
            return web.json_response({"status": "error", "error": "GitHub 未配置"}, status=400)

        exists = await check_repo_exists(token, username, repo_name)
        return web.json_response({"status": "success", "exists": exists, "repo_name": repo_name})
    except Exception as e:
        return web.json_response({"status": "error", "error": str(e)}, status=500)


async def github_test_connection(request):
    """测试 GitHub Token 连接有效性"""
    try:
        client_ip = request.remote or "unknown"
        if not _rate_limiter.is_allowed(client_ip):
            return _error_response("请求过于频繁，请稍后重试", 429)

        # P1-1：同步 I/O 放入线程池
        settings = await asyncio.to_thread(load_settings)
        token = settings.get("github_token", "")
        username = settings.get("github_username", "")

        if not token:
            return web.json_response({"valid": False, "message": "Token 未配置"})
        if not username:
            return web.json_response({"valid": False, "message": "用户名未配置"})

        result = await test_github_connection(token, username)
        return web.json_response(result)
    except Exception as e:
        return web.json_response({"valid": False, "message": str(e)}, status=500)


def register_GitHub路由(routes):
    """注册 GitHub 集成端点"""
    routes.post("/ai-coder/github-sync")(github_sync)
    routes.post("/ai-coder/github-check-repo")(github_check_repo)
    routes.post("/ai-coder/github-test-connection")(github_test_connection)
