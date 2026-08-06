import json
import os
import re
import shutil
import sys
import tempfile
import time
import uuid
from datetime import datetime
from pathlib import Path

# 有效的会话类型常量（统一定义，供路由层与本模块共享）
_VALID_SESSION_TYPES = ("develop", "optimize", "visualize")

from .日志配置 import 获取日志器

logger = 获取日志器(__name__)

try:
    from cryptography.fernet import Fernet
except Exception as _e:  # ImportError 或 cryptography Rust 绑定加载异常等
    Fernet = None
    logger.warning(
        f"cryptography 模块加载失败，加密配置功能不可用: {_e}"
    )

from .会话存储SQLite import (
    保存会话 as _sqlite_保存会话,
)
from .会话存储SQLite import (
    加载会话 as _sqlite_加载会话,
)
from .会话存储SQLite import (
    删除会话 as _sqlite_删除会话,
)
from .会话存储SQLite import (
    查询会话摘要列表 as _sqlite_查询摘要,
)
from .系统环境映射 import get_plugin_root

# ─── 文件大小限制 ──────────────────────────────────────────
# 单次读取插件文件的最大字节数，超过则拒绝读取，防止内存溢出
MAX_READ_FILE_SIZE = 10 * 1024 * 1024  # 10MB


def _备份文件(file_path: Path):
    """写入前备份原文件为 .bak（覆盖式，仅保留最近一份）"""
    if file_path.exists():
        bak_path = file_path.with_suffix(file_path.suffix + '.bak')
        try:
            shutil.copy2(file_path, bak_path)
        except Exception:
            # 备份失败不应阻塞写入主流程
            pass


def 安全写入JSON(file_path, data):
    """原子安全写入 JSON 文件（写入临时文件 → fsync → os.replace 原子替换）

    确保并发写入或进程崩溃时不会导致 JSON 文件损坏：
    1. 先写入同目录下的 .tmp 临时文件
    2. 调用 fsync 确保数据落盘
    3. 使用 os.replace 原子替换原文件（即使中途崩溃，原文件也不受影响）

    参数:
        file_path: 目标文件路径（Path 或 str）
        data: 要序列化的数据（dict/list 等 JSON 可序列化对象）

    异常:
        写入失败时抛出原始异常，同时自动清理临时文件
    """
    file_path = Path(file_path)
    # 确保目标目录存在
    file_path.parent.mkdir(parents=True, exist_ok=True)

    # 在同目录下创建临时文件，避免跨文件系统 rename 失败
    tmp_fd = None
    tmp_path = None
    try:
        tmp_fd, tmp_path = tempfile.mkstemp(
            suffix='.tmp',
            dir=str(file_path.parent),
            prefix=f'.{file_path.stem}_'
        )
        # 写入 JSON 数据
        with os.fdopen(tmp_fd, 'w', encoding='utf-8') as tmp_f:
            tmp_fd = None  # fdopen 接管了 fd，后续不需要 close
            json.dump(data, tmp_f, ensure_ascii=False, indent=2)
            tmp_f.flush()
            os.fsync(tmp_f.fileno())

        # 原子替换：os.replace 在 Windows/Linux/macOS 上均为原子操作
        os.replace(tmp_path, str(file_path))
        tmp_path = None  # 替换成功后无需清理

    except Exception:
        # 写入失败：清理临时文件，原文件不受影响
        if tmp_fd is not None:
            try:
                os.close(tmp_fd)
            except OSError:
                pass
        if tmp_path is not None:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
        raise
    finally:
        # 双重保障：如果临时文件仍然残留则清理
        if tmp_path is not None:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass


# ─── 数据目录管理 ──────────────────────────────────────────

def get_data_dir():
    """获取/创建数据目录 (插件根目录/数据/)"""
    data_dir = Path(get_plugin_root()) / "数据"
    data_dir.mkdir(parents=True, exist_ok=True)
    return data_dir


def get_sessions_dir():
    """获取/创建会话目录 (插件根目录/数据/会话/)

    注：会话已迁移至 SQLite（数据/会话.db），本目录仅供 数据迁移.py
    读取存量 JSON 会话文件时使用，不再是运行时存储位置。
    """
    sessions_dir = get_data_dir() / "会话"
    sessions_dir.mkdir(parents=True, exist_ok=True)
    return sessions_dir


# ─── 会话 CRUD（阶段三：底层存储为 SQLite，接口签名与返回结构不变）────
#
# 存储实现见 后端/会话存储SQLite.py：
# - 追加消息只 INSERT 新行，根治「追加一条重写整文件」的写放大；
# - 列表查询直接 SQL ORDER BY created_at DESC，
#   取代阶段二的 会话索引.json（内存缓存→索引文件→glob 自愈）机制。


def load_sessions_list(session_type=None):
    """加载所有会话摘要（不含 messages），按创建时间倒序排列

    性能：直接 SQL 查询（ORDER BY created_at DESC），无需遍历文件。

    参数：
        session_type: 可选，按界面类型过滤会话。有效值: "develop"/"optimize"/"visualize"。
                      为 None 时返回全部会话。
    """
    # 无效类型不过滤（与旧行为一致）
    if session_type is not None and session_type not in _VALID_SESSION_TYPES:
        session_type = None
    return _sqlite_查询摘要(session_type=session_type)


def create_session(title="新会话", plugin_folder="", session_type="develop"):
    """创建新会话并写入文件

    参数：
        title: 会话标题
        plugin_folder: 关联的插件文件夹名称
        session_type: 会话所属界面类型，有效值: "develop"/"optimize"/"visualize"，
                      非法值默认回退为 "develop"

    返回：新创建的会话数据 dict
    """
    if session_type not in _VALID_SESSION_TYPES:
        session_type = "develop"

    session_id = str(uuid.uuid4())
    now = datetime.now().isoformat(timespec="seconds")

    session_data = {
        "id": session_id,
        "title": title,
        "created_at": now,
        "plugin_folder": plugin_folder,
        "type": session_type,
        "messages": [],
    }
    save_session(session_data)
    return session_data


def load_session(session_id):
    """加载指定会话完整数据，不存在返回 None"""
    return _sqlite_加载会话(session_id)


def save_session(session_data):
    """保存会话数据（SQLite 增量写入：仅写入新增/变化的消息与元数据）

    Bug 14 修复：写入前对 session_data 进行结构与必需字段校验，
    避免写入空数据 / 错误类型 / 缺少 id 的非法会话数据。

    写入事务化：失败自动回滚，库中原数据不受影响。
    """
    # ─── 数据完整性校验 ────────────────────────────────────
    if not session_data or not isinstance(session_data, dict):
        raise ValueError("会话数据格式不正确")
    session_id = session_data.get("id")
    if not session_id:
        raise ValueError("会话数据缺少 id 字段")
    if 'messages' in session_data and not isinstance(session_data['messages'], list):
        raise ValueError("会话消息列表格式不正确")

    _sqlite_保存会话(session_data)


def delete_session(session_id, delete_folder=False):
    """删除会话记录，可选同时删除关联的插件文件夹

    参数：
        session_id: 会话 ID
        delete_folder: 是否同时删除会话关联的 plugin_folder。删除失败不会阻止会话本身删除。

    返回：是否成功删除会话（会话不存在时返回 False，与旧行为一致）
    """
    # 若需要删除文件夹，先读取会话数据获取 plugin_folder
    if delete_folder:
        try:
            session_data = _sqlite_加载会话(session_id)
            plugin_folder = (session_data or {}).get("plugin_folder", "")
            if plugin_folder:
                _删除插件文件夹(plugin_folder)
        except Exception as e:
            logger.warning("删除关联文件夹失败: %s", e)
            # 文件夹删除失败不影响会话删除

    return _sqlite_删除会话(session_id)


def _删除插件文件夹(plugin_folder_name):
    """安全删除关联的插件文件夹

    安全校验：
    1. 路径必须在 custom_nodes 目录内
    2. 不能是 ComfyUI-NodeCraft-AI 自身
    3. 必须是目录（非文件）
    4. 防止路径穿越（不允许 ../ 等）
    """
    from .系统环境映射 import get_custom_nodes_path

    # 基础校验：文件夹名不能为空或包含路径分隔符
    if (not plugin_folder_name
            or '/' in plugin_folder_name
            or '\\' in plugin_folder_name
            or '..' in plugin_folder_name):
        raise ValueError(f"非法的文件夹名称: {plugin_folder_name}")

    custom_nodes_dir = get_custom_nodes_path()
    target_path = custom_nodes_dir / plugin_folder_name

    # 解析后确保仍在 custom_nodes 内（防止符号链接等穿越）
    try:
        resolved = target_path.resolve()
        resolved.relative_to(custom_nodes_dir.resolve())
    except (ValueError, OSError):
        raise ValueError(f"路径安全校验失败: {plugin_folder_name}")

    # 不允许删除自身
    self_name = "ComfyUI-NodeCraft-AI"
    if resolved.name == self_name or plugin_folder_name == self_name:
        raise ValueError("不允许删除 NodeCraft AI 自身目录")

    # 必须是目录
    if not resolved.is_dir():
        raise ValueError(f"目标不是目录: {plugin_folder_name}")

    # 执行删除
    shutil.rmtree(resolved)
    logger.info("已删除插件文件夹: %s", resolved)


# ─── 设置管理 ─────────────────────────────────────────────

_DEFAULT_SETTINGS = {
    "model_source": "api",
    "local_path": "",
    "local_model_name": "",
    # API 直连（用户自持 API Key）；顶层四字段为当前激活配置，api_profiles 保存多套配置方案
    "api_provider": "openai",
    "base_url": "https://api.openai.com/v1",
    "api_key": "",
    "model_name": "",
    "api_profiles": [],
    "active_api_profile_id": "",
    "temperature": 0.2,
    "max_tokens": 4096,
    "github_token": "",
    "github_username": "",
    "github_default_repo": "",
    "github_visibility": "public",
    "proactive_pitfall_check": True,
}

_SENSITIVE_KEYS = ["github_token", "api_key"]


# ─── 加密/解密 ─────────────────────────────────────────────

def _get_cipher_key_path():
    """获取加密密钥文件路径"""
    data_dir = get_data_dir()
    return data_dir / ".密钥"


def _set_secure_permissions(file_path):
    """设置文件权限为仅所有者可读写 (0o600)

    Windows 不完全支持 Unix 权限位，失败时静默忽略。
    """
    try:
        os.chmod(str(file_path), 0o600)
    except (OSError, NotImplementedError):
        # Windows 或其他不支持 chmod 的平台：跳过权限设置
        pass


def _get_cipher():
    """获取或创建 Fernet 加密器

    若 cryptography 模块加载失败（Fernet is None），返回 None，调用方需做降级处理。
    """
    if Fernet is None:
        return None
    key_path = _get_cipher_key_path()
    if not key_path.exists():
        key = Fernet.generate_key()
        key_path.write_bytes(key)
        # 仅所有者可读写，防止密钥被其他用户读取
        _set_secure_permissions(key_path)
    else:
        key = key_path.read_bytes()
    return Fernet(key)


def _encrypt_value(value, field_name=""):
    """加密字符串

    cryptography 不可用时：敏感字段（字段名含 key/token/secret/password）抛出 RuntimeError，
    非敏感字段降级为明文存储（返回原值），并记录一次警告。
    """
    if not value:
        return ""
    if Fernet is None:
        名称小写 = field_name.lower()
        if any(kw in 名称小写 for kw in ("key", "token", "secret", "password")):
            raise RuntimeError("加密模块不可用，无法安全存储敏感信息")
        logger.warning("cryptography 不可用，非敏感字段将以明文形式保存")
        return value
    try:
        cipher = _get_cipher()
        if cipher is None:
            return value
        return cipher.encrypt(value.encode('utf-8')).decode('utf-8')
    except Exception:
        return value


def _decrypt_value(value):
    """解密字符串

    cryptography 不可用时按明文返回，避免设置加载失败导致整个后端不可用。
    """
    if not value:
        return ""
    if Fernet is None:
        # 旧版本写入的密文在此场景下无法解密，原样返回避免抛错
        return value
    try:
        cipher = _get_cipher()
        if cipher is None:
            return value
        return cipher.decrypt(value.encode('utf-8')).decode('utf-8')
    except Exception:
        return ""


def load_settings():
    """加载设置，不存在则返回默认值"""
    filepath = get_data_dir() / "设置.json"
    if not filepath.exists():
        return dict(_DEFAULT_SETTINGS)
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            saved = json.load(f)
        # 合并默认值，确保新增字段有默认值
        result = dict(_DEFAULT_SETTINGS)
        result.update(saved)
        # 解密敏感字段
        for key in _SENSITIVE_KEYS:
            if key in result and result[key]:
                result[key] = _decrypt_value(result[key])
        # 解密 API 配置方案列表中的嵌套 api_key
        if isinstance(result.get("api_profiles"), list):
            for profile in result["api_profiles"]:
                if isinstance(profile, dict) and profile.get("api_key"):
                    profile["api_key"] = _decrypt_value(profile["api_key"])
        return result
    except Exception:
        return dict(_DEFAULT_SETTINGS)


def save_settings(settings):
    """保存设置到文件（合并写入，保留未传入的已有字段）"""
    filepath = get_data_dir() / "设置.json"
    # 先加载现有设置，再合并新值，避免全量覆盖导致未传入字段丢失
    existing = load_settings()
    merged = dict(existing)
    merged.update(settings)
    # 加密敏感字段后再写入
    settings_to_save = dict(merged)
    for key in _SENSITIVE_KEYS:
        if key in settings_to_save and settings_to_save[key]:
            try:
                settings_to_save[key] = _encrypt_value(settings_to_save[key], key)
            except RuntimeError:
                raise ValueError(
                    "保存失败：加密模块不可用，无法安全存储敏感信息"
                    "（如 API Key、Token），请安装 cryptography 库后重试"
                )
    # 加密 API 配置方案列表中的嵌套 api_key（浅拷贝每个配置，避免污染内存中的明文对象）
    if isinstance(settings_to_save.get("api_profiles"), list):
        加密后配置 = []
        for profile in settings_to_save["api_profiles"]:
            if isinstance(profile, dict) and profile.get("api_key"):
                profile = dict(profile)
                try:
                    profile["api_key"] = _encrypt_value(profile["api_key"], "api_key")
                except RuntimeError:
                    raise ValueError(
                        "保存失败：加密模块不可用，无法安全存储敏感信息"
                        "（如 API Key、Token），请安装 cryptography 库后重试"
                    )
            加密后配置.append(profile)
        settings_to_save["api_profiles"] = 加密后配置
    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(settings_to_save, f, ensure_ascii=False, indent=2)


# ─── 插件脚手架 ───────────────────────────────────────────


def _generate_readme_template(plugin_name: str) -> str:
    """生成 8 模块结构的 README.md 模板"""
    return f"""# {plugin_name}

一个为 ComfyUI 打造的自定义节点插件，提供便捷的工作流增强能力。

## 设计理念

本插件注重简洁实用，遵循 ComfyUI 节点开发规范，力求在不增加学习成本的前提下提升创作效率。

## 功能特点

- 基于 ComfyUI V3 节点规范开发
- 支持中文界面显示
- 轻量级设计，无额外依赖

## 界面预览

（待补充截图或 GIF 演示）

## 更新介绍

- 插件初始化创建

## 安装说明

1. 将插件文件夹复制到 `ComfyUI/custom_nodes/` 目录下
2. 重启 ComfyUI
3. 在节点菜单中搜索插件名称即可使用

## 开源协议

本项目遵循 MIT 开源协议。

## 致谢

感谢 ComfyUI 社区提供的优秀插件生态和开发规范。
"""


def update_readme_changelog(plugin_path: str, changelog_entry: str) -> dict:
    """在 README.md 的“更新介绍”模块追加一条更新记录

    参数:
        plugin_path: 插件根目录路径
        changelog_entry: 一条简洁的更新说明（禁用专业术语）

    返回:
        {"success": bool, "message": str}
    """
    readme_path = Path(plugin_path) / "README.md"

    if not readme_path.exists():
        return {"success": False, "message": "README.md 不存在，请先创建插件"}

    try:
        content = readme_path.read_text(encoding="utf-8")
    except Exception as e:
        return {"success": False, "message": f"读取 README.md 失败: {e}"}

    # 定位“更新介绍”模块
    marker = "## 更新介绍"
    idx = content.find(marker)
    if idx == -1:
        # 没有该模块，在末尾追加
        content += f"\n{marker}\n\n- {changelog_entry}\n"
    else:
        # 在该模块标题后插入新条目
        insert_pos = idx + len(marker)
        # 跳过标题后的换行
        while insert_pos < len(content) and content[insert_pos] in "\r\n":
            insert_pos += 1
        content = content[:insert_pos] + f"- {changelog_entry}\n" + content[insert_pos:]

    try:
        readme_path.write_text(content, encoding="utf-8")
    except Exception as e:
        return {"success": False, "message": f"写入 README.md 失败: {e}"}

    return {"success": True, "message": f"已更新 README 更新介绍: {changelog_entry}"}


def create_plugin_scaffold(base_path, plugin_name):
    """一键生成合规的 ComfyUI 插件脚手架

    返回: (success: bool, message: str, path: str)
    """
    if not re.match(r"^[a-zA-Z0-9_-]+$", plugin_name):
        return False, "❌ 验证失败：插件根目录必须使用纯英文、数字或下划线，以防止 ComfyUI 加载出错。", ""

    target_path = Path(base_path) / plugin_name
    if target_path.exists():
        return False, f"❌ 文件夹 '{plugin_name}' 已存在，请更换名称。", ""

    try:
        target_path.mkdir()
        (target_path / "节点").mkdir()
        (target_path / "网页资源").mkdir()

        init_content = (
            "# AI 自动生成的 ComfyUI 节点注册入口\n"
            "WEB_DIRECTORY = \"./网页资源\"\n"
            "\n"
            "NODE_CLASS_MAPPINGS = {}\n"
            "NODE_DISPLAY_NAME_MAPPINGS = {}\n"
            "__all__ = ['NODE_CLASS_MAPPINGS', 'NODE_DISPLAY_NAME_MAPPINGS', 'WEB_DIRECTORY']\n"
        )
        (target_path / "__init__.py").write_text(init_content, encoding="utf-8")

        (target_path / "README.md").write_text(
            _generate_readme_template(plugin_name),
            encoding="utf-8"
        )

        (target_path / "requirements.txt").write_text("", encoding="utf-8")

        return True, f"✅ 成功创建插件目录：{plugin_name}。已初始化 __init__.py。", str(target_path)
    except Exception as e:
        return False, f"❌ 创建目录时发生错误: {str(e)}", ""


# ─── 插件文件操作 ─────────────────────────────────────────

# 扫描时忽略的目录
_IGNORED_DIRS = {'__pycache__', '.git', 'node_modules', '.venv', 'venv', '.eggs', '.tox', '.mypy_cache'}


def scan_plugin_file_tree(plugin_path):
    """递归扫描插件目录，返回文件树结构

    返回: list[dict]，每个 dict 含 name, path(相对路径), type(file/dir), children(仅目录)
    """
    plugin_path = Path(plugin_path)
    if not plugin_path.exists():
        return []

    def _scan(current_path, relative_base):
        items = []
        try:
            entries = sorted(current_path.iterdir(), key=lambda x: (not x.is_dir(), x.name.lower()))
        except PermissionError:
            return items

        for entry in entries:
            # 跳过隐藏文件、忽略目录和备份文件
            if entry.name.startswith('.') or entry.name in _IGNORED_DIRS:
                continue
            if entry.name.endswith('.bak'):
                continue

            rel_path = str(entry.relative_to(plugin_path)).replace('\\', '/')

            if entry.is_dir():
                children = _scan(entry, relative_base)
                items.append({
                    "name": entry.name,
                    "path": rel_path,
                    "type": "dir",
                    "children": children
                })
            elif entry.is_file():
                try:
                    file_size = entry.stat().st_size
                    if file_size < 0:
                        file_size = 0
                except (OSError, ValueError) as e:
                    logger.warning("文件大小检查失败: %s - %s", entry, e)
                    file_size = 0
                items.append({
                    "name": entry.name,
                    "path": rel_path,
                    "type": "file",
                    "size": file_size
                })
        return items

    return _scan(plugin_path, plugin_path)


def read_plugin_file(plugin_path, relative_file_path, start_line=None, end_line=None):
    """读取插件内指定文件的内容

    参数:
        plugin_path: 插件根目录的完整路径
        relative_file_path: 相对于插件根目录的文件路径
        start_line: 起始行号（1-based，可选，默认从头开始）
        end_line: 结束行号（1-based，包含该行，可选，默认到文件末尾）

    返回: (success: bool, content_or_error: str)
    """
    plugin_path = Path(plugin_path)
    file_path = (plugin_path / relative_file_path).resolve()

    # 安全校验：确保文件路径在插件目录内
    try:
        file_path.relative_to(plugin_path.resolve())
    except ValueError:
        return False, "安全错误：文件路径超出插件目录范围"

    if not file_path.exists():
        return False, f"文件不存在: {relative_file_path}"

    if not file_path.is_file():
        return False, f"不是文件: {relative_file_path}"

    # 检查文件大小（防止内存溢出），添加异常值保护
    try:
        file_size = file_path.stat().st_size
        if file_size < 0 or file_size > MAX_READ_FILE_SIZE:
            return False, f"文件大小 ({file_size / (1024*1024):.1f}MB) 超过限制 ({MAX_READ_FILE_SIZE // (1024*1024)}MB)"
    except (OSError, ValueError) as e:
        logger.warning("文件大小检查失败: %s - %s", file_path, e)
        return False, f"文件大小检查失败: {e}"

    try:
        content = file_path.read_text(encoding='utf-8')
    except UnicodeDecodeError:
        return False, "无法读取：文件不是 UTF-8 文本格式"
    except Exception as e:
        return False, f"读取失败: {str(e)}"

    # 无参数调用时行为完全不变：直接返回完整内容
    if start_line is None and end_line is None:
        return True, content

    # 按行号切片（保留换行符）
    lines = content.splitlines(True)
    total = len(lines)

    # 边界处理
    actual_start = start_line if (isinstance(start_line, int) and start_line >= 1) else 1
    actual_end = end_line if (isinstance(end_line, int) and end_line <= total and end_line >= 1) else total
    if isinstance(end_line, int) and end_line > total:
        actual_end = total

    if actual_start > total:
        return True, f"[起始行 {actual_start} 超过文件总行数 {total}，无内容]\n"

    if actual_start > actual_end:
        return False, f"起始行号 ({actual_start}) 不能大于结束行号 ({actual_end})"

    sliced = lines[actual_start - 1:actual_end]
    meta = f"[行 {actual_start}-{actual_end}，共 {total} 行]\n"
    return True, meta + "".join(sliced)


def search_plugin_file(plugin_path, relative_file_path, pattern, use_regex=False, context_lines=3):
    """在文件中搜索关键词或正则表达式，返回匹配行号和上下文

    参数:
        plugin_path: 插件根目录的完整路径
        relative_file_path: 相对于插件根目录的文件路径
        pattern: 搜索关键词或正则表达式
        use_regex: 是否启用正则模式（默认 False = 普通关键词不区分大小写搜索）
        context_lines: 每个匹配前后显示的行数（默认3，最大10）

    返回: (success: bool, result_str_or_error: str)
    """
    plugin_path = Path(plugin_path)
    file_path = (plugin_path / relative_file_path).resolve()

    # 安全校验：确保文件路径在插件目录内
    try:
        file_path.relative_to(plugin_path.resolve())
    except ValueError:
        return False, "安全错误：文件路径超出插件目录范围"

    if not file_path.exists():
        return False, f"文件不存在: {relative_file_path}"

    if not file_path.is_file():
        return False, f"不是文件: {relative_file_path}"

    # 检查文件大小（防止内存溢出），添加异常值保护
    try:
        file_size = file_path.stat().st_size
        if file_size < 0 or file_size > MAX_READ_FILE_SIZE:
            return False, f"文件大小 ({file_size / (1024*1024):.1f}MB) 超过限制 ({MAX_READ_FILE_SIZE // (1024*1024)}MB)"
    except (OSError, ValueError) as e:
        logger.warning("文件大小检查失败: %s - %s", file_path, e)
        return False, f"文件大小检查失败: {e}"

    # pattern 长度限制
    if not pattern or len(pattern) > 200:
        return False, "搜索关键词长度需在 1-200 字符之间"

    # context_lines 限制在 0-10
    try:
        context_lines = int(context_lines)
    except (TypeError, ValueError):
        context_lines = 3
    context_lines = max(0, min(context_lines, 10))

    try:
        content = file_path.read_text(encoding='utf-8')
    except UnicodeDecodeError:
        return False, "无法读取：文件不是 UTF-8 文本格式"
    except Exception as e:
        return False, f"读取失败: {str(e)}"

    lines = content.splitlines()
    total_lines = len(lines)

    # 编译匹配器
    if use_regex:
        try:
            regex = re.compile(pattern)
        except re.error as e:
            return False, f"正则表达式语法错误: {e}"
        def matcher(ln):
            return regex.search(ln) is not None
    else:
        lowered = pattern.lower()
        def matcher(ln):
            return lowered in ln.lower()

    # 收集匹配行号（1-based）
    matched_lines = [i + 1 for i, ln in enumerate(lines) if matcher(ln)]
    total_matches = len(matched_lines)

    if total_matches == 0:
        return True, f"在 {relative_file_path} 中未找到匹配「{pattern}」的内容（共 {total_lines} 行）"

    # 限制最多 20 个匹配
    shown_lines = matched_lines[:20]
    remaining = total_matches - len(shown_lines)

    # 相邻匹配上下文重叠时合并为块
    blocks = []  # 每个块: {"matches": [行号...], "start": int, "end": int}
    for ln in shown_lines:
        blk_start = max(1, ln - context_lines)
        blk_end = min(total_lines, ln + context_lines)
        if blocks and blk_start <= blocks[-1]["end"] + 1:
            blocks[-1]["end"] = max(blocks[-1]["end"], blk_end)
            blocks[-1]["matches"].append(ln)
        else:
            blocks.append({"matches": [ln], "start": blk_start, "end": blk_end})

    parts = [f"在 {relative_file_path} 中找到 {total_matches} 个匹配（共 {total_lines} 行）：\n"]
    for idx, blk in enumerate(blocks, 1):
        match_label = "、".join(f"第 {m} 行" for m in blk["matches"])
        parts.append(f"--- 匹配 {idx} [{match_label}] ---")
        match_set = set(blk["matches"])
        for ln_no in range(blk["start"], blk["end"] + 1):
            text = lines[ln_no - 1]
            if ln_no in match_set:
                parts.append(f"{ln_no}: >>> {text} <<<")
            else:
                parts.append(f"{ln_no}: {text}")
        parts.append("")

    result = "\n".join(parts).rstrip("\n")
    if remaining > 0:
        result += f"\n...还有 {remaining} 个匹配未显示"
    return True, result


def write_plugin_file(plugin_path, relative_file_path, content):
    """写入/修改插件内指定文件（直接覆盖，不创建备份文件）

    参数:
        plugin_path: 插件根目录的完整路径
        relative_file_path: 相对于插件根目录的文件路径
        content: 要写入的文件内容

    返回: (success: bool, message: str)
    """
    plugin_path = Path(plugin_path)
    file_path = (plugin_path / relative_file_path).resolve()

    # 安全校验
    try:
        file_path.relative_to(plugin_path.resolve())
    except ValueError:
        return False, "安全错误：文件路径超出插件目录范围"

    try:
        # 确保父目录存在
        file_path.parent.mkdir(parents=True, exist_ok=True)

        # 直接覆盖写入，不创建 .bak 备份（避免备份文件污染插件目录）
        file_path.write_text(content, encoding='utf-8')

        # 自动清理当前文件的 .bak 残留
        try:
            bak_path = Path(str(file_path) + '.bak')
            if bak_path.exists():
                bak_path.unlink()
        except Exception:
            pass  # 清理失败不影响主流程

        return True, f"成功写入: {relative_file_path}"
    except Exception as e:
        return False, f"写入失败: {str(e)}"


# ─── 增量补丁（Unified Diff）应用 ─────────────────────────

def _parse_unified_diff(patch_content: str) -> list:
    """解析 unified diff 格式的补丁内容，返回 hunk 列表

    Returns:
        list[dict]: 每个 dict 含:
            - old_start: 原文件起始行号 (1-indexed)
            - old_count: 原文件涉及行数
            - new_start: 新文件起始行号 (1-indexed)
            - new_count: 新文件涉及行数
            - lines: list of (type, content) where type is ' ', '+', '-'
    """
    hunks = []
    lines = patch_content.splitlines()
    i = 0

    # 跳过 --- 和 +++ 头行及空行，定位到第一个 @@
    while i < len(lines):
        if lines[i].startswith('@@'):
            break
        i += 1

    while i < len(lines):
        line = lines[i]

        # 解析 @@ hunk header
        match = re.match(r'^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@', line)
        if not match:
            i += 1
            continue

        old_start = int(match.group(1))
        old_count = int(match.group(2)) if match.group(2) is not None else 1
        new_start = int(match.group(3))
        new_count = int(match.group(4)) if match.group(4) is not None else 1

        hunk_lines = []
        i += 1

        # 读取 hunk 内容行
        while i < len(lines):
            hunk_line = lines[i]
            # 遇到下一个 hunk 头或文件头，结束当前 hunk
            if hunk_line.startswith('@@'):
                break
            if hunk_line.startswith('--- ') or hunk_line.startswith('+++ '):
                break

            if hunk_line.startswith('+'):
                hunk_lines.append(('+', hunk_line[1:]))
            elif hunk_line.startswith('-'):
                hunk_lines.append(('-', hunk_line[1:]))
            elif hunk_line.startswith(' '):
                hunk_lines.append((' ', hunk_line[1:]))
            elif hunk_line.rstrip() == '':
                # 空行或仅含空白：视为上下文行（某些 diff 工具会省略前导空格）
                hunk_lines.append((' ', hunk_line))
            elif hunk_line.startswith('\\'):
                # \ No newline at end of file 等元信息行，跳过
                pass
            else:
                # 未知行类型，按上下文行处理（容错）
                hunk_lines.append((' ', hunk_line))
            i += 1

        hunks.append({
            'old_start': old_start,
            'old_count': old_count,
            'new_start': new_start,
            'new_count': new_count,
            'lines': hunk_lines,
        })

    return hunks


def _normalize_line(s: str) -> str:
    """归一化行内容用于比较：去除行尾换行符和尾部空白"""
    return s.rstrip('\r\n').rstrip()


def _find_match_position(lines: list, old_lines: list, expected_start: int) -> int:
    """在 lines 中查找 old_lines 的匹配位置

    查找策略（逐级放宽）：
      1. 在 expected_start 处精确匹配
      2. 在 expected_start 附近搜索（±50行）
      3. 全局搜索

    Returns:
        匹配起始索引（0-indexed），未找到返回 -1
    """
    if not old_lines:
        # 纯插入：直接在 expected_start 处插入
        return max(0, min(expected_start, len(lines)))

    old_normalized = [_normalize_line(ln) for ln in old_lines]
    old_len = len(old_normalized)

    def check_at(pos: int) -> bool:
        if pos < 0 or pos + old_len > len(lines):
            return False
        for j, old_line in enumerate(old_normalized):
            if _normalize_line(lines[pos + j]) != old_line:
                return False
        return True

    # 1. 在期望位置精确匹配
    if check_at(expected_start):
        return expected_start

    # 2. 在附近搜索（±50行）
    search_range = 50
    for offset in range(1, search_range + 1):
        if check_at(expected_start - offset):
            return expected_start - offset
        if check_at(expected_start + offset):
            return expected_start + offset

    # 3. 全局搜索（最后手段）
    for k in range(len(lines) - old_len + 1):
        if check_at(k):
            return k

    return -1


def apply_patch(file_path: Path, patch_content: str) -> str:
    """应用补丁到文件（自动识别格式）

    优先识别 SEARCH/REPLACE 块格式（LLM 容错率更高，推荐）；
    未命中则回退解析标准 unified diff 格式（--- /+++ /@@，兼容旧输入）。
    补丁应用前自动备份原文件（创建 .bak）。

    Args:
        file_path: 目标文件路径
        patch_content: SEARCH/REPLACE 块或 unified diff 格式的补丁内容

    Returns:
        应用补丁后的完整文件内容

    Raises:
        FileNotFoundError: 目标文件不存在
        ValueError: 补丁格式无效或上下文不匹配
    """
    # P2-10：SEARCH/REPLACE 块格式优先分派（保留 unified diff 作为兼容输入）
    if _SR_SEARCH_MARK in patch_content:
        return apply_search_replace(file_path, patch_content)

    file_path = Path(file_path)

    if not file_path.exists():
        raise FileNotFoundError(f"目标文件不存在: {file_path}")

    # 读取原文件
    original = file_path.read_text(encoding='utf-8')

    # 记录原始换行符类型
    original_ends_with_newline = original.endswith('\n')
    original.endswith('\r\n')

    # 按行分割（不保留换行符，后续统一拼接）
    original_lines = original.splitlines()

    # 解析补丁
    hunks = _parse_unified_diff(patch_content)

    if not hunks:
        raise ValueError(
            "补丁内容无效：未找到任何 @@ hunk 头。"
            "请确保补丁使用标准 unified diff 格式"
            "（以 @@ -start,count +start,count @@ 开头）。"
        )

    # 应用补丁
    result_lines = list(original_lines)
    line_offset = 0  # 累计行偏移量（前一个 hunk 修改导致的行数变化）
    applied_hunks = 0

    for hunk_idx, hunk in enumerate(hunks):
        # 提取 old lines（context + removed）和 new lines（context + added）
        old_lines = [content for typ, content in hunk['lines'] if typ in (' ', '-')]
        new_lines = [content for typ, content in hunk['lines'] if typ in (' ', '+')]

        # 计算在 result_lines 中的搜索起始位置
        expected_start = hunk['old_start'] - 1 + line_offset  # 转为 0-indexed
        expected_start = max(0, min(expected_start, len(result_lines)))

        # 在原文件中查找匹配位置
        match_pos = _find_match_position(result_lines, old_lines, expected_start)

        if match_pos < 0:
            # 构建详细的错误信息
            old_preview = '\n'.join(old_lines[:5])
            if len(old_lines) > 5:
                old_preview += '\n...(更多行已省略)'
            raise ValueError(
                f"补丁第 {hunk_idx + 1} 个 hunk 上下文不匹配"
                f"（期望从原文件第 {hunk['old_start']} 行开始）。\n"
                f"期望的上下文/删除行内容（前5行）:\n{old_preview}\n"
                f"请确认补丁基于文件最新内容生成，且上下文行准确无误。"
            )

        # 替换匹配的行
        if old_lines:
            result_lines[match_pos:match_pos + len(old_lines)] = new_lines
        else:
            # 纯插入：在 match_pos 处插入新行
            result_lines[match_pos:match_pos] = new_lines

        # 更新行偏移
        line_offset += len(new_lines) - len(old_lines)
        applied_hunks += 1

    # 重建文件内容
    result = '\n'.join(result_lines)

    # 保持原始换行符结尾行为
    if original_ends_with_newline and not result.endswith('\n'):
        result += '\n'
    elif not original_ends_with_newline and result.endswith('\n'):
        result = result.rstrip('\n')

    # 补丁应用前备份原文件
    _备份文件(file_path)

    # 写入结果
    file_path.write_text(result, encoding='utf-8')

    # 写入成功后清理 .bak 残留（与 write_plugin_file 保持一致）
    try:
        bak_path = Path(str(file_path) + '.bak')
        if bak_path.exists():
            bak_path.unlink()
    except OSError:
        pass

    logger.info("补丁应用成功: %s, 共 %d 个 hunk", file_path.name, applied_hunks)
    return result


# ─── SEARCH/REPLACE 块编辑（P2-10） ─────────────────────────

_SR_SEARCH_MARK = '<<<<<<< SEARCH'
_SR_DIVIDER_MARK = '======='
_SR_REPLACE_MARK = '>>>>>>> REPLACE'


def _解析SR块(patch_content: str) -> list:
    """解析 SEARCH/REPLACE 块，返回 [(search_lines, replace_lines), ...]

    格式：
        <<<<<<< SEARCH
        原文内容（逐字符与文件一致）
        =======
        替换后内容
        >>>>>>> REPLACE
    """
    blocks = []
    lines = patch_content.splitlines()
    i = 0
    while i < len(lines):
        if lines[i].strip() != _SR_SEARCH_MARK:
            i += 1
            continue
        search_lines = []
        replace_lines = []
        i += 1
        while i < len(lines) and lines[i].strip() != _SR_DIVIDER_MARK:
            if lines[i].strip() == _SR_REPLACE_MARK:
                raise ValueError(
                    f"第 {len(blocks) + 1} 个 SEARCH/REPLACE 块缺少 ======= 分隔线，"
                    "格式应为：<<<<<<< SEARCH / 原文 / ======= / 替换文本 / >>>>>>> REPLACE"
                )
            search_lines.append(lines[i])
            i += 1
        if i >= len(lines):
            raise ValueError(f"第 {len(blocks) + 1} 个 SEARCH/REPLACE 块缺少 ======= 分隔线")
        i += 1  # 跳过 =======
        while i < len(lines) and lines[i].strip() != _SR_REPLACE_MARK:
            replace_lines.append(lines[i])
            i += 1
        if i >= len(lines):
            raise ValueError(f"第 {len(blocks) + 1} 个 SEARCH/REPLACE 块缺少 >>>>>>> REPLACE 结束标记")
        i += 1  # 跳过 >>>>>>> REPLACE
        if not any(ln.strip() for ln in search_lines):
            raise ValueError(
                f"第 {len(blocks) + 1} 个块的 SEARCH 部分为空。"
                "新增内容请在 SEARCH 中包含插入点附近的原文行，并在 REPLACE 中一并写出。"
            )
        blocks.append((search_lines, replace_lines))
    return blocks


def _查找SR匹配(file_lines: list, search_lines: list):
    """在文件行中查找 SEARCH 块的匹配位置（逐级放宽的容错匹配）

    层级1：逐字符精确匹配
    层级2：忽略行尾空白（容忍末尾空格/制表符差异）
    层级3：忽略行首尾空白（容忍缩进差异，替换文本仍按 REPLACE 块原样写入）

    Returns:
        (匹配起始索引, 匹配方式描述)，未找到返回 (-1, '')

    Raises:
        ValueError: 同一层级匹配到多处（无法安全定位）
    """
    n = len(search_lines)
    if n == 0 or n > len(file_lines):
        return -1, ''

    候选方案 = (
        ('精确', lambda ln: ln),
        ('忽略行尾空白', lambda ln: ln.rstrip()),
        ('忽略首尾空白', lambda ln: ln.strip()),
    )
    for 方式, 归一 in 候选方案:
        目标 = [归一(ln) for ln in search_lines]
        命中 = [k for k in range(len(file_lines) - n + 1)
              if [归一(ln) for ln in file_lines[k:k + n]] == 目标]
        if len(命中) == 1:
            return 命中[0], 方式
        if len(命中) > 1:
            raise ValueError(
                f"SEARCH 块在文件中匹配到 {len(命中)} 处（{方式}），无法安全定位。"
                f"请在 SEARCH 块中增加前后上下文行，确保内容在文件中唯一。"
            )
    return -1, ''


def _最近候选提示(file_lines: list, search_lines: list) -> str:
    """SEARCH 块未匹配时，用 difflib 找出文件中最相近的片段回传给模型自纠"""
    import difflib
    n = min(len(search_lines), 10)
    if n == 0 or not file_lines:
        return ""
    目标文本 = '\n'.join(ln.strip() for ln in search_lines[:n])
    最佳分 = 0.0
    最佳位置 = -1
    for k in range(len(file_lines) - n + 1):
        窗口文本 = '\n'.join(ln.strip() for ln in file_lines[k:k + n])
        分 = difflib.SequenceMatcher(None, 目标文本, 窗口文本).ratio()
        if 分 > 最佳分:
            最佳分 = 分
            最佳位置 = k
    if 最佳位置 < 0 or 最佳分 < 0.4:
        return ""
    片段 = '\n'.join(file_lines[最佳位置:最佳位置 + n])[:600]
    return (
        f"文件中最相近的片段（第 {最佳位置 + 1} 行起，相似度 {最佳分:.0%}）：\n"
        f"{片段}"
    )


def apply_search_replace(file_path: Path, patch_content: str) -> str:
    """应用 SEARCH/REPLACE 块到文件（P2-10，推荐的增量编辑格式）

    相比 unified diff：无需行号、无需 hunk 头，LLM 生成失误率更低；
    匹配失败时回传最相近候选片段，帮助模型自纠。
    多个块按顺序依次应用（后续块在前块修改后的内容上匹配）。

    Returns:
        应用后的完整文件内容

    Raises:
        FileNotFoundError: 目标文件不存在
        ValueError: 块格式无效 / 未找到匹配 / 匹配不唯一
    """
    file_path = Path(file_path)
    if not file_path.exists():
        raise FileNotFoundError(f"目标文件不存在: {file_path}")

    original = file_path.read_text(encoding='utf-8')
    original_ends_with_newline = original.endswith('\n')
    file_lines = original.splitlines()

    blocks = _解析SR块(patch_content)
    if not blocks:
        raise ValueError(
            "补丁内容无效：未找到任何 SEARCH/REPLACE 块。"
            "格式：<<<<<<< SEARCH / 原文内容 / ======= / 替换内容 / >>>>>>> REPLACE"
        )

    容错匹配次数 = 0
    for bi, (search_lines, replace_lines) in enumerate(blocks):
        pos, 匹配方式 = _查找SR匹配(file_lines, search_lines)
        if pos < 0:
            候选提示 = _最近候选提示(file_lines, search_lines)
            预览 = '\n'.join(search_lines[:5])
            if len(search_lines) > 5:
                预览 += '\n...(更多行已省略)'
            raise ValueError(
                f"第 {bi + 1} 个 SEARCH 块未在文件中找到匹配内容。\n"
                f"SEARCH 块内容（前5行）：\n{预览}\n"
                + (f"{候选提示}\n" if 候选提示 else "")
                + "请基于文件最新内容重新生成 SEARCH 块（逐字符一致）。"
            )
        if 匹配方式 != '精确':
            容错匹配次数 += 1
        file_lines[pos:pos + len(search_lines)] = replace_lines

    result = '\n'.join(file_lines)
    if original_ends_with_newline and not result.endswith('\n'):
        result += '\n'
    elif not original_ends_with_newline and result.endswith('\n'):
        result = result.rstrip('\n')

    # 应用前备份原文件（与 apply_patch 保持一致）
    _备份文件(file_path)
    file_path.write_text(result, encoding='utf-8')
    try:
        bak_path = Path(str(file_path) + '.bak')
        if bak_path.exists():
            bak_path.unlink()
    except OSError:
        pass

    logger.info("SEARCH/REPLACE 应用成功: %s, 共 %d 个块（容错匹配 %d 个）",
                file_path.name, len(blocks), 容错匹配次数)
    return result


# ─── 跨文件搜索 ─────────────────────────────────────────

def grep_plugin_files(plugin_path, pattern, glob_filter=None, use_regex=False,
                      max_results=30, context_lines=2):
    """跨文件搜索关键词或正则表达式。

    遍历插件目录下所有匹配 glob_filter 的文件，返回每个匹配的行号和上下文。

    参数:
        plugin_path: 插件根目录的完整路径
        pattern: 搜索关键词或正则表达式
        glob_filter: 文件过滤 glob 模式（可选，如 '*.py'、'*.{js,ts}'）
        use_regex: 是否启用正则模式（默认 False = 不区分大小写关键词搜索）
        max_results: 最多返回的匹配结果数（默认 30，防止结果过多）
        context_lines: 每个匹配前后显示的行数（默认 2，最大 5）

    返回: (success: bool, result_str_or_error: str)
    """
    plugin_path = Path(plugin_path)
    if not plugin_path.exists():
        return False, "插件目录不存在"

    if not pattern or len(pattern) > 200:
        return False, "搜索关键词长度需在 1-200 字符之间"

    # context_lines 限制
    try:
        context_lines = int(context_lines)
    except (TypeError, ValueError):
        context_lines = 2
    context_lines = max(0, min(context_lines, 5))

    # 编译匹配器
    if use_regex:
        try:
            regex = re.compile(pattern)
        except re.error as e:
            return False, f"正则表达式语法错误: {e}"
        def matcher(text):
            return bool(regex.search(text))
        pattern_display = f"正则 /{pattern}/"
    else:
        lowered = pattern.lower()
        def matcher(text):
            return lowered in text.lower()
        pattern_display = f"「{pattern}」"

    # 收集所有匹配文件（按 glob_filter 过滤）
    if glob_filter:
        try:
            candidates = list(plugin_path.glob(f"**/{glob_filter}"))
        except Exception:
            return False, f"glob 模式无效: {glob_filter}"
    else:
        candidates = list(plugin_path.rglob("*"))

    # 过滤：只保留文件，跳过忽略目录、超大文件
    candidates = [
        f for f in candidates
        if f.is_file()
        and not any(d in f.parts for d in _IGNORED_DIRS)
        and f.stat().st_size <= MAX_READ_FILE_SIZE
        and not f.name.endswith(('.bak', '.pyc', '.pyo', '.pyd', '.so', '.dll'))
    ]

    if not candidates:
        glob_info = f"（过滤: {glob_filter}）" if glob_filter else ""
        return True, f"未找到可搜索的文件{glob_info}"

    total_matches = 0
    file_results = []  # [(file_path, [(line_no, line_content), ...])]

    for file_path in candidates:
        if total_matches >= max_results:
            break

        # 读取文件内容
        try:
            content = file_path.read_text(encoding='utf-8')
        except (UnicodeDecodeError, OSError):
            continue

        lines = content.splitlines()
        file_matches = []

        for i, line in enumerate(lines):
            if matcher(line):
                # 提取上下文窗口
                start = max(0, i - context_lines)
                end = min(len(lines), i + context_lines + 1)
                context_block = []
                for ci in range(start, end):
                    prefix = ">>>" if ci == i else "   "
                    context_block.append(f"{prefix} L{ci + 1:5d}: {lines[ci]}")
                file_matches.append("\n".join(context_block))
                total_matches += 1
                if total_matches >= max_results:
                    break

        if file_matches:
            rel_path = file_path.relative_to(plugin_path)
            file_results.append((str(rel_path), file_matches))

    if not file_results:
        return True, f"在 {len(candidates)} 个文件中未找到匹配 {pattern_display} 的内容"

    # 构建输出
    glob_info = f" 过滤: {glob_filter}" if glob_filter else ""
    output = f"🔍 跨文件搜索 {pattern_display} ({len(candidates)} 文件{glob_info})\n"
    output += f"共 {total_matches} 处匹配（显示前 {max_results} 个）\n"
    output += "=" * 50 + "\n\n"

    for rel_path, matches in file_results:
        output += f"📄 {rel_path}（{len(matches)} 处）:\n"
        for m in matches:
            output += f"{m}\n\n"

    return True, output


# ─── Glob 文件查找 ────────────────────────────────────

def find_plugin_files(plugin_path, glob_pattern):
    """按 glob 模式查找文件。

    参数:
        plugin_path: 插件根目录的完整路径
        glob_pattern: 文件 glob 模式，如 '*.py'、'**/test_*.py'

    返回: (success: bool, result_str_or_error: str)
    """
    plugin_path = Path(plugin_path)
    if not plugin_path.exists():
        return False, "插件目录不存在"

    if not glob_pattern or len(glob_pattern) > 500:
        return False, "glob 模式长度需在 1-500 字符之间"

    try:
        matches = list(plugin_path.glob(f"**/{glob_pattern}"))
    except Exception as e:
        return False, f"glob 模式无效: {e}"

    # 过滤忽略目录
    matches = [
        m for m in matches
        if not any(d in m.parts for d in _IGNORED_DIRS)
    ]

    if not matches:
        return True, f"未找到匹配 '{glob_pattern}' 的文件"

    # 构建输出：路径 + 类型 + 大小
    lines = [f"📁 找到 {len(matches)} 个匹配 '{glob_pattern}' 的文件:\n"]
    for m in sorted(matches, key=lambda x: (not x.is_file(), str(x).lower())):
        rel = m.relative_to(plugin_path)
        icon = "📄" if m.is_file() else "📁"
        size_str = ""
        if m.is_file():
            try:
                size = m.stat().st_size
                if size < 1024:
                    size_str = f" ({size}B)"
                elif size < 1024 * 1024:
                    size_str = f" ({size / 1024:.1f}KB)"
                else:
                    size_str = f" ({size / (1024 * 1024):.1f}MB)"
            except OSError:
                pass
        lines.append(f"{icon} {rel}{size_str}")

    return True, "\n".join(lines)


# ─── 文件重命名/移动 ─────────────────────────────────

def rename_plugin_file(plugin_path, source_path, dest_path):
    """重命名或移动文件。

    安全校验：
    1. 两端路径均必须在插件目录内
    2. 源文件必须存在
    3. 目标文件不存在时才允许（防止意外覆盖）
    4. 自动创建目标父目录
    5. 操作前备份源文件为 .bak

    参数:
        plugin_path: 插件根目录的完整路径
        source_path: 源文件相对路径
        dest_path: 目标文件相对路径

    返回: (success: bool, message: str)
    """
    plugin_root = Path(plugin_path).resolve()

    # 安全校验：解析路径
    try:
        src = (plugin_root / source_path).resolve()
        src.relative_to(plugin_root)
    except ValueError:
        return False, f"安全错误：源路径 '{source_path}' 超出插件目录范围"

    try:
        dst = (plugin_root / dest_path).resolve()
        dst.relative_to(plugin_root)
    except ValueError:
        return False, f"安全错误：目标路径 '{dest_path}' 超出插件目录范围"

    if not src.exists():
        return False, f"源文件不存在: {source_path}"

    if src == dst:
        return False, "源路径和目标路径相同，无需操作"

    if dst.exists():
        return False, f"目标文件已存在: {dest_path}（不允许覆盖，请先删除或选择其他名称）"

    # 创建目标父目录
    try:
        dst.parent.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        return False, f"无法创建目标目录: {e}"

    # 备份源文件
    _备份文件(src)

    # 执行移动
    try:
        shutil.move(str(src), str(dst))
    except OSError as e:
        return False, f"移动失败: {e}"

    # 清理 .bak
    try:
        bak_path = Path(str(src) + '.bak')
        if bak_path.exists():
            bak_path.unlink()
    except OSError:
        pass

    logger.info("文件移动成功: %s → %s", source_path, dest_path)
    return True, f"✅ 已移动: {source_path} → {dest_path}"


# ─── 文件删除 ─────────────────────────────────────────

def delete_plugin_file(plugin_path, file_path):
    """删除插件目录内的指定文件。

    安全校验：
    1. 路径必须在插件目录内
    2. 仅允许删除文件，不允许删除目录
    3. 不允许删除 .bak 备份文件（防止连备份一起清掉后无法恢复）
    4. 删除前备份为 .bak（覆盖式，保留最近一份，可手动恢复）

    参数:
        plugin_path: 插件根目录的完整路径
        file_path: 相对于插件根目录的文件路径

    返回: (success: bool, message: str)
    """
    plugin_root = Path(plugin_path).resolve()

    try:
        target = (plugin_root / file_path).resolve()
        target.relative_to(plugin_root)
    except ValueError:
        return False, f"安全错误：文件路径 '{file_path}' 超出插件目录范围"

    if not target.exists():
        return False, f"文件不存在: {file_path}"

    if not target.is_file():
        return False, f"不是文件（不允许删除目录）: {file_path}"

    if target.suffix == '.bak':
        return False, f"不允许删除备份文件: {file_path}"

    # 删除前备份为 .bak，误删可手动恢复
    _备份文件(target)

    try:
        target.unlink()
    except OSError as e:
        return False, f"删除失败: {e}"

    logger.info("文件删除成功: %s（已备份为 .bak）", file_path)
    return True, f"已删除: {file_path}（原文件已备份为 {file_path}.bak，可手动恢复）"


# ─── Python 语法检查 ────────────────────────────────

def check_python_syntax(plugin_path, file_path):
    """检查 Python 文件的语法是否正确。

    使用 py_compile 编译为字节码但不写入磁盘，仅检查语法。
    安全校验：文件路径必须在插件目录内。

    参数:
        plugin_path: 插件根目录的完整路径
        file_path: 相对于插件根目录的 Python 文件路径

    返回: (success: bool, result_str_or_error: str)
    """
    plugin_root = Path(plugin_path).resolve()
    full_path = (plugin_root / file_path).resolve()

    try:
        full_path.relative_to(plugin_root)
    except ValueError:
        return False, f"安全错误：文件路径 '{file_path}' 超出插件目录范围"

    if not full_path.exists():
        return False, f"文件不存在: {file_path}"

    if not full_path.is_file():
        return False, f"不是文件: {file_path}"

    if not full_path.suffix.lower() == '.py':
        return False, f"不是 Python 文件: {file_path}（后缀: {full_path.suffix}）"

    # 检查文件大小
    try:
        file_size = full_path.stat().st_size
        if file_size < 0 or file_size > MAX_READ_FILE_SIZE:
            return False, f"文件太大 ({file_size / (1024*1024):.1f}MB)，超过限制"
    except (OSError, ValueError) as e:
        return False, f"文件大小检查失败: {e}"

    import py_compile
    import tempfile

    try:
        # 编译到临时文件验证语法
        with tempfile.NamedTemporaryFile(suffix='.pyc', delete=False) as tmp:
            tmp_path = tmp.name
        try:
            py_compile.compile(str(full_path), cfile=tmp_path, doraise=True)
            return True, f"✅ 语法检查通过: {file_path}"
        finally:
            try:
                Path(tmp_path).unlink(missing_ok=True)
            except OSError:
                pass
    except py_compile.PyCompileError as e:
        return False, f"❌ 语法错误: {file_path}\n{e}"
    except Exception as e:
        return False, f"❌ 语法检查失败: {type(e).__name__}: {e}"


# ─── ruff 快速 lint（写后自动检查用）──────────────────────

# ruff 可用性缓存：None=未探测，False=不可用（避免每次写入都空跑子进程）
_ruff_available = None

# 仅高信号规则：语法错误/比较错误/未定义名/未用导入/重复定义（LLM 高频错型）
_RUFF_SELECT_RULES = "E9,F63,F7,F82,F401,F811"


def ruff_quick_lint(plugin_path, file_paths, timeout=10):
    """对若干 .py 文件执行 ruff 快速 lint（仅高信号规则）

    py_compile 只能发现语法错误；ruff 的 F82（未定义名）、F401（未用导入）、
    F811（重复定义）正是 LLM 生成代码的高频错型。
    使用 --isolated 忽略项目配置，保证规则集稳定、输出无风格噪音。

    返回: (available: bool, report: str)
        ruff 未安装或执行失败时 available=False，调用方应静默跳过。
    """
    global _ruff_available
    if _ruff_available is False:
        return False, ""

    import subprocess
    import sys

    plugin_root = Path(plugin_path).resolve()
    safe_files = []
    for fp in file_paths:
        full_path = (plugin_root / fp).resolve()
        try:
            full_path.relative_to(plugin_root)
        except ValueError:
            continue
        if full_path.is_file() and full_path.suffix.lower() == ".py":
            safe_files.append(str(full_path.relative_to(plugin_root)))
    if not safe_files:
        return False, ""

    cmd = [
        sys.executable, "-m", "ruff", "check",
        "--isolated", "--quiet", "--no-cache",
        "--select", _RUFF_SELECT_RULES,
        *safe_files,
    ]
    creationflags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    try:
        proc = subprocess.run(
            cmd, cwd=str(plugin_root), capture_output=True, text=True,
            timeout=timeout, creationflags=creationflags,
        )
    except subprocess.TimeoutExpired:
        logger.warning(f"ruff lint 超时（{timeout}s），跳过本次检查")
        return False, ""
    except OSError as e:
        logger.warning(f"ruff lint 启动失败，禁用写后 lint: {e}")
        _ruff_available = False
        return False, ""

    # python -m ruff 未安装时 stderr 含 "No module named"，缓存不可用避免重复探测
    if proc.returncode != 0 and "No module named" in (proc.stderr or ""):
        logger.info("ruff 未安装，写后自动 lint 已禁用（可 pip install ruff 启用）")
        _ruff_available = False
        return False, ""
    # 0=无问题，1=发现违规；其他退出码为 ruff 自身错误，不缓存不可用（可能是偶发）
    if proc.returncode not in (0, 1):
        logger.warning(f"ruff lint 执行异常（exit={proc.returncode}）: {(proc.stderr or '')[:200]}")
        return False, ""

    _ruff_available = True
    if proc.returncode == 0:
        return True, "✅ [自动lint] ruff 检查通过（未定义名/未用导入等无问题）"
    output = (proc.stdout or "").strip()
    if len(output) > 800:
        output = output[:800] + "\n...（lint 输出已截断）"
    return True, f"⚠️ [自动lint] ruff 发现以下问题（不阻塞写入，建议修复）：\n{output}"


# ─── Shell 命令执行（安全受限）──────────────────────────

# 允许的命令前缀白名单
_ALLOWED_COMMAND_PREFIXES = [
    ("pip install",     True),   # (前缀, 是否自动追加安全参数)
    ("pip3 install",    True),
    ("python -m pip install", True),
    ("git status",      False),
    ("git diff",        False),
    ("git log",         False),
    ("python -c ",      False),
    ("python3 -c ",     False),
    ("python -m py_compile ", False),
    ("dir",             False),
    ("ls",              False),
]

# python/pip 类命令 → 当前解释器参数映射（前缀, 等效参数前缀）
# 便携版/嵌入式 Python 不在 PATH 中，直接执行字面量 "python"/"pip" 必然「命令未找到」，
# 故统一改由 sys.executable 执行。条目与上方白名单一一对应。
_PYTHON_命令前缀映射 = [
    ("python -m pip install",  "-m pip install"),
    ("pip3 install",           "-m pip install"),
    ("pip install",            "-m pip install"),
    ("python -m py_compile ",  "-m py_compile "),
    ("python -c ",             "-c "),
    ("python3 -c ",            "-c "),
]


def _映射到当前解释器(cmd: str):
    """命令属于 python/pip 类时返回「去掉解释器后的等效参数字符串」，否则返回 None"""
    for 前缀, 参数前缀 in _PYTHON_命令前缀映射:
        if cmd.startswith(前缀) or cmd.lower().startswith(前缀):
            return 参数前缀 + cmd[len(前缀):]
    return None


# 命令中禁止出现的危险命令词（在完整命令文本上检查）
_BLOCKED_PATTERNS = [
    "rm ", "rmdir ", "del ", "rd ", "erase ",
    "format ", "mkfs", "diskpart",
    "curl ", "wget ", "nc ", "telnet ", "nslookup ",
    "sudo ", "su ", "chmod 777", "chown ",
]

# shell 元字符黑名单（仅在引号外检查）：
# 执行方式为 subprocess 无 shell 模式，引号内的元字符不会被 shell 解释，
# 例如 python -c "import os; print(1)" 中的分号是合法 Python 语法，不应误伤
_BLOCKED_SHELL_CHARS = [
    ">>", ">",          # 输出重定向（难以验证目标路径安全）
    "|",                # 管道（无法校验管道后的命令，含 || 场景）
    "&&", "&", ";",     # 命令链 / 后台执行
    "`", "$(",          # 命令替换
]


def _遮蔽引号内容(cmd: str) -> str:
    """将单/双引号内的内容替换为空格（保留引号本身），仅用于 shell 元字符检测"""
    结果 = []
    引号 = None
    for ch in cmd:
        if 引号:
            if ch == 引号:
                引号 = None
                结果.append(ch)
            else:
                结果.append(' ')
        elif ch in ('"', "'"):
            引号 = ch
            结果.append(ch)
        else:
            结果.append(ch)
    return ''.join(结果)

# 输出大小上限
_MAX_COMMAND_OUTPUT = 20 * 1024  # 20KB


def execute_command(plugin_path, command, timeout=60):
    """在受控环境中执行 shell 命令。

    安全措施：
    1. 命令前缀白名单：仅允许 pip install / git / python -c 等安全命令
    2. 参数黑名单：禁止 rm/del/curl/管道/重定向等危险模式
    3. 超时控制：默认 60 秒，最大 120 秒
    4. 输出限制：最多返回 20KB
    5. 工作目录限制：始终在插件目录内执行
    6. 无标准输入：命令必须是纯批处理模式

    参数:
        plugin_path: 插件根目录的完整路径
        command: 要执行的命令字符串
        timeout: 超时秒数（默认 60，最大 120）

    返回: (success: bool, output_or_error: str)
    """
    import shlex
    import subprocess

    if not command or not command.strip():
        return False, "命令不能为空"

    command = command.strip()

    # 超时限制
    try:
        timeout = int(timeout)
    except (TypeError, ValueError):
        timeout = 60
    timeout = max(5, min(timeout, 120))

    # ── 白名单检查 ──
    matched = False
    auto_safe_args = False
    for prefix, add_safe in _ALLOWED_COMMAND_PREFIXES:
        if command.startswith(prefix) or command.lower().startswith(prefix):
            matched = True
            auto_safe_args = add_safe
            break

    if not matched:
        allowed_list = "\n".join(f"  • {p}" for p, _ in _ALLOWED_COMMAND_PREFIXES)
        return False, f"❌ 不允许的命令。仅支持以下前缀：\n{allowed_list}\n\n当前命令: {command[:100]}"

    # ── 危险模式检查 ──
    cmd_lower = command.lower()
    for pattern in _BLOCKED_PATTERNS:
        if pattern in cmd_lower:
            return False, f"❌ 命令包含危险模式 '{pattern.strip()}'，已拒绝执行"

    # shell 元字符仅检查引号外部分（引号内属于 python -c 等的代码内容，无 shell 不会被解释）
    遮蔽命令 = _遮蔽引号内容(command)
    for pattern in _BLOCKED_SHELL_CHARS:
        if pattern in 遮蔽命令:
            return False, f"❌ 命令包含危险模式 '{pattern}'（引号外），已拒绝执行"

    # ── pip install 自动追加安全参数 ──
    if auto_safe_args:
        if "--quiet" not in command and "-q" not in command.split():
            command += " --quiet"
        if "--no-input" not in command:
            command += " --no-input"
        if "--disable-pip-version-check" not in command:
            command += " --disable-pip-version-check"

    # ── 解析并执行 ──
    # python/pip 前缀统一解析到当前解释器，避免便携版无 PATH 时找不到命令
    解释器参数 = _映射到当前解释器(command)

    if os.name == "nt":
        # Windows：直接传命令字符串（仍无 shell），由 CreateProcess 原生解析引号与反斜杠，
        # 避免 shlex posix 模式把 S:\xxx 这类路径的反斜杠当转义符吃掉
        if 解释器参数 is not None:
            args = f'"{sys.executable}" {解释器参数}'
            首命令 = sys.executable
        else:
            args = command
            首命令 = command.split()[0]
    else:
        try:
            拆分 = shlex.split(解释器参数 if 解释器参数 is not None else command)
        except ValueError as e:
            return False, f"命令解析失败: {e}"
        if not 拆分:
            return False, "命令解析为空"
        if 解释器参数 is not None:
            args = [sys.executable, *拆分]
            首命令 = sys.executable
        else:
            args = 拆分
            首命令 = 拆分[0]

    plugin_dir = Path(plugin_path).resolve()
    if not plugin_dir.exists():
        return False, f"插件目录不存在: {plugin_dir}"

    开始时刻 = time.monotonic()
    try:
        # 执行命令
        result = subprocess.run(
            args,
            cwd=str(plugin_dir),
            capture_output=True,
            text=True,
            timeout=timeout,
            stdin=subprocess.DEVNULL,
            env={**os.environ, "PIP_REQUIRE_VIRTUALENV": "false"},
        )
    except subprocess.TimeoutExpired:
        return False, f"❌ 命令执行超时（>{timeout}秒）: {command[:100]}"
    except FileNotFoundError:
        return False, f"❌ 命令未找到: {首命令}。请确认该命令已安装并在 PATH 中。"
    except PermissionError:
        return False, f"❌ 权限不足，无法执行: {首命令}"
    except OSError as e:
        return False, f"❌ 系统错误: {type(e).__name__}: {e}"
    实际耗时 = time.monotonic() - 开始时刻

    # ── 构建输出 ──
    output_parts = []
    if result.stdout:
        out = result.stdout
        if len(out) > _MAX_COMMAND_OUTPUT:
            out = out[:_MAX_COMMAND_OUTPUT] + f"\n...[stdout 已截断，原始输出 {len(result.stdout)} 字符]"
        output_parts.append(out)
    if result.stderr:
        err = result.stderr
        if len(err) > _MAX_COMMAND_OUTPUT // 2:
            err = err[:_MAX_COMMAND_OUTPUT // 2] + f"\n...[stderr 已截断，原始 {len(result.stderr)} 字符]"
        if output_parts:
            output_parts.append(f"\n--- stderr ---\n{err}")
        else:
            output_parts.append(err)

    output = "".join(output_parts) if output_parts else "(无输出)"

    # 构建最终返回（耗时为真实执行时间，而非超时配置上限）
    rc = result.returncode
    status = "✅" if rc == 0 else f"❌ (退出码 {rc})"
    header = f"{status} 命令: {command[:150]}\n耗时: {实际耗时:.1f}s（超时上限 {timeout}s）\n输出 ({len(output)} 字符):\n"

    return rc == 0, header + output
