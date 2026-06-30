"""基础图像处理节点 — ComfyUI 插件模板"""

from .image_node import ImageProcessNode

NODE_CLASS_MAPPINGS = {
    "ImageProcessNode": ImageProcessNode
}

NODE_DISPLAY_NAME_MAPPINGS = {
    "ImageProcessNode": "图像处理节点"
}

WEB_DIRECTORY = "./js"

__all__ = ["NODE_CLASS_MAPPINGS", "NODE_DISPLAY_NAME_MAPPINGS", "WEB_DIRECTORY"]
