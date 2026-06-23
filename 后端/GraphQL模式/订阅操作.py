"""GraphQL Subscription 根类型

NodeCraft AI GraphQL 订阅（实时数据流）入口。

当前包含：
- 心跳：测试用订阅，验证 WebSocket 通路与异步生成器
- 聊天流：流式聊天 —— 逐 token 推送 AI 回复（含完成事件）

后续 Task 4-8 会在此扩展更多订阅业务（如文件变更通知等）。

GraphQL 命名规范：根类型名/字段名/参数名必须满足 ``[_a-zA-Z0-9]``。
通过 ``name=``（字段）和 ``Annotated[..., strawberry.argument(name=...)]``
（参数）保持 Python 中文命名的同时，对外暴露 ASCII 兼容名称。
"""

import asyncio
import datetime
import time
from typing import Annotated, AsyncGenerator

import strawberry
from strawberry.types import Info

from .类型定义 import 聊天流事件

# 心跳订阅最大连接时长（秒）：超时后主动结束流，避免长连接无限期占用资源
HEARTBEAT_MAX_DURATION_SECONDS = 1800  # 30 分钟


# ─── 常用参数别名（GraphQL ASCII 名称）───
_间隔秒参数 = Annotated[int, strawberry.argument(name="intervalSeconds")]
_会话ID参数 = Annotated[str, strawberry.argument(name="sessionId")]
_消息内容参数 = Annotated[str, strawberry.argument(name="messageContent")]
_插件上下文参数 = Annotated[str, strawberry.argument(name="pluginContext")]
_模型来源参数 = Annotated[str, strawberry.argument(name="modelSource")]


@strawberry.type(name="Subscription")
class Subscription:
    """NodeCraft AI GraphQL 订阅根类型（实时数据流）"""

    @strawberry.subscription(
        name="heartbeat",
        description="测试订阅（保留：可用于验证 WebSocket 连接）",
    )
    async def 心跳(
        self,
        info: Info,
        间隔秒: _间隔秒参数 = 5,
    ) -> AsyncGenerator[str, None]:
        """每隔 N 秒推送一次心跳，最多推送 10 次或达到最大连接时长后自动结束。"""
        start_time = time.time()
        for _ in range(10):
            # 最大连接时长检查：超时后优雅结束订阅
            if time.time() - start_time > HEARTBEAT_MAX_DURATION_SECONDS:
                yield (
                    f"心跳超时结束（{HEARTBEAT_MAX_DURATION_SECONDS // 60} 分钟限制）"
                )
                return
            yield f"心跳: {datetime.datetime.now().isoformat()}"
            await asyncio.sleep(间隔秒)

    @strawberry.subscription(
        name="chatStream",
        description="流式聊天 — 逐 token 推送 AI 回复",
    )
    async def 聊天流(
        self,
        info: Info,
        会话ID: _会话ID参数,
        消息内容: _消息内容参数,
        插件上下文: _插件上下文参数 = "",
        模型来源: _模型来源参数 = "",
    ) -> AsyncGenerator[聊天流事件, None]:
        """订阅流式聊天事件。

        - 持续 yield 聊天流事件(内容=token, 完成=False)
        - 模型生成结束后 yield 聊天流事件(内容="", 完成=True)
        - 异常时 yield 一个 完成=True 的错误事件，避免连接断开
        """
        from .解析器.聊天逻辑 import 聊天流生成器

        async for 事件 in 聊天流生成器(
            info,
            会话ID,
            消息内容,
            插件上下文,
            模型来源,
        ):
            yield 事件
