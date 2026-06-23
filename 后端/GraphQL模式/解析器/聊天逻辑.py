"""GraphQL 聊天 Resolver

实现 GraphQL 聊天业务逻辑：
- 发送聊天消息(): 非流式聊天 Mutation —— 累积全部 token 后返回完整文本
- 聊天流生成器(): 流式聊天 Subscription —— 逐 token yield 聊天流事件

设计原则：
- 复用 ``后端.聊天路由._build_chat_context``：完整继承 RAG 检索、系统提示词
  构建、上下文压缩、插件树注入等业务逻辑，避免重复实现。
- 统一通过 ``智能体.*.流式对话`` 接口生成 token；非流式版本只是把全部 chunk
  累积成一条字符串后返回，保证两条路径在模型行为上完全一致。
- 错误处理：
    * 非流式：抛出 GraphQL 业务异常（认证 / 资源不存在 / 业务错误）
    * 流式：统一捕获后 yield 一个 完成=True 的错误事件，避免 WebSocket
      因未捕获异常断开连接。
"""

from __future__ import annotations

import re
import time
from datetime import datetime
from pathlib import Path
from typing import AsyncGenerator, Optional

from strawberry.types import Info

from ..异常定义 import 业务错误, 资源不存在
from ..中间件 import 检查认证
from ..类型定义 import 聊天流事件

# 复用聊天路由中已实现的上下文构建与工具执行器构建逻辑
from ...聊天路由 import _build_chat_context, _build_tool_executor
from ...路由公共 import (
    llm_client,
    local_model_client,
    _agent_available,
)
from ...文件读写操作 import save_session
from ...系统环境映射 import get_default_llm_path
from ...日志配置 import 获取日志器


logger = 获取日志器("GraphQL.聊天逻辑")

# 流式响应最大时长（秒）：超过该时长后自动结束流，避免长连接无限期占用资源
STREAM_TIMEOUT_SECONDS = 300  # 5 分钟


# ─── 内部工具 ──────────────────────────────────────────────

def _解析本地模型路径(settings: dict, 数据: dict) -> Optional[str]:
    """与 聊天路由 中的本地模型路径解析逻辑保持一致"""
    local_model_name = 数据.get("local_model_name") or settings.get("local_model_name", "")
    local_path = settings.get("local_path", "")

    if not local_path and local_model_name:
        local_path = str(get_default_llm_path() / local_model_name)

    if not local_path:
        llm_dir = get_default_llm_path()
        try:
            if llm_dir.exists():
                for item in llm_dir.iterdir():
                    if item.is_dir() and (item / "config.json").exists():
                        local_path = str(item)
                        break
        except Exception as e:
            logger.exception(f"扫描本地模型目录失败: {e}")

    return local_path or None


async def _执行流式对话(ctx_data: dict, 数据: dict) -> AsyncGenerator[str, None]:
    """统一的 token 生成器：屏蔽 API / 本地模型差异，逐 chunk yield 字符串"""
    complete_messages = ctx_data["complete_messages"]
    settings = ctx_data["settings"]
    model_source = ctx_data["model_source"]
    plugin_path = ctx_data.get("plugin_path")

    if model_source == "local" and local_model_client is not None:
        local_path = _解析本地模型路径(settings, 数据)
        if not local_path:
            raise ValueError(
                "未检测到可用的本地模型，请在设置中配置模型路径或将模型放入 models/LLM 目录"
            )

        try:
            if local_model_client.当前模型名 != Path(local_path).name:
                # 与 聊天路由 一致：使用异步加载（asyncio.Lock + 双重检查）
                await local_model_client.异步加载模型(local_path)
        except ImportError as e:
            raise ValueError(f"缺少依赖库: {e}。请安装 transformers 和 torch") from e
        except FileNotFoundError as e:
            raise ValueError(f"模型路径不存在: {local_path}") from e
        except Exception as e:
            raise ValueError(f"模型加载失败: {e}") from e

        if plugin_path and plugin_path.exists():
            file_tools, _tool_executor = _build_tool_executor(plugin_path)
            async for chunk in local_model_client.流式对话_with_tools(
                complete_messages, settings, tools=file_tools, tool_executor=_tool_executor
            ):
                yield chunk
        else:
            async for chunk in local_model_client.流式对话(complete_messages, settings):
                yield chunk
    else:
        if llm_client is None:
            raise ValueError("API 模型客户端未初始化")
        # API 模式：如插件路径有效则启用 Function Calling
        file_tools, _tool_executor = _build_tool_executor(plugin_path)
        async for chunk in llm_client.流式对话(
            complete_messages,
            settings,
            tools=file_tools,
            tool_executor=_tool_executor,
        ):
            yield chunk


def _清洗思维链(text: str) -> str:
    """剥离 <thinking>...</thinking> 区块（与 聊天路由 SSE 路径保持一致）"""
    if not text:
        return ""
    return re.sub(r"<thinking>[\s\S]*?</thinking>", "", text).strip()


def _保存助手回复(session_data: dict, full_reply: str) -> None:
    """将清洗后的 AI 回复持久化到会话文件"""
    clean_reply = _清洗思维链(full_reply)
    if not clean_reply:
        return
    ai_now = datetime.now().isoformat(timespec="seconds")
    session_data.setdefault("messages", []).append({
        "role": "assistant",
        "content": clean_reply,
        "timestamp": ai_now,
    })
    try:
        save_session(session_data)
    except Exception as e:
        logger.exception(f"保存 AI 回复到会话失败: {e}")


def _构建数据字典(模型来源: str) -> dict:
    """构建 _build_chat_context 所需的 data 兼容字典"""
    数据: dict = {}
    if 模型来源:
        数据["model_source"] = 模型来源
    return 数据


# ─── 非流式聊天（Mutation 入口） ──────────────────────────────

async def 发送聊天消息(
    info: Info,
    会话ID: str,
    消息内容: str,
    插件上下文: str = "",
    模型来源: str = "",
) -> str:
    """非流式聊天：等待模型完整生成后返回回复全文。

    流程：
        1. 认证检查
        2. _build_chat_context 构建消息上下文（RAG / 系统提示 / 历史压缩）
        3. 通过 流式对话 累积全部 token
        4. 持久化助手消息到会话
        5. 返回清洗后的回复文本
    """
    await 检查认证(info)

    if not _agent_available:
        raise 业务错误("智能体模块未加载，聊天功能不可用")
    if not 会话ID:
        raise 业务错误("缺少 会话ID")

    数据 = _构建数据字典(模型来源)

    try:
        ctx = await _build_chat_context(
            request=info.context.request,
            session_id=会话ID,
            message=消息内容,
            attachments=[],
            plugin_context=(插件上下文 or None),
            data=数据,
        )
    except ValueError as e:
        msg = str(e)
        if msg == "会话不存在":
            raise 资源不存在("会话", 会话ID) from e
        raise 业务错误(msg) from e

    full_reply = ""
    try:
        async for chunk in _执行流式对话(ctx, 数据):
            full_reply += chunk
    except ValueError as e:
        # 模型可用性 / 加载失败等可预期错误
        raise 业务错误(str(e)) from e
    except Exception as e:
        logger.exception(f"调用 LLM 失败: {e}")
        raise 业务错误(f"模型调用失败: {e}") from e

    _保存助手回复(ctx["session_data"], full_reply)
    return _清洗思维链(full_reply)


# ─── 流式聊天（Subscription 入口） ────────────────────────────

async def 聊天流生成器(
    info: Info,
    会话ID: str,
    消息内容: str,
    插件上下文: str = "",
    模型来源: str = "",
) -> AsyncGenerator[聊天流事件, None]:
    """流式聊天：逐 token yield 聊天流事件，结束时 yield 完成=True 事件。

    任何异常都会被转换为 一个 完成=True 的错误事件，避免 Subscription
    因未捕获异常导致 WebSocket 断开连接。
    """
    # 1. 认证检查（异常优雅降级为错误事件）
    try:
        await 检查认证(info)
    except Exception as e:
        yield 聊天流事件(内容=f"[认证失败] {e}", 完成=True)
        return

    if not _agent_available:
        yield 聊天流事件(内容="智能体模块未加载，聊天功能不可用", 完成=True)
        return
    if not 会话ID:
        yield 聊天流事件(内容="缺少 会话ID", 完成=True)
        return

    数据 = _构建数据字典(模型来源)

    # 2. 构建上下文
    try:
        ctx = await _build_chat_context(
            request=info.context.request,
            session_id=会话ID,
            message=消息内容,
            attachments=[],
            plugin_context=(插件上下文 or None),
            data=数据,
        )
    except ValueError as e:
        yield 聊天流事件(内容=str(e), 完成=True)
        return
    except Exception as e:
        logger.exception(f"构建聊天上下文失败: {e}")
        yield 聊天流事件(内容=f"上下文构建失败: {e}", 完成=True)
        return

    # 3. 逐 token 推送（带最大响应时间限制，超时优雅结束）
    full_reply = ""
    start_time = time.time()
    try:
        async for chunk in _执行流式对话(ctx, 数据):
            # 超时检查：超过最大响应时长后 yield 错误事件并优雅结束
            if time.time() - start_time > STREAM_TIMEOUT_SECONDS:
                logger.warning(
                    f"流式聊天响应超时（>{STREAM_TIMEOUT_SECONDS}s），自动结束流"
                )
                yield 聊天流事件(
                    内容=f"流式响应超时（{STREAM_TIMEOUT_SECONDS // 60} 分钟限制）",
                    完成=True,
                )
                return
            full_reply += chunk
            yield 聊天流事件(内容=chunk, 完成=False)
    except Exception as e:
        logger.exception(f"流式聊天异常: {e}")
        yield 聊天流事件(内容=f"[错误] {e}", 完成=True)
        return

    # 4. 持久化助手回复（保存失败不影响前端流结束信号）
    _保存助手回复(ctx["session_data"], full_reply)

    # 5. 流结束信号
    yield 聊天流事件(内容="", 完成=True)
