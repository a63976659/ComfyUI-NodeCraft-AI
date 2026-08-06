"""模型加载节点 — ComfyUI 插件模板"""

from .model_loader import 模型加载_Node

NODE_CLASS_MAPPINGS = {
    "模型加载": 模型加载_Node
}

NODE_DISPLAY_NAME_MAPPINGS = {
    "模型加载": "📦 模型加载节点 (Model Loader)"
}

__all__ = ["NODE_CLASS_MAPPINGS", "NODE_DISPLAY_NAME_MAPPINGS"]
