"""GraphQL 模型市场 / 模板市场 / 打包文件 Resolver

实现模型市场与模板市场相关的业务逻辑函数：
- 搜索 HuggingFace 模型 / 获取模型详情 / 启动下载 / 查询下载状态
- 列出本地已下载的市场模型
- 列出与从模板创建项目
- 列出已打包文件

为与 REST 端点 ``后端.市场与打包路由`` 共享下载任务状态、
模型目录与打包目录，本模块直接复用其模块级单例。
"""

from __future__ import annotations

import math
import uuid
from typing import List, Optional

from strawberry.types import Info

from ..中间件 import 检查认证
from ..异常定义 import 验证错误, 资源不存在, 业务错误
from ..类型定义 import (
    分页信息,
    本地模型,
    模型市场项,
    模型市场搜索响应,
    下载状态,
    模板,
    模板列表响应,
    打包文件,
    打包文件列表响应,
)

# 复用 REST 路由模块中已创建的单例，确保下载状态共享
from ...市场与打包路由 import (
    _model_market,
    _template_market,
    _plugin_packager,
)


# ─── 内部工具 ────────────────────────────────────────────

def _规范化页码(页数: int, 每页数量: int) -> tuple[int, int]:
    """规范化分页参数，限制最大每页数量。"""
    if 页数 < 1:
        页数 = 1
    if 每页数量 < 1:
        每页数量 = 1
    if 每页数量 > 100:
        每页数量 = 100
    return 页数, 每页数量


def _构建分页信息(总数: int, 当前页: int, 页大小: int) -> 分页信息:
    """构建 GraphQL 分页信息对象。"""
    if 页大小 <= 0:
        总页数 = 0
    else:
        总页数 = math.ceil(总数 / 页大小) if 总数 > 0 else 0
    return 分页信息(
        总数=总数,
        当前页=当前页,
        页大小=页大小,
        总页数=总页数,
    )


def _切片分页(数据: list, 页数: int, 每页数量: int) -> list:
    """对列表执行 offset 分页切片。"""
    起始 = (页数 - 1) * 每页数量
    结束 = 起始 + 每页数量
    return 数据[起始:结束]


def _字典转模型市场项(数据: dict) -> 模型市场项:
    """将 HuggingFace 模型原始 dict 转为 GraphQL 类型。"""
    大小标签 = ""
    if "size_mb" in 数据:
        try:
            大小标签 = f"{float(数据.get('size_mb') or 0):.1f} MB"
        except (TypeError, ValueError):
            大小标签 = ""
    elif "total_size_mb" in 数据:
        try:
            大小标签 = f"{float(数据.get('total_size_mb') or 0):.1f} MB"
        except (TypeError, ValueError):
            大小标签 = ""

    pipeline_tag = str(数据.get("pipeline_tag", "") or "")
    描述 = str(数据.get("description", "") or pipeline_tag)

    return 模型市场项(
        ID=str(数据.get("id", "") or ""),
        名称=str(数据.get("name", "") or 数据.get("id", "") or ""),
        描述=描述,
        下载量=int(数据.get("downloads", 0) or 0),
        点赞数=int(数据.get("likes", 0) or 0),
        大小=大小标签,
    )


def _字典转本地模型(数据: dict) -> 本地模型:
    """将本地模型 dict 转为 GraphQL 类型。"""
    名称 = str(数据.get("id") or 数据.get("name") or "")
    路径 = str(数据.get("path", "") or "")
    类型 = str(数据.get("type") or "transformers")
    return 本地模型(名称=名称, 类型=类型, 路径=路径)


def _字典转模板(数据: dict) -> 模板:
    """将模板 dict 转为 GraphQL 类型。"""
    return 模板(
        ID=str(数据.get("id", "") or ""),
        名称=str(数据.get("name", "") or ""),
        描述=str(数据.get("description", "") or ""),
        分类=str(数据.get("category", "") or ""),
    )


def _字典转打包文件(数据: dict) -> 打包文件:
    """将打包文件 dict 转为 GraphQL 类型。

    底层返回 size_mb（MB），此处转换为字节数整数以匹配 GraphQL 类型。
    """
    try:
        size_bytes = int(round(float(数据.get("size_mb", 0) or 0) * 1024 * 1024))
    except (TypeError, ValueError):
        size_bytes = 0
    return 打包文件(
        文件名=str(数据.get("filename", "") or ""),
        大小=size_bytes,
        创建时间=str(数据.get("created", "") or ""),
    )


# ─── Query Resolver ──────────────────────────────────────

async def 搜索模型市场(
    info: Info,
    关键词: str,
    排序: str = "downloads",
    页数: int = 1,
    每页数量: int = 10,
) -> 模型市场搜索响应:
    """搜索 HuggingFace 模型市场（需要认证）

    HuggingFace 公开 API 不直接提供分页游标，本接口通过加大底层
    ``limit`` 后做内存分页，``排序`` 参数当前由底层模块固定为
    ``downloads``，保留入参以兼容前端。
    """
    await 检查认证(info)

    关键词清理 = (关键词 or "").strip()
    if not 关键词清理:
        raise 验证错误("搜索关键词不能为空")

    页数, 每页数量 = _规范化页码(页数, 每页数量)
    抓取上限 = max(页数 * 每页数量, 每页数量)
    抓取上限 = min(抓取上限, 200)

    try:
        原始 = await _model_market.search_models(
            关键词清理, task="text-generation", limit=抓取上限,
        )
    except Exception as e:
        raise 业务错误(f"搜索失败: {e}")

    总数 = len(原始 or [])
    当前页 = _切片分页(原始 or [], 页数, 每页数量)

    return 模型市场搜索响应(
        模型列表=[_字典转模型市场项(项) for 项 in 当前页 if isinstance(项, dict)],
        分页=_构建分页信息(总数, 页数, 每页数量),
    )


async def 获取模型详情(info: Info, 模型ID: str) -> 模型市场项:
    """获取指定 HuggingFace 模型的详细信息（需要认证）"""
    await 检查认证(info)

    模型ID清理 = (模型ID or "").strip()
    if not 模型ID清理:
        raise 验证错误("模型ID 不能为空")

    try:
        信息 = await _model_market.get_model_info(模型ID清理)
    except Exception as e:
        raise 业务错误(f"获取模型信息失败: {e}")

    if 信息 is None:
        raise 资源不存在("模型", 模型ID清理)

    return _字典转模型市场项(信息)


async def 获取下载状态(info: Info, 模型ID: str) -> 下载状态:
    """获取模型下载进度（需要认证）"""
    await 检查认证(info)

    模型ID清理 = (模型ID or "").strip()
    if not 模型ID清理:
        raise 验证错误("模型ID 不能为空")

    原始 = _model_market.get_download_status(模型ID清理) or {}
    底层状态 = str(原始.get("status", "unknown") or "unknown")

    # 状态映射：底层 downloading/done/error/unknown → GraphQL 标准状态
    状态映射 = {
        "downloading": "downloading",
        "done": "completed",
        "error": "failed",
        "unknown": "unknown",
    }
    标准状态 = 状态映射.get(底层状态, 底层状态)

    try:
        进度 = float(原始.get("progress", 0) or 0) / 100.0
    except (TypeError, ValueError):
        进度 = 0.0
    if 进度 < 0:
        进度 = 0.0
    if 进度 > 1:
        进度 = 1.0

    消息 = str(原始.get("error", "") or "")
    if not 消息:
        if 标准状态 == "completed":
            消息 = "下载完成"
        elif 标准状态 == "downloading":
            消息 = f"下载中 {int(进度 * 100)}%"
        elif 标准状态 == "failed":
            消息 = "下载失败"
        else:
            消息 = "无下载任务"

    return 下载状态(状态=标准状态, 进度=进度, 消息=消息)


async def 获取本地市场模型(info: Info) -> List[本地模型]:
    """列出本地已下载的市场模型（需要认证）"""
    await 检查认证(info)
    模型 = _model_market.list_local_models() or []
    return [_字典转本地模型(项) for 项 in 模型 if isinstance(项, dict)]


async def 获取模板列表(
    info: Info,
    页数: int = 1,
    每页数量: int = 20,
) -> 模板列表响应:
    """获取模板列表（需要认证，分页）"""
    await 检查认证(info)
    页数, 每页数量 = _规范化页码(页数, 每页数量)

    全部 = _template_market.list_templates() or []
    总数 = len(全部)
    当前页 = _切片分页(全部, 页数, 每页数量)

    return 模板列表响应(
        模板列表=[_字典转模板(项) for 项 in 当前页 if isinstance(项, dict)],
        分页=_构建分页信息(总数, 页数, 每页数量),
    )


async def 获取打包文件列表(
    info: Info,
    页数: int = 1,
    每页数量: int = 20,
) -> 打包文件列表响应:
    """列出已打包文件（需要认证，分页）"""
    await 检查认证(info)
    页数, 每页数量 = _规范化页码(页数, 每页数量)

    全部 = _plugin_packager.list_packages() or []
    总数 = len(全部)
    当前页 = _切片分页(全部, 页数, 每页数量)

    return 打包文件列表响应(
        文件列表=[_字典转打包文件(项) for 项 in 当前页 if isinstance(项, dict)],
        分页=_构建分页信息(总数, 页数, 每页数量),
    )


# ─── Mutation Resolver ───────────────────────────────────

async def 下载模型(info: Info, 模型ID: str) -> str:
    """启动模型下载（需要认证），返回任务 ID

    与 REST ``/ai-coder/model-market/download`` 行为一致：在事件循环中
    创建异步下载任务，立即返回任务 ID，前端通过 ``获取下载状态`` 轮询。
    """
    await 检查认证(info)

    模型ID清理 = (模型ID or "").strip()
    if not 模型ID清理:
        raise 验证错误("模型ID 不能为空")

    任务ID = uuid.uuid4().hex
    try:
        import asyncio
        asyncio.create_task(_model_market.download_model(模型ID清理))
    except Exception as e:
        raise 业务错误(f"启动下载失败: {e}")

    return 任务ID


async def 从模板创建(info: Info, 模板ID: str, 项目名称: str) -> str:
    """从模板创建新项目（需要认证），返回新建项目的绝对路径"""
    await 检查认证(info)

    模板ID清理 = (模板ID or "").strip()
    项目名称清理 = (项目名称 or "").strip()
    if not 模板ID清理:
        raise 验证错误("模板ID 不能为空")
    if not 项目名称清理:
        raise 验证错误("项目名称不能为空")

    try:
        结果 = _template_market.create_from_template(模板ID清理, 项目名称清理)
    except Exception as e:
        raise 业务错误(f"创建项目失败: {e}")

    if not 结果.get("success"):
        raise 业务错误(str(结果.get("message") or "创建项目失败"))

    return str(结果.get("path", "") or "")
