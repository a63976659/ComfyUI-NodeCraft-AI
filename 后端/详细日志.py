"""详细日志模块

记录完整的请求-响应链路细节，供用户根据日志优化提示词、工具链与知识库：
- 每次用户输入与上传附件内容（文本附件解码后记录正文）
- 每次 AI 完整输出（含耗时与 token 用量）
- 每次工具调用的参数与返回结果（含耗时、成功/异常状态）

与主日志的关系：
- 主日志（logs/nodecraft_ai.log）保持精简，仅记录状态与错误；
- 详细日志单独写入 logs/nodecraft_ai_detail.log（20MB×3 轮转），
  内容量大不挤占主日志空间；只写文件不刷 ComfyUI 控制台。

设计参考：LiteLLM / LangFuse 的可观测性分层实践——状态日志与全量
内容日志分文件落盘，内容按阈值截断防止单条日志无限膨胀。

使用方式：
    from 后端.详细日志 import 记录用户输入, 记录AI输出, 记录工具调用
"""
import base64
import binascii
import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path

from .日志配置 import 获取日志器

# 主日志中的提示前缀（便于 grep 过滤详细链路）
_前缀 = "[详细日志]"

# ─── 截断上限（字符数）────────────────────────────────────────
# 工具参数字符串遵循项目既有规范：超 100 字符只留前 80 字符
_上限_用户消息 = 2000
_上限_附件正文 = 1500
_上限_AI输出 = 8000
_上限_工具参数 = 80
_上限_工具结果 = 500

_logger = 获取日志器("详细日志")
_已初始化 = False


def _确保初始化() -> None:
    """为详细日志挂载独立的文件 handler（轮转防膨胀，不输出到控制台）

    重复调用会被忽略；创建失败（权限/磁盘）时静默降级，
    详细日志缺失不影响主流程。
    """
    global _已初始化
    if _已初始化:
        return
    _已初始化 = True
    try:
        日志目录 = Path(__file__).resolve().parent.parent / "logs"
        日志目录.mkdir(parents=True, exist_ok=True)
        handler = RotatingFileHandler(
            日志目录 / "nodecraft_ai_detail.log",
            maxBytes=20 * 1024 * 1024,
            backupCount=3,
            encoding="utf-8",
        )
        handler.setLevel(logging.INFO)
        handler.setFormatter(logging.Formatter(
            '[%(asctime)s] %(message)s',
            datefmt='%Y-%m-%d %H:%M:%S'
        ))
        _logger.addHandler(handler)
        _logger.setLevel(logging.INFO)
        # 详细日志只落盘不冒泡：避免刷屏 ComfyUI 控制台与主日志文件
        _logger.propagate = False
    except OSError as e:
        # 挂载失败时降级：退回主日志器输出（受主日志轮转约束）
        globals()['_logger'] = 获取日志器("聊天路由")
        import sys
        sys.stderr.write(f"[NodeCraftAI] 详细日志文件初始化失败，降级到主日志: {e}\n")


def _截断(文本: str, 上限: int) -> str:
    """超长文本截断：保留前 上限 字符并追加 ...(共N字符) 说明"""
    if 文本 is None:
        return ""
    文本 = str(文本)
    if len(文本) <= 上限:
        return 文本
    return f"{文本[:上限]}...(共{len(文本)}字符)"


def _解码附件正文(附件: dict) -> str:
    """解码文本类附件的 base64 data；图片/音频等二进制仅返回占位说明"""
    类型 = str(附件.get("type", ""))
    if 类型.startswith(("image/", "audio/", "video/")):
        return f"[二进制内容不记录: {类型}]"
    data = 附件.get("data")
    if not data:
        return "[无正文数据]"
    try:
        # data 可能带 data:xxx;base64, 前缀，取逗号后部分
        if "," in data[:100] and data.lstrip().lower().startswith("data:"):
            data = data.split(",", 1)[1]
        return base64.b64decode(data).decode("utf-8", errors="replace")
    except (binascii.Error, ValueError, UnicodeDecodeError) as e:
        return f"[解码失败: {e}]"


def 记录用户输入(会话id, 界面, 用户消息, 附件列表=None, 渠道="SSE"):
    """记录一次用户请求：消息正文 + 每个上传附件的名称/类型/大小/正文"""
    _确保初始化()
    附件列表 = 附件列表 or []
    _logger.info(
        f"{_前缀} 用户输入 会话={会话id} 界面={界面} 渠道={渠道} "
        f"附件数={len(附件列表)}\n  消息: {_截断(用户消息, _上限_用户消息)}"
    )
    for 附件 in 附件列表:
        if not isinstance(附件, dict):
            continue
        名称 = 附件.get("name", "?")
        类型 = 附件.get("type", "?")
        大小 = 附件.get("size", 0)
        _logger.info(
            f"{_前缀} 上传附件 会话={会话id} 名称={名称} 类型={类型} 大小={大小}字节\n"
            f"  内容: {_截断(_解码附件正文(附件), _上限_附件正文)}"
        )


def 记录AI输出(会话id, 回复, 耗时秒=None, token数=None, 渠道="SSE"):
    """记录一次 AI 完整输出（截断保护）"""
    _确保初始化()
    指标 = ""
    if 耗时秒 is not None:
        指标 += f" 耗时={耗时秒:.1f}s"
    if token数:
        指标 += f" tokens={token数}"
    _logger.info(
        f"{_前缀} AI输出 会话={会话id} 渠道={渠道} "
        f"长度={len(回复 or '')}{指标}\n  回复: {_截断(回复, _上限_AI输出)}"
    )


def 记录工具调用(工具名, 参数, 结果, 耗时秒=None, 是否异常=False):
    """记录一次工具调用的参数与结果

    参数字符串按项目规范截断（超100字符留前80）；结果按 _上限_工具结果 截断。
    """
    _确保初始化()

    def _压参数(v):
        if isinstance(v, str) and len(v) > 100:
            return f"{v[:_上限_工具参数]}...(共{len(v)}字符)"
        return v

    参数摘要 = {k: _压参数(v) for k, v in (参数 or {}).items()} if isinstance(参数, dict) else 参数
    状态 = "异常" if 是否异常 else "成功"
    耗时 = f" 耗时={耗时秒:.2f}s" if 耗时秒 is not None else ""
    _logger.info(
        f"{_前缀} 工具调用 [{工具名}] {状态}{耗时} 参数={参数摘要}\n"
        f"  结果: {_截断(结果, _上限_工具结果)}"
    )
