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
    _error_response, _rate_limiter, _metrics_collector,
    _format_attachment_descriptions, _format_file_tree, _检查上传大小,
    llm_client, local_model_client, tool_router, ContextManager, _agent_available,
    _记忆管理器, _项目上下文分析器, _幂等缓存, _检查附件类型, RateLimiter,
)
from .文件读写操作 import load_session, save_session, load_settings
from .文件路由 import _resolve_plugin_path
from .系统环境映射 import get_custom_nodes_path, get_default_llm_path
from .日志配置 import 获取日志器
from 智能体.任务分类器 import 分类任务
from 智能体.模型能力注册表 import 模型能力注册表

logger = 获取日志器("聊天路由")

try:
    from 智能体.工具路由器 import 执行工具 as _执行工具
except Exception as _e:  # 保护性导入，失败时降级为 None
    logger.error(f"导入 工具路由器.执行工具 失败: {_e}")
    _执行工具 = None

# 全局模型能力注册表实例
_model_registry = 模型能力注册表()


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


# ─── 代码补全专用提示词 ──────────────────────────────────────
_CODE_COMPLETION_PROMPT = """You are a code completion assistant for ComfyUI plugin development.
Complete the code at the cursor position (marked by <|cursor|>).
Rules:
1. Output ONLY the completion text, no explanation
2. Complete the current line or block naturally
3. Follow the existing code style and indentation
4. For ComfyUI nodes, use V3 API (comfy_api.latest, io.Schema)
5. Keep completions short (1-5 lines typically)
6. Do not repeat code that already exists before the cursor"""

# 代码补全独立限流（120次/分钟，不与聊天共享 60次/分钟 限制）
_completion_rate_limiter = RateLimiter(max_requests=120, window_seconds=60)


# ─── 工具使用指导（System Prompt 追加块） ──────────────────────
_TOOL_USAGE_GUIDE = """

## 执行原则（最高优先级）
你是一个自动执行的编程 Agent。收到任务后必须：
1. **直接执行，不要描述方案等待确认** — 读取文件后立即调用 write_plugin_file/edit_file/batch_edit 完成修改
2. **一次完成所有修改** — 在一次对话中完成全部文件修改，不要分步等待用户回复
3. **修改完成后才说明** — 先执行操作，完成后简要总结改了什么
4. **遇到问题自行解决** — 如果工具执行失败，分析错误并重试，不要停下来问用户

禁止行为：
- ❌ 输出"修改方案"或"改造计划"然后等待用户说"开始"
- ❌ 读完文件后输出分析而不执行修改
- ❌ 对每个修改步骤都请求用户确认

## 工具能力
你可以使用以下工具直接操作当前插件文件：
- read_plugin_file(file_path): 读取指定文件的完整内容
- write_plugin_file(file_path, content): 全量写入文件（覆盖整个文件，适用于新建文件或大幅重写）
- edit_file(file_path, patch): 增量编辑文件（仅修改需要变更的部分，适用于局部修改，更高效）
- batch_edit(operations): 批量操作多个文件（适用于创建完整项目结构、同时修改多个文件）
- list_plugin_files(): 查看插件的完整文件目录结构

## 文件修改策略
你有三种文件修改方式，请根据场景选择：

### write_plugin_file（全量写入）
适用场景：
- 新建文件
- 文件大幅重写（超过一半内容需要修改）
- 文件较小（<50行）且修改较多

### edit_file（增量编辑）
适用场景：
- 修改文件中的几行或几十行
- 修改大文件的局部内容
- 只需要添加/删除/替换少量代码

### batch_edit（批量操作）
适用场景：
- 创建新插件时一次创建所有文件（__init__.py、节点文件、前端 JS 等）
- 同时修改多个相关文件
- 批量创建项目目录结构

操作类型说明：
- create: 创建新文件（文件已存在则跳过，不覆盖）
- write: 全量写入文件（覆盖已有内容）
- edit: 增量编辑（content 为 unified diff 补丁，格式同 edit_file）
- delete: 删除文件（自动创建 .bak 备份）

batch_edit 调用示例：
```json
{"name": "batch_edit", "arguments": {"operations": [
  {"action": "create", "file_path": "__init__.py", "content": "from .nodes import *\n"},
  {"action": "create", "file_path": "nodes.py", "content": "class MyNode:\n    pass\n"},
  {"action": "edit", "file_path": "config.py", "content": "--- a/config.py\n+++ b/config.py\n@@ -1,3 +1,4 @@\n old line\n+new line\n old line"}
]}}
```

优先级：**局部修改时优先使用 edit_file**，可以大幅节省 token 和时间。
**创建完整项目时优先使用 batch_edit**，一次操作多个文件更高效。

选择规则：
- 当文件超过50行时，必须优先使用 edit_file 进行局部修改，而非 write_plugin_file 全量覆写。
- 对于创建新文件，使用 write_plugin_file；对于修改已有文件，优先使用 edit_file。
- edit_file 使用 unified diff 格式（@@ -行号,行数 +行号,行数 @@），请确保 diff 上下文行与文件内容精确匹配。

edit_file 的 patch 参数使用 unified diff 格式，例如：
```
--- a/example.py
+++ b/example.py
@@ -10,3 +10,4 @@
 def hello():
-    print("old")
+    print("new")
+    print("added")
 def world():
```

使用 edit_file 时：
1. 先用 read_plugin_file 读取当前文件内容
2. 基于读取到的最新内容生成补丁
3. 补丁中的上下文行必须与文件实际内容精确匹配
4. 每个修改处提供足够的上下文行（至少1行）以确保准确定位

当用户要求修改文件时，你的执行流程：
1. 用 read_plugin_file 读取相关文件
2. 立即调用 edit_file 或 write_plugin_file 完成修改（不要停下来描述方案）
3. 所有修改完成后，简要列出改动点
"""


# _resolve_plugin_path 已从 文件路由 模块导入（统一实现）


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


def _get_model_context_limit(model_name: str, max_tokens_setting: int) -> int:
    """获取模型上下文窗口大小（委托给模型能力注册表，未知模型使用动态回退）"""
    info = _model_registry.获取模型信息(model_name)
    if info:
        return info.get("上下文窗口", 32000)
    return max(max_tokens_setting * 4, 32000)


def _calculate_context_health(complete_messages: list, model_name: str, max_tokens_setting: int) -> dict:
    """计算上下文健康度指标"""
    from 智能体.记忆与上下文压缩 import ContextManager
    import json as _json

    _ctx_mgr = ContextManager()
    # 估算所有消息的 token 总量
    _total_chars = sum(len(str(m.get("content", ""))) for m in complete_messages)
    _total_tokens = int(_total_chars * 1.2)  # 粗略估算

    _limit = _get_model_context_limit(model_name, max_tokens_setting)
    _percent = min((_total_tokens / _limit) * 100, 100.0)

    if _percent < 70:
        _level = "ok"
    elif _percent < 85:
        _level = "info"
    elif _percent < 95:
        _level = "warning"
    else:
        _level = "critical"

    return {
        "used_tokens": _total_tokens,
        "max_tokens": _limit,
        "usage_percent": round(_percent, 1),
        "level": _level
    }


# ─── 公共聊天上下文构建 ────────────────────────────────────────

async def _build_chat_context(request, session_id, message, attachments=None, plugin_context=None, data=None, active_tab="develop", truncate_at=None):
    """构建聊天上下文（供 /chat-stream 和 WebSocket 共用）

    Returns:
        dict: 包含以下键：
            - session_data: 会话数据字典
            - complete_messages: 完整消息列表（含系统提示）
            - settings: 设置字典
            - model_source: 模型来源（"api" 或 "local"）
            - full_message: 用户完整消息文本
            - plugin_path: 插件路径（Path 对象或 None）
            - system_prompt: 系统提示词文本
            - history_to_send: 发送给模型的历史消息列表

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

            # 项目上下文深度分析（AST 级结构化摘要）
            if _项目上下文分析器 is not None:
                try:
                    项目摘要 = await asyncio.to_thread(_项目上下文分析器.分析项目, plugin_path)
                    if 项目摘要:
                        system_prompt += f"\n\n--- 当前插件项目上下文 ---\n{项目摘要}"
                except Exception as e:
                    logger.debug(f"项目上下文分析失败: {e}")
        except Exception as e:
            logger.exception(f"注入插件上下文失败: {e}")

    # 5.2 注入跨会话记忆
    if _记忆管理器 is not None:
        try:
            _记忆文本 = await asyncio.to_thread(
                _记忆管理器.获取记忆注入文本,
                str(plugin_path) if plugin_path else None,
            )
            if _记忆文本:
                system_prompt += _记忆文本
        except Exception as e:
            logger.debug(f"跨会话记忆注入失败（忽略）: {e}")

    # 6. 读取设置（P1-1：同步 I/O 放入线程池）
    settings = await asyncio.to_thread(load_settings)

    # 7. 压缩上下文（LLM 摘要模式：需提前确定模型来源和客户端）
    model_source = data.get("model_source") or settings.get("model_source", "api")
    _compress_llm_client = local_model_client if model_source == "local" else llm_client
    context_mgr = ContextManager()
    history_to_send = await context_mgr.compress_history(
        session_data["messages"],
        max_tokens=int(settings.get("max_tokens", 4096) * 0.7),
        reserved_recent=2,
        llm_client=_compress_llm_client,
        model_source=model_source
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

    # model_source（已在步骤 7 压缩上下文前确定）

    # 自动模型路由（可选启用）
    if settings.get("auto_model_routing", False) and model_source == "api":
        _task_type = 分类任务(full_message, active_tab)
        _preferred = settings.get("preferred_models", {})
        _recommended = _preferred.get(_task_type) or _model_registry.获取推荐模型(_task_type)
        if _recommended:
            _current_model = settings.get("model_name", "")
            if _recommended != _current_model:
                logger.info(
                    f"[模型编排] 任务类型: {_task_type}, 当前模型: {_current_model}, "
                    f"切换到推荐模型: {_recommended}"
                )
                settings["model_name"] = _recommended
            else:
                logger.info(f"[模型编排] 任务类型: {_task_type}, 推荐模型与当前一致: {_recommended}")
        else:
            logger.info(f"[模型编排] 任务类型: {_task_type}, 未找到推荐模型，保持当前模型")
    elif settings.get("auto_model_routing", False):
        # auto_model_routing 开启但非 API 模式，仅记录日志
        logger.debug("[模型编排] 自动路由仅适用于 API 模式，当前为本地模式，跳过")

    return {
        "session_data": session_data,
        "complete_messages": complete_messages,
        "settings": settings,
        "model_source": model_source,
        "full_message": full_message,
        "plugin_path": plugin_path if (plugin_path is not None and plugin_path.exists()) else None,
        "system_prompt": system_prompt,
        "history_to_send": history_to_send,
    }


# ─── 非流式聊天 ───────────────────────────────────────────────

async def handle_chat(request):
    """聊天接口：RAG 检索 → 构造提示词 → 压缩上下文 → 调用 LLM → 保存回复"""
    _start = time.time()
    response_status = 200
    try:
        # H4: 在读取请求体前预校验上传大小（附件 base64 可能超大）
        大小检查 = await _检查上传大小(request)
        if 大小检查:
            return 大小检查

        client_ip = request.remote or "unknown"
        if not _rate_limiter.is_allowed(client_ip):
            return _error_response("请求过于频繁，请稍后重试", 429)

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

        # H4: 附件类型白名单校验
        附件类型错误 = await _检查附件类型(attachments)
        if 附件类型错误:
            return 附件类型错误

        # M8: 幂等键消费——防止网络重试导致重复提交
        idempotency_key = request.headers.get("X-Idempotency-Key", "")
        cached = _幂等缓存.检查(f"chat:{session_id}", idempotency_key)
        if cached is not None:
            return web.json_response(cached)

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
            return web.json_response({"success": False, "error": msg}, status=400)
        except Exception as e:
            logger.exception(f"构建聊天上下文失败: {e}")
            return web.json_response({"success": False, "error": f"上下文构建失败: {e}"}, status=500)

        session_data = ctx["session_data"]
        system_prompt = ctx["system_prompt"]
        history_to_send = ctx["history_to_send"]
        settings = ctx["settings"]
        model_source = ctx["model_source"]
        plugin_path = ctx.get("plugin_path")

        # 根据 model_source 决定调用方式
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

            logger.debug(f"[工具调用] 非流式分支检查 - plugin_path={plugin_path}")
            if plugin_path:
                file_tools, _tool_executor = _build_tool_executor(plugin_path)
                reply_content = await local_model_client.generate_response_with_tools(
                    system_prompt, history_to_send, tools=file_tools, tool_executor=_tool_executor
                )
            else:
                reply_content = await local_model_client.generate_response(system_prompt, history_to_send)
        else:
            # API 模式：如插件路径有效则传递 tools/tool_executor，启用 Function Calling
            file_tools, _tool_executor = _build_tool_executor(plugin_path)
            reply_content = await llm_client.generate_response(
                system_prompt, history_to_send,
                tools=file_tools,
                tool_executor=_tool_executor,
            )

        # 记录模型推理指标
        try:
            _metrics_collector.record_inference((time.time() - _start) * 1000, 0)
        except Exception:
            pass

        # 9. 将 AI 回复存入会话
        ai_now = datetime.now().isoformat(timespec="seconds")
        ai_msg = {"role": "assistant", "content": reply_content, "timestamp": ai_now}
        session_data["messages"].append(ai_msg)
        # P1-1：同步 I/O 放入线程池
        await asyncio.to_thread(save_session, session_data)

        # 异步提取跨会话记忆（不阻塞响应）
        if _记忆管理器 is not None:
            _plugin_path_str = str(plugin_path) if plugin_path else None
            asyncio.create_task(asyncio.to_thread(
                _记忆管理器.自动提取记忆,
                user_message, reply_content, _plugin_path_str
            ))

        _result = {"status": "success", "reply": reply_content}
        _幂等缓存.记录(f"chat:{session_id}", idempotency_key, _result)
        return web.json_response(_result)
    except Exception as e:
        logger.error(f"聊天请求异常: {e}", exc_info=True)
        response_status = 500
        return web.json_response({"success": False, "error": "服务器内部错误，请查看日志"}, status=500)
    finally:
        _metrics_collector.record_request("/ai-coder/chat", (time.time() - _start) * 1000, response_status)


# ─── SSE 流式对话 ─────────────────────────────────────────────

async def handle_chat_stream(request):
    """流式对话接口（SSE）：RAG 检索 → 构造提示词 → 压缩上下文 → 流式调用 LLM → 保存回复"""
    _start = time.time()
    response_status = 200
    try:
        # H4: 在读取请求体前预校验上传大小（附件 base64 可能超大）
        大小检查 = await _检查上传大小(request)
        if 大小检查:
            return 大小检查

        client_ip = request.remote or "unknown"
        if not _rate_limiter.is_allowed(client_ip):
            return _error_response("请求过于频繁，请稍后重试", 429)

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

        # H4: 附件类型白名单校验
        附件类型错误 = await _检查附件类型(attachments)
        if 附件类型错误:
            return 附件类型错误

        # M8: 幂等键消费——防止网络重试导致重复提交
        idempotency_key = request.headers.get("X-Idempotency-Key", "")
        cached = _幂等缓存.检查(f"stream:{session_id}", idempotency_key)
        if cached is not None:
            # 缓存命中：以 SSE 格式返回缓存的完整回复
            cached_response = web.StreamResponse()
            cached_response.headers['Content-Type'] = 'text/event-stream'
            cached_response.headers['Cache-Control'] = 'no-cache'
            cached_response.headers['Connection'] = 'keep-alive'
            cached_response.headers['X-Accel-Buffering'] = 'no'
            await cached_response.prepare(request)
            chunk_data = json.dumps({"content": cached, "done": False}, ensure_ascii=False)
            await cached_response.write(f"data: {chunk_data}\n\n".encode('utf-8'))
            done_data = json.dumps({"content": "", "done": True}, ensure_ascii=False)
            await cached_response.write(f"data: {done_data}\n\n".encode('utf-8'))
            return cached_response

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

        # 推送上下文健康度（流开始前）
        _health = _calculate_context_health(
            complete_messages,
            settings.get("model", ""),
            settings.get("max_tokens", 4096)
        )
        _health_data = json.dumps({"type": "context_health", **_health}, ensure_ascii=False)
        await response.write(f"data: {_health_data}\n\n".encode('utf-8'))

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
                        # 区分工具执行状态和普通文本（与 WebSocket路由 保持一致）
                        if chunk.strip().startswith("[正在执行:") and chunk.strip().endswith("...]"): 
                            chunk_data = json.dumps({"type": "tool_executing", "content": chunk, "done": False}, ensure_ascii=False)
                        else:
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
                _total_chars = sum(len(str(m.get('content', ''))) for m in complete_messages)
                logger.debug(f"[聊天路由] 发送消息总字符数: {_total_chars}, 消息数: {len(complete_messages)}")
                if _total_chars > 50000:
                    logger.warning(f"[聊天路由] 消息总字符数({_total_chars})过大，可能超出模型上下文窗口")
                file_tools, _tool_executor = _build_tool_executor(plugin_path)
                async for chunk in llm_client.流式对话(
                    complete_messages, settings,
                    tools=file_tools,
                    tool_executor=_tool_executor,
                ):
                    # 区分工具执行状态和普通文本（与本地模型流式路径保持一致）
                    if chunk.strip().startswith("[正在执行:") and chunk.strip().endswith("...]"):
                        chunk_data = json.dumps({"type": "tool_executing", "content": chunk, "done": False}, ensure_ascii=False)
                    elif chunk.strip().startswith("[自动继续执行"):
                        chunk_data = json.dumps({"type": "tool_executing", "content": chunk, "done": False}, ensure_ascii=False)
                    else:
                        full_reply += chunk
                        chunk_data = json.dumps({"content": chunk, "done": False}, ensure_ascii=False)
                    await response.write(f"data: {chunk_data}\n\n".encode('utf-8'))

            # 记录模型推理指标
            try:
                _metrics_collector.record_inference((time.time() - _start) * 1000, 0)
            except Exception:
                pass

            # 将 AI 回复存入会话
            if full_reply:
                import re
                clean_reply = re.sub(r'<thinking>[\s\S]*?</thinking>', '', full_reply).strip()
                ai_now = datetime.now().isoformat(timespec="seconds")
                ai_msg = {"role": "assistant", "content": clean_reply, "timestamp": ai_now}
                session_data["messages"].append(ai_msg)
                # P1-1：同步 I/O 放入线程池
                await asyncio.to_thread(save_session, session_data)

                # 异步提取跨会话记忆（不阻塞响应）
                if _记忆管理器 is not None:
                    _plugin_path_str = str(plugin_path) if plugin_path else None
                    asyncio.create_task(asyncio.to_thread(
                        _记忆管理器.自动提取记忆,
                        user_message, clean_reply, _plugin_path_str
                    ))

            # 推送最终上下文健康度（含本轮回复）
            _final_messages = complete_messages + [{"role": "assistant", "content": full_reply}]
            _final_health = _calculate_context_health(
                _final_messages,
                settings.get("model", ""),
                settings.get("max_tokens", 4096)
            )
            _final_health_data = json.dumps({"type": "context_health", **_final_health}, ensure_ascii=False)
            await response.write(f"data: {_final_health_data}\n\n".encode('utf-8'))

            # 发送完成信号
            done_data = json.dumps({"content": "", "done": True}, ensure_ascii=False)
            await response.write(f"data: {done_data}\n\n".encode('utf-8'))

            # M8: 记录幂等缓存（流式响应缓存完整回复文本）
            if full_reply:
                _幂等缓存.记录(f"stream:{session_id}", idempotency_key, full_reply)

        except Exception as e:
            error_data = json.dumps({"error": str(e), "done": True}, ensure_ascii=False)
            await response.write(f"data: {error_data}\n\n".encode('utf-8'))

        return response
    except Exception as e:
        response_status = 500
        return _error_response(str(e), 500)
    finally:
        _metrics_collector.record_request("/ai-coder/chat-stream", (time.time() - _start) * 1000, response_status)


# ─── 代码补全 ───────────────────────────────────────────────

async def handle_code_completion(request):
    """代码补全端点：根据光标位置补全代码"""
    _start = time.time()
    response_status = 200
    try:
        # 大小检查
        大小检查 = await _检查上传大小(request)
        if 大小检查:
            return 大小检查

        # 速率限制（独立限流，120次/分钟）
        client_ip = request.remote or "unknown"
        if not _completion_rate_limiter.is_allowed(client_ip):
            return _error_response("请求过于频繁，请稍后重试", 429)

        # 请求体解析
        data = await request.json()
        file_content = data.get("file_content") or ""
        if not isinstance(file_content, str):
            file_content = str(file_content)
        model_source = data.get("model_source", "api")
        local_model_name = data.get("local_model_name", "")

        # cursor_offset 安全转换
        try:
            cursor_offset = max(0, int(data.get("cursor_offset", 0)))
        except (TypeError, ValueError):
            cursor_offset = 0
        cursor_offset = min(cursor_offset, len(file_content))

        # 根据 cursor_offset 构建 prefix / suffix
        prefix = file_content[:cursor_offset]
        suffix = file_content[cursor_offset:]

        # 读取设置
        settings = await asyncio.to_thread(load_settings)
        if local_model_name:
            settings["local_model_name"] = local_model_name

        # 模型调用
        if model_source == "local" and local_model_client is not None:
            completion_text = await local_model_client.generate_completion(
                system_prompt=_CODE_COMPLETION_PROMPT, prefix=prefix, suffix=suffix, settings=settings
            )
        else:
            completion_text = await llm_client.generate_completion(
                system_prompt=_CODE_COMPLETION_PROMPT, prefix=prefix, suffix=suffix, settings=settings
            )

        # 记录模型推理指标
        try:
            _metrics_collector.record_inference((time.time() - _start) * 1000, 0)
        except Exception:
            pass

        return web.json_response({"status": "success", "completion": completion_text})
    except Exception as e:
        logger.error(f"代码补全请求异常: {e}", exc_info=True)
        response_status = 500
        return web.json_response({"status": "error", "completion": ""})
    finally:
        _metrics_collector.record_request("/ai-coder/code-completion", (time.time() - _start) * 1000, response_status)


def register_聊天路由(routes):
    """注册聊天端点"""
    routes.post("/ai-coder/chat")(handle_chat)
    routes.post("/ai-coder/chat-stream")(handle_chat_stream)
    routes.post("/ai-coder/code-completion")(handle_code_completion)
