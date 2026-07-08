"""路由公共模块

提供子路由模块共享的工具与单例：
- 统一响应工厂 _error_response / _success_response
- 速率限制器 RateLimiter（_rate_limiter 单例）
- 附件与文件树格式化 _format_attachment_descriptions / _format_file_tree
- 智能体模块（llm_client / local_model_client / tool_router / ContextManager）
- 性能监控收集器 _metrics_collector
- 模块加载副作用：注册 audit_logger / 审计中间件到 PromptServer.app
"""
from aiohttp import web
import sys
import time
import uuid
from pathlib import Path
from collections import defaultdict

from server import PromptServer

from .系统环境映射 import get_plugin_root, 是否云端环境, 项目版本
from .审计日志 import AuditLogger, audit_middleware
from .性能监控 import get_metrics_collector, 连接计数中间件
from .日志配置 import 获取日志器

logger = 获取日志器("路由公共")

# M5: 错误消息脱敏 - 通用服务器错误响应
服务器内部错误 = "服务器内部错误，请稍后重试"


# ─── 智能体模块导入 ───────────────────────────────────────
_plugin_root = str(get_plugin_root())
if _plugin_root not in sys.path:
    sys.path.insert(0, _plugin_root)

try:
    from 智能体.模型客户端 import AICoderClient, LocalModelClient
    from 智能体.工具路由器 import ToolRouter
    from 智能体.记忆与上下文压缩 import ContextManager
    from 智能体.跨会话记忆 import 跨会话记忆管理器
    from 智能体.项目上下文分析 import 项目上下文分析器

    llm_client = AICoderClient()
    local_model_client = LocalModelClient()
    tool_router = ToolRouter()
    _项目上下文分析器 = 项目上下文分析器()
    _agent_available = True
except ImportError as e:
    logger.exception(f"智能体模块加载失败（聊天功能不可用）: {e}")
    llm_client = None
    local_model_client = None
    tool_router = None
    _项目上下文分析器 = None
    ContextManager = None
    _agent_available = False


# ─── 初始化审计 ─────────────────────────────────────
_data_dir = Path(__file__).parent.parent / "数据"
_audit_logger = AuditLogger(_data_dir / "审计日志")

# 注册到 PromptServer app
_app = PromptServer.instance.app
_app['audit_logger'] = _audit_logger

# 注册审计中间件
_app.middlewares.append(audit_middleware)

# 注册 HTTP 连接计数中间件（自动追踪并发连接数）
if 连接计数中间件 not in _app.middlewares:
    _app.middlewares.append(连接计数中间件)

# 性能监控收集器
_metrics_collector = get_metrics_collector()

# 跨会话记忆管理器（全局单例）
# 智能体模块不可用时降级为 None
if _agent_available:
    _记忆管理器 = 跨会话记忆管理器(_data_dir / "记忆")
else:
    _记忆管理器 = None


# ─── P3: 本地模型后台预热（消除首次推理 30s+ 加载延迟）─────────
# 仅当 model_source=="local" 且 local_path 有效时，延迟 30 秒在后台
# 加载模型；预热失败不影响正常功能。
async def _启动本地模型预热(_app):
    try:
        if local_model_client is not None:
            local_model_client.调度预热()
    except Exception as e:
        logger.debug(f"调度本地模型预热失败（忽略）: {e}")


try:
    _app.on_startup.append(_启动本地模型预热)
except Exception as _e:
    logger.debug(f"注册本地模型预热钩子失败（忽略）: {_e}")


# ─── 统一响应工厂 ──────────────────────────────────────────

def _error_response(message: str, status: int = 400) -> web.Response:
    return web.json_response({"success": False, "message": message, "data": None}, status=status)


def _success_response(data=None, message: str = "ok") -> web.Response:
    return web.json_response({"success": True, "message": message, "data": data})


# ─── 分页工具 ─────────────────────────────────────────────

# 分页相关常量
_DEFAULT_PAGE_SIZE = 20
_MAX_PAGE_SIZE = 100


def _分页参数(request, default_page_size: int = _DEFAULT_PAGE_SIZE, max_page_size: int = _MAX_PAGE_SIZE):
    """从 aiohttp 请求 query 中提取分页参数。

    返回 (page, page_size, paginated)
    - paginated=False 表示请求未携带任何分页参数（用于向后兼容：直接返回完整列表）
    - 异常值（负数、非数字）会被规整到合法范围内
    """
    has_page = 'page' in request.query
    has_size = 'page_size' in request.query
    paginated = has_page or has_size

    try:
        page = int(request.query.get('page', 1))
    except (TypeError, ValueError):
        page = 1
    try:
        page_size = int(request.query.get('page_size', default_page_size))
    except (TypeError, ValueError):
        page_size = default_page_size

    page = max(1, page)
    page_size = max(1, min(max_page_size, page_size))
    return page, page_size, paginated


def _分页响应(items: list, page: int, page_size: int, paginated: bool,
           list_key: str = "items", extra: dict = None) -> dict:
    """构造分页响应字典。

    - paginated=True：按 page/page_size 切片
    - paginated=False：返回完整列表（向后兼容），page_size 取实际长度
    - 始终附带 total / page / page_size / total_pages 元数据
    """
    total = len(items)
    if paginated:
        start = (page - 1) * page_size
        end = start + page_size
        sliced = items[start:end]
        effective_page_size = page_size
    else:
        sliced = items
        page = 1
        effective_page_size = total if total > 0 else page_size

    total_pages = (total + effective_page_size - 1) // effective_page_size if effective_page_size > 0 else 0
    payload = {
        list_key: sliced,
        "total": total,
        "page": page,
        "page_size": effective_page_size,
        "total_pages": total_pages,
    }
    if extra:
        payload.update(extra)
    return payload


# ─── 速率限制器 ───────────────────────────────────────────

class RateLimiter:
    def __init__(self, max_requests=60, window_seconds=60):
        self.max_requests = max_requests
        self.window = window_seconds
        self._requests = defaultdict(list)
        self._cleanup_counter = 0
        self._cleanup_interval = 100  # 每 100 次调用执行一次过期 IP 清理

    def is_allowed(self, client_ip: str) -> bool:
        now = time.time()
        self._requests[client_ip] = [t for t in self._requests[client_ip] if now - t < self.window]

        # 周期性清理过期的 IP 记录（避免长时间运行后 _requests 字典无限增长）
        self._cleanup_counter += 1
        if self._cleanup_counter >= self._cleanup_interval:
            self._cleanup_counter = 0
            self._cleanup_expired(now)

        if len(self._requests[client_ip]) >= self.max_requests:
            return False
        self._requests[client_ip].append(now)
        return True

    def _cleanup_expired(self, now: float):
        """清理最后一条记录的时间戳超过 2 倍窗口时间的 IP"""
        expired_threshold = now - self.window * 2
        expired_ips = [
            ip for ip, timestamps in self._requests.items()
            if not timestamps or timestamps[-1] < expired_threshold
        ]
        for ip in expired_ips:
            del self._requests[ip]


_rate_limiter = RateLimiter()


# ─── L4: 标准 429 限流响应（含 Retry-After 头）───────────────

def _限流响应(等待秒: int = 60, 错误消息: str = "请求过于频繁，请稍后再试") -> web.Response:
    """构建标准 429 限流响应。

    总是携带 Retry-After 头（HTTP 语义上要求客户端等待的秒数）。
    """
    return web.json_response(
        {"success": False, "status": "error", "error": 错误消息, "message": 错误消息},
        status=429,
        headers={"Retry-After": str(max(1, int(等待秒)))},
    )


# ─── 上传大小预校验 ────────────────────────────────────────

# 文件上传大小限制：50MB
_最大上传字节 = 50 * 1024 * 1024


async def _检查上传大小(request, 上限字节: int = _最大上传字节):
    """在读取请求体前检查 Content-Length，超限返回 413。

    返回值：
    - 超限：返回一个 web.Response（status=413），调用方应直接返回该响应；
    - 未超限或无 Content-Length：返回 None。
    """
    content_length = request.headers.get("Content-Length")
    if content_length:
        try:
            if int(content_length) > 上限字节:
                上限MB = 上限字节 // (1024 * 1024)
                return web.json_response(
                    {
                        "success": False,
                        "status": "error",
                        "error": f"文件大小超过限制（最大{上限MB}MB）",
                    },
                    status=413,
                )
        except (ValueError, TypeError):
            pass
    return None


# ─── 幂等缓存（M8：防止网络重试导致重复提交）──────────────────

class 幂等缓存器:
    """短期内存幂等缓存，防止网络重试导致重复提交"""
    def __init__(self, ttl秒=60):
        self._缓存 = {}  # key: f"{session_id}:{idempotency_key}" -> (result, timestamp)
        self._ttl = ttl秒

    def 检查(self, session_id, idempotency_key):
        """检查是否已存在相同幂等键的请求，返回缓存的响应或 None"""
        if not idempotency_key:
            return None
        key = f"{session_id}:{idempotency_key}"
        now = time.time()
        # 清理过期条目
        self._缓存 = {k: v for k, v in self._缓存.items() if now - v[1] < self._ttl}
        if key in self._缓存:
            return self._缓存[key][0]
        return None

    def 记录(self, session_id, idempotency_key, result):
        """记录请求结果到缓存"""
        if not idempotency_key:
            return
        key = f"{session_id}:{idempotency_key}"
        self._缓存[key] = (result, time.time())


# 全局实例
_幂等缓存 = 幂等缓存器(ttl秒=60)


# ─── 附件类型白名单校验（H4）──────────────────────────────────

_允许的附件类型 = {"image/jpeg", "image/png", "image/gif", "image/webp", "image/bmp", "text/plain", "text/markdown", "application/json"}


async def _检查附件类型(attachments):
    """校验附件 MIME 类型是否在白名单内，返回错误响应或 None"""
    if not attachments:
        return None
    for att in attachments:
        att_type = att.get("type", "") if isinstance(att, dict) else ""
        if not att_type or att_type not in _允许的附件类型:
            return web.json_response({
                "success": False,
                "error": f"不支持的文件类型: {att_type or '(空)'}"
            }, status=415)
    return None


# ─── CSRF 校验 ──────────────────────────────────────────────

# 需要 CSRF 校验的写操作 HTTP 方法（GET/HEAD/OPTIONS 等只读方法直接放行）
_CSRF写操作方法 = {"POST", "PUT", "DELETE", "PATCH"}

# CSRF 校验仅作用于本插件的 API 路径前缀，避免拦截 ComfyUI 核心路由
_CSRF适用前缀 = ("/ai-coder/", "/v1/ai-coder/", "/v1/nca/", "/nca/")

# CSRF 豁免路径前缀
# - /static/ 静态资源不涉及状态变更
# - 首次认证类端点：用户此时尚未持有 session/CSRF token，必须豁免
#   · /nca/billing/verify-login   RanKing 账号登录验证（前端 API_VERIFY_LOGIN）
_CSRF豁免前缀 = (
    "/static/",
    "/nca/billing/verify-login",
)


def _是CSRF豁免请求(request) -> bool:
    """判断请求是否豁免 CSRF 校验。

    豁免条件（任一满足即豁免）：
    0. 本地环境（非云端）：ComfyUI 个人独占使用，不存在跨域攻击风险，
       直接全量豁免。
    1. 只读方法（GET/HEAD/OPTIONS 等非写操作）
    2. 路径不在本插件 API 前缀范围内（例如 ComfyUI 自身的 /prompt、/upload）
    3. 路径以静态资源前缀开头
    4. WebSocket 升级请求（Upgrade: websocket）
    """
    # 本地环境完全豁免 CSRF（个人使用，无跨域风险）；仅云端执行后续校验。
    if not 是否云端环境():
        return True

    method = (request.method or "").upper()
    if method not in _CSRF写操作方法:
        return True

    path = request.path or ""
    if not any(path.startswith(p) for p in _CSRF适用前缀):
        return True
    if path.startswith(_CSRF豁免前缀):
        return True

    upgrade头 = (request.headers.get("Upgrade", "") or "").lower()
    if upgrade头 == "websocket":
        return True

    return False


def _校验CSRF令牌(request):
    """对写操作请求校验 X-CSRF-Token 请求头。

    校验规则：
    - 豁免请求直接放行（返回 None）
    - 必须存在 X-CSRF-Token 头，且其值为合法 UUID

    返回值：
    - 通过/豁免：返回 None
    - 失败：返回 web.Response（403 JSON），调用方应直接返回该响应而不再执行后续 handler
    """
    if _是CSRF豁免请求(request):
        return None

    令牌 = request.headers.get("X-CSRF-Token", "") or ""
    路径 = request.path or ""

    if not 令牌:
        logger.warning(f"CSRF 校验失败：缺少 X-CSRF-Token（method={request.method}, path={路径}）")
        return web.json_response({"error": "CSRF token missing or invalid"}, status=403)

    try:
        uuid.UUID(令牌)
    except (ValueError, AttributeError, TypeError):
        logger.warning(f"CSRF 校验失败：token 格式非法（method={request.method}, path={路径}）")
        return web.json_response({"error": "CSRF token missing or invalid"}, status=403)

    return None


# ─── 附件与文件树格式化 ────────────────────────────────────

# 文本附件内联展开的最大字节数（防止单个大文件吵爆上下文）
_MAX_INLINE_TEXT_BYTES = 32 * 1024


def _format_attachment_descriptions(attachments):
    """将附件转为插入用户消息的描述列表

    - 图片附件：仅生成文本占位（实际 base64 交由 _image_attachments 传递给 vision 模型）
    - 文本/代码附件：如果携带 base64 data，解码后以 fenced code block 内联展开
    - 其他附件：仅输出文件名/类型/大小
    """
    import base64
    descriptions = []
    for att in attachments or []:
        att_name = att.get("name", "未命名文件")
        att_type = att.get("type", "") or ""
        att_size = att.get("size", 0)
        att_data = att.get("data")

        if att_type.startswith("image/"):
            descriptions.append(f"[图片附件: {att_name}]")
            continue

        if att_data:
            try:
                base64_str = att_data.split(",", 1)[1] if "," in att_data else att_data
                raw = base64.b64decode(base64_str)
                content = raw.decode("utf-8")
                if len(content) > _MAX_INLINE_TEXT_BYTES:
                    content = content[:_MAX_INLINE_TEXT_BYTES] + "\n...(内容已截断)"
                descriptions.append(f"[文件: {att_name}]\n```\n{content}\n```")
                continue
            except Exception:
                pass

        descriptions.append(f"[附件: {att_name} ({att_type}, {att_size}字节)]")
    return descriptions


def _format_file_tree(tree, indent=0):
    """将文件树格式化为文本摘要"""
    lines = []
    for item in tree:
        prefix = "  " * indent
        if item["type"] == "dir":
            lines.append(f"{prefix}📁 {item['name']}/")
            if item.get("children"):
                lines.append(_format_file_tree(item["children"], indent + 1))
        else:
            size_str = f" ({item.get('size', 0)}B)" if item.get('size', 0) > 0 else ""
            lines.append(f"{prefix}📄 {item['name']}{size_str}")
    return "\n".join(lines)
