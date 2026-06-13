from pathlib import Path
import sys

_plugin_root = str(Path(__file__).parent.parent.resolve())
if _plugin_root not in sys.path:
    sys.path.insert(0, _plugin_root)
from 后端.系统环境映射 import get_plugin_root


class ToolRouter:
    """知识库检索与意图路由"""

    def __init__(self):
        self.知识库路径 = Path(get_plugin_root()) / "知识库"
        # 意图→文档映射（支持多级目录）
        self.意图映射 = {
            "节点": ["基础节点开发/V3节点结构与注册.md"],
            "输入": ["输入输出系统/输入类型详解.md"],
            "输出": ["输入输出系统/输出类型详解.md"],
            "类型": ["输入输出系统/数据类型系统.md"],
            "前端": ["前端扩展/JS钩子与UI组件.md"],
            "UI": ["前端扩展/JS钩子与UI组件.md"],
            "高级": ["高级开发模式/动态输入与类型匹配.md"],
            "生命周期": ["高级开发模式/生命周期与缓存.md"],
            "迁移": ["迁移指南/V1到V3迁移.md"],
            "打包": ["基础节点开发/项目结构与打包.md"],
            "发布": ["基础节点开发/项目结构与打包.md"],
        }

    def retrieve_knowledge(self, user_message):
        """
        根据用户消息检索相关知识库文档

        Returns:
            str: 检索到的规范文档内容，拼接为一个字符串
        """
        # 1. 尝试读取总览文件
        总览路径 = self.知识库路径 / "总览.md"
        总览内容 = ""
        if 总览路径.exists():
            总览内容 = 总览路径.read_text(encoding="utf-8")

        # 2. 意图匹配 - 找到需要读取的文件
        匹配文件列表 = set()
        for 关键词, 文件列表 in self.意图映射.items():
            if 关键词 in user_message:
                匹配文件列表.update(文件列表)

        # 3. 默认返回基础文档
        if not 匹配文件列表:
            匹配文件列表.add("基础节点开发/V3节点结构与注册.md")

        # 4. 读取匹配的文件内容
        知识内容 = []
        for 相对路径 in 匹配文件列表:
            完整路径 = self.知识库路径 / 相对路径
            if 完整路径.exists():
                内容 = 完整路径.read_text(encoding="utf-8")
                知识内容.append(f"## 参考文档: {相对路径}\n\n{内容}")

        # 5. 拼接返回
        if not 知识内容 and not 总览内容:
            return ""  # 知识库为空时返回空字符串

        结果 = ""
        if 总览内容:
            结果 += f"## 知识库总览\n\n{总览内容}\n\n"
        if 知识内容:
            结果 += "\n\n---\n\n".join(知识内容)

        return 结果
