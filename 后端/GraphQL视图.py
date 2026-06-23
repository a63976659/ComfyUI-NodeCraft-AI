"""自定义 GraphQL View — 集成 aiohttp + JWT 认证

将 ``strawberry.aiohttp.views.GraphQLView`` 与项目内 JWT 认证体系桥接，
并提供 ``register_graphql_routes`` 函数将 ``/graphql`` 端点挂载到
ComfyUI ``PromptServer`` 的 ``RouteTableDef`` 上。

挂载路径：``/graphql``
- ``GET``  ：返回 GraphiQL IDE（HTML）或执行 GET 形式查询
- ``POST`` ：执行 GraphQL 查询 / Mutation
- ``WebSocket``：用于 GraphQL Subscription（由 Strawberry View 内部处理）

设计要点
--------
1. **Context 注入**：override ``get_context``，调用项目侧
   ``get_graphql_context`` 解析 JWT、构建 ``GraphQLContext``。
2. **路由兼容**：ComfyUI 提供的是 ``aiohttp.web.RouteTableDef``，使用
   装饰器风格 ``routes.get(path)(handler)`` / ``routes.post(path)(handler)``
   完成注册。``GraphQLView`` 实例本身可作为 aiohttp handler 使用
   （``__call__(request)`` 异步签名）。
3. **健壮降级**：``register_graphql_routes`` 自身不吞异常，由调用方
   （``接口路由.py``）以 ``try/except`` 包裹，确保 Strawberry 未安装
   时不影响 REST API 正常工作。
"""

from __future__ import annotations

from typing import Any, Optional

from aiohttp import web
from strawberry.aiohttp.views import GraphQLView as StrawberryGraphQLView

from .GraphQL模式 import (
    GraphQLContext,
    get_graphql_context,
    schema,
)


class NodeCraftGraphQLView(StrawberryGraphQLView):
    """NodeCraft AI 自定义 GraphQL View

    扩展点：
    - 自定义 ``get_context``：注入 JWT 认证后的 ``GraphQLContext``
    - 复用 Strawberry 内置 GraphiQL IDE 与错误格式化
    """

    async def get_context(  # type: ignore[override]
        self,
        request: web.Request,
        response: Optional[web.StreamResponse] = None,
    ) -> Any:
        """注入项目认证上下文（含 JWT 解析结果）"""
        return await get_graphql_context(request, response)


def register_graphql_routes(app_or_routes) -> None:
    """注册 GraphQL 路由到 aiohttp ``RouteTableDef``

    Args:
        app_or_routes: ``aiohttp.web.RouteTableDef`` 实例
            （即 ``PromptServer.instance.routes``）。

    挂载路径：``/graphql``
    - ``GET``：GraphiQL IDE / 查询
    - ``POST``：查询执行
    - WebSocket：Subscriptions（由 Strawberry View 内部识别 Upgrade）
    """
    view = NodeCraftGraphQLView(
        schema=schema,
        graphql_ide="graphiql",
        allow_queries_via_get=True,
    )

    # RouteTableDef 装饰器风格注册：表达式等价于
    #     @routes.get('/graphql')
    #     async def handler(request): ...
    # GraphQLView 实例自身实现了 __call__(request) -> Response，可直接作为 handler。
    app_or_routes.get("/graphql")(view)
    app_or_routes.post("/graphql")(view)


__all__ = [
    "NodeCraftGraphQLView",
    "register_graphql_routes",
]
