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
import shutil
from datetime import datetime
from pathlib import Path

from .日志配置 import 获取日志器

logger = 获取日志器(__name__)

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


# ─── 阶段三：会话存储迁移至 SQLite ───────────────────────────
@注册迁移(版本号=2, 描述="会话JSON文件迁移至SQLite（数据/会话.db）")
def _迁移_002():
    """将 数据/会话/*.json 逐个导入 SQLite，成功后将 JSON 移入备份目录。

    行为约束：
    - 幂等：版本机制保证正常只跑一次；即使重复执行，已在库中的会话
      会被跳过（不覆盖），已备份的 JSON 不会重复处理；
    - 空数据兼容：全新安装无任何会话 JSON 时直接返回（仅建空库）；
    - 单文件失败：记日志并跳过（原 JSON 原地保留），不中断整体迁移；
    - 备份：导入成功的 JSON 移动到 数据/会话_backup_json/（不删除），
      旧的 会话索引.json 也一并移入备份目录（SQLite 方案下已废弃）。

    回滚预案（回到 JSON 文件方案）：
    1. 删除 数据/会话.db（连同 .db-wal / .db-shm）；
    2. 将 数据/会话_backup_json/ 下的 *.json 移回 数据/会话/；
    3. 将 数据/migration_version.json 的 version 回退为 1；
    4. 代码回退到 JSON 存储版本（会话索引.json 会自愈重建）。
    """
    from .会话存储SQLite import get_db_path, 保存会话, 会话是否存在

    会话目录 = _数据根 / "会话"
    json文件列表 = sorted(会话目录.glob("*.json")) if 会话目录.exists() else []

    db已存在 = get_db_path().exists()
    if not json文件列表:
        # 空数据（全新安装）：无需导入，后续读写时自动建库
        logger.info("  无存量会话 JSON 文件，跳过导入")
        return
    if db已存在:
        # 库已存在（异常情况：如手动回退过版本号）：仍逐个导入，
        # 但 会话是否存在 守卫会跳过库中已有的会话，不覆盖更新数据
        logger.warning("  会话.db 已存在，仅导入库中缺失的会话（不覆盖）")

    备份目录 = _数据根 / "会话_backup_json"
    备份目录.mkdir(parents=True, exist_ok=True)

    成功数 = 0
    跳过数 = 0
    for json文件 in json文件列表:
        try:
            data = json.loads(json文件.read_text(encoding="utf-8"))
            if not isinstance(data, dict) or not data.get("id"):
                raise ValueError("结构异常或缺少 id 字段")
            if 会话是否存在(data["id"]):
                logger.info("  会话已在库中，跳过导入: %s", json文件.name)
            else:
                保存会话(data)
            # 导入成功：移入备份目录（不删除，作为回滚手段）
            目标 = 备份目录 / json文件.name
            if 目标.exists():
                目标.unlink()  # 重复执行时旧备份让位，保留最新一份
            shutil.move(str(json文件), str(目标))
            成功数 += 1
        except Exception as e:
            # 单文件失败：跳过并记日志（原文件原地保留），不中断整体
            跳过数 += 1
            logger.warning("  会话文件导入失败，已跳过: %s (%s)", json文件.name, e)

    # 旧索引文件随 JSON 方案一并废弃，移入备份目录
    索引文件 = _数据根 / "会话索引.json"
    if 索引文件.exists():
        try:
            目标 = 备份目录 / 索引文件.name
            if 目标.exists():
                目标.unlink()
            shutil.move(str(索引文件), str(目标))
        except OSError as e:
            logger.warning("  会话索引.json 归档失败（不影响迁移）: %s", e)

    logger.info(
        "  会话迁移完成：成功 %d 个，跳过 %d 个，备份目录: %s",
        成功数, 跳过数, 备份目录,
    )


# ─── 阶段四：API 配置迁移至多配置方案列表 ──────────────
@注册迁移(版本号=3, 描述="API配置迁移至多配置方案列表（api_profiles）")
def _迁移_003():
    """将设置中已有的顶层 API 配置（base_url/api_key/model_name）转为
    api_profiles 列表中的第一套配置方案，并标记为当前激活配置。

    行为约束：
    - 幂等：api_profiles 已有内容时直接跳过；
    - 空配置兼容：未填过 api_key 且未填模型名时无需创建，直接返回；
    - 加解密由 load_settings/save_settings 统一处理，迁移不碰密文。
    """
    import uuid

    from .文件读写操作 import load_settings, save_settings

    settings = load_settings()
    if settings.get("api_profiles"):
        logger.info("  api_profiles 已存在，跳过")
        return
    if not (settings.get("api_key") or settings.get("model_name")):
        logger.info("  无已配置的 API 信息，跳过")
        return

    配置ID = uuid.uuid4().hex[:8]
    配置 = {
        "id": 配置ID,
        "name": settings.get("model_name") or "默认配置",
        "api_provider": settings.get("api_provider", "custom"),
        "base_url": settings.get("base_url", ""),
        "api_key": settings.get("api_key", ""),
        "model_name": settings.get("model_name", ""),
    }
    save_settings({"api_profiles": [配置], "active_api_profile_id": 配置ID})
    logger.info("  已将现有 API 配置转为配置方案: %s", 配置["name"])
