"""
任务分类器 - 分析用户意图，分类为不同任务类型

基于规则的轻量分类器，不依赖额外模型。
"""
import logging

logger = logging.getLogger("NodeCraftAI.任务分类器")


# 任务类型常量
任务类型_编码 = "编码"
任务类型_优化 = "优化"
任务类型_分析 = "分析"
任务类型_通用 = "通用"

# 关键词映射
_关键词_编码 = [
    "实现", "写", "创建", "添加", "修改", "开发", "编码", "函数", "类",
    "节点", "插件", "组件", "widget", "input", "output", "node",
    "代码", "编写", "新建", "生成"
]

_关键词_优化 = [
    "优化", "性能", "加速", "减少", "提升", "效率", "瓶颈", "内存",
    "显存", "VRAM", "缓存", "批处理", "并行", "延迟", "耗时",
    "简化", "重构", "改进"
]

_关键词_分析 = [
    "分析", "查看", "检查", "结构", "关系", "依赖", "数据流",
    "可视化", "图表", "统计", "监控", "日志", "调试", "排查"
]


def 分类任务(user_message: str, active_tab: str = "develop") -> str:
    """分析用户意图，返回任务类型。

    Args:
        user_message: 用户的输入消息
        active_tab: 当前激活的 Tab（develop/optimize/visualize）

    Returns:
        任务类型：编码/优化/分析/通用
    """
    if not user_message:
        return 任务类型_通用

    msg_lower = user_message.lower()

    # 1. Tab 上下文优先
    if active_tab == "optimize":
        # optimize Tab 默认倾向优化，除非明确是编码请求
        if any(kw in msg_lower for kw in _关键词_编码[:5]):
            return 任务类型_编码
        return 任务类型_优化

    if active_tab == "visualize":
        # visualize Tab 默认倾向分析
        return 任务类型_分析

    # 2. 关键词匹配（develop Tab）
    编码分 = sum(1 for kw in _关键词_编码 if kw in msg_lower)
    优化分 = sum(1 for kw in _关键词_优化 if kw in msg_lower)
    分析分 = sum(1 for kw in _关键词_分析 if kw in msg_lower)

    logger.debug(f"[任务分类] 编码={编码分} 优化={优化分} 分析={分析分}")

    # 3. 选择最高分
    if 优化分 > 编码分 and 优化分 > 分析分 and 优化分 >= 2:
        return 任务类型_优化
    if 分析分 > 编码分 and 分析分 >= 2:
        return 任务类型_分析
    if 编码分 >= 1:
        return 任务类型_编码

    # 4. 默认编码（develop Tab）
    return 任务类型_编码
