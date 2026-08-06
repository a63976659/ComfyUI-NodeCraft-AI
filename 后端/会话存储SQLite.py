"""会话存储 SQLite 后端 — 替换「单会话单 JSON 文件」方案

设计目标：
1. 根治写放大：追加一条消息只 INSERT 一行，不再重写整个会话文件；
2. 列表查询走 SQL（ORDER BY created_at DESC），取代 会话索引.json 机制；
3. 对上层完全透明：文件读写操作.py 的五个会话函数签名与返回结构不变。

─── 表结构 ────────────────────────────────────────────────────
sessions:
    id            TEXT PRIMARY KEY   会话 UUID
    title         TEXT               会话标题
    type          TEXT               界面类型 develop/optimize/visualize
    plugin_folder TEXT               关联插件文件夹
    created_at    TEXT               ISO 时间串（列表排序键）
    updated_at    TEXT               ISO 时间串（空串=原数据无此字段）
    version       INTEGER            并发乐观锁 _version（0=原数据无此字段）
    extra         TEXT(JSON)         未知/低频字段兜底（如 _pending_pattern_hint），防丢失
messages:
    session_id    TEXT               所属会话
    seq           INTEGER            消息在会话内的序号（0 起，即数组索引）
    role / content / timestamp       消息核心字段
    extra         TEXT(JSON)         attachments 等扩展字段兜底
    PRIMARY KEY (session_id, seq)    复合主键自带 (session_id, seq) 索引

─── 连接策略 ──────────────────────────────────────────────────
调用方通过 asyncio.to_thread 在线程池工作线程中执行本模块函数，因此采用
threading.local 每线程一个连接（而非单连接+锁）：
- 线程池线程数量少且长生命周期，连接可复用，无跨线程共享连接的竞态；
- WAL 模式：读写不互斥，多个读线程可与单个写线程并发；
- busy_timeout=5000ms：写-写冲突时自动重试等待而非立即抛 database is locked；
- synchronous=NORMAL：WAL 下兼顾持久性与写入性能。
所有写操作使用 `with conn:` 事务化（成功提交、异常回滚）。

─── 回滚预案 ──────────────────────────────────────────────────
若需回退到 JSON 文件方案（详见 后端/数据迁移.py 的 _迁移_002）：
1. 删除 数据/会话.db（连同 会话.db-wal / 会话.db-shm）；
2. 将 数据/会话_backup_json/ 下的 *.json 移回 数据/会话/；
3. 将 数据/migration_version.json 的 version 回退为 1；
4. 代码回退到 JSON 存储版本（旧 会话索引.json 机制会自愈重建）。
"""
import json
import sqlite3
import threading
from pathlib import Path

from .日志配置 import 获取日志器
from .系统环境映射 import get_plugin_root

logger = 获取日志器(__name__)

# 会话字典的核心字段（映射到 sessions 表专列；其余字段序列化进 extra 列）
_会话核心字段 = ("id", "title", "type", "plugin_folder", "created_at",
                 "updated_at", "_version", "messages")
# 消息字典的核心字段（映射到 messages 表专列；其余如 attachments 进 extra 列）
_消息核心字段 = ("role", "content", "timestamp")

# 每线程一个连接（asyncio.to_thread 线程池线程长生命周期，连接复用）
_线程本地 = threading.local()


def get_db_path() -> Path:
    """获取会话数据库文件路径 (插件根目录/数据/会话.db)"""
    data_dir = Path(get_plugin_root()) / "数据"
    data_dir.mkdir(parents=True, exist_ok=True)
    return data_dir / "会话.db"


_建表SQL = """
CREATE TABLE IF NOT EXISTS sessions (
    id            TEXT PRIMARY KEY,
    title         TEXT NOT NULL DEFAULT '',
    type          TEXT NOT NULL DEFAULT 'develop',
    plugin_folder TEXT NOT NULL DEFAULT '',
    created_at    TEXT NOT NULL DEFAULT '',
    updated_at    TEXT NOT NULL DEFAULT '',
    version       INTEGER NOT NULL DEFAULT 0,
    extra         TEXT NOT NULL DEFAULT '{}'
);
CREATE TABLE IF NOT EXISTS messages (
    session_id    TEXT NOT NULL,
    seq           INTEGER NOT NULL,
    role          TEXT NOT NULL DEFAULT '',
    content       TEXT NOT NULL DEFAULT '',
    timestamp     TEXT NOT NULL DEFAULT '',
    extra         TEXT NOT NULL DEFAULT '{}',
    PRIMARY KEY (session_id, seq)
);
CREATE INDEX IF NOT EXISTS idx_sessions_created_at ON sessions(created_at DESC);
"""


def _获取连接() -> sqlite3.Connection:
    """获取当前线程的 SQLite 连接（不存在则创建并初始化 PRAGMA/建表）"""
    conn = getattr(_线程本地, "conn", None)
    if conn is not None:
        return conn
    conn = sqlite3.connect(str(get_db_path()), timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.executescript(_建表SQL)
    _线程本地.conn = conn
    return conn


# ─── 字典 ⇄ 行 转换 ────────────────────────────────────────────

def _dumps(obj) -> str:
    """extra 列统一序列化（sort_keys 保证增量对比时字符串稳定）"""
    return json.dumps(obj, ensure_ascii=False, sort_keys=True)


def _拆分会话(data: dict) -> tuple:
    """会话 dict → sessions 表行元组（核心字段类型异常时降级进 extra 防丢失）"""
    extra = {k: v for k, v in data.items() if k not in _会话核心字段}

    def _取文本(key, 默认=""):
        值 = data.get(key, 默认)
        if not isinstance(值, str):
            extra[key] = 值
            return 默认
        return 值

    version = data.get("_version", 0)
    if not isinstance(version, int) or isinstance(version, bool):
        extra["_version"] = version
        version = 0

    return (
        data["id"],
        _取文本("title"),
        _取文本("type", "develop"),
        _取文本("plugin_folder"),
        _取文本("created_at"),
        _取文本("updated_at"),
        version,
        _dumps(extra) if extra else "{}",
    )


def _行转会话(row) -> dict:
    """sessions 表行 → 会话 dict（不含 messages，由调用方补充）

    updated_at/_version 仅在有值时还原为字段，保持与原 JSON 结构一致
    （新建会话本就没有这两个字段）。
    """
    data = {
        "id": row["id"],
        "title": row["title"],
        "created_at": row["created_at"],
        "plugin_folder": row["plugin_folder"],
        "type": row["type"],
    }
    if row["updated_at"]:
        data["updated_at"] = row["updated_at"]
    if row["version"]:
        data["_version"] = row["version"]
    try:
        extra = json.loads(row["extra"] or "{}")
        if isinstance(extra, dict):
            data.update(extra)
    except (json.JSONDecodeError, TypeError):
        logger.warning("会话 extra 列解析失败 [%s]，已忽略", row["id"])
    return data


def _拆分消息(msg: dict) -> tuple:
    """消息 dict → messages 表行元组（不含 session_id/seq）"""
    extra = {k: v for k, v in msg.items() if k not in _消息核心字段}
    核心值 = []
    for key in _消息核心字段:
        值 = msg.get(key, "")
        if not isinstance(值, str):
            extra[key] = 值  # 非字符串核心字段降级进 extra，load 时覆盖还原
            值 = ""
        核心值.append(值)
    return (*核心值, _dumps(extra) if extra else "{}")


def _行转消息(row) -> dict:
    """messages 表行 → 消息 dict"""
    msg = {"role": row["role"], "content": row["content"]}
    if row["timestamp"]:
        msg["timestamp"] = row["timestamp"]
    try:
        extra = json.loads(row["extra"] or "{}")
        if isinstance(extra, dict):
            msg.update(extra)
    except (json.JSONDecodeError, TypeError):
        logger.warning("消息 extra 列解析失败，已忽略")
    return msg


# ─── 会话 CRUD（供 文件读写操作.py 委托调用） ────────────────────

def 加载会话(session_id: str):
    """加载完整会话数据（含 messages），不存在返回 None"""
    conn = _获取连接()
    row = conn.execute(
        "SELECT * FROM sessions WHERE id = ?", (session_id,)
    ).fetchone()
    if row is None:
        return None
    data = _行转会话(row)
    data["messages"] = [
        _行转消息(r) for r in conn.execute(
            "SELECT role, content, timestamp, extra FROM messages "
            "WHERE session_id = ? ORDER BY seq", (session_id,)
        )
    ]
    return data


def 保存会话(session_data: dict):
    """保存会话（增量写入，upsert 语义）

    消息增量策略：
    1. 读出库中该会话全部消息行（内存读，远比 JSON 全文件重写廉价），
       与新消息列表逐条对比（对比序列化后的行元组），定位首个差异位置；
    2. 差异起点之前的行保持不动；DELETE seq >= 差异起点 的旧行，
       再批量 INSERT 新行 —— 天然覆盖三类场景：
       - 纯追加（聊天）：差异起点=库中行数，只 INSERT 新增消息；
       - 截断+追加（编辑重生成 truncate_at）：前缀相同，删尾部再插新尾部；
       - 无变化（仅改标题等元数据）：不动 messages 表；
    3. 会话元数据单行 UPSERT（单行写，代价可忽略）。
    整个过程在一个事务内完成，异常自动回滚。
    """
    sid = session_data["id"]
    新消息行 = [_拆分消息(m) for m in session_data.get("messages", []) or []]
    会话行 = _拆分会话(session_data)

    conn = _获取连接()
    with conn:  # 事务：提交或整体回滚
        库中行 = conn.execute(
            "SELECT role, content, timestamp, extra FROM messages "
            "WHERE session_id = ? ORDER BY seq", (sid,)
        ).fetchall()

        重叠数 = min(len(库中行), len(新消息行))
        差异起点 = 重叠数
        for i in range(重叠数):
            if tuple(库中行[i]) != 新消息行[i]:
                差异起点 = i
                break

        if 差异起点 < len(库中行):
            # 消息被截断/修改：删除差异起点及之后的旧行
            conn.execute(
                "DELETE FROM messages WHERE session_id = ? AND seq >= ?",
                (sid, 差异起点),
            )
        if 差异起点 < len(新消息行):
            conn.executemany(
                "INSERT INTO messages (session_id, seq, role, content, timestamp, extra) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                [(sid, seq, *新消息行[seq])
                 for seq in range(差异起点, len(新消息行))],
            )

        conn.execute(
            "INSERT INTO sessions (id, title, type, plugin_folder, created_at, "
            "updated_at, version, extra) VALUES (?, ?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(id) DO UPDATE SET "
            "title=excluded.title, type=excluded.type, "
            "plugin_folder=excluded.plugin_folder, created_at=excluded.created_at, "
            "updated_at=excluded.updated_at, version=excluded.version, "
            "extra=excluded.extra",
            会话行,
        )


def 删除会话(session_id: str) -> bool:
    """删除会话及其全部消息，返回会话是否存在（与旧文件删除语义一致）"""
    conn = _获取连接()
    with conn:
        cur = conn.execute("DELETE FROM sessions WHERE id = ?", (session_id,))
        conn.execute("DELETE FROM messages WHERE session_id = ?", (session_id,))
        return cur.rowcount > 0


def 查询会话摘要列表(session_type=None) -> list:
    """查询会话摘要列表（不含 messages），按 created_at 倒序

    返回结构与旧 load_sessions_list / _会话摘要 完全一致：
    id / title / created_at / updated_at / plugin_folder / type 六个键。
    """
    conn = _获取连接()
    sql = ("SELECT id, title, created_at, updated_at, plugin_folder, type "
           "FROM sessions")
    参数: tuple = ()
    if session_type is not None:
        sql += " WHERE type = ?"
        参数 = (session_type,)
    sql += " ORDER BY created_at DESC"
    return [
        {
            "id": r["id"],
            "title": r["title"],
            "created_at": r["created_at"],
            "updated_at": r["updated_at"],
            "plugin_folder": r["plugin_folder"],
            "type": r["type"],
        }
        for r in conn.execute(sql, 参数)
    ]


def 会话是否存在(session_id: str) -> bool:
    """检查会话是否已在库中（迁移导入时防止覆盖更新的数据）"""
    conn = _获取连接()
    row = conn.execute(
        "SELECT 1 FROM sessions WHERE id = ?", (session_id,)
    ).fetchone()
    return row is not None
