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
日志输出：控制台 INFO+（错误和异常显示在 ComfyUI 后端日志中）；
         文件 logs/nodecraft_ai.log（UTF-8，10MB×3 份轮转，重启后可追溯）
日志清理：每次启动时自动清理 logs 目录——删除超过保留期的旧日志，
         并兜底删除轮转失效产生的超大文件，防止日志目录无限膨胀
"""
import logging
import sys
import time
from logging.handlers import RotatingFileHandler
from pathlib import Path

# 日志清理策略参数
_日志保留天数 = 7                       # 超过 7 天未修改的日志文件删除
_单文件大小兜底上限 = 50 * 1024 * 1024  # 50MB：轮转失效时的异常大文件直接删除


def 获取日志器(模块名: str) -> logging.Logger:
    """获取模块级别的日志器

    Args:
        模块名: 子模块标识（如 "聊天路由"、"本地模型客户端"）

    Returns:
        归属于 NodeCraftAI 命名空间的 Logger 实例
    """
    return logging.getLogger(f"NodeCraftAI.{模块名}")


def _清理历史日志(日志目录: Path) -> None:
    """启动时自动清理日志目录，防止大文件与旧文件堆积

    清理规则（仅限 logs 目录下的 *.log* 文件，不碰其它文件）：
    1. 超过保留天数（默认 7 天）未修改的日志文件 → 删除；
    2. 单文件超过兜底上限（50MB，正常轮转不可能达到）→ 删除，
       覆盖轮转失效（如文件被占用导致 rollover 失败）的异常膨胀。
    单个文件删除失败（如被占用）不影响其它文件清理与后续初始化。
    """
    if not 日志目录.is_dir():
        return
    过期时间点 = time.time() - _日志保留天数 * 86400
    for 文件 in 日志目录.glob("*.log*"):
        try:
            if not 文件.is_file():
                continue
            信息 = 文件.stat()
            if 信息.st_mtime < 过期时间点 or 信息.st_size > _单文件大小兜底上限:
                文件.unlink()
                sys.stdout.write(f"[NodeCraftAI] 已清理旧日志: {文件.name} ({信息.st_size} 字节)\n")
        except OSError as e:
            # 文件被占用等情况跳过，下次启动再试
            sys.stderr.write(f"[NodeCraftAI] 清理日志文件失败（跳过）: {文件.name}: {e}\n")


def 初始化日志系统() -> None:
    """初始化全局日志配置

    应在应用启动时调用一次。重复调用会被忽略以避免重复 handler。
    所有日志输出到控制台（INFO+），错误和异常显示在 ComfyUI 后端日志中；
    同时写入插件目录下 logs/nodecraft_ai.log 文件（控制台日志可能不落盘，
    文件输出保证重启后仍可追溯报错堆栈）。
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

    # 文件 handler：INFO+，UTF-8 编码（避免 Windows GBK 控制台乱码），轮转防膨胀。
    # 创建失败（权限/磁盘等）不应阻断插件启动，降级为仅控制台输出。
    try:
        日志目录 = Path(__file__).resolve().parent.parent / "logs"
        日志目录.mkdir(parents=True, exist_ok=True)
        # 启动时清理：删除超期/超大的历史日志，防止目录膨胀
        _清理历史日志(日志目录)
        file_handler = RotatingFileHandler(
            日志目录 / "nodecraft_ai.log",
            maxBytes=10 * 1024 * 1024,
            backupCount=3,
            encoding="utf-8",
        )
        file_handler.setLevel(logging.INFO)
        file_handler.setFormatter(logging.Formatter(
            '[%(asctime)s][%(levelname)s][%(name)s] %(message)s',
            datefmt='%Y-%m-%d %H:%M:%S'
        ))
        root_logger.addHandler(file_handler)
    except OSError as e:
        sys.stderr.write(f"[NodeCraftAI] 日志文件初始化失败，仅输出到控制台: {e}\n")

