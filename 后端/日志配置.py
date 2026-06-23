"""统一日志配置模块

为 NodeCraft AI 插件提供统一的结构化日志支持，替代散落各处的 print 调用。

使用方式：
    from 后端.日志配置 import 获取日志器
    logger = 获取日志器("模块名")
    logger.info("普通信息")
    logger.warning("警告信息")
    logger.error("错误信息")
    logger.exception("捕获异常")

日志格式：[时间][级别][模块] 消息
日志层次：所有日志归属 NodeCraftAI 命名空间，避免污染 ComfyUI 主日志。
"""
import logging
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path


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
    """
    root_logger = logging.getLogger("NodeCraftAI")
    if root_logger.handlers:
        return  # 已初始化

    root_logger.setLevel(logging.DEBUG)
    # 防止日志冒泡到根 logger 引起重复输出
    root_logger.propagate = False

    handler = logging.StreamHandler(sys.stdout)
    handler.setLevel(logging.DEBUG)

    formatter = logging.Formatter(
        '[%(asctime)s][%(levelname)s][%(name)s] %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )
    handler.setFormatter(formatter)
    root_logger.addHandler(handler)

    # 文件日志（带轮转）
    try:
        _配置文件日志(root_logger)
    except Exception as e:
        # 文件日志失败不影响控制台日志
        root_logger.warning(f"文件日志初始化失败（仅控制台输出）: {e}")


def _配置文件日志(logger: logging.Logger, 日志目录: str = None) -> None:
    """为日志器添加带轮转的文件 Handler。

    Args:
        logger: 目标 Logger（通常是 NodeCraftAI 根 Logger）。
        日志目录: 日志输出目录，默认为项目根目录下的 logs/ 。

    特性：
    - 单文件最大 100MB；
    - 滚动保留最近 10 份历史；
    - UTF-8 编码，避免中文乱码。
    """
    if 日志目录 is None:
        # 项目根 = 后端/ 的上级
        日志路径 = Path(__file__).resolve().parent.parent / "logs"
    else:
        日志路径 = Path(日志目录)
    日志路径.mkdir(parents=True, exist_ok=True)

    文件处理器 = RotatingFileHandler(
        日志路径 / "nodecraft_ai.log",
        maxBytes=100 * 1024 * 1024,  # 100MB
        backupCount=10,
        encoding="utf-8",
    )
    文件处理器.setLevel(logging.DEBUG)
    文件处理器.setFormatter(logging.Formatter(
        "[%(asctime)s][%(levelname)s][%(name)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    ))
    logger.addHandler(文件处理器)
