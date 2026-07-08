"""
性能监控模块 - 收集并提供 API 指标
"""
import time
import threading
from collections import deque, defaultdict

from aiohttp import web


class MetricsCollector:
    """指标收集器（线程安全）"""

    def __init__(self, window_size: int = 300):
        """window_size: 保留最近 N 秒的数据"""
        self.window_size = window_size
        self._lock = threading.Lock()
        self._requests = deque(maxlen=10000)  # (timestamp, endpoint, duration_ms, status)
        self._model_inferences = deque(maxlen=1000)  # (timestamp, duration_ms, tokens)
        self._active_connections = 0
        self._ws_connections = 0
        self._start_time = time.time()

    def record_request(self, endpoint: str, duration_ms: float, status: int):
        """记录 API 请求"""
        with self._lock:
            self._requests.append((time.time(), endpoint, duration_ms, status))

    def record_inference(self, duration_ms: float, tokens: int = 0):
        """记录模型推理"""
        with self._lock:
            self._model_inferences.append((time.time(), duration_ms, tokens))

    def increment_connections(self, ws=False):
        with self._lock:
            self._active_connections += 1
            if ws:
                self._ws_connections += 1

    def decrement_connections(self, ws=False):
        with self._lock:
            self._active_connections -= 1
            if ws:
                self._ws_connections -= 1

    def get_metrics(self) -> dict:
        """获取当前指标快照"""
        now = time.time()
        window_start = now - 60  # 最近 1 分钟的统计

        with self._lock:
            # 最近 1 分钟的请求
            recent_requests = [(t, e, d, s) for t, e, d, s in self._requests if t > window_start]
            recent_inferences = [(t, d, tk) for t, d, tk in self._model_inferences if t > window_start]

        # QPS
        qps = len(recent_requests) / 60.0 if recent_requests else 0

        # 延迟分位数
        durations = sorted([d for _, _, d, _ in recent_requests]) if recent_requests else []
        p50 = durations[len(durations) // 2] if durations else 0
        p90 = durations[int(len(durations) * 0.9)] if durations else 0
        p99 = durations[int(len(durations) * 0.99)] if durations else 0

        # 模型推理统计
        inference_durations = [d for _, d, _ in recent_inferences]
        avg_inference = sum(inference_durations) / len(inference_durations) if inference_durations else 0
        total_tokens = sum(tk for _, _, tk in recent_inferences)

        # 错误率
        errors = sum(1 for _, _, _, s in recent_requests if s >= 400)
        error_rate = errors / len(recent_requests) * 100 if recent_requests else 0

        # 端点分布
        endpoint_counts = defaultdict(int)
        for _, e, _, _ in recent_requests:
            endpoint_counts[e] += 1
        top_endpoints = sorted(endpoint_counts.items(), key=lambda x: -x[1])[:5]

        return {
            "uptime_seconds": round(now - self._start_time),
            "qps": round(qps, 2),
            "latency": {
                "p50_ms": round(p50, 1),
                "p90_ms": round(p90, 1),
                "p99_ms": round(p99, 1),
            },
            "requests_1m": len(recent_requests),
            "error_rate_percent": round(error_rate, 1),
            "active_connections": self._active_connections,
            "ws_connections": self._ws_connections,
            "model": {
                "inferences_1m": len(recent_inferences),
                "avg_duration_ms": round(avg_inference, 1),
                "tokens_1m": total_tokens,
            },
            "top_endpoints": top_endpoints,
        }


# 全局单例
_metrics = MetricsCollector()


def get_metrics_collector() -> MetricsCollector:
    return _metrics


@web.middleware
async def 连接计数中间件(request, handler):
    """HTTP 连接计数中间件 - 自动追踪 /ai-coder 请求的并发连接生命周期。

    请求进入时 +1，处理结束（含异常）时 -1，使监控面板的 active_connections
    反映当前并发的 HTTP 请求数。WebSocket 端点(/ai-coder/ws)已由 WS 处理器
    自行计数，此处排除以避免重复统计。
    """
    _追踪 = request.path.startswith('/ai-coder') and request.path != '/ai-coder/ws'
    if _追踪:
        _metrics.increment_connections()
    try:
        return await handler(request)
    finally:
        if _追踪:
            _metrics.decrement_connections()
