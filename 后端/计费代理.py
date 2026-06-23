"""计费代理 - 转发计费请求到 RanKing 云端

提供本地后端的计费代理路由，将扣款 / 余额 / 验证 / 退款请求
透明转发到 RanKing 云端服务。

端点列表：
- GET  /nca/billing/balance       → RanKing /api/open/balance
- POST /nca/billing/deduct        → RanKing /api/open/deduct
- POST /nca/billing/verify-login  → RanKing /api/open/verify-token
- POST /nca/billing/refund        → RanKing /api/open/refund

代理逻辑：
1. 透传 Authorization header（用户登录态）
2. 附加 X-Plugin-Key header（NodeCraft AI 插件密钥）
3. 透传 query / body 参数
4. 使用 aiohttp.ClientSession 异步转发
5. 统一错误处理（不可达 / 超时 / 上游错误）
"""
import asyncio
import os
from typing import Optional, Tuple

import aiohttp
from aiohttp import web

from .日志配置 import 获取日志器

logger = 获取日志器("计费代理")


# ─── 配置常量 ─────────────────────────────────────────────

# RanKing 云端地址（支持通过环境变量 RANKING_CLOUD_URL 覆盖）
RANKING_CLOUD_URL = os.environ.get(
    "RANKING_CLOUD_URL",
    "https://zhiwei666-comfyui-ranking-api.hf.space",
)

# NodeCraft AI 的插件密钥
NCA_PLUGIN_KEY = "nca_2026_nodecraft_ai_plugin"

# 请求超时时间（秒）
_REQUEST_TIMEOUT = 10

# 路由路径映射：本地路径 → RanKing 路径
_REMOTE_PATH_BALANCE = "/api/open/balance"
_REMOTE_PATH_DEDUCT = "/api/open/deduct"
_REMOTE_PATH_VERIFY = "/api/open/verify-token"
_REMOTE_PATH_REFUND = "/api/open/refund"


# ─── 通用工具 ─────────────────────────────────────────────

def _build_forward_headers(request: web.Request) -> dict:
    """构建转发到 RanKing 的请求头

    - 必带 X-Plugin-Key：插件身份认证
    - 透传 Authorization：用户登录态
    - 透传 Content-Type：保持请求语义
    """
    headers = {
        "X-Plugin-Key": NCA_PLUGIN_KEY,
    }
    auth = request.headers.get("Authorization")
    if auth:
        headers["Authorization"] = auth
    content_type = request.headers.get("Content-Type")
    if content_type:
        headers["Content-Type"] = content_type
    return headers


def _service_unavailable_response() -> web.Response:
    """RanKing 云端不可达的统一响应"""
    return web.json_response(
        {
            "error": "billing_service_unavailable",
            "message": "计费服务暂时不可用，请稍后重试",
        },
        status=503,
    )


def _timeout_response() -> web.Response:
    """请求超时的统一响应"""
    return web.json_response(
        {
            "error": "timeout",
            "message": "请求超时",
        },
        status=504,
    )


async def _read_request_body(request: web.Request) -> Tuple[Optional[bytes], Optional[str]]:
    """读取原始请求体，保留原 Content-Type 以原样转发

    返回 (raw_body, content_type)；body 为空时返回 (None, content_type)。
    """
    if request.can_read_body:
        raw = await request.read()
        return (raw if raw else None), request.headers.get("Content-Type")
    return None, request.headers.get("Content-Type")


async def _forward_request(
    request: web.Request,
    method: str,
    remote_path: str,
    *,
    forward_query: bool = False,
    forward_body: bool = False,
) -> web.Response:
    """通用转发逻辑

    Args:
        request: 本地接收到的请求
        method: HTTP 方法（GET / POST）
        remote_path: 目标 RanKing 路径
        forward_query: 是否透传 query 参数
        forward_body: 是否透传 body
    """
    target_url = f"{RANKING_CLOUD_URL.rstrip('/')}{remote_path}"
    headers = _build_forward_headers(request)
    params = dict(request.query) if forward_query else None

    body_bytes: Optional[bytes] = None
    if forward_body:
        body_bytes, _ = await _read_request_body(request)

    timeout = aiohttp.ClientTimeout(total=_REQUEST_TIMEOUT)

    try:
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.request(
                method,
                target_url,
                headers=headers,
                params=params,
                data=body_bytes,
            ) as upstream:
                resp_body = await upstream.read()
                # 透传上游响应（保留状态码与 Content-Type）
                resp_content_type = upstream.headers.get("Content-Type", "application/json")
                return web.Response(
                    body=resp_body,
                    status=upstream.status,
                    content_type=resp_content_type.split(";")[0].strip() or "application/json",
                )
    except asyncio.TimeoutError:
        logger.warning(f"计费代理请求超时: {method} {target_url}")
        return _timeout_response()
    except aiohttp.ClientConnectorError as e:
        logger.warning(f"计费云端不可达: {target_url} - {e}")
        return _service_unavailable_response()
    except aiohttp.ClientError as e:
        logger.warning(f"计费代理请求失败: {target_url} - {e}")
        return _service_unavailable_response()
    except Exception as e:
        logger.exception(f"计费代理未知异常: {target_url} - {e}")
        return _service_unavailable_response()


# ─── 路由处理器 ───────────────────────────────────────────

async def 查询余额(request: web.Request) -> web.Response:
    """GET /nca/billing/balance → RanKing /api/open/balance"""
    return await _forward_request(
        request,
        "GET",
        _REMOTE_PATH_BALANCE,
        forward_query=True,
    )


async def 扣款(request: web.Request) -> web.Response:
    """POST /nca/billing/deduct → RanKing /api/open/deduct"""
    return await _forward_request(
        request,
        "POST",
        _REMOTE_PATH_DEDUCT,
        forward_body=True,
    )


async def 验证登录(request: web.Request) -> web.Response:
    """POST /nca/billing/verify-login → RanKing /api/open/verify-token"""
    response = await _forward_request(
        request,
        "POST",
        _REMOTE_PATH_VERIFY,
        forward_body=True,
    )
    # 登录验证成功后，后台静默同步知识库
    if 200 <= response.status < 300:
        auth_header = request.headers.get("Authorization", "")
        token = auth_header.replace("Bearer ", "").replace("bearer ", "").strip()
        if token:
            asyncio.create_task(_后台知识库同步(token))
    return response


async def _后台知识库同步(token: str) -> None:
    """后台执行知识库同步，失败不影响登录"""
    try:
        from .知识库同步 import 执行知识库同步
        await 执行知识库同步(token)
    except Exception as e:
        logger.warning(f"知识库同步失败(不影响使用): {e}")


async def 退款(request: web.Request) -> web.Response:
    """POST /nca/billing/refund → RanKing /api/open/refund"""
    return await _forward_request(
        request,
        "POST",
        _REMOTE_PATH_REFUND,
        forward_body=True,
    )


# ─── 路由注册 ─────────────────────────────────────────────

def register_计费代理路由(routes) -> None:
    """注册计费代理端点到 PromptServer 路由表"""
    routes.get("/nca/billing/balance")(查询余额)
    routes.post("/nca/billing/deduct")(扣款)
    routes.post("/nca/billing/verify-login")(验证登录)
    routes.post("/nca/billing/refund")(退款)
