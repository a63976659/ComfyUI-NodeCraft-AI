"""Token 精确计费模块

根据 DeepSeek API 返回的 usage 信息，结合云端定价配置，
计算并执行积分扣费。

NCA 本地余额管理：
- NCA 维护独立的 token 余额（与 RanKing 钱包余额隔离）
- 充值时：NCA 余额 += 充值金额 × 200000
- API 调用时：NCA 余额 -= 消耗的 token 数
- 查询余额时：返回 NCA 自己的余额
"""
import asyncio
import json as _json
import os
import threading
import time
import uuid
from typing import Optional, Union

import aiohttp
from aiohttp import web

from .日志配置 import 获取日志器
from .计费代理 import NCA_PLUGIN_KEY, RANKING_CLOUD_URL

logger = 获取日志器("token计费")


# ─── NCA 本地余额管理 ─────────────────────────────────────────

# 余额存储文件路径
_DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "数据", "用户")
_BALANCE_FILE = os.path.join(_DATA_DIR, "nca_balance.json")

# Token → Account 缓存（登录验证成功时填充）
_token_account_cache: dict = {}

# 文件操作锁（线程安全）
_balance_lock = threading.Lock()

# token 与积分的换算比例（1元 = 200000 token）
TOKENS_PER_CREDIT = 200000


def 注册token账户映射(token: str, account: str):
    """登录验证成功后注册 token→account 映射"""
    if token and account:
        _token_account_cache[token] = account


def 获取token对应账户(token: str) -> str:
    """从缓存获取 token 对应的 account"""
    return _token_account_cache.get(token, "")


def _确保余额目录存在():
    """确保余额文件所在目录存在"""
    os.makedirs(_DATA_DIR, exist_ok=True)


def _读取余额文件() -> dict:
    """读取整个余额文件，返回 {account: {balance: int, last_updated: float}}"""
    try:
        if os.path.exists(_BALANCE_FILE):
            with open(_BALANCE_FILE, "r", encoding="utf-8") as f:
                data = _json.load(f)
                if isinstance(data, dict):
                    return data
    except (OSError, _json.JSONDecodeError) as e:
        logger.warning(f"读取余额文件失败: {e}")
    return {}


def _写入余额文件(data: dict):
    """写入整个余额文件"""
    _确保余额目录存在()
    try:
        with open(_BALANCE_FILE, "w", encoding="utf-8") as f:
            _json.dump(data, f, ensure_ascii=False, indent=2)
    except OSError as e:
        logger.error(f"写入余额文件失败: {e}")


def 读取NCA余额(account: str) -> int:
    """读取指定账户的 NCA token 余额"""
    if not account:
        return 0
    with _balance_lock:
        data = _读取余额文件()
        entry = data.get(account, {})
        return int(entry.get("balance", 0))


def 保存NCA余额(account: str, balance: int):
    """保存指定账户的 NCA token 余额"""
    if not account:
        return
    with _balance_lock:
        data = _读取余额文件()
        data[account] = {
            "balance": max(0, int(balance)),
            "last_updated": time.time(),
        }
        _写入余额文件(data)


def NCA余额增加(account: str, tokens: int) -> int:
    """增加 NCA 余额，返回新余额"""
    if not account or tokens <= 0:
        return 读取NCA余额(account)
    with _balance_lock:
        data = _读取余额文件()
        entry = data.get(account, {})
        current = int(entry.get("balance", 0))
        new_balance = current + int(tokens)
        data[account] = {
            "balance": new_balance,
            "last_updated": time.time(),
        }
        _写入余额文件(data)
    logger.info(f"NCA余额增加: account={str(account)[:20]}, +{tokens}, 新余额={new_balance}")
    return new_balance


def NCA余额扣减(account: str, tokens: int) -> int:
    """扣减 NCA 余额，返回新余额（不会低于0）"""
    if not account or tokens <= 0:
        return 读取NCA余额(account)
    with _balance_lock:
        data = _读取余额文件()
        entry = data.get(account, {})
        current = int(entry.get("balance", 0))
        new_balance = max(0, current - int(tokens))
        data[account] = {
            "balance": new_balance,
            "last_updated": time.time(),
        }
        _写入余额文件(data)
    logger.info(f"NCA余额扣减: account={str(account)[:20]}, -{tokens}, 新余额={new_balance}")
    return new_balance


# ─── 配置常量 ─────────────────────────────────────────────

# 内置降级定价（云端不可达时使用）
DEFAULT_PRICING = {
    "deepseek-v4-pro": {
        "input_price_per_million": 3.0,
        "input_cached_price_per_million": 0.025,
        "output_price_per_million": 6.0,
        "is_free": False,
    },
    "glm-5.2": {
        "input_price_per_million": 8.0,
        "input_cached_price_per_million": 2.0,
        "output_price_per_million": 28.0,
        "is_free": False,
    },
    "deepseek-chat": {
        "input_price_per_million": 1.0,
        "input_cached_price_per_million": 0.02,
        "output_price_per_million": 2.0,
        "is_free": False,
    },
}

# 模型名前缀 → 降级定价键（未精确匹配时按前缀查找）
# 降级策略：优先使用同系列中定价最高的模型，避免少收费导致收益亏损
_PREFIX_FALLBACK = {
    "glm": "glm-5.2",
    "deepseek": "deepseek-v4-pro",
}

# 请求超时时间（秒）
_REQUEST_TIMEOUT = aiohttp.ClientTimeout(total=10)

# RanKing 云端定价查询路径
_REMOTE_PATH_PRICING = "/api/open/pricing/{model_id}"


# ─── 通用工具 ─────────────────────────────────────────────

def _get_internal_url(request: web.Request, path: str) -> str:
    """根据当前请求构造内部端点 URL。

    优先使用 Host 请求头，兼容反向代理；兜底使用请求原始 URL。
    """
    host = request.headers.get("Host")
    if host:
        scheme = request.headers.get("X-Forwarded-Proto", request.scheme)
        return f"{scheme}://{host}{path}"
    return str(request.url.with_path(path).with_query({}))


def _get_internal_headers(request: web.Request, idempotency_key: Optional[str] = None) -> dict:
    """构造调用本地计费端点的请求头。

    - 透传 Authorization：用户登录态
    - 附带 X-CSRF-Token：通过 CSRF 中间件校验（UUID 格式即可）
    - 可选 X-Idempotency-Key：保证扣费幂等
    """
    headers = {
        "Content-Type": "application/json",
        "X-CSRF-Token": str(uuid.uuid4()),
    }
    auth = request.headers.get("Authorization")
    if auth:
        headers["Authorization"] = auth
    if idempotency_key:
        headers["X-Idempotency-Key"] = idempotency_key
    return headers


def _get_cloud_headers(token: Optional[str] = None) -> dict:
    """构造直接调用 RanKing 云端的请求头。"""
    headers = {
        "X-Plugin-Key": NCA_PLUGIN_KEY,
        "Content-Type": "application/json",
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def _extract_balance(data) -> float:
    """从余额响应中安全提取 balance 数值。"""
    if not isinstance(data, dict):
        return 0.0
    if "balance" in data:
        try:
            return float(data["balance"])
        except (TypeError, ValueError):
            pass
    nested = data.get("data")
    if isinstance(nested, dict) and "balance" in nested:
        try:
            return float(nested["balance"])
        except (TypeError, ValueError):
            pass
    return 0.0


# ─── 核心函数 ─────────────────────────────────────────────

async def 获取模型定价(model_id: str) -> dict:
    """优先从云端获取定价，失败降级到本地默认值。"""
    remote_path = _REMOTE_PATH_PRICING.format(model_id=model_id)
    target_url = f"{RANKING_CLOUD_URL.rstrip('/')}{remote_path}"
    headers = _get_cloud_headers()

    try:
        async with aiohttp.ClientSession(timeout=_REQUEST_TIMEOUT) as session:
            async with session.get(target_url, headers=headers) as upstream:
                if 200 <= upstream.status < 300:
                    data = await upstream.json()
                    # 云端单模型端点直接返回定价dict（含 id/input_price_per_million/...），
                    # 列表端点返回 {"pricing": [...]}。此处调用单模型端点，data 即定价dict。
                    pricing = data.get("pricing") if isinstance(data, dict) and "pricing" in data else data
                    if pricing and isinstance(pricing, dict) and all(
                        k in pricing
                        for k in (
                            "input_price_per_million",
                            "input_cached_price_per_million",
                            "output_price_per_million",
                        )
                    ):
                        return pricing
    except asyncio.TimeoutError:
        logger.warning(f"获取模型定价超时: {target_url}")
    except aiohttp.ClientError as e:
        logger.warning(f"获取模型定价失败(云端不可达): {target_url} - {e}")
    except Exception as e:
        logger.warning(f"获取模型定价失败: {target_url} - {e}")

    fallback = DEFAULT_PRICING.get(model_id)
    # 精确匹配失败时，按模型名前缀查找降级定价
    if not fallback:
        model_lower = model_id.lower()
        for prefix, fallback_key in _PREFIX_FALLBACK.items():
            if model_lower.startswith(prefix):
                fallback = DEFAULT_PRICING.get(fallback_key)
                break
    if fallback:
        return fallback
    # 无明确降级配置时，使用 deepseek-chat 作为通用兜底
    return DEFAULT_PRICING["deepseek-chat"]


async def 是否免费模型(model_id: str) -> bool:
    """判断指定模型是否为免费体验模型。

    免费模型由管理员在 API 配置界面动态管理，is_free 随定价同步到云端。
    云端不可达时降级到本地 DEFAULT_PRICING（内置模型均为 is_free=False）。
    """
    pricing = await 获取模型定价(model_id)
    return bool(pricing.get("is_free", False))


async def 计算本次消耗积分(usage: dict, model_id: str) -> float:
    """根据API返回的usage字段计算积分消耗。

    usage 字段格式（DeepSeek返回）：
    {
        "prompt_tokens": 1200,
        "completion_tokens": 500,
        "prompt_cache_hit_tokens": 800,
        "total_tokens": 1700
    }
    """
    pricing = await 获取模型定价(model_id)

    prompt_tokens = int(usage.get("prompt_tokens", 0) or 0)
    completion_tokens = int(usage.get("completion_tokens", 0) or 0)
    cache_hit_tokens = int(usage.get("prompt_cache_hit_tokens", 0) or 0)
    cache_miss_tokens = max(0, prompt_tokens - cache_hit_tokens)

    input_cost = (cache_miss_tokens / 1_000_000) * pricing["input_price_per_million"]
    cache_cost = (cache_hit_tokens / 1_000_000) * pricing["input_cached_price_per_million"]
    output_cost = (completion_tokens / 1_000_000) * pricing["output_price_per_million"]

    total = input_cost + cache_cost + output_cost
    return round(total, 6)


async def 检查余额(request_or_token: Union[web.Request, str, None]) -> dict:
    """查询用户 NCA 本地 token 余额，返回 {balance: float, sufficient: bool}。"""
    token = None
    if isinstance(request_or_token, web.Request):
        auth = request_or_token.headers.get("Authorization", "")
        token = auth.replace("Bearer ", "").replace("bearer ", "").strip()
    elif request_or_token:
        token = str(request_or_token)

    if token:
        account = 获取token对应账户(token)
        if account:
            balance = 读取NCA余额(account)
            return {"balance": float(balance), "sufficient": balance > 0}

    # 无法获取余额时默认允许继续，避免阻断正常流程
    return {"balance": 0.0, "sufficient": True}


async def 执行扣费(
    request: web.Request,
    amount: float,
    session_id: str,
    msg_id: str,
    model_id: str,
    usage: dict,
) -> dict:
    """从 NCA 本地余额扣减 token。

    amount: 消耗的积分（元），将转换为 token 扣减
    返回 {success: bool, balance: float, error?: str}
    """
    # 获取账户
    auth = request.headers.get("Authorization", "")
    token = auth.replace("Bearer ", "").replace("bearer ", "").strip()
    account = 获取token对应账户(token)

    if not account:
        logger.warning(f"执行扣费: 无法获取账户, session={session_id}, msg={msg_id}")
        return {"success": False, "balance": 0.0, "error": "无法识别用户账户"}

    # 将积分(元)转换为 token 数
    tokens_to_deduct = int(amount * TOKENS_PER_CREDIT)
    if tokens_to_deduct <= 0:
        balance = 读取NCA余额(account)
        return {"success": True, "balance": float(balance)}

    # 检查余额是否足够
    current_balance = 读取NCA余额(account)
    if current_balance < tokens_to_deduct:
        return {
            "success": False,
            "balance": float(current_balance),
            "error": "NCA余额不足",
        }

    # 扣减余额
    new_balance = NCA余额扣减(account, tokens_to_deduct)

    prompt_tokens = int(usage.get("prompt_tokens", 0) or 0)
    completion_tokens = int(usage.get("completion_tokens", 0) or 0)
    cache_hit_tokens = int(usage.get("prompt_cache_hit_tokens", 0) or 0)
    logger.info(
        f"[NCA扣费] account={str(account)[:20]}, model={model_id}, "
        f"input:{prompt_tokens} output:{completion_tokens} cached:{cache_hit_tokens} "
        f"消耗:{amount}积分({tokens_to_deduct} tokens), 余额:{new_balance}"
    )
    return {"success": True, "balance": float(new_balance)}


def 本地扣减NCA余额(token: str, cost_credits: float) -> dict:
    """根据 token 和消耗积分(元)扣减 NCA 余额。

    供聊天路由在云端代理返回计费信息后调用。
    返回 {success: bool, balance: int, deducted: int}
    """
    account = 获取token对应账户(token)
    if not account:
        return {"success": False, "balance": 0, "deducted": 0}

    tokens_to_deduct = int(cost_credits * TOKENS_PER_CREDIT)
    if tokens_to_deduct <= 0:
        balance = 读取NCA余额(account)
        return {"success": True, "balance": balance, "deducted": 0}

    new_balance = NCA余额扣减(account, tokens_to_deduct)
    return {"success": True, "balance": new_balance, "deducted": tokens_to_deduct}
