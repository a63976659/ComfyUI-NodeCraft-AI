"""计费代理 - 转发计费请求到 RanKing 云端

提供本地后端的计费代理路由，将扣款 / 余额 / 验证 / 退款请求
透明转发到 RanKing 云端服务。

端点列表：
- GET  /nca/billing/balance       → RanKing /api/open/balance
- GET  /nca/billing/token-balance → NCA 云端 /api/open/token-balance
- POST /nca/billing/deduct        → RanKing /api/open/deduct
- POST /nca/billing/verify-login  → RanKing /api/open/verify-token
- POST /nca/billing/refund        → RanKing /api/open/refund
- POST /nca/billing/membership-purchase → RanKing 扣费 + NCA 云端记录

代理逻辑：
1. 透传 Authorization header（用户登录态）
2. 附加 X-Plugin-Key header（NodeCraft AI 插件密钥）
3. 透传 query / body 参数
4. 使用 aiohttp.ClientSession 异步转发
5. 统一错误处理（不可达 / 超时 / 上游错误）
"""
import asyncio
import json as _json
import os
import time
import uuid
from datetime import datetime
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

# NodeCraft AI 的插件密钥（支持环境变量覆盖，避免源码公开后密钥暴露）
NCA_PLUGIN_KEY = os.environ.get(
    "NCA_PLUGIN_KEY",
    "nca_2026_nodecraft_ai_plugin",
)

# 内置降级定价（云端不可达时使用）
DEFAULT_PRICING = {
    "deepseek-v4-pro": {
        "input_price_per_million": 3.0,
        "input_cached_price_per_million": 0.025,
        "output_price_per_million": 6.0,
        "is_free": False,
    },
    "glm-5.2": {
        "input_price_per_million": 5.0,
        "input_cached_price_per_million": 0.5,
        "output_price_per_million": 10.0,
        "is_free": False,
    },
    "deepseek-chat": {
        "input_price_per_million": 0.5,
        "input_cached_price_per_million": 0.01,
        "output_price_per_million": 1.0,
        "is_free": False,
    },
}

# 模型名前缀 → 降级定价键（未精确匹配时按前缀查找）
_PREFIX_FALLBACK = {
    "glm": "glm-5.2",
    "deepseek": "deepseek-chat",
}

# 请求超时时间（秒）—— RanKing 云端调用
_REQUEST_TIMEOUT = 10

# NCA 云端调用超时时间（秒）—— ModelScope Studio 闲置后休眠，冷启动需较长时间
_NCA_REQUEST_TIMEOUT = 30

# 路由路径映射：本地路径 → RanKing 路径
_REMOTE_PATH_BALANCE = "/api/open/balance"
_REMOTE_PATH_DEDUCT = "/api/open/deduct"
_REMOTE_PATH_VERIFY = "/api/open/verify-token"
_REMOTE_PATH_REFUND = "/api/open/refund"
_REMOTE_PATH_PRICING = "/api/open/pricing/{model_id}"

# 正确的 NCA 云端 API 专用地址（ModelScope api-inference 域名）
# 注意：旧的 .ms.show 域名已被 ModelScope 废弃，会返回 403 错误码 10010101007
_NCA_CLOUD_CONTAINER_URL = "https://studio-zhuzhiwei74521-comfyui-nodecraft-ai.api-inference.modelscope.net"

# NodeCraft AI 云端服务地址
# 环境变量 NCA_CLOUD_URL 可覆盖，用于调试或切换环境
NCA_CLOUD_URL = os.environ.get(
    "NCA_CLOUD_URL",
    _NCA_CLOUD_CONTAINER_URL,
).rstrip("/")

# 迁移：自动修正已废弃的旧 URL 为正确的 API 专用地址
# 1. modelscope.cn/studios/ — 网页 URL，对所有路径返回 HTML 页面（HTTP 200）
# 2. .ms.show — 旧 Studio SDK 访问路径，已被 ModelScope 禁止 API 调用（返回 403）
if "modelscope.cn/studios/" in NCA_CLOUD_URL or ".ms.show" in NCA_CLOUD_URL:
    NCA_CLOUD_URL = _NCA_CLOUD_CONTAINER_URL


# ─── 通用工具 ─────────────────────────────────────────────

def _get_comfyui_port() -> int:
    """获取当前 ComfyUI 实例端口"""
    try:
        from server import PromptServer
        return getattr(PromptServer.instance, "port", 8188)
    except Exception:
        return 8188


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
            "message": "请检查你的RanKing账号登录状态",
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


# ─── NCA 云端余额同步 ─────────────────────────────────────────

def _获取modelscope_sdk_token() -> str:
    """从设置中读取 ModelScope SDK Token（用于通过 ModelScope 网关认证）"""
    try:
        from .文件读写操作 import load_settings
        settings = load_settings()
        return settings.get("modelscope_sdk_token", "")
    except Exception as e:
        logger.warning(f"读取 ModelScope SDK Token 失败: {e}")
        return ""


async def _云端同步余额(account: str) -> Optional[int]:
    """调用云端 sync-balance 获取 NCA token 余额

    返回 token 余额数值；云端不可达时返回 None。
    """
    result = await _云端同步用户信息(account)
    if result is not None:
        return result.get("nca_balance")
    return None


async def _云端同步用户信息(account: str) -> Optional[dict]:
    """调用云端 sync-balance 获取 NCA token 余额和会员信息

    返回 dict: {"nca_balance": int, "tier_info": {...} | None}；云端不可达时返回 None。
    """
    url = f"{NCA_CLOUD_URL}/api/open/sync-balance"
    headers = {"Content-Type": "application/json"}
    # 添加 ModelScope SDK Token 通过网关认证
    sdk_token = _获取modelscope_sdk_token()
    if sdk_token:
        headers["Authorization"] = f"Bearer {sdk_token}"
    body = {"plugin_key": NCA_PLUGIN_KEY, "user_id": account}
    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=15)) as session:
            async with session.post(url, json=body, headers=headers) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    if isinstance(data, dict) and data.get("status") == "success":
                        balance = data.get("nca_balance", 0)
                        tier_info = data.get("tier_info")  # 会员信息（可能为 None）
                        logger.info(f"云端同步用户信息成功: account={str(account)[:20]}, balance={balance}")
                        return {"nca_balance": int(balance), "tier_info": tier_info}
    except asyncio.TimeoutError:
        logger.warning(f"云端同步余额超时: account={str(account)[:20]}")
    except aiohttp.ClientError as e:
        logger.warning(f"云端同步余额失败(网络): account={str(account)[:20]}, error={e}")
    except Exception as e:
        logger.warning(f"云端同步余额异常: account={str(account)[:20]}, error={e}")
    return None


async def _云端记录交易(
    account: str,
    tx_type: str,
    amount: float = 0,
    reference_id: str = "",
    tokens_used: int = 0,
    reason: str = "",
    tier: str = "",
) -> Optional[dict]:
    """调用云端 record-transaction-local 记录交易

    返回云端响应 dict（含 nca_balance）；云端不可达时返回 None。
    """
    url = f"{NCA_CLOUD_URL}/api/open/record-transaction-local"
    headers = {"Content-Type": "application/json"}
    # 添加 ModelScope SDK Token 通过网关认证
    sdk_token = _获取modelscope_sdk_token()
    if sdk_token:
        headers["Authorization"] = f"Bearer {sdk_token}"
    body = {
        "plugin_key": NCA_PLUGIN_KEY,
        "user_id": account,
        "type": tx_type,
        "amount": amount,
        "tokens_used": tokens_used,
        "reason": reason or f"{tx_type}",
        "source_plugin": "NCA",
        "reference_id": reference_id,
    }
    # 会员购买时附加 tier 字段
    if tier:
        body["tier"] = tier
    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=15)) as session:
            async with session.post(url, json=body, headers=headers) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    if isinstance(data, dict) and data.get("status") == "success":
                        logger.info(
                            f"云端记录交易成功: account={str(account)[:20]}, "
                            f"type={tx_type}, nca_balance={data.get('nca_balance')}"
                        )
                        return data
                else:
                    resp_text = await resp.text()
                    logger.warning(
                        f"云端记录交易HTTP错误: account={str(account)[:20]}, "
                        f"type={tx_type}, status={resp.status}, body={resp_text[:200]}"
                    )
    except asyncio.TimeoutError:
        logger.warning(f"云端记录交易超时: account={str(account)[:20]}, type={tx_type}")
    except aiohttp.ClientError as e:
        logger.warning(f"云端记录交易失败(网络): account={str(account)[:20]}, type={tx_type}, error={e}")
    except Exception as e:
        logger.warning(f"云端记录交易异常: account={str(account)[:20]}, type={tx_type}, error={e}")
    return None


# ─── 路由处理器 ───────────────────────────────────────────

async def 查询余额(request: web.Request) -> web.Response:
    """GET /nca/billing/balance → RanKing /api/open/balance"""
    return await _forward_request(
        request,
        "GET",
        _REMOTE_PATH_BALANCE,
        forward_query=True,
    )


async def 查询模型定价(request: web.Request) -> web.Response:
    """GET /nca/billing/model-pricing/{model_id} → RanKing /api/open/pricing/{model_id}"""
    model_id = request.match_info.get("model_id", "")
    response = await _forward_request(
        request,
        "GET",
        _REMOTE_PATH_PRICING.format(model_id=model_id),
        forward_query=True,
    )
    # 云端不可达或返回错误时，降级返回本地默认定价
    if 200 <= response.status < 300:
        return response
    fallback = DEFAULT_PRICING.get(model_id)
    # 精确匹配失败时，按模型名前缀查找降级定价
    if not fallback:
        model_lower = model_id.lower()
        for prefix, fallback_key in _PREFIX_FALLBACK.items():
            if model_lower.startswith(prefix):
                fallback = DEFAULT_PRICING.get(fallback_key)
                break
    if fallback:
        return web.json_response(
            {
                "model_id": model_id,
                "pricing": fallback,
                "source": "local_fallback",
            }
        )
    return response


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
    # 登录验证成功后，注册 token→account 映射 + 同步 NCA 余额
    if 200 <= response.status < 300:
        # 优先从 Authorization 头获取 token，兜底从请求体中获取
        auth_header = request.headers.get("Authorization", "")
        token = auth_header.replace("Bearer ", "").replace("bearer ", "").strip()
        if not token:
            # 前端将 token 放在 body 而非 Authorization 头
            try:
                body_bytes = await request.read()
                body_data = _json.loads(body_bytes) if body_bytes else {}
                token = body_data.get("token", "") if isinstance(body_data, dict) else ""
            except Exception:
                token = ""
        if token:
            # 从响应中提取 account 并注册映射
            account = ""
            try:
                from .token计费 import 注册token账户映射, 保存NCA余额, 读取NCA余额
                resp_data = _json.loads(response.body)
                account = resp_data.get("account", "")
                if account:
                    注册token账户映射(token, account)
                    logger.info(f"登录映射注册: token=...{token[-8:]}, account={str(account)[:20]}")
                    # 将 ranking_token 持久化到 设置.json，供 API 模型客户端读取使用
                    try:
                        from .文件读写操作 import load_settings, save_settings
                        settings = load_settings()
                        if settings.get("ranking_token") != token:
                            settings["ranking_token"] = token
                            save_settings(settings)
                    except Exception as e:
                        logger.warning(f"ranking_token 持久化失败: {e}")
            except Exception as e:
                logger.warning(f"解析登录响应失败(不影响登录): {e}")

            # 登录成功后同步 NCA 余额与会员信息（云端优先，降级本地缓存）
            nca_balance = 0
            tier_info = None
            if account:
                try:
                    cloud_info = await _云端同步用户信息(account)
                    if cloud_info is not None:
                        nca_balance = cloud_info.get("nca_balance", 0)
                        tier_info = cloud_info.get("tier_info")
                        保存NCA余额(account, nca_balance)
                        # 缓存会员信息到本地
                        if tier_info:
                            _保存会员缓存(account, tier_info)
                    else:
                        # 云端不可达时从本地缓存读取
                        nca_balance = 读取NCA余额(account)
                        tier_info = _读取会员缓存(account)
                except Exception as e:
                    logger.warning(f"登录同步余额失败(降级本地缓存): {e}")
                    nca_balance = 读取NCA余额(account)
                    tier_info = _读取会员缓存(account)

                # 将 nca_balance 和 tier_info 注入到响应中返回给前端
                try:
                    resp_data = _json.loads(response.body)
                    resp_data["nca_balance"] = nca_balance
                    if tier_info:
                        resp_data["tier_info"] = tier_info
                    response = web.json_response(resp_data, status=response.status)
                except Exception as e:
                    logger.warning(f"注入 nca_balance/tier_info 到响应失败: {e}")
    return response


async def 退款(request: web.Request) -> web.Response:
    """POST /nca/billing/refund → RanKing /api/open/refund"""
    return await _forward_request(
        request,
        "POST",
        _REMOTE_PATH_REFUND,
        forward_body=True,
    )


async def 充值(request: web.Request) -> web.Response:
    """POST /nca/billing/recharge
    
    充值流程：
    1. 参数校验（account、amount、token）
    2. 调用本地 RanKing 扣费 POST http://127.0.0.1:{port}/ranking/local/deduct
    3. 扣费成功后调用云端 record-transaction-local 记录充值
    4. 云端记录成功 → 更新本地缓存 → 返回成功
    5. 云端记录失败 → 调用 RanKing 退款 → 返回失败
    """
    # 读取请求体
    try:
        body = await request.json()
    except Exception:
        return web.json_response({"success": False, "error": "无效的请求体"}, status=400)

    token = body.get("token", "")
    amount = body.get("amount", 0)
    account = body.get("account", "")

    # 前置校验
    try:
        amount = float(amount)
    except (TypeError, ValueError):
        return web.json_response({"success": False, "error": "金额必须为数字"}, status=400)

    if amount < 10:
        return web.json_response({"success": False, "error": "最小充值额度为10元"}, status=400)

    # 整数校验：RanKing 扣款要求整数金额，确保 RanKing 与 NCA 记录两侧数据一致
    if amount != int(amount):
        return web.json_response({"success": False, "error": "充值金额必须为整数"}, status=400)
    amount = int(amount)  # 后续所有 amount 使用均为 int 类型

    # 单笔扣款上限校验：RanKing 默认 max_deduct_per_tx=100，超过会被拒绝
    MAX_RECHARGE_AMOUNT = 100
    if amount > MAX_RECHARGE_AMOUNT:
        return web.json_response({
            "success": False,
            "error": f"单笔充值不能超过 {MAX_RECHARGE_AMOUNT} 元"
        }, status=400)

    if not token:
        return web.json_response({"success": False, "error": "缺少登录凭证"}, status=401)

    if not account:
        return web.json_response({"success": False, "error": "缺少账户信息"}, status=400)

    # 确保 token→account 映射已注册（充值时 body 携带 account，补充注册）
    from .token计费 import 注册token账户映射
    注册token账户映射(token, account)

    # 生成幂等性ID（追加随机后缀保证并发唯一性）
    reference_id = f"recharge_{account}_{int(time.time() * 1000)}_{uuid.uuid4().hex[:8]}"

    # 步骤1：调用 RanKing 本地路由扣减积分（同一 ComfyUI 实例内部代理到云端）
    ranking_body = {
        "token": token,
        "amount": int(amount),  # RanKing 要求整数
        "reason": f"用户充值{int(amount)}元",
        "reference_id": reference_id,
    }
    ranking_local_url = f"http://127.0.0.1:{_get_comfyui_port()}/ranking/local/deduct"
    ranking_headers = {
        "Content-Type": "application/json",
        "X-Plugin-Key": NCA_PLUGIN_KEY,
    }
    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=_REQUEST_TIMEOUT)) as session:
            async with session.post(
                ranking_local_url,
                headers=ranking_headers,
                json=ranking_body,
            ) as resp:
                ranking_resp_body = await resp.read()
                ranking_resp = web.Response(
                    body=ranking_resp_body,
                    status=resp.status,
                    content_type="application/json",
                )
    except asyncio.TimeoutError:
        logger.warning(f"RanKing 本地扣费超时: {ranking_local_url}")
        return _timeout_response()
    except aiohttp.ClientConnectorError as e:
        logger.warning(f"RanKing 本地路由不可达: {ranking_local_url} - {e}")
        return _service_unavailable_response()
    except aiohttp.ClientError as e:
        logger.warning(f"RanKing 本地扣费请求失败: {ranking_local_url} - {e}")
        return _service_unavailable_response()

    # 检查 RanKing 扣费结果
    if ranking_resp.status != 200:
        return ranking_resp

    try:
        ranking_data = _json.loads(ranking_resp.body)
    except (_json.JSONDecodeError, ValueError) as e:
        logger.error(
            f"RanKing 扣费响应解析失败: account={str(account)[:20]}, "
            f"amount={amount}, reference_id={reference_id[:50]}, "
            f"http_status={ranking_resp.status}, body={str(ranking_resp.body)[:200]}"
        )
        return web.json_response({
            "success": False,
            "error": "扣费服务返回异常响应",
            "reference_id": reference_id,
        }, status=502)
    # 防御非 dict 类型响应（如 JSON 数组或字符串），避免 .get() 调用抛出 AttributeError
    if not isinstance(ranking_data, dict):
        logger.error(
            f"RanKing 扣费响应非 dict 类型: account={str(account)[:20]}, "
            f"amount={amount}, reference_id={reference_id[:50]}, "
            f"http_status={ranking_resp.status}, body={str(ranking_resp.body)[:200]}"
        )
        return web.json_response({
            "success": False,
            "error": "扣费服务返回异常响应",
            "reference_id": reference_id,
        }, status=502)
    # 宽松模式判定（与 token计费.py 一致）：HTTP 200 且 success 不为 False
    # 缺失 success 字段时视为成功，因为 RanKing 可能只返回 {remaining_balance: xxx}
    if not (ranking_resp.status == 200 and ranking_data.get("success") is not False):
        # RanKing 扣费失败（HTTP错误或业务失败，如余额不足等）
        return web.json_response(ranking_data, status=200)

    # 提取 RanKing 返回的交易 ID 和余额（退款回滚及成功响应均需使用）
    ranking_transaction_id = ranking_data.get("transaction_id")
    ranking_remaining_balance = ranking_data.get("remaining_balance")

    # 防御性检查：RanKing 正常应返回 transaction_id，若缺失则记录告警
    # 不中断流程——NCA 记录仍可继续，仅退款回滚可能失败（已有降级处理）
    if not ranking_transaction_id:
        logger.warning(
            f"RanKing 扣费成功但未返回 transaction_id: "
            f"account={str(account)[:20]}, amount={amount}, "
            f"reference_id={reference_id[:50]}"
        )
    # 防御性检查：RanKing 未返回 remaining_balance 时兜底为 0，避免前端 null 显示异常
    if ranking_remaining_balance is None:
        logger.warning(
            f"RanKing 扣费成功但未返回 remaining_balance: "
            f"account={str(account)[:20]}, amount={amount}, "
            f"reference_id={reference_id[:50]}"
        )
        ranking_remaining_balance = 0  # 兜底值，避免前端 null 显示异常

    # 步骤2：调用云端记录充值交易，获取最新余额
    from .token计费 import 保存NCA余额 as _保存余额
    cloud_result = await _云端记录交易(
        account, "recharge", amount=amount,
        reference_id=reference_id, reason=f"用户充值{int(amount)}元"
    )
    if cloud_result:
        # 云端记录成功 → 更新本地缓存 → 返回成功
        new_nca_balance = int(cloud_result.get("nca_balance", 0))
        _保存余额(account, new_nca_balance)
        logger.info(
            f"充值成功: account={str(account)[:20]}, amount={amount}, "
            f"reference_id={reference_id[:50]}, nca_balance={new_nca_balance}"
        )
        return web.json_response({
            "success": True,
            "message": f"充值成功，到账 {int(amount)} 元",
            "balance": new_nca_balance,
            "reference_id": reference_id,
        })

    # 云端记录失败 → 调用 RanKing 本地退款 → 返回充值失败
    logger.warning(
        f"云端记录失败，尝试自动退款: account={str(account)[:20]}, "
        f"amount={amount}, reference_id={reference_id[:50]}"
    )
    refund_body = {
        "token": token,
        "amount": int(amount),
        "reason": "云端记录失败自动退款",
        "reference_id": reference_id,
    }
    refund_url = f"http://127.0.0.1:{_get_comfyui_port()}/ranking/local/refund"
    refund_headers = {
        "Content-Type": "application/json",
        "X-Plugin-Key": NCA_PLUGIN_KEY,
    }
    refund_success = False
    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=_REQUEST_TIMEOUT)) as session:
            async with session.post(
                refund_url,
                headers=refund_headers,
                json=refund_body,
            ) as refund_resp:
                if refund_resp.status == 200:
                    refund_data = await refund_resp.json()
                    if isinstance(refund_data, dict) and refund_data.get("success") is not False:
                        refund_success = True
                        logger.info(
                            f"自动退款成功: account={str(account)[:20]}, "
                            f"amount={amount}, reference_id={reference_id[:50]}"
                        )
                else:
                    refund_text = await refund_resp.text()
                    logger.error(
                        f"自动退款失败(HTTP {refund_resp.status}): "
                        f"account={str(account)[:20]}, amount={amount}, "
                        f"response={refund_text[:200]}"
                    )
    except Exception as e:
        logger.error(
            f"自动退款异常: account={str(account)[:20]}, "
            f"amount={amount}, error={str(e)[:200]}"
        )

    if refund_success:
        return web.json_response({
            "success": False,
            "error": "充值记录失败，已自动退回",
            "message": f"充值记录失败，{int(amount)} 元已自动退回您的账户",
            "refunded": True,
            "reference_id": reference_id,
        }, status=200)
    else:
        # 退款也失败了，记录严重错误，提示用户联系客服
        logger.error(
            f"严重：云端记录失败且退款失败！account={str(account)[:20]}, "
            f"amount={amount}, reference_id={reference_id[:50]}"
        )
        return web.json_response({
            "success": False,
            "error": "充值异常，请联系客服处理",
            "message": f"充值记录失败且退款异常，请联系客服并提供参考号: {reference_id[:30]}",
            "refunded": False,
            "reference_id": reference_id,
        }, status=200)


# NCA 云端交易记录推送地址（公开 Space，无需认证头即可直达 FastAPI 容器）
# 旧的 .ms.show 域名已完全废弃，所有请求均返回 403 错误码 10010101007
_NCA_CLOUD_RECORD_URL = f"{NCA_CLOUD_URL}/api/open/record-transaction-local"


async def _try_nca_cloud_record(
    token: str,
    account: str,
    amount: int,
    reference_id: str,
    ranking_transaction_id: str,
    ranking_remaining_balance: float,
) -> None:
    """异步尝试 NCA 云端记录（非阻塞，失败仅日志，不退款）

    NCA 云端 Space 为公开访问，不需要任何认证头即可到达 FastAPI 容器。
    业务验证通过 body 中的 plugin_key 完成。
    """
    nca_body = {
        "plugin_key": NCA_PLUGIN_KEY,  # 业务认证通过 body 传递
        "user_id": account,
        "type": "recharge",
        "amount": amount,
        "reason": f"用户充值{int(amount)}元",
        "source_plugin": "nca_recharge",
        "reference_id": reference_id,
        "ranking_transaction_id": ranking_transaction_id,  # RanKing 交易 ID，用于对账
        "ranking_balance": ranking_remaining_balance,       # 扣费后余额
    }
    headers = {
        "Content-Type": "application/json",
    }
    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=_NCA_REQUEST_TIMEOUT)) as session:
            async with session.post(
                _NCA_CLOUD_RECORD_URL,
                headers=headers,
                json=nca_body,
            ) as resp:
                nca_http_status = resp.status
                try:
                    nca_result = await resp.json()
                except Exception:
                    nca_result = {}
        # 判定 NCA 记录是否成功
        if not isinstance(nca_result, dict):
            nca_result = {}
        nca_success = nca_http_status == 200 and nca_result.get("status") == "success"
        if nca_success:
            logger.info(
                f"NCA 云端记录成功: account={str(account)[:20]}, "
                f"amount={amount}, reference_id={reference_id[:50]}"
            )
        else:
            logger.warning(
                f"NCA 云端记录失败（不影响充值结果）: account={str(account)[:20]}, "
                f"amount={amount}, reference_id={reference_id[:50]}, "
                f"http_status={nca_http_status}, response={str(nca_result)[:200]}"
            )
    except Exception as e:
        logger.warning(
            f"NCA 云端记录失败（不影响充值结果）: account={str(account)[:20]}, "
            f"amount={amount}, reference_id={reference_id[:50]}, error={str(e)[:200]}"
        )


# token 与积分的换算比例（1元 = 200000 token）
_TOKENS_PER_CREDIT = 200000

# 会员等级配置：积分费用与有效天数
# 说明：仅 cost 字段参与实际扣费逻辑；gift_tokens/days 为展示用途，
# 真实赠送与有效期由云端 _会员配置 计算（gift_yuan × 200000）。
_MEMBERSHIP_CONFIG = {
    "basic": {"cost": 1, "days": 0, "gift_tokens": 0},
    "pro": {"cost": 20, "days": 30, "gift_tokens": 2_400_000},
    "premium": {"cost": 50, "days": 30, "gift_tokens": 9_000_000},
}


# ─── 会员本地缓存 ─────────────────────────────────────

def _获取会员缓存路径() -> str:
    """获取会员信息缓存文件路径"""
    data_dir = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "数据", "用户"
    )
    return os.path.join(data_dir, "membership_cache.json")


def _保存会员缓存(account: str, tier_info: dict) -> None:
    """保存会员信息到本地缓存"""
    if not account or not tier_info:
        return
    cache_path = _获取会员缓存路径()
    try:
        data = {}
        if os.path.exists(cache_path):
            with open(cache_path, "r", encoding="utf-8") as f:
                data = _json.load(f)
        data[account] = {
            "tier": tier_info.get("tier", ""),
            "expire": tier_info.get("expire", ""),
            "api_available": tier_info.get("api_available", False),
            "last_updated": time.time(),
        }
        os.makedirs(os.path.dirname(cache_path), exist_ok=True)
        with open(cache_path, "w", encoding="utf-8") as f:
            _json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.warning(f"保存会员缓存失败: {e}")


def _读取会员缓存(account: str) -> Optional[dict]:
    """读取本地缓存的会员信息"""
    if not account:
        return None
    cache_path = _获取会员缓存路径()
    try:
        if os.path.exists(cache_path):
            with open(cache_path, "r", encoding="utf-8") as f:
                data = _json.load(f)
                entry = data.get(account)
                if isinstance(entry, dict) and entry.get("tier"):
                    return {
                        "tier": entry["tier"],
                        "expire": entry.get("expire", ""),
                        "api_available": entry.get("api_available", False),
                    }
    except Exception as e:
        logger.warning(f"读取会员缓存失败: {e}")
    return None


async def 检查API权限(request: web.Request) -> Optional[dict]:
    """检查当前用户是否有使用 API 模型的会员权限。

    权限来源：登录时云端 sync-balance 返回的 tier_info.api_available，
    已缓存到本地 membership_cache.json。

    返回：
      - dict {"api_available": bool, "tier": str}：可明确判定的权限信息
      - None：无法确定（未识别账户 / 本地无缓存且云端不可达）→ 调用方应降级放行
    """
    try:
        from .token计费 import 获取token对应账户
        # 优先从 Authorization 头取 token，兜底读取本地持久化的 ranking_token
        auth = request.headers.get("Authorization", "")
        token = auth.replace("Bearer ", "").replace("bearer ", "").strip()
        if not token:
            try:
                from .文件读写操作 import load_settings
                token = load_settings().get("ranking_token", "")
            except Exception:
                token = ""
        if not token:
            return None

        account = 获取token对应账户(token)
        if not account:
            return None

        # 优先读本地缓存（登录时已同步 tier_info）
        cache = _读取会员缓存(account)
        if cache is not None:
            return {
                "api_available": bool(cache.get("api_available", False)),
                "tier": cache.get("tier", ""),
            }

        # 本地无缓存 → 尝试云端同步
        cloud_info = await _云端同步用户信息(account)
        if cloud_info is not None:
            tier_info = cloud_info.get("tier_info")
            if tier_info:
                _保存会员缓存(account, tier_info)
                return {
                    "api_available": bool(tier_info.get("api_available", False)),
                    "tier": tier_info.get("tier", ""),
                }
            # 云端明确返回无会员信息 → 确认无 API 权限
            return {"api_available": False, "tier": ""}

        # 云端不可达且本地无缓存 → 无法确定，降级放行
        return None
    except Exception as e:
        logger.warning(f"检查API权限异常（降级放行）: {e}")
        return None


async def 检查会员资格(request: web.Request) -> Optional[web.Response]:
    """检查用户是否有会员资格，非会员返回403。

    三层会员权限体系：非会员禁止使用任何功能。

    返回：
      - None：放行（有会员资格，或云端不可达降级放行）
      - web.Response：拦截（非会员，返回403）
    """
    # 复用现有的权限检查逻辑获取 tier_info
    tier_info = await 检查API权限(request)
    if tier_info is None:
        # 云端不可达且无本地缓存 → 降级放行
        return None
    tier = tier_info.get("tier", "")
    if not tier:
        # 无会员等级 = 非会员 → 拦截
        return web.json_response({
            "error": True,
            "code": "NO_MEMBERSHIP",
            "message": "此功能需要会员权限，请先购买会员"
        }, status=403)
    return None  # 有会员资格，放行


async def 查询用量(request: web.Request) -> web.Response:
    """GET /nca/billing/usage → RanKing 云端 /api/open/transactions
    
    改为查询 RanKing 云端交易记录（NCA 云端不可用），并转换为前端期望的格式。
    """
    # 构建请求头
    headers = {
        "X-Plugin-Key": NCA_PLUGIN_KEY,
    }
    auth = request.headers.get("Authorization")
    if auth:
        headers["Authorization"] = auth

    # 解析 days 参数，转换为 start_time
    days = int(request.query.get("days", 30))
    start_time = int(time.time()) - days * 86400

    target_url = f"{RANKING_CLOUD_URL}/api/open/transactions"
    params = {
        "page": "1",
        "page_size": "100",
        "start_time": str(start_time),
    }

    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=_REQUEST_TIMEOUT)) as session:
            async with session.get(target_url, headers=headers, params=params) as resp:
                if resp.status != 200:
                    resp_body = await resp.read()
                    return web.Response(
                        body=resp_body,
                        status=resp.status,
                        content_type="application/json",
                    )
                data = await resp.json()

        # 将 RanKing 交易记录转换为前端期望的用量格式
        transactions = data.get("transactions", [])
        total_count = data.get("total", len(transactions))
        total_amount = 0
        by_type = {}

        for tx in transactions:
            amt = abs(int(tx.get("amount", 0)))
            tokens = amt * _TOKENS_PER_CREDIT
            total_amount += tokens
            tx_type = tx.get("reason") or tx.get("type") or "其他"
            if tx_type not in by_type:
                by_type[tx_type] = {"total_tokens": 0, "count": 0}
            by_type[tx_type]["total_tokens"] += tokens
            by_type[tx_type]["count"] += 1

        return web.json_response({
            "success": True,
            "transaction_count": total_count,
            "total_tokens": total_amount,
            "by_type": by_type,
        })
    except asyncio.TimeoutError:
        logger.warning(f"查询用量超时: {target_url}")
        return _timeout_response()
    except aiohttp.ClientConnectorError as e:
        logger.warning(f"RanKing 云端不可达: {e}")
        return _service_unavailable_response()
    except aiohttp.ClientError as e:
        logger.warning(f"查询用量失败: {target_url} - {e}")
        return _service_unavailable_response()


async def 查询token余额(request: web.Request) -> web.Response:
    """GET /nca/billing/token-balance → 返回 NCA token 余额
    
    优先调用云端 sync-balance 获取最新余额，降级读本地缓存。
    """
    from .token计费 import 获取token对应账户, 读取NCA余额, 保存NCA余额, TOKENS_PER_CREDIT as _TPC2
    # 从 Authorization 头提取 token，查找对应 account
    auth = request.headers.get("Authorization", "")
    token = auth.replace("Bearer ", "").replace("bearer ", "").strip()
    account = 获取token对应账户(token)

    if not account:
        # 未找到账户映射（可能未登录或缓存已失效）
        return web.json_response({
            "token_balance": 0,
            "tokens_per_credit": _TPC2,
            "error": "未找到账户信息，请重新登录",
        })

    # 优先从云端同步余额
    cloud_balance = await _云端同步余额(account)
    if cloud_balance is not None:
        保存NCA余额(account, cloud_balance)
        balance = cloud_balance
    else:
        # 云端不可达时降级读本地缓存
        balance = 读取NCA余额(account)

    return web.json_response({
        "token_balance": balance,
        "tokens_per_credit": _TPC2,
    })


# ─── 路由注册 ───────────────────────────────────────────

async def 购买会员(request: web.Request) -> web.Response:
    """POST /nca/billing/membership-purchase

    会员购买流程：
    1. 参数校验（tier 必须为 basic/pro/premium）
    2. 如果费用 > 0，调用 RanKing 本地扣减积分
    3. 调用云端 record-transaction-local 记录会员购买
    4. 云端成功 → 更新本地缓存 → 返回成功
    5. 云端失败且已扣费 → RanKing 退款 → 返回失败
    """
    # 读取请求体
    try:
        body = await request.json()
    except Exception:
        return web.json_response({"success": False, "error": "无效的请求体"}, status=400)

    token = body.get("token", "")
    account = body.get("account", "")
    tier = body.get("tier", "").strip()

    # 参数校验
    if not token:
        return web.json_response({"success": False, "error": "缺少登录凭证"}, status=401)
    if not account:
        return web.json_response({"success": False, "error": "缺少账户信息"}, status=400)
    if tier not in _MEMBERSHIP_CONFIG:
        return web.json_response(
            {"success": False, "error": f"无效的会员等级: {tier}，可选: basic/pro/premium"},
            status=400,
        )

    config = _MEMBERSHIP_CONFIG[tier]
    cost = config["cost"]

    # 拒绝重复购买：若购买的是 basic 且用户当前已有会员（任何等级），在扣费前直接拒绝
    if tier == "basic":
        当前会员 = _读取会员缓存(account)
        if 当前会员 and 当前会员.get("tier"):
            return web.json_response({"success": False, "error": "您已拥有基础会员"}, status=400)

    # 确保 token→account 映射已注册
    from .token计费 import 注册token账户映射, 保存NCA余额
    注册token账户映射(token, account)

    # 生成幂等性ID
    reference_id = f"membership_{tier}_{account}_{int(time.time() * 1000)}_{uuid.uuid4().hex[:8]}"

    ranking_transaction_id = None

    # 步骤1：如果有费用，调用 RanKing 扣减积分
    if cost > 0:
        ranking_body = {
            "token": token,
            "amount": cost,
            "reason": f"购买{tier}会员",
            "reference_id": reference_id,
        }
        ranking_local_url = f"http://127.0.0.1:{_get_comfyui_port()}/ranking/local/deduct"
        ranking_headers = {
            "Content-Type": "application/json",
            "X-Plugin-Key": NCA_PLUGIN_KEY,
        }
        try:
            async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=_REQUEST_TIMEOUT)) as session:
                async with session.post(
                    ranking_local_url,
                    headers=ranking_headers,
                    json=ranking_body,
                ) as resp:
                    if resp.status != 200:
                        resp_data = await resp.json()
                        return web.json_response(resp_data, status=resp.status)
                    ranking_data = await resp.json()
                    if not isinstance(ranking_data, dict) or ranking_data.get("success") is False:
                        return web.json_response(
                            ranking_data if isinstance(ranking_data, dict) else {"success": False, "error": "RanKing 扣费异常"},
                            status=200,
                        )
                    ranking_transaction_id = ranking_data.get("transaction_id")
        except asyncio.TimeoutError:
            logger.warning(f"RanKing 会员扣费超时: tier={tier}, account={str(account)[:20]}")
            return _timeout_response()
        except aiohttp.ClientConnectorError as e:
            logger.warning(f"RanKing 本地路由不可达: {e}")
            return _service_unavailable_response()
        except aiohttp.ClientError as e:
            logger.warning(f"RanKing 会员扣费请求失败: {e}")
            return _service_unavailable_response()

    # 步骤2：调用云端记录会员购买交易
    cloud_result = await _云端记录交易(
        account,
        "membership_purchase",
        amount=cost,
        reference_id=reference_id,
        reason=f"购买{tier}会员",
        tier=tier,
    )

    if cloud_result:
        # 云端记录成功
        tier_info = cloud_result.get("tier_info")
        new_nca_balance = int(cloud_result.get("nca_balance", 0))

        # 更新本地缓存
        保存NCA余额(account, new_nca_balance)
        if tier_info:
            _保存会员缓存(account, tier_info)

        logger.info(
            f"会员购买成功: account={str(account)[:20]}, tier={tier}, "
            f"cost={cost}, nca_balance={new_nca_balance}"
        )
        return web.json_response({
            "success": True,
            "message": f"成功开通 {tier} 会员",
            "tier_info": tier_info,
            "nca_balance": new_nca_balance,
            "reference_id": reference_id,
        })

    # 云端记录失败 → 如果已扣费则退款
    if cost > 0 and ranking_transaction_id:
        logger.warning(
            f"会员购买云端记录失败，尝试退款: account={str(account)[:20]}, "
            f"tier={tier}, cost={cost}"
        )
        refund_body = {
            "token": token,
            "amount": cost,
            "reason": "会员购买失败自动退款",
            "reference_id": reference_id,
        }
        refund_url = f"http://127.0.0.1:{_get_comfyui_port()}/ranking/local/refund"
        refund_headers = {
            "Content-Type": "application/json",
            "X-Plugin-Key": NCA_PLUGIN_KEY,
        }
        refund_success = False
        try:
            async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=_REQUEST_TIMEOUT)) as session:
                async with session.post(
                    refund_url, headers=refund_headers, json=refund_body
                ) as refund_resp:
                    if refund_resp.status == 200:
                        refund_data = await refund_resp.json()
                        if isinstance(refund_data, dict) and refund_data.get("success") is not False:
                            refund_success = True
                            logger.info(f"会员购买退款成功: account={str(account)[:20]}, cost={cost}")
        except Exception as e:
            logger.error(f"会员购买退款异常: {e}")

        if refund_success:
            return web.json_response({
                "success": False,
                "error": "会员开通失败，积分已自动退回",
                "refunded": True,
                "reference_id": reference_id,
            }, status=200)
        else:
            logger.error(
                f"严重：会员购买云端失败且退款失败！account={str(account)[:20]}, "
                f"tier={tier}, cost={cost}, reference_id={reference_id[:50]}"
            )
            return web.json_response({
                "success": False,
                "error": "会员开通异常，请联系客服处理",
                "refunded": False,
                "reference_id": reference_id,
            }, status=200)
    elif cost > 0:
        # 扣费成功但未获取 transaction_id，且云端失败
        logger.error(
            f"会员购买云端失败且无法退款(transaction_id缺失): "
            f"account={str(account)[:20]}, tier={tier}, cost={cost}"
        )
        return web.json_response({
            "success": False,
            "error": "会员开通异常，请联系客服处理",
            "refunded": False,
            "reference_id": reference_id,
        }, status=200)
    else:
        # 免费会员(basic)云端记录失败
        return web.json_response({
            "success": False,
            "error": "会员开通失败，请稍后重试",
            "reference_id": reference_id,
        }, status=200)


# ─── 路由注册 ───────────────────────────────────────────

def register_计费代理路由(routes) -> None:
    """注册计费代理端点到 PromptServer 路由表"""
    routes.get("/nca/billing/balance")(查询余额)
    routes.get("/nca/billing/model-pricing/{model_id}")(查询模型定价)
    routes.post("/nca/billing/deduct")(扣款)
    routes.post("/nca/billing/verify-login")(验证登录)
    routes.post("/nca/billing/refund")(退款)
    routes.post("/nca/billing/recharge")(充值)
    routes.post("/nca/billing/membership-purchase")(购买会员)
    routes.get("/nca/billing/usage")(查询用量)
    routes.get("/nca/billing/token-balance")(查询token余额)

