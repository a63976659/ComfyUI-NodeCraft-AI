"""代码补全路由模块

提供独立的代码补全端点，与聊天路由分离以便独立限流和维护。
"""
import asyncio
import time

from aiohttp import web

from .文件读写操作 import load_settings
from .日志配置 import 获取日志器
from .路由公共 import (
    RateLimiter,
    _error_response,
    _metrics_collector,
    _检查上传大小,
    llm_client,
    local_model_client,
)

logger = 获取日志器("代码补全路由")


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


def _提取推理token数(model_source):
    """提取最近一次 LLM 调用的实际 token 数（用于监控指标）。"""
    try:
        if model_source != "local" and llm_client is not None:
            _usage = getattr(llm_client, "上次usage", None)
            if isinstance(_usage, dict):
                return int(_usage.get("total_tokens") or _usage.get("completion_tokens") or 0)
    except Exception:
        pass
    return 0


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
            _metrics_collector.record_inference((time.time() - _start) * 1000, _提取推理token数(model_source))
        except Exception:
            pass

        return web.json_response({"status": "success", "completion": completion_text})
    except Exception as e:
        logger.error(f"代码补全请求异常: {e}", exc_info=True)
        response_status = 500
        return web.json_response({"status": "error", "completion": ""})
    finally:
        _metrics_collector.record_request("/ai-coder/code-completion", (time.time() - _start) * 1000, response_status)


def register_代码补全路由(routes):
    """注册代码补全端点"""
    routes.post("/ai-coder/code-completion")(handle_code_completion)
