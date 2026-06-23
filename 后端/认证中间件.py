"""
JWT 认证中间件 - 适配 RanKing Token

NCA 本地后端不再自行管理用户账户与签发 Token，
而是验证由 RanKing 平台签发的 JWT（基于共享 HMAC-SHA256 密钥）。

Token 格式（与 RanKing 严格一致）：
- Header : {"alg": "HS256", "typ": "JWT"}
- Payload: {"sub": <account>, "iat": ..., "exp": ...,
            "remember": bool, "pwd_ver": <timestamp>}

对外公开接口（保持向后兼容）：
- ``AuthManager.verify_token(token)``  -> payload 或 None
- ``AuthManager.has_users()``          -> True（接入 RanKing 后始终需要认证）
- ``AuthManager.get_user_dir(account)``-> ``pathlib.Path``
- ``require_auth(handler)``            -> aiohttp 装饰器
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import os
import threading
import time
from pathlib import Path
from typing import Optional

_日志 = logging.getLogger(__name__)


# ─── RanKing 共享 JWT 密钥 ───────────────────────────────────────────
# 必须与 RanKing 平台严格一致，切勿随意修改。
# 生产环境必须通过环境变量设置，开发模式使用默认值
_默认JWT密钥 = "ComfyUI-Ranking-JWT-Secret-v2-beta-2026"
JWT_SECRET = os.environ.get("RANKING_JWT_SECRET", _默认JWT密钥)


def _是否生产环境() -> bool:
    """通过 ``COMFYUI_ENV`` / ``RANKING_ENV`` 环境变量判定是否为生产环境。

    任一变量取值（忽略大小写与首尾空格）等于 ``production`` 或 ``prod`` 时，
    视为生产环境。
    """
    for 变量名 in ("COMFYUI_ENV", "RANKING_ENV"):
        取值 = (os.environ.get(变量名) or "").strip().lower()
        if 取值 in ("production", "prod"):
            return True
    return False


# 云端环境判定已统一抽取至 ``系统环境映射.是否云端环境``，
# 此处保留私有别名以兼容历史内部调用。
try:
    from .系统环境映射 import 是否云端环境 as _是否云端环境  # type: ignore[no-redef]
except ImportError:  # pragma: no cover - 仅作为极端兜底
    def _是否云端环境() -> bool:
        for 变量名 in ("China_MAINLAND", "MODELSCOPE_SPACE"):
            if (os.environ.get(变量名) or "").strip():
                return True
        return False


def _校验JWT密钥安全() -> None:
    """模块加载时执行：检测 JWT_SECRET 是否仍为硬编码默认值。

    - 生产环境使用默认密钥：记录 CRITICAL 日志并打印设置指南；
    - 云端（创空间）使用默认密钥：记录 WARNING 日志（云端存在公网暴露风险）；
    - 本地环境使用默认密钥：仅记录 DEBUG 日志（用户独占使用，无外部访问风险）；
    - 已通过环境变量自定义：仅记录 INFO，提示当前已启用自定义密钥。

    本函数不会抛出异常，避免阻断本地开发启动。
    """
    使用默认 = (JWT_SECRET == _默认JWT密钥)
    生产 = _是否生产环境()
    云端 = _是否云端环境()

    if not 使用默认:
        _日志.info("[安全检查] RANKING_JWT_SECRET 已通过环境变量自定义，配置正常")
        return

    设置指南 = (
        "请按以下步骤设置自定义 JWT 密钥：\n"
        "  1. 生成强随机串：python -c \"import secrets; print(secrets.token_urlsafe(48))\"\n"
        "  2. 配置环境变量 RANKING_JWT_SECRET=<上一步输出>\n"
        "  3. 务必与 RanKing 平台保持完全一致，否则 Token 验证失败\n"
        "  4. 重启 ComfyUI 服务以使新密钥生效"
    )

    if 生产:
        _日志.critical(
            "[安全检查][严重] 生产环境检测到 RANKING_JWT_SECRET 仍使用硬编码默认值，"
            "任何持有源码的人都可伪造 Token 越权访问，存在严重安全风险！"
        )
        _日志.critical("[安全检查] %s", 设置指南)
    elif 云端:
        _日志.warning(
            "[安全检查] 当前 RANKING_JWT_SECRET 为硬编码默认值（开发模式允许）。"
            "上线前必须通过环境变量覆盖，否则 Token 可被任意伪造。"
        )
        _日志.warning("[安全检查] %s", 设置指南)
    else:
        # 本地 ComfyUI 插件场景：用户独占使用，无 Token 伪造风险，降级为 DEBUG 静默提示。
        _日志.debug(
            "[安全检查] 当前使用默认 RANKING_JWT_SECRET（本地开发模式，无安全风险）"
        )


# ─── 线程安全初始化（双检锁，防止多线程导入竞态） ──────────────
_init_lock = threading.Lock()
_init_done = False


def _安全初始化() -> None:
    """线程安全的 JWT 密钥初始化与校验（双检锁模式）。

    多个工作线程在 import 期间并发触发模块初始化时，确保
    ``_校验JWT密钥安全`` 仅执行一次，避免日志重复或竞态副作用。
    """
    global _init_done
    if _init_done:
        return
    with _init_lock:
        if not _init_done:
            _校验JWT密钥安全()
            _init_done = True


# 模块加载时立即执行安全校验（线程安全）
_安全初始化()


def _verify_ranking_token(token: str) -> Optional[dict]:
    """RanKing 兼容的纯 Python JWT 验证。

    验证步骤：
        1. 按 ``header.payload.signature`` 三段拆分 token
        2. 使用 ``HMAC-SHA256 + JWT_SECRET`` 重算签名并以 base64url 比对
        3. 补齐 base64url padding 后解码 payload
        4. 校验 ``exp`` 字段未过期，且 ``sub`` 字段非空

    任意环节失败统一返回 ``None``，调用方据此判定认证失败。
    """
    if not token or not isinstance(token, str):
        return None

    parts = token.split(".")
    if len(parts) != 3:
        return None

    header_b64, payload_b64, signature_b64 = parts

    # 1) 重算签名（HMAC-SHA256），与 RanKing 实现一致
    message = f"{header_b64}.{payload_b64}".encode("utf-8")
    expected = hmac.new(JWT_SECRET.encode("utf-8"), message, hashlib.sha256).digest()
    expected_b64 = base64.urlsafe_b64encode(expected).rstrip(b"=").decode("ascii")
    if not hmac.compare_digest(signature_b64, expected_b64):
        return None

    # 2) 解码 payload（补齐 base64url padding）
    try:
        padding = "=" * (-len(payload_b64) % 4)
        payload_bytes = base64.urlsafe_b64decode(payload_b64 + padding)
        payload = json.loads(payload_bytes)
    except (ValueError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict):
        return None

    # 3) 校验过期
    exp = payload.get("exp")
    if not isinstance(exp, (int, float)) or time.time() > float(exp):
        return None

    # 4) 必须携带 sub（用户 account）
    if not payload.get("sub"):
        return None

    return payload


class AuthManager:
    """认证管理器（RanKing Token 验证版）。

    NCA 本地后端不再持有任何用户账户数据；
    本类仅负责：
    - 验证 RanKing 签发的 access token；
    - 维护以 account 为粒度的本地用户数据目录（会话、设置等）。
    """

    def __init__(self, data_dir: Path):
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)

    # ─── 对外核心接口 ─────────────────────────────────────
    def verify_token(self, token: str) -> Optional[dict]:
        """验证 RanKing JWT Token，返回 payload 或 None。

        为兼容历史调用方（``require_auth`` / GraphQL Context 等）对
        ``type == "access"`` 的检查，验证通过后为 payload 合成
        ``type="access"`` 默认值；不影响 RanKing 原始字段。
        """
        payload = _verify_ranking_token(token)
        if payload is None:
            return None
        payload.setdefault("type", "access")
        return payload

    def has_users(self) -> bool:
        """是否启用认证。

        接入 RanKing 后，本地后端始终要求请求携带有效 RanKing Token，
        故恒定返回 True。
        """
        return True

    def get_user_dir(self, account: str) -> Path:
        """获取以 account 为隔离粒度的用户数据目录。"""
        user_dir = self.data_dir / account
        user_dir.mkdir(parents=True, exist_ok=True)
        return user_dir


def require_auth(handler):
    """认证装饰器 - 校验请求中的 RanKing Bearer Token。

    成功时将解析后的 payload 写入 ``request['user']``；其中 ``sub`` 字段为
    用户 account 字符串，可直接用于后续的业务数据隔离。
    """
    async def wrapped(request):
        from aiohttp import web

        auth_manager: AuthManager = request.app['auth_manager']

        auth_header = request.headers.get('Authorization', '')
        if not auth_header.startswith('Bearer '):
            return web.json_response(
                {"success": False, "message": "未登录"},
                status=401,
            )

        token = auth_header[7:]
        payload = auth_manager.verify_token(token)
        if payload is None:
            return web.json_response(
                {"success": False, "message": "Token 无效或已过期"},
                status=401,
            )

        request['user'] = payload
        return await handler(request)

    # 保留原函数元信息
    wrapped.__name__ = getattr(handler, '__name__', 'wrapped')
    wrapped.__doc__ = getattr(handler, '__doc__', None)
    return wrapped
