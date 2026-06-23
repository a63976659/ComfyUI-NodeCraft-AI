"""GraphQL Mutation 根类型

NodeCraft AI GraphQL 变更操作入口。

提供测试 Mutation、会话 CRUD、设置变更、文件/插件操作、GitHub 同步、
模型市场等变更字段。

GraphQL 命名规范：所有暴露给 GraphQL 的根类型名/字段名/参数名必须满足
``[_a-zA-Z0-9]``。Python 端保持中文命名，通过 Strawberry 的
``name=`` 参数（字段）以及 ``Annotated[..., strawberry.argument(name=...)]``
（参数）为 GraphQL 暴露 ASCII 兼容名称。
"""

from typing import Annotated

import strawberry
from strawberry.types import Info

from .解析器.会话逻辑 import (
    创建会话 as _创建会话,
    更新会话标题 as _更新会话标题,
    删除会话 as _删除会话,
)
from .解析器.文件逻辑 import (
    写入文件 as _写入文件,
    创建插件文件夹 as _创建插件文件夹,
    分析插件 as _分析插件,
    执行可视化分析 as _执行可视化分析,
    打包插件 as _打包插件,
)
from .解析器.GitHub逻辑 import (
    同步到GitHub as _同步到GitHub,
)
from .解析器.市场逻辑 import (
    下载模型 as _下载模型,
    从模板创建 as _从模板创建,
)
# 类型别名：避免与同名方法冲突（例如方法 ``更新设置`` / ``卸载模型`` 等）
from .类型定义 import (
    会话,
    设置 as 设置类型,
    设置输入 as 设置输入类型,
    可视化数据,
    GitHub同步结果,
)


# ─── 常用参数别名（GraphQL ASCII 名称） ───
_会话ID参数 = Annotated[str, strawberry.argument(name="sessionId")]
_标题参数 = Annotated[str, strawberry.argument(name="title")]
_新标题参数 = Annotated[str, strawberry.argument(name="newTitle")]
_插件目录参数 = Annotated[str, strawberry.argument(name="pluginFolder")]
_消息内容参数 = Annotated[str, strawberry.argument(name="messageContent")]
_插件上下文参数 = Annotated[str, strawberry.argument(name="pluginContext")]
_模型来源参数 = Annotated[str, strawberry.argument(name="modelSource")]
_模型名称参数 = Annotated[str, strawberry.argument(name="modelName")]
_测试消息参数 = Annotated[str, strawberry.argument(name="message")]
_输入参数 = Annotated[设置输入类型, strawberry.argument(name="input")]
_插件名称参数 = Annotated[str, strawberry.argument(name="pluginName")]
_插件路径参数 = Annotated[str, strawberry.argument(name="pluginPath")]
_文件路径参数 = Annotated[str, strawberry.argument(name="filePath")]
_文件内容参数 = Annotated[str, strawberry.argument(name="content")]
_编码参数 = Annotated[str, strawberry.argument(name="encoding")]
_模式参数 = Annotated[str, strawberry.argument(name="mode")]
_仓库名称参数 = Annotated[str, strawberry.argument(name="repoName")]
_忽略gitignore参数 = Annotated[bool, strawberry.argument(name="ignoreGitignore")]
_模型ID参数 = Annotated[str, strawberry.argument(name="modelId")]
_模板ID参数 = Annotated[str, strawberry.argument(name="templateId")]
_项目名称参数 = Annotated[str, strawberry.argument(name="projectName")]


@strawberry.type(name="Mutation")
class Mutation:
    """NodeCraft AI GraphQL 变更操作根类型"""

    @strawberry.mutation(name="ping", description="测试 Mutation（后续扩展）")
    async def 测试(
        self,
        info: Info,
        消息: _测试消息参数 = "hello",
    ) -> str:
        """回显接收到的消息，验证 Mutation 通路。"""
        return f"收到: {消息}"

    @strawberry.mutation(name="createSession", description="创建新会话")
    async def 创建新会话(
        self,
        info: Info,
        标题: _标题参数 = "新会话",
        插件目录: _插件目录参数 = "",
    ) -> 会话:
        """创建一个新会话并返回会话摘要。"""
        return await _创建会话(info, 标题, 插件目录)

    @strawberry.mutation(name="updateSessionTitle", description="更新会话标题")
    async def 修改会话标题(
        self,
        info: Info,
        会话ID: _会话ID参数,
        新标题: _新标题参数,
    ) -> bool:
        """更新指定会话标题，成功返回 True。"""
        return await _更新会话标题(info, 会话ID, 新标题)

    @strawberry.mutation(name="deleteSession", description="删除会话")
    async def 删除指定会话(
        self,
        info: Info,
        会话ID: _会话ID参数,
    ) -> bool:
        """删除指定会话，成功返回 True。"""
        return await _删除会话(info, 会话ID)

    @strawberry.mutation(name="sendMessage", description="发送聊天消息（同步，等待完整回复）")
    async def 发送消息(
        self,
        info: Info,
        会话ID: _会话ID参数,
        消息内容: _消息内容参数,
        插件上下文: _插件上下文参数 = "",
        模型来源: _模型来源参数 = "",  # 空 = 使用设置中的默认值
    ) -> str:
        """返回 AI 回复的完整文本"""
        from .解析器.聊天逻辑 import 发送聊天消息

        return await 发送聊天消息(
            info,
            会话ID,
            消息内容,
            插件上下文,
            模型来源,
        )

    @strawberry.mutation(name="updateSettings", description="更新系统设置")
    async def 更新设置(
        self,
        info: Info,
        输入: _输入参数,
    ) -> 设置类型:
        """合并更新设置：仅写入显式提供（非 None）的字段，返回更新后的设置。"""
        from .解析器.设置逻辑 import 更新设置 as _更新设置
        return await _更新设置(info, 输入)

    @strawberry.mutation(name="unloadModel", description="卸载本地模型释放显存")
    async def 卸载模型(self, info: Info) -> bool:
        """卸载本地模型，释放显存。"""
        from .解析器.设置逻辑 import 卸载模型 as _卸载模型
        return await _卸载模型(info)

    @strawberry.mutation(name="resetModelStatus", description="重置模型状态（清除失败黑名单）")
    async def 重置模型状态(
        self,
        info: Info,
        模型名称: _模型名称参数 = "",
    ) -> bool:
        """清除模型失败黑名单。空字符串视为清除全部。"""
        from .解析器.设置逻辑 import 重置模型状态 as _重置模型状态
        return await _重置模型状态(info, 模型名称)

    # ─── 文件与插件变更 ─────────────────────────────────
    @strawberry.mutation(name="createPluginFolder", description="创建插件脚手架目录")
    async def 创建插件文件夹(
        self,
        info: Info,
        插件名称: _插件名称参数,
    ) -> str:
        """返回创建的插件绝对路径。"""
        return await _创建插件文件夹(info, 插件名称)

    @strawberry.mutation(name="writeFile", description="写入文件内容（路径穿越防护 + 自动备份）")
    async def 写入文件(
        self,
        info: Info,
        文件路径: _文件路径参数,
        内容: _文件内容参数,
        编码: _编码参数 = "utf-8",
    ) -> bool:
        return await _写入文件(info, 文件路径, 内容, 编码)

    @strawberry.mutation(name="analyzePlugin", description="分析插件依赖关系（不持久化）")
    async def 分析插件(
        self,
        info: Info,
        插件路径: _插件路径参数,
    ) -> str:
        """返回 graphData JSON 字符串。"""
        return await _分析插件(info, 插件路径)

    @strawberry.mutation(name="runVisualization", description="执行可视化分析（文件/功能模式）并落盘")
    async def 执行可视化分析(
        self,
        info: Info,
        插件路径: _插件路径参数,
        模式: _模式参数,
    ) -> 可视化数据:
        return await _执行可视化分析(info, 插件路径, 模式)

    @strawberry.mutation(name="packPlugin", description="打包插件为 ZIP，返回生成的文件名")
    async def 打包插件(
        self,
        info: Info,
        插件路径: _插件路径参数,
    ) -> str:
        return await _打包插件(info, 插件路径)

    # ─── GitHub 同步 ─────────────────────────────────
    @strawberry.mutation(name="syncToGithub", description="同步本地插件目录到 GitHub 仓库")
    async def 同步到GitHub(
        self,
        info: Info,
        插件路径: _插件路径参数,
        仓库名称: _仓库名称参数,
        忽略gitignore: _忽略gitignore参数 = False,
    ) -> GitHub同步结果:
        return await _同步到GitHub(info, 插件路径, 仓库名称, 忽略gitignore)

    # ─── 模型市场 / 模板 ──────────────────────────
    @strawberry.mutation(name="downloadModel", description="启动模型下载任务，返回任务ID")
    async def 下载模型(
        self,
        info: Info,
        模型ID: _模型ID参数,
    ) -> str:
        return await _下载模型(info, 模型ID)

    @strawberry.mutation(name="createFromTemplate", description="从模板创建新项目，返回项目路径")
    async def 从模板创建(
        self,
        info: Info,
        模板ID: _模板ID参数,
        项目名称: _项目名称参数,
    ) -> str:
        return await _从模板创建(info, 模板ID, 项目名称)
