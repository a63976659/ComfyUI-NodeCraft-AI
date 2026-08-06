"""
并发控制模块 — 后台任务管理与会话版本冲突检测

提供：
- BackgroundTaskManager：管理后台异步任务的生命周期
- 会话版本号机制：检测和防止并发编辑冲突
"""
import asyncio
import logging
from datetime import datetime

logger = logging.getLogger("并发控制")


# ─── 并发冲突检测：基于版本号的乐观锁 ─────────────────

def _检查会话版本(session_data: dict, expected_version: int = None) -> bool:
    """检查会话版本是否一致，不一致时返回 False

    用于检测并发写入冲突：若其他请求已修改会话（版本号递增），
    当前请求应重新加载会话数据或返回 409。
    """
    try:
        current_version = session_data.get("_version", 0)
        if expected_version is not None and current_version != expected_version:
            return False
    except Exception:
        pass
    return True


def _递增会话版本(session_data: dict):
    """保存前递增版本号，供后续并发冲突检测使用

    此函数是所有会话内容变更保存前的统一入口，顺带刷新 updated_at，
    保证聊天/工具执行等路径下“最近活跃时间”不会长期停留在创建时刻。
    """
    try:
        session_data["_version"] = session_data.get("_version", 0) + 1
        session_data["updated_at"] = datetime.now().isoformat(timespec="seconds")
    except Exception:
        pass


class _BackgroundTaskManager:
    """后台任务生命周期管理：上限控制、超时保护、失败日志"""

    def __init__(self, max_tasks: int = 100, task_timeout: float = 300.0):
        self._tasks: set = set()
        self._max_tasks = max_tasks
        self._task_timeout = task_timeout

    def create_task(self, coro, name: str = "unnamed"):
        """创建受管理的后台任务"""
        if len(self._tasks) >= self._max_tasks:
            logger.warning(f"[后台任务] 任务数已达上限({self._max_tasks})，拒绝新任务: {name}")
            return None

        async def _wrapped():
            try:
                await asyncio.wait_for(coro, timeout=self._task_timeout)
            except asyncio.TimeoutError:
                logger.warning(f"[后台任务] {name} 超时({self._task_timeout}s)")
            except asyncio.CancelledError:
                pass
            except Exception as e:
                logger.error(f"[后台任务] {name} 失败: {e}")

        task = asyncio.create_task(_wrapped())
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return task

    async def shutdown(self):
        """取消所有未完成的后台任务"""
        for task in list(self._tasks):
            if not task.done():
                task.cancel()
        self._tasks.clear()


_后台任务管理器 = _BackgroundTaskManager(max_tasks=100, task_timeout=300.0)


def _创建后台任务(coro, name: str = "unnamed"):
    """创建后台任务并存储引用（兼容旧调用签名）"""
    return _后台任务管理器.create_task(coro, name=name)
