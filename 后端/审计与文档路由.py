"""审计与文档路由模块

包含：
- 审计日志查询端点：/ai-coder/audit-logs
- 性能指标端点：/ai-coder/metrics
- API 文档端点：/ai-coder/docs / /ai-coder/openapi.json

注：本地认证端点（register / login / refresh / logout）已随认证体系精简移除，
本模块不涉及鉴权，所有端点均为本地只读查询。
"""
from aiohttp import web

from .性能监控 import get_metrics_collector
from .接口文档 import docs_page, openapi_json
from .路由公共 import _分页参数, _分页响应, llm_client, local_model_client


async def get_audit_logs(request):
    """查询审计日志。

    支持分页：?page=1&page_size=20，最大 page_size=100。
    向后兼容：未传递分页参数时返回默认 50 条记录。
    """
    audit_logger = request.app['audit_logger']

    page, page_size, paginated = _分页参数(request)
    if paginated:
        # 按页查询：底层拉取足够多的数据以供切片（限制 1000 条上限）
        max_fetch = min(page * page_size, 1000)
        logs = audit_logger.query(limit=max_fetch)
        payload = _分页响应(logs, page, page_size, True, list_key="data",
                          extra={"success": True})
    else:
        logs = audit_logger.query(limit=50)
        payload = _分页响应(logs, page, page_size, False, list_key="data",
                          extra={"success": True})
    return web.json_response(payload)


async def metrics_handler(request):
    """获取性能指标（含两个模型客户端的推理统计）"""
    collector = get_metrics_collector()
    data = collector.get_metrics()
    # P0-3 前缀缓存可观测：累计命中率原本只能从日志看，挂到已有只读端点上
    if llm_client is not None:
        try:
            data["llm客户端"] = llm_client.获取性能报告()
        except Exception:
            pass  # 监控端点不能因统计取数失败而不可用
    # 本地推理统计同为纯内存读取（poll() 只探活，不会拉起 worker）
    if local_model_client is not None:
        try:
            data["本地模型客户端"] = local_model_client.获取性能报告()
        except Exception:
            pass
    return web.json_response({"success": True, "data": data})


def register_审计与文档路由(routes):
    """将审计、性能、文档相关端点注册到 routes"""
    # 审计日志查询
    routes.get('/ai-coder/audit-logs')(get_audit_logs)

    # 性能监控
    routes.get('/ai-coder/metrics')(metrics_handler)

    # API 文档
    routes.get('/ai-coder/docs')(docs_page)
    routes.get('/ai-coder/openapi.json')(openapi_json)
