import os
import sys

# 1. 将当前插件根目录加入系统路径，以支持导入中文目录作为 Python 模块
PLUGIN_DIR = os.path.dirname(os.path.abspath(__file__))
if PLUGIN_DIR not in sys.path:
    sys.path.insert(0, PLUGIN_DIR)

# 2. 向 ComfyUI 声明前端静态文件存放的相对目录
WEB_DIRECTORY = "./前端"

# ComfyUI 节点规范（即使只是侧边栏，也需要暴露这两个空字典防止报错）
NODE_CLASS_MAPPINGS = {}
NODE_DISPLAY_NAME_MAPPINGS = {}

# 3. 导入后端路由，装饰器在模块加载时自动注册
try:
    from 后端 import 接口路由
    print("[AI Coder] 后端接口路由加载成功！")
except Exception as e:
    print(f"[AI Coder] 后端加载失败: {e}")

__all__ = ["WEB_DIRECTORY", "NODE_CLASS_MAPPINGS", "NODE_DISPLAY_NAME_MAPPINGS"]
