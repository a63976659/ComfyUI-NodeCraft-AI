"""
数据迁移框架 — 版本化数据模型变更管理

使用方式：
    from 后端.数据迁移 import 执行迁移
    await 执行迁移()  # 启动时调用，自动执行未应用的迁移

注册新迁移：
    @注册迁移(版本号=2, 描述="为会话表补充新字段")
    async def _迁移_002():
        ...
"""
import json
import logging
from pathlib import Path
from datetime import datetime

logger = logging.getLogger("NodeCraftAI.数据迁移")

_数据根 = Path(__file__).resolve().parent.parent / "数据"
_版本文件 = _数据根 / "migration_version.json"


def _读取当前版本() -> int:
    """读取已应用的最高迁移版本号"""
    if _版本文件.exists():
        try:
            data = json.loads(_版本文件.read_text("utf-8"))
            return int(data.get("version", 0))
        except (json.JSONDecodeError, KeyError, ValueError, OSError):
            pass
    return 0


def _写入版本(版本号: int) -> None:
    """持久化当前迁移版本"""
    _版本文件.parent.mkdir(parents=True, exist_ok=True)
    _版本文件.write_text(
        json.dumps(
            {"version": 版本号, "updated_at": datetime.now().isoformat()},
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )


# ─── 迁移注册表 ────────────────────────────────────────────
_迁移列表: list[dict] = []


def 注册迁移(版本号: int, 描述: str):
    """装饰器：注册一个迁移函数。

    Args:
        版本号: 单调递增的整数；同一版本号只允许注册一次。
        描述: 简短的中文描述，用于日志输出。
    """
    def decorator(fn):
        # 防止同一版本号被重复注册
        for m in _迁移列表:
            if m["version"] == 版本号:
                raise ValueError(f"迁移版本号 {版本号} 已被注册：{m['description']}")
        _迁移列表.append({
            "version": int(版本号),
            "description": 描述,
            "up": fn,
        })
        return fn
    return decorator


async def 执行迁移() -> None:
    """执行所有未应用的迁移（按版本号升序）。

    - 每个迁移成功后立即写入版本文件，避免中途失败重跑已成功的步骤；
    - 同步函数与 async 函数都支持；
    - 任一迁移失败将抛出 RuntimeError，由调用方决定是否中断启动。
    """
    当前版本 = _读取当前版本()
    待执行 = sorted(
        [m for m in _迁移列表 if m["version"] > 当前版本],
        key=lambda m: m["version"],
    )

    if not 待执行:
        logger.debug(f"数据迁移：当前版本 {当前版本}，无待执行迁移")
        return

    logger.info(f"数据迁移：当前版本 {当前版本}，待执行 {len(待执行)} 个迁移")

    for 迁移 in 待执行:
        try:
            logger.info(f"  执行迁移 v{迁移['version']}: {迁移['description']}")
            result = 迁移["up"]()
            # 支持异步迁移函数
            if hasattr(result, "__await__"):
                await result
            _写入版本(迁移["version"])
            logger.info(f"  ✓ 迁移 v{迁移['version']} 完成")
        except Exception as e:
            logger.error(f"  ✗ 迁移 v{迁移['version']} 失败: {e}", exc_info=True)
            raise RuntimeError(f"数据迁移失败于 v{迁移['version']}: {e}") from e


# ─── 示例迁移（保留作参考） ─────────────────────────────────
@注册迁移(版本号=1, 描述="FAQ条目添加author字段")
def _迁移_001():
    """为所有已有 FAQ 条目补充 author 字段默认值。"""
    faq_dir = _数据根 / "踩坑记录"
    if not faq_dir.exists():
        return
    for f in faq_dir.glob("*.json"):
        if f.name.endswith(".deleted.json"):
            continue
        try:
            data = json.loads(f.read_text("utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        if isinstance(data, dict) and "author" not in data:
            data["author"] = "anonymous"
            try:
                f.write_text(
                    json.dumps(data, ensure_ascii=False, indent=2),
                    encoding="utf-8",
                )
            except OSError:
                pass
