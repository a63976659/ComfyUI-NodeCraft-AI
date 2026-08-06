"""API 调用节点 — ComfyUI 插件模板"""

from .api_node import API调用_Node

NODE_CLASS_MAPPINGS = {
    "API调用": API调用_Node
}

NODE_DISPLAY_NAME_MAPPINGS = {
    "API调用": "🌐 API 调用节点 (HTTP Request)"
}

__all__ = ["NODE_CLASS_MAPPINGS", "NODE_DISPLAY_NAME_MAPPINGS"]
