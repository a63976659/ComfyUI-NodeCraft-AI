from pathlib import Path
import folder_paths


def get_plugin_root():
    """获取本插件根目录 (ComfyUI-NodeCraft-AI)，跨平台兼容"""
    return Path(__file__).parent.parent.resolve()


def get_comfyui_root():
    """获取 ComfyUI 安装根目录（从插件位置向上推导：custom_nodes/ComfyUI-NodeCraft-AI → ComfyUI）"""
    return get_plugin_root().parent.parent


def get_custom_nodes_path():
    """获取当前 ComfyUI 的 custom_nodes 目录"""
    try:
        paths = folder_paths.get_folder_paths("custom_nodes")
        if paths:
            return Path(paths[0])
    except (ImportError, Exception):
        pass
    # 备选：从插件位置推导
    return get_plugin_root().parent


def get_default_llm_path():
    """获取本地 LLM 的默认路径 (ComfyUI/models/LLM)"""
    models_dir = folder_paths.models_dir
    llm_path = Path(models_dir) / "LLM"
    llm_path.mkdir(parents=True, exist_ok=True)
    return llm_path
