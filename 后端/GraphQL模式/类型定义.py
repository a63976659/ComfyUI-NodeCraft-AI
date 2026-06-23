"""NodeCraft AI GraphQL 数据类型定义

使用 Strawberry GraphQL 定义全部对象类型、输入类型、枚举与分页响应类型。
保持 Python 中文命名风格（与项目其余模块一致），通过 Strawberry 的
``name=`` 参数为 GraphQL 暴露 ASCII 兼容名称（满足 [_a-zA-Z0-9] 规范）。
"""

import strawberry
from datetime import datetime
from typing import Optional, List
from enum import Enum


# ─── 枚举 ───
# 注：GraphQL 枚举值名也必须满足 [_a-zA-Z0-9]，因此枚举成员名采用 ASCII 大写
@strawberry.enum(name="ModelSource")
class 模型来源(Enum):
    API = "api"
    LOCAL = "local"


@strawberry.enum(name="MessageRole")
class 消息角色(Enum):
    USER = "user"
    ASSISTANT = "assistant"
    SYSTEM = "system"


# ─── 分页 ───
@strawberry.type(name="PageInfo")
class 分页信息:
    总数: int = strawberry.field(name="total")
    当前页: int = strawberry.field(name="page")
    页大小: int = strawberry.field(name="pageSize")
    总页数: int = strawberry.field(name="totalPages")


# ─── 核心对象类型 ───
@strawberry.type(name="Attachment")
class 附件:
    名称: str = strawberry.field(name="name")
    类型: str = strawberry.field(name="mimeType")  # MIME type
    大小字节数: int = strawberry.field(name="size")


@strawberry.type(name="Message")
class 消息:
    角色: 消息角色 = strawberry.field(name="role")
    内容: str = strawberry.field(name="content")
    时间戳: str = strawberry.field(name="timestamp")
    附件列表: List[附件] = strawberry.field(name="attachments")


@strawberry.type(name="Session")
class 会话:
    ID: str = strawberry.field(name="id")
    标题: str = strawberry.field(name="title")
    创建时间: str = strawberry.field(name="createdAt")
    更新时间: str = strawberry.field(name="updatedAt")
    插件目录: str = strawberry.field(name="pluginFolder")
    消息计数: int = strawberry.field(name="messageCount")


@strawberry.type(name="SessionDetail")
class 会话详情:
    ID: str = strawberry.field(name="id")
    标题: str = strawberry.field(name="title")
    创建时间: str = strawberry.field(name="createdAt")
    更新时间: str = strawberry.field(name="updatedAt")
    插件目录: str = strawberry.field(name="pluginFolder")
    消息列表: List[消息] = strawberry.field(name="messages")


@strawberry.type(name="Settings")
class 设置:
    模型来源: str = strawberry.field(name="modelSource")
    本地模型路径: str = strawberry.field(name="localModelPath")
    本地模型名称: str = strawberry.field(name="localModelName")
    API基础URL: str = strawberry.field(name="apiBaseUrl")
    模型名称: str = strawberry.field(name="modelName")
    温度: float = strawberry.field(name="temperature")
    最大令牌数: int = strawberry.field(name="maxTokens")
    GitHub用户名: str = strawberry.field(name="githubUsername")
    GitHub默认仓库: str = strawberry.field(name="githubDefaultRepo")
    GitHub可见性: str = strawberry.field(name="githubVisibility")


@strawberry.type(name="LocalModel")
class 本地模型:
    名称: str = strawberry.field(name="name")
    类型: str = strawberry.field(name="type")  # "gguf" | "transformers"
    路径: str = strawberry.field(name="path")


@strawberry.type(name="ModelCapability")
class 模型能力:
    模型来源: str = strawberry.field(name="modelSource")
    模型名称: str = strawberry.field(name="modelName")
    支持视觉: bool = strawberry.field(name="supportsVision")
    支持文件内容: bool = strawberry.field(name="supportsFileContent")


@strawberry.type(name="Plugin")
class 插件:
    名称: str = strawberry.field(name="name")
    路径: str = strawberry.field(name="path")


@strawberry.type(name="FolderItem")
class 文件夹项:
    名称: str = strawberry.field(name="name")
    路径: str = strawberry.field(name="path")


@strawberry.type(name="FileTreeNode")
class 文件树节点:
    名称: str = strawberry.field(name="name")
    类型: str = strawberry.field(name="type")  # "file" | "dir"
    大小: int = strawberry.field(name="size")
    子节点: List['文件树节点'] = strawberry.field(name="children")


@strawberry.type(name="FileContent")
class 文件内容:
    内容: str = strawberry.field(name="content")
    是否二进制: bool = strawberry.field(name="isBinary")
    编码: str = strawberry.field(name="encoding")


@strawberry.type(name="VisualizationData")
class 可视化数据:
    节点: str = strawberry.field(name="nodes")  # JSON string
    连线: str = strawberry.field(name="edges")  # JSON string


@strawberry.type(name="AuditLogItem")
class 审计日志项:
    时间戳: str = strawberry.field(name="timestamp")
    用户: str = strawberry.field(name="user")
    操作: str = strawberry.field(name="action")
    状态: str = strawberry.field(name="status")
    详情: str = strawberry.field(name="details")  # JSON string


@strawberry.type(name="PerformanceMetrics")
class 性能指标:
    总请求数: int = strawberry.field(name="totalRequests")
    平均响应时间: float = strawberry.field(name="avgResponseTime")
    端点统计: str = strawberry.field(name="endpointStats")  # JSON string


@strawberry.type(name="ChatStreamEvent")
class 聊天流事件:
    内容: str = strawberry.field(name="content")
    完成: bool = strawberry.field(name="done")


@strawberry.type(name="GithubSyncResult")
class GitHub同步结果:
    状态: str = strawberry.field(name="status")
    消息: str = strawberry.field(name="message")
    仓库URL: str = strawberry.field(name="repoUrl")
    上传文件数: int = strawberry.field(name="fileCount")


@strawberry.type(name="ModelMarketItem")
class 模型市场项:
    ID: str = strawberry.field(name="id")
    名称: str = strawberry.field(name="name")
    描述: str = strawberry.field(name="description")
    下载量: int = strawberry.field(name="downloads")
    点赞数: int = strawberry.field(name="likes")
    大小: str = strawberry.field(name="size")


@strawberry.type(name="DownloadStatus")
class 下载状态:
    状态: str = strawberry.field(name="status")  # "downloading" | "completed" | "failed"
    进度: float = strawberry.field(name="progress")  # 0-1
    消息: str = strawberry.field(name="message")


@strawberry.type(name="Template")
class 模板:
    ID: str = strawberry.field(name="id")
    名称: str = strawberry.field(name="name")
    描述: str = strawberry.field(name="description")
    分类: str = strawberry.field(name="category")


@strawberry.type(name="PackageFile")
class 打包文件:
    文件名: str = strawberry.field(name="filename")
    大小: int = strawberry.field(name="size")
    创建时间: str = strawberry.field(name="createdAt")


# ─── 列表响应类型（带分页） ───
@strawberry.type(name="SessionListResponse")
class 会话列表响应:
    会话列表: List[会话] = strawberry.field(name="sessions")
    分页: 分页信息 = strawberry.field(name="pageInfo")


@strawberry.type(name="MessageListResponse")
class 消息列表响应:
    消息列表: List[消息] = strawberry.field(name="messages")
    分页: 分页信息 = strawberry.field(name="pageInfo")


@strawberry.type(name="LocalModelListResponse")
class 本地模型列表响应:
    模型列表: List[本地模型] = strawberry.field(name="models")
    分页: 分页信息 = strawberry.field(name="pageInfo")


@strawberry.type(name="PluginListResponse")
class 插件列表响应:
    插件列表: List[str] = strawberry.field(name="plugins")
    分页: 分页信息 = strawberry.field(name="pageInfo")


@strawberry.type(name="AuditLogListResponse")
class 审计日志列表响应:
    日志列表: List[审计日志项] = strawberry.field(name="logs")
    分页: 分页信息 = strawberry.field(name="pageInfo")


@strawberry.type(name="ModelMarketSearchResponse")
class 模型市场搜索响应:
    模型列表: List[模型市场项] = strawberry.field(name="models")
    分页: 分页信息 = strawberry.field(name="pageInfo")


@strawberry.type(name="TemplateListResponse")
class 模板列表响应:
    模板列表: List[模板] = strawberry.field(name="templates")
    分页: 分页信息 = strawberry.field(name="pageInfo")


@strawberry.type(name="PackageFileListResponse")
class 打包文件列表响应:
    文件列表: List[打包文件] = strawberry.field(name="files")
    分页: 分页信息 = strawberry.field(name="pageInfo")


# ─── 输入类型 ───
@strawberry.input(name="SettingsInput")
class 设置输入:
    模型来源: Optional[str] = strawberry.field(name="modelSource", default=None)
    本地模型路径: Optional[str] = strawberry.field(name="localModelPath", default=None)
    本地模型名称: Optional[str] = strawberry.field(name="localModelName", default=None)
    API基础URL: Optional[str] = strawberry.field(name="apiBaseUrl", default=None)
    模型名称: Optional[str] = strawberry.field(name="modelName", default=None)
    API密钥: Optional[str] = strawberry.field(name="apiKey", default=None)
    温度: Optional[float] = strawberry.field(name="temperature", default=None)
    最大令牌数: Optional[int] = strawberry.field(name="maxTokens", default=None)
    GitHub令牌: Optional[str] = strawberry.field(name="githubToken", default=None)
    GitHub用户名: Optional[str] = strawberry.field(name="githubUsername", default=None)
    GitHub默认仓库: Optional[str] = strawberry.field(name="githubDefaultRepo", default=None)
    GitHub可见性: Optional[str] = strawberry.field(name="githubVisibility", default=None)


@strawberry.input(name="AttachmentInput")
class 附件输入:
    名称: str = strawberry.field(name="name")
    类型: str = strawberry.field(name="mimeType")
    大小: int = strawberry.field(name="size")
    数据: str = strawberry.field(name="data")  # base64
