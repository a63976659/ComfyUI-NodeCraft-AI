"""GraphQL DataLoader — 请求级批量加载 / 缓存优化

由于 NodeCraft AI 的数据存储是文件系统（JSON / JSONL），不是数据库，
经典的 N+1 查询问题并不严重；这里的 DataLoader 主要承担"请求级缓存"
职责：在同一个 GraphQL 请求内，避免对相同资源重复执行 I/O。

每个 GraphQL 请求会通过 ``get_graphql_context`` 创建一组新的 DataLoader
实例（见 ``上下文.py``），请求结束后随 Context 一起释放。

当前内置：
- ``会话消息计数``：按 ``会话ID`` 缓存消息条数，供会话列表/详情中
  ``消息计数`` 字段命中时复用。
"""

from __future__ import annotations

from typing import Dict


class 会话消息计数缓存:
    """缓存会话消息计数，避免重复文件 I/O

    使用方式（在 resolver 中）::

        cache = info.context.dataloaders["会话消息计数"]
        计数 = await cache.获取消息计数(会话ID)

    设计要点：
    - 仅按需加载（懒加载），首次访问才读文件
    - 单请求内严格幂等：相同 ``会话ID`` 只读一次磁盘
    - 失败降级为 0，不抛异常（GraphQL 字段层面应保持可用）
    """

    def __init__(self) -> None:
        self._cache: Dict[str, int] = {}

    async def 获取消息计数(self, 会话ID: str) -> int:
        """返回指定会话的消息总数（命中缓存优先）"""
        if not 会话ID:
            return 0
        if 会话ID in self._cache:
            return self._cache[会话ID]

        计数 = 0
        try:
            # 延迟导入：避免在 GraphQL Schema 初始化阶段触发 I/O 模块副作用
            from ..文件读写操作 import load_session

            数据 = load_session(会话ID)
            if 数据 and isinstance(数据.get("messages"), list):
                计数 = len(数据["messages"])
        except Exception:
            计数 = 0

        self._cache[会话ID] = 计数
        return 计数

    def 清除缓存(self) -> None:
        """显式清空缓存（一般无需调用，请求结束随 Context 释放即可）"""
        self._cache.clear()


def create_dataloaders() -> Dict[str, object]:
    """创建一组 DataLoader 实例（每个 GraphQL 请求一组）

    返回一个 dict，键为 DataLoader 名称，值为对应实例。
    后续如果需要新增（例如：用户信息缓存、文件元数据缓存等），
    只需在此处追加即可，无需改动 ``GraphQLContext`` 结构。
    """
    return {
        "会话消息计数": 会话消息计数缓存(),
    }


__all__ = [
    "会话消息计数缓存",
    "create_dataloaders",
]
