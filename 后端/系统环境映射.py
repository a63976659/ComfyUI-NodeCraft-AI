import os
from pathlib import Path
import folder_paths


def 是否云端环境() -> bool:
    """判定当前是否运行于魔搭创空间云端环境。

    依据项目既有约定，``China_MAINLAND`` 或 ``MODELSCOPE_SPACE`` 任一环境变量
    存在（值非空）即视为云端 Space 部署；否则视为本地 ComfyUI 插件运行环境。

    本函数为跨模块通用工具：认证中间件、CSRF 校验等均依赖它来决定是否启用
    与公网安全相关的策略（如 JWT 默认密钥告警、CSRF 校验）。
    """
    for 变量名 in ("China_MAINLAND", "MODELSCOPE_SPACE"):
        if (os.environ.get(变量名) or "").strip():
            return True
    return False


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
