"""自定义 UI 扩展 — ComfyUI 插件模板

此模板演示前端 UI 扩展，不注册任何节点类。
后端仅提供 API 端点供前端 JS 调用。
"""

from aiohttp import web
from server import PromptServer
from folder_paths import get_output_directory

# ─── 后端 API 端点（供前端 JS 调用） ────────────────────────

# 获取 PromptServer 实例的路由列表
routes = PromptServer.instance.routes


@routes.get("/custom_ui_example/data")
async def custom_ui_get_data(request):
    """GET 端点：返回自定义数据供前端使用"""
    return web.json_response({
        "success": True,
        "data": {
            "message": "来自后端的自定义数据",
            "output_dir": get_output_directory(),
        },
    })


@routes.post("/custom_ui_example/action")
async def custom_ui_post_action(request):
    """POST 端点：接收前端的操作请求"""
    try:
        data = await request.json()
        action = data.get("action", "")
        return web.json_response({
            "success": True,
            "message": f"已执行操作: {action}",
        })
    except Exception as e:
        return web.json_response({"success": False, "error": str(e)}, status=400)


# ─── 节点注册（此模板无节点，仅前端扩展） ──────────────────

NODE_CLASS_MAPPINGS = {}
NODE_DISPLAY_NAME_MAPPINGS = {}

WEB_DIRECTORY = "./js"

__all__ = ["NODE_CLASS_MAPPINGS", "NODE_DISPLAY_NAME_MAPPINGS", "WEB_DIRECTORY"]
