"""
GitHub 同步核心模块
通过 GitHub REST API 将本地插件目录同步到 GitHub 仓库
"""

import re
import json
import time
import base64
import asyncio
from pathlib import Path

import aiohttp

from .文件读写操作 import load_settings
from .日志配置 import 获取日志器

logger = 获取日志器("GitHub同步")


# ─── 常量定义 ──────────────────────────────────────────

_LOG_PREFIX = "[节点梦工厂]"

_DEFAULT_IGNORED_DIRS = {
    "__pycache__", ".git", "node_modules", ".venv", "venv",
    ".eggs", ".tox", ".mypy_cache", ".pytest_cache",
    ".env", ".idea", ".vscode", ".DS_Store", "Thumbs.db"
}

_IGNORED_EXTENSIONS = {".pyc", ".pyo"}

_MAX_FILE_SIZE = 100 * 1024 * 1024  # 100MB

_GITHUB_API_BASE = "https://api.github.com"

_MAX_RETRIES = 4
_CONCURRENCY_LIMIT = 5
_SYNC_PROGRESS_FILE = ".sync_progress.json"


# ─── 辅助函数 ──────────────────────────────────────────

def validate_repo_name(name: str) -> dict:
    """验证仓库名格式，允许 [a-zA-Z0-9_.-]，最长 100 字符

    返回: {"valid": bool, "message": str}
    """
    if not name:
        return {"valid": False, "message": "仓库名不能为空"}
    if len(name) > 100:
        return {"valid": False, "message": "仓库名不能超过 100 个字符"}
    if not re.match(r'^[a-zA-Z0-9_.\-]+$', name):
        return {"valid": False, "message": "仓库名只能包含字母、数字、下划线、点和连字符"}
    return {"valid": True, "message": "仓库名格式正确"}


def _build_headers(token: str) -> dict:
    """构建 GitHub API 请求头"""
    return {
        "Authorization": f"token {token}",
        "Accept": "application/vnd.github.v3+json",
        "User-Agent": "ComfyUI-NodeCraft-AI"
    }


def _parse_gitignore(gitignore_path: Path) -> list:
    """解析 .gitignore 文件，返回忽略规则列表"""
    patterns = []
    if not gitignore_path.exists():
        return patterns
    try:
        content = gitignore_path.read_text(encoding="utf-8")
        for line in content.splitlines():
            line = line.strip()
            # 跳过空行和注释
            if not line or line.startswith("#"):
                continue
            patterns.append(line)
    except Exception:
        pass
    return patterns


def _match_gitignore_pattern(rel_path: str, pattern: str) -> bool:
    """检查相对路径是否匹配 .gitignore 规则（简化版）

    支持:
    - 直接文件/目录名匹配
    - 通配符 * 匹配
    - 目录专用规则（以 / 结尾）
    - 路径前缀匹配（含 /）
    """
    # 去除尾部斜杠（目录规则标识）
    is_dir_rule = pattern.endswith("/")
    if is_dir_rule:
        pattern = pattern.rstrip("/")

    # 将 pattern 转为正则
    # 如果 pattern 包含 /，从路径开头匹配
    if "/" in pattern:
        regex_pattern = pattern.replace(".", r"\.").replace("*", "[^/]*")
        regex_pattern = f"^{regex_pattern}"
    else:
        # 否则匹配路径中任何一层
        regex_pattern = pattern.replace(".", r"\.").replace("*", "[^/]*")
        regex_pattern = f"(^|/){regex_pattern}(/|$)"

    try:
        return bool(re.search(regex_pattern, rel_path))
    except re.error:
        return False


def _scan_files_for_upload(plugin_path: Path, ignore_gitignore: bool = False) -> list:
    """扫描插件目录中需要上传的文件

    参数:
        plugin_path: 插件根目录路径
        ignore_gitignore: 是否忽略 .gitignore 规则（True=不使用 .gitignore 过滤）

    返回: list[dict]，每项含 "abs_path" 和 "rel_path"（/ 分隔符）
    """
    plugin_path = Path(plugin_path).resolve()
    files = []

    # 加载 .gitignore 规则
    gitignore_patterns = []
    if not ignore_gitignore:
        gitignore_file = plugin_path / ".gitignore"
        if gitignore_file.exists():
            gitignore_patterns = _parse_gitignore(gitignore_file)

    def _should_ignore(rel_path: str, is_dir: bool = False) -> bool:
        """判断路径是否应该被忽略"""
        # 检查 .gitignore 规则
        for pattern in gitignore_patterns:
            if _match_gitignore_pattern(rel_path, pattern):
                return True
        return False

    def _walk(current: Path):
        try:
            entries = sorted(current.iterdir(), key=lambda x: x.name.lower())
        except PermissionError:
            logger.warning(f"权限不足，跳过目录: {current}")
            return

        for entry in entries:
            # 检查默认忽略的目录/文件名
            if entry.name in _DEFAULT_IGNORED_DIRS:
                continue

            # 相对路径，统一使用 / 分隔符
            rel_path = str(entry.relative_to(plugin_path)).replace("\\", "/")

            if entry.is_dir():
                # 检查是否应忽略该目录
                if _should_ignore(rel_path + "/", is_dir=True):
                    continue
                _walk(entry)

            elif entry.is_file():
                # 检查扩展名
                if entry.suffix.lower() in _IGNORED_EXTENSIONS:
                    continue
                # 检查 .gitignore 规则
                if _should_ignore(rel_path):
                    continue
                # 检查文件大小
                try:
                    size = entry.stat().st_size
                    if size > _MAX_FILE_SIZE:
                        logger.warning(f"跳过超大文件 (>{_MAX_FILE_SIZE // (1024*1024)}MB): {rel_path}")
                        continue
                except OSError:
                    continue

                files.append({
                    "abs_path": str(entry),
                    "rel_path": rel_path
                })

    _walk(plugin_path)
    return files


# ─── 断点续传 ─────────────────────────────────────────

def _load_sync_progress(plugin_path: Path) -> set:
    """加载同步进度文件，返回已成功上传的文件集合"""
    progress_file = plugin_path / _SYNC_PROGRESS_FILE
    if not progress_file.exists():
        return set()
    try:
        data = json.loads(progress_file.read_text(encoding="utf-8"))
        return set(data.get("uploaded_files", []))
    except (json.JSONDecodeError, OSError, KeyError):
        return set()


def _save_sync_progress(plugin_path: Path, uploaded_files: set):
    """保存同步进度到文件"""
    progress_file = plugin_path / _SYNC_PROGRESS_FILE
    try:
        data = {"uploaded_files": sorted(uploaded_files), "timestamp": time.time()}
        progress_file.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    except OSError:
        pass


def _remove_sync_progress(plugin_path: Path):
    """同步全部完成后删除进度文件"""
    progress_file = plugin_path / _SYNC_PROGRESS_FILE
    try:
        if progress_file.exists():
            progress_file.unlink()
    except OSError:
        pass


# ─── 异步 API 函数 ─────────────────────────────────────

async def _api_request_with_retry(session: aiohttp.ClientSession, method: str,
                                  url: str, headers: dict, **kwargs) -> aiohttp.ClientResponse:
    """带指数退避重试的 API 请求

    检测 HTTP 403/429 状态码，实现指数退避重试：1s → 2s → 4s → 8s，最多 4 次
    读取 X-RateLimit-Reset header 智能等待
    """
    for attempt in range(_MAX_RETRIES):
        try:
            resp = await session.request(method, url, headers=headers, **kwargs)

            # 非速率限制错误，直接返回
            if resp.status not in (403, 429):
                return resp

            # 速率限制：计算等待时间
            reset_time = resp.headers.get("X-RateLimit-Reset")
            if reset_time:
                try:
                    wait_seconds = max(0, int(reset_time) - int(time.time())) + 1
                    # 限制最大等待时间为 60 秒
                    wait_seconds = min(wait_seconds, 60)
                except (ValueError, TypeError):
                    wait_seconds = (2 ** attempt)
            else:
                wait_seconds = (2 ** attempt)  # 1, 2, 4, 8

            if attempt < _MAX_RETRIES - 1:
                logger.warning(f"速率限制，等待 {wait_seconds}s 后重试 ({attempt + 1}/{_MAX_RETRIES})...")
                await asyncio.sleep(wait_seconds)
                # 释放响应资源
                resp.release()
            else:
                # 最后一次尝试仍失败，返回响应让调用者处理
                return resp

        except (aiohttp.ClientError, asyncio.TimeoutError) as e:
            if attempt < _MAX_RETRIES - 1:
                wait_seconds = (2 ** attempt)
                logger.warning(f"网络异常，等待 {wait_seconds}s 后重试 ({attempt + 1}/{_MAX_RETRIES}): {e}")
                await asyncio.sleep(wait_seconds)
            else:
                raise

    # 不应到达此处，但以防万一
    raise aiohttp.ClientError(f"超过最大重试次数 ({_MAX_RETRIES})")


async def test_github_connection(token: str, username: str) -> dict:
    """验证 GitHub Token 有效性

    返回: {"valid": bool, "message": str}
    """
    if not token:
        return {"valid": False, "message": "Token 为空"}
    if not username:
        return {"valid": False, "message": "用户名为空"}

    # 验证 token 格式
    if not (token.startswith("ghp_") or token.startswith("github_pat_")):
        return {"valid": False, "message": "Token 格式无效，应以 ghp_ 或 github_pat_ 开头"}

    headers = _build_headers(token)

    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(
                f"{_GITHUB_API_BASE}/user",
                headers=headers
            ) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    login = data.get("login", "")
                    if login.lower() == username.lower():
                        return {"valid": True, "message": f"Token 验证成功，用户: {login}"}
                    else:
                        return {
                            "valid": False,
                            "message": f"Token 对应用户 ({login}) 与填写的用户名 ({username}) 不匹配"
                        }
                elif resp.status == 401:
                    return {"valid": False, "message": "Token 无效或已过期"}
                else:
                    return {"valid": False, "message": f"验证失败，HTTP 状态码: {resp.status}"}
    except aiohttp.ClientError as e:
        return {"valid": False, "message": f"网络连接失败: {str(e)}"}
    except Exception as e:
        return {"valid": False, "message": f"验证异常: {str(e)}"}


async def check_repo_exists(token: str, username: str, repo_name: str) -> bool:
    """检查仓库是否存在

    返回: bool
    """
    headers = _build_headers(token)

    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(
                f"{_GITHUB_API_BASE}/repos/{username}/{repo_name}",
                headers=headers
            ) as resp:
                return resp.status == 200
    except Exception:
        return False


async def _create_repo(session: aiohttp.ClientSession, headers: dict,
                       repo_name: str, description: str = "",
                       private: bool = False) -> dict:
    """创建 GitHub 仓库

    返回: {"success": bool, "message": str, "url": str}
    """
    payload = {
        "name": repo_name,
        "description": description or f"Synced from ComfyUI plugin via NodeCraft AI",
        "private": private,
        "auto_init": False
    }

    try:
        async with session.post(
            f"{_GITHUB_API_BASE}/user/repos",
            headers=headers,
            json=payload
        ) as resp:
            if resp.status in (200, 201):
                data = await resp.json()
                return {
                    "success": True,
                    "message": "仓库创建成功",
                    "url": data.get("html_url", "")
                }
            elif resp.status == 422:
                data = await resp.json()
                msg = data.get("message", "")
                errors = data.get("errors", [])
                if errors:
                    msg = errors[0].get("message", msg)
                return {"success": False, "message": f"创建仓库失败: {msg}", "url": ""}
            else:
                return {"success": False, "message": f"创建仓库失败，HTTP {resp.status}", "url": ""}
    except Exception as e:
        return {"success": False, "message": f"创建仓库异常: {str(e)}", "url": ""}


async def _get_all_file_shas(session: aiohttp.ClientSession, headers: dict,
                             username: str, repo_name: str,
                             branch: str = "main") -> dict:
    """批量获取仓库所有文件的 SHA（消除 N+1 查询）

    调用 Git Trees API 一次性获取整个仓库文件树

    参数:
        session: aiohttp 会话
        headers: 请求头
        username: GitHub 用户名
        repo_name: 仓库名
        branch: 分支名，默认 main

    返回: {path: sha} 字典，仓库为空时返回空字典
    """
    url = f"{_GITHUB_API_BASE}/repos/{username}/{repo_name}/git/trees/{branch}?recursive=1"

    try:
        resp = await _api_request_with_retry(session, "GET", url, headers)
        async with resp:
            if resp.status == 200:
                data = await resp.json()
                tree = data.get("tree", [])
                # 只取 blob 类型（文件），忽略 tree 类型（目录）
                return {
                    item["path"]: item["sha"]
                    for item in tree
                    if item.get("type") == "blob"
                }
            elif resp.status == 404:
                # 仓库为空（无任何 commit）或分支不存在
                return {}
            else:
                logger.warning(f"获取文件树失败 (HTTP {resp.status})，将逐文件查询 SHA")
                return {}
    except (aiohttp.ClientError, asyncio.TimeoutError) as e:
        logger.warning(f"获取文件树网络异常: {e}，将逐文件查询 SHA")
        return {}
    except Exception as e:
        logger.exception(f"获取文件树异常: {e}，将逐文件查询 SHA")
        return {}


async def _get_file_sha(session: aiohttp.ClientSession, headers: dict,
                        username: str, repo_name: str, file_path: str) -> str | None:
    """获取文件当前 SHA（若文件已存在）- 备用方法

    返回: SHA 字符串或 None
    """
    try:
        async with session.get(
            f"{_GITHUB_API_BASE}/repos/{username}/{repo_name}/contents/{file_path}",
            headers=headers
        ) as resp:
            if resp.status == 200:
                data = await resp.json()
                return data.get("sha")
            return None
    except Exception:
        return None


async def _upload_file(session: aiohttp.ClientSession, headers: dict,
                       username: str, repo_name: str,
                       file_path: str, content_b64: str,
                       sha: str | None = None) -> dict:
    """上传或更新单个文件（带速率限制重试）

    返回: {"success": bool, "message": str}
    """
    payload = {
        "message": f"Sync: {file_path}",
        "content": content_b64
    }
    if sha:
        payload["sha"] = sha

    url = f"{_GITHUB_API_BASE}/repos/{username}/{repo_name}/contents/{file_path}"

    try:
        resp = await _api_request_with_retry(session, "PUT", url, headers, json=payload)
        async with resp:
            if resp.status in (200, 201):
                return {"success": True, "message": f"上传成功: {file_path}"}
            else:
                body = await resp.text()
                return {"success": False, "message": f"上传失败 ({resp.status}): {file_path} - {body[:200]}"}
    except (aiohttp.ClientError, asyncio.TimeoutError) as e:
        return {"success": False, "message": f"上传网络异常: {file_path} - {str(e)}"}
    except Exception as e:
        return {"success": False, "message": f"上传异常: {file_path} - {str(e)}"}


async def _upload_file_concurrent(sem: asyncio.Semaphore, session: aiohttp.ClientSession,
                                  headers: dict, username: str, repo_name: str,
                                  file_path: str, content_b64: str,
                                  sha: str | None = None) -> dict:
    """并发上传单个文件（受 Semaphore 控制并发度）

    返回: {"success": bool, "message": str, "file_path": str}
    """
    async with sem:
        result = await _upload_file(session, headers, username, repo_name,
                                    file_path, content_b64, sha)
        result["file_path"] = file_path
        return result


# ─── 主同步函数 ────────────────────────────────────────

async def sync_to_github(plugin_dir: str, repo_name: str,
                         ignore_gitignore: bool = False,
                         visibility: str = "public",
                         progress_callback=None) -> dict:
    """将本地插件目录同步到 GitHub 仓库

    参数:
        plugin_dir: 插件目录的绝对路径
        repo_name: GitHub 仓库名称
        ignore_gitignore: 是否忽略 .gitignore 规则
        visibility: 仓库可见性 ("public" 或 "private")
        progress_callback: 可选进度回调函数 callback(current, total, filename)

    返回: {
        "success": bool,
        "message": str,
        "repo_url": str,
        "uploaded_count": int,
        "failed_count": int,
        "error": str  # 仅失败时
    }
    """
    # 1. 加载凭证
    settings = load_settings()
    github_token = settings.get("github_token", "")
    github_username = settings.get("github_username", "")

    if not github_token:
        return {
            "success": False,
            "message": "",
            "repo_url": "",
            "uploaded_count": 0,
            "failed_count": 0,
            "error": "未配置 GitHub Token，请在设置中填写"
        }

    if not github_username:
        return {
            "success": False,
            "message": "",
            "repo_url": "",
            "uploaded_count": 0,
            "failed_count": 0,
            "error": "未配置 GitHub 用户名，请在设置中填写"
        }

    # 2. 验证 Token 格式
    if not (github_token.startswith("ghp_") or github_token.startswith("github_pat_")):
        return {
            "success": False,
            "message": "",
            "repo_url": "",
            "uploaded_count": 0,
            "failed_count": 0,
            "error": "GitHub Token 格式无效，应以 ghp_ 或 github_pat_ 开头"
        }

    # 验证仓库名
    name_check = validate_repo_name(repo_name)
    if not name_check["valid"]:
        return {
            "success": False,
            "message": "",
            "repo_url": "",
            "uploaded_count": 0,
            "failed_count": 0,
            "error": name_check["message"]
        }

    # 验证插件目录
    plugin_path = Path(plugin_dir).resolve()
    if not plugin_path.exists() or not plugin_path.is_dir():
        return {
            "success": False,
            "message": "",
            "repo_url": "",
            "uploaded_count": 0,
            "failed_count": 0,
            "error": f"插件目录不存在: {plugin_dir}"
        }

    headers = _build_headers(github_token)
    private = (visibility == "private")
    repo_url = f"https://github.com/{github_username}/{repo_name}"

    logger.info(f"开始同步到 GitHub: {repo_name}")

    try:
        async with aiohttp.ClientSession() as session:
            # 3. 检查/创建仓库
            repo_exists = await check_repo_exists(github_token, github_username, repo_name)

            if not repo_exists:
                logger.info(f"仓库不存在，正在创建: {repo_name}")
                create_result = await _create_repo(
                    session, headers, repo_name,
                    description=f"ComfyUI plugin synced via NodeCraft AI",
                    private=private
                )
                if not create_result["success"]:
                    return {
                        "success": False,
                        "message": "",
                        "repo_url": "",
                        "uploaded_count": 0,
                        "failed_count": 0,
                        "error": create_result["message"]
                    }
                if create_result["url"]:
                    repo_url = create_result["url"]
                logger.info(f"仓库创建成功: {repo_url}")
            else:
                logger.info(f"仓库已存在: {repo_url}")

            # 4. 扫描本地文件
            logger.info("正在扫描本地文件...")
            files = _scan_files_for_upload(plugin_path, ignore_gitignore)
            logger.info(f"扫描完成，共 {len(files)} 个文件待同步")

            if not files:
                return {
                    "success": True,
                    "message": "没有需要同步的文件",
                    "repo_url": repo_url,
                    "uploaded_count": 0,
                    "failed_count": 0
                }

            # 5. 加载断点续传进度
            already_uploaded = _load_sync_progress(plugin_path)
            if already_uploaded:
                logger.info(f"检测到上次未完成的同步，已完成 {len(already_uploaded)} 个文件，从断点继续")

            # 过滤掉已上传的文件
            pending_files = [f for f in files if f["rel_path"] not in already_uploaded]
            total_files = len(files)
            skipped_count = len(already_uploaded & {f["rel_path"] for f in files})

            if not pending_files:
                # 所有文件都已上传完成
                _remove_sync_progress(plugin_path)
                return {
                    "success": True,
                    "message": f"同步完成，所有 {total_files} 个文件已在之前上传",
                    "repo_url": repo_url,
                    "uploaded_count": total_files,
                    "failed_count": 0
                }

            logger.info(f"待上传 {len(pending_files)} 个文件 (跳过已完成 {skipped_count} 个)")

            # 6. 批量获取仓库文件 SHA（消除 N+1）
            logger.info("正在获取仓库文件树...")
            sha_map = await _get_all_file_shas(session, headers, github_username, repo_name)
            logger.info(f"获取到 {len(sha_map)} 个已有文件的 SHA")

            # 7. 准备上传任务
            upload_tasks = []
            sem = asyncio.Semaphore(_CONCURRENCY_LIMIT)

            for file_info in pending_files:
                abs_path = Path(file_info["abs_path"])
                rel_path = file_info["rel_path"]

                try:
                    # 读取文件内容并 base64 编码
                    content_bytes = abs_path.read_bytes()
                    content_b64 = base64.b64encode(content_bytes).decode("utf-8")
                except OSError as e:
                    logger.warning(f"⚠️ 文件读取失败 ({rel_path}): {e}")
                    continue

                # 从批量 SHA 映射中获取（无需逐文件查询）
                sha = sha_map.get(rel_path)

                upload_tasks.append(
                    _upload_file_concurrent(
                        sem, session, headers,
                        github_username, repo_name,
                        rel_path, content_b64, sha
                    )
                )

            # 8. 并发上传并收集结果
            uploaded_count = skipped_count
            failed_count = 0
            current_progress = skipped_count

            # 使用 asyncio.as_completed 实现逐步进度更新
            tasks_with_index = [asyncio.ensure_future(t) for t in upload_tasks]
            for coro in asyncio.as_completed(tasks_with_index):
                try:
                    result = await coro
                except Exception as e:
                    failed_count += 1
                    current_progress += 1
                    logger.exception(f"⚠️ 上传任务异常: {e}")
                    continue

                current_progress += 1
                file_path = result.get("file_path", "")

                if result["success"]:
                    uploaded_count += 1
                    # 更新断点续传进度
                    already_uploaded.add(file_path)
                    _save_sync_progress(plugin_path, already_uploaded)
                else:
                    failed_count += 1
                    logger.warning(f"⚠️ {result['message']}")

                # 调用进度回调
                if progress_callback:
                    try:
                        progress_callback(current_progress, total_files, file_path)
                    except Exception:
                        pass

                # 打印进度（每 10 个文件或最后一个）
                if current_progress % 10 == 0 or current_progress == total_files:
                    logger.info(f"进度: {current_progress}/{total_files}")

            # 9. 同步完成处理
            if failed_count == 0:
                # 全部成功，删除进度文件
                _remove_sync_progress(plugin_path)

            total = uploaded_count + failed_count
            logger.info(f"同步完成: {uploaded_count}/{total} 成功, {failed_count} 失败")

            if failed_count == 0:
                return {
                    "success": True,
                    "message": f"同步完成，共上传 {uploaded_count} 个文件",
                    "repo_url": repo_url,
                    "uploaded_count": uploaded_count,
                    "failed_count": 0
                }
            else:
                return {
                    "success": uploaded_count > 0,
                    "message": f"同步部分完成: {uploaded_count} 成功, {failed_count} 失败",
                    "repo_url": repo_url,
                    "uploaded_count": uploaded_count,
                    "failed_count": failed_count
                }

    except aiohttp.ClientError as e:
        return {
            "success": False,
            "message": "",
            "repo_url": repo_url,
            "uploaded_count": 0,
            "failed_count": 0,
            "error": f"网络连接错误: {str(e)}"
        }
    except Exception as e:
        return {
            "success": False,
            "message": "",
            "repo_url": repo_url,
            "uploaded_count": 0,
            "failed_count": 0,
            "error": f"同步过程发生异常: {str(e)}"
        }
