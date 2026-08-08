"""
聊天上下文构建模块 — 7 步流程构建完整对话上下文

流程：加载会话 → 规划注入 → 附件处理 → 知识库检索 → Token 检查 → 消息构建 → 健康度评估

本模块将各类上下文信息（系统提示、工具结果、知识库片段、规划状态）
组装为 LLM 可消费的消息列表。
"""
import asyncio
from datetime import datetime

from 智能体.任务分类器 import 分类任务
from 智能体.模型能力注册表 import 模型能力注册表

from .会话缓存 import 会话缓存
from .并发控制 import _递增会话版本
from .文件读写操作 import load_session, load_settings, save_session
from .文件路由 import _resolve_plugin_path
from .日志配置 import 获取日志器
from .路由公共 import (
    ContextManager,
    _agent_available,
    _format_attachment_descriptions,
    _format_file_tree,
    _会话锁,
    _记忆管理器,
    _项目上下文分析器,
    llm_client,
    local_model_client,
    tool_router,
)

logger = 获取日志器("聊天上下文")

# 全局模型能力注册表实例
_model_registry = 模型能力注册表()


# 各Tab页的差异化系统提示词
TAB_SYSTEM_PROMPTS = {
    "develop": (
        "你是专业的 ComfyUI 插件开发专家。请严格按照以下提供的规范编写代码。\n"
        "要求：\n"
        "1. 提供完整、可运行的代码，不要省略逻辑。\n"
        "2. __init__.py 等核心文件必须遵循英文命名，其他业务文件夹允许使用中文。\n"
        "\n## 前端 UI 设计规范（必须遵守）\n\n"
        "创建 ComfyUI 自定义节点时，必须为每个节点设计富 UI 前端扩展，使节点放置到画布上就能直观展示功能：\n\n"
        "1. **每个插件必须包含前端扩展**：\n"
        "   - `__init__.py` 中声明 `WEB_DIRECTORY`\n"
        "   - 前端目录下创建 `.js` 扩展文件，通过 `app.registerExtension()` 注册\n\n"
        "2. **节点 UI 设计原则**（放置即可见，无需执行）：\n"
        "   - 使用 Canvas Widget 绘制：按钮、进度条、状态指示器、预览区域\n"
        "   - 使用 DOM Widget 嵌入：视频播放区、富文本区、配置面板、时间轴\n"
        "   - 在 `nodeCreated` 钩子中添加所有自定义 Widget\n"
        "   - 设置合理的 `computeSize` 确保 UI 区域可见\n\n"
        "3. **推荐的 UI 组件**：\n"
        "   - 核心操作区：Canvas Widget 按钮（draw + mouse 方法）\n"
        "   - 预览/展示区：DOM Widget（背景色 + 占位图标 + 尺寸标注）\n"
        "   - 状态反馈：Canvas 进度条 widget\n"
        "   - 控制面板：DOM Widget 内嵌按钮组\n\n"
        "4. **节点大小**：通过 `node.setSize([宽, 高])` 设置适合展示 UI 的初始尺寸\n\n"
        "5. **参考知识库**：`知识库/开发插件/前端扩展/节点富UI前端开发.md` 中有完整的代码模板\n"
    ),
    "optimize": (
        "你是专业的 ComfyUI 插件性能优化与代码质量专家。"
        "你擅长从多个维度对插件代码进行深度分析，"
        "并提供具有量化依据的改进方案。\n\n"
        "## 性能反模式识别\n"
        "请检查以下常见反模式，如发现请明确指出并提供修正方案：\n"
        "1. 在循环内部调用 torch.cuda.empty_cache() — 破坏 CUDA 缓存复用，应移至循环外\n"
        "2. 推理代码未使用 torch.no_grad() 或 torch.inference_mode() — 浪费显存记录梯度\n"
        "3. 频繁在 CPU/GPU 间移动大张量（.cpu()/.cuda() 在循环中） — 应批量传输\n"
        "4. 未实现 IS_CHANGED 导致每次执行都重新计算 — 应添加缓存判断\n"
        "5. 同步 HTTP/文件 IO 阻塞执行线程 — 建议改用异步方案\n"
        "6. 全局变量持有大型 Tensor 引用未及时释放 — 导致显存泄漏\n"
        "7. 重复加载模型（每次执行 node 都重新 load） — 应缓存到类变量或全局\n"
        "8. 使用 Python list 拼接大量 Tensor 再 torch.cat — 应预分配内存\n\n"
        "## 并发安全审查\n"
        "ComfyUI 的执行队列可能并发调用节点，请检查：\n"
        "1. 全局可变状态是否有线程安全保护（Lock/RLock）\n"
        "2. 文件写入是否有原子操作保障（避免部分写入）\n"
        "3. 共享资源（模型实例、缓存字典）的访问是否安全\n"
        "4. asyncio 环境中是否存在竞态条件\n"
        "5. 是否存在锁泄漏风险（未在 finally 中释放）\n\n"
        "## 错误处理检查\n"
        "健壮的插件需要完整的错误处理链：\n"
        "1. 关键操作（模型加载、文件IO、网络请求）是否有 try-except 保护\n"
        "2. 异常是否被记录到日志（而非静默吞掉）\n"
        "3. 是否有合理的降级方案（如模型加载失败时的回退）\n"
        "4. 用户可见的错误信息是否清晰（避免暴露内部堆栈）\n"
        "5. 资源清理是否在 finally/with 中保证执行\n\n"
        "## 架构与设计建议\n"
        "1. 识别循环依赖（A→B→A），建议抽取公共接口解耦\n"
        "2. 检查单个文件是否承担过多职责（>500行建议拆分）\n"
        "3. 检查 import 扇出是否过高（单文件>10个内部模块引用需关注）\n"
        "4. 评估接口边界清晰度（是否有明确的公开API vs 内部实现）\n"
        "5. 配置是否硬编码（建议提取为可配置参数）\n\n"
        "## 回复格式\n"
        "请按以下结构组织优化建议：\n"
        "1. **问题摘要**：一句话概述核心问题\n"
        "2. **影响评估**：该问题对性能/安全/维护性的影响程度（高/中/低）\n"
        "3. **具体代码**：给出修改前后的代码对比\n"
        "4. **预期效果**：量化预期改善（如\"预计减少30%显存占用\"）\n"
        "5. **风险提示**：修改可能带来的副作用\n"
    ),
    "visualize": (
        "你是专业的 ComfyUI 插件功能分析专家。请帮助用户理解插件的功能结构和依赖关系。\n"
        "要求：\n"
        "1. 提供清晰的功能拆解和可视化建议。\n"
        "2. 识别模块间的耦合关系和优化机会。\n"
        "3. 本模式为只读分析模式：仅提供文件读取/搜索类工具，没有任何写入工具，"
        "不要尝试调用 edit_file、write_plugin_file 等修改类工具。\n"
        "4. 如果用户要求修改代码，请直接分析并给出修改建议，"
        "同时明确告知用户：请切换到「开发插件」或「优化插件」Tab 执行实际修改。\n"
    ),
}

# 合法的 activeTab 取值
_VALID_ACTIVE_TABS = ("develop", "optimize", "visualize")


# ─── P0-1 前缀稳定化：动态上下文块 ──────────────────────────
# 每轮都会变化的动态内容（知识库检索、主动踩坑、项目说明、文件树、AST 摘要、
# 跨会话记忆）不再拼进 messages[0] 的 system prompt，而是由一条独立的
# [本轮参考上下文] 消息承载，插在最后一条用户消息之前。
# 这样「system + 历史」构成从第 0 条起字节级稳定的前缀，可命中
# DeepSeek / Kimi / 百炼 / 智谱四家的前缀匹配式上下文缓存。
# 动态块只能用 user 角色：Qwen3.5 chat_template 硬校验 system 必须首位，
# 第二条 system 会直接报错（见既有踩坑记录）。
_DYNAMIC_CONTEXT_PREFIX = "[本轮参考上下文]"


def _构建动态上下文块(retrieved_rules: str = "", proactive_pitfalls: str = "",
                     项目说明: str = "", 文件树摘要: str = "",
                     项目摘要: str = "", 记忆文本: str = "",
                     pattern_hint: str = "") -> str:
    """把五类每轮变化的动态内容合成为一条 [本轮参考上下文] 消息正文。

    各内容段保留原有小节标题；全部为空时返回空串（不插入动态块）。
    动态块紧邻用户问题，规避 "lost in the middle" 注意力衰减。
    """
    parts = [p.strip("\n") for p in (
        retrieved_rules, proactive_pitfalls, 项目说明,
        文件树摘要, 项目摘要, 记忆文本, pattern_hint,
    ) if p and p.strip()]
    if not parts:
        return ""
    return _DYNAMIC_CONTEXT_PREFIX + "\n" + "\n\n".join(parts)


def _追加动态块(complete_messages: list, text: str) -> None:
    """按 [本轮参考上下文] 前缀匹配定位动态块并追加内容（无块时新建）。

    新建的块插到最后一条用户消息之前（与 _build_chat_context 的组装规则一致）；
    末条非 user 时追加到末尾，避免切断 assistant(tool_calls)→tool 相邻性。
    """
    if not text or not complete_messages:
        return
    # 传入文本自带前缀时先剥掉，否则新建分支会拼出双前缀
    if text.startswith(_DYNAMIC_CONTEXT_PREFIX):
        text = text[len(_DYNAMIC_CONTEXT_PREFIX):]
        if not text:
            return
    for msg in complete_messages:
        if msg.get("role") == "user" and str(msg.get("content", "")).startswith(_DYNAMIC_CONTEXT_PREFIX):
            msg["content"] = str(msg.get("content", "")) + text
            return
    new_msg = {"role": "user", "content": _DYNAMIC_CONTEXT_PREFIX + text}
    if complete_messages[-1].get("role") == "user":
        complete_messages.insert(len(complete_messages) - 1, new_msg)
    else:
        complete_messages.append(new_msg)


def _注入规划上下文(complete_messages: list, 计划结果: dict) -> None:
    """将规划结果中的关键决策信息注入动态上下文块（[本轮参考上下文]）。

    注入内容包括：
    - needs_frontend: 是否需要创建前端 JS 文件
    - needs_ux_optimization: 是否需要 UX 优化手段
    - needs_interactive_ui: 是否需要高级交互模式
    - reference_categories: 应查阅的技术方案类别
    - plan_steps: 执行步骤纲要

    P0-1：不再写 messages[0] 的 system prompt（会破坏前缀缓存），
    改为按前缀匹配定位动态块追加。
    """
    if not complete_messages or not 计划结果:
        return

    parts = []

    # 1. 前端需求决策
    需要前端 = 计划结果.get("needs_frontend")
    前端原因 = 计划结果.get("frontend_reason", "")
    if 需要前端 is True and 前端原因:
        parts.append(
            f"\n## 规划决策：需要前端 JS 扩展\n"
            f"- 原因：{前端原因}\n"
            f"- 请在 `网页资源/` 目录下创建对应的 .js 文件\n"
            f"- 务必在 `__init__.py` 中设置 `WEB_DIRECTORY = \"./网页资源\"` 并在 `__all__` 中导出"
        )
    elif 需要前端 is True:
        parts.append(
            "\n## 规划决策：需要前端 JS 扩展\n"
            "- 请在 `网页资源/` 目录下创建对应的 .js 文件\n"
            "- 务必在 `__init__.py` 中设置 `WEB_DIRECTORY = \"./网页资源\"` 并在 `__all__` 中导出"
        )
    elif 需要前端 is False:
        parts.append(
            "\n## 规划决策：无需前端 JS 扩展\n"
            "- 该插件为纯 Python 节点，不需要创建 `网页资源/` 目录\n"
            "- `__init__.py` 中不需要设置 `WEB_DIRECTORY`"
        )

    # 2. UX 优化需求
    需要UX优化 = 计划结果.get("needs_ux_optimization")
    UX原因 = 计划结果.get("ux_reason", "")
    if 需要UX优化 is True:
        原因文本 = f"（原因：{UX原因}）" if UX原因 else ""
        parts.append(
            f"\n## 规划决策：需要用户体验优化{原因文本}\n"
            f"- 请务必查阅知识库 `开发插件/用户体验优化/减少繁复操作与流畅度提升.md`\n"
            f"- 应用匹配的模式：状态持久化、智能默认值、防抖节流、加载状态、错误边界等\n"
            f"- 注意：UX 优化可与前端 JS 共存，也可独立存在"
        )

    # 3. 交互 UI 复杂度
    需要交互UI = 计划结果.get("needs_interactive_ui")
    交互UI原因 = 计划结果.get("interactive_ui_reason", "")
    if 需要交互UI is True:
        原因文本 = f"（原因：{交互UI原因}）" if 交互UI原因 else ""
        parts.append(
            f"\n## 规划决策：需要高级交互 UI 模式{原因文本}\n"
            f"- 请查阅知识库 `知识库/技术方案/UI交互/` 目录下对应方案\n"
            f"- 常用模式：控件联动、模式切换动态UI、组件条件停用、设置联动UI\n"
            f"- 交互复杂度较高，建议先理清状态流转再编码"
        )

    # 4. 技术方案参考
    参考类别 = 计划结果.get("reference_categories", [])
    if 参考类别:
        cats_text = "、".join(参考类别)
        parts.append(
            f"\n## 规划决策：建议查阅技术方案（{cats_text}）\n"
            f"- 知识库路径 `知识库/技术方案/` 下有已验证的实现参考\n"
            f"- 请先搜索匹配的方案文档，复用已有经验，避免重复踩坑"
        )

    # 5. 执行步骤纲要
    plan_steps = 计划结果.get("plan_steps", [])
    if plan_steps:
        steps_text = "\n".join(f"  {i+1}. {step}" for i, step in enumerate(plan_steps))
        parts.append(f"\n## 规划执行步骤\n{steps_text}")

    if parts:
        _追加动态块(complete_messages, "".join(parts))


def _注入用户决策前端标志(complete_messages: list, 用户选择: dict) -> None:
    """从用户确认的决策中提取前端需求标志，注入动态上下文块。"""
    if not complete_messages:
        return

    for q, a in 用户选择.items():
        a_lower = a.lower()
        # 匹配用户关于需要/不需要前端的回答
        if "前端" in q or "界面" in q or "js" in q.lower():
            if any(kw in a_lower for kw in ("需要", "要", "是", "yes", "true")):
                _追加动态块(complete_messages, (
                    "\n## 用户决策：需要前端 JS 扩展\n"
                    "- 请在 `网页资源/` 目录下创建对应的 .js 文件\n"
                    "- 务必在 `__init__.py` 中设置 `WEB_DIRECTORY = \"./网页资源\"` 并在 `__all__` 中导出"
                ))
            elif any(kw in a_lower for kw in ("不需要", "不用", "否", "no", "false")):
                _追加动态块(complete_messages, (
                    "\n## 用户决策：无需前端 JS 扩展\n"
                    "- 该插件为纯 Python 节点，不需要创建 `网页资源/` 目录\n"
                    "- `__init__.py` 中不需要设置 `WEB_DIRECTORY`"
                ))
            break


# ─── 工具使用指导（System Prompt 追加块） ──────────────────────
_TOOL_USAGE_GUIDE = """

## 执行原则（最高优先级）
你是一个自动执行的编程 Agent。收到任务后必须：
1. **直接执行，不要描述方案等待确认** — 读取文件后立即调用 write_plugin_file/edit_file/batch_edit 完成修改
2. **一次完成所有修改** — 在一次对话中完成全部文件修改，不要分步等待用户回复
3. **修改完成后才说明** — 先执行操作，完成后简要总结改了什么
4. **遇到技术性错误自行解决** — 如果工具执行失败，分析错误并重试（仅限可自行排查的技术错误）

## 停下等待规则（与执行原则同等优先级，必须遵守）
- 用户只要求**检查、分析、审查、评估**（未要求修改）时，汇报结果后必须立即结束回合，**禁止自作主张开始修改**
- **一旦向用户提出问题，必须立即停下** — 提问后立即结束回合，等待用户真实回复；**严禁替用户作答**（如自问自答"收到，立即执行"）后继续调用任何工具
- 遇到必须由用户拍板的决策点（需求不明确、存在多种方向）时，调用 ask_user 工具提问，然后结束回合等待回答，不得猜测用户意图擅自执行

禁止行为：
- ❌ 输出"修改方案"或"改造计划"然后等待用户说"开始"
- ❌ 用户要求修改时，读完文件后只输出分析而不执行修改
- ❌ 对每个修改步骤都请求用户确认
- ❌ 提问后自问自答，伪造用户回复继续执行

## 工具能力
你可以使用以下工具直接操作当前插件文件：
- read_plugin_file(file_path, start_line?, end_line?): 读取指定文件内容，支持行号范围分段读取
- search_plugin_file(file_path, pattern, use_regex?, context_lines?): 搜索文件内容，返回匹配行号和上下文
- write_plugin_file(file_path, content): 全量写入文件（覆盖整个文件，适用于新建文件或大幅重写）
- edit_file(file_path, patch): 增量编辑文件（仅修改需要变更的部分，适用于局部修改，更高效）
- batch_edit(operations): 批量操作多个文件（适用于创建完整项目结构、同时修改多个文件）
- list_plugin_files(): 查看插件的完整文件目录结构
- update_readme(changelog_entry): 在 README.md 的"更新介绍"模块追加一条更新记录（仅在实际修改了插件文件后调用）
- update_plan(steps): 维护任务执行计划清单（仅内存态，不写入文件）
- ask_user(question, options?): 向用户提出必须由用户拍板的问题；调用后必须立即结束回合并等待用户真实回复，严禁自行假设答案继续执行

## 多步骤任务计划
涉及 3 步以上的任务：开始前先调用 update_plan 列出全部步骤，每完成一步立即再次调用更新状态（覆盖式：每次传入完整清单）。
这能防止长任务中遗漏步骤。简单任务（1-2 步）无需调用。

## 文件修改策略
你有三种文件修改方式，请根据场景选择：

### write_plugin_file（全量写入）
适用场景：
- 新建文件
- 文件大幅重写（超过一半内容需要修改）
- 文件较小（<50行）且修改较多

### edit_file（增量编辑）
适用场景：
- 修改文件中的几行或几十行
- 修改大文件的局部内容
- 只需要添加/删除/替换少量代码

### batch_edit（批量操作）
适用场景：
- 创建新插件时一次创建所有文件（__init__.py、节点文件、前端 JS 等）
- 同时修改多个相关文件
- 批量创建项目目录结构

操作类型说明：
- create: 创建新文件（文件已存在则跳过，不覆盖）
- write: 全量写入文件（覆盖已有内容）
- edit: 增量编辑（content 为 SEARCH/REPLACE 块补丁，格式同 edit_file）
- delete: 删除文件（自动创建 .bak 备份）

batch_edit 调用示例：
```json
{"name": "batch_edit", "arguments": {"operations": [
  {"action": "create", "file_path": "__init__.py", "content": "from .nodes import *\\n"},
  {"action": "create", "file_path": "nodes.py", "content": "class MyNode:\\n    pass\\n"},
  {"action": "edit", "file_path": "config.py",
   "content": "<<<<<<< SEARCH\\nold line\\n=======\\nnew line\\n>>>>>>> REPLACE"}
]}}
```

优先级：**局部修改时优先使用 edit_file**，可以大幅节省 token 和时间。
**创建完整项目时优先使用 batch_edit**，一次操作多个文件更高效。

## 任务完成后的收尾步骤
仅当本次任务**实际修改了插件文件**（write/edit/batch_edit 成功执行过）时，才调用 `update_readme` 追加一条更新记录；
纯阅读、分析、问答任务不要调用。
要求：
- 更新说明必须是一句简洁易懂的大白话，禁止使用专业术语
- 例如：✅ "新增了图片风格转换功能"  ❌ "实现了基于 StyleGAN 的 latent space 映射"
- 每次只在"更新介绍"模块追加，不要修改其他模块

选择规则：
- 当文件超过50行时，必须优先使用 edit_file 进行局部修改，而非 write_plugin_file 全量覆写。
- 对于创建新文件，使用 write_plugin_file；对于修改已有文件，优先使用 edit_file。
- edit_file 使用 SEARCH/REPLACE 块格式，SEARCH 部分必须与文件实际内容逐行一致。

## 避免重复操作
- 调用工具前，先回顾上文中的工具执行记录，检查是否已完成过相同操作
- 如果一个文件已经被 read_plugin_file 读取过且中间没有被修改，不要再次读取
- 如果一个文件已经被 write_plugin_file/edit_file 写入过，在没有新需求时不要重复写入
- 特别注意：连续调用同一工具2次以上通常意味着操作逻辑有误

edit_file 的 patch 参数使用 SEARCH/REPLACE 块格式，例如：
```
<<<<<<< SEARCH
def hello():
    print("old")
=======
def hello():
    print("new")
    print("added")
>>>>>>> REPLACE
```
一次补丁可包含多个 SEARCH/REPLACE 块，按在文件中出现的顺序依次编写。

使用 edit_file 时：
1. 先用 read_plugin_file 读取当前文件内容
2. 基于读取到的最新内容编写 SEARCH 部分（逐字照抄，不要凭记忆默写）
3. SEARCH 部分必须与文件实际内容逐行一致，且在文件中唯一
4. 若 SEARCH 片段在文件中出现多次，请多包含几行上下文确保唯一定位

当用户要求修改文件时，你的执行流程：
1. 用 read_plugin_file 读取相关文件
2. 立即调用 edit_file 或 write_plugin_file 完成修改（不要停下来描述方案）
3. 所有修改完成后，简要列出改动点

## 大文件处理策略
当文件超过 200 行时，使用「搜索 → 定位 → 精读」工作流：

1. 用 search_plugin_file 定位目标代码：
   {"name": "search_plugin_file", "arguments": {"file_path": "nodes.py", "pattern": "class MyNode"}}
   → 返回匹配行号和上下文

2. 基于行号用 read_plugin_file 精确读取：
   {"name": "read_plugin_file", "arguments": {"file_path": "nodes.py", "start_line": 45, "end_line": 120}}
   → 只读需要的部分，避免截断

3. 修改时用 edit_file 增量编辑（无需重读整个文件）

这种方式比一次性读取整个大文件更高效，避免内容被截断丢失关键信息。
"""


def _get_model_context_limit(model_name: str, max_tokens_setting: int) -> int:
    """获取模型上下文窗口大小（委托给模型能力注册表，未知模型使用动态回退）"""
    info = _model_registry.获取模型信息(model_name)
    if info:
        return info.get("上下文窗口", 32000)
    return max(max_tokens_setting * 4, 32000)


def _calculate_context_health(complete_messages: list, model_name: str, max_tokens_setting: int) -> dict:
    """计算上下文健康度指标"""

    from 智能体.记忆与上下文压缩 import ContextManager

    _ctx_mgr = ContextManager()
    # 估算所有消息的 token 总量
    _total_chars = sum(len(str(m.get("content", ""))) for m in complete_messages)
    _total_tokens = int(_total_chars * 0.75)  # 与 API 客户端统一的 chars×0.75 估算

    _limit = _get_model_context_limit(model_name, max_tokens_setting)
    _percent = min((_total_tokens / _limit) * 100, 100.0)

    if _percent < 70:
        _level = "ok"
    elif _percent < 85:
        _level = "info"
    elif _percent < 95:
        _level = "warning"
    else:
        _level = "critical"

    return {
        "used_tokens": _total_tokens,
        "max_tokens": _limit,
        "usage_percent": round(_percent, 1),
        "level": _level
    }


# ─── 公共聊天上下文构建 ────────────────────────────────────────

async def _build_chat_context(
    request, session_id, message, attachments=None,
    plugin_context=None, data=None, active_tab="develop", truncate_at=None
):
    """构建聊天上下文（供 /chat-stream 和 WebSocket 共用）

    Returns:
        dict: 包含以下键：
            - session_data: 会话数据字典
            - complete_messages: 完整消息列表（含系统提示）
            - settings: 设置字典
            - model_source: 模型来源（"api" 或 "local"）
            - full_message: 用户完整消息文本
            - plugin_path: 插件路径（Path 对象或 None）
            - system_prompt: 系统提示词文本
            - history_to_send: 发送给模型的历史消息列表

    或抛出 ValueError(msg)

    truncate_at: 编辑重生成时使用，保留 messages[0:truncate_at] 后再追加新用户消息。
    """
    if attachments is None:
        attachments = []
    if data is None:
        data = {}

    # active_tab 校验：不在合法集合内时降级为 develop
    if active_tab not in _VALID_ACTIVE_TABS:
        active_tab = "develop"

    if not _agent_available:
        raise ValueError("智能体模块未加载，聊天功能不可用")

    # 1. 加载会话（优先从 LRU 缓存读取，未命中再走磁盘 I/O）
    session_data = await 会话缓存.get(session_id)
    if session_data is None:
        session_data = await asyncio.to_thread(load_session, session_id)
        if session_data is None:
            raise ValueError("会话不存在")
        await 会话缓存.put(session_id, session_data)

    # 2. 处理附件
    attachment_descriptions = _format_attachment_descriptions(attachments)

    full_message = message
    if attachment_descriptions:
        full_message = message + "\n" + "\n".join(attachment_descriptions)

    # 编辑重生成：截断指定位置之后的消息
    if truncate_at is not None:
        messages = session_data.get("messages", [])
        if truncate_at < 0 or truncate_at > len(messages):
            raise ValueError(f"truncate_at 越界: {truncate_at}, 当前消息数 {len(messages)}")
        session_data["messages"] = messages[:truncate_at]

    # 3. 将用户消息存入会话（不包含 _image_attachments 临时字段）
    now = datetime.now().isoformat(timespec="seconds")
    user_msg = {"role": "user", "content": message, "timestamp": now}
    if attachments:
        user_msg["attachments"] = [
            {"name": a.get("name"), "type": a.get("type"), "size": a.get("size")}
            for a in attachments
        ]
    session_data.setdefault("messages", []).append(user_msg)
    # P2: 保存前递增会话版本号（并发冲突检测基础设施）
    _递增会话版本(session_data)
    # P1: 使用会话锁保护写入，防止并发冲突
    async with _会话锁.获取锁(session_id):
        await asyncio.to_thread(save_session, session_data)
    # 写穿透：保存成功后用最新数据回填缓存（而非清空）
    await 会话缓存.put(session_id, session_data)

    # 4. 并行执行：知识库检索与上下文压缩
    #    两者逻辑上无数据依赖（检索输入是 full_message，压缩输入是
    #    session_data["messages"]，压缩不消费检索结果），用 gather 并行以缩短
    #    首字延迟；retrieve_knowledge 是同步函数，包 asyncio.to_thread 避免阻塞事件循环。
    #    return_exceptions=True 保证异常隔离：任一失败不影响另一方，失败方单独降级。

    # 4.1 读取设置（P1-1：同步 I/O 放入线程池）—— 压缩预算、模型来源与主动踩坑检索均依赖设置
    settings = await asyncio.to_thread(load_settings)

    # 压缩参数准备（LLM 摘要模式：需提前确定模型来源和客户端）
    # 本地模型不使用 LLM 摘要（_生成LLM摘要 中已防御），此处也确保不传递 local_model_client
    model_source = data.get("model_source") or settings.get("model_source", "api")
    _compress_llm_client = None if model_source == "local" else llm_client
    context_mgr = ContextManager()
    # 使用模型实际上下文窗口长度（而非生成 max_tokens）作为压缩预算
    # 这样只有在真正接近模型上下文上限时才会触发压缩，避免过早截断导致智能体遗忘
    _model_name = settings.get("model_name", "")
    _context_window = _get_model_context_limit(_model_name, settings.get("max_tokens", 4096))
    _compress_budget = int(_context_window * 0.9)  # 留 10% 余量给系统提示和新回复

    retrieved_rules, history_to_send = await asyncio.gather(
        # RAG 检索知识库（按 activeTab 差异化检索，同步函数 → 线程池）
        asyncio.to_thread(tool_router.retrieve_knowledge, full_message, active_tab=active_tab),
        # 压缩上下文（内部已有 5s 超时降级）
        context_mgr.compress_history(
            session_data["messages"],
            max_tokens=_compress_budget,
            reserved_recent=10,
            llm_client=_compress_llm_client,
            model_source=model_source
        ),
        return_exceptions=True,
    )

    # 异常隔离：检索失败降级为空规则，并置位 kb_status 降级提醒信号
    if isinstance(retrieved_rules, Exception):
        logger.warning(f"知识库检索失败（降级为空规则）: {retrieved_rules}")
        kb_retrieval_failed = True
        kb_failure_reason = str(retrieved_rules)
        retrieved_rules = ""
    else:
        # 记录本次知识库检索是否失败（供 SSE 推送 kb_status 降级提醒）
        kb_retrieval_failed = getattr(tool_router, "kb_retrieval_failed", False)
        kb_failure_reason = getattr(tool_router, "kb_failure_reason", "")

    # 异常隔离：压缩失败降级为保留最近 10 条（与 reserved_recent 对齐）
    if isinstance(history_to_send, Exception):
        logger.warning(f"上下文压缩失败（降级为保留最近10条）: {history_to_send}")
        history_to_send = [dict(m) for m in session_data["messages"][-10:]]

    # 4.2 主动踩坑检索（编码/优化任务时，需设置开启）
    proactive_pitfalls = ""
    if active_tab in ("develop", "optimize") and settings.get("proactive_pitfall_check", True):
        _task_type = 分类任务(full_message, active_tab)
        if _task_type in ("编码", "优化"):
            try:
                from 智能体.工具路由器 import retrieve_pitfalls_proactive
                proactive_pitfalls = retrieve_pitfalls_proactive(
                    full_message, _task_type, active_tab
                )
            except Exception as e:
                logger.debug(f"主动踩坑检索失败（忽略）: {e}")

    # 5. 构造系统提示词（P0-1 前缀稳定化：只保留字节级恒定的静态段）
    # 静态区 = Tab 人格 + 工具使用指导 + 回复语言指令，多轮请求间字节级不变，
    # 可命中 DeepSeek/Kimi/百炼/智谱四家从第 0 条起前缀匹配的上下文缓存。
    # 每轮变化的五类动态内容（知识库检索、主动踩坑、项目说明、文件树/AST 摘要、
    # 跨会话记忆）全部移入 [本轮参考上下文] 独立消息（见步骤 8 组装）。
    base_prompt = TAB_SYSTEM_PROMPTS.get(active_tab, TAB_SYSTEM_PROMPTS["develop"])
    system_prompt = base_prompt

    plugin_path = _resolve_plugin_path(plugin_context)
    _has_plugin = bool(plugin_context) and plugin_path is not None and plugin_path.exists()
    if _has_plugin:
        # 工具使用指导为纯静态文本，紧跟人格之后注入
        system_prompt += _TOOL_USAGE_GUIDE

    # 动态区收集：知识库检索 → 主动踩坑 → 项目说明 → 文件树/项目摘要 → 记忆 → 工作模式提示
    项目说明 = ""
    文件树摘要 = ""
    项目摘要 = ""
    if _has_plugin:
        # 项目级用户指令文件（对标 Claude Code 的 CLAUDE.md / Codex 的 AGENTS.md）：
        # 用户可在插件根目录放 AI说明.md，给 AI 留下该插件的专属规矩（如禁改目录、代码风格），
        # 注入动态区并限幅 1000 字符，避免撑爆上下文
        try:
            _指令文件 = None
            for _fname in ("AI说明.md", "AGENTS.md"):
                _cand = plugin_path / _fname
                if _cand.exists() and _cand.is_file():
                    _指令文件 = _cand
                    break
            if _指令文件 is not None:
                _指令内容 = (await asyncio.to_thread(
                    _指令文件.read_text, encoding="utf-8", errors="replace"
                )).strip()
                if _指令内容:
                    if len(_指令内容) > 1000:
                        _指令内容 = _指令内容[:1000] + "\n…（说明过长已截断）"
                    项目说明 = f"## 用户项目说明（来自 {_指令文件.name}，须严格遵守）\n{_指令内容}"
        except Exception as e:
            logger.debug(f"读取项目指令文件失败（忽略）: {e}")

        from .文件读写操作 import scan_plugin_file_tree
        try:
            # P1-1：目录扫描属同步 I/O，放入线程池
            file_tree = await asyncio.to_thread(scan_plugin_file_tree, str(plugin_path))
            tree_summary = _format_file_tree(file_tree)
            # 文件树摘要限幅：超长目录截断到 600 字符，完整结构可用 list_plugin_files 获取
            if len(tree_summary) > 600:
                tree_summary = tree_summary[:600] + "\n…（目录过大已截断，完整结构请调用 list_plugin_files）"
            文件树摘要 = (
                f"## 当前操作的插件: {plugin_context}\n"
                f"插件路径: {plugin_path}\n"
                f"文件结构:\n{tree_summary}\n\n"
                "以上文件结构已提供，无需调用 list_plugin_files 重复获取。"
                "当用户要求修改文件时，直接读取相关文件并完成修改。"
            )

            # 项目上下文深度分析（AST 级结构化摘要）
            if _项目上下文分析器 is not None:
                try:
                    _项目摘要文本 = await asyncio.to_thread(_项目上下文分析器.分析项目, plugin_path)
                    if _项目摘要文本:
                        项目摘要 = f"--- 当前插件项目上下文 ---\n{_项目摘要文本}"
                except Exception as e:
                    logger.debug(f"项目上下文分析失败: {e}")
        except Exception as e:
            logger.exception(f"注入插件上下文失败: {e}")

    # 跨会话记忆
    记忆文本 = ""
    if _记忆管理器 is not None:
        try:
            记忆文本 = await asyncio.to_thread(
                _记忆管理器.获取记忆注入文本,
                str(plugin_path) if plugin_path else None,
            ) or ""
        except Exception as e:
            logger.debug(f"跨会话记忆注入失败（忽略）: {e}")

    # 待处理的工作模式提示（上一轮检测到的重复模式）
    _pending_hint = session_data.pop("_pending_pattern_hint", None) or ""
    if _pending_hint:
        logger.debug(f"注入工作模式提示: {_pending_hint[:80]}...")

    # 合成动态上下文块（由独立的 user 消息承载，见步骤 8 组装）
    动态上下文块 = _构建动态上下文块(
        retrieved_rules, proactive_pitfalls, 项目说明,
        文件树摘要, 项目摘要, 记忆文本, _pending_hint,
    )

    # 5.4 回复语言指令：跟随前端界面语言（双语切换按钮），覆盖模型默认输出语言。
    # 放在系统提示末尾（最后、最强的一句），本地模型与 API 模型共用此单一注入点。
    # 前端未传 language 时不注入，保持模型原有行为。
    _ui_lang = (data or {}).get("language")
    _lang_directive = {
        "zh-CN": "\n\n## 回复语言\n请始终使用简体中文回复用户，无论用户以何种语言提问。",
        "en": "\n\n## Response Language\nAlways respond to the user in English, regardless of the language used in the question.",
    }.get(_ui_lang)
    if _lang_directive:
        system_prompt += _lang_directive
        logger.debug(f"注入回复语言指令: {_ui_lang}")

    # 7. 上下文压缩已在步骤 4 与知识库检索并行完成（history_to_send）

    # 附件增强注入：文本附件已在 full_message 中展开，这里处理图片附件的 base64 传递
    image_attachments = [
        a for a in attachments
        if a.get("type", "").startswith("image/") and a.get("data")
    ]
    # 本地模型视觉能力前置检查：不支持视觉的模型不注入图片附件，避免 Worker 推理崩溃
    if image_attachments and model_source == "local" and local_model_client is not None:
        _local_model_name = (data or {}).get("local_model_name") or settings.get("local_model_name", "")
        try:
            _supports_vision = local_model_client._supports_vision(_local_model_name)
            logger.info(
                f"[视觉检测] model_name={_local_model_name!r}, "
                f"_supports_vision={_supports_vision}, "
                f"client._is_multimodal={getattr(local_model_client, '_is_multimodal', 'N/A')}, "
                f"image_count={len(image_attachments)}"
            )
        except Exception as e:
            logger.warning(f"[视觉检测] 本地模型视觉能力检测异常（按不支持处理）: {e}")
            _supports_vision = False
        if not _supports_vision:
            logger.warning(f"[视觉检测] 本地模型 {_local_model_name} 不支持图片分析，已跳过图片附件")
            # 在用户消息中追加提示，避免用户以为图片被模型处理了
            _img_names = ", ".join(a.get("name", "图片") for a in image_attachments)
            full_message += f"\n\n[注意: 当前本地模型不支持图片分析，已忽略图片附件: {_img_names}]"
            image_attachments = []

    # 音频附件：仅本地多模态音频模型（如 Gemma 4 12B/E 系）支持，
    # API 模型链路暂不支持音频输入，直接丢弃并提示
    audio_attachments = [
        a for a in attachments
        if a.get("type", "").startswith("audio/") and a.get("data")
    ]
    if audio_attachments:
        _supports_audio = False
        if model_source == "local" and local_model_client is not None:
            _local_model_name = (data or {}).get("local_model_name") or settings.get("local_model_name", "")
            try:
                _supports_audio = local_model_client._supports_audio(_local_model_name)
                logger.info(
                    f"[音频检测] model_name={_local_model_name!r}, "
                    f"_supports_audio={_supports_audio}, "
                    f"audio_count={len(audio_attachments)}"
                )
            except Exception as e:
                logger.warning(f"[音频检测] 本地模型音频能力检测异常（按不支持处理）: {e}")
        if not _supports_audio:
            logger.warning("[音频检测] 当前模型不支持音频分析，已跳过音频附件")
            _audio_names = ", ".join(a.get("name", "音频") for a in audio_attachments)
            full_message += f"\n\n[注意: 当前模型不支持音频分析，已忽略音频附件: {_audio_names}]"
            audio_attachments = []
    if (image_attachments or audio_attachments or attachment_descriptions) and history_to_send:
        last_user_idx = None
        for i in range(len(history_to_send) - 1, -1, -1):
            if history_to_send[i].get("role") == "user":
                last_user_idx = i
                break
        if last_user_idx is not None:
            # 创建新字典避免污染 session_data。compress_history 返回的是新 dict，
            # 但为清晰起见仍重建。将 content 替换为含附件描述的 full_message，
            # 同时为图片/音频附件设置临时字段（仅请求链可见）
            new_msg = dict(history_to_send[last_user_idx])
            new_msg["content"] = full_message
            if image_attachments:
                new_msg["_image_attachments"] = image_attachments
            if audio_attachments:
                new_msg["_audio_attachments"] = audio_attachments
            history_to_send[last_user_idx] = new_msg

    # 8. 构建完整 messages 列表
    # P0-1：动态上下文块插到最后一条用户消息之前（role=user 单条消息）；
    # history_to_send 为空或末条非 user 时追加到末尾。
    # history_to_send 是压缩层返回的新副本，插入不会污染 session_data；
    # 非流式链路（handle_chat）同样消费 history_to_send，两条路径行为一致。
    if 动态上下文块:
        _动态消息 = {"role": "user", "content": 动态上下文块}
        if history_to_send and history_to_send[-1].get("role") == "user":
            history_to_send.insert(len(history_to_send) - 1, _动态消息)
        else:
            history_to_send.append(_动态消息)
    complete_messages = [{"role": "system", "content": system_prompt}] + history_to_send

    # model_source（已在步骤 4 并行压缩前确定）

    # 自动模型路由（可选启用）
    if settings.get("auto_model_routing", False) and model_source == "api":
        _task_type = 分类任务(full_message, active_tab)
        _preferred = settings.get("preferred_models", {})
        _recommended = _preferred.get(_task_type) or _model_registry.获取推荐模型(_task_type)
        if _recommended:
            _current_model = settings.get("model_name", "")
            if _recommended != _current_model:
                logger.info(
                    f"[模型编排] 任务类型: {_task_type}, 当前模型: {_current_model}, "
                    f"切换到推荐模型: {_recommended}"
                )
                settings["model_name"] = _recommended
            else:
                logger.info(f"[模型编排] 任务类型: {_task_type}, 推荐模型与当前一致: {_recommended}")
        else:
            logger.info(f"[模型编排] 任务类型: {_task_type}, 未找到推荐模型，保持当前模型")
    elif settings.get("auto_model_routing", False):
        # auto_model_routing 开启但非 API 模式，仅记录日志
        logger.debug("[模型编排] 自动路由仅适用于 API 模式，当前为本地模式，跳过")

    return {
        "session_data": session_data,
        "complete_messages": complete_messages,
        "settings": settings,
        "model_source": model_source,
        "full_message": full_message,
        "plugin_path": plugin_path if (plugin_path is not None and plugin_path.exists()) else None,
        "system_prompt": system_prompt,
        "history_to_send": history_to_send,
        # 云端知识库检索失败信号（供 SSE 推送 kb_status 降级提醒）
        "kb_retrieval_failed": kb_retrieval_failed,
        "kb_failure_reason": kb_failure_reason,
    }
