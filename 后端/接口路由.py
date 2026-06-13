from aiohttp import web
from server import PromptServer
import sys
import uuid
from pathlib import Path
from datetime import datetime

from .文件读写操作 import (
    load_sessions_list, load_session, save_session, delete_session,
    load_settings, save_settings, create_plugin_scaffold,
)
from .系统环境映射 import get_custom_nodes_path, get_default_llm_path, get_plugin_root

# ─── 导入智能体模块 ───────────────────────────────────────
_plugin_root = str(get_plugin_root())
if _plugin_root not in sys.path:
    sys.path.insert(0, _plugin_root)

try:
    from 智能体.模型客户端 import AICoderClient, LocalModelClient
    from 智能体.工具路由器 import ToolRouter
    from 智能体.记忆与上下文压缩 import ContextManager

    llm_client = AICoderClient()
    local_model_client = LocalModelClient()
    tool_router = ToolRouter()
    _agent_available = True
except ImportError as e:
    print(f"[AI Coder] 智能体模块加载失败（聊天功能不可用）: {e}")
    llm_client = None
    local_model_client = None
    tool_router = None
    ContextManager = None
    _agent_available = False

# ─── 注册路由（装饰器方式，模块被导入时自动注册） ──────────
routes = PromptServer.instance.routes


# ─── 会话管理 ──────────────────────────────────────────────

@routes.get("/ai-coder/sessions")
async def handle_get_sessions(request):
    """获取所有会话列表"""
    try:
        sessions = load_sessions_list()
        return web.json_response({"sessions": sessions})
    except Exception as e:
        return web.json_response({"success": False, "error": str(e)}, status=500)


@routes.post("/ai-coder/sessions")
async def handle_create_session(request):
    """创建新会话"""
    try:
        data = await request.json()
        title = data.get("title", "新会话")
        plugin_folder = data.get("plugin_folder", "")

        session_id = str(uuid.uuid4())
        now = datetime.now().isoformat(timespec="seconds")

        session_data = {
            "id": session_id,
            "title": title,
            "created_at": now,
            "plugin_folder": plugin_folder,
            "messages": [],
        }
        save_session(session_data)
        return web.json_response({"session": session_data})
    except Exception as e:
        return web.json_response({"success": False, "error": str(e)}, status=500)


@routes.delete("/ai-coder/sessions/{id}")
async def handle_delete_session(request):
    """删除指定会话及其消息记录文件"""
    try:
        session_id = request.match_info["id"]
        delete_session(session_id)
        return web.json_response({"success": True})
    except Exception as e:
        return web.json_response({"success": False, "error": str(e)}, status=500)


@routes.get("/ai-coder/sessions/{id}/messages")
async def handle_get_messages(request):
    """获取指定会话的所有消息"""
    try:
        session_id = request.match_info["id"]
        session_data = load_session(session_id)
        if session_data is None:
            return web.json_response({"success": False, "error": "会话不存在"}, status=404)
        messages = session_data.get("messages", [])
        return web.json_response({"messages": messages})
    except Exception as e:
        return web.json_response({"success": False, "error": str(e)}, status=500)


# ─── 本地模型扫描 ────────────────────────────────────────

@routes.get("/ai-coder/local-models")
async def handle_get_local_models(request):
    """扫描本地 LLM 目录，返回可用模型列表"""
    try:
        llm_path = get_default_llm_path()
        models = []

        if llm_path.exists():
            for item in llm_path.iterdir():
                if item.is_dir():
                    # 检查是否是有效的模型目录（包含 config.json 或 .gguf 文件）
                    has_config = (item / "config.json").exists()
                    has_gguf = any(item.glob("*.gguf"))
                    if has_config or has_gguf:
                        models.append({
                            "name": item.name,
                            "type": "gguf" if has_gguf else "transformers",
                            "path": str(item)
                        })
                elif item.suffix == ".gguf":
                    # 单独的 GGUF 文件也列出
                    models.append({
                        "name": item.stem,
                        "type": "gguf",
                        "path": str(item)
                    })

        return web.json_response({"models": models})
    except Exception as e:
        return web.json_response({"models": [], "error": str(e)}, status=200)


# ─── 聊天 ─────────────────────────────────────────────────

@routes.post("/ai-coder/chat")
async def handle_chat(request):
    """聊天接口：RAG 检索 → 构造提示词 → 压缩上下文 → 调用 LLM → 保存回复"""
    try:
        data = await request.json()
        user_message = data.get("message", "")
        session_id = data.get("session_id")
        attachments = data.get("attachments", [])

        if not session_id:
            return web.json_response({"success": False, "error": "缺少 session_id"}, status=400)

        if not _agent_available:
            return web.json_response({"success": False, "error": "智能体模块未加载，聊天功能不可用"}, status=503)

        # 1. 加载会话
        session_data = load_session(session_id)
        if session_data is None:
            return web.json_response({"success": False, "error": "会话不存在"}, status=404)

        # 2. 处理附件：将附件信息追加到用户消息中
        attachment_descriptions = []
        for att in attachments:
            att_name = att.get("name", "未命名文件")
            att_type = att.get("type", "")
            att_size = att.get("size", 0)
            attachment_descriptions.append(f"[附件: {att_name} ({att_type}, {att_size}字节)]")

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

        # 4. RAG 检索知识库
        retrieved_rules = tool_router.retrieve_knowledge(full_message)

        # 5. 构造系统提示词
        system_prompt = (
            "你是专业的 ComfyUI 插件开发专家。请严格按照以下提供的规范编写代码。\n"
            "要求：\n"
            "1. 提供完整、可运行的代码，不要省略逻辑。\n"
            "2. __init__.py 等核心文件必须遵循英文命名，其他业务文件夹允许使用中文。\n"
            f"{retrieved_rules}"
        )

        # 6. 读取设置并更新 LLM 客户端配置
        settings = load_settings()
        llm_client.api_base = settings.get("base_url", "https://api.openai.com/v1")
        llm_client.api_key = settings.get("api_key", "")
        llm_client.model_name = settings.get("model_name", "qwen2.5-coder-32b-instruct")
        llm_client.mode = settings.get("model_source", "api")

        # 7. 压缩上下文（ContextManager 为无状态工具类）
        context_mgr = ContextManager()
        history_to_send = context_mgr.compress_history(
            session_data["messages"],
            max_tokens=settings.get("max_tokens", 4096)
        )

        # 如果有图片附件，将最新用户消息的图片附件base64加入上下文（支持视觉模型）
        image_attachments = [a for a in attachments if a.get("type", "").startswith("image/")]
        if image_attachments and history_to_send:
            # 在最后一条用户消息中补充图片描述
            last_user_idx = None
            for i in range(len(history_to_send) - 1, -1, -1):
                if history_to_send[i].get("role") == "user":
                    last_user_idx = i
                    break
            if last_user_idx is not None:
                img_desc = "\n".join([f"[图片附件: {a.get('name', '')}]" for a in image_attachments])
                history_to_send[last_user_idx]["content"] = full_message + "\n" + img_desc if full_message != user_message else full_message

        # 8. 根据 model_source 决定调用方式
        model_source = settings.get("model_source", "api")

        if model_source == "local" and local_model_client is not None:
            # 本地模型推理
            local_model_name = settings.get("local_model_name", "")
            local_path = settings.get("local_path", "")
            if not local_path and local_model_name:
                # 从 LLM 目录拼接路径
                local_path = str(get_default_llm_path() / local_model_name)

            if not local_path:
                return web.json_response({"success": False, "error": "未配置本地模型路径"}, status=400)

            # 加载模型（如果尚未加载或切换了模型）
            if local_model_client.当前模型名 != Path(local_path).name:
                local_model_client.加载模型(local_path)

            reply_content = await local_model_client.generate_response(system_prompt, history_to_send)
        else:
            # API 模式
            reply_content = await llm_client.generate_response(system_prompt, history_to_send)

        # 9. 将 AI 回复存入会话
        ai_now = datetime.now().isoformat(timespec="seconds")
        ai_msg = {"role": "assistant", "content": reply_content, "timestamp": ai_now}
        session_data["messages"].append(ai_msg)
        save_session(session_data)

        return web.json_response({"status": "success", "reply": reply_content})
    except Exception as e:
        return web.json_response({"success": False, "error": str(e)}, status=500)


# ─── 设置 ─────────────────────────────────────────────────

@routes.get("/ai-coder/settings")
async def handle_get_settings(request):
    """获取当前设置（含 default_local_path）"""
    try:
        settings = load_settings()
        settings["default_local_path"] = str(get_default_llm_path())
        return web.json_response(settings)
    except Exception as e:
        return web.json_response({"success": False, "error": str(e)}, status=500)


@routes.post("/ai-coder/settings")
async def handle_save_settings(request):
    """保存设置"""
    try:
        data = await request.json()
        # 移除前端附加的非持久化字段
        data.pop("default_local_path", None)
        save_settings(data)
        return web.json_response({"success": True})
    except Exception as e:
        return web.json_response({"success": False, "error": str(e)}, status=500)


# ─── 文件操作 ─────────────────────────────────────────────

@routes.post("/ai-coder/create-folder")
async def handle_create_folder(request):
    """创建插件脚手架目录"""
    try:
        data = await request.json()
        plugin_name = data.get("plugin_name", "").strip()
        nodes_path = get_custom_nodes_path()
        success, message, path = create_plugin_scaffold(nodes_path, plugin_name)
        return web.json_response({"success": success, "message": message, "path": path})
    except Exception as e:
        return web.json_response({"success": False, "error": str(e)}, status=500)
