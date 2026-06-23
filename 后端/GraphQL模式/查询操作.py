"""GraphQL Query 根类型

NodeCraft AI GraphQL 查询入口。

字段分组：
- 健康检查 / 版本：基础信息
- 会话相关：会话列表、会话详情、会话消息
- 文件与插件：本地插件列表、浏览文件夹、插件文件树、读取文件、可视化数据

GraphQL 命名规范：所有暴露给 GraphQL 的根类型名/字段名/参数名必须满足
``[_a-zA-Z0-9]``。Python 端保持中文命名，通过 Strawberry 的
``name=`` 参数（字段）以及 ``Annotated[..., strawberry.argument(name=...)]``
（参数）为 GraphQL 暴露 ASCII 兼容名称。
"""

from typing import Optional, List
from typing import Annotated

import strawberry
from strawberry.types import Info

# 类型别名：避免与同名方法冲突（例如方法 ``设置`` 与类型 ``设置``）
from .类型定义 import (
    会话列表响应,
    会话详情,
    消息列表响应,
    设置 as 设置类型,
    本地模型列表响应,
    模型能力 as 模型能力类型,
    插件列表响应,
    文件夹项,
    文件树节点,
    文件内容,
    可视化数据 as 可视化数据类型,
    本地模型,
    模型市场项,
    模型市场搜索响应,
    下载状态,
    模板列表响应,
    打包文件列表响应,
    审计日志列表响应,
    性能指标 as 性能指标类型,
)
from .解析器.会话逻辑 import (
    获取会话列表 as _获取会话列表,
    获取会话详情 as _获取会话详情,
    获取会话消息 as _获取会话消息,
)
from .解析器.文件逻辑 import (
    获取本地插件列表 as _获取本地插件列表,
    浏览文件夹 as _浏览文件夹,
    获取插件文件树 as _获取插件文件树,
    读取文件 as _读取文件,
    获取可视化数据 as _获取可视化数据,
)
from .解析器.GitHub逻辑 import (
    检查仓库是否存在 as _检查仓库是否存在,
    测试GitHub连接 as _测试GitHub连接,
)
from .解析器.市场逻辑 import (
    搜索模型市场 as _搜索模型市场,
    获取模型详情 as _获取模型详情,
    获取下载状态 as _获取下载状态,
    获取本地市场模型 as _获取本地市场模型,
    获取模板列表 as _获取模板列表,
    获取打包文件列表 as _获取打包文件列表,
)
from .解析器.监控逻辑 import (
    获取审计日志 as _获取审计日志,
    获取性能指标 as _获取性能指标,
)


# ─── 常用参数别名（GraphQL ASCII 名称）───
# 通过 ``Annotated`` + ``strawberry.argument(name=...)`` 为中文形参指定
# 英文 GraphQL 参数名，避免在每个解析器内重复书写。
_页数参数 = Annotated[int, strawberry.argument(name="page")]
_每页数量参数 = Annotated[int, strawberry.argument(name="pageSize")]
_会话ID参数 = Annotated[str, strawberry.argument(name="sessionId")]
_插件路径参数 = Annotated[str, strawberry.argument(name="pluginPath")]
_文件路径参数 = Annotated[str, strawberry.argument(name="filePath")]
_仓库名称参数 = Annotated[str, strawberry.argument(name="repoName")]
_令牌参数 = Annotated[str, strawberry.argument(name="token")]
_关键词参数 = Annotated[str, strawberry.argument(name="keyword")]
_排序参数 = Annotated[str, strawberry.argument(name="sort")]
_模型ID参数 = Annotated[str, strawberry.argument(name="modelId")]
_模型来源参数 = Annotated[str, strawberry.argument(name="modelSource")]
_模型名称参数 = Annotated[str, strawberry.argument(name="modelName")]
_初始目录参数 = Annotated[str, strawberry.argument(name="initialDir")]


@strawberry.type(name="Query")
class Query:
    """NodeCraft AI GraphQL 查询根类型"""

    @strawberry.field(name="health", description="GraphQL 服务健康检查")
    async def 健康检查(self, info: Info) -> str:
        """返回 GraphQL 服务运行状态字符串。"""
        return "NodeCraft AI GraphQL 服务运行正常"

    @strawberry.field(name="version", description="获取 GraphQL Schema 版本")
    async def 版本(self, info: Info) -> str:
        """返回当前 GraphQL Schema 的语义化版本号。"""
        return "1.0.0"

    # ─── 会话查询 ──────────────────────────────────────────
    @strawberry.field(name="sessions", description="获取会话列表（分页）")
    async def 会话列表(
        self,
        info: Info,
        页数: _页数参数 = 1,
        每页数量: _每页数量参数 = 20,
    ) -> 会话列表响应:
        return await _获取会话列表(info, 页数, 每页数量)

    @strawberry.field(name="sessionDetail", description="获取指定会话的完整详情（含全部消息）")
    async def 会话详情(
        self,
        info: Info,
        会话ID: _会话ID参数,
    ) -> Optional[会话详情]:
        return await _获取会话详情(info, 会话ID)

    @strawberry.field(name="sessionMessages", description="获取指定会话的消息列表（分页）")
    async def 会话消息(
        self,
        info: Info,
        会话ID: _会话ID参数,
        页数: _页数参数 = 1,
        每页数量: _每页数量参数 = 50,
    ) -> 消息列表响应:
        return await _获取会话消息(info, 会话ID, 页数, 每页数量)

    # ─── 设置与模型查询 ─────────────────────────────────
    @strawberry.field(name="settings", description="获取系统设置")
    async def 设置(self, info: Info) -> 设置类型:
        """读取持久化的系统设置（敏感字段已脱敏）"""
        from .解析器.设置逻辑 import 获取设置
        return await 获取设置(info)

    @strawberry.field(name="localModels", description="获取本地模型列表")
    async def 本地模型列表(
        self,
        info: Info,
        页数: _页数参数 = 1,
        每页数量: _每页数量参数 = 20,
    ) -> 本地模型列表响应:
        """扫描 ComfyUI/models/LLM 目录并分页返回。"""
        from .解析器.设置逻辑 import 获取本地模型列表
        return await 获取本地模型列表(info, 页数, 每页数量)

    @strawberry.field(name="modelCapability", description="获取模型能力信息")
    async def 模型能力(
        self,
        info: Info,
        模型来源: _模型来源参数 = "api",
        模型名称: _模型名称参数 = "",
    ) -> 模型能力类型:
        """返回当前/指定模型的能力（是否支持视觉、文件内容等）。"""
        from .解析器.设置逻辑 import 获取模型能力
        return await 获取模型能力(info, 模型来源, 模型名称)

    # ─── 文件与插件查询 ─────────────────────────────────
    @strawberry.field(name="localPlugins", description="扫描 custom_nodes 目录，返回本地插件列表（分页）")
    async def 本地插件列表(
        self,
        info: Info,
        页数: _页数参数 = 1,
        每页数量: _每页数量参数 = 20,
    ) -> 插件列表响应:
        return await _获取本地插件列表(info, 页数, 每页数量)

    @strawberry.field(name="browseFolders", description="浏览指定目录下的子文件夹列表（路径穿越防护）")
    async def 浏览文件夹(
        self,
        info: Info,
        初始目录: _初始目录参数 = "",
    ) -> List[文件夹项]:
        return await _浏览文件夹(info, 初始目录)

    @strawberry.field(name="pluginFileTree", description="获取指定插件的文件树结构")
    async def 插件文件树(
        self,
        info: Info,
        插件路径: _插件路径参数,
    ) -> List[文件树节点]:
        return await _获取插件文件树(info, 插件路径)

    @strawberry.field(name="readFile", description="读取指定文件内容（路径穿越防护 + 大小限制）")
    async def 读取文件(
        self,
        info: Info,
        文件路径: _文件路径参数,
    ) -> 文件内容:
        return await _读取文件(info, 文件路径)

    @strawberry.field(name="visualizationData", description="读取已持久化的可视化分析数据")
    async def 可视化数据(
        self,
        info: Info,
        插件路径: _插件路径参数,
    ) -> Optional[可视化数据类型]:
        return await _获取可视化数据(info, 插件路径)

    # ─── GitHub 集成 ─────────────────────────────────────
    @strawberry.field(name="githubRepoExists", description="检查 GitHub 仓库是否存在")
    async def GitHub仓库是否存在(
        self,
        info: Info,
        仓库名称: _仓库名称参数,
    ) -> bool:
        """基于已配置的 GitHub 凭证检查指定仓库是否存在。"""
        return await _检查仓库是否存在(info, 仓库名称)

    @strawberry.field(name="testGithubConnection", description="测试 GitHub Token 并返回登录用户名")
    async def 测试GitHub连接(
        self,
        info: Info,
        令牌: _令牌参数,
    ) -> str:
        """使用传入 Token 调用 GitHub /user 接口，返回登录用户名。"""
        return await _测试GitHub连接(info, 令牌)

    # ─── 模型市场 / 模板 / 打包文件 ──────────────────────
    @strawberry.field(name="searchModelMarket", description="搜索 HuggingFace 模型市场（分页）")
    async def 搜索模型市场(
        self,
        info: Info,
        关键词: _关键词参数,
        排序: _排序参数 = "downloads",
        页数: _页数参数 = 1,
        每页数量: _每页数量参数 = 10,
    ) -> 模型市场搜索响应:
        return await _搜索模型市场(info, 关键词, 排序, 页数, 每页数量)

    @strawberry.field(name="modelDetail", description="获取 HuggingFace 模型详情")
    async def 获取模型详情(
        self,
        info: Info,
        模型ID: _模型ID参数,
    ) -> 模型市场项:
        return await _获取模型详情(info, 模型ID)

    @strawberry.field(name="downloadStatus", description="获取模型下载状态")
    async def 获取下载状态(
        self,
        info: Info,
        模型ID: _模型ID参数,
    ) -> 下载状态:
        return await _获取下载状态(info, 模型ID)

    @strawberry.field(name="localMarketModels", description="列出本地已下载的市场模型")
    async def 获取本地市场模型(
        self,
        info: Info,
    ) -> List[本地模型]:
        return await _获取本地市场模型(info)

    @strawberry.field(name="templates", description="获取插件模板列表（分页）")
    async def 获取模板列表(
        self,
        info: Info,
        页数: _页数参数 = 1,
        每页数量: _每页数量参数 = 20,
    ) -> 模板列表响应:
        return await _获取模板列表(info, 页数, 每页数量)

    @strawberry.field(name="packageFiles", description="获取已打包文件列表（分页）")
    async def 获取打包文件列表(
        self,
        info: Info,
        页数: _页数参数 = 1,
        每页数量: _每页数量参数 = 20,
    ) -> 打包文件列表响应:
        return await _获取打包文件列表(info, 页数, 每页数量)

    # ─── 监控查询 ────────────────────────────────────────
    @strawberry.field(name="auditLogs", description="获取审计日志列表（分页，按时间倒序）")
    async def 审计日志(
        self,
        info: Info,
        页数: _页数参数 = 1,
        每页数量: _每页数量参数 = 20,
    ) -> 审计日志列表响应:
        return await _获取审计日志(info, 页数, 每页数量)

    @strawberry.field(name="performanceMetrics", description="获取性能指标快照（QPS / 延迟 / 错误率 等）")
    async def 性能指标(self, info: Info) -> 性能指标类型:
        return await _获取性能指标(info)
