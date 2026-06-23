"""GraphQL 监控查询 Resolver

实现审计日志查询与性能指标查询的业务逻辑：
- 获取审计日志（分页）：直接复用现有 ``AuditLogger`` 单例（注册在
  ``request.app['audit_logger']``），读取 ``数据/审计日志/`` 下的 JSONL
  日志文件，转为 GraphQL ``审计日志项`` 列表。
- 获取性能指标：调用 ``后端.性能监控.get_metrics_collector`` 拉取统计快照，
  转为 GraphQL ``性能指标`` 类型（``端点统计`` 字段使用 JSON 字符串保留扩展性）。

兼容性：
- 与项目其它 resolver 保持一致，统一执行 ``await 检查认证(info)``。
- 当系统未注册 ``audit_logger`` 时，给出空列表/零值，避免阻断查询。
"""

from __future__ import annotations

import json
import math
from typing import Any, List, Optional

from strawberry.types import Info

from ..中间件 import 检查认证
from ..异常定义 import 验证错误
from ..类型定义 import (
    审计日志项,
    审计日志列表响应,
    分页信息,
    性能指标,
)


# ─── 内部工具 ──────────────────────────────────────────────

def _规范化页码(页数: int, 每页数量: int, 上限: int = 200) -> tuple[int, int]:
    """规范化分页参数，限制范围"""
    if 页数 < 1:
        页数 = 1
    if 每页数量 < 1:
        每页数量 = 1
    if 每页数量 > 上限:
        每页数量 = 上限
    return 页数, 每页数量


def _构建分页信息(总数: int, 当前页: int, 页大小: int) -> 分页信息:
    """根据总数与分页参数构建分页信息对象"""
    if 页大小 <= 0:
        总页数 = 0
    else:
        总页数 = math.ceil(总数 / 页大小) if 总数 > 0 else 0
    return 分页信息(
        总数=总数,
        当前页=当前页,
        页大小=页大小,
        总页数=总页数,
    )


def _切片分页(数据: list, 页数: int, 每页数量: int) -> list:
    """对列表执行 offset 分页切片"""
    起始 = (页数 - 1) * 每页数量
    结束 = 起始 + 每页数量
    return 数据[起始:结束]


def _获取审计单例(info: Info) -> Optional[Any]:
    """从 GraphQL Context 关联的 aiohttp app 中拿到 AuditLogger 单例"""
    try:
        request = getattr(info.context, "request", None)
        if request is None:
            return None
        return request.app.get("audit_logger")
    except Exception:
        return None


def _字典转审计项(数据: dict) -> 审计日志项:
    """将原始 dict 转为 ``审计日志项`` Strawberry 类型实例

    保留原始结构到 ``详情`` 字段（JSON 字符串），便于前端按需展示。
    """
    时间戳 = str(数据.get("timestamp", "") or "")
    用户 = str(数据.get("user", "") or "anonymous")
    操作 = str(
        数据.get("action")
        or 数据.get("endpoint")
        or 数据.get("method")
        or ""
    )

    状态原始 = 数据.get("status")
    if 状态原始 is None and "error" in 数据:
        状态原始 = "error"
    状态 = str(状态原始) if 状态原始 is not None else ""

    try:
        详情 = json.dumps(数据, ensure_ascii=False)
    except (TypeError, ValueError):
        详情 = "{}"

    return 审计日志项(
        时间戳=时间戳,
        用户=用户,
        操作=操作,
        状态=状态,
        详情=详情,
    )


# ─── Query Resolver ───────────────────────────────────────

async def 获取审计日志(
    info: Info,
    页数: int = 1,
    每页数量: int = 20,
) -> 审计日志列表响应:
    """获取审计日志（分页）

    参数:
        info: GraphQL 解析上下文
        页数: 1 起始的页码
        每页数量: 单页条数（最大 200）

    返回:
        ``审计日志列表响应`` 对象，按时间倒序排列。

    备注：
        审计日志条数通常较少（按天分文件，自动清理 30 天前的日志），
        因此此处直接通过 ``query`` 拉取上限条数后内存切片，避免重复实现
        分页文件遍历逻辑。
    """
    await 检查认证(info)

    if 页数 < 1 and 页数 != 0:
        raise 验证错误("页数必须 >= 1")
    页数, 每页数量 = _规范化页码(页数, 每页数量)

    audit_logger = _获取审计单例(info)
    全部记录: List[dict] = []
    if audit_logger is not None:
        try:
            # 上限 1000 条，与 REST ``/ai-coder/audit-logs`` 保持一致
            上限 = min(max(页数 * 每页数量, 100), 1000)
            全部记录 = audit_logger.query(limit=上限) or []
        except Exception:
            全部记录 = []

    总数 = len(全部记录)
    当前页数据 = _切片分页(全部记录, 页数, 每页数量)
    日志列表 = [
        _字典转审计项(项) for 项 in 当前页数据 if isinstance(项, dict)
    ]

    return 审计日志列表响应(
        日志列表=日志列表,
        分页=_构建分页信息(总数, 页数, 每页数量),
    )


async def 获取性能指标(info: Info) -> 性能指标:
    """获取性能指标快照

    复用 ``后端.性能监控.MetricsCollector`` 的全局单例，转换为 GraphQL 类型。

    映射规则：
        - ``总请求数`` ← ``requests_1m``（最近 1 分钟请求数）
        - ``平均响应时间`` ← ``latency.p50_ms``（p50 延迟，单位 ms）
        - ``端点统计`` ← 完整 metrics dict 的 JSON 字符串，保留 QPS / 错误率 /
          连接数 / 模型推理 / TopN 端点等指标
    """
    await 检查认证(info)

    # 延迟导入以避免循环依赖与 GraphQL 模块加载副作用
    from ...性能监控 import get_metrics_collector

    try:
        快照 = get_metrics_collector().get_metrics() or {}
    except Exception:
        快照 = {}

    总请求数 = int(快照.get("requests_1m", 0) or 0)
    延迟 = 快照.get("latency", {}) or {}
    try:
        平均响应时间 = float(延迟.get("p50_ms", 0) or 0)
    except (TypeError, ValueError):
        平均响应时间 = 0.0

    try:
        端点统计 = json.dumps(快照, ensure_ascii=False)
    except (TypeError, ValueError):
        端点统计 = "{}"

    return 性能指标(
        总请求数=总请求数,
        平均响应时间=平均响应时间,
        端点统计=端点统计,
    )
