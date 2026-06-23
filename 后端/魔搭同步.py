"""魔搭同步 - ModelScope Dataset 批量同步引擎

参考 ComfyUI-Ranking 的批量同步机制实现：
- 脏文件集合标记待同步文件
- 30 秒间隔批量提交
- 指数退避重试（60s → 120s → 240s，最大 600s）
- 原子写入：先写临时文件再 rename
"""
import os
import json
import time
import asyncio
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Optional, Set, List, Dict, Any

from aiohttp import web

from .系统环境映射 import get_plugin_root
from .日志配置 import 获取日志器

logger = 获取日志器("魔搭同步")

# M5: 错误消息脱敏 - 通用服务器错误文案
_服务器内部错误 = "服务器内部错误，请稍后重试"


# ─── 常量配置 ─────────────────────────────────────────────
SYNC_INTERVAL = 30                  # 批量同步间隔（秒）
RETRY_BASE_DELAY = 60               # 指数退避基础延时（秒）
RETRY_MAX_DELAY = 600               # 最大退避延时（秒）
DEFAULT_DATASET_REPO = "NodeCraft-AI/community-data"


# ─── ModelScope 同步引擎 ──────────────────────────────────

class ModelScopeSync:
    """ModelScope Dataset 同步引擎（单例）"""

    def __init__(self, dataset_repo_id: str, token: Optional[str] = None):
        self.dataset_repo_id = dataset_repo_id
        self.token = token or os.environ.get("MODELSCOPE_API_TOKEN", "")

        # 脏文件集合（待同步的相对路径）
        self.dirty_files: Set[str] = set()

        # 同步状态
        self.last_sync_time: Optional[float] = None
        self.last_error: Optional[str] = None
        self.error_count: int = 0
        self.is_syncing: bool = False

        # 退避控制
        self._current_delay: int = RETRY_BASE_DELAY
        self._loop_task: Optional[asyncio.Task] = None
        self._lock = asyncio.Lock()

        # 数据根目录（同步范围）
        self.data_root = get_plugin_root() / "数据"
        self.faq_dir = self.data_root / "踩坑记录"
        self.faq_dir.mkdir(parents=True, exist_ok=True)

        # 知识库（项目根 / 知识库）增量扫描状态
        self.kb_root = get_plugin_root() / "知识库"
        # {rel_posix_path: last_seen_mtime}
        self._kb_mtimes: Dict[str, float] = {}
        self._last_kb_scan_time: Optional[float] = None

    # ─── 公共方法 ────────────────────────────────────────

    def mark_dirty(self, filepath: str) -> None:
        """标记文件需要同步

        filepath 可以为绝对路径或相对插件根的相对路径，
        统一存储为相对 plugin_root 的 POSIX 风格路径。
        """
        try:
            p = Path(filepath)
            if p.is_absolute():
                rel = p.resolve().relative_to(get_plugin_root().resolve())
            else:
                rel = p
            self.dirty_files.add(str(rel).replace("\\", "/"))
        except Exception as e:
            logger.exception(f"mark_dirty 失败 ({filepath}): {e}")

    def 扫描知识库变更(self) -> int:
        """扫描 知识库/ 下的 .md 文件，对 mtime 发生变化的文件调用 mark_dirty

        - 首次调用：仅记录现有 mtime 基线，不触发同步（避免启动时全量上传）
        - 后续调用：只有 mtime > 上次记录才调用 mark_dirty
        - 文件被删除：从状态表中移除（不增量同步删除）

        返回本次新增标记为脏的文件数。
        """
        if not self.kb_root.exists():
            return 0

        is_first_scan = self._last_kb_scan_time is None
        plugin_root = get_plugin_root().resolve()
        seen: Set[str] = set()
        marked = 0

        try:
            for md in self.kb_root.rglob("*.md"):
                if not md.is_file():
                    continue
                try:
                    rel = str(md.resolve().relative_to(plugin_root)).replace("\\", "/")
                except ValueError:
                    continue
                seen.add(rel)
                try:
                    mtime = md.stat().st_mtime
                except OSError:
                    continue

                prev = self._kb_mtimes.get(rel)
                self._kb_mtimes[rel] = mtime
                if is_first_scan:
                    continue
                if prev is None or mtime > prev:
                    self.mark_dirty(rel)
                    marked += 1
        except Exception as e:
            logger.exception(f"知识库扫描异常: {e}")
            return marked

        # 清理已删除的文件记录
        for stale in list(self._kb_mtimes.keys()):
            if stale not in seen:
                self._kb_mtimes.pop(stale, None)

        self._last_kb_scan_time = time.time()
        return marked

    async def sync_now(self) -> Dict[str, Any]:
        """立即执行同步（清空 dirty_files）"""
        async with self._lock:
            if self.is_syncing:
                return {"success": False, "error": "同步进行中，请稍后再试"}

            if not self.dirty_files:
                return {"success": True, "data": {"synced": 0, "message": "无需同步"}}

            if not self.token:
                return {"success": False, "error": "未配置 MODELSCOPE_API_TOKEN"}

            files_snapshot = list(self.dirty_files)
            self.is_syncing = True

            try:
                result = await self._upload_batch(files_snapshot)
                if result["success"]:
                    # 同步成功后清除脏文件标记
                    for f in files_snapshot:
                        self.dirty_files.discard(f)
                    self.last_sync_time = time.time()
                    self.last_error = None
                    self.error_count = 0
                    self._current_delay = RETRY_BASE_DELAY
                    return {
                        "success": True,
                        "data": {
                            "synced": len(files_snapshot),
                            "files": files_snapshot,
                            "timestamp": self.last_sync_time,
                        },
                    }
                else:
                    self.last_error = result.get("error", "未知错误")
                    self.error_count += 1
                    return {"success": False, "error": self.last_error}
            finally:
                self.is_syncing = False

    def get_status(self) -> Dict[str, Any]:
        """获取同步状态"""
        return {
            "last_sync_time": self.last_sync_time,
            "pending_files": sorted(self.dirty_files),
            "pending_count": len(self.dirty_files),
            "errors": self.error_count,
            "last_error": self.last_error,
            "is_syncing": self.is_syncing,
            "dataset_repo_id": self.dataset_repo_id,
            "token_configured": bool(self.token),
            "current_retry_delay": self._current_delay,
        }

    # ─── 后台循环 ────────────────────────────────────────

    async def _sync_loop(self) -> None:
        """后台同步循环：每 SYNC_INTERVAL 秒检查一次脏文件"""
        logger.info(f"启动后台同步循环（间隔 {SYNC_INTERVAL}s）")
        while True:
            try:
                await asyncio.sleep(SYNC_INTERVAL)
                # 增量扫描知识库：mtime 变化的 .md 文件会被标为脏
                try:
                    self.扫描知识库变更()
                except Exception as e:
                    logger.exception(f"知识库扫描失败: {e}")

                # 定期清理过期墓碑（超过 7 天）
                try:
                    self._清理过期墓碑()
                except Exception as e:
                    logger.exception(f"清理过期墓碑异常: {e}")

                if not self.dirty_files:
                    continue
                if not self.token:
                    continue

                result = await self.sync_now()
                if result.get("success"):
                    # 上传成功后自动拉取云端数据，实现双向同步
                    try:
                        pull_result = await self.pull_from_cloud()
                        if pull_result.get("success"):
                            logger.info("自动拉取云端数据完成")
                        else:
                            logger.warning(f"自动拉取失败: {pull_result.get('error')}")
                    except Exception as e:
                        logger.warning(f"自动拉取失败: {e}")
                else:
                    # 失败：进入指数退避
                    backoff = min(self._current_delay, RETRY_MAX_DELAY)
                    logger.warning(f"同步失败，{backoff}s 后重试: {result.get('error')}")
                    await asyncio.sleep(backoff)
                    self._current_delay = min(self._current_delay * 2, RETRY_MAX_DELAY)
            except asyncio.CancelledError:
                logger.info("后台同步循环已停止")
                break
            except Exception as e:
                logger.exception(f"同步循环异常: {e}")

    def start(self) -> None:
        """启动后台同步任务"""
        if self._loop_task is not None and not self._loop_task.done():
            return
        try:
            loop = asyncio.get_event_loop()
            self._loop_task = loop.create_task(self._sync_loop())
        except RuntimeError:
            # 无运行的事件循环时延后挂起
            self._loop_task = None

    def stop(self) -> None:
        if self._loop_task is not None:
            self._loop_task.cancel()
            self._loop_task = None

    # ─── 内部上传逻辑 ────────────────────────────────────

    async def _upload_batch(self, files: List[str]) -> Dict[str, Any]:
        """批量上传文件到 ModelScope Dataset

        使用线程池避免 SDK 阻塞事件循环。
        """
        try:
            return await asyncio.to_thread(self._upload_batch_sync, files)
        except Exception as e:
            return {"success": False, "error": f"上传异常: {e}"}

    def _upload_batch_sync(self, files: List[str]) -> Dict[str, Any]:
        """同步上传实现（在线程中调用）"""
        try:
            # 延迟导入 modelscope，避免插件启动时强依赖
            from modelscope.hub.api import HubApi
        except ImportError:
            return {
                "success": False,
                "error": "未安装 modelscope 包，请执行 pip install modelscope",
            }

        try:
            api = HubApi()
            api.login(self.token)
        except Exception as e:
            return {"success": False, "error": f"ModelScope 登录失败: {e}"}

        plugin_root = get_plugin_root()
        uploaded: List[str] = []
        failed: List[Dict[str, str]] = []

        for rel_path in files:
            local_file = plugin_root / rel_path
            if not local_file.exists():
                failed.append({"file": rel_path, "error": "文件不存在"})
                continue

            try:
                # 原子写入：先写到临时文件再 rename，确保上传源稳定
                tmp_path = self._atomic_snapshot(local_file)
                # 与云端 Space 路径对齐：去除本地 "数据/" 前缀，使 path_in_repo 直接以 "踩坑记录/" 起头
                cloud_path = rel_path[len("数据/"):] if rel_path.startswith("数据/") else rel_path
                # Bug 37 修复：关键上传操作增加指数退避重试
                self._带重试执行(lambda: api.upload_file(
                    path_or_fileobj=str(tmp_path),
                    path_in_repo=cloud_path,
                    repo_id=self.dataset_repo_id,
                    repo_type="dataset",
                    commit_message=f"sync {cloud_path}",
                ))
                uploaded.append(rel_path)
                try:
                    tmp_path.unlink(missing_ok=True)
                except Exception:
                    pass
            except Exception as e:
                failed.append({"file": rel_path, "error": str(e)})

        if failed and not uploaded:
            return {
                "success": False,
                "error": f"全部 {len(failed)} 个文件上传失败",
                "failed": failed,
            }
        return {
            "success": True,
            "uploaded": uploaded,
            "failed": failed,
        }

    @staticmethod
    def _atomic_snapshot(src: Path) -> Path:
        """对源文件做原子快照，返回临时文件路径"""
        with open(src, "rb") as fr:
            content = fr.read()
        fd, tmp_name = tempfile.mkstemp(prefix=".syncsnap_", suffix=src.suffix, dir=str(src.parent))
        try:
            with os.fdopen(fd, "wb") as fw:
                fw.write(content)
            tmp_path = Path(tmp_name)
            return tmp_path
        except Exception:
            try:
                os.close(fd)
            except Exception:
                pass
            raise

    def _带重试执行(self, fn, max_retries: int = 3):
        """带指数退避的同步执行包装（用于 ModelScope SDK 调用）

        仅对超时/连接/5xx 等临时性错误进行重试。
        """
        import time
        last_exc = None
        for attempt in range(max_retries):
            try:
                return fn()
            except Exception as e:
                last_exc = e
                msg = str(e).lower()
                is_transient = any(k in msg for k in (
                    "timeout", "connection", "temporary",
                    "503", "502", "500", "504",
                ))
                if not is_transient or attempt >= max_retries - 1:
                    raise
                wait = 2 ** attempt
                logger.warning(
                    "ModelScope SDK 调用异常: %s，%ds后重试(%d/%d)",
                    e, wait, attempt + 1, max_retries,
                )
                time.sleep(wait)
        raise last_exc

    # ─── 云端拉取逻辑 ──────────────────────────

    async def pull_from_cloud(self) -> Dict[str, Any]:
        """异步拉取云端数据到本地"""
        try:
            result = await asyncio.to_thread(self._pull_from_cloud_sync)
            if result.get("success"):
                logger.info(
                    f"云端拉取完成: pulled={result.get('pulled', 0)}, "
                    f"merged={result.get('merged', 0)}, deleted={result.get('deleted', 0)}, "
                    f"skipped={result.get('skipped', 0)}"
                )
            return result
        except Exception as e:
            logger.exception(f"云端拉取异常: {e}")
            return {"success": False, "error": str(e)}

    def _pull_from_cloud_sync(self) -> Dict[str, Any]:
        """从云端拉取 FAQ 数据到本地（同步执行，在线程中调用）

        处理逻辑：
        - 列出云端 踩坑记录/ 下的所有 .json 文件
        - 墓碑文件（*.deleted.json）→ 删除本地同 id 原件，同步墓碑到本地
        - 普通文件冲突合并：按 updated_at 保留较新版本
        - 如本地已存在墓碑则跳过拉取该记录
        """
        if not self.token:
            return {"success": False, "error": "未配置 MODELSCOPE_API_TOKEN"}

        try:
            from modelscope.hub.api import HubApi
        except ImportError:
            return {
                "success": False,
                "error": "未安装 modelscope 包，请执行 pip install modelscope",
            }

        try:
            api = HubApi()
            api.login(self.token)
        except Exception as e:
            return {"success": False, "error": f"ModelScope 登录失败: {e}"}

        repo_id = self.dataset_repo_id
        faq_dir = get_plugin_root() / "数据" / "踩坑记录"
        faq_dir.mkdir(parents=True, exist_ok=True)

        # 列出云端数据集文件列表（兼容不同版本 API）
        try:
            faq_files = self._list_remote_faq_files(api, repo_id)
        except Exception as e:
            logger.warning(f"列出云端文件失败: {e}")
            return {"success": False, "error": f"列出云端文件失败: {e}"}

        # 构建本地 FAQ title 索引用于去重
        local_titles = set()
        for f in faq_dir.glob("*.json"):
            if f.name.endswith(".deleted.json"):
                continue
            try:
                with open(f, "r", encoding="utf-8") as fp:
                    data = json.load(fp)
                    title = (data.get("title") or "").strip()
                    if title:
                        local_titles.add(title)
            except Exception:
                pass

        pulled = 0
        merged = 0
        deleted = 0
        skipped = 0
        duplicates = 0

        for remote_path in faq_files:
            filename = remote_path.split("/")[-1]

            try:
                local_tmp = self._download_remote_file(api, repo_id, remote_path)
                with open(local_tmp, "r", encoding="utf-8") as fp:
                    remote_data = json.load(fp)
            except Exception as e:
                logger.warning(f"下载云端文件 {remote_path} 失败: {e}")
                skipped += 1
                continue

            # 处理墓碑文件
            if filename.endswith(".deleted.json"):
                faq_id = filename[: -len(".deleted.json")]
                local_faq = faq_dir / f"{faq_id}.json"
                local_tombstone = faq_dir / f"{faq_id}.deleted.json"

                if local_faq.exists():
                    try:
                        local_faq.unlink()
                        deleted += 1
                    except Exception as e:
                        logger.warning(f"删除本地文件 {local_faq} 失败: {e}")

                # 写入本地墓碑（如不存在）
                if not local_tombstone.exists():
                    try:
                        tmp = local_tombstone.with_suffix(".json.tmp")
                        with open(tmp, "w", encoding="utf-8") as fp:
                            json.dump(remote_data, fp, ensure_ascii=False, indent=2)
                        tmp.replace(local_tombstone)
                    except Exception as e:
                        logger.warning(f"写入本地墓碑 {local_tombstone} 失败: {e}")
                continue

            # 处理普通 FAQ 文件
            local_file = faq_dir / filename

            # 如本地已有墓碑，跳过拉取（本地已删除，不重新创建）
            faq_id = filename[: -len(".json")]
            local_tombstone = faq_dir / f"{faq_id}.deleted.json"
            if local_tombstone.exists():
                skipped += 1
                continue

            if local_file.exists():
                # 冲突合并：比较 updated_at 时间戳，保留较新版本
                try:
                    with open(local_file, "r", encoding="utf-8") as fp:
                        local_data = json.load(fp)
                    remote_time = remote_data.get("updated_at", "") or ""
                    local_time = local_data.get("updated_at", "") or ""
                    if remote_time > local_time:
                        tmp = local_file.with_suffix(".json.tmp")
                        with open(tmp, "w", encoding="utf-8") as fp:
                            json.dump(remote_data, fp, ensure_ascii=False, indent=2)
                        tmp.replace(local_file)
                        merged += 1
                    else:
                        skipped += 1
                except Exception as e:
                    logger.warning(f"合并本地文件 {local_file} 失败: {e}")
                    skipped += 1
            else:
                # 本地不存在，且未被墓碑标记
                # 内容去重：检查 title 是否与本地已有记录重复
                remote_title = (remote_data.get("title") or "").strip()
                if remote_title and remote_title in local_titles:
                    logger.info(f"去重跳过(标题重复): {remote_title}")
                    duplicates += 1
                    continue
                # 正常拉取
                try:
                    tmp = local_file.with_suffix(".json.tmp")
                    with open(tmp, "w", encoding="utf-8") as fp:
                        json.dump(remote_data, fp, ensure_ascii=False, indent=2)
                    tmp.replace(local_file)
                    pulled += 1
                    # 新拉取的条目也加入索引（防止本次循环中再次拉取相同 title）
                    if remote_title:
                        local_titles.add(remote_title)
                except Exception as e:
                    logger.warning(f"写入本地文件 {local_file} 失败: {e}")
                    skipped += 1

        return {
            "success": True,
            "pulled": pulled,
            "merged": merged,
            "deleted": deleted,
            "skipped": skipped,
            "duplicates": duplicates,
        }

    @staticmethod
    def _list_remote_faq_files(api, repo_id: str) -> List[str]:
        """列出云端 踩坑记录/ 下的所有 .json 文件路径。

        兼容不同版本的 modelscope HubApi：
        - 优先 list_repo_files
        - 回退 get_dataset_file_list / list_dataset_files
        """
        prefix = "踩坑记录/"

        # 1) 新版 list_repo_files(repo_id, repo_type='dataset')
        if hasattr(api, "list_repo_files"):
            try:
                file_list = api.list_repo_files(repo_id=repo_id, repo_type="dataset")
                names = ModelScopeSync._normalize_file_list(file_list)
                return [n for n in names if n.startswith(prefix) and n.endswith(".json")]
            except TypeError:
                # 可能不接受 repo_type 参数
                try:
                    file_list = api.list_repo_files(repo_id)
                    names = ModelScopeSync._normalize_file_list(file_list)
                    return [n for n in names if n.startswith(prefix) and n.endswith(".json")]
                except Exception:
                    pass
            except Exception:
                pass

        # 2) 旧版 get_dataset_file_list / list_dataset_files
        for method_name in ("get_dataset_file_list", "list_dataset_files"):
            if hasattr(api, method_name):
                try:
                    file_list = getattr(api, method_name)(repo_id)
                    names = ModelScopeSync._normalize_file_list(file_list)
                    return [n for n in names if n.startswith(prefix) and n.endswith(".json")]
                except Exception:
                    continue

        raise RuntimeError("当前 modelscope HubApi 未提供可用的文件列表接口")

    @staticmethod
    def _normalize_file_list(raw) -> List[str]:
        """将 list_repo_files / get_dataset_file_list 返回的多种结构拉平为文件路径列表"""
        if raw is None:
            return []
        # 直接字符串列表
        if isinstance(raw, list) and (not raw or isinstance(raw[0], str)):
            return [s for s in raw if isinstance(s, str)]
        # 列表[dict] 结构 [{Name|name|path: ...}]
        if isinstance(raw, list):
            out: List[str] = []
            for item in raw:
                if isinstance(item, str):
                    out.append(item)
                elif isinstance(item, dict):
                    name = item.get("Path") or item.get("path") or item.get("Name") or item.get("name")
                    if isinstance(name, str):
                        out.append(name)
            return out
        # dict 包裹: {Files: [...]} / {data: {files: [...]}}
        if isinstance(raw, dict):
            for key in ("Files", "files", "data", "Data"):
                if key in raw:
                    return ModelScopeSync._normalize_file_list(raw[key])
        return []

    @staticmethod
    def _download_remote_file(api, repo_id: str, remote_path: str) -> str:
        """下载云端单个文件，返回本地临时路径。兼容多个 API 入口。"""
        # 1) HubApi.download_single_file
        if hasattr(api, "download_single_file"):
            try:
                return api.download_single_file(
                    repo_id=repo_id,
                    file_path=remote_path,
                    repo_type="dataset",
                )
            except TypeError:
                try:
                    return api.download_single_file(repo_id, remote_path)
                except Exception:
                    pass
            except Exception:
                pass

        # 2) modelscope.hub.file_download.dataset_file_download
        try:
            from modelscope.hub.file_download import dataset_file_download  # type: ignore
            return dataset_file_download(
                dataset_id=repo_id,
                file_path=remote_path,
            )
        except Exception:
            pass

        # 3) modelscope.hub.snapshot_download 回退（下载整仓 → 仅拼接路径）
        try:
            from modelscope.hub.snapshot_download import dataset_snapshot_download  # type: ignore
            local_dir = dataset_snapshot_download(repo_id, allow_patterns=[remote_path])
            return str(Path(local_dir) / remote_path)
        except Exception:
            pass

        raise RuntimeError(f"当前 modelscope HubApi 未提供可用的文件下载接口 ({remote_path})")

    # ─── 墓碑清理 ──────────────────────────────────

    def _清理过期墓碑(self) -> int:
        """清理超过 7 天的本地墓碑文件，返回清理数量"""
        faq_dir = get_plugin_root() / "数据" / "踩坑记录"
        if not faq_dir.exists():
            return 0

        cleaned = 0
        now = datetime.now()

        for f in faq_dir.glob("*.deleted.json"):
            try:
                with open(f, "r", encoding="utf-8") as fp:
                    data = json.load(fp)
                deleted_at_str = data.get("deleted_at", "") or ""
                deleted_at = datetime.strptime(deleted_at_str, "%Y-%m-%dT%H:%M:%S")
                if (now - deleted_at).days >= 7:
                    f.unlink()
                    cleaned += 1
            except Exception:
                # 解析失败的墓碑也清理
                try:
                    f.unlink()
                    cleaned += 1
                except Exception:
                    pass

        if cleaned > 0:
            logger.info(f"清理了 {cleaned} 个过期墓碑文件")
        return cleaned


# ─── 单例 ─────────────────────────────────────────────────

_sync_engine: Optional[ModelScopeSync] = None


def get_sync_engine() -> ModelScopeSync:
    """获取全局同步引擎单例"""
    global _sync_engine
    if _sync_engine is None:
        _sync_engine = ModelScopeSync(
            dataset_repo_id=os.environ.get("MODELSCOPE_DATASET_REPO", DEFAULT_DATASET_REPO),
            token=os.environ.get("MODELSCOPE_API_TOKEN", ""),
        )
        _sync_engine.start()
    return _sync_engine


def mark_dirty(filepath: str) -> None:
    """对外快捷方法：标记文件待同步"""
    get_sync_engine().mark_dirty(filepath)


# ─── HTTP 路由处理器 ──────────────────────────────────────

async def 手动同步(request: web.Request) -> web.Response:
    """POST /ai-coder/cloud/sync - 手动触发同步"""
    try:
        engine = get_sync_engine()
        result = await engine.sync_now()
        if result.get("success"):
            return web.json_response({"success": True, "data": result.get("data")})
        return web.json_response({"success": False, "error": result.get("error")}, status=400)
    except Exception as e:
        logger.error(f"手动同步异常: {e}", exc_info=True)
        return web.json_response({"success": False, "error": _服务器内部错误}, status=500)


async def 同步状态(request: web.Request) -> web.Response:
    """GET /ai-coder/cloud/status - 获取同步状态"""
    try:
        engine = get_sync_engine()
        return web.json_response({"success": True, "data": engine.get_status()})
    except Exception as e:
        logger.error(f"获取同步状态异常: {e}", exc_info=True)
        return web.json_response({"success": False, "error": _服务器内部错误}, status=500)


async def 上传文件(request: web.Request) -> web.Response:
    """POST /ai-coder/cloud/upload - 上传单个文件到数据集

    请求体: {"filepath": "数据/踩坑记录/xxx.json"}
    """
    try:
        data = await request.json()
        filepath = data.get("filepath", "")
        if not filepath:
            return web.json_response({"success": False, "error": "缺少 filepath"}, status=400)

        plugin_root = get_plugin_root()
        target = Path(filepath)
        if not target.is_absolute():
            target = plugin_root / filepath

        if not target.exists():
            return web.json_response({"success": False, "error": f"文件不存在: {filepath}"}, status=404)

        try:
            rel = target.resolve().relative_to(plugin_root.resolve())
        except ValueError:
            return web.json_response({"success": False, "error": "文件不在插件目录内"}, status=400)

        engine = get_sync_engine()
        result = await engine._upload_batch([str(rel).replace("\\", "/")])
        if result.get("success"):
            return web.json_response({"success": True, "data": result})
        return web.json_response({"success": False, "error": result.get("error")}, status=400)
    except Exception as e:
        logger.error(f"上传文件到云端异常: {e}", exc_info=True)
        return web.json_response({"success": False, "error": _服务器内部错误}, status=500)


async def 手动拉取(request: web.Request) -> web.Response:
    """POST /ai-coder/cloud/pull - 手动触发云端拉取"""
    try:
        engine = get_sync_engine()
        result = await engine.pull_from_cloud()
        status_code = 200 if result.get("success") else 500
        return web.json_response(result, status=status_code)
    except Exception as e:
        logger.error(f"手动拉取云端数据异常: {e}", exc_info=True)
        return web.json_response({"success": False, "error": _服务器内部错误}, status=500)


# ─── 路由注册 ─────────────────────────────────────────────

def register_魔搭同步路由(routes) -> None:
    """注册 ModelScope 同步相关端点"""
    routes.post("/ai-coder/cloud/sync")(手动同步)
    routes.get("/ai-coder/cloud/status")(同步状态)
    routes.post("/ai-coder/cloud/upload")(上传文件)
    routes.post("/ai-coder/cloud/pull")(手动拉取)

    # 主动初始化同步引擎并启动后台循环
    try:
        get_sync_engine()
    except Exception as e:
        logger.exception(f"引擎初始化失败: {e}")
