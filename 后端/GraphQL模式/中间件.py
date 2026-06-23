"""GraphQL 中间件 — 错误处理 + 认证装饰器

- ``require_auth``：resolver 级强制认证装饰器
- ``检查认证``：内联认证检查工具函数
- ``graphql_error_handler``：将自定义异常转为 GraphQL 标准错误格式
"""

import functools
import logging
from typing import Callable

from strawberry.types import Info

from .异常定义 import GraphQL基础异常, 认证错误

logger = logging.getLogger("NodeCraftAI.GraphQL")


def require_auth(func: Callable) -> Callable:
    """认证装饰器 — 强制要求用户认证

    用于 resolver 函数级别的认证控制。
    兼容“无用户模式”：如果系统没有注册用户，不强制认证。

    用法::

        @strawberry.field
        @require_auth
        async def 需要认证的查询(self, info: Info) -> str:
            ...
    """

    @functools.wraps(func)
    async def wrapper(*args, **kwargs):
        # 从位置参数中找到 info 对象
        info = None
        for arg in args:
            if isinstance(arg, Info):
                info = arg
                break
        if info is None:
            info = kwargs.get("info")

        if info is None:
            # 无法获取 info，放行（避免误伤非 GraphQL 调用场景）
            return await func(*args, **kwargs)

        context = info.context

        # 如果系统启用了认证且用户未认证，拒绝
        if getattr(context, "需要认证", False) and not getattr(context, "已认证", False):
            raise 认证错误()

        return await func(*args, **kwargs)

    return wrapper


async def 检查认证(info: Info) -> None:
    """内联认证检查（在 resolver 内部调用）

    用法::

        async def my_resolver(self, info: Info):
            await 检查认证(info)
            # ... 业务逻辑
    """
    context = info.context
    if getattr(context, "需要认证", False) and not getattr(context, "已认证", False):
        raise 认证错误()


def graphql_error_handler(error):
    """GraphQL 错误格式化处理

    将自定义异常转为标准 GraphQL 错误格式，
    在 ``extensions`` 中携带业务 ``code`` 与 HTTP ``status_code``。
    """
    original = getattr(error, "original_error", None)

    if isinstance(original, GraphQL基础异常):
        error.extensions = {
            "code": original.code,
            "status_code": original.status_code,
        }
        logger.warning(
            f"GraphQL 业务错误 [{original.code}]: {original.message}"
        )
    else:
        # 未知错误：记录完整堆栈，对外返回标准内部错误码
        if original is not None:
            logger.exception(f"GraphQL 内部错误: {original}")
            error.extensions = {
                "code": "INTERNAL_SERVER_ERROR",
                "status_code": 500,
            }

    return error
