"""GraphQL 设置与模型 Resolver

实现以下业务逻辑函数：
- 获取设置 / 更新设置（读写持久化设置；敏感字段在返回时不暴露明文）
- 获取本地模型列表（扫描 ComfyUI/models/LLM 目录并分页）
- 获取模型能力（参考 model-capabilities REST 端点逻辑）
- 卸载模型（释放本地模型显存）
- 重置模型状态（清除失败黑名单）

所有 resolver 入口对“需要写或访问敏感能力”的操作执行 ``await 检查认证(info)``，
读取类操作（如获取设置）也执行认证检查以保持与 REST 端点一致。
"""

from __future__ import annotations

from typing import Optional

from strawberry.types import Info

from ..中间件 import 检查认证
from ..异常定义 import 业务错误
from ..类型定义 import (
    设置,
    设置输入,
    本地模型,
    本地模型列表响应,
    模型能力,
    分页信息,
)

# 复用 REST 路由共享的同步 I/O 与单例
from ...文件读写操作 import load_settings, save_settings
from ...系统环境映射 import get_default_llm_path


# ─── 内部工具 ──────────────────────────────────────────────

# 设置输入字段（GraphQL）→ 持久化字段（设置.json）映射
_输入字段映射 = {
    "模型来源": "model_source",
    "本地模型路径": "local_path",
    "本地模型名称": "local_model_name",
    "API基础URL": "base_url",
    "模型名称": "model_name",
    "API密钥": "api_key",
    "温度": "temperature",
    "最大令牌数": "max_tokens",
    "GitHub令牌": "github_token",
    "GitHub用户名": "github_username",
    "GitHub默认仓库": "github_default_repo",
    "GitHub可见性": "github_visibility",
}


def _规范化页码(页数: int, 每页数量: int) -> tuple[int, int]:
    """规范化分页参数，避免越界"""
    if 页数 < 1:
        页数 = 1
    if 每页数量 < 1:
        每页数量 = 1
    if 每页数量 > 100:
        每页数量 = 100
    return 页数, 每页数量


def _构建分页信息(总数: int, 当前页: int, 页大小: int) -> 分页信息:
    """构建分页信息对象"""
    总页数 = (总数 + 页大小 - 1) // 页大小 if 页大小 > 0 else 0
    return 分页信息(
        总数=总数,
        当前页=当前页,
        页大小=页大小,
        总页数=总页数,
    )


def _字典转设置(数据: dict) -> 设置:
    """将持久化设置字典转换为 GraphQL 设置类型。

    注意：``设置`` 类型本身未声明 api_key / github_token 字段，
    因此这些敏感数据天然不会出现在 GraphQL 返回值中（最稳妥的脱敏方式）。
    """
    return 设置(
        模型来源=str(数据.get("model_source", "api")),
        本地模型路径=str(数据.get("local_path", "")),
        本地模型名称=str(数据.get("local_model_name", "")),
        API基础URL=str(数据.get("base_url", "")),
        模型名称=str(数据.get("model_name", "")),
        温度=float(数据.get("temperature", 0.2) or 0.0),
        最大令牌数=int(数据.get("max_tokens", 4096) or 0),
        GitHub用户名=str(数据.get("github_username", "")),
        GitHub默认仓库=str(数据.get("github_default_repo", "")),
        GitHub可见性=str(数据.get("github_visibility", "public")),
    )


def _获取本地模型客户端():
    """惰性获取 local_model_client 单例

    使用惰性导入避免在测试或非 ComfyUI 环境下加载 PromptServer 失败。
    """
    from ...路由公共 import local_model_client
    return local_model_client


def _获取_llm_客户端():
    """惰性获取 llm_client 单例"""
    from ...路由公共 import llm_client
    return llm_client


# ─── Resolver 入口 ────────────────────────────────────────

async def 获取设置(info: Info) -> 设置:
    """读取系统设置并转换为 GraphQL 类型"""
    await 检查认证(info)
    数据 = load_settings()
    return _字典转设置(数据)


async def 更新设置(info: Info, 设置输入: 设置输入) -> 设置:
    """合并更新设置：仅写入显式提供（非 None）的字段"""
    await 检查认证(info)

    当前 = load_settings()
    # 仅更新非 None 字段
    for 输入键, 持久化键 in _输入字段映射.items():
        值 = getattr(设置输入, 输入键, None)
        if 值 is not None:
            当前[持久化键] = 值

    try:
        save_settings(当前)
    except Exception as e:
        raise 业务错误(f"保存设置失败: {e}") from e

    # 返回脱敏后的设置（重新从磁盘读取以确保数据一致）
    最新 = load_settings()
    return _字典转设置(最新)


async def 获取本地模型列表(info: Info, 页数: int = 1, 每页数量: int = 20) -> 本地模型列表响应:
    """扫描 ComfyUI/models/LLM 目录并分页返回"""
    await 检查认证(info)

    页数, 每页数量 = _规范化页码(页数, 每页数量)

    模型 = []
    llm_path = get_default_llm_path()
    if llm_path.exists():
        for item in llm_path.iterdir():
            if item.is_dir():
                has_config = (item / "config.json").exists()
                has_gguf = any(item.glob("*.gguf"))
                if has_config or has_gguf:
                    模型.append(本地模型(
                        名称=item.name,
                        类型="gguf" if has_gguf else "transformers",
                        路径=str(item),
                    ))
            elif item.suffix == ".gguf":
                模型.append(本地模型(
                    名称=item.stem,
                    类型="gguf",
                    路径=str(item),
                ))

    总数 = len(模型)
    起 = (页数 - 1) * 每页数量
    止 = 起 + 每页数量
    切片 = 模型[起:止]

    return 本地模型列表响应(
        模型列表=切片,
        分页=_构建分页信息(总数, 页数, 每页数量),
    )


async def 获取模型能力(info: Info, 模型来源: str = "api", 模型名称: str = "") -> 模型能力:
    """返回当前/指定模型的能力信息（是否支持视觉、文件内容等）"""
    await 检查认证(info)

    设置数据 = load_settings()
    实际来源 = 模型来源 or 设置数据.get("model_source", "api")
    实际名称 = 模型名称 or 设置数据.get("model_name", "")

    支持视觉 = False
    if 实际来源 == "api" and 实际名称:
        try:
            llm_client = _获取_llm_客户端()
            if llm_client is not None:
                llm_client.当前模型名 = 实际名称
                支持视觉 = bool(llm_client._supports_vision())
        except Exception:
            支持视觉 = False

    return 模型能力(
        模型来源=实际来源,
        模型名称=实际名称,
        支持视觉=支持视觉,
        # 文本文件内容始终支持（后端会解码 base64 并内联到消息）
        支持文件内容=True,
    )


async def 卸载模型(info: Info) -> bool:
    """卸载本地模型释放显存"""
    await 检查认证(info)

    客户端 = _获取本地模型客户端()
    if 客户端 is None:
        raise 业务错误("本地模型客户端未初始化")
    try:
        await 客户端.异步卸载模型()
        return True
    except Exception as e:
        raise 业务错误(f"卸载模型失败: {e}") from e


async def 重置模型状态(info: Info, 模型名称: Optional[str] = "") -> bool:
    """清除模型失败黑名单，允许重新加载"""
    await 检查认证(info)

    客户端 = _获取本地模型客户端()
    if 客户端 is None:
        raise 业务错误("本地模型客户端未初始化")
    try:
        # 空字符串视为未指定，清除全部黑名单
        实参 = 模型名称 if 模型名称 else None
        客户端.重置模型状态(实参)
        return True
    except Exception as e:
        raise 业务错误(f"重置模型状态失败: {e}") from e
