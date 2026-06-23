"""踩坑记录 - FAQ 管理系统

提供本地 JSON 存储的 FAQ CRUD 接口，并在创建/更新/删除时
通过 魔搭同步 模块标记文件待同步到云端数据集。
"""
import asyncio
import json
import time
import uuid
from dataclasses import dataclass, field, asdict
from datetime import datetime
from pathlib import Path
from typing import List, Optional, Dict, Any

from aiohttp import web

from .系统环境映射 import get_plugin_root
from .魔搭同步 import mark_dirty as _cloud_mark_dirty
from .日志配置 import 获取日志器

logger = 获取日志器("踩坑记录")

# M5: 错误消息脱敏 - 通用服务器错误文案
_服务器内部错误 = "服务器内部错误，请稍后重试"


# ─── 数据模型 ─────────────────────────────────────────────

@dataclass
class FaqEntry:
    """踩坑记录条目"""
    id: str
    title: str
    problem: str
    solution: str
    tags: List[str] = field(default_factory=list)
    created_at: str = ""
    updated_at: str = ""
    sync_status: str = "pending"   # pending | synced | error
    author: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "FaqEntry":
        return cls(
            id=data.get("id", ""),
            title=data.get("title", ""),
            problem=data.get("problem", ""),
            solution=data.get("solution", ""),
            tags=data.get("tags", []) or [],
            created_at=data.get("created_at", ""),
            updated_at=data.get("updated_at", ""),
            sync_status=data.get("sync_status", "pending"),
            author=data.get("author", ""),
        )


# ─── 存储路径 ─────────────────────────────────────────────

def _get_faq_dir() -> Path:
    """FAQ 存储目录: {plugin_dir}/数据/踩坑记录/"""
    faq_dir = get_plugin_root() / "数据" / "踩坑记录"
    faq_dir.mkdir(parents=True, exist_ok=True)
    return faq_dir


def _get_faq_file(faq_id: str) -> Path:
    return _get_faq_dir() / f"{faq_id}.json"


# ─── 工具函数 ─────────────────────────────────────────────

def _now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime())


def _load_faq(faq_id: str) -> Optional[FaqEntry]:
    f = _get_faq_file(faq_id)
    if not f.exists():
        return None
    try:
        with open(f, "r", encoding="utf-8") as fp:
            return FaqEntry.from_dict(json.load(fp))
    except Exception as e:
        logger.exception(f"加载失败 {faq_id}: {e}")
        return None


def _save_faq(entry: FaqEntry) -> None:
    """原子写入 + 标记云端待同步"""
    f = _get_faq_file(entry.id)
    tmp = f.with_suffix(".json.tmp")
    with open(tmp, "w", encoding="utf-8") as fp:
        json.dump(entry.to_dict(), fp, ensure_ascii=False, indent=2)
    tmp.replace(f)

    # 标记需要云端同步
    try:
        _cloud_mark_dirty(str(f))
    except Exception as e:
        logger.exception(f"标记同步失败 {entry.id}: {e}")


def _list_all_faqs() -> List[FaqEntry]:
    faq_dir = _get_faq_dir()
    entries: List[FaqEntry] = []
    for f in faq_dir.glob("*.json"):
        # 跳过墓碑文件（.deleted.json）
        if f.name.endswith(".deleted.json"):
            continue
        try:
            with open(f, "r", encoding="utf-8") as fp:
                entries.append(FaqEntry.from_dict(json.load(fp)))
        except Exception as e:
            logger.warning(f"跳过损坏文件 {f.name}: {e}")
    return entries


def _delete_faq_with_tombstone(faq_id: str) -> bool:
    """墓碑式删除：创建 .deleted.json 标记文件，然后删除原始文件

    返回值：是否成功（原文件与墓碑文件均不存在时返回 False）
    """
    faq_dir = _get_faq_dir()
    original_file = faq_dir / f"{faq_id}.json"
    tombstone_file = faq_dir / f"{faq_id}.deleted.json"

    if not original_file.exists() and not tombstone_file.exists():
        return False

    # 创建墓碑文件
    tombstone_data = {
        "id": faq_id,
        "deleted_at": datetime.now().strftime("%Y-%m-%dT%H:%M:%S"),
        "tombstone": True,
    }
    # 原子写入墓碑文件
    tmp = tombstone_file.with_suffix(".json.tmp")
    with open(tmp, "w", encoding="utf-8") as fp:
        json.dump(tombstone_data, fp, ensure_ascii=False, indent=2)
    tmp.replace(tombstone_file)

    # 标记墓碑文件待同步（延迟导入避免循环依赖）
    try:
        from .魔搭同步 import mark_dirty
        mark_dirty(str(tombstone_file))
    except Exception as e:
        logger.exception(f"标记墓碑同步失败 {faq_id}: {e}")

    # 删除原始文件
    if original_file.exists():
        try:
            original_file.unlink()
        except Exception as e:
            logger.exception(f"删除原始文件失败 {faq_id}: {e}")

    return True


def _matches_keyword(entry: FaqEntry, kw: str) -> bool:
    kw = kw.lower()
    if kw in entry.title.lower():
        return True
    if kw in entry.problem.lower():
        return True
    if kw in entry.solution.lower():
        return True
    if any(kw in (t or "").lower() for t in entry.tags):
        return True
    return False


def _标记已同步_同步(faq_id: str, status: str):
    """P1-1: 将原同步逻辑抽取为完全同步子过程，供路由层以 asyncio.to_thread 调用"""
    entry = _load_faq(faq_id)
    if entry is None:
        return None
    entry.sync_status = status if status in ("pending", "synced", "error") else "synced"
    entry.updated_at = _now_iso()
    # 直接落盘，但避免再次标记为 dirty 造成循环
    f = _get_faq_file(entry.id)
    tmp = f.with_suffix(".json.tmp")
    with open(tmp, "w", encoding="utf-8") as fp:
        json.dump(entry.to_dict(), fp, ensure_ascii=False, indent=2)
    tmp.replace(f)
    return entry

# ─── 路由处理器 ───────────────────────────────────────────

async def 创建记录(request: web.Request) -> web.Response:
    """POST /ai-coder/faq/create"""
    try:
        from .路由公共 import _check_auth
        auth_resp = await _check_auth(request)
        if auth_resp is not None:
            return auth_resp
        author = request.get('user', {}).get('sub', 'anonymous')

        data = await request.json()
        title = (data.get("title") or "").strip()
        problem = (data.get("problem") or "").strip()
        solution = (data.get("solution") or "").strip()
        tags = data.get("tags") or []

        if not title:
            return web.json_response({"success": False, "error": "title 不能为空"}, status=400)
        if not problem:
            return web.json_response({"success": False, "error": "problem 不能为空"}, status=400)

        now = _now_iso()
        entry = FaqEntry(
            id=str(uuid.uuid4()),
            title=title,
            problem=problem,
            solution=solution,
            tags=list(tags) if isinstance(tags, list) else [],
            created_at=now,
            updated_at=now,
            sync_status="pending",
            author=author,
        )
        # P1-1：同步文件 I/O 放入线程池
        await asyncio.to_thread(_save_faq, entry)
        # 创建后刷新 BM25 检索索引（延迟导入避免循环依赖）
        try:
            from 智能体.工具路由器 import invalidate_bm25_cache
            invalidate_bm25_cache()
        except Exception:
            pass
        return web.json_response({"success": True, "data": entry.to_dict()})
    except Exception as e:
        logger.error(f"创建踩坑记录异常: {e}", exc_info=True)
        return web.json_response({"success": False, "error": _服务器内部错误}, status=500)


async def 列表查询(request: web.Request) -> web.Response:
    """GET /ai-coder/faq/list?page=1&page_size=20&q=keyword&tag=xxx"""
    try:
        page = max(int(request.query.get("page", 1)), 1)
        page_size = max(min(int(request.query.get("page_size", 20)), 200), 1)
        keyword = (request.query.get("q") or "").strip()
        tag = (request.query.get("tag") or "").strip()

        # P1-1：目录遍历与文件读取放入线程池
        entries = await asyncio.to_thread(_list_all_faqs)

        if keyword:
            entries = [e for e in entries if _matches_keyword(e, keyword)]
        if tag:
            entries = [e for e in entries if tag in (e.tags or [])]

        # 按更新时间倒序
        entries.sort(key=lambda e: e.updated_at or e.created_at, reverse=True)

        total = len(entries)
        start = (page - 1) * page_size
        end = start + page_size
        page_items = [e.to_dict() for e in entries[start:end]]

        return web.json_response({
            "success": True,
            "data": {
                "items": page_items,
                "total": total,
                "page": page,
                "page_size": page_size,
            },
        })
    except ValueError:
        return web.json_response({"success": False, "error": "分页参数非法"}, status=400)
    except Exception as e:
        logger.error(f"查询踩坑记录列表异常: {e}", exc_info=True)
        return web.json_response({"success": False, "error": _服务器内部错误}, status=500)


async def 获取详情(request: web.Request) -> web.Response:
    """GET /ai-coder/faq/{id}"""
    try:
        faq_id = request.match_info.get("id", "")
        if not faq_id:
            return web.json_response({"success": False, "error": "缺少 id"}, status=400)

        # P1-1：同步文件 I/O 放入线程池
        entry = await asyncio.to_thread(_load_faq, faq_id)
        if entry is None:
            return web.json_response({"success": False, "error": "记录不存在"}, status=404)
        return web.json_response({"success": True, "data": entry.to_dict()})
    except Exception as e:
        logger.error(f"获取踩坑记录详情异常: {e}", exc_info=True)
        return web.json_response({"success": False, "error": _服务器内部错误}, status=500)


async def 更新记录(request: web.Request) -> web.Response:
    """PUT /ai-coder/faq/{id}"""
    try:
        faq_id = request.match_info.get("id", "")
        if not faq_id:
            return web.json_response({"success": False, "error": "缺少 id"}, status=400)

        # P1-1：同步文件 I/O 放入线程池
        entry = await asyncio.to_thread(_load_faq, faq_id)
        if entry is None:
            return web.json_response({"success": False, "error": "记录不存在"}, status=404)

        data = await request.json()
        if "title" in data:
            entry.title = (data.get("title") or "").strip()
        if "problem" in data:
            entry.problem = (data.get("problem") or "").strip()
        if "solution" in data:
            entry.solution = (data.get("solution") or "").strip()
        if "tags" in data:
            tags = data.get("tags") or []
            entry.tags = list(tags) if isinstance(tags, list) else []

        entry.updated_at = _now_iso()
        entry.sync_status = "pending"
        # P1-1：同步文件 I/O 放入线程池
        await asyncio.to_thread(_save_faq, entry)
        # 更新后刷新 BM25 检索索引（延迟导入避免循环依赖）
        try:
            from 智能体.工具路由器 import invalidate_bm25_cache
            invalidate_bm25_cache()
        except Exception:
            pass
        return web.json_response({"success": True, "data": entry.to_dict()})
    except Exception as e:
        logger.error(f"更新踩坑记录异常: {e}", exc_info=True)
        return web.json_response({"success": False, "error": _服务器内部错误}, status=500)


async def 删除记录(request: web.Request) -> web.Response:
    """DELETE /ai-coder/faq/{id}——墓碑式删除"""
    try:
        faq_id = request.match_info.get("id", "")
        if not faq_id:
            return web.json_response({"success": False, "error": "缺少 id"}, status=400)

        # P1-1：同步 I/O（写墓碑+删原件）整体放入线程池
        deleted = await asyncio.to_thread(_delete_faq_with_tombstone, faq_id)
        if not deleted:
            return web.json_response({"success": False, "error": "记录不存在"}, status=404)

        # 删除后刷新 BM25 检索索引（延迟导入避免循环依赖）
        try:
            from 智能体.工具路由器 import invalidate_bm25_cache
            invalidate_bm25_cache()
        except Exception:
            pass
        return web.json_response({"success": True, "data": {"id": faq_id, "deleted": True}})
    except Exception as e:
        logger.error(f"删除踩坑记录异常: {e}", exc_info=True)
        return web.json_response({"success": False, "error": _服务器内部错误}, status=500)


async def 标记已同步(request: web.Request) -> web.Response:
    """内部：将指定 id 的 sync_status 置为 synced（供同步引擎回调）"""
    try:
        data = await request.json()
        faq_id = data.get("id", "")
        status = data.get("status", "synced")
        # P1-1：将同步 I/O（读取+写入+重命名）整体放入线程池执行
        entry = await asyncio.to_thread(_标记已同步_同步, faq_id, status)
        if entry is None:
            return web.json_response({"success": False, "error": "记录不存在"}, status=404)
        return web.json_response({"success": True, "data": entry.to_dict()})
    except Exception as e:
        logger.error(f"标记踩坑记录同步状态异常: {e}", exc_info=True)
        return web.json_response({"success": False, "error": _服务器内部错误}, status=500)


# ─── 路由注册 ─────────────────────────────────────────────

def register_踩坑记录路由(routes) -> None:
    """注册 FAQ 管理端点"""
    routes.post("/ai-coder/faq/create")(创建记录)
    routes.get("/ai-coder/faq/list")(列表查询)
    routes.get(r"/ai-coder/faq/{id}")(获取详情)
    routes.put(r"/ai-coder/faq/{id}")(更新记录)
    routes.delete(r"/ai-coder/faq/{id}")(删除记录)
    routes.post("/ai-coder/faq/mark-synced")(标记已同步)
