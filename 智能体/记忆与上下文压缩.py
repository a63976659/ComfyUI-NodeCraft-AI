import sys
from pathlib import Path

_plugin_root = str(Path(__file__).parent.parent.resolve())
if _plugin_root not in sys.path:
    sys.path.insert(0, _plugin_root)
from 后端.文件读写操作 import load_settings

try:
    import tiktoken
    _TIKTOKEN_AVAILABLE = True
except ImportError:
    _TIKTOKEN_AVAILABLE = False


class ContextManager:
    """上下文压缩管理器 - 无状态工具类"""

    def __init__(self):
        if _TIKTOKEN_AVAILABLE:
            try:
                self.编码器 = tiktoken.get_encoding("cl100k_base")
            except Exception:
                self.编码器 = None
        else:
            self.编码器 = None

    def get_token_count(self, text):
        """计算文本的 token 数量"""
        if self.编码器:
            return len(self.编码器.encode(text))
        # 备用估算：中文约 2 token/字，英文约 1.3 token/词
        return len(text) * 2

    def compress_history(self, messages, max_tokens=None):
        """
        从消息列表构建压缩后的对话历史

        Args:
            messages: 完整消息列表 [{"role": "...", "content": "...", "timestamp": "..."}]
            max_tokens: 最大 token 数，None 时从设置读取

        Returns:
            list: 压缩后的消息列表 [{"role": "...", "content": "..."}]
        """
        if max_tokens is None:
            settings = load_settings()
            # 上下文窗口取模型 max_tokens 的 70% 留给历史，30% 留给新回复
            max_tokens = int(settings.get("max_tokens", 4096) * 0.7)

        if not messages:
            return []

        # 从最新消息往前回溯，保留尽可能多的完整对话
        压缩结果 = []
        当前token数 = 0

        for msg in reversed(messages):
            msg_tokens = self.get_token_count(msg.get("content", ""))
            if 当前token数 + msg_tokens > max_tokens:
                break
            压缩结果.insert(0, {
                "role": msg["role"],
                "content": msg["content"]
            })
            当前token数 += msg_tokens

        # 如果历史被截断，在开头添加系统提示
        if len(压缩结果) < len(messages):
            截断数 = len(messages) - len(压缩结果)
            压缩结果.insert(0, {
                "role": "system",
                "content": f"[注意: 之前有 {截断数} 条对话已被压缩省略，以节省上下文空间。请基于当前可见的对话继续。]"
            })

        return 压缩结果
