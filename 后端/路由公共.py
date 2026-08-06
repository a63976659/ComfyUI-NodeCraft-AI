"""路由公共模块

提供子路由模块共享的工具与单例：
- 统一响应工厂 _error_response / _success_response
- 速率限制器 RateLimiter（_rate_limiter 单例）
- 附件与文件树格式化 _format_attachment_descriptions / _format_file_tree
- 智能体模块（llm_client / local_model_client / tool_router / ContextManager）
- 性能监控收集器 _metrics_collector
- 模块加载副作用：注册 audit_logger / 审计中间件到 PromptServer.app

─── 模块地图 ───────────────────────────────────────────────────────────

    接口路由.py       总入口路由器，注册所有子路由到 PromptServer
    聊天路由.py       /chat 聊天请求处理、流式输出、上下文压缩
    会话路由.py       /sessions CRUD、消息持久化
    文件路由.py       /files 文件读写、目录浏览、文件树
    设置与模型路由.py  /settings 配置读写、/local-models 本地模型管理
    审计与文档路由.py  审计日志查询、性能指标、API 文档
    市场与打包路由.py  模板市场、插件打包下载
    GitHub路由.py     GitHub 仓库同步
    WebSocket路由.py  实时双向通信（代码补全等）
    路由公共.py（本文件）  全局单例 & 工具函数

─── 初始化流程 ─────────────────────────────────────────────────────────

    1. 服务容器创建（_ServiceContainer 单例）
    2. 智能体模块导入（llm_client / local_model_client / tool_router）
    3. 审计日志初始化 → 注册审计中间件到 PromptServer.app
    4. 性能监控收集器初始化 → 注册连接计数中间件
    5. 跨会话记忆管理器初始化
    6. 会话锁管理器注册
    7. on_startup 钩子：工具路由知识库索引预热
    8. on_cleanup 钩子：Worker 子进程优雅关闭

─── 依赖图 ─────────────────────────────────────────────────────────────

    接口路由.py
        ├── 聊天路由.py       → 路由公共.py (单例: llm_client, tool_router)
        ├── 会话路由.py       → 路由公共.py (单例: _会话锁, _记忆管理器)
        ├── 文件路由.py       → 路由公共.py (单例: _rate_limiter)
        ├── 设置与模型路由.py  → 路由公共.py (单例: local_model_client)
        ├── 审计与文档路由.py → 路由公共.py (_分页参数, _分页响应)
        ├── 市场与打包路由.py  → 路由公共.py (_success_response)
        ├── GitHub路由.py     → 路由公共.py (_error_response)
        └── WebSocket路由.py  → 路由公共.py (服务容器)
"""
import asyncio
import sys
import time
from collections import defaultdict
from pathlib import Path

from aiohttp import web
from server import PromptServer

from .审计日志 import AuditLogger, audit_middleware
from .性能监控 import get_metrics_collector, 连接计数中间件
from .日志配置 import 获取日志器
from .系统环境映射 import get_custom_nodes_path, get_models_dir, get_plugin_root

logger = 获取日志器("路由公共")

# M5: 错误消息脱敏 - 通用服务器错误响应
服务器内部错误 = "服务器内部错误，请稍后重试"


# ─── 模块初始化依赖声明 ──────────────────────────────────────
_初始化依赖图 = {
    "会话缓存": [],
    "会话锁管理器": [],
    "工具路由器": ["会话缓存"],
    "本地模型客户端": [],
    "工具路由预热": ["工具路由器"],
}


def _验证初始化顺序(已初始化: list):
    """检查当前初始化顺序是否满足依赖关系"""
    try:
        for module, deps in _初始化依赖图.items():
            if module in 已初始化:
                for dep in deps:
                    if dep not in 已初始化 or 已初始化.index(dep) > 已初始化.index(module):
                        logger.warning(f"[初始化] 依赖违规: {module} 在 {dep} 之前初始化")
    except Exception as e:
        logger.debug(f"[初始化] 依赖验证异常（忽略）: {e}")


# ─── 服务容器（轻量依赖注入）──────────────────────────────────
class _ServiceContainer:
    """简易服务容器，集中管理全局单例服务"""

    def __init__(self):
        self._services = {}

    def register(self, name: str, instance):
        """注册服务实例"""
        self._services[name] = instance

    def get(self, name: str, default=None):
        """获取服务实例"""
        return self._services.get(name, default)

    def has(self, name: str) -> bool:
        return name in self._services

    @property
    def registered(self) -> list:
        return list(self._services.keys())


服务容器 = _ServiceContainer()


# ─── 智能体模块导入 ───────────────────────────────────────
_plugin_root = str(get_plugin_root())
if _plugin_root not in sys.path:
    sys.path.insert(0, _plugin_root)

try:
    from 智能体.工具路由器 import ToolRouter
    from 智能体.模型客户端 import AICoderClient, LocalModelClient
    from 智能体.记忆与上下文压缩 import ContextManager
    from 智能体.跨会话记忆 import 跨会话记忆管理器
    from 智能体.项目上下文分析 import 项目上下文分析器

    llm_client = AICoderClient()
    local_model_client = LocalModelClient()
    tool_router = ToolRouter()
    _项目上下文分析器 = 项目上下文分析器()
    _agent_available = True

    # 注册到服务容器（不改变现有全局引用方式，仅作为未来过渡）
    服务容器.register("llm_client", llm_client)
    服务容器.register("local_model_client", local_model_client)
    服务容器.register("工具路由器", tool_router)
    服务容器.register("项目上下文分析器", _项目上下文分析器)
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


# ─── 本插件静态资源缓存策略（防浏览器旧模块缓存）────────────
# 宿主对 /extensions/ 的缓存头随 ComfyUI 版本而异：旧版 web.static 不发
# Cache-Control（浏览器启发式缓存会导致 import 命中旧模块），当前版本统一
# 发 no-store（每次全量重传）。此处以中间件将本插件路径统一为 no-cache：
# 每次强制 revalidate，配合 web.static 的 Last-Modified/ETag 可返回 304，
# 兼顾新鲜度与传输量；对无缓存头的旧版宿主则是必要兜底。
# 仅命中本插件前缀，不影响宿主与其它插件的 /extensions/ 路径。
# 注：custom nodes 在宿主 add_routes/setup 之前导入（main.py 时序），
# 此时 app.middlewares 尚未冻结，追加安全（同上方审计中间件）。
# 前缀为 ASCII 目录名，不受中文文件名 URL 编码影响（request.path 已解码）。
_静态资源前缀 = f"/extensions/{get_plugin_root().name}/"


@web.middleware
async def _静态缓存中间件(request, handler):
    response = await handler(request)
    if request.path.startswith(_静态资源前缀):
        # 直接赋值而非 setdefault：本中间件处于宿主 cache_control 内层，
        # 先执行；宿主侧用 setdefault 不会覆盖此处结果
        response.headers["Cache-Control"] = "no-cache"
    return response


try:
    _app.middlewares.append(_静态缓存中间件)
except Exception as _e:
    logger.debug(f"注册静态缓存中间件失败（忽略）: {_e}")

# 性能监控收集器
_metrics_collector = get_metrics_collector()

# 跨会话记忆管理器（全局单例）
# 智能体模块不可用时降级为 None
if _agent_available:
    _记忆管理器 = 跨会话记忆管理器(_data_dir / "记忆")
else:
    _记忆管理器 = None


# ─── P1: 工具路由知识库索引预热（消除首次检索延迟）────────────
async def _启动工具路由预热(_app=None):
    """延迟预热工具路由知识库索引"""
    await asyncio.sleep(2)  # 延迟2秒避免与主启动竞争
    try:
        if tool_router is not None:
            await asyncio.to_thread(tool_router._预热知识库索引)
    except Exception as e:
        logger.debug(f"工具路由预热失败（忽略）: {e}")


try:
    _app.on_startup.append(_启动工具路由预热)
except Exception as _e:
    logger.debug(f"注册工具路由预热钩子失败（忽略）: {_e}")


# ─── P1: Worker 子进程优雅清理（应用关闭时释放资源）────────────
async def _应用关闭清理(_app=None):
    """应用关闭时优雅清理 Worker 子进程和后台任务"""
    # 清理本地模型 Worker 子进程
    try:
        if local_model_client is not None:
            await local_model_client.shutdown()
    except Exception as e:
        logger.debug(f"Worker 清理失败（忽略）: {e}")
    # 清理后台任务（直指真实定义处：_后台任务管理器已迁移至 并发控制）
    try:
        from .并发控制 import _后台任务管理器
        await _后台任务管理器.shutdown()
    except Exception as e:
        logger.debug(f"后台任务清理失败（忽略）: {e}")


try:
    _app.on_cleanup.append(_应用关闭清理)
except Exception as _e:
    logger.debug(f"注册应用关闭清理钩子失败（忽略）: {_e}")


# ─── P1: 会话文件锁管理器（防止同一会话并发写入）────────────

class _会话锁管理器:
    """按会话 ID 维护 asyncio.Lock，防止同一会话的并发写入冲突。

    每个会话独立一把锁，不同会话的操作不会互相阻塞。
    闲置锁周期性清理，避免内存无限增长。
    """

    def __init__(self):
        self._locks: dict[str, asyncio.Lock] = {}
        self._access_time: dict[str, float] = {}  # 记录最近访问时间
        self._cleanup_counter = 0
        self._cleanup_interval = 50  # 每 50 次获取执行一次清理
        self._max_idle_seconds = 300  # 锁空闲超过 5 分钟则清理

    def 获取锁(self, session_id: str) -> asyncio.Lock:
        """获取指定会话的 asyncio.Lock（不存在则自动创建）"""
        now = time.time()
        if session_id not in self._locks:
            self._locks[session_id] = asyncio.Lock()
        self._access_time[session_id] = now

        # 周期性清理闲置锁
        self._cleanup_counter += 1
        if self._cleanup_counter >= self._cleanup_interval:
            self._cleanup_counter = 0
            self._清理闲置锁(now)

        return self._locks[session_id]

    def _清理闲置锁(self, now: float):
        """清理空闲超时且未被持有的锁"""
        expired_ids = [
            sid for sid, t in self._access_time.items()
            if now - t > self._max_idle_seconds
            and sid in self._locks
            and not self._locks[sid].locked()
        ]
        for sid in expired_ids:
            del self._locks[sid]
            del self._access_time[sid]


# 全局会话锁实例
_会话锁 = _会话锁管理器()

# 注册会话锁到服务容器
服务容器.register("会话锁管理器", _会话锁)

# 启动时验证初始化顺序
_验证初始化顺序(["会话缓存", "会话锁管理器", "工具路由器", "本地模型客户端"])


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


# ─── 路径白名单 ──────────────────────────────────

def 获取允许路径根() -> list:
    """返回允许访问的三个根目录（已 resolve）。

    ComfyUI 根 / custom_nodes / models：桌面版下 custom_nodes 可能被重定向，
    与 models 不共根，单一 custom_nodes 白名单会误拦设置面板选择 models/LLM 的合法请求。
    """
    roots = []
    for root in (get_custom_nodes_path().parent, get_custom_nodes_path(), get_models_dir()):
        try:
            roots.append(Path(root).resolve())
        except Exception:
            continue
    return roots


def 路径在允许范围内(目标路径) -> bool:
    """校验路径（resolve 后）是否位于白名单三根之内，用于路径穿越防护"""
    try:
        target = Path(目标路径).resolve()
    except Exception:
        return False
    for root in 获取允许路径根():
        try:
            if target.is_relative_to(root):
                return True
        except (TypeError, ValueError):
            continue
    return False


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

_允许的附件类型 = {
    "image/jpeg", "image/png", "image/gif", "image/webp", "image/bmp",
    "text/plain", "text/markdown", "application/json",
}


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


# ─── 附件与文件树格式化 ────────────────────────────────────

# 文本附件内联展开的最大字节数（防止单个大文件吵爆上下文）
_MAX_INLINE_TEXT_BYTES = 32 * 1024


def _format_attachment_descriptions(attachments):
    """将附件转为插入用户消息的描述列表

    - 图片附件：仅生成文本占位（实际 base64 交由 _image_attachments 传递给 vision 模型）
    - 音频附件：仅生成文本占位（实际 base64 交由 _audio_attachments 传递给音频模型）
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

        if att_type.startswith("audio/"):
            descriptions.append(f"[音频附件: {att_name}]")
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
