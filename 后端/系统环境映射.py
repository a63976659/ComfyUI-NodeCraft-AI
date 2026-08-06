from pathlib import Path

# folder_paths 为 ComfyUI 运行时模块：脱离 ComfyUI（单测/独立脚本）时不存在，
# 若顶层裸导入会拖垮整条导入链，故 try 化并在各函数内做兜底
try:
    import folder_paths
except Exception:
    folder_paths = None

# ─── 项目版本号（全局唯一源）─────────────────────────────────
# 在以下位置使用：
#   - 路由公共.py：导入并暴露为全局变量
#   - 接口文档.py：OpenAPI spec 的 info.version 字段
#   - __init__.py：启动日志输出版本号
# 文档（README / 技术文档）中的版本号允许与此处不同步，不强制修改。
项目版本 = "0.16.0"


def get_plugin_root():
    """获取本插件根目录 (ComfyUI-NodeCraft-AI)，跨平台兼容"""
    return Path(__file__).parent.parent.resolve()


def get_custom_nodes_path():
    """获取当前 ComfyUI 的 custom_nodes 目录"""
    if folder_paths is not None:
        try:
            paths = folder_paths.get_folder_paths("custom_nodes")
            if paths:
                return Path(paths[0])
        except Exception:
            pass
    # 备选：从插件位置推导（无论 custom_nodes 被重定向到哪，本插件父目录永远是 custom_nodes）
    return get_plugin_root().parent


def get_models_dir() -> Path:
    """获取用户模型根目录，兼容秋叶/便携版/桌面版三种发行版。

    优先 custom_nodes 同级的 models（三种发行版默认布局中 models 都与
    custom_nodes 同级，桌面版下两者同在用户数据目录）；不存在时回退
    folder_paths.models_dir（桌面版该值可能指向程序资源目录，故仅作兜底）。
    """
    同级models = get_custom_nodes_path().parent / "models"
    if 同级models.is_dir():
        return 同级models
    if folder_paths is not None:
        try:
            return Path(folder_paths.models_dir)
        except Exception:
            pass
    return 同级models


def get_default_llm_path():
    """获取本地 LLM 的默认路径 (ComfyUI/models/LLM)"""
    llm_path = get_models_dir() / "LLM"
    llm_path.mkdir(parents=True, exist_ok=True)
    return llm_path
