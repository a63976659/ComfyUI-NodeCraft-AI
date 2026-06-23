"""GraphQL Context 工厂 — 认证与请求上下文管理

- 持有 aiohttp request/response
- 解析 JWT，挂载 user payload
- 提供已认证 / 用户名 / 需要认证 等便捷属性
- 注入请求级 DataLoader 缓存（见 ``数据加载器.create_dataloaders``）

兼容性说明：
    项目通过 ``request.app['auth_manager']`` 暴露 AuthManager 实例（见
    ``后端/路由公共.py``），并未提供模块级单例。因此本文件优先从
    ``request.app`` 获取 auth_manager，以与现有认证体系无缝对接。
"""

from typing import Optional, Dict, Any
import sys
import os

from aiohttp.web import Request, Response

from .数据加载器 import create_dataloaders

# 添加项目根目录到 path（兼容 ComfyUI 插件导入）
_plugin_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _plugin_root not in sys.path:
    sys.path.insert(0, _plugin_root)


class GraphQLContext:
    """GraphQL 上下文对象（贯穿整个请求生命周期）

    职责：
    - 持有 aiohttp request/response
    - 持有已解析的用户信息（JWT payload）
    - 提供认证检查便捷方法
    - 注入请求级 DataLoader 实例（见 ``dataloaders`` 属性）
    """

    def __init__(
        self,
        request: Request,
        response: Optional[Response] = None,
        user: Optional[Dict[str, Any]] = None,
    ):
        self.request = request
        self.response = response
        # JWT payload: {"sub": username, "exp": ..., "iat": ..., "type": "access"}
        self.user = user
        self.dataloaders: Dict[str, Any] = {}

    # ─── 认证状态 ───
    @property
    def 已认证(self) -> bool:
        """用户是否已通过认证"""
        return self.user is not None and "sub" in self.user

    @property
    def 用户名(self) -> str:
        """当前用户名（未认证时返回 anonymous）"""
        if self.user:
            return self.user.get("sub", "anonymous")
        return "anonymous"

    @property
    def 需要认证(self) -> bool:
        """系统是否启用了认证（有注册用户时需要认证）"""
        auth_manager = self._获取_auth_manager()
        try:
            return auth_manager is not None and auth_manager.has_users()
        except Exception:
            return False

    # ─── 内部工具 ───
    def _获取_auth_manager(self):
        """从 aiohttp app 中获取 AuthManager 实例"""
        try:
            return self.request.app.get("auth_manager")
        except Exception:
            return None


async def get_graphql_context(
    request: Request,
    response: Optional[Response] = None,
) -> GraphQLContext:
    """GraphQL Context 工厂函数（由 GraphQLView 调用）

    流程：
    1. 从 ``request.app`` 获取 ``auth_manager``
    2. 从 Authorization header 提取 Bearer Token
    3. 调用 ``auth_manager.verify_token`` 验证
    4. 构建 GraphQLContext 返回

    兼容三层认证策略：
    - 无 auth_manager：不验证，user=None
    - 无用户模式：不验证，user=None
    - 有用户模式：验证 Token，验证失败 user=None

    同时为每个请求创建一组独立的 DataLoader 实例，挂载到
    ``context.dataloaders``，供 resolver 调用。
    """
    user: Optional[Dict[str, Any]] = None

    try:
        auth_manager = request.app.get("auth_manager") if request is not None else None

        if auth_manager is not None:
            auth_header = request.headers.get("Authorization", "")
            if auth_header.startswith("Bearer "):
                token = auth_header[7:]
                payload = auth_manager.verify_token(token)
                # 仅接受 access 类型 token（与 require_auth 保持一致）
                if payload and payload.get("type") == "access":
                    user = payload
    except Exception:
        # 任何异常都视为未认证，不阻断请求
        user = None

    context = GraphQLContext(request=request, response=response, user=user)
    context.dataloaders = create_dataloaders()
    return context
