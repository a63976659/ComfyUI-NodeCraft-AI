"""GraphQL 会话管理 Resolver

实现会话 CRUD 的业务逻辑函数：
- 获取会话列表（分页）
- 获取会话详情（含全部消息）
- 获取会话消息（分页）
- 创建会话
- 更新会话标题
- 删除会话

每个 resolver 入口均执行 `await 检查认证(info)`，确保兼容三层认证策略。
数据持久化复用 `后端.文件读写操作` 中的同步 I/O 函数。
"""

from __future__ import annotations

import math
import uuid
from datetime import datetime
from typing import List, Optional

from strawberry.types import Info

from ..中间件 import 检查认证
from ..异常定义 import 验证错误, 资源不存在
from ..类型定义 import (
    会话,
    会话详情,
    会话列表响应,
    分页信息,
    消息,
    消息列表响应,
    消息角色,
    附件,
)

# 复用现有同步文件 I/O 函数
from ...文件读写操作 import (
    load_sessions_list,
    load_session,
    save_session,
    delete_session,
)


# ─── 内部工具 ──────────────────────────────────────────────

def _规范化页码(页数: int, 每页数量: int) -> tuple[int, int]:
    """规范化分页参数，限制范围"""
    if 页数 < 1:
        页数 = 1
    if 每页数量 < 1:
        每页数量 = 1
    if 每页数量 > 200:
        每页数量 = 200
    return 页数, 每页数量


def _构建分页信息(总数: int, 当前页: int, 页大小: int) -> 分页信息:
    """根据总数与分页参数构建分页信息对象"""
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
    """对列表执行 offset 分页切片"""
    起始 = (页数 - 1) * 每页数量
    结束 = 起始 + 每页数量
    return 数据[起始:结束]


def _解析角色(role_str: str) -> 消息角色:
    """将字符串角色转为枚举，未知角色降级为 用户"""
    映射 = {
        "user": 消息角色.USER,
        "assistant": 消息角色.ASSISTANT,
        "system": 消息角色.SYSTEM,
    }
    return 映射.get((role_str or "").lower(), 消息角色.USER)


def _字典转附件(数据: dict) -> 附件:
    """将原始 dict 转为附件 Strawberry 类型实例"""
    return 附件(
        名称=数据.get("name", "") or 数据.get("名称", ""),
        类型=数据.get("type", "") or 数据.get("mime", "") or "",
        大小字节数=int(数据.get("size", 0) or 数据.get("大小", 0) or 0),
    )


def _字典转消息(数据: dict) -> 消息:
    """将原始消息 dict 转为消息 Strawberry 类型实例"""
    附件原始 = 数据.get("attachments") or 数据.get("附件") or []
    附件列表 = [_字典转附件(项) for 项 in 附件原始 if isinstance(项, dict)]
    return 消息(
        角色=_解析角色(数据.get("role", "user")),
        内容=str(数据.get("content", "") or ""),
        时间戳=str(数据.get("timestamp", "") or ""),
        附件列表=附件列表,
    )


def _字典转会话摘要(数据: dict) -> 会话:
    """将摘要 dict 转为 会话 Strawberry 类型实例"""
    创建时间 = str(数据.get("created_at", "") or "")
    更新时间 = str(数据.get("updated_at") or 创建时间)
    return 会话(
        ID=str(数据.get("id", "") or ""),
        标题=str(数据.get("title", "") or "未命名会话"),
        创建时间=创建时间,
        更新时间=更新时间,
        插件目录=str(数据.get("plugin_folder", "") or ""),
        消息计数=int(数据.get("message_count", 0) or 0),
    )


# ─── Query Resolver ───────────────────────────────────────

async def 获取会话列表(info: Info, 页数: int = 1, 每页数量: int = 20) -> 会话列表响应:
    """获取会话列表（分页）"""
    await 检查认证(info)

    页数, 每页数量 = _规范化页码(页数, 每页数量)

    全部会话 = load_sessions_list() or []
    总数 = len(全部会话)

    当前页数据 = _切片分页(全部会话, 页数, 每页数量)
    会话项列表 = [_字典转会话摘要(项) for 项 in 当前页数据]

    return 会话列表响应(
        会话列表=会话项列表,
        分页=_构建分页信息(总数, 页数, 每页数量),
    )


async def 获取会话详情(info: Info, 会话ID: str) -> Optional[会话详情]:
    """获取指定会话的完整详情（含所有消息）"""
    await 检查认证(info)

    if not 会话ID:
        raise 验证错误("会话ID 不能为空")

    数据 = load_session(会话ID)
    if 数据 is None:
        return None

    消息原始 = 数据.get("messages") or []
    消息列表: List[消息] = [
        _字典转消息(项) for 项 in 消息原始 if isinstance(项, dict)
    ]

    创建时间 = str(数据.get("created_at", "") or "")
    更新时间 = str(数据.get("updated_at") or 创建时间)

    return 会话详情(
        ID=str(数据.get("id", "") or 会话ID),
        标题=str(数据.get("title", "") or "未命名会话"),
        创建时间=创建时间,
        更新时间=更新时间,
        插件目录=str(数据.get("plugin_folder", "") or ""),
        消息列表=消息列表,
    )


async def 获取会话消息(
    info: Info,
    会话ID: str,
    页数: int = 1,
    每页数量: int = 50,
) -> 消息列表响应:
    """获取指定会话的消息列表（分页）"""
    await 检查认证(info)

    if not 会话ID:
        raise 验证错误("会话ID 不能为空")

    页数, 每页数量 = _规范化页码(页数, 每页数量)

    数据 = load_session(会话ID)
    if 数据 is None:
        raise 资源不存在("会话", 会话ID)

    消息原始 = 数据.get("messages") or []
    总数 = len(消息原始)

    当前页数据 = _切片分页(消息原始, 页数, 每页数量)
    消息项列表 = [_字典转消息(项) for 项 in 当前页数据 if isinstance(项, dict)]

    return 消息列表响应(
        消息列表=消息项列表,
        分页=_构建分页信息(总数, 页数, 每页数量),
    )


# ─── Mutation Resolver ────────────────────────────────────

async def 创建会话(info: Info, 标题: str = "新会话", 插件目录: str = "") -> 会话:
    """创建新会话，返回会话摘要"""
    await 检查认证(info)

    标题 = (标题 or "").strip() or "新会话"
    if len(标题) > 500:
        raise 验证错误("会话标题长度不能超过500个字符")

    会话ID = str(uuid.uuid4())
    现在 = datetime.now().isoformat(timespec="seconds")

    会话数据 = {
        "id": 会话ID,
        "title": 标题,
        "created_at": 现在,
        "updated_at": 现在,
        "plugin_folder": 插件目录 or "",
        "messages": [],
    }
    save_session(会话数据)

    return 会话(
        ID=会话ID,
        标题=标题,
        创建时间=现在,
        更新时间=现在,
        插件目录=插件目录 or "",
        消息计数=0,
    )


async def 更新会话标题(info: Info, 会话ID: str, 新标题: str) -> bool:
    """更新指定会话的标题"""
    await 检查认证(info)

    if not 会话ID:
        raise 验证错误("会话ID 不能为空")

    新标题清理 = (新标题 or "").strip()
    if not 新标题清理:
        raise 验证错误("会话标题不能为空")
    if len(新标题清理) > 500:
        raise 验证错误("会话标题长度不能超过500个字符")

    数据 = load_session(会话ID)
    if 数据 is None:
        raise 资源不存在("会话", 会话ID)

    数据["title"] = 新标题清理
    数据["updated_at"] = datetime.now().isoformat(timespec="seconds")
    save_session(数据)
    return True


async def 删除会话(info: Info, 会话ID: str) -> bool:
    """删除指定会话"""
    await 检查认证(info)

    if not 会话ID:
        raise 验证错误("会话ID 不能为空")

    成功 = delete_session(会话ID)
    if not 成功:
        raise 资源不存在("会话", 会话ID)
    return True
