"""API 调用节点 — ComfyUI 插件模板"""

from .api_node import APICallNode

NODE_CLASS_MAPPINGS = {
    "APICallNode": APICallNode
}

NODE_DISPLAY_NAME_MAPPINGS = {
    "APICallNode": "API 调用节点"
}

__all__ = ["NODE_CLASS_MAPPINGS", "NODE_DISPLAY_NAME_MAPPINGS"]
