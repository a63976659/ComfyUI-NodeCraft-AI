"""GraphQL 自定义异常层级"""


class GraphQL基础异常(Exception):
    """NodeCraft GraphQL 基础异常"""

    def __init__(self, message: str, code: str, status_code: int = 400):
        self.message = message
        self.code = code
        self.status_code = status_code
        super().__init__(message)


class 认证错误(GraphQL基础异常):
    def __init__(self, message: str = "未授权：需要有效的认证令牌"):
        super().__init__(message, "UNAUTHENTICATED", 401)


class 权限错误(GraphQL基础异常):
    def __init__(self, message: str = "权限不足"):
        super().__init__(message, "FORBIDDEN", 403)


class 验证错误(GraphQL基础异常):
    def __init__(self, message: str):
        super().__init__(message, "VALIDATION_ERROR", 400)


class 资源不存在(GraphQL基础异常):
    def __init__(self, 资源类型: str, 资源ID: str):
        super().__init__(f"{资源类型} '{资源ID}' 不存在", "NOT_FOUND", 404)


class 频率限制错误(GraphQL基础异常):
    def __init__(self, message: str = "请求过于频繁，请稍后再试"):
        super().__init__(message, "RATE_LIMITED", 429)


class 业务错误(GraphQL基础异常):
    def __init__(self, message: str):
        super().__init__(message, "BUSINESS_ERROR", 400)
