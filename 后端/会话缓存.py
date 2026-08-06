"""会话数据 LRU 缓存，避免频繁磁盘 I/O"""
import asyncio
import logging
import time

logger = logging.getLogger("nodecraft_ai.会话缓存")


class SessionLRUCache:
    """线程安全的异步 LRU 缓存，用于会话数据热存储。

    - 容量上限 max_size（默认 50 个会话）
    - TTL 过期淘汰（默认 30 分钟，写穿透策略下仅作兜底）
    - 写穿透：保存成功后用最新 session_data 回填缓存（而非清空），
      put 带 _version 版本守卫，防止并发下旧数据覆盖新数据
    - asyncio.Lock 保护并发安全
    """

    def __init__(self, max_size: int = 50, ttl_seconds: float = 1800.0):
        self._cache: dict = {}  # session_id -> (data, timestamp)
        self._access_order: list = []  # LRU 顺序
        self._max_size = max_size
        self._ttl = ttl_seconds
        self._lock = asyncio.Lock()
        self._hits = 0
        self._misses = 0

    async def get(self, session_id: str):
        """获取缓存的会话数据，返回 None 表示未命中。"""
        async with self._lock:
            if session_id not in self._cache:
                self._misses += 1
                return None
            data, timestamp = self._cache[session_id]
            if time.time() - timestamp > self._ttl:
                # TTL 过期
                del self._cache[session_id]
                if session_id in self._access_order:
                    self._access_order.remove(session_id)
                self._misses += 1
                return None
            # 更新访问顺序（移到末尾）
            if session_id in self._access_order:
                self._access_order.remove(session_id)
            self._access_order.append(session_id)
            self._hits += 1
            return data

    async def put(self, session_id: str, data):
        """存储会话数据到缓存（写穿透：保存后用最新数据回填）。

        版本守卫：若缓存中已存在 _version 更大的数据，跳过本次写入，
        防止并发场景下旧版本数据覆盖新版本造成脏读。
        """
        async with self._lock:
            if session_id in self._cache:
                cached_data, _ = self._cache[session_id]
                try:
                    cached_version = cached_data.get("_version", 0)
                    new_version = data.get("_version", 0)
                except AttributeError:
                    cached_version, new_version = 0, 0
                if new_version < cached_version:
                    logger.debug(
                        f"[会话缓存] 跳过旧版本回填 {session_id}: "
                        f"new={new_version} < cached={cached_version}"
                    )
                    return
                if session_id in self._access_order:
                    self._access_order.remove(session_id)
            elif len(self._cache) >= self._max_size:
                # 淘汰最久未访问的
                if self._access_order:
                    lru_id = self._access_order.pop(0)
                    self._cache.pop(lru_id, None)
            self._cache[session_id] = (data, time.time())
            self._access_order.append(session_id)

    async def invalidate(self, session_id: str):
        """使指定会话的缓存失效（仅在删除会话等数据不复存在时调用；
        普通写入请改用 put 写穿透回填）。"""
        async with self._lock:
            self._cache.pop(session_id, None)
            if session_id in self._access_order:
                self._access_order.remove(session_id)

    @property
    def stats(self) -> dict:
        """返回缓存统计信息。"""
        total = self._hits + self._misses
        return {
            "size": len(self._cache),
            "max_size": self._max_size,
            "hits": self._hits,
            "misses": self._misses,
            "hit_rate": f"{self._hits / total * 100:.1f}%" if total > 0 else "N/A",
        }


# 全局单例
会话缓存 = SessionLRUCache(max_size=50, ttl_seconds=1800.0)
