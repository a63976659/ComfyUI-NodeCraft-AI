"""GraphQL GitHub 集成 Resolver

实现 GitHub 集成相关的业务逻辑函数：
- 同步插件目录到 GitHub 仓库
- 检查仓库是否存在
- 测试 Token 有效性并返回对应 GitHub 用户名

凭证（github_token / github_username / github_visibility）从用户设置中
读取，复用 ``后端.GitHub同步`` 提供的底层 API 调用实现。
"""

from __future__ import annotations

from pathlib import Path

import aiohttp
from strawberry.types import Info

from ..中间件 import 检查认证
from ..异常定义 import 验证错误, 业务错误
from ..类型定义 import GitHub同步结果

from ...文件读写操作 import load_settings
from ...系统环境映射 import get_custom_nodes_path
from ...GitHub同步 import (
    sync_to_github,
    check_repo_exists,
    test_github_connection,
    validate_repo_name,
    _GITHUB_API_BASE,
    _build_headers,
)


def _解析插件路径(插件路径: str) -> Path:
    """将相对/绝对路径解析为绝对插件目录路径。"""
    路径 = Path(插件路径)
    if not 路径.is_absolute():
        路径 = get_custom_nodes_path() / 插件路径
    return 路径


# ─── Mutation Resolver ───────────────────────────────────

async def 同步到GitHub(
    info: Info,
    插件路径: str,
    仓库名称: str,
    忽略gitignore: bool = False,
) -> GitHub同步结果:
    """将本地插件目录同步到 GitHub 仓库（需要认证）"""
    await 检查认证(info)

    if not (插件路径 or "").strip():
        raise 验证错误("插件路径不能为空")

    # 仓库名称基础校验
    name_check = validate_repo_name(仓库名称)
    if not name_check["valid"]:
        raise 验证错误(name_check["message"])

    # 解析插件目录
    解析路径 = _解析插件路径(插件路径)
    if not 解析路径.exists():
        raise 验证错误(f"插件路径不存在: {插件路径}")

    # 从设置读取 GitHub 凭证与可见性
    settings = load_settings()
    github_token = settings.get("github_token", "") or ""
    github_username = settings.get("github_username", "") or ""
    visibility = settings.get("github_visibility", "public") or "public"

    if not github_token:
        raise 业务错误("未配置 GitHub Token，请在设置中填写")
    if not github_username:
        raise 业务错误("未配置 GitHub 用户名，请在设置中填写")

    # 验证 Token 有效性
    try:
        token_check = await test_github_connection(github_token, github_username)
    except Exception as e:
        raise 业务错误(f"GitHub 连接异常: {e}")
    if not token_check.get("valid"):
        raise 业务错误(token_check.get("message", "GitHub Token 无效"))

    # 执行同步
    try:
        结果 = await sync_to_github(
            plugin_dir=str(解析路径),
            repo_name=仓库名称,
            ignore_gitignore=bool(忽略gitignore),
            visibility=visibility,
        )
    except Exception as e:
        raise 业务错误(f"同步过程发生异常: {e}")

    成功 = bool(结果.get("success"))
    return GitHub同步结果(
        状态="success" if 成功 else "error",
        消息=str(结果.get("message") or 结果.get("error") or ""),
        仓库URL=str(结果.get("repo_url", "") or ""),
        上传文件数=int(结果.get("uploaded_count", 0) or 0),
    )


# ─── Query Resolver ──────────────────────────────────────

async def 检查仓库是否存在(info: Info, 仓库名称: str) -> bool:
    """检查 GitHub 仓库是否存在（需要认证）"""
    await 检查认证(info)

    if not (仓库名称 or "").strip():
        raise 验证错误("仓库名称不能为空")

    settings = load_settings()
    token = settings.get("github_token", "") or ""
    username = settings.get("github_username", "") or ""

    if not token or not username:
        raise 业务错误("GitHub 未配置（缺少 Token 或用户名）")

    try:
        return bool(await check_repo_exists(token, username, 仓库名称))
    except Exception as e:
        raise 业务错误(f"检查仓库异常: {e}")


async def 测试GitHub连接(info: Info, 令牌: str) -> str:
    """测试 GitHub Token 有效性，返回对应的 GitHub 用户名（需要认证）

    与 REST 端点行为略有差异：本接口直接以传入的 ``令牌`` 调用
    ``GET /user`` 获取登录名，便于前端在保存设置前提前验证。
    """
    await 检查认证(info)

    令牌清理 = (令牌 or "").strip()
    if not 令牌清理:
        raise 验证错误("Token 不能为空")
    if not (令牌清理.startswith("ghp_") or 令牌清理.startswith("github_pat_")):
        raise 验证错误("Token 格式无效，应以 ghp_ 或 github_pat_ 开头")

    headers = _build_headers(令牌清理)
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(
                f"{_GITHUB_API_BASE}/user",
                headers=headers,
                timeout=aiohttp.ClientTimeout(total=15),
            ) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    login = str(data.get("login", "") or "")
                    if not login:
                        raise 业务错误("GitHub 返回数据缺少 login 字段")
                    return login
                if resp.status == 401:
                    raise 业务错误("Token 无效或已过期")
                raise 业务错误(f"验证失败，HTTP 状态码: {resp.status}")
    except aiohttp.ClientError as e:
        raise 业务错误(f"网络连接失败: {e}")
