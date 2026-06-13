from pathlib import Path
import json
import re
from datetime import datetime

from .系统环境映射 import get_plugin_root


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

def load_sessions_list():
    """加载所有会话摘要（不含 messages），按创建时间倒序排列"""
    sessions_dir = get_sessions_dir()
    sessions = []
    for filepath in sessions_dir.glob("*.json"):
        try:
            data = json.loads(filepath.read_text(encoding="utf-8"))
            sessions.append({
                "id": data.get("id", ""),
                "title": data.get("title", "未命名会话"),
                "created_at": data.get("created_at", ""),
                "plugin_folder": data.get("plugin_folder", ""),
            })
        except Exception:
            continue
    # 按创建时间倒序
    sessions.sort(key=lambda x: x.get("created_at", ""), reverse=True)
    return sessions


def load_session(session_id):
    """加载指定会话完整数据，不存在返回 None"""
    filepath = get_sessions_dir() / f"{session_id}.json"
    if not filepath.exists():
        return None
    with open(filepath, "r", encoding="utf-8") as f:
        return json.load(f)


def save_session(session_data):
    """保存会话数据到文件"""
    session_id = session_data.get("id")
    if not session_id:
        raise ValueError("会话数据缺少 id 字段")
    filepath = get_sessions_dir() / f"{session_id}.json"
    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(session_data, f, ensure_ascii=False, indent=2)


def delete_session(session_id):
    """删除会话文件，返回是否成功"""
    filepath = get_sessions_dir() / f"{session_id}.json"
    if filepath.exists():
        filepath.unlink()
        return True
    return False


# ─── 设置管理 ─────────────────────────────────────────────

_DEFAULT_SETTINGS = {
    "model_source": "api",
    "local_path": "",
    "local_model_name": "",
    "base_url": "https://api.openai.com/v1",
    "model_name": "qwen2.5-coder-32b-instruct",
    "api_key": "",
    "temperature": 0.2,
    "max_tokens": 4096,
}


def load_settings():
    """加载设置，不存在则返回默认值"""
    filepath = get_data_dir() / "settings.json"
    if not filepath.exists():
        return dict(_DEFAULT_SETTINGS)
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            saved = json.load(f)
        # 合并默认值，确保新增字段有默认值
        result = dict(_DEFAULT_SETTINGS)
        result.update(saved)
        return result
    except Exception:
        return dict(_DEFAULT_SETTINGS)


def save_settings(settings):
    """保存设置到文件"""
    filepath = get_data_dir() / "settings.json"
    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(settings, f, ensure_ascii=False, indent=2)


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
            "NODE_CLASS_MAPPINGS = {}\n"
            "NODE_DISPLAY_NAME_MAPPINGS = {}\n"
            "__all__ = ['NODE_CLASS_MAPPINGS', 'NODE_DISPLAY_NAME_MAPPINGS']\n"
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
