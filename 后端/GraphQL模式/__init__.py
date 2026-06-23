"""NodeCraft AI GraphQL Schema 模块

负责装配 Strawberry Schema 并对外暴露：
- ``schema``：组合好的 ``strawberry.Schema`` 单例
- 根类型：``Query`` / ``Mutation`` / ``Subscription``
- 上下文：``GraphQLContext`` / ``get_graphql_context``
- 错误处理：``graphql_error_handler``
- DataLoader：``create_dataloaders``
- 公共类型与异常（通过 ``类型定义`` / ``异常定义`` 透传）
错误处理集成说明：
    Strawberry 的 ``Schema`` 允许重写 ``process_errors`` 方法以拦截运行时
    错误。这里通过子类 ``NodeCraftSchema`` 在 schema 层面集成项目自定义错
    误格式化器 ``graphql_error_handler``，让业务异常携带 ``code`` 与
    ``status_code`` extension 输出。
"""

import re
from typing import Any, List, Optional, Union

import strawberry
from graphql import ExecutionResult
from graphql.error import GraphQLError
from graphql.language import DocumentNode, print_ast

from .类型定义 import *  # noqa: F401,F403
from .异常定义 import *  # noqa: F401,F403
from .查询操作 import Query
from .变更操作 import Mutation
from .订阅操作 import Subscription
from .上下文 import GraphQLContext, get_graphql_context
from .中间件 import graphql_error_handler
from .数据加载器 import create_dataloaders


# ─── M4: 查询安全限制 ─────────────────────────────────────
最大查询深度: int = 10
最大查询复杂度: int = 100

# 用于复杂度估算时排除的 GraphQL 关键字 / 字面量
_保留字集合 = {
    "query", "mutation", "subscription", "fragment", "on",
    "true", "false", "null", "type", "input", "enum", "interface",
    "union", "scalar", "schema", "directive", "extend", "implements",
    "repeatable",
}

_标识符模式 = re.compile(r"\b[a-zA-Z_][a-zA-Z0-9_]*\b")
_字符串字面量模式 = re.compile(r'"(?:\\.|[^"\\])*"')


def _检查查询深度(query_string: str) -> int:
    """基于花括号计数法估算 GraphQL 查询的最大嵌套深度。

    会忽略字符串字面量内的花括号，避免变量值中的 `{` 干扰判定。
    """
    if not query_string:
        return 0
    当前深度 = 0
    最大检测深度 = 0
    在字符串中 = False
    上一个字符 = ""
    for ch in query_string:
        if ch == '"' and 上一个字符 != "\\":
            在字符串中 = not 在字符串中
            上一个字符 = ch
            continue
        if 在字符串中:
            上一个字符 = ch
            continue
        if ch == "{":
            当前深度 += 1
            if 当前深度 > 最大检测深度:
                最大检测深度 = 当前深度
        elif ch == "}":
            当前深度 -= 1
        上一个字符 = ch
    return 最大检测深度


def _估算查询复杂度(query_string: str) -> int:
    """基于字段标识符数量做简易复杂度估算（忽略字符串字面量与关键字）。"""
    if not query_string:
        return 0
    cleaned = _字符串字面量模式.sub("", query_string)
    标识符列表 = _标识符模式.findall(cleaned)
    return sum(1 for name in 标识符列表 if name not in _保留字集合)


def 校验查询安全(query: Optional[Union[str, DocumentNode]]) -> Optional[str]:
    """统一的 GraphQL 查询安全校验入口。

    返回:
        若违反深度/复杂度限制，返回错误描述字符串；否则返回 ``None``。
    """
    if query is None:
        return None
    if not isinstance(query, str):
        try:
            query = print_ast(query)
        except Exception:
            return None
    if not query.strip():
        return None

    深度 = _检查查询深度(query)
    if 深度 > 最大查询深度:
        return f"查询深度超过限制（最大 {最大查询深度} 层，当前 {深度} 层）"

    复杂度 = _估算查询复杂度(query)
    if 复杂度 > 最大查询复杂度:
        return f"查询复杂度超过限制（最大 {最大查询复杂度} 分，当前 {复杂度} 分）"

    return None


class NodeCraftSchema(strawberry.Schema):
    """项目专用 Schema — 在 schema 层面挂载错误处理器与查询安全限制

    1. ``process_errors``：拦截运行时错误并交由 ``graphql_error_handler``
       格式化业务异常 / 内部异常对外暴露的语义。
    2. ``execute`` / ``execute_sync``：在调用底层执行前，先做查询深度与
       复杂度校验，超限直接返回带 ``GraphQLError`` 的 ``ExecutionResult``，
       避免恶意查询拖垮后端。
    """

    def process_errors(  # type: ignore[override]
        self,
        errors: List[GraphQLError],
        execution_context=None,
    ) -> None:
        for error in errors or []:
            try:
                graphql_error_handler(error)
            except Exception:
                # 错误处理器本身不得招起异常，以免造成二次损伤
                pass
        super().process_errors(errors, execution_context)

    async def execute(  # type: ignore[override]
        self,
        query: Optional[Union[str, DocumentNode]],
        *args: Any,
        **kwargs: Any,
    ) -> ExecutionResult:
        错误消息 = 校验查询安全(query)
        if 错误消息:
            return ExecutionResult(data=None, errors=[GraphQLError(错误消息)])
        return await super().execute(query, *args, **kwargs)

    def execute_sync(  # type: ignore[override]
        self,
        query: Optional[Union[str, DocumentNode]],
        *args: Any,
        **kwargs: Any,
    ) -> ExecutionResult:
        错误消息 = 校验查询安全(query)
        if 错误消息:
            return ExecutionResult(data=None, errors=[GraphQLError(错误消息)])
        return super().execute_sync(query, *args, **kwargs)


# ─── 装配 GraphQL Schema ───
schema = NodeCraftSchema(
    query=Query,
    mutation=Mutation,
    subscription=Subscription,
)

__all__ = [
    "schema",
    "NodeCraftSchema",
    "Query",
    "Mutation",
    "Subscription",
    "GraphQLContext",
    "get_graphql_context",
    "graphql_error_handler",
    "create_dataloaders",
    "校验查询安全",
    "最大查询深度",
    "最大查询复杂度",
]
