"""知识库同步客户端 - 云端→本地单向同步

提供：
- 登录后自动静默同步
- 对比本地与云端文件差异（SHA256 哈希）
- 云端为权威源：始终以云端版本为准
- 本地独有文件保留不动，不上传
"""
from __future__ import annotations

import os
import json
import asyncio
import hashlib
import base64
from pathlib import Path
from datetime import datetime
from typing import Dict, Any, Optional, Tuple

import aiohttp

from .系统环境映射 import get_plugin_root
from .日志配置 import 获取日志器

logger = 获取日志器("知识库同步")

# ─── 配置 ────────────────────────────────────────────────────

RANKING_CLOUD_URL = os.environ.get(
    "RANKING_CLOUD_URL",
    "https://zhiwei666-comfyui-ranking-api.hf.space",
)

# 本地知识库根目录
_本地知识库目录 = get_plugin_root() / "知识库"
_本地知识库目录.mkdir(parents=True, exist_ok=True)

# 请求超时（秒）
_请求超时 = aiohttp.ClientTimeout(total=30)


# ─── 重试包装器（仅用于下载/只读操作） ──────────────────────

async def _带重试请求(
    session: aiohttp.ClientSession,
    method: str,
    url: str,
    *,
    最大重试: int = 3,
    **kwargs,
) -> Tuple[int, bytes]:
    """指数退避重试包装器

    - 4xx 不重试，直接返回（业务错误由调用方处理）
    - 5xx 服务端错误：重试，退避 1s → 2s → 4s
    - 网络异常 / 超时：重试
    返回 (status, body_bytes)。
    """
    最后异常: Optional[BaseException] = None
    for 尝试次数 in range(最大重试):
        try:
            async with session.request(method, url, **kwargs) as resp:
                if resp.status < 500:  # 4xx 不重试，5xx 重试
                    return resp.status, await resp.read()
                最后异常 = RuntimeError(f"服务端错误: {resp.status}")
        except (aiohttp.ClientError, asyncio.TimeoutError) as e:
            最后异常 = e

        if 尝试次数 < 最大重试 - 1:
            等待秒 = 2 ** 尝试次数  # 1s, 2s, 4s
            logger.warning(f"请求失败({最后异常})，{等待秒}s 后重试 {url}")
            await asyncio.sleep(等待秒)

    assert 最后异常 is not None
    raise 最后异常


# ─── 内部工具 ────────────────────────────────────────────────

def _计算文件哈希(文件路径: Path) -> str:
    """计算文件的 SHA256 哈希"""
    h = hashlib.sha256()
    with open(文件路径, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()


def _获取本地文件列表() -> Dict[str, Dict[str, Any]]:
    """扫描本地知识库目录，返回 {相对路径: {hash, modified_at, size}}"""
    结果 = {}
    if not _本地知识库目录.exists():
        return 结果
    for f in _本地知识库目录.rglob("*"):
        if not f.is_file():
            continue
        try:
            stat = f.stat()
            相对路径 = str(f.relative_to(_本地知识库目录)).replace("\\", "/")
            结果[相对路径] = {
                "hash": _计算文件哈希(f),
                "modified_at": datetime.fromtimestamp(stat.st_mtime).isoformat(),
                "size": stat.st_size,
            }
        except Exception as e:
            logger.warning(f"扫描本地文件失败 {f}: {e}")
    return 结果


async def _获取云端文件列表(token: str) -> Dict[str, Dict[str, Any]]:
    """调用云端 API 获取文件列表（只读操作，启用重试）"""
    headers = {"Authorization": f"Bearer {token}"}
    async with aiohttp.ClientSession(timeout=_请求超时) as session:
        status, body = await _带重试请求(
            session,
            "GET",
            f"{RANKING_CLOUD_URL}/api/open/knowledge/list",
            headers=headers,
        )
        if status != 200:
            raise RuntimeError(
                f"获取云端列表失败: {status} {body[:200]!r}"
            )
        data = json.loads(body.decode("utf-8"))
        return {
            item["path"]: item
            for item in data.get("files", [])
        }


async def _下载云端文件(token: str, path: str) -> bytes:
    """从云端下载单个文件（只读操作，启用重试）"""
    headers = {"Authorization": f"Bearer {token}"}
    async with aiohttp.ClientSession(timeout=_请求超时) as session:
        status, body = await _带重试请求(
            session,
            "GET",
            f"{RANKING_CLOUD_URL}/api/open/knowledge/file",
            headers=headers,
            params={"path": path},
        )
        if status != 200:
            raise RuntimeError(
                f"下载云端文件失败 {path}: {status} {body[:200]!r}"
            )
        return body


async def _上传本地文件(token: str, path: str, content: bytes, modified_at: str) -> None:
    """上传单个文件到云端"""
    headers = {"Authorization": f"Bearer {token}"}
    payload = {
        "path": path,
        "content": base64.b64encode(content).decode("ascii"),
        "modified_at": modified_at,
    }
    async with aiohttp.ClientSession(timeout=_请求超时) as session:
        async with session.post(
            f"{RANKING_CLOUD_URL}/api/open/knowledge/upload",
            headers=headers,
            json=payload,
        ) as resp:
            if resp.status != 200:
                text = await resp.text()
                raise RuntimeError(f"上传文件失败 {path}: {resp.status} {text}")


def _解析时间(时间字符串: str) -> Optional[datetime]:
    """解析 ISO 格式时间字符串"""
    if not 时间字符串:
        return None
    try:
        return datetime.fromisoformat(时间字符串.replace("Z", "+00:00"))
    except Exception:
        return None


# ─── 核心同步函数 ───────────────────────────────────────────

async def 执行知识库同步(token: str) -> Dict[str, Any]:
    """执行云端→本地知识库的单向同步

    Args:
        token: RanKing 用户的 Bearer Token

    Returns:
        {"downloaded": int, "errors": int, "skipped": int}
    """
    logger.info("开始知识库单向同步(云端→本地)...")

    # 1. 获取两端文件列表
    云端文件 = await _获取云端文件列表(token)
    本地文件 = _获取本地文件列表()

    下载数 = 0
    错误数 = 0
    跳过数 = 0

    所有路径 = set(云端文件.keys()) | set(本地文件.keys())

    for 路径 in 所有路径:
        云端有 = 路径 in 云端文件
        本地有 = 路径 in 本地文件

        try:
            if 云端有 and not 本地有:
                # 云端有、本地无 → 下载
                logger.info(f"下载云端文件: {路径}")
                内容 = await _下载云端文件(token, 路径)
                本地路径 = _本地知识库目录 / 路径.replace("/", os.sep)
                本地路径.parent.mkdir(parents=True, exist_ok=True)
                with open(本地路径, "wb") as f:
                    f.write(内容)
                下载数 += 1

            elif 本地有 and not 云端有:
                # 本地独有文件，保留不动（不上传到云端）
                跳过数 += 1

            else:
                # 两端都有
                云端哈希 = 云端文件[路径].get("hash", "")
                本地哈希 = 本地文件[路径].get("hash", "")
                if 云端哈希 == 本地哈希:
                    跳过数 += 1
                    continue

                # 云端为权威源，直接下载覆盖本地
                logger.info(f"更新本地文件(云端较新): {路径}")
                内容 = await _下载云端文件(token, 路径)
                本地路径 = _本地知识库目录 / 路径.replace("/", os.sep)
                with open(本地路径, "wb") as f:
                    f.write(内容)
                下载数 += 1

        except Exception as e:
            logger.warning(f"同步文件失败 {路径}: {e}")
            错误数 += 1

    结果 = {
        "downloaded": 下载数,
        "errors": 错误数,
        "skipped": 跳过数,
    }
    logger.info(f"知识库同步完成: {结果}")
    return 结果


__all__ = ["执行知识库同步"]
