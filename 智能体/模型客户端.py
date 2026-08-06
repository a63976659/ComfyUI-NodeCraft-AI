"""模型客户端入口模块（向后兼容聚合层）

为保持现有调用方代码无需改动（如 `from 智能体.模型客户端 import AICoderClient,
LocalModelClient`），本文件作为精简入口：

- 定义两个客户端共享的轻量数据类（性能统计）
- 在文件末尾 re-export 拆分后的子模块（API 与 本地）

实际实现位于：
- 智能体/JSON修复工具.py     —— JSON 三层修复策略
- 智能体/API模型客户端.py    —— AICoderClient（OpenAI 兼容、SSE 流式）
- 智能体/本地模型客户端.py   —— LocalModelClient（subprocess 代理层，通过 worker 进程通信）
"""
from collections import deque
from dataclasses import dataclass, field

# ============================================================
#  P2: 性能监控数据类（API 与 本地客户端共享）
# ============================================================

@dataclass
class 性能统计:
    """API/推理性能统计"""
    总请求数: int = 0
    成功数: int = 0
    失败数: int = 0
    _响应时间列表: deque = field(default_factory=lambda: deque(maxlen=100))

    @property
    def 平均响应时间(self) -> float:
        if not self._响应时间列表:
            return 0.0
        return sum(self._响应时间列表) / len(self._响应时间列表)

    def 记录请求(self, 耗时秒: float, 成功: bool):
        self.总请求数 += 1
        if 成功:
            self.成功数 += 1
        else:
            self.失败数 += 1
        self._响应时间列表.append(耗时秒)


# ============================================================
#  向后兼容 re-export
#  注意：以下导入必须放在 性能统计 定义之后，
#  因为子模块会反向 from 智能体.模型客户端 import 性能统计
# ============================================================

from 智能体.API模型客户端 import AICoderClient  # noqa: E402
from 智能体.JSON修复工具 import _修复截断JSON, _智能截断修复  # noqa: E402,F401
from 智能体.本地模型客户端 import LocalModelClient  # noqa: E402

__all__ = [
    "性能统计",
    "AICoderClient",
    "LocalModelClient",
    "_修复截断JSON",
    "_智能截断修复",
]
