"""审计与文档路由模块

包含：
- 审计日志查询端点：/ai-coder/audit-logs
- 性能指标端点：/ai-coder/metrics
- API 文档端点：/ai-coder/docs / /ai-coder/openapi.json

注：本地认证端点（register / login / refresh / logout）已随 RanKing
联合认证迁移而移除，不再由本模块提供。
"""
from aiohttp import web

from .路由公共 import _check_auth, _分页参数, _分页响应
from .接口文档 import docs_page, openapi_json
from .性能监控 import get_metrics_collector


async def get_audit_logs(request):
    """查询审计日志（需要认证）。

    支持分页：?page=1&page_size=20，最大 page_size=100。
    向后兼容：未传递分页参数时返回默认 50 条记录。
    """
    auth_error = await _check_auth(request)
    if auth_error:
        return auth_error
    # Bug 16 修复：require_auth 中间件将 payload 写入 request['user']，
    # 但理论上其他中间件可能注入非 dict 类型；增加类型检查防止 AttributeError。
    user_data = request.get('user')
    user = None
    if isinstance(user_data, dict):
        user = user_data.get('sub')
    elif user_data is not None and hasattr(user_data, 'get'):
        user = user_data.get('sub')
    audit_logger = request.app['audit_logger']

    page, page_size, paginated = _分页参数(request)
    if paginated:
        # 按页查询：底层拉取足够多的数据以供切片（限制 1000 条上限）
        max_fetch = min(page * page_size, 1000)
        logs = audit_logger.query(user=user, limit=max_fetch)
        payload = _分页响应(logs, page, page_size, True, list_key="data",
                          extra={"success": True})
    else:
        logs = audit_logger.query(user=user, limit=50)
        payload = _分页响应(logs, page, page_size, False, list_key="data",
                          extra={"success": True})
    return web.json_response(payload)


async def metrics_handler(request):
    """获取性能指标"""
    auth_error = await _check_auth(request)
    if auth_error:
        return auth_error
    collector = get_metrics_collector()
    return web.json_response({"success": True, "data": collector.get_metrics()})


def register_审计与文档路由(routes):
    """将审计、性能、文档相关端点注册到 routes"""
    # 审计日志查询
    routes.get('/ai-coder/audit-logs')(get_audit_logs)

    # 性能监控
    routes.get('/ai-coder/metrics')(metrics_handler)

    # API 文档（无需认证）
    routes.get('/ai-coder/docs')(docs_page)
    routes.get('/ai-coder/openapi.json')(openapi_json)
