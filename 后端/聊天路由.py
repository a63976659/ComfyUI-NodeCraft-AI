"""聊天路由模块

包含：
- POST /ai-coder/chat          非流式聊天
- POST /ai-coder/chat-stream   SSE 流式聊天
- _build_chat_context()        公共上下文构建（供 chat-stream 与 WebSocket 复用）
"""
import asyncio
import json
import time
from datetime import datetime
from pathlib import Path

from aiohttp import web

from .路由公共 import (
    _check_auth, _error_response, _rate_limiter, _metrics_collector,
    _format_attachment_descriptions, _format_file_tree, _检查上传大小,
    llm_client, local_model_client, tool_router, ContextManager, _agent_available,
)
from .文件读写操作 import load_session, save_session, load_settings
from .系统环境映射 import get_custom_nodes_path, get_default_llm_path
from .日志配置 import 获取日志器

logger = 获取日志器("聊天路由")

try:
    from 智能体.工具路由器 import 执行工具 as _执行工具
except Exception as _e:  # 保护性导入，失败时降级为 None
    logger.error(f"导入 工具路由器.执行工具 失败: {_e}")
    _执行工具 = None


# 各Tab页的差异化系统提示词
TAB_SYSTEM_PROMPTS = {
    "develop": (
        "你是专业的 ComfyUI 插件开发专家。请严格按照以下提供的规范编写代码。\n"
        "要求：\n"
        "1. 提供完整、可运行的代码，不要省略逻辑。\n"
        "2. __init__.py 等核心文件必须遵循英文命名，其他业务文件夹允许使用中文。\n"
    ),
    "optimize": (
        "你是专业的 ComfyUI 插件性能优化专家。请分析用户插件的性能瓶颈，提供优化方案。\n"
        "要求：\n"
        "1. 给出具体的优化代码和解释。\n"
        "2. 关注内存占用、推理速度、并发安全等关键指标。\n"
    ),
    "visualize": (
        "你是专业的 ComfyUI 插件功能分析专家。请帮助用户理解插件的功能结构和依赖关系。\n"
        "要求：\n"
        "1. 提供清晰的功能拆解和可视化建议。\n"
        "2. 识别模块间的耦合关系和优化机会。\n"
    ),
}

# 合法的 activeTab 取值
_VALID_ACTIVE_TABS = ("develop", "optimize", "visualize")


# ─── 工具使用指导（System Prompt 追加块） ──────────────────────
_TOOL_USAGE_GUIDE = """

## 工具能力
你可以使用以下工具直接操作当前插件文件：
- read_plugin_file(file_path): 读取指定文件的完整内容
- write_plugin_file(file_path, content): 修改指定文件（写入完整新内容）
- list_plugin_files(): 查看插件的完整文件目录结构

当用户要求查看或修改文件时，你应该：
1. 先用 read_plugin_file 读取当前文件内容
2. 分析理解代码后，用 write_plugin_file 写入修改后的完整文件内容
3. 向用户清晰说明你做了哪些修改

注意：write_plugin_file 需要提供文件的完整内容，不是增量补丁。
"""


def _resolve_plugin_path(plugin_context):
    """解析插件路径，支持绝对路径、相对 custom_nodes 路径或插件文件夹名，失败返回 None"""
    if not plugin_context:
        logger.debug("[工具调用] plugin_context 为空，跳过工具注入")
        return None

    logger.debug(f"[工具调用] 收到 plugin_context: {plugin_context}")

    try:
        # 1. 先尝试作为直接路径（绝对路径或当前工作目录相对路径）
        plugin_path = Path(plugin_context)
        if plugin_path.exists() and plugin_path.is_dir():
            logger.debug(f"[工具调用] 直接路径有效: {plugin_path}")
            return plugin_path.resolve()

        # 2. 尝试 resolve 后检查（处理 ../ 等相对路径）
        resolved_path = plugin_path.resolve()
        if resolved_path.exists() and resolved_path.is_dir():
            logger.debug(f"[工具调用] resolve 后路径有效: {resolved_path}")
            return resolved_path

        # 3. 尝试相对于 custom_nodes 目录解析
        custom_nodes_root = get_custom_nodes_path()
        relative_path = custom_nodes_root / plugin_context
        if relative_path.exists() and relative_path.is_dir():
            logger.debug(f"[工具调用] 相对 custom_nodes 路径有效: {relative_path}")
            return relative_path.resolve()

        # 4. 尝试作为插件名在 custom_nodes 中查找
        name_path = custom_nodes_root / plugin_context
        if name_path.exists() and name_path.is_dir():
            logger.debug(f"[工具调用] 插件名路径有效: {name_path}")
            return name_path.resolve()

        logger.warning(f"[工具调用] 无法解析插件路径: {plugin_context}")
        return None
    except Exception as e:
        logger.warning(f"[工具调用] 解析 plugin_context 异常: {e}")
        return None


def _build_tool_executor(plugin_path):
    """构建绑定 plugin_path 的 tool_executor 闭包；不可用时返回 (None, None)"""
    if not plugin_path or _执行工具 is None or tool_router is None:
        return None, None
    try:
        file_tools = tool_router.get_file_tools()
    except Exception as e:
        logger.exception(f"获取文件工具定义失败: {e}")
        return None, None

    plugin_path_str = str(plugin_path)

    async def _tool_executor(tool_name: str, tool_args: dict) -> str:
        try:
            return await _执行工具(tool_name, tool_args, plugin_path_str)
        except Exception as ex:
            return f"[工具执行异常]: {type(ex).__name__}: {ex}"

    return file_tools, _tool_executor


# ─── 公共聊天上下文构建 ────────────────────────────────────────

async def _build_chat_context(request, session_id, message, attachments=None, plugin_context=None, data=None, active_tab="develop", truncate_at=None):
    """构建聊天上下文（供 /chat-stream 和 WebSocket 共用）

    返回 dict:
        session_data, complete_messages, settings, model_source, full_message
    或抛出 ValueError(msg)

    truncate_at: 编辑重生成时使用，保留 messages[0:truncate_at] 后再追加新用户消息。
    """
    if attachments is None:
        attachments = []
    if data is None:
        data = {}

    # active_tab 校验：不在合法集合内时降级为 develop
    if active_tab not in _VALID_ACTIVE_TABS:
        active_tab = "develop"

    if not _agent_available:
        raise ValueError("智能体模块未加载，聊天功能不可用")

    # 1. 加载会话（P1-1：同步 I/O 放入线程池，避免阻塞事件循环）
    session_data = await asyncio.to_thread(load_session, session_id)
    if session_data is None:
        raise ValueError("会话不存在")

    # 2. 处理附件
    attachment_descriptions = _format_attachment_descriptions(attachments)

    full_message = message
    if attachment_descriptions:
        full_message = message + "\n" + "\n".join(attachment_descriptions)

    # 编辑重生成：截断指定位置之后的消息
    if truncate_at is not None:
        messages = session_data.get("messages", [])
        if truncate_at < 0 or truncate_at > len(messages):
            raise ValueError(f"truncate_at 越界: {truncate_at}, 当前消息数 {len(messages)}")
        session_data["messages"] = messages[:truncate_at]

    # 3. 将用户消息存入会话（不包含 _image_attachments 临时字段）
    now = datetime.now().isoformat(timespec="seconds")
    user_msg = {"role": "user", "content": message, "timestamp": now}
    if attachments:
        user_msg["attachments"] = [{"name": a.get("name"), "type": a.get("type"), "size": a.get("size")} for a in attachments]
    session_data.setdefault("messages", []).append(user_msg)
    # P1-1：同步 I/O 放入线程池
    await asyncio.to_thread(save_session, session_data)

    # 4. RAG 检索知识库（按 activeTab 差异化检索）
    retrieved_rules = tool_router.retrieve_knowledge(full_message, active_tab=active_tab)

    # 5. 构造系统提示词（按 activeTab 选择基础角色）
    base_prompt = TAB_SYSTEM_PROMPTS.get(active_tab, TAB_SYSTEM_PROMPTS["develop"])
    system_prompt = f"{base_prompt}{retrieved_rules}"

    # 5.1 插件上下文注入
    plugin_path = _resolve_plugin_path(plugin_context)
    if plugin_context and plugin_path is not None and plugin_path.exists():
        from .文件读写操作 import scan_plugin_file_tree
        try:
            # P1-1：目录扫描属同步 I/O，放入线程池
            file_tree = await asyncio.to_thread(scan_plugin_file_tree, str(plugin_path))
            tree_summary = _format_file_tree(file_tree)
            system_prompt += (
                f"\n\n## 当前操作的插件: {plugin_context}\n"
                f"插件路径: {plugin_path}\n"
                f"文件结构:\n{tree_summary}\n\n"
                "你可以直接建议修改上述文件中的内容。"
                "当用户要求修改文件时，请给出完整的修改方案和代码。"
            )
            # 追加工具使用指导（function calling 能力说明）
            system_prompt += _TOOL_USAGE_GUIDE
        except Exception as e:
            logger.exception(f"注入插件上下文失败: {e}")

    # 6. 读取设置（P1-1：同步 I/O 放入线程池）
    settings = await asyncio.to_thread(load_settings)

    # 7. 压缩上下文
    context_mgr = ContextManager()
    history_to_send = context_mgr.compress_history(
        session_data["messages"],
        max_tokens=settings.get("max_tokens", 4096)
    )

    # 附件增强注入：文本附件已在 full_message 中展开，这里处理图片附件的 base64 传递
    image_attachments = [
        a for a in attachments
        if a.get("type", "").startswith("image/") and a.get("data")
    ]
    if (image_attachments or attachment_descriptions) and history_to_send:
        last_user_idx = None
        for i in range(len(history_to_send) - 1, -1, -1):
            if history_to_send[i].get("role") == "user":
                last_user_idx = i
                break
        if last_user_idx is not None:
            # 创建新字典避免污染 session_data。compress_history 返回的是新 dict，
            # 但为清晰起见仍重建。将 content 替换为含附件描述的 full_message，
            # 同时为图片附件设置 _image_attachments 临时字段（仅请求链可见）
            new_msg = dict(history_to_send[last_user_idx])
            new_msg["content"] = full_message
            if image_attachments:
                new_msg["_image_attachments"] = image_attachments
            history_to_send[last_user_idx] = new_msg

    # 8. 构建完整 messages 列表
    complete_messages = [{"role": "system", "content": system_prompt}] + history_to_send

    # model_source
    model_source = data.get("model_source") or settings.get("model_source", "api")

    return {
        "session_data": session_data,
        "complete_messages": complete_messages,
        "settings": settings,
        "model_source": model_source,
        "full_message": full_message,
        "plugin_path": plugin_path if (plugin_path is not None and plugin_path.exists()) else None,
    }


# ─── 非流式聊天 ───────────────────────────────────────────────

async def handle_chat(request):
    """聊天接口：RAG 检索 → 构造提示词 → 压缩上下文 → 调用 LLM → 保存回复"""
    _start = time.time()
    try:
        # H4: 在读取请求体前预校验上传大小（附件 base64 可能超大）
        大小检查 = await _检查上传大小(request)
        if 大小检查:
            return 大小检查

        client_ip = request.remote or "unknown"
        if not _rate_limiter.is_allowed(client_ip):
            return _error_response("请求过于频繁，请稍后重试", 429)

        auth_error = await _check_auth(request)
        if auth_error:
            return auth_error

        data = await request.json()
        user_message = data.get("message", "")
        session_id = data.get("session_id")
        attachments = data.get("attachments", [])
        # 读取 activeTab，非法值降级为 develop
        active_tab = data.get("activeTab", "develop")
        if active_tab not in _VALID_ACTIVE_TABS:
            active_tab = "develop"

        # 编辑重生成：截断位置（保留 messages[0:truncate_at] 后追加新用户消息）
        truncate_at = data.get("truncate_at")
        if truncate_at is not None:
            try:
                truncate_at = int(truncate_at)
            except (TypeError, ValueError):
                return web.json_response({"success": False, "error": "truncate_at 必须为整数"}, status=400)

        if not session_id:
            return web.json_response({"success": False, "error": "缺少 session_id"}, status=400)

        if not _agent_available:
            return web.json_response({"success": False, "error": "智能体模块未加载，聊天功能不可用"}, status=503)

        # 1. 加载会话（P1-1：同步 I/O 放入线程池）
        session_data = await asyncio.to_thread(load_session, session_id)
        if session_data is None:
            return web.json_response({"success": False, "error": "会话不存在"}, status=404)

        # 编辑重生成：截断指定位置之后的消息
        if truncate_at is not None:
            messages = session_data.get("messages", [])
            if truncate_at < 0 or truncate_at > len(messages):
                return web.json_response(
                    {"success": False, "error": f"truncate_at 越界: {truncate_at}, 当前消息数 {len(messages)}"},
                    status=400,
                )
            session_data["messages"] = messages[:truncate_at]

        # 2. 处理附件：将附件信息（含文本附件内容）追加到用户消息中
        attachment_descriptions = _format_attachment_descriptions(attachments)

        # 拼接完整的用户消息内容
        full_message = user_message
        if attachment_descriptions:
            full_message = user_message + "\n" + "\n".join(attachment_descriptions)

        # 3. 将用户消息存入会话
        now = datetime.now().isoformat(timespec="seconds")
        user_msg = {"role": "user", "content": user_message, "timestamp": now}
        if attachments:
            user_msg["attachments"] = [{"name": a.get("name"), "type": a.get("type"), "size": a.get("size")} for a in attachments]
        session_data.setdefault("messages", []).append(user_msg)
        # P1-1：同步 I/O 放入线程池
        await asyncio.to_thread(save_session, session_data)  # 先持久化用户消息，防止后续 LLM 调用失败导致丢失

        # 4. RAG 检索知识库（按 activeTab 差异化检索）
        retrieved_rules = tool_router.retrieve_knowledge(full_message, active_tab=active_tab)

        # 5. 构造系统提示词（按 activeTab 选择基础角色）
        base_prompt = TAB_SYSTEM_PROMPTS.get(active_tab, TAB_SYSTEM_PROMPTS["develop"])
        system_prompt = f"{base_prompt}{retrieved_rules}"

        # 5.1 如果有插件上下文（优化模式），注入文件树信息
        plugin_context = data.get("plugin_context")
        plugin_path = _resolve_plugin_path(plugin_context)
        if plugin_context and plugin_path is not None and plugin_path.exists():
            from .文件读写操作 import scan_plugin_file_tree
            try:
                # P1-1：目录扫描属同步 I/O，放入线程池
                file_tree = await asyncio.to_thread(scan_plugin_file_tree, str(plugin_path))
                tree_summary = _format_file_tree(file_tree)
                system_prompt += (
                    f"\n\n## 当前操作的插件: {plugin_context}\n"
                    f"插件路径: {plugin_path}\n"
                    f"文件结构:\n{tree_summary}\n\n"
                    "你可以直接建议修改上述文件中的内容。"
                    "当用户要求修改文件时，请给出完整的修改方案和代码。"
                )
                # 追加工具使用指导（function calling 能力说明）
                system_prompt += _TOOL_USAGE_GUIDE
            except Exception as e:
                logger.exception(f"注入插件上下文失败: {e}")

        # 6. 读取设置（新版 AICoderClient 内部自动读取 settings，无需外部赋值）
        # P1-1：同步 I/O 放入线程池
        settings = await asyncio.to_thread(load_settings)

        # 7. 压缩上下文（ContextManager 为无状态工具类）
        context_mgr = ContextManager()
        history_to_send = context_mgr.compress_history(
            session_data["messages"],
            max_tokens=settings.get("max_tokens", 4096)
        )

        # 附件增强注入：文本附件已在 full_message 中展开，这里处理图片附件的 base64 传递
        image_attachments = [
            a for a in attachments
            if a.get("type", "").startswith("image/") and a.get("data")
        ]
        if (image_attachments or attachment_descriptions) and history_to_send:
            last_user_idx = None
            for i in range(len(history_to_send) - 1, -1, -1):
                if history_to_send[i].get("role") == "user":
                    last_user_idx = i
                    break
            if last_user_idx is not None:
                # 将 content 替换为含附件描述的 full_message，同时为图片附件设置 _image_attachments
                new_msg = dict(history_to_send[last_user_idx])
                new_msg["content"] = full_message
                if image_attachments:
                    new_msg["_image_attachments"] = image_attachments
                history_to_send[last_user_idx] = new_msg

        # 8. 根据 model_source 决定调用方式（优先使用请求体中的值）
        model_source = data.get("model_source") or settings.get("model_source", "api")

        if model_source == "local" and local_model_client is not None:
            # ─── 本地模型处理 ───
            local_model_name = settings.get("local_model_name", "")
            local_path = settings.get("local_path", "")

            # 优先使用请求中传来的模型信息（解决竞态问题）
            if data.get("local_model_name"):
                local_model_name = data["local_model_name"]

            if not local_path and local_model_name:
                # 从 LLM 目录拼接路径
                local_path = str(get_default_llm_path() / local_model_name)

            # 如果还是没有路径，尝试自动检测第一个可用模型
            if not local_path:
                llm_dir = get_default_llm_path()
                if llm_dir.exists():
                    for item in llm_dir.iterdir():
                        if item.is_dir() and (item / "config.json").exists():
                            local_path = str(item)
                            logger.info(f"自动选择本地模型: {item.name}")
                            break

            if not local_path:
                logger.error(f"本地模型路径为空! local_model_name='{local_model_name}', settings={settings}")
                return web.json_response({"success": False, "error": "未检测到可用的本地模型，请在设置中配置模型路径或将模型放入 models/LLM 目录"}, status=400)

            # 模型加载（增加错误处理）
            try:
                if local_model_client.当前模型名 != Path(local_path).name:
                    # P0-4: 使用异步加载（asyncio.Lock + 双重检查）防止并发重复加载
                    await local_model_client.异步加载模型(local_path)
            except ImportError as e:
                return web.json_response({"success": False, "error": f"缺少依赖库: {str(e)}。请安装 transformers 和 torch"}, status=500)
            except FileNotFoundError:
                return web.json_response({"success": False, "error": f"模型路径不存在: {local_path}"}, status=404)
            except RuntimeError as e:
                return web.json_response({"success": False, "error": str(e)}, status=503)
            except Exception as e:
                return web.json_response({"success": False, "error": f"模型加载失败: {str(e)}"}, status=500)

            logger.debug(f"[工具调用] 非流式分支检查 - plugin_path={plugin_path}, exists={plugin_path.exists() if plugin_path else False}")
            effective_plugin_path = plugin_path if (plugin_path is not None and plugin_path.exists()) else None
            if effective_plugin_path:
                file_tools, _tool_executor = _build_tool_executor(effective_plugin_path)
                reply_content = await local_model_client.generate_response_with_tools(
                    system_prompt, history_to_send, tools=file_tools, tool_executor=_tool_executor
                )
            else:
                reply_content = await local_model_client.generate_response(system_prompt, history_to_send)
        else:
            # API 模式：如插件路径有效则传递 tools/tool_executor，启用 Function Calling
            file_tools, _tool_executor = _build_tool_executor(plugin_path if (plugin_path is not None and plugin_path.exists()) else None)
            reply_content = await llm_client.generate_response(
                system_prompt, history_to_send,
                tools=file_tools,
                tool_executor=_tool_executor,
            )

        # 9. 将 AI 回复存入会话
        ai_now = datetime.now().isoformat(timespec="seconds")
        ai_msg = {"role": "assistant", "content": reply_content, "timestamp": ai_now}
        session_data["messages"].append(ai_msg)
        # P1-1：同步 I/O 放入线程池
        await asyncio.to_thread(save_session, session_data)

        return web.json_response({"status": "success", "reply": reply_content})
    except Exception as e:
        return web.json_response({"success": False, "error": str(e)}, status=500)
    finally:
        _metrics_collector.record_request("/ai-coder/chat", (time.time() - _start) * 1000, 200)


# ─── SSE 流式对话 ─────────────────────────────────────────────

async def handle_chat_stream(request):
    """流式对话接口（SSE）：RAG 检索 → 构造提示词 → 压缩上下文 → 流式调用 LLM → 保存回复"""
    _start = time.time()
    try:
        # H4: 在读取请求体前预校验上传大小（附件 base64 可能超大）
        大小检查 = await _检查上传大小(request)
        if 大小检查:
            return 大小检查

        client_ip = request.remote or "unknown"
        if not _rate_limiter.is_allowed(client_ip):
            return _error_response("请求过于频繁，请稍后重试", 429)

        auth_error = await _check_auth(request)
        if auth_error:
            return auth_error

        data = await request.json()
        user_message = data.get("message", "")
        session_id = data.get("session_id")
        attachments = data.get("attachments", [])
        # 读取 activeTab，非法值降级为 develop
        active_tab = data.get("activeTab", "develop")
        if active_tab not in _VALID_ACTIVE_TABS:
            active_tab = "develop"

        # 编辑重生成：截断位置（保留 messages[0:truncate_at] 后追加新用户消息）
        truncate_at = data.get("truncate_at")
        if truncate_at is not None:
            try:
                truncate_at = int(truncate_at)
            except (TypeError, ValueError):
                return _error_response("truncate_at 必须为整数", 400)

        if not session_id:
            return _error_response("缺少 session_id", 400)

        # 与 handle_chat 行为对齐：智能体模块不可用时立即返回 503，避免进入流式响应后仍崩溃
        if not _agent_available:
            return web.json_response(
                {"success": False, "error": "智能体模块未加载，聊天功能不可用"},
                status=503,
            )

        # 使用公共上下文构建（捕获 ValueError，按错误语义区分状态码）
        try:
            ctx = await _build_chat_context(
                request, session_id, user_message,
                attachments=attachments,
                plugin_context=data.get("plugin_context"),
                data=data,
                active_tab=active_tab,
                truncate_at=truncate_at,
            )
        except ValueError as e:
            msg = str(e)
            if "智能体模块" in msg:
                return web.json_response({"success": False, "error": msg}, status=503)
            if msg == "会话不存在":
                return web.json_response({"success": False, "error": msg}, status=404)
            return _error_response(msg, 400)
        except Exception as e:
            logger.exception(f"构建聊天上下文失败: {e}")
            return web.json_response({"success": False, "error": f"上下文构建失败: {e}"}, status=500)

        session_data = ctx["session_data"]
        complete_messages = ctx["complete_messages"]
        settings = ctx["settings"]
        model_source = ctx["model_source"]
        plugin_path = ctx.get("plugin_path")

        # 准备 SSE 响应
        response = web.StreamResponse()
        response.headers['Content-Type'] = 'text/event-stream'
        response.headers['Cache-Control'] = 'no-cache'
        response.headers['Connection'] = 'keep-alive'
        response.headers['X-Accel-Buffering'] = 'no'
        await response.prepare(request)

        full_reply = ""

        try:
            if model_source == "local" and local_model_client is not None:
                # 本地模型流式
                local_model_name = settings.get("local_model_name", "")
                local_path = settings.get("local_path", "")

                if data.get("local_model_name"):
                    local_model_name = data["local_model_name"]

                if not local_path and local_model_name:
                    local_path = str(get_default_llm_path() / local_model_name)

                if not local_path:
                    llm_dir = get_default_llm_path()
                    if llm_dir.exists():
                        for item in llm_dir.iterdir():
                            if item.is_dir() and (item / "config.json").exists():
                                local_path = str(item)
                                break

                if not local_path:
                    error_data = json.dumps({"error": "未检测到可用的本地模型", "done": True}, ensure_ascii=False)
                    await response.write(f"data: {error_data}\n\n".encode('utf-8'))
                    return response

                # 模型加载
                try:
                    if local_model_client.当前模型名 != Path(local_path).name:
                        # P0-4: 使用异步加载（asyncio.Lock + 双重检查）防止并发重复加载
                        await local_model_client.异步加载模型(local_path)
                except Exception as e:
                    error_data = json.dumps({"error": f"模型加载失败: {str(e)}", "done": True}, ensure_ascii=False)
                    await response.write(f"data: {error_data}\n\n".encode('utf-8'))
                    return response

                logger.debug(f"[工具调用] 流式分支检查 - plugin_path={plugin_path}, exists={plugin_path.exists() if plugin_path else False}")
                if plugin_path and plugin_path.exists():
                    file_tools, _tool_executor = _build_tool_executor(plugin_path)
                    async for chunk in local_model_client.流式对话_with_tools(
                        complete_messages, settings, tools=file_tools, tool_executor=_tool_executor
                    ):
                        full_reply += chunk
                        chunk_data = json.dumps({"content": chunk, "done": False}, ensure_ascii=False)
                        await response.write(f"data: {chunk_data}\n\n".encode('utf-8'))
                else:
                    async for chunk in local_model_client.流式对话(complete_messages, settings):
                        full_reply += chunk
                        chunk_data = json.dumps({"content": chunk, "done": False}, ensure_ascii=False)
                        await response.write(f"data: {chunk_data}\n\n".encode('utf-8'))
            else:
                # API 模式流式：如插件路径有效则启用 Function Calling
                file_tools, _tool_executor = _build_tool_executor(plugin_path)
                async for chunk in llm_client.流式对话(
                    complete_messages, settings,
                    tools=file_tools,
                    tool_executor=_tool_executor,
                ):
                    full_reply += chunk
                    chunk_data = json.dumps({"content": chunk, "done": False}, ensure_ascii=False)
                    await response.write(f"data: {chunk_data}\n\n".encode('utf-8'))

            # 发送完成信号
            done_data = json.dumps({"content": "", "done": True}, ensure_ascii=False)
            await response.write(f"data: {done_data}\n\n".encode('utf-8'))

            # 将 AI 回复存入会话
            if full_reply:
                import re
                clean_reply = re.sub(r'<thinking>[\s\S]*?</thinking>', '', full_reply).strip()
                ai_now = datetime.now().isoformat(timespec="seconds")
                ai_msg = {"role": "assistant", "content": clean_reply, "timestamp": ai_now}
                session_data["messages"].append(ai_msg)
                # P1-1：同步 I/O 放入线程池
                await asyncio.to_thread(save_session, session_data)

        except Exception as e:
            error_data = json.dumps({"error": str(e), "done": True}, ensure_ascii=False)
            await response.write(f"data: {error_data}\n\n".encode('utf-8'))

        return response
    except Exception as e:
        return _error_response(str(e), 500)
    finally:
        _metrics_collector.record_request("/ai-coder/chat-stream", (time.time() - _start) * 1000, 200)


def register_聊天路由(routes):
    """注册聊天端点"""
    routes.post("/ai-coder/chat")(handle_chat)
    routes.post("/ai-coder/chat-stream")(handle_chat_stream)
