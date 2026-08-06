"""文本处理节点 — ComfyUI 插件模板"""

from .text_node import 文本处理_Node

NODE_CLASS_MAPPINGS = {
    "文本处理": 文本处理_Node
}

NODE_DISPLAY_NAME_MAPPINGS = {
    "文本处理": "📝 文本处理节点 (Text Process)"
}

__all__ = ["NODE_CLASS_MAPPINGS", "NODE_DISPLAY_NAME_MAPPINGS"]
