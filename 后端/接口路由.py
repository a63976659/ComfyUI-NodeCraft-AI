"""接口路由 - 主入口

按功能分组将路由注册委托给子模块。

子模块清单：
- 路由公共.py            共享工具与单例（导入即完成 app 初始化、审计中间件挂载）
- 认证路由.py            审计日志 / 性能指标 / API 文档（本地认证端点已移除）
- 会话路由.py            sessions CRUD + messages
- 聊天路由.py            /chat 与 /chat-stream
- 设置与模型路由.py      settings + 模型管理 + local-models
- 文件路由.py            create-folder / browse / read / write / analyze
- GitHub路由.py          GitHub 同步
- 市场与打包路由.py      package / model-market / templates
- WebSocket路由.py       /ai-coder/ws
- 计费代理.py            /nca/billing/* 计费代理（转发到 RanKing 云端）
"""
import os
from urllib.parse import urlparse

from aiohttp import web
from server import PromptServer

# 导入 路由公共 触发 app/auth/audit/agent 初始化（必须最先导入）
from . import 路由公共  # noqa: F401
from .路由公共 import _校验CSRF令牌
from .系统环境映射 import 是否云端环境

from .认证路由 import register_审计与文档路由
from .会话路由 import register_会话路由
from .聊天路由 import register_聊天路由
from .设置与模型路由 import register_设置与模型路由
from .文件路由 import register_文件路由
from .GitHub路由 import register_GitHub路由
from .市场与打包路由 import register_市场与打包路由
from .WebSocket路由 import register_WebSocket路由
from .魔搭同步 import register_魔搭同步路由
from .踩坑记录 import register_踩坑记录路由
from .计费代理 import register_计费代理路由
from .日志配置 import 获取日志器

logger = 获取日志器("接口路由")


# ─── L1: CORS 中间件（手动实现，无需 aiohttp_cors 外部依赖）──────


def _获取允许的Origin白名单():
    """从环境变量 ALLOWED_ORIGINS 读取 Origin 白名单（逗号分隔）。

    未配置时返回空列表，调用方按 localhost 默认规则处理。
    """
    raw = os.environ.get("ALLOWED_ORIGINS", "")
    if raw.strip():
        return [o.strip() for o in raw.split(",") if o.strip()]
    return []


def _Origin是否允许(origin):
    """校验 Origin 是否在白名单中。

    优先匹配 ALLOWED_ORIGINS 环境变量配置的白名单；
    未配置时默认只允许 localhost 相关 Origin。
    """
    白名单 = _获取允许的Origin白名单()
    if 白名单:
        return origin in 白名单
    # 未配置白名单时，默认只允许 localhost 相关 Origin
    try:
        hostname = urlparse(origin).hostname
        return hostname in ("localhost", "127.0.0.1", "::1")
    except Exception:
        return False


@web.middleware
async def cors_中间件(request, handler):
    """统一为 /ai-coder/* 与 /v1/* 端点添加 CORS 响应头。

    - 本地环境：反射 Origin（方便本地开发）；
    - 云端环境：使用 Origin 白名单，仅允许配置的 Origin；
    - OPTIONS 预检请求：直接返回 204 + CORS 头；
    - 其他请求：转发给后续 handler，并在响应上叠加 CORS 头。
    """
    origin = request.headers.get("Origin", "*")

    # 云端环境：校验 Origin 白名单，不在白名单时不设置 CORS 头
    允许反射 = True
    if 是否云端环境() and origin != "*":
        允许反射 = _Origin是否允许(origin)

    if request.method == "OPTIONS":
        response = web.Response(status=204 if 允许反射 else 403)
    else:
        try:
            response = await handler(request)
        except web.HTTPException as exc:
            response = exc

    if 允许反射:
        try:
            response.headers["Access-Control-Allow-Origin"] = origin
            response.headers["Access-Control-Allow-Methods"] = "GET, POST, PUT, DELETE, OPTIONS"
            response.headers["Access-Control-Allow-Headers"] = (
                "Content-Type, Authorization, X-CSRF-Token, X-Idempotency-Key"
            )
            response.headers["Access-Control-Allow-Credentials"] = "true"
            response.headers["Access-Control-Expose-Headers"] = "*"
        except Exception:
            pass

    if isinstance(response, web.HTTPException):
        raise response
    return response


try:
    _app_for_cors = PromptServer.instance.app
    if cors_中间件 not in _app_for_cors.middlewares:
        _app_for_cors.middlewares.append(cors_中间件)
        logger.info("✓ CORS 中间件已注册")
except Exception as _cors_e:
    logger.warning(f"CORS 中间件注册失败（忽略）: {_cors_e}")


# ─── L1.5: CSRF 校验中间件（在 CORS 之后注册）────────────────
# aiohttp 中间件按注册顺序外到内生效：cors 负责响应 CORS 头与 OPTIONS 预检，
# csrf 在其内部运行，确保：
# - OPTIONS 预检不会被 CSRF 拦截（cors 中间件直接返回 204）
# - CSRF 返回的 403 响应仍会由 cors 中间件叠加 CORS 响应头
@web.middleware
async def csrf_中间件(request, handler):
    """对本插件的写操作请求执行 CSRF 校验（遵循 _校验CSRF令牌 的校验与豁免规则）。"""
    校验失败响应 = _校验CSRF令牌(request)
    if 校验失败响应 is not None:
        return 校验失败响应
    return await handler(request)


try:
    _app_for_csrf = PromptServer.instance.app
    if csrf_中间件 not in _app_for_csrf.middlewares:
        _app_for_csrf.middlewares.append(csrf_中间件)
        logger.info("✓ CSRF 中间件已注册")
except Exception as _csrf_e:
    logger.warning(f"CSRF 中间件注册失败（忽略）: {_csrf_e}")


# 获取 PromptServer 路由表
routes = PromptServer.instance.routes

# 依次注册各子模块路由
register_审计与文档路由(routes)
register_会话路由(routes)
register_聊天路由(routes)
register_设置与模型路由(routes)
register_文件路由(routes)
register_GitHub路由(routes)
register_市场与打包路由(routes)
register_WebSocket路由(routes)
register_魔搭同步路由(routes)
register_踩坑记录路由(routes)
register_计费代理路由(routes)


# ─── L2: API 版本前缀 /v1/ ──────────────────────────────────
# 在所有子模块路由注册完成后，遍历当前 RouteTableDef，为每个
# /ai-coder/* 路由再注册一份 /v1/ai-coder/* 别名，同时保留无前缀
# 路由作为向后兼容。
def _注册v1前缀别名(routes_table) -> int:
    """为现有路由注册 /v1 前缀别名，返回成功注册的别名数量。"""
    计数 = 0
    现有 = list(routes_table)
    for r in 现有:
        try:
            path = getattr(r, "path", None)
            method = getattr(r, "method", None)
            handler = getattr(r, "handler", None)
            if not path or not method or not handler:
                continue
            if path.startswith("/v1/") or path == "/v1":
                continue
            routes_table.route(method, f"/v1{path}")(handler)
            计数 += 1
        except Exception:
            continue
    return 计数


try:
    _v1计数 = _注册v1前缀别名(routes)
    logger.info(f"✓ API 版本前缀 /v1 已注册（{_v1计数} 个别名）")
except Exception as _v1_e:
    logger.warning(f"v1 前缀别名注册失败（忽略）: {_v1_e}")
