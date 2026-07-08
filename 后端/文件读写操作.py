from pathlib import Path
import json
import os
import re
import shutil
import uuid
from datetime import datetime

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


# ─── 数据目录管理 ──────────────────────────────────────────

def get_data_dir():
    """获取/创建数据目录 (插件根目录/数据/)"""
    data_dir = Path(get_plugin_root()) / "数据"
    data_dir.mkdir(parents=True, exist_ok=True)
    return data_dir


def get_sessions_dir():
    """获取/创建会话目录 (插件根目录/数据/会话/)"""
    sessions_dir = get_data_dir() / "会话"
    sessions_dir.mkdir(parents=True, exist_ok=True)
    return sessions_dir


# ─── 会话 CRUD ─────────────────────────────────────────────

def load_sessions_list(session_type=None):
    """加载所有会话摘要（不含 messages），按创建时间倒序排列

    参数：
        session_type: 可选，按界面类型过滤会话。有效值: "develop"/"optimize"/"visualize"。
                      为 None 时返回全部会话。

    向后兼容：旧会话文件没有 type 字段时，默认为 "develop"。
    """
    sessions_dir = get_sessions_dir()
    sessions = []
    for filepath in sessions_dir.glob("*.json"):
        try:
            data = json.loads(filepath.read_text(encoding="utf-8"))
            # 旧会话文件没有 type 字段时默认为 "develop"，保持向后兼容
            sess_type = data.get("type", "develop")
            sessions.append({
                "id": data.get("id", ""),
                "title": data.get("title", "未命名会话"),
                "created_at": data.get("created_at", ""),
                "updated_at": data.get("updated_at", ""),
                "plugin_folder": data.get("plugin_folder", ""),
                "type": sess_type,
            })
        except Exception:
            continue
    # 按界面类型过滤（仅当传入有效类型时生效）
    if session_type is not None and session_type in _VALID_SESSION_TYPES:
        sessions = [s for s in sessions if s.get("type") == session_type]
    # 按创建时间倒序
    sessions.sort(key=lambda x: x.get("created_at", ""), reverse=True)
    return sessions


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
    """加载指定会话完整数据，不存在返回 None

    Bug 36 修复：JSON 解析失败时尝试加载 .bak 备份并恢复主文件。
    """
    filepath = get_sessions_dir() / f"{session_id}.json"
    if not filepath.exists():
        return None
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, UnicodeDecodeError) as e:
        logger.warning("会话文件损坏 [%s]: %s，尝试加载备份", session_id, e)
        bak_file = filepath.with_suffix(filepath.suffix + ".bak")
        if bak_file.exists():
            try:
                with open(bak_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                # 恢复成功，覆盖损坏文件
                with open(filepath, "w", encoding="utf-8") as f:
                    json.dump(data, f, ensure_ascii=False, indent=2)
                logger.info("从备份恢复会话 [%s] 成功", session_id)
                return data
            except Exception as be:
                logger.error("备份文件也无法加载 [%s]: %s", session_id, be)
        return None


def save_session(session_data):
    """保存会话数据到文件（写入前自动备份原文件为 .bak，覆盖式保留最近一份）

    Bug 14 修复：写入前对 session_data 进行结构与必需字段校验，
    避免写入空数据 / 错误类型 / 缺少 id 的非法会话文件。
    """
    # ─── 数据完整性校验 ────────────────────────────────────
    if not session_data or not isinstance(session_data, dict):
        raise ValueError("会话数据格式不正确")
    session_id = session_data.get("id")
    if not session_id:
        raise ValueError("会话数据缺少 id 字段")
    if 'messages' in session_data and not isinstance(session_data['messages'], list):
        raise ValueError("会话消息列表格式不正确")

    filepath = get_sessions_dir() / f"{session_id}.json"
    # 写入前备份原文件，避免写入失败导致数据丢失
    _备份文件(filepath)
    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(session_data, f, ensure_ascii=False, indent=2)
    # 写入成功后清理 .bak 残留（与 write_plugin_file 保持一致）
    try:
        bak_path = filepath.with_suffix(filepath.suffix + '.bak')
        if bak_path.exists():
            bak_path.unlink()
    except OSError:
        pass


def delete_session(session_id, delete_folder=False):
    """删除会话文件，可选同时删除关联的插件文件夹

    参数：
        session_id: 会话 ID
        delete_folder: 是否同时删除会话关联的 plugin_folder。删除失败不会阻止会话本身删除。

    返回：是否成功删除会话文件
    """
    filepath = get_sessions_dir() / f"{session_id}.json"
    if not filepath.exists():
        return False

    # 若需要删除文件夹，先读取会话数据获取 plugin_folder
    if delete_folder:
        try:
            with open(filepath, "r", encoding="utf-8") as f:
                session_data = json.load(f)
            plugin_folder = session_data.get("plugin_folder", "")
            if plugin_folder:
                _删除插件文件夹(plugin_folder)
        except Exception as e:
            logger.warning("删除关联文件夹失败: %s", e)
            # 文件夹删除失败不影响会话删除

    # 删除会话 JSON 文件
    filepath.unlink()
    return True


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
    "model_name": "qwen2.5-coder-32b-instruct",
    "temperature": 0.2,
    "max_tokens": 4096,
    "github_token": "",
    "github_username": "",
    "github_default_repo": "",
    "github_visibility": "public",
    "cloud_url": "https://studio-zhuzhiwei74521-comfyui-nodecraft-ai.api-inference.modelscope.net",
    "proactive_pitfall_check": True,
}

_SENSITIVE_KEYS = ["github_token", "api_key", "modelscope_sdk_token"]


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
        # 迁移：自动修正已废弃的旧 URL 为正确的 API 专用地址
        # 1. modelscope.cn/studios/ — 网页 URL，返回 HTML
        # 2. .ms.show — 旧 SDK 访问路径，已被禁止 API 调用（403）
        _cloud = result.get("cloud_url") or ""
        if "modelscope.cn/studios/" in _cloud or ".ms.show" in _cloud:
            result["cloud_url"] = "https://studio-zhuzhiwei74521-comfyui-nodecraft-ai.api-inference.modelscope.net"
        # 解密敏感字段
        for key in _SENSITIVE_KEYS:
            if key in result and result[key]:
                result[key] = _decrypt_value(result[key])
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
                raise ValueError("保存失败：加密模块不可用，无法安全存储敏感信息（如 API Key、Token），请安装 cryptography 库后重试")
    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(settings_to_save, f, ensure_ascii=False, indent=2)


# ─── 插件脚手架 ───────────────────────────────────────────

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
        (target_path / "逻辑处理模块").mkdir()
        (target_path / "界面与静态资源").mkdir()

        init_content = (
            "# AI 自动生成的 ComfyUI 节点注册入口\n"
            "WEB_DIRECTORY = \"./界面与静态资源\"\n"
            "\n"
            "NODE_CLASS_MAPPINGS = {}\n"
            "NODE_DISPLAY_NAME_MAPPINGS = {}\n"
            "__all__ = ['NODE_CLASS_MAPPINGS', 'NODE_DISPLAY_NAME_MAPPINGS', 'WEB_DIRECTORY']\n"
        )
        (target_path / "__init__.py").write_text(init_content, encoding="utf-8")

        (target_path / "README.md").write_text(
            f"# {plugin_name}\n\n基于 ComfyUI_AI_Coder 生成的自定义节点。",
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
        matcher = lambda ln: regex.search(ln) is not None
    else:
        lowered = pattern.lower()
        matcher = lambda ln: lowered in ln.lower()

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

    old_normalized = [_normalize_line(l) for l in old_lines]
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
    """应用 unified diff 补丁到文件

    解析标准 unified diff 格式（--- /+++ /@@）的补丁，应用到现有文件内容上。
    补丁应用前自动备份原文件（创建 .bak）。

    Args:
        file_path: 目标文件路径
        patch_content: unified diff 格式的补丁内容

    Returns:
        应用补丁后的完整文件内容

    Raises:
        FileNotFoundError: 目标文件不存在
        ValueError: 补丁格式无效或上下文不匹配
    """
    file_path = Path(file_path)

    if not file_path.exists():
        raise FileNotFoundError(f"目标文件不存在: {file_path}")

    # 读取原文件
    original = file_path.read_text(encoding='utf-8')

    # 记录原始换行符类型
    original_ends_with_newline = original.endswith('\n')
    original_ends_with_crlf = original.endswith('\r\n')

    # 按行分割（不保留换行符，后续统一拼接）
    original_lines = original.splitlines()

    # 解析补丁
    hunks = _parse_unified_diff(patch_content)

    if not hunks:
        raise ValueError("补丁内容无效：未找到任何 @@ hunk 头。请确保补丁使用标准 unified diff 格式（以 @@ -start,count +start,count @@ 开头）。")

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
