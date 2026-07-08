"""统一日志配置模块

为 NodeCraft AI 插件提供统一的结构化日志支持，替代散落各处的 print 调用。

使用方式：
    from 后端.日志配置 import 获取日志器
    logger = 获取日志器("模块名")
    logger.warning("警告信息")
    logger.error("错误信息")
    logger.exception("捕获异常")

日志格式：[时间][级别][模块] 消息
日志层次：所有日志归属 NodeCraftAI 命名空间，避免污染 ComfyUI 主日志。
日志输出：控制台 INFO+（错误和异常显示在 ComfyUI 后端日志中）
"""
import logging
import sys


def 获取日志器(模块名: str) -> logging.Logger:
    """获取模块级别的日志器

    Args:
        模块名: 子模块标识（如 "聊天路由"、"本地模型客户端"）

    Returns:
        归属于 NodeCraftAI 命名空间的 Logger 实例
    """
    return logging.getLogger(f"NodeCraftAI.{模块名}")


def 初始化日志系统() -> None:
    """初始化全局日志配置

    应在应用启动时调用一次。重复调用会被忽略以避免重复 handler。
    所有日志输出到控制台（INFO+），错误和异常显示在 ComfyUI 后端日志中。
    """
    root_logger = logging.getLogger("NodeCraftAI")
    if root_logger.handlers:
        return  # 已初始化

    root_logger.setLevel(logging.INFO)
    # 防止日志冒泡到根 logger 引起重复输出
    root_logger.propagate = False

    # 控制台 handler：INFO+，输出到 ComfyUI 后端日志
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(logging.INFO)
    console_handler.setFormatter(logging.Formatter(
        '[%(asctime)s][%(levelname)s][%(name)s] %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    ))
    root_logger.addHandler(console_handler)

