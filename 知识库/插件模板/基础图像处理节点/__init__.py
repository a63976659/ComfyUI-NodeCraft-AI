"""基础图像处理节点 — ComfyUI 插件模板"""

from .image_node import 图像处理_Node

NODE_CLASS_MAPPINGS = {
    "图像处理": 图像处理_Node
}

NODE_DISPLAY_NAME_MAPPINGS = {
    "图像处理": "🖼️ 图像处理节点 (Image Process)"
}

WEB_DIRECTORY = "./js"

__all__ = ["NODE_CLASS_MAPPINGS", "NODE_DISPLAY_NAME_MAPPINGS", "WEB_DIRECTORY"]
