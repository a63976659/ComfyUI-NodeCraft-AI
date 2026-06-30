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

# 3. 初始化统一日志系统（在导入任何后端模块之前）
from 后端.日志配置 import 初始化日志系统, 获取日志器
初始化日志系统()
logger = 获取日志器("启动")

# 输出项目版本号
from 后端.系统环境映射 import 项目版本
logger.info(f"NodeCraft AI v{项目版本} 启动中...")

# 4. 导入后端路由，装饰器在模块加载时自动注册
try:
    from 后端 import 接口路由
    logger.info("后端接口路由加载成功！")
except Exception as e:
    logger.exception(f"后端加载失败: {e}")

# 5. 在路由注册完成后执行数据迁移
try:
    import asyncio
    from 后端.数据迁移 import 执行迁移
    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            # 如果循环已在运行，创建任务
            loop.create_task(执行迁移())
        else:
            loop.run_until_complete(执行迁移())
    except RuntimeError:
        # 没有事件循环，创建新的
        asyncio.run(执行迁移())
    logger.info("数据迁移执行完成")
except Exception as e:
    logger.warning(f"数据迁移执行失败（忽略）: {e}")

__all__ = ["WEB_DIRECTORY", "NODE_CLASS_MAPPINGS", "NODE_DISPLAY_NAME_MAPPINGS"]
