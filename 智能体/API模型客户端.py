"""API 模型客户端（OpenAI 兼容）

负责 HTTP 调用、SSE 流式输出、致命错误分类、自动续写、性能统计。
JSON 修复函数从 智能体.JSON修复工具 导入。
共享数据类（性能统计）从 智能体.模型客户端 导入。
"""
import asyncio
import json
import re
import sys
import time
from pathlib import Path
from typing import Optional

import aiohttp

_plugin_root = str(Path(__file__).parent.parent.resolve())
if _plugin_root not in sys.path:
    sys.path.insert(0, _plugin_root)
from 后端.文件读写操作 import load_settings
from 后端.日志配置 import 获取日志器
from 智能体.JSON修复工具 import _修复截断JSON, _智能截断修复  # noqa: F401  re-export 兼容
from 智能体.工具结果摘要器 import (
    MAX_FILE_READ_RESULT_CHARS,
    MAX_TOOL_RESULT_CHARS,
    _文件读取类工具,
    已被截断,
    构建轮次上限提示,
)
from 智能体.模型客户端 import 性能统计
from 智能体.模型客户端工具 import (  # noqa: F401  re-export 兼容
    _strip_thinking_stream,
    _tool_log_summary,
    _计算参数哈希,
    _免去重工具集,
    _是否合理重复,
    _处理工具错误,
    _记录文件读取,
    _文件读取配额检查,
)
from 智能体.模型能力注册表 import 模型能力注册表
from 智能体.错误恢复器 import 错误恢复器

logger = 获取日志器("API模型客户端")

# 模型上下文窗口统一由 模型能力注册表 管理（单一数据源）
_model_registry = 模型能力注册表()


def _normalize_tool_content(content) -> str:
    """将 tool 消息的 content 正规化为字符串（防御非字符串返回值）。

    None → ""；str 原样返回；list/dict 尝试 JSON 序列化，失败回退 str()。
    """
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, (list, dict)):
        try:
            return json.dumps(content, ensure_ascii=False)
        except (TypeError, ValueError):
            return str(content)
    return str(content)


# 写入类工具名（纯文本重提/只读续跑的门控与提示语拼接共用）
_写工具名集 = ("write_plugin_file", "edit_file", "batch_edit")

# 修改意图关键词：用户消息命中任一关键词才视为写任务（纯问答/分析不强制重提工具调用）
_写意图关键词 = (
    "修改", "更改", "改为", "改成", "添加", "增加", "新增", "删除", "移除",
    "修复", "调整", "替换", "重命名", "优化", "重构", "实现", "创建", "编写",
    "写一个", "写入", "更新", "翻译", "汉化", "加上", "去掉", "补充",
    "fix", "modify", "change", "update", "implement", "create",
    "refactor", "rename", "delete", "remove", "rewrite",
)


def _解析工具调用信息(tool_calls) -> list:
    """解析 tool_calls 为 {id, name, args} 列表（非流式/流式共用）"""
    _tc_infos = []
    for tc in tool_calls:
        tc_id = tc.get("id", "")
        fn = tc.get("function", {}) or {}
        tool_name = fn.get("name", "")
        args_str = fn.get("arguments", "") or "{}"
        try:
            tool_args = json.loads(args_str) if args_str else {}
        except json.JSONDecodeError:
            tool_args = {}
        _tc_infos.append({"id": tc_id, "name": tool_name, "args": tool_args})
    return _tc_infos


async def _并行执行工具(_async_tasks) -> dict:
    """并行等待所有实际执行的工具结果，返回 {index: 结果}"""
    if _async_tasks:
        _执行结果列表 = await asyncio.gather(*[task for _, task in _async_tasks], return_exceptions=True)
        return {idx: result for (idx, _), result in zip(_async_tasks, _执行结果列表)}
    return {}


async def _响应提示tools不支持(resp) -> bool:
    """读取错误响应文本，判断 400 是否由 tools 参数不支持引起"""
    try:
        err_text_for_check = await resp.text()
    except (aiohttp.ClientError, UnicodeDecodeError) as e:
        logger.debug(f"读取错误响应文本失败: {e}")
        err_text_for_check = ""
    return "tool" in err_text_for_check.lower()


def _提取可用写工具名(tools_payload) -> list:
    """从 tools payload 中提取可用的写入类工具名（兼容 OpenAI function 嵌套格式）。

    可视化 Tab 只下发只读工具时返回空列表，重提/续跑机制据此跳过。
    """
    名单 = []
    for t in tools_payload or []:
        if not isinstance(t, dict):
            continue
        name = ((t.get("function") or {}).get("name") or t.get("name") or "")
        if name in _写工具名集:
            名单.append(name)
    return 名单


def _检测写意图(messages: list) -> bool:
    """检测最近的真实用户消息是否包含修改/写入类意图。

    系统注入的 [系统提醒]/[系统提示] 消息不参与判断；
    「继续」类短消息本身无意图信息，回看上一条真实用户消息。
    纯问答（如「这个插件是做什么的」）不应触发强制重提，避免完整回答被打断。
    """
    真实消息 = []
    for msg in messages:
        if msg.get("role") != "user":
            continue
        content = msg.get("content", "")
        if isinstance(content, list):  # 视觉消息为分段列表，仅取文本部分
            content = " ".join(
                p.get("text", "") for p in content if isinstance(p, dict)
            )
        content = str(content or "").strip()
        # 系统注入的提醒消息与 P0-1 动态上下文块/操作台账不参与意图判断
        if not content or content.startswith(("[系统提醒", "[系统提示", "[本轮参考上下文]", "[操作台账]")):
            continue
        真实消息.append(content)
    if not 真实消息:
        return False
    候选 = [真实消息[-1]]
    if len(真实消息) >= 2 and len(候选[0]) <= 6:
        候选.append(真实消息[-2])
    return any(
        关键词 in 文本.lower() for 文本 in 候选 for 关键词 in _写意图关键词
    )


def _文本是否向用户提问(text: str) -> bool:
    """检测模型回复文本中是否包含对用户的提问（中英双语）。

    模型提问时应当结束回合等待用户真实回复，禁止强制重提/续跑
    （否则注入的系统提醒会把模型推向自问自答后继续执行）。
    只扫描文本尾部 500 字符：对用户的提问几乎总在回复末尾，
    正文中的代码片段/URL 含 "?" 会误报。
    """
    文本 = (text or "").strip()[-500:]
    if not 文本:
        return False
    小写 = 文本.lower()
    # 信号1a：中文显式疑问句式（不依赖标点，部分模型提问不带标点）
    if re.search(
        r"(请问|请确认|请选择|请告知|是否继续|要不要|需不需要|需要我|您想|你想|您希望|你希望)",
        文本,
    ):
        return True
    # 信号1b：英文显式疑问句式（同样不依赖标点）
    if re.search(
        r"\b(would you like|do you want|do you prefer|should i|shall i|please "
        r"(confirm|choose|select|let me know|tell me)|let me know (which|if|whether|what)|"
        r"which (option|one|approach)|please advise)",
        小写,
    ):
        return True
    # 信号2a：中文问号 + 疑问语气词共现（防代码三元运算/URL 中的 ? 单独误报）
    if ("?" in 文本 or "？" in 文本) and re.search(
        r"(吗|呢|哪|什么|如何|怎么|谁|多少)[^。\.\n]{0,10}[?？]", 文本):
        return True
    # 信号2b：问号置于尾部（英文疑问句的标准形态；代码/URL 的 ? 不会出现在回复末尾）
    if re.search(r"[?？][\s”\")）'\]]{0,4}$", 文本):
        return True
    return False


class AICoderClient:
    """OpenAI 兼容 API 客户端（支持续写、重试、JSON修复、流式输出）

    参考 Flying-Translation2.0 企业级实现：
    - 持久化 aiohttp 会话复用
    - 自动重试 2 次（可恢复错误）
    - 致命错误不重试（401/403/402）
    - 检测 finish_reason=length 自动续写
    - 剥离 <thinking> 标签
    - 超时可配置（默认300秒），支持 sock_connect 快速失败
    - 性能监控统计
    - SSE 流式对话（async generator）
    """

    # 致命错误状态码（不重试）
    _致命错误码 = {
        401: "[认证失败] 请检查 API 密钥是否正确",
        403: "[访问被拒绝] API 密钥无权访问该模型或服务",
        402: "[配额耗尽] API 账户余额不足，请到供应商后台充值后重试",
    }

    # 工具调用最大重试次数
    _max_tool_retries = 3
    # 同工具+同文件的最大连续失败次数（跨轮次追踪，防止死循环）
    _max_same_tool_failures = 5
    # 同 (tool_name, error_type) 失败次数阈值，达到后自动创建踩坑记录
    _pitfall_threshold = 2

    # 已知支持多模态（vision）的 API 模型关键词
    _VISION_MODEL_KEYWORDS = (
        "gpt-4o", "gpt-4-turbo", "gpt-4-vision",
        "claude-3", "claude-3.5",
        "gemini-pro-vision", "gemini-1.5", "gemini-2",
        "qwen-vl", "qwen2-vl", "qwen2.5-vl", "qwen3-vl", "qwen3.5-vl",
        "qwen3.8",  # qwen3.8 系列原生多模态
        "glm-4v", "yi-vision",
        "kimi-k3", "kimi-k2.5", "kimi-k2.6", "kimi-k2.7", "vision-preview",
    )

    def __init__(self):
        self._会话 = None
        self._性能 = 性能统计()
        self.当前模型名: Optional[str] = None  # 请求时从 settings 同步，用于 vision 能力检测
        self._错误恢复 = 错误恢复器()
        self.上次usage: Optional[dict] = None  # 最近一次请求的 token 用量信息
        # chars→token 估算系数：中文实测约 0.6、英文代码约 0.3，默认 0.75 偏保守；
        # 每轮用 API 返回的 usage.prompt_tokens 动态校准（EMA 平滑）
        self._token估算系数 = 0.75
        # P0-3 前缀缓存累计统计（供 获取性能报告 输出累计命中率）
        self._缓存累计命中tokens = 0
        self._缓存累计输入tokens = 0
        # 客户端是全局单例，多模型混用时总计会互相稀释，另按模型名分桶便于定位
        self._缓存分模型统计 = {}  # 模型名 → [命中tokens, 输入tokens]

    def _build_api_headers(self, settings: dict) -> dict:
        """构建 OpenAI 兼容 API 请求头（用户自持 API Key 直连）。

        协议约定：
        - Authorization: Bearer {api_key}
        - Content-Type: application/json

        api_key 在 settings.json 中加密存储（_SENSITIVE_KEYS 包含 api_key），
        load_settings() 会自动解密。
        """
        api_key = settings.get("api_key", "")
        return {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
        }

    def _supports_vision(self) -> bool:
        """检测当前 API 模型是否支持视觉/多模态输入"""
        model_name = (self.当前模型名 or "").lower()
        if not model_name:
            return False
        return any(vm in model_name for vm in self._VISION_MODEL_KEYWORDS)

    def _temperature固定(self, model_name: str) -> bool:
        """检测模型的采样参数是否固定不可改
        Kimi k3/k2.x 系列的 temperature/top_p 等参数固定，显式传入其他值会报错，
        此类模型的请求 payload 中应省略 temperature。
        """
        return (model_name or "").lower().startswith("kimi-")

    # ===== 思考模式能力表（各供应商协议不同，按模型名前缀匹配，顺序敏感）=====
    # 开关: None = 无开关（始终思考，传开关参数会报错）
    #       "thinking"        = {"thinking": {"type": "enabled"/"disabled"}}
    #       "enable_thinking" = {"enable_thinking": true/false}（通义千问混合思考，非 OpenAI 标准字段）
    # 深度: 是否支持 reasoning_effort（low/high/max）
    # 回传: 工具循环中是否需原样回传 assistant 消息里的 reasoning_content
    #       （不需要的供应商回传会报 400）
    _思考能力表 = (
        # Kimi K3：始终思考 + 保留式思考始终开启，不应传 thinking；仅顶层 reasoning_effort（默认 max）
        ("kimi-k3", {"开关": None, "深度": True, "回传": True}),
        # Kimi K2.7-code：始终思考，传 disabled 报错；不支持 reasoning_effort
        ("kimi-k2.7", {"开关": None, "深度": False, "回传": True}),
        # Kimi K2.6 / K2.5：默认开启思考，可用 thinking.type 关闭；不支持 reasoning_effort
        ("kimi-k2.6", {"开关": "thinking", "深度": False, "回传": True}),
        ("kimi-k2.5", {"开关": "thinking", "深度": False, "回传": True}),
        # 其余 kimi 系列：保守处理（不传开关/深度参数），但沿用 Kimi 的 reasoning_content 回传要求
        ("kimi-", {"开关": None, "深度": False, "回传": True}),
        # DeepSeek V4 pro/flash：thinking.type 开关（默认 enabled）+ reasoning_effort（默认 high）。
        # 官方明确：携带 tools 的请求，后续请求必须完整回传 reasoning_content，否则报 400
        ("deepseek-v4", {"开关": "thinking", "深度": True, "回传": True}),
        # DeepSeek reasoner（R1 系）：思考不可关闭，且回传 reasoning_content 会报 400
        ("deepseek-reasoner", {"开关": None, "深度": False, "回传": False}),
        # 通义千问 Qwen3 系列（含 Qwen3.8-Max）：混合思考，enable_thinking 开关（百炼侧默认开启）；
        # 多轮对话不需要回传 reasoning_content
        ("qwen3", {"开关": "enable_thinking", "深度": False, "回传": False}),
        ("qwen-plus", {"开关": "enable_thinking", "深度": False, "回传": False}),
        ("qwen-turbo", {"开关": "enable_thinking", "深度": False, "回传": False}),
        ("qwen-flash", {"开关": "enable_thinking", "深度": False, "回传": False}),
    )
    _无思考能力 = {"开关": None, "深度": False, "回传": False}

    def _思考能力(self, model_name: str) -> dict:
        """查询模型的思考模式能力（开关风格 / 是否支持深度 / 是否需回传 reasoning_content）"""
        名 = (model_name or "").lower()
        for 前缀, 能力 in self._思考能力表:
            if 名.startswith(前缀):
                return 能力
        return self._无思考能力

    def _支持思考参数(self, model_name: str) -> bool:
        """工具循环中是否需要原样回传 assistant 消息里的 reasoning_content
        Kimi K系列与 DeepSeek V4 的思考协议要求完整回传，否则 API 报 400；
        其他供应商（通义千问、DeepSeek reasoner 等）回传反而会报错。
        """
        return bool(self._思考能力(model_name).get("回传"))

    def _supports_JSON输出(self, model_name: str) -> bool:
        """查询模型是否支持原生 response_format={\"type\": \"json_object\"}

        沿用 _思考能力表 的前缀匹配风格；未知供应商一律 False，
        避免向不支持的端点传 response_format 导致 400。
        """
        名 = (model_name or "").lower()
        for 前缀, 支持 in self._JSON输出能力表:
            if 名.startswith(前缀):
                return 支持
        return False

    # ===== 原生 JSON Output 能力表（按模型名前缀匹配）=====
    # DeepSeek / Kimi / 智谱 GLM / 通义千问（百炼）均官方支持 json_object；
    # 未知供应商 False（防 400），降级走 JSON修复工具 兜底
    _JSON输出能力表 = (
        ("deepseek-", True),
        ("kimi-", True),
        ("glm-", True),
        ("qwen", True),
    )

    # ===== 前缀续写能力（P1-2：替换"追加 user 提示"的截断续写土办法）=====
    def _前缀续写能力(self, model_name: str):
        """返回供应商官方续写协议：

        - "partial": Kimi（末条 assistant 消息带 partial: True；
          思考模式官方要求带回 reasoning_content）
        - "prefix": DeepSeek（末条 assistant 消息带 prefix: True，需 /beta 端点）
        - None: 无一手文档（千问/GLM 等），保留现有追加 user 提示的土办法，不猜测
        """
        名 = (model_name or "").lower()
        if 名.startswith("kimi-"):
            return "partial"
        if 名.startswith("deepseek-"):
            return "prefix"
        return None

    def _beta端点(self, base_url: str) -> str:
        """将 base_url 的 path 替换为 /beta（DeepSeek prefix 续写与 FIM 需 beta 端点）

        兼容用户填 https://api.deepseek.com 与 https://api.deepseek.com/v1 两种写法。
        """
        from urllib.parse import urlparse
        try:
            parsed = urlparse((base_url or "").rstrip("/"))
            if parsed.scheme and parsed.netloc:
                return f"{parsed.scheme}://{parsed.netloc}/beta"
        except ValueError:
            pass
        return (base_url or "").rstrip("/") + "/beta"

    # ===== FIM（Fill-In-the-Middle）能力表（P1-3）=====
    # 仅 DeepSeek 官方提供 /beta/completions 的 prompt+suffix FIM；
    # Kimi 无 FIM，GLM/Qwen 无一手文档 → 保守保留伪标签方案（不猜测，猜错直接 400）
    _FIM能力表 = (
        ("deepseek-", True),
    )

    def _支持FIM(self, model_name: str) -> bool:
        名 = (model_name or "").lower()
        for 前缀, 支持 in self._FIM能力表:
            if 名.startswith(前缀):
                return 支持
        return False

    def _max_tokens参数名(self, model_name: str) -> str:
        """输出长度参数的字段名
        Kimi K3 官方要求用 max_completion_tokens（默认 131072，最大 1M），
        max_tokens 为旧字段；其他 OpenAI 兼容供应商继续用 max_tokens。
        """
        return "max_completion_tokens" if (model_name or "").lower().startswith("kimi-") else "max_tokens"

    def _校验消息配对(self, messages: list) -> list:
        """确保 assistant(tool_calls) 与 tool 消息配对完整
        如果发现不配对，自动修复（移除孤立的 tool 消息或补充空 tool 响应）
        """
        需要的tool_ids = set()
        已有的tool_ids = set()

        for msg in messages:
            if msg.get("role") == "assistant" and msg.get("tool_calls"):
                for tc in msg["tool_calls"]:
                    tc_id = tc.get("id") or tc.get("function", {}).get("id")
                    if tc_id:
                        需要的tool_ids.add(tc_id)
            elif msg.get("role") == "tool":
                tool_id = msg.get("tool_call_id")
                if tool_id:
                    已有的tool_ids.add(tool_id)

        # 找出缺失的 tool 响应
        缺失ids = 需要的tool_ids - 已有的tool_ids
        if 缺失ids:
            logger.warning(f"[消息配对修复] 发现 {len(缺失ids)} 个缺失的 tool 响应，自动补充")
            for missing_id in 缺失ids:
                messages.append({
                    "role": "tool",
                    "tool_call_id": missing_id,
                    "content": "[系统] 工具响应丢失，已自动补充"
                })

        # 找出孤立的 tool 消息（没有对应的 assistant tool_calls）
        孤立ids = 已有的tool_ids - 需要的tool_ids
        if 孤立ids:
            logger.warning(f"[消息配对修复] 发现 {len(孤立ids)} 个孤立 tool 消息，移除")
            messages[:] = [m for m in messages if not (m.get("role") == "tool" and m.get("tool_call_id") in 孤立ids)]

        return messages

    def _prepare_messages(self, messages: list) -> list:
        """根据模型能力准备最终发送的消息列表

        - 支持 vision 且 消息含 _image_attachments 时，转为 OpenAI Vision multimodal 格式
        - 其它情况剥除 _image_attachments 临时字段（避免传递给 API）
        - _audio_attachments 等其他临时字段同样被剥除（白名单式重建，
          API 链路不支持音频输入，后端已在聊天上下文层提前丢弃并提示）
        """
        支持视觉 = self._supports_vision()
        converted = []
        for msg in messages:
            if not isinstance(msg, dict):
                converted.append(msg)
                continue
            image_atts = msg.get("_image_attachments")
            if 支持视觉 and image_atts and msg.get("role") == "user":
                content_parts = [{"type": "text", "text": msg.get("content", "")}]
                for att in image_atts:
                    data_url = att.get("data") if isinstance(att, dict) else None
                    if data_url:
                        content_parts.append({
                            "type": "image_url",
                            "image_url": {"url": data_url, "detail": "auto"},
                        })
                converted.append({"role": msg["role"], "content": content_parts})
            else:
                # 剥离临时字段，保留标准字段
                role = msg.get("role")
                content = msg.get("content", "")
                # tool 消息内容先正规化为字符串（None/dict/list 等非字符串兜底）
                if role == "tool":
                    content = _normalize_tool_content(content)
                new_msg = {"role": role, "content": content}
                # 保留 assistant 的 tool_calls / reasoning_content（OpenAI 协议必需；
                # 历史工具轮消息被剥掉 tool_calls 会让配对校验误判 tool 消息为孤立并删除）
                if role == "assistant":
                    if "tool_calls" in msg:
                        new_msg["tool_calls"] = msg["tool_calls"]
                    if "reasoning_content" in msg:
                        new_msg["reasoning_content"] = msg["reasoning_content"]
                # 保留 tool 消息的 tool_call_id（OpenAI 协议必需）
                if role == "tool" and "tool_call_id" in msg:
                    new_msg["tool_call_id"] = msg["tool_call_id"]
                # 截断过长的 tool 消息内容（阈值单一来源：工具结果摘要器.MAX_TOOL_RESULT_CHARS，与工具路由层对齐）；
                # 已在工具路由层截断/摘要过的内容直接跳过，避免重复压缩丢信息
                if role == "tool" and len(content) > MAX_TOOL_RESULT_CHARS:
                    from .工具结果摘要器 import 已被截断, 摘要化工具结果
                    if not 已被截断(content):
                        try:
                            new_msg["content"] = 摘要化工具结果("unknown", content, max_chars=MAX_TOOL_RESULT_CHARS)
                        except Exception:
                            new_msg["content"] = content[:MAX_TOOL_RESULT_CHARS] + f"\n...[内容已截断，原始长度 {len(content)} 字符]"
                converted.append(new_msg)
        return converted

    def _get_model_context_limit(self, model_name: str) -> int:
        """获取模型的上下文窗口大小（委托给模型能力注册表）"""
        return _model_registry.获取上下文窗口(model_name)

    def _统计消息字符数(self, messages: list) -> int:
        """统计消息列表的总字符数（content + tool_calls）"""
        total_chars = 0
        for msg in messages:
            content = msg.get("content", "")
            if content:
                total_chars += len(str(content))
            # tool_calls 也占 token
            if "tool_calls" in msg:
                total_chars += len(str(msg["tool_calls"]))
        return total_chars

    def _estimate_tokens(self, messages: list) -> int:
        """估算消息列表的总token数（系数由 usage 动态校准）"""
        return int(self._统计消息字符数(messages) * self._token估算系数)

    def _校准token估算(self, messages: list, usage: dict):
        """用 API 返回的 prompt_tokens 校准 chars→token 估算系数（EMA 平滑）

        避免固定系数高估近一倍导致动态窗口过早压缩/删消息（智能体失忆）。
        prompt_tokens 含 tools schema 等额外开销，校准后系数略偏大，天然留有安全余量。
        """
        try:
            prompt_tokens = int((usage or {}).get("prompt_tokens") or 0)
            chars = self._统计消息字符数(messages)
            if prompt_tokens <= 0 or chars < 2000:
                return  # 样本过小，校准无意义
            实测系数 = max(0.3, min(prompt_tokens / chars, 1.5))
            self._token估算系数 = 0.5 * self._token估算系数 + 0.5 * 实测系数
        except (TypeError, ValueError, ZeroDivisionError):
            pass

    def _解析缓存命中(self, usage: dict) -> tuple:
        """从 usage 中解析前缀缓存命中 tokens，兼容两种供应商字段风格：

        - DeepSeek：usage.prompt_cache_hit_tokens / prompt_cache_miss_tokens
        - Kimi / 智谱 / 百炼：usage.prompt_tokens_details.cached_tokens
          （百炼另有 cache_creation_input_tokens，不计入命中）

        返回 (命中tokens, 总输入tokens)；无法解析时返回 (0, 0)。
        """
        if not usage or not isinstance(usage, dict):
            return 0, 0
        try:
            hit = usage.get("prompt_cache_hit_tokens")
            if hit is not None:
                miss = usage.get("prompt_cache_miss_tokens") or 0
                return int(hit), int(hit) + int(miss)
            prompt_tokens = int(usage.get("prompt_tokens") or 0)
            details = usage.get("prompt_tokens_details") or {}
            cached = details.get("cached_tokens") if isinstance(details, dict) else None
            if cached is not None:
                return int(cached), prompt_tokens
        except (TypeError, ValueError):
            pass
        return 0, 0

    def _记录缓存命中(self, usage: dict, 轮次: int) -> None:
        """P0-3 前缀缓存可观测：解析命中率、累计统计并打日志（本方案的验收标尺）"""
        _命中, _总输入 = self._解析缓存命中(usage)
        if _总输入 <= 0:
            return  # 供应商未返回缓存字段（或解析失败），不输出误导性日志
        self._缓存累计命中tokens += _命中
        self._缓存累计输入tokens += _总输入
        _桶 = self._缓存分模型统计.setdefault(self.当前模型名 or "未知", [0, 0])
        _桶[0] += _命中
        _桶[1] += _总输入
        logger.info(
            f"[前缀缓存] 第{轮次}轮 命中 {_命中}/{_总输入} tokens "
            f"({_命中 / max(_总输入, 1) * 100:.1f}%)"
        )

    def _compress_tool_messages(self, messages: list) -> list:
        """压缩消息列表中的 tool 消息（使用摘要器）

        跨轮次历史工具消息瘦身到 800 字符（刻意设计，保留）；
        历史读取类结果直接折叠为一行（P2-14：内容可随时重读，无需保留正文），
        最后一轮的读取结果不折叠（模型可能正在使用）；
        已截断/摘要过的内容不再重复调用摘要器（避免对摘要再摘要产生噪音），
        直接硬截断并保留原始长度信息。

        P0-2 append-only：压缩结果就地固化（打 _已压缩 标记），后续轮次
        遇到该标记直接跳过重算。保证任一条消息一旦压缩，其文本在后续
        所有请求中字节一致，避免重算导致前缀缓存持续断裂。
        """
        from .工具结果摘要器 import 已被截断, 摘要化工具结果

        # 定位最后一轮工具调用的起点：该轮之后的读取结果不折叠
        _最后轮起点 = -1
        for i, m in enumerate(messages):
            if m.get("role") == "assistant" and "tool_calls" in m:
                _最后轮起点 = i

        for i, msg in enumerate(messages):
            if msg.get("role") != "tool":
                continue
            if msg.get("_已压缩"):
                continue  # 已固化：跳过重算，确保文本跨轮次字节一致
            # 先正规化为字符串（None/dict/list 等非字符串兜底）再判长度
            content = _normalize_tool_content(msg.get("content", ""))
            if len(content) > 800:
                # 从上下文推断 tool_name
                tool_name = self._infer_tool_name(messages, msg)
                if tool_name in _文件读取类工具 and i < _最后轮起点:
                    # P2-14 探索折叠：历史读取结果保留首行定位信息即可
                    首行 = content.split("\n", 1)[0].strip()[:150]
                    compressed = f"{首行}\n[历史读取结果已折叠，如需内容请重新读取该文件]"
                elif 已被截断(content):
                    # 已压缩过的内容直接硬截断，不再重复摘要
                    compressed = content[:800] + f"\n...[内容已截断，原始长度 {len(content)} 字符]"
                else:
                    compressed = 摘要化工具结果(tool_name, content, max_chars=800)
                msg["content"] = compressed
                msg["_已压缩"] = True  # 内部标记，_构建请求payload 发送前会剥除
            else:
                msg["content"] = content
        return messages

    def _infer_tool_name(self, messages: list, tool_msg: dict) -> str:
        """从上下文推断 tool 消息对应的工具名"""
        tool_call_id = tool_msg.get("tool_call_id", "")
        if not tool_call_id:
            return "unknown"
        # 向前查找含有匹配 tool_call_id 的 assistant 消息
        for msg in messages:
            if msg.get("role") == "assistant" and "tool_calls" in msg:
                for tc in msg["tool_calls"]:
                    if tc.get("id") == tool_call_id:
                        return tc.get("function", {}).get("name", "unknown")
        return "unknown"

    def _drop_oldest_tool_pairs(self, messages: list, model_name: str = "", 上下文窗口: int = 0) -> list:
        """删除最早的 tool_calls assistant + tool 消息对（最后兜底，正常不应触发）

        P0-2：删除历史会使前缀从第 0 条起全废且直接失忆，故触发阈值提到
        _limit*0.98，触发时以 error 级别记录模型名与上下文窗口，
        便于发现窗口配置错误（1M 窗口模型正常不应触发）。
        """
        # 找到第一个含 tool_calls 的 assistant 消息
        for i, msg in enumerate(messages):
            if msg.get("role") == "assistant" and "tool_calls" in msg:
                # 找到它后面所有对应的 tool 消息
                end = i + 1
                while end < len(messages) and messages[end].get("role") == "tool":
                    end += 1
                # 删除 i 到 end 之间的所有消息
                logger.error(
                    f"[动态窗口] 删除第 {i}-{end-1} 条消息（最早的工具调用对）"
                    f" | model={model_name or '未知'}, 上下文窗口={上下文窗口}"
                )
                return messages[:i] + messages[end:]
        return messages

    def _更新操作台账(self, messages: list, 已修改文件: set, 已读文件: set, 任务计划: str = "") -> list:
        """P2-12 操作台账：压缩/删除历史消息时注入已完成操作清单，防止模型遗忘重做。

        P0-2 append-only：台账以新的一条 user 消息追加到 messages 末尾
        （末条为 user 时插到它之前，不切断 assistant(tool_calls)→tool 相邻性），
        不再原地改写已发送过的消息；旧台账保留在历史中（内容小，代价可忽略），
        保证前缀稳定不断裂。
        """
        if not 已修改文件 and not 已读文件 and not 任务计划:
            return messages
        行 = ["[操作台账] 历史上下文已压缩，以下操作本次任务中已完成（勿重复执行）："]
        if 已修改文件:
            行.append("已修改文件：" + "、".join(sorted(已修改文件)))
        if 已读文件:
            行.append("已读取文件：" + "、".join(sorted(已读文件)))
        if 任务计划:
            行.append("当前任务计划（继续按此推进）：\n" + 任务计划)
        台账 = "\n".join(行)
        新台账消息 = {"role": "user", "content": 台账}
        if messages and messages[-1].get("role") == "user":
            messages.insert(len(messages) - 1, 新台账消息)
        else:
            messages.append(新台账消息)
        return messages

    def _构建请求payload(self, model_name, messages, max_tokens, temperature,
                        settings, tools_payload, 工具不支持降级, 流式=False, response_format=None):
        """构建 API 请求 payload 并校验消息配对（非流式/流式共用）

        P1-1：response_format 非空且模型在 _JSON输出能力表 内时透传（原生 JSON Output）；
        不支持的模型静默不传，降级走 JSON修复工具 兜底。
        """
        # 发送前校验消息配对（防止 tool_calls 与 tool 响应不匹配导致 API 400 错误）
        self._校验消息配对(messages)
        # 剥除内部标记字段（如 P0-2 的 _已压缩），避免把非协议字段发给 API
        clean_messages = [
            ({k: v for k, v in m.items() if not k.startswith("_")} if isinstance(m, dict) else m)
            for m in messages
        ]
        payload = {
            "model": model_name,
            "messages": clean_messages,
            self._max_tokens参数名(model_name): max_tokens,
        }
        if response_format and self._supports_JSON输出(model_name):
            payload["response_format"] = response_format
        if 流式:
            payload["stream"] = True
            payload["stream_options"] = {"include_usage": True}
        if not self._temperature固定(model_name):
            payload["temperature"] = temperature
        _思考 = self._思考能力(model_name)
        _thinking_mode = str(settings.get("thinking_mode") or "").strip().lower()
        # 思考深度（low/high/max，不传时由服务端取默认值），仅 K3 / DeepSeek V4 支持该参数；
        # 仅当模型真的有思考开关且被显式关闭时才不传深度（该参数只在思考模式下有意义）。
        # K3 这类思考不可关闭的模型（开关=None）不能被配置里残留的 off 静默吞掉 reasoning_effort
        _reasoning_effort = str(settings.get("reasoning_effort") or "").strip()
        _显式关思考 = bool(_思考.get("开关")) and _thinking_mode == "off"
        if _reasoning_effort and _思考.get("深度") and not _显式关思考:
            payload["reasoning_effort"] = _reasoning_effort
        # 思考模式开关（空 = 不传参数走服务端默认；on/off = 显式开关），各供应商字段风格不同
        if _thinking_mode in ("on", "off") and _思考.get("开关"):
            if _思考["开关"] == "thinking":
                payload["thinking"] = {"type": "enabled" if _thinking_mode == "on" else "disabled"}
            else:  # enable_thinking（通义千问混合思考）
                payload["enable_thinking"] = (_thinking_mode == "on")
        if tools_payload and not 工具不支持降级:
            payload["tools"] = tools_payload

        return payload

    def _工具去重检测(self, _tc_infos, _工具执行历史, _已修改文件集, tool_executor, 流式=False,
                    _文件读取计数=None):
        """工具执行成功去重检测（非流式/流式共用，流式时提示/日志更详细）。

        除 args_hash 精确去重外，另按文件路径做累计读取配额检查（坑 98 同源），
        拦截「换行号区间反复重读同一文件」这类 hash 认不出的空转。

        返回 (_async_tasks, _去重结果, _去重提示, _同轮重复, _本轮结果缓存)。
        """
        _async_tasks = []
        _去重结果 = {}  # index -> 复用的结果字符串（None表示需要实际执行）
        _去重提示 = {}  # index -> 去重提示消息（将合并到 tool 响应中）
        _本轮已见 = {}  # 去重键 -> 首个调用的 index（同轮相同参数并行调用去重）
        _同轮重复 = {}  # 重复调用的 index -> 首个调用的 index
        _本轮结果缓存 = {}  # index -> 摘要后的结果（供同轮重复调用复用）
        for idx, info in enumerate(_tc_infos):
            tool_name = info["name"]
            tool_args = info["args"]
            args_hash = _计算参数哈希(tool_name, tool_args)
            去重键 = f"{tool_name}|{args_hash}"

            # 同轮去重：同一轮并行调用中出现相同工具+参数，直接复用首个调用的结果
            if 去重键 in _本轮已见:
                _同轮重复[idx] = _本轮已见[去重键]
                logger.info(f"[工具去重] 同轮重复调用: {tool_name}，复用本轮第{_本轮已见[去重键] + 1}个调用的结果")
                continue
            _本轮已见[去重键] = idx

            if 去重键 in _工具执行历史 and not _是否合理重复(tool_name, tool_args, _已修改文件集):
                历史记录 = _工具执行历史[去重键]
                重复次数 = 历史记录["次数"] + 1
                _工具执行历史[去重键]["次数"] = 重复次数

                if 重复次数 >= 3:
                    # 第3次及以上：不再执行，直接复用之前的结果
                    复用结果 = (
                        f"{历史记录['结果摘要']}\n\n"
                        f"[系统提示] 此操作已执行过{重复次数}次以上"
                        f"（首次在第{历史记录['轮次']}轮），自动复用之前的结果。"
                    )
                    _去重结果[idx] = 复用结果
                    if 流式:
                        logger.info(
                            f"[工具去重] 强制复用: {tool_name} "
                            f"(重复第{重复次数}次, 首次在第{历史记录['轮次']}轮)"
                        )
                    else:
                        logger.info(f"[工具去重] 强制复用: {tool_name} (重复第{重复次数}次)")
                    continue
                elif 重复次数 == 2:
                    # 第2次重复：注入警告提示，仍然执行
                    if 流式:
                        提示消息 = (
                            f"[系统提示] 工具 {tool_name} 使用相同参数"
                            f"在第{历史记录['轮次']}轮已成功执行过，"
                            f"结果为：{历史记录['结果摘要'][:150]}。"
                            f"这是第2次重复调用，请注意避免不必要的重复。"
                        )
                        logger.warning(f"[工具去重] 第2次重复警告: {tool_name} args_hash={args_hash}")
                    else:
                        提示消息 = (
                            f"[系统提示] 工具 {tool_name} 使用相同参数"
                            f"在第{历史记录['轮次']}轮已成功执行过，这是第2次重复调用。"
                        )
                        logger.warning(f"[工具去重] 第2次重复警告: {tool_name}")
                    _去重提示[idx] = 提示消息
                else:
                    # 第1次重复：注入提示，仍然执行
                    if 流式:
                        提示消息 = (
                            f"[系统提示] 工具 {tool_name} 使用相同参数"
                            f"在第{历史记录['轮次']}轮已成功执行过，"
                            f"结果为：{历史记录['结果摘要'][:150]}。"
                            f"如确实需要重新执行请继续，否则请跳过。"
                        )
                        logger.warning(f"[工具去重] 第1次重复提示: {tool_name} args_hash={args_hash}")
                    else:
                        提示消息 = (
                            f"[系统提示] 工具 {tool_name} 使用相同参数"
                            f"在第{历史记录['轮次']}轮已成功执行过，如确实需要重新执行请继续。"
                        )
                        logger.warning(f"[工具去重] 第1次重复提示: {tool_name}")
                    _去重提示[idx] = 提示消息

            # ===== 同文件累计读取配额（忽略行号差异，坑 98 同源）=====
            if _文件读取计数 is not None:
                _处置, _配额提示 = _文件读取配额检查(
                    tool_name, tool_args, _文件读取计数, _已修改文件集
                )
                if _处置 == "拒绝":
                    _去重结果[idx] = _配额提示
                    logger.warning(f"[读取配额] 拦截 {tool_name} {tool_args.get('file_path', '')}")
                    continue
                if _处置 == "警告":
                    _去重提示[idx] = (
                        f"{_去重提示[idx]}\n{_配额提示}" if idx in _去重提示 else _配额提示
                    )
                    logger.warning(f"[读取配额] 警告 {tool_name} {tool_args.get('file_path', '')}")

            # 需要实际执行
            _去重结果[idx] = None
            if 流式:
                logger.info(f"工具调用(流式并行): {tool_name} {_tool_log_summary(tool_name, tool_args)}")
            else:
                logger.info(f"工具调用(并行): {tool_name} args={tool_args}")
            _async_tasks.append((idx, tool_executor(tool_name, tool_args)))
        return _async_tasks, _去重结果, _去重提示, _同轮重复, _本轮结果缓存

    def _装配工具结果(self, target_messages: list, _tc_infos: list, _去重结果: dict,
                    _同轮重复: dict, _本轮结果缓存: dict, _执行结果映射: dict,
                    _去重提示: dict, _工具重试计数: dict, _工具名重试计数: dict,
                    _工具错误类型计数: dict, _工具执行历史: dict,
                    _已修改文件集: set, _进度修改文件: set, _进度工具统计: dict,
                    _已读文件清单: set, _任务计划容器: dict, 工具轮次: int,
                    _文件读取计数: dict = None):
        """P2-11 共享装配：按顺序装配工具执行结果并追加 tool 消息（非流式/流式共用）。

        含错误恢复、重复失败熔断检测、执行历史记录、写后提示、结果摘要化。
        返回 (熔断中断, 熔断工具名)。
        """
        from .工具结果摘要器 import 摘要化工具结果

        _熔断中断 = False
        _熔断工具名 = ""
        for idx, info in enumerate(_tc_infos):
            if _去重结果.get(idx) is not None:
                # 复用之前的结果（第3次及以上重复）
                result_str = _去重结果[idx]
            elif idx in _同轮重复:
                # 同轮并行重复调用 → 复用首个调用的结果
                result_str = (
                    f"[同轮去重] 与本轮第{_同轮重复[idx] + 1}个调用参数相同，复用其结果。\n"
                    f"{_本轮结果缓存.get(_同轮重复[idx], '')}"
                )
            elif idx in _执行结果映射:
                result = _执行结果映射[idx]
                if isinstance(result, Exception):
                    error_msg = f"{type(result).__name__}: {result}"
                    result_str = _处理工具错误(
                        self._错误恢复, error_msg, info,
                        _工具重试计数, _工具名重试计数, self._max_tool_retries,
                        _工具错误类型计数
                    )
                    logger.error(f"[工具执行] {info['name']} 异常: {error_msg}")
                    if "已连续失败" in result_str:
                        _熔断中断 = True
                        _熔断工具名 = info['name']
                else:
                    result_str = str(result)
                    if result_str.startswith("❌"):
                        result_str = _处理工具错误(
                            self._错误恢复, result_str, info,
                            _工具重试计数, _工具名重试计数, self._max_tool_retries,
                            _工具错误类型计数
                        )
                        if "最终失败" in result_str:
                            logger.error(f"[工具执行] {info['name']} 最终失败: {result_str[:200]}")
                        if "已连续失败" in result_str:
                            _熔断中断 = True
                            _熔断工具名 = info['name']
                    else:
                        # 成功执行 → 重置连续失败计数并记录到执行历史
                        tool_name = info["name"]
                        tool_args = info["args"]
                        _工具名重试计数.pop(f"{tool_name}|{tool_args.get('file_path', '')}", None)
                        _进度工具统计[tool_name] = _进度工具统计.get(tool_name, 0) + 1
                        args_hash = _计算参数哈希(tool_name, tool_args)
                        去重键 = f"{tool_name}|{args_hash}"
                        _工具执行历史[去重键] = {
                            "轮次": 工具轮次 + 1,
                            "结果摘要": result_str[:1500],
                            "次数": _工具执行历史.get(去重键, {}).get("次数", 0),
                        }
                        # update_plan 成功 → 保存最新计划文本（操作台账保活用）
                        if tool_name == "update_plan":
                            _任务计划容器["文本"] = result_str
                        # 写入/编辑操作成功 → 更新已修改文件集
                        if tool_name in ("write_plugin_file", "edit_file", "batch_edit"):
                            file_path = tool_args.get("file_path", "")
                            if file_path:
                                _已修改文件集.add(file_path)
                                _进度修改文件.add(file_path)
                            # batch_edit 可能修改多个文件
                            if tool_name == "batch_edit":
                                for op in tool_args.get("operations", []):
                                    fp = op.get("file_path", "")
                                    if fp:
                                        _已修改文件集.add(fp)
                                        _进度修改文件.add(fp)
                            # 写入成功提示：引导模型不要回读验证（写入结果已含语法检查结论）
                            result_str += (
                                "\n[系统提示] 文件改动已完整写入磁盘，"
                                "无需再调用 read_plugin_file 重新读取验证。"
                            )
                        # read 成功后，从已修改文件集中移除该文件（下次再读才算重复），
                        # 并记入已读清单（P2-12 操作台账用）
                        if tool_name == "read_plugin_file":
                            file_path = tool_args.get("file_path", "")
                            _已修改文件集.discard(file_path)
                            if file_path:
                                _已读文件清单.add(file_path)
                            # 按路径累计读取次数与已读区间（累计读取配额控制用）
                            if _文件读取计数 is not None:
                                _记录文件读取(_文件读取计数, file_path, tool_args)
            else:
                result_str = "[错误] 工具执行结果丢失"

            # 工具结果摘要化（含时序标记）：文件读取类结果放宽阈值，避免压成纯结构摘要
            # 迫使模型分页反复重读；已被路由层截断的读取结果直接透传，不做二次压缩
            if idx in _同轮重复:
                pass  # 同轮去重复用的是已摘要内容，无需再压缩
            elif info["name"] in _文件读取类工具:
                if not 已被截断(result_str):
                    result_str = 摘要化工具结果(info["name"], result_str, call_index=idx + 1,
                                          max_chars=MAX_FILE_READ_RESULT_CHARS)
            else:
                result_str = 摘要化工具结果(info["name"], result_str, call_index=idx + 1, max_chars=1500)
            _本轮结果缓存[idx] = result_str
            # 去重提示合并到 tool 响应内容中（不能作为独立 user 消息插入，否则破坏 tool_calls 配对）
            if idx in _去重提示:
                result_str = f"[去重提示] {_去重提示[idx]}\n\n{result_str}"
            target_messages.append({
                "role": "tool",
                "tool_call_id": info["id"],
                "content": result_str,
            })
        return _熔断中断, _熔断工具名

    async def _获取会话(self):
        """获取或创建持久的 aiohttp 会话"""
        if self._会话 is None or self._会话.closed:
            self._会话 = aiohttp.ClientSession()
        return self._会话

    async def generate_response(self, system_prompt, messages_history, tools=None, tool_executor=None,
                                response_format=None):
        """调用 LLM API 生成回复（支持 OpenAI Function Calling 工具调用循环）

        特性：
        - 致命错误不重试（401/403/402）
        - 可恢复错误自动重试 2 次（5xx、网络错误）
        - 检测 finish_reason=length 自动续写
        - 检测 finish_reason=tool_calls 自动执行工具并继续对话（最多 5 轮）
        - 剥离 <thinking> 标签
        - 超时可配置（默认300秒），支持 sock_connect 快速失败
        - 性能统计

        Args:
            tools: OpenAI 风格的工具描述列表（function 字典数组），传 None 时行为与原版一致
            tool_executor: async 回调 (tool_name, tool_args) -> str，用于执行工具
            response_format: P1-1 原生 JSON Output（如 {"type": "json_object"}），
                仅对 _JSON输出能力表 内的模型透传，其余模型静默忽略
        """
        开始时间 = time.time()
        settings = load_settings()
        self.上次usage = None  # 重置 token 用量信息（监控指标依赖此字段）

        model_name = settings.get("model_name", "")
        temperature = settings.get("temperature", 0.2)
        max_tokens = settings.get("max_tokens", 4096)

        # 同步当前模型名用于 vision 能力检测
        self.当前模型名 = model_name

        # API 直连模式：用户自持 API Key，向供应商兼容端点发送 OpenAI 格式请求
        base_url = settings.get("base_url", "").rstrip("/")
        api_key = settings.get("api_key", "")
        if not api_key:
            return "[错误] 未配置 API Key，请到设置 > 模型 中填写"
        if not base_url:
            return "[错误] 未配置 Base URL，请到设置 > 模型 中填写"
        if not model_name:
            return "[错误] 未配置模型名，请到设置 > 模型 中填写"

        # OpenAI 兼容认证：Authorization: Bearer {api_key}
        headers = self._build_api_headers(settings)

        # 根据模型能力转换消息格式（vision 模型传递图片，其他模型剔除临时字段）
        prepared_history = self._prepare_messages(messages_history)
        complete_messages = [
            {"role": "system", "content": system_prompt},
            *prepared_history
        ]

        url = f"{base_url}/chat/completions"
        会话 = await self._获取会话()

        # OpenAI tools 格式封装
        tools_payload = None
        if tools:
            try:
                tools_payload = [{"type": "function", "function": tool} for tool in tools]
            except (TypeError, KeyError, ValueError) as e:
                logger.warning(f"tools 参数转换失败，降级为普通对话: {e}")
                tools_payload = None

        # 工具调用最大轮次可配置（默认25；-1/0/"无限" 表示不限制，兜底9999防死循环）
        _配置轮次 = settings.get("max_tool_rounds", 25)
        try:
            最大工具循环次数 = 9999 if _配置轮次 in (-1, 0, "无限", "unlimited") else int(_配置轮次)
        except (TypeError, ValueError):
            最大工具循环次数 = 25
        工具不支持降级 = False
        最终内容 = ""
        _工具重试计数 = {}  # tool_call_id -> 重试次数
        _工具名重试计数 = {}  # (tool_name|file_path) -> 跨轮次失败次数
        _工具错误类型计数 = {}  # (tool_name|error_type) -> 失败次数（踩坑记录触发用）
        _工具执行历史 = {}  # key: "tool_name|args_hash" → value: {"轮次": int, "结果摘要": str, "次数": int}
        _已修改文件集 = set()  # write/edit 成功后将文件路径加入
        _进度工具统计 = {}     # 工具名 → 成功执行次数（轮次上限时的进度摘要用）
        _进度修改文件 = set()  # 只增不减（区别于 _已修改文件集的 read 后 discard 去重语义）
        _已读文件清单 = set()  # read 成功记录，只增不减（P2-12 操作台账用）
        _文件读取计数 = {}     # 文件路径 → {"次数", "区间"}（累计读取配额用）
        _任务计划容器 = {}     # update_plan 最新清单文本（台账保活用）
        _熔断中断 = False      # 同一工具连续失败达上限时置位，中断循环
        _熔断工具名 = ""
        _连续空转轮数 = 0     # 本轮工具调用全部被强制复用（无实际执行）的连续轮数

        for 工具轮次 in range(最大工具循环次数):
            # 动态窗口控制（仅压缩工具结果文本，不删除工具消息对，避免智能体遗忘已执行操作）
            _estimated = self._estimate_tokens(complete_messages)
            _limit = self._get_model_context_limit(model_name)

            _发生压缩 = False
            if _estimated > _limit * 0.95:
                complete_messages = self._compress_tool_messages(complete_messages)
                logger.warning(f"[动态窗口] token预估({_estimated})接近上限({_limit})，已压缩工具消息文本")
                _estimated = self._estimate_tokens(complete_messages)
                _发生压缩 = True

            # 兜底：压缩后仍超限 → 逐对删除最早的工具消息对，防止上下文无限膨胀
            # P0-2：阈值 0.95→0.98，删除历史会废掉全部前缀缓存并直接失忆，尽量晚触发
            _drop次数 = 0
            while _estimated > _limit * 0.98 and _drop次数 < 20:
                _新消息 = self._drop_oldest_tool_pairs(complete_messages, model_name, _limit)
                if len(_新消息) == len(complete_messages):
                    break
                complete_messages = _新消息
                _estimated = self._estimate_tokens(complete_messages)
                _drop次数 += 1
            if _drop次数:
                logger.error(
                    f"[动态窗口] 压缩后仍超限，已删除最早的 {_drop次数} 组工具消息对，当前预估 {_estimated}"
                    f" | model={model_name}, 上下文窗口={_limit}"
                )

            # P2-12：压缩/删除发生后注入操作台账，防止模型遗忘已完成操作重复执行
            if _发生压缩 or _drop次数:
                complete_messages = self._更新操作台账(
                    complete_messages, _进度修改文件, _已读文件清单,
                    _任务计划容器.get("文本", "")
                )

            payload = self._构建请求payload(
                model_name, complete_messages, max_tokens, temperature,
                settings, tools_payload, 工具不支持降级,
                response_format=response_format,
            )

            响应数据 = None
            # 内部重试 2 次（仅可恢复错误）
            _total = max(settings.get("chat_timeout", 300000) / 1000, 60)
            for retry in range(2):
                try:
                    async with 会话.post(url, json=payload, headers=headers,
                                        timeout=aiohttp.ClientTimeout(
                                            total=_total,
                                            sock_connect=15,
                                        )) as resp:
                        # --- P0: 致命错误分类 ---
                        if resp.status in self._致命错误码:
                            self._性能.记录请求(time.time() - 开始时间, False)
                            return self._致命错误码[resp.status]

                        if resp.status == 200:
                            # 非流式获取完整响应 JSON
                            响应数据 = await resp.json()
                            break

                        # API 不支持 tools 参数 → 降级为普通对话重试
                        if resp.status == 400 and tools_payload and not 工具不支持降级:
                            if await _响应提示tools不支持(resp):
                                logger.warning("API 不支持 tools 参数，降级为普通对话")
                                工具不支持降级 = True
                                break

                        # --- 429 速率限制：指数退避 + jitter 重试 ---
                        if resp.status == 429 and retry < 1:
                            wait = 2 ** retry
                            logger.warning(f"速率限制(429)，等待 {wait:.1f}s 后重试 ({retry + 1}/2)...")
                            try:
                                resp.release()
                            except Exception:
                                pass
                            await asyncio.sleep(wait)
                            continue

                        # --- P0: 可恢复错误（5xx）走重试 ---
                        if resp.status >= 500 and retry < 1:
                            logger.warning(f"服务器错误 {resp.status}，重试...")
                            await asyncio.sleep(1)
                            continue
                        else:
                            error_text = await resp.text()
                            self._性能.记录请求(time.time() - 开始时间, False)
                            if resp.status == 429:
                                return "[请求过于频繁] API 速率限制已达上限，请稍后重试"
                            return f"[API 错误 {resp.status}]: {error_text[:300]}"

                except (aiohttp.ClientError, asyncio.TimeoutError) as e:
                    # 可恢复的网络错误，重试
                    if retry < 1:
                        logger.warning(f"网络错误，重试: {e}")
                        await asyncio.sleep(1)
                        continue
                    self._性能.记录请求(time.time() - 开始时间, False)
                    return f"[连接错误]: {str(e)}"
                except (json.JSONDecodeError, KeyError, TypeError, ValueError) as e:
                    self._性能.记录请求(time.time() - 开始时间, False)
                    return f"[未知错误]: {str(e)}"

            # 触发了 tools 降级 → 重新进入本轮（不带 tools）
            if 工具不支持降级 and 响应数据 is None:
                continue

            if 响应数据 is None:
                self._性能.记录请求(time.time() - 开始时间, False)
                return "[错误] 多次重试均失败"

            # 保存 usage 信息（非流式响应），并用 prompt_tokens 校准 token 估算系数
            _usage = 响应数据.get("usage")
            if _usage:
                self.上次usage = _usage
                self._校准token估算(complete_messages, _usage)
                self._记录缓存命中(_usage, 工具轮次 + 1)

            choice = 响应数据["choices"][0]
            finish_reason = choice.get("finish_reason", "stop")
            message = choice.get("message", {}) or {}
            content = message.get("content") or ""
            tool_calls = message.get("tool_calls") or []

            # ===== 工具调用分支 =====
            if finish_reason == "tool_calls" and tool_executor and tool_calls:
                # 保留 assistant 工具调用消息到上下文（OpenAI 协议要求）
                _assistant_msg = {
                    "role": "assistant",
                    "content": content,
                    "tool_calls": tool_calls,
                }
                # Kimi/DeepSeek 官方要求：工具循环原样回传完整 assistant 消息（含 reasoning_content）
                if message.get("reasoning_content") and self._支持思考参数(model_name):
                    _assistant_msg["reasoning_content"] = message["reasoning_content"]
                complete_messages.append(_assistant_msg)
                # 解析工具调用信息
                _tc_infos = _解析工具调用信息(tool_calls)

                # ===== 工具执行成功去重检测 =====
                (_async_tasks, _去重结果, _去重提示,
                 _同轮重复, _本轮结果缓存) = self._工具去重检测(
                    _tc_infos, _工具执行历史, _已修改文件集, tool_executor,
                    _文件读取计数=_文件读取计数,
                )

                # 并行等待所有实际执行的结果
                _执行结果映射 = await _并行执行工具(_async_tasks)

                # 按顺序追加结果（P2-11 共享装配：含错误恢复 + 重复失败检测 + 摘要化）
                _熔断中断, _熔断工具名 = self._装配工具结果(
                    complete_messages, _tc_infos, _去重结果, _同轮重复, _本轮结果缓存,
                    _执行结果映射, _去重提示, _工具重试计数, _工具名重试计数,
                    _工具错误类型计数, _工具执行历史, _已修改文件集, _进度修改文件,
                    _进度工具统计, _已读文件清单, _任务计划容器, 工具轮次,
                    _文件读取计数
                )
                # 熔断：同一工具连续失败达上限 → 中断循环，避免无限重试烧 token
                if _熔断中断:
                    logger.warning(f"[熔断] 工具 {_熔断工具名} 连续失败达上限，中断工具循环")
                    self._性能.记录请求(time.time() - 开始时间, True)
                    return 最终内容 or (
                        f"[已中断] 工具 {_熔断工具名} 已连续失败多次，"
                        f"已停止自动重试以避免死循环。请检查失败原因后再继续对话。"
                    )
                # 空转熔断：本轮所有工具调用全被"强制复用"（零实际执行）视为空转，
                # 连续 3 轮空转说明模型在重复打转（如反复重读同一文件），中断避免烧 token（坑 98）
                if _tc_infos and not _async_tasks:
                    _连续空转轮数 += 1
                else:
                    _连续空转轮数 = 0
                if _连续空转轮数 >= 3:
                    logger.warning(f"[熔断] 连续 {_连续空转轮数} 轮工具调用全部被强制复用（无实际进展），中断工具循环")
                    self._性能.记录请求(time.time() - 开始时间, True)
                    return 最终内容 or (
                        "[已中断] 模型连续多轮重复调用相同工具且无实际进展，已停止循环。"
                        "请补充更明确的指示后再继续对话。"
                    )
                # 进入下一轮（携带工具结果再次请求模型）
                continue

            # ===== 普通文本分支 =====
            # 剥离 thinking 标签（Qwen3.5 变体 + 标准，与 generate_completion/流式方法一致）
            content = re.sub(r'lld[\s\S]*?ullets', '', content)
            content = re.sub(r'<thinking>[\s\S]*?</thinking>', '', content).strip()

            # P0: 截断续写（P1-2：优先官方 partial/prefix 前缀续写协议）
            if finish_reason == "length":
                logger.info("API 输出被截断，尝试续写...")
                try:
                    _续写风格 = self._前缀续写能力(model_name)
                    if _续写风格:
                        # 官方协议：末条 assistant 消息带 partial/prefix 标记承载已输出内容，
                        # 模型从断点自然接续，无重复文本；不再追加"你被截断了"的 user 消息
                        _前缀消息 = {"role": "assistant", "content": content, _续写风格: True}
                        # Kimi 思考模式官方要求续写时带回 reasoning_content
                        if _续写风格 == "partial" and message.get("reasoning_content"):
                            _前缀消息["reasoning_content"] = message["reasoning_content"]
                        续写messages = complete_messages + [_前缀消息]
                        # DeepSeek prefix 续写需 /beta 端点；Kimi partial 走标准端点
                        _续写url = (f"{self._beta端点(base_url)}/chat/completions"
                                    if _续写风格 == "prefix" else url)
                    else:
                        # 无官方协议的供应商（千问/GLM 等）：保留现有土办法，行为不变
                        续写messages = complete_messages.copy()
                        续写messages.append({"role": "assistant", "content": content})
                        续写messages.append({
                            "role": "user",
                            "content": "你的输出被截断了，请从中断处继续完成，不要重复已输出的部分。"
                        })
                        _续写url = url
                    续写payload = {
                        "model": model_name,
                        # 续写 payload 手工组装不经 _构建请求payload，需同样剥除内部标记
                        # （如压缩固化的 _已压缩），避免非协议字段发给 API
                        "messages": [
                            ({k: v for k, v in m.items() if not k.startswith("_")}
                             if isinstance(m, dict) else m)
                            for m in 续写messages
                        ],
                        # 官方明确续写要给足 max_tokens，否则再次截断（原 //2 减半是错的）
                        self._max_tokens参数名(model_name): max_tokens
                    }
                    if not self._temperature固定(model_name):
                        续写payload["temperature"] = temperature
                    # JSON 任务被截断时，续写段同样要受 json_object 约束，
                    # 否则续写文本脱离 JSON 语法，拼接后只能靠修复工具兜底
                    if response_format and self._supports_JSON输出(model_name):
                        续写payload["response_format"] = response_format
                    async with 会话.post(_续写url, json=续写payload, headers=headers,
                                        timeout=aiohttp.ClientTimeout(
                                            total=_total,
                                            sock_connect=15,
                                        )) as 续写resp:
                        if 续写resp.status == 200:
                            续写data = await 续写resp.json()
                            续写content = 续写data["choices"][0]["message"]["content"]
                            续写content = re.sub(r'lld[\s\S]*?ullets', '', 续写content)
                            续写content = re.sub(r'<thinking>[\s\S]*?</thinking>', '', 续写content).strip()
                            content = content + 续写content
                        else:
                            _续写错误 = await 续写resp.text()
                            logger.warning(f"续写请求返回 {续写resp.status}: {_续写错误[:200]}")
                except (aiohttp.ClientError, asyncio.TimeoutError, json.JSONDecodeError, KeyError) as e:
                    logger.exception(f"续写失败: {e}")

            self._性能.记录请求(time.time() - 开始时间, True)
            return content

        # 工具调用循环达到上限：附带进度摘要与续接引导（与流式路径文案统一）
        logger.warning(f"工具调用循环达到上限（{最大工具循环次数} 轮）")
        self._性能.记录请求(time.time() - 开始时间, True)
        上限提示 = 构建轮次上限提示(最大工具循环次数, _进度工具统计, _进度修改文件)
        return (最终内容 + 上限提示) if 最终内容 else 上限提示.lstrip()

    async def generate_completion(self, system_prompt, prefix, suffix=None, settings=None):
        """代码补全专用方法。
        :param system_prompt: 补全系统提示词
        :param prefix: 光标前的代码内容
        :param suffix: 光标后的代码内容（可选）
        :param settings: 设置字典，含 base_url, api_key, model_name 等
        :return: 补全文本字符串
        """
        try:
            if settings is None:
                settings = load_settings()

            model_name = settings.get("model_name", "")

            # 同步当前模型名用于 vision 能力检测
            self.当前模型名 = model_name
            self.上次usage = None  # 重置 token 用量信息（监控指标依赖此字段）

            # API 直连模式：用户自持 API Key，向供应商兼容端点发送 OpenAI 格式补全请求
            base_url = settings.get("base_url", "").rstrip("/")
            api_key = settings.get("api_key", "")
            if not api_key:
                return ""
            if not base_url:
                return ""
            if not model_name:
                return ""

            headers = self._build_api_headers(settings)

            会话 = await self._获取会话()

            # 保持代码补全的快速响应特性：使用 completion_timeout（毫秒→秒），默认8秒
            _completion_timeout_ms = settings.get("completion_timeout", 8000)
            _total_timeout = max(_completion_timeout_ms / 1000, 3)  # 至少3秒
            _completion_timeout = aiohttp.ClientTimeout(total=_total_timeout, sock_connect=10)

            # P1-3：DeepSeek 走官方 FIM（/beta/completions 的 prompt+suffix，取 choices[0].text）；
            # FIM 最大补全长度 4K（官方限制），当前 100 tokens 无冲突。
            # 其余供应商（Kimi 无 FIM，GLM/Qwen 未确认）按 _FIM能力表 分流，
            # 继续走现有 <|prefix|>/<|cursor|> 伪标签方案，行为不变。
            if self._支持FIM(model_name):
                fim_url = f"{self._beta端点(base_url)}/completions"  # 注意：非 chat/completions
                fim_payload = {
                    "model": model_name,
                    "prompt": prefix,
                    "suffix": suffix or "",
                    "max_tokens": 100,
                }
                if not self._temperature固定(model_name):
                    fim_payload["temperature"] = 0.1
                logger.debug(f"[代码补全] DeepSeek FIM 请求: {fim_url}")
                async with 会话.post(fim_url, json=fim_payload, headers=headers,
                                    timeout=_completion_timeout) as resp:
                    if resp.status == 200:
                        响应数据 = await resp.json()
                        self.上次usage = 响应数据.get("usage")  # 保存 token 用量（监控指标依赖此字段）
                        content = 响应数据["choices"][0].get("text") or ""  # FIM 取 text 而非 message.content
                        content = re.sub(r'lld[\s\S]*?ullets', '', content)
                        content = re.sub(r'<thinking>[\s\S]*?</thinking>', '', content).strip()
                        return content
                    error_text = await resp.text()
                    logger.warning(f"[代码补全] FIM API 返回状态码 {resp.status}: {error_text[:300]}")
                    return ""

            # 构建补全请求消息（伪标签方案）
            if suffix:
                user_content = f"<|prefix|>\n{prefix}\n<|cursor|>\n<|suffix|>\n{suffix}"
            else:
                user_content = f"<|prefix|>\n{prefix}\n<|cursor|>"

            messages = [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_content},
            ]

            payload = {
                "model": model_name,
                "messages": messages,
                self._max_tokens参数名(model_name): 100,
            }
            if not self._temperature固定(model_name):
                payload["temperature"] = 0.1

            url = f"{base_url}/chat/completions"

            async with 会话.post(url, json=payload, headers=headers,
                                timeout=_completion_timeout) as resp:
                if resp.status == 200:
                    响应数据 = await resp.json()
                    self.上次usage = 响应数据.get("usage")  # 保存 token 用量（监控指标依赖此字段）
                    content = 响应数据["choices"][0]["message"]["content"] or ""
                    # 剥离 lld 标签（Qwen3.5 变体）
                    content = re.sub(r'lld[\s\S]*?ullets', '', content)
                    # 剥离 thinking 标签
                    content = re.sub(r'<thinking>[\s\S]*?</thinking>', '', content).strip()
                    return content
                else:
                    error_text = await resp.text()
                    logger.warning(f"[代码补全] API 返回状态码 {resp.status}: {error_text[:300]}")
                    return ""

        except (aiohttp.ClientError, asyncio.TimeoutError, json.JSONDecodeError,
                KeyError, TypeError, ValueError, IndexError) as e:
            logger.warning(f"[代码补全] 请求失败，返回空字符串: {type(e).__name__}: {e}")
            return ""
        except Exception as e:
            logger.exception(f"[代码补全] 未预期异常: {e}")
            return ""

    async def 流式对话(self, messages: list, settings: dict, tools=None, tool_executor=None,
                     response_format=None):
        """流式对话 - yield 每个 token chunk

        Args:
            messages: 完整的消息列表（含 system prompt）
            settings: 设置字典
            tools: OpenAI 风格的工具描述列表，传 None 时与原版行为一致
            tool_executor: async 回调 (tool_name, tool_args) -> str
            response_format: P1-1 原生 JSON Output，仅能力表内模型透传
        """
        model_source = settings.get("model_source", "api")
        if model_source == "local":
            # 本地模式不在此客户端处理，由外部分发
            yield "[错误] API客户端不支持本地模式流式对话"
        else:
            async for chunk in self._api_流式对话(messages, settings, tools=tools,
                                              tool_executor=tool_executor,
                                              response_format=response_format):
                yield chunk

    async def _api_流式对话(self, messages: list, settings: dict, tools=None, tool_executor=None,
                          response_format=None):
        """API 模式流式对话 - OpenAI 兼容 SSE 格式

        在请求 body 中设置 stream: true，逐行解析 SSE data。
        支持 Function Calling：累积 delta 中的 tool_calls 片段，
        在 finish_reason=tool_calls 时执行工具并发起新的流式请求继续生成。
        """
        logger.debug(
            f"[流式对话] 准备发送, messages数={len(messages)}, "
            f"预估字符数={sum(len(str(m.get('content', ''))) for m in messages)}"
        )
        self.上次usage = None  # 重置 token 用量信息（监控指标依赖此字段）

        model_name = settings.get("model_name", "")
        temperature = settings.get("temperature", 0.2)
        max_tokens = settings.get("max_tokens", 4096)

        # 工具模式需要更大的 token 预算（thinking推理 + 工具调用JSON + 正文输出）
        # DeepSeek等模型的 thinking token 会占用 max_tokens，4096 经常不够
        if tools and max_tokens < 8192:
            max_tokens = 8192

        # 同步当前模型名用于 vision 能力检测
        self.当前模型名 = model_name

        # API 直连模式：用户自持 API Key，向供应商兼容端点发送 OpenAI 格式流式请求
        base_url = settings.get("base_url", "").rstrip("/")
        api_key = settings.get("api_key", "")
        if not api_key:
            yield "[错误] 未配置 API Key，请到设置 > 模型 中填写"
            return
        if not base_url:
            yield "[错误] 未配置 Base URL，请到设置 > 模型 中填写"
            return
        if not model_name:
            yield "[错误] 未配置模型名，请到设置 > 模型 中填写"
            return

        headers = self._build_api_headers(settings)

        # 根据模型能力转换消息格式（vision 模型传递图片，其他模型剥除临时字段）
        prepared_messages = self._prepare_messages(messages)

        # OpenAI tools 格式封装
        tools_payload = None
        if tools:
            try:
                tools_payload = [{"type": "function", "function": tool} for tool in tools]
            except (TypeError, KeyError, ValueError) as e:
                logger.warning(f"tools 参数转换失败，降级为普通流式: {e}")
                tools_payload = None

        url = f"{base_url}/chat/completions"
        会话 = await self._获取会话()

        # 工具调用最大轮次可配置（默认25；-1/0/"无限" 表示不限制，兜底9999防死循环）
        _配置轮次 = settings.get("max_tool_rounds", 25)
        try:
            最大工具循环次数 = 9999 if _配置轮次 in (-1, 0, "无限", "unlimited") else int(_配置轮次)
        except (TypeError, ValueError):
            最大工具循环次数 = 25
        工具不支持降级 = False
        _工具重试计数 = {}  # tool_call_id -> 重试次数
        _工具名重试计数 = {}  # (tool_name|file_path) -> 跨轮次失败次数
        _工具错误类型计数 = {}  # (tool_name|error_type) -> 失败次数（踩坑记录触发用）
        _工具执行历史 = {}  # key: "tool_name|args_hash" → value: {"轮次": int, "结果摘要": str, "次数": int}
        _已修改文件集 = set()  # write/edit 成功后将文件路径加入，用于排除合理的 read 重复
        _进度工具统计 = {}     # 工具名 → 成功执行次数（轮次上限时的进度摘要用）
        _进度修改文件 = set()  # 只增不减（区别于 _已修改文件集的 read 后 discard 去重语义）
        _已读文件清单 = set()  # read 成功记录，只增不减（P2-12 操作台账用）
        _文件读取计数 = {}     # 文件路径 → {"次数", "区间"}（累计读取配额用）
        _任务计划容器 = {}     # update_plan 最新清单文本（台账保活用）
        _截断续写次数 = 0      # token截断重试（finish_reason=="length"）
        _纯文本重提次数 = 0    # 模型输出文字不调工具时的强制重提
        _只读续跑次数 = 0      # 首轮只读不写时的续跑
        _429连续重试 = 0       # 429 速率限制连续重试次数（独立于工具轮次，最多 3 次）
        _熔断中断 = False      # 同一工具连续失败达上限时置位，中断循环
        _熔断工具名 = ""
        _连续空转轮数 = 0     # 本轮工具调用全部被强制复用（无实际执行）的连续轮数
        _曾输出正文 = False   # 跨轮标记：此前轮次是否输出过叙述文本（轮间分隔用）
        _上次重提文本 = ""    # 纯文本重提后上一次的输出（重复总结检测用）
        _续写走beta = False   # P1-2：DeepSeek prefix 续写时下一轮请求切到 /beta 端点
        _续写禁用tools = False  # P1-2：DeepSeek prefix 与 tools 官方互斥，该轮请求不带 tools

        for 工具轮次 in range(最大工具循环次数):
            # 动态窗口控制
            _estimated = self._estimate_tokens(prepared_messages)
            _limit = self._get_model_context_limit(model_name)

            # 注：提案1/3/4（剥离工具指南/主动压缩旧轮次/阶段压缩）已删除：
            # 均会改写已发送的历史消息，与 append-only 前缀缓存稳定化直接冲突

            _发生压缩 = False
            if _estimated > _limit * 0.95:
                prepared_messages = self._compress_tool_messages(prepared_messages)
                logger.warning(f"[动态窗口] token预估({_estimated})接近上限({_limit})，已压缩工具消息文本")
                _estimated = self._estimate_tokens(prepared_messages)
                _发生压缩 = True

            # 兜底：压缩后仍超限 → 逐对删除最早的工具消息对，防止上下文无限膨胀
            # P0-2：阈值 0.95→0.98，删除历史会废掉全部前缀缓存并直接失忆，尽量晚触发
            _drop次数 = 0
            while _estimated > _limit * 0.98 and _drop次数 < 20:
                _新消息 = self._drop_oldest_tool_pairs(prepared_messages, model_name, _limit)
                if len(_新消息) == len(prepared_messages):
                    break
                prepared_messages = _新消息
                _estimated = self._estimate_tokens(prepared_messages)
                _drop次数 += 1
            if _drop次数:
                logger.error(
                    f"[动态窗口] 压缩后仍超限，已删除最早的 {_drop次数} 组工具消息对，当前预估 {_estimated}"
                    f" | model={model_name}, 上下文窗口={_limit}"
                )

            # P2-12：压缩/删除发生后注入操作台账，防止模型遗忘已完成操作重复执行
            if _发生压缩 or _drop次数:
                prepared_messages = self._更新操作台账(
                    prepared_messages, _进度修改文件, _已读文件清单,
                    _任务计划容器.get("文本", "")
                )

            # P1-2 防护：partial/prefix 续写标记官方仅对末条消息有效；
            # 续写轮之后若还有后续请求轮次，剥掉非末条消息上的标记防 400
            for _m in prepared_messages[:-1]:
                if isinstance(_m, dict):
                    _m.pop("partial", None)
                    _m.pop("prefix", None)

            # P1-2：DeepSeek 官方拒绝 prefix 与 tools 同时出现（实测返回 400
            # "Function call should not be used with prefix"），故 prefix 续写轮
            # 临时不带 tools，续写完成后下一轮自动恢复工具能力
            payload = self._构建请求payload(
                model_name, prepared_messages, max_tokens, temperature,
                settings, (None if _续写禁用tools else tools_payload),
                工具不支持降级, 流式=True,
                response_format=response_format,
            )

            # P1-2：仅 DeepSeek prefix 续写的下一轮请求走 /beta 端点，主链路不变
            _本轮请求url = (f"{self._beta端点(base_url)}/chat/completions"
                          if _续写走beta else url)
            _续写走beta = False
            _续写禁用tools = False

            tool_calls_accumulator = {}  # {index: {"id":..., "function":{"name":..., "arguments":...}}}
            finish_reason_final = None
            assistant_content_buffer = ""
            done_flag = False
            thinking_buffer = ""
            in_thinking = False
            reasoning_content_buffer = ""  # 思考模型（K3/R1等）的 reasoning_content 增量累积（进度推送 + Kimi 回传）
            _思考已推送长度 = -1           # -1 表示尚未推送过（首个增量立即推送一次）
            _本轮已输出正文 = False        # 本轮首段正文前是否需要补轮间分隔

            # 从 settings 获取超时配置（毫秒→秒），默认300秒总超时、15秒连接超时
            # 300s 适配 ModelScope 推理服务处理大上下文 + thinking 推理的耗时；
            # 深思考模型(DeepSeek-R1等)可能需 60-120s 仅用于推理阶段，之后才开始输出
            _chat_timeout_ms = settings.get("chat_timeout", 300000)
            _total_timeout = max(_chat_timeout_ms / 1000, 60)  # 至少60秒
            _connect_timeout = min(15, _total_timeout / 2)  # 连接超时15秒上限

            try:
                # 日志：记录本轮请求的上下文规模，便于排查超时
                _ctx_chars = sum(len(str(m.get('content', ''))) for m in prepared_messages)
                _ctx_msgs = len(prepared_messages)
                logger.info(
                    f"[流式对话] 第{工具轮次+1}轮请求: 消息数={_ctx_msgs}, "
                    f"总字符={_ctx_chars}, timeout={_total_timeout}s"
                )
                logger.debug(f"[流式对话] 正在连接 API: {_本轮请求url}, timeout={_total_timeout}s, 工具轮次={工具轮次}")
                async with 会话.post(_本轮请求url, json=payload, headers=headers,
                                    timeout=aiohttp.ClientTimeout(
                                        total=_total_timeout,
                                        sock_connect=_connect_timeout,
                                    )) as resp:
                    # 致命错误检查
                    if resp.status in self._致命错误码:
                        yield self._致命错误码[resp.status]
                        return

                    if resp.status != 200:
                        # API 不支持 tools 参数 → 降级重试
                        if resp.status == 400 and tools_payload and not 工具不支持降级:
                            if await _响应提示tools不支持(resp):
                                logger.warning("API 不支持 tools 参数，降级为普通流式")
                                工具不支持降级 = True
                                continue
                        # 429 速率限制：独立重试计数器 + 指数退避（不消耗工具轮次，最多 3 次）
                        if resp.status == 429:
                            if _429连续重试 < 3:
                                wait = 2 ** _429连续重试
                                _429连续重试 += 1
                                logger.warning(f"[流式] 速率限制(429)，等待 {wait:.1f}s 后重试（{_429连续重试}/3）...")
                                try:
                                    resp.release()
                                except Exception:
                                    pass
                                await asyncio.sleep(wait)
                                continue  # 429重试不消耗工具轮次（for-range下次迭代自动递增）
                            # 重试耗尽 → 落入下方错误处理
                            logger.warning(f"[流式] 429 连续重试 {_429连续重试} 次仍被限流，放弃重试")
                        error_text = await resp.text()
                        logger.warning(f"[流式对话] API 返回非200状态码: {resp.status}, 响应: {error_text[:300]}")
                        if resp.status == 429:
                            yield "[请求过于频繁] API 速率限制已达上限，请稍后重试"
                        else:
                            yield f"[API 错误 {resp.status}]: {error_text[:300]}"
                        return

                    # 逐行读取 SSE 流
                    _429连续重试 = 0  # 请求成功，重置 429 连续重试计数
                    buffer = ""
                    async for raw_chunk in resp.content.iter_any():
                        if done_flag:
                            break
                        buffer += raw_chunk.decode('utf-8', errors='ignore')
                        while '\n' in buffer:
                            line, buffer = buffer.split('\n', 1)
                            line = line.strip()
                            if not line:
                                continue
                            if line.startswith('data: '):
                                data_str = line[6:]
                                if data_str.strip() == '[DONE]':
                                    done_flag = True
                                    break
                                try:
                                    data_json = json.loads(data_str)
                                    if data_json.get("type") == "error":
                                        continue
                                    # 捕获最后一个chunk中的usage信息，并校准 token 估算系数
                                    _chunk_usage = data_json.get("usage")
                                    if _chunk_usage:
                                        self.上次usage = _chunk_usage
                                        self._校准token估算(prepared_messages, _chunk_usage)
                                        self._记录缓存命中(_chunk_usage, 工具轮次 + 1)
                                    choice = data_json.get('choices', [{}])[0]
                                    delta = choice.get('delta', {}) or {}
                                    fr = choice.get('finish_reason')
                                    if fr:
                                        finish_reason_final = fr
                                    content = delta.get('content', '')
                                    if content:
                                        assistant_content_buffer += content
                                        # 剥离 thinking 标签片段（流式中可能跨chunk）
                                        text, thinking_buffer, in_thinking = _strip_thinking_stream(
                                            content, thinking_buffer, in_thinking
                                        )
                                        if text:
                                            # 轮间分隔：新一轮的首段正文与上一轮叙述之间补空行，
                                            # 防止多轮工具循环的叙述文本在前端渲染时粘连成一段
                                            if not _本轮已输出正文:
                                                _本轮已输出正文 = True
                                                if _曾输出正文:
                                                    text = "\n\n" + text.lstrip("\n")
                                                _曾输出正文 = True
                                            yield text
                                    # 思考模型（K3 始终开启思考）：思考阶段 content 全空可长达几十秒，
                                    # 消费 reasoning_content 增量并推送进度，避免用户面对空白气泡误以为卡死
                                    delta_reasoning = delta.get('reasoning_content')
                                    if delta_reasoning:
                                        reasoning_content_buffer += delta_reasoning
                                        # 节流：首个增量立即推送，此后每累积400字符更新一次进度
                                        if _思考已推送长度 < 0 or len(reasoning_content_buffer) - _思考已推送长度 >= 400:
                                            _思考已推送长度 = len(reasoning_content_buffer)
                                            yield f"\n[思考中: {len(reasoning_content_buffer)}字...]\n"
                                    # 累积 tool_calls 片段
                                    delta_tool_calls = delta.get("tool_calls")
                                    if delta_tool_calls:
                                        for tc in delta_tool_calls:
                                            idx = tc.get("index", 0)
                                            if idx not in tool_calls_accumulator:
                                                tool_calls_accumulator[idx] = {
                                                    "id": tc.get("id", "") or "",
                                                    "type": "function",
                                                    "function": {"name": "", "arguments": ""},
                                                }
                                            if tc.get("id"):
                                                tool_calls_accumulator[idx]["id"] = tc["id"]
                                            fn = tc.get("function") or {}
                                            if fn.get("name"):
                                                tool_calls_accumulator[idx]["function"]["name"] += fn["name"]
                                            if "arguments" in fn and fn["arguments"] is not None:
                                                tool_calls_accumulator[idx]["function"]["arguments"] += fn["arguments"]
                                except (json.JSONDecodeError, IndexError, KeyError):
                                    continue

            except asyncio.TimeoutError:
                logger.error(
                    f"[流式对话] API 请求超时 ({_total_timeout}s), url={url}, "
                    f"轮次={工具轮次+1}, 消息数={len(prepared_messages)}, "
                    f"预估token={self._estimate_tokens(prepared_messages)}"
                )
                yield (
                    f"⚠ API 请求超时（{int(_total_timeout)}秒无响应）\n\n"
                    f"可能原因：\n"
                    f"1. 模型推理服务繁忙，排队等待中\n"
                    f"2. 上下文过大导致处理缓慢\n"
                    f"3. 网络连接不稳定\n\n"
                    f"建议：在设置中调大「API 超时时间」（当前 {int(_total_timeout)}s），"
                    f"或切换更轻量的模型重试"
                )
                return
            except aiohttp.ClientError as e:
                logger.error(f"[流式对话] 网络连接失败: {e}")
                yield f"⚠ 网络连接失败: {e}"
                return
            except (json.JSONDecodeError, KeyError, IndexError) as e:
                logger.error(f"[流式对话] 连接异常: {type(e).__name__}: {e}")
                yield f"\n\n[连接错误]: {str(e)}"
                return

            # 流结束后刷新 thinking 缓冲区（非 thinking 状态下残留的文本需输出）
            if thinking_buffer and not in_thinking:
                yield thinking_buffer
                thinking_buffer = ""

            # 检测流式响应是否未产出任何内容
            if not assistant_content_buffer and not tool_calls_accumulator and finish_reason_final is None:
                logger.warning(f"[流式对话] 流式响应未产出任何内容, model={model_name}")

            # ===== 本轮流结束，判断是否需要执行工具 =====
            logger.info(f"[流式工具循环] 第{工具轮次+1}轮完成: finish_reason={finish_reason_final}, "
                        f"tool_calls={len(tool_calls_accumulator)}, content_len={len(assistant_content_buffer)}")

            if finish_reason_final == "tool_calls" and tool_executor and tool_calls_accumulator:
                tool_calls_list = [tool_calls_accumulator[i] for i in sorted(tool_calls_accumulator.keys())]
                # 保留 assistant 工具调用消息到上下文
                _assistant_msg = {
                    "role": "assistant",
                    "content": assistant_content_buffer,
                    "tool_calls": tool_calls_list,
                }
                # Kimi/DeepSeek 官方要求：工具循环原样回传完整 assistant 消息（含 reasoning_content），不能只留 content
                if reasoning_content_buffer and self._支持思考参数(model_name):
                    _assistant_msg["reasoning_content"] = reasoning_content_buffer
                prepared_messages.append(_assistant_msg)
                # 解析工具调用信息
                _tc_infos = _解析工具调用信息(tool_calls_list)

                # ===== 工具执行成功去重检测 =====
                (_async_tasks, _去重结果, _去重提示,
                 _同轮重复, _本轮结果缓存) = self._工具去重检测(
                    _tc_infos, _工具执行历史, _已修改文件集, tool_executor, 流式=True,
                    _文件读取计数=_文件读取计数,
                )

                # 推送工具执行进度（仅推送需要实际执行的，同轮重复调用不推送）
                for idx, tc_info in enumerate(_tc_infos):
                    if _去重结果.get(idx) is None and idx not in _同轮重复:
                        yield f"\n[正在执行: {tc_info['name']}...]\n"

                # 并行等待所有实际执行的结果
                _执行结果映射 = await _并行执行工具(_async_tasks)

                # 按顺序追加结果（P2-11 共享装配：含错误恢复 + 重复失败检测 + 插入时即摘要化）
                _熔断中断, _熔断工具名 = self._装配工具结果(
                    prepared_messages, _tc_infos, _去重结果, _同轮重复, _本轮结果缓存,
                    _执行结果映射, _去重提示, _工具重试计数, _工具名重试计数,
                    _工具错误类型计数, _工具执行历史, _已修改文件集, _进度修改文件,
                    _进度工具统计, _已读文件清单, _任务计划容器, 工具轮次,
                    _文件读取计数
                )
                # 熔断：同一工具连续失败达上限 → 中断循环，避免无限重试烧 token
                if _熔断中断:
                    logger.warning(f"[熔断] 工具 {_熔断工具名} 连续失败达上限，中断流式工具循环")
                    yield (
                        f"\n\n[已中断] 工具 {_熔断工具名} 已连续失败多次，"
                        f"已停止自动重试以避免死循环。请检查失败原因后再继续对话。"
                    )
                    return
                # 空转熔断：本轮工具调用全被"强制复用"（零实际执行）视为空转，
                # 连续 3 轮空转说明模型在重复打转（如反复重读同一文件），中断避免烧 token（坑 98）
                if _tc_infos and not _async_tasks:
                    _连续空转轮数 += 1
                else:
                    _连续空转轮数 = 0
                if _连续空转轮数 >= 3:
                    logger.warning(f"[熔断] 连续 {_连续空转轮数} 轮工具调用全部被强制复用（无实际进展），中断流式工具循环")
                    yield (
                        "\n\n[已中断] 模型连续多轮重复调用相同工具且无实际进展，已停止循环。"
                        "请补充更明确的指示后再继续对话。"
                    )
                    return
                # 进入下一轮流式请求，继续 yield 后续文本
                continue

            # ===== finish_reason=length 处理：模型 token 耗尽被截断 =====
            # 典型场景：DeepSeek 等模型 thinking 占满了 max_tokens，没输出内容就被截断
            if (finish_reason_final == "length"
                and tool_executor
                and tools_payload
                and not 工具不支持降级
                and _截断续写次数 < 2):
                _截断续写次数 += 1
                # P1-2：有已输出内容且供应商支持官方续写协议时，用 partial/prefix 标记
                # 的 assistant 消息承载前缀，模型从断点自然接续，不重复已输出内容
                _续写风格 = (self._前缀续写能力(model_name)
                           if assistant_content_buffer.strip() else None)
                if _续写风格:
                    _前缀消息 = {"role": "assistant", "content": assistant_content_buffer,
                               _续写风格: True}
                    # Kimi 思考模式官方要求续写时带回 reasoning_content
                    if _续写风格 == "partial" and reasoning_content_buffer:
                        _前缀消息["reasoning_content"] = reasoning_content_buffer
                    prepared_messages.append(_前缀消息)
                    _续写走beta = (_续写风格 == "prefix")  # DeepSeek 下一轮走 /beta
                    # DeepSeek prefix 与 tools 实测互斥（400），该轮必须去掉 tools；
                    # Kimi partial 与 tools 实测可共存，保持带 tools 不打断工具循环
                    _续写禁用tools = (_续写风格 == "prefix")
                else:
                    # 无官方协议或无已输出内容：保留现有土办法，行为不变
                    if assistant_content_buffer.strip():
                        prepared_messages.append({
                            "role": "assistant",
                            "content": assistant_content_buffer,
                        })
                    prepared_messages.append({
                        "role": "user",
                        "content": (
                            "[系统提示] 你的响应被截断了（token不足）。"
                            "请简洁地继续执行任务，直接调用工具完成修改，不要重复分析。"
                        )
                    })
                logger.info(
                    f"[流式工具循环] token截断重试({_截断续写次数}/2): "
                    f"finish_reason=length, content_len={len(assistant_content_buffer)}, "
                    f"续写方式={_续写风格 or '土办法'}"
                )
                yield "\n[自动继续执行...]\n"
                continue

            # ===== 纯文本响应自动重提（参考 OpenHands 响应分类器） =====
            # 条件：模型返回了纯文本（finish_reason != "tool_calls"），
            #       但工具可用且不是第一轮，说明模型应该调工具但没调。
            # 任务类型感知门控：无写入工具（如可视化 Tab 只读模式）或用户消息无修改意图
            #（纯问答）时，纯文本就是正常结束，不强制重提，避免完整回答被打断成短回复
            _可用写工具 = _提取可用写工具名(tools_payload)
            if (finish_reason_final in ("stop", None)
                and tool_executor
                and tools_payload
                and not 工具不支持降级
                and 工具轮次 > 0  # 第一轮可能是模型在分析，允许
                and _纯文本重提次数 < 2
                and assistant_content_buffer.strip()  # 确实有内容输出
                and _可用写工具  # 存在写入工具（只读模式下纯文本为正常结束）
                and _检测写意图(prepared_messages)):  # 用户消息含修改意图（纯问答不重提）
                # 停下等待保护：模型文本向用户提问 → 视为等待用户回复，正常结束回合；
                # 禁止强制重提，否则注入的系统提醒会把模型推向自问自答后继续执行
                if _文本是否向用户提问(assistant_content_buffer):
                    logger.info("[流式工具循环] 纯文本重提跳过: 模型输出含对用户的提问，结束回合等待回复")
                    return
                # 重复总结检测：本次输出与上次重提后的输出开头几乎一致，说明模型
                # 坚持认为任务已完成、再提只会逗它复述一遍总结 → 视为正常结束
                _本次文本 = assistant_content_buffer.strip()
                if _上次重提文本 and _本次文本[:200] == _上次重提文本[:200]:
                    logger.info("[流式工具循环] 纯文本重提跳过: 输出与上次几乎相同，视为正常结束")
                    return
                _上次重提文本 = _本次文本
                _纯文本重提次数 += 1
                # 保留模型的文本输出到上下文
                prepared_messages.append({
                    "role": "assistant",
                    "content": assistant_content_buffer,
                })
                # 注入强制执行指令（工具名按本次实际下发的写入工具动态拼接）
                prepared_messages.append({
                    "role": "user",
                    "content": (
                        f"[系统提醒 {_纯文本重提次数}/2] 你的响应没有调用任何工具。"
                        f"请现在立即使用 {'、'.join(_可用写工具)} 等工具执行你描述的修改。"
                        f"直接调用工具，不要再解释方案，也不要重复输出之前已给出的总结或表格。"
                    )
                })
                logger.info(f"[流式工具循环] 纯文本自动重提({_纯文本重提次数}/2): 模型返回文本但未调用工具")
                yield "\n[自动继续执行...]\n"
                continue

            # 只读不写续跑（第一轮特殊处理：模型读了文件就停了）
            # 同受任务类型门控：只读模式或纯问答场景下，读完即答是正常行为
            if (_只读续跑次数 < 1
                and 工具轮次 == 0  # 仅第一轮
                and tool_executor
                and tools_payload
                and not 工具不支持降级
                and _可用写工具  # 存在写入工具（只读模式下读完即答是正常行为）
                and _检测写意图(prepared_messages)  # 用户消息含修改意图（纯问答不续跑）
                and not _文本是否向用户提问(assistant_content_buffer)  # 模型在提问则等待用户回复，不续跑
                and self._should_auto_continue(prepared_messages)):
                _只读续跑次数 += 1
                prepared_messages.append({
                    "role": "user",
                    "content": (
                        f"[系统提醒 {_只读续跑次数}/1] 你只读取了文件但没有执行修改。"
                        f"请立即使用 {'、'.join(_可用写工具)} 等工具完成文件变更。"
                    )
                })
                logger.info(f"[流式工具循环] 只读不写续跑({_只读续跑次数}/1): 首轮只有读操作")
                yield "\n[自动继续执行...]\n"
                continue

            # 正常结束
            if 工具轮次 > 0:
                logger.info(f"[流式工具循环] 循环结束: 共执行{工具轮次+1}轮, 最终finish_reason={finish_reason_final}")
            return

        # 工具循环耗尽：附带进度摘要与续接引导（与非流式路径文案统一）
        yield 构建轮次上限提示(最大工具循环次数, _进度工具统计, _进度修改文件)

    def _should_auto_continue(self, messages: list) -> bool:
        """检测是否需要自动续跑：有读操作但无写操作时触发"""
        has_read = False
        has_write = False
        for msg in messages:
            if msg.get("role") == "assistant" and msg.get("tool_calls"):
                for tc in msg["tool_calls"]:
                    name = tc.get("function", {}).get("name", "")
                    if name in ("read_plugin_file", "list_plugin_files"):
                        has_read = True
                    elif name in ("write_plugin_file", "edit_file", "batch_edit"):
                        has_write = True
        return has_read and not has_write

    def 获取性能报告(self) -> dict:
        """返回 API 调用性能统计（含 P0-3 前缀缓存累计命中率）"""
        return {
            "总请求数": self._性能.总请求数,
            "成功数": self._性能.成功数,
            "失败数": self._性能.失败数,
            "平均响应时间(秒)": round(self._性能.平均响应时间, 2),
            "最近请求数": len(self._性能._响应时间列表),
            "缓存累计命中tokens": self._缓存累计命中tokens,
            "缓存累计输入tokens": self._缓存累计输入tokens,
            "缓存累计命中率(%)": round(
                self._缓存累计命中tokens / max(self._缓存累计输入tokens, 1) * 100, 1
            ),
            # 总计会被多模型混算稀释，同时给出每个模型的单独命中率
            "缓存分模型命中率(%)": {
                模型: round(命中 / max(输入, 1) * 100, 1)
                for 模型, (命中, 输入) in self._缓存分模型统计.items()
            },
        }


__all__ = ["AICoderClient"]
