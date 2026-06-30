"""文本处理节点 — ComfyUI 插件模板"""

from .text_node import TextProcessNode

NODE_CLASS_MAPPINGS = {
    "TextProcessNode": TextProcessNode
}

NODE_DISPLAY_NAME_MAPPINGS = {
    "TextProcessNode": "文本处理节点"
}

__all__ = ["NODE_CLASS_MAPPINGS", "NODE_DISPLAY_NAME_MAPPINGS"]
