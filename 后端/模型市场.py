"""
模型市场模块 - HuggingFace 模型浏览与下载
"""
import asyncio
from pathlib import Path
from typing import Dict, List, Optional

import aiohttp

HF_API_BASE = "https://huggingface.co/api"

# 共享 ClientSession（懒加载，复用连接池）
_共享会话 = None


async def 获取会话():
    """获取或创建共享的 aiohttp ClientSession"""
    global _共享会话
    if _共享会话 is None or _共享会话.closed:
        _共享会话 = aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=30),
            connector=aiohttp.TCPConnector(limit=10)
        )
    return _共享会话


async def 关闭共享会话():
    """关闭共享会话（应用退出时调用）"""
    global _共享会话
    if _共享会话 is not None and not _共享会话.closed:
        await _共享会话.close()
    _共享会话 = None


def _list_local_models(models_dir: Path) -> List[Dict]:
    """列出指定目录下已下载的模型（模块级公共实现）"""
    models = []
    if models_dir.exists():
        for d in models_dir.iterdir():
            if d.is_dir():
                size = sum(f.stat().st_size for f in d.rglob("*") if f.is_file())
                models.append({
                    "id": d.name.replace("--", "/"),
                    "path": str(d),
                    "size_mb": round(size / 1024 / 1024, 1)
                })
    return models


class ModelMarket:
    """HuggingFace 模型市场"""

    def __init__(self, models_dir: Path):
        self.models_dir = models_dir
        self._download_tasks = {}  # {model_id: {"progress": 0-100, "status": "downloading/done/error"}}

    async def search_models(self, query: str, task: str = "text-generation",
                            limit: int = 10) -> List[Dict]:
        """搜索 HuggingFace 模型"""
        params = {
            "search": query,
            "filter": task,
            "limit": limit,
            "sort": "downloads",
            "direction": "-1"
        }
        session = await 获取会话()
        async with session.get(f"{HF_API_BASE}/models", params=params, timeout=15) as resp:
            if resp.status == 200:
                models = await resp.json()
                return [{
                    "id": m["modelId"],
                    "name": m["modelId"].split("/")[-1],
                    "author": m["modelId"].split("/")[0] if "/" in m["modelId"] else "unknown",
                    "downloads": m.get("downloads", 0),
                    "likes": m.get("likes", 0),
                    "pipeline_tag": m.get("pipeline_tag", ""),
                    "tags": m.get("tags", [])[:5],
                    "last_modified": m.get("lastModified", ""),
                    "size_mb": round(sum(f.get("size", 0) for f in m.get("siblings", [])) / 1024 / 1024, 1),
                } for m in models]
            return []

    async def get_model_info(self, model_id: str) -> Optional[Dict]:
        """获取模型详细信息"""
        session = await 获取会话()
        async with session.get(f"{HF_API_BASE}/models/{model_id}", timeout=15) as resp:
            if resp.status == 200:
                data = await resp.json()
                # 计算模型大小
                siblings = data.get("siblings", [])
                total_size = sum(f.get("size", 0) for f in siblings)
                return {
                    "id": data["modelId"],
                    "description": data.get("cardData", {}).get("description", ""),
                    "license": data.get("cardData", {}).get("license", "unknown"),
                    "downloads": data.get("downloads", 0),
                    "likes": data.get("likes", 0),
                    "tags": data.get("tags", []),
                    "files": [{"name": f["rfilename"], "size": f.get("size", 0)} for f in siblings[:20]],
                    "total_size_mb": round(total_size / 1024 / 1024, 1),
                    "pipeline_tag": data.get("pipeline_tag", ""),
                }
            return None

    async def download_model(self, model_id: str, progress_callback=None) -> Dict:
        """下载模型到本地（使用 aiohttp 直接下载）"""
        target_dir = self.models_dir / model_id.replace("/", "--")

        if target_dir.exists():
            return {"success": True, "message": "模型已存在", "path": str(target_dir)}

        self._download_tasks[model_id] = {"progress": 0, "status": "downloading"}

        try:
            session = await 获取会话()
            # 1. 获取模型文件列表
            async with session.get(f"{HF_API_BASE}/models/{model_id}", timeout=15) as resp:
                if resp.status != 200:
                    raise RuntimeError(f"获取模型信息失败: HTTP {resp.status}")
                data = await resp.json()

            siblings = data.get("siblings", [])
            # 过滤出可下载的权重文件
            weight_exts = {'.bin', '.safetensors', '.gguf', '.json', '.model', '.txt', '.tiktoken'}
            files_to_download = []
            for f in siblings:
                fname = f.get("rfilename", "")
                ext = Path(fname).suffix.lower()
                if ext in weight_exts or fname in (
                    'config.json', 'tokenizer.json', 'tokenizer_config.json',
                    'special_tokens_map.json', 'vocab.json',
                    'generation_config.json', 'model.safetensors.index.json',
                ):
                    files_to_download.append(f)

            if not files_to_download:
                # 如果没有过滤到文件，下载所有文件
                files_to_download = siblings

            total_files = len(files_to_download)

            # 2. 逐文件下载
            target_dir.mkdir(parents=True, exist_ok=True)
            for i, f in enumerate(files_to_download):
                fname = f["rfilename"]
                file_url = f"https://huggingface.co/{model_id}/resolve/main/{fname}"
                file_path = target_dir / fname
                file_path.parent.mkdir(parents=True, exist_ok=True)

                async with session.get(file_url, timeout=aiohttp.ClientTimeout(total=3600)) as dl_resp:
                    if dl_resp.status != 200:
                        continue  # 跳过无法下载的文件

                    total_size = int(dl_resp.headers.get('Content-Length', 0))
                    downloaded = 0

                    with open(file_path, 'wb') as fp:
                        async for chunk in dl_resp.content.iter_chunked(8192):
                            fp.write(chunk)
                            downloaded += len(chunk)
                            if total_size > 0:
                                file_progress = downloaded / total_size
                                overall_progress = int(((i + file_progress) / total_files) * 100)
                                self._download_tasks[model_id]["progress"] = overall_progress
                                if progress_callback:
                                    progress_callback(overall_progress)

                self._download_tasks[model_id]["progress"] = int(((i + 1) / total_files) * 100)

            self._download_tasks[model_id] = {"progress": 100, "status": "done"}
            return {"success": True, "message": "下载完成", "path": str(target_dir)}
        except Exception as e:
            self._download_tasks[model_id] = {"progress": 0, "status": "error", "error": str(e)}
            return {"success": False, "message": f"下载失败: {str(e)}"}

    def get_download_status(self, model_id: str) -> Dict:
        """获取下载进度"""
        return self._download_tasks.get(model_id, {"progress": 0, "status": "unknown"})

    def list_local_models(self) -> List[Dict]:
        """列出已下载的模型"""
        return _list_local_models(self.models_dir)


MODELSCOPE_API_BASE = "https://modelscope.cn/api/v1"


class ModelScopeMarket:
    """魔搭 ModelScope 模型市场"""

    def __init__(self, models_dir: Path):
        self.models_dir = models_dir
        self._download_tasks = {}

    async def search_models(self, query: str, task: str = "text-generation",
                            limit: int = 10) -> List[Dict]:
        """搜索 ModelScope 模型（PUT + JSON body）"""
        payload = {
            "Path": query or "",
            "PageNumber": 1,
            "PageSize": limit,
        }

        headers = {"Content-Type": "application/json"}

        session = await 获取会话()
        async with session.put(
            f"{MODELSCOPE_API_BASE}/models/",
            json=payload,
            headers=headers,
            timeout=aiohttp.ClientTimeout(total=15)
        ) as resp:
            if resp.status == 200:
                data = await resp.json()
                if not data.get("Success"):
                    return []
                models = data.get("Data", {}).get("Models", []) or []
                return [{
                    "id": m.get("Path", ""),
                    "name": m.get("Name", "").split("/")[-1],
                    "author": m.get("Owner", "unknown"),
                    "downloads": m.get("Downloads", 0),
                    "likes": m.get("Likes", 0),
                    "pipeline_tag": (
                        m.get("Tasks", [""])[0]
                        if isinstance(m.get("Tasks"), list) and m.get("Tasks")
                        else ""
                    ),
                    "tags": m.get("Tags", [])[:5] if isinstance(m.get("Tags"), list) else [],
                    "last_modified": m.get("LastUpdatedDate", ""),
                    "size_mb": 0,  # ModelScope API 不直接返回大小
                } for m in models]
            return []

    async def get_model_info(self, model_id: str) -> Optional[Dict]:
        """获取 ModelScope 模型详细信息"""
        session = await 获取会话()
        async with session.get(
            f"{MODELSCOPE_API_BASE}/models/{model_id}",
            timeout=aiohttp.ClientTimeout(total=15)
        ) as resp:
            if resp.status == 200:
                data = await resp.json()
                if not data.get("Success"):
                    return None
                m = data.get("Data", {})
                return {
                    "id": m.get("Path", model_id),
                    "description": m.get("ChineseDescription", "") or m.get("Description", ""),
                    "license": m.get("License", "unknown"),
                    "downloads": m.get("Downloads", 0),
                    "likes": m.get("Likes", 0),
                    "tags": m.get("Tags", []) if isinstance(m.get("Tags"), list) else [],
                    "files": [],  # ModelScope 文件列表需单独 API
                    "total_size_mb": 0,
                    "pipeline_tag": m.get("Tasks", [""])[0] if isinstance(m.get("Tasks"), list) else "",
                }
            return None

    async def download_model(self, model_id: str, progress_callback=None) -> Dict:
        """通过 git clone 从 ModelScope 下载模型"""
        target_dir = self.models_dir / model_id.replace("/", "--")

        if target_dir.exists():
            return {"success": True, "message": "模型已存在", "path": str(target_dir)}

        self._download_tasks[model_id] = {"progress": 0, "status": "downloading"}

        try:
            import subprocess
            target_dir.mkdir(parents=True, exist_ok=True)
            clone_url = f"https://modelscope.cn/{model_id}.git"

            proc = await asyncio.get_event_loop().run_in_executor(
                None,
                lambda: subprocess.run(
                    ["git", "clone", "--depth=1", clone_url, str(target_dir)],
                    capture_output=True, text=True, timeout=3600
                )
            )

            if proc.returncode == 0:
                self._download_tasks[model_id] = {"progress": 100, "status": "done"}
                return {"success": True, "message": "下载完成", "path": str(target_dir)}
            else:
                raise RuntimeError(f"git clone 失败: {proc.stderr[:200]}")

        except FileNotFoundError:
            self._download_tasks[model_id] = {"progress": 0, "status": "error", "error": "git 命令不可用"}
            import shutil
            if target_dir.exists():
                shutil.rmtree(target_dir, ignore_errors=True)
            return {"success": False, "message": "下载失败: 系统未安装 git，请先安装 git 后重试"}
        except Exception as e:
            self._download_tasks[model_id] = {"progress": 0, "status": "error", "error": str(e)}
            import shutil
            if target_dir.exists():
                shutil.rmtree(target_dir, ignore_errors=True)
            return {"success": False, "message": f"下载失败: {str(e)}"}

    def get_download_status(self, model_id: str) -> Dict:
        return self._download_tasks.get(model_id, {"progress": 0, "status": "unknown"})

    def list_local_models(self) -> List[Dict]:
        """复用与 HF 相同的本地模型列表逻辑"""
        return _list_local_models(self.models_dir)
