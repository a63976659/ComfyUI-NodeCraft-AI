"""模型加载节点 — ComfyUI 插件模板"""

from .model_loader import ModelLoaderNode

NODE_CLASS_MAPPINGS = {
    "ModelLoaderNode": ModelLoaderNode
}

NODE_DISPLAY_NAME_MAPPINGS = {
    "ModelLoaderNode": "模型加载节点"
}

__all__ = ["NODE_CLASS_MAPPINGS", "NODE_DISPLAY_NAME_MAPPINGS"]
