"""工具结果智能摘要模块

替代对 tool 消息的 2000 字符硬截断，根据工具类型和内容特征智能生成摘要。
支持 Python/JS/JSON/Markdown 等文件类型的结构化摘要，以及通用内容的智能截断。
"""

import json
import logging
import re as _re

logger = logging.getLogger("NodeCraftAI.工具结果摘要器")

# 工具结果当前轮次压缩阈值的单一来源（字符数）：
# 工具路由器与 API模型客户端 均引用此常量，避免两处硬编码字面值不一致
#（本模块不依赖包内其他模块，无循环导入风险）
MAX_TOOL_RESULT_CHARS = 4000

# 文件读取类工具的当前轮次压缩阈值（字符数）：
# 读取结果是模型工作的直接素材，压得过狠会迫使模型分页反复重读（烧轮次烧 token），
# 因此放宽到 12000；历史轮次瘦身（800）与动态窗口压缩仍按各自阈值执行
MAX_FILE_READ_RESULT_CHARS = 12000

# 现有各层截断/摘要产出的标记片段，用于识别内容是否已被压缩过，避免逐层重复摘要
# 注："搜索结果已截断"/"批量操作摘要已截断" 分别含 "结果已截断"/"摘要已截断" 子串，已覆盖；
# "文件已截断"（代码审查路由）与 "文件内容已截断" 非子串关系，需单独列出
_截断标记片段 = (
    "结果已截断",
    "内容已截断",
    "文件内容已截断",
    "摘要已截断",
    "省略详细实现",
    "文件列表已截断",
    "stdout 已截断",
    "stderr 已截断",
    "文件已截断",
)

# 文件读取类工具：结果被截断时追加分段读取的行动指引
_文件读取类工具 = ("read_plugin_file", "search_plugin_file", "grep_plugin_files")
分段读取指引 = (
    "提示：可先用 search_plugin_file 搜索关键词定位行号，"
    "再用 read_plugin_file 的 start_line/end_line 分段读取完整内容"
)


def 已被截断(content: str) -> bool:
    """判断内容是否已被某一层截断/摘要过（含已知截断标记）。

    用于各压缩层短路：已压缩内容不再重复调用摘要器，避免信息逐层丢失。

    误判安全性：本判断仅决定"是否再次调用摘要器"，不影响硬截断兜底——
    即使标记短语碰巧出现在正常文件内容中被误判，超过 800/4000 阈值的内容
    仍会被对应压缩层硬截断，不存在无限长内容进入上下文的风险。
    """
    if not content:
        return False
    return any(标记 in content for 标记 in _截断标记片段)


def 摘要化工具结果(tool_name: str, raw_content: str, call_index: int = None, max_chars: int = 1500) -> str:
    """根据工具类型和内容特征，智能生成摘要。

    如果内容长度不超过 max_chars，直接返回原文（可能加时序前缀）。
    超过时按工具类型选择不同的摘要策略。

    Args:
        tool_name: 工具名称，用于选择摘要策略
        raw_content: 工具返回的原始内容
        call_index: 可选的调用序号，用于标记多次调用的时序
        max_chars: 摘要最大字符数（前缀不计入此限制）

    支持的工具：
    - list_plugin_files / read_plugin_file / write_plugin_file / edit_file
    - search_plugin_file / grep_plugin_files / update_readme / batch_edit
    - find_plugin_files / rename_plugin_file / check_python_syntax
    - 未知工具：智能截断（保留首尾关键信息，非硬截断）
    """
    # 时序前缀：帮助 LLM 区分同一工具的不同轮次调用结果
    prefix = f"[第{call_index}次调用] " if call_index is not None else ""

    if not raw_content:
        return prefix + (raw_content or "")

    if len(raw_content) <= max_chars:
        return prefix + raw_content if prefix else raw_content

    try:
        if tool_name == "list_plugin_files" or tool_name == "find_plugin_files":
            摘要 = _摘要_文件列表(raw_content, max_chars)
        elif tool_name == "read_plugin_file":
            摘要 = _摘要_文件内容(raw_content, max_chars)
        elif tool_name in ("write_plugin_file", "edit_file", "update_readme",
                           "rename_plugin_file", "check_python_syntax", "execute_command"):
            摘要 = _摘要_写入结果(raw_content, max_chars, tool_name)
        elif tool_name in ("search_plugin_file", "grep_plugin_files"):
            摘要 = _摘要_搜索结果(raw_content, max_chars)
        elif tool_name == "batch_edit":
            摘要 = _摘要_批量操作(raw_content, max_chars)
        else:
            摘要 = _智能通用截断(raw_content, max_chars)
    except (ValueError, TypeError, OSError) as e:
        logger.warning("摘要化失败，回退到智能截断: %s", e)
        摘要 = _智能通用截断(raw_content, max_chars)

    # 文件读取类工具被截断时追加行动指引（幂等：已含指引则不重复追加）
    if tool_name in _文件读取类工具 and 分段读取指引 not in 摘要:
        摘要 += "\n" + 分段读取指引

    return prefix + 摘要 if prefix else 摘要


# ---------------------------------------------------------------------------
# 各工具策略
# ---------------------------------------------------------------------------

def _摘要_文件列表(content: str, max_chars: int) -> str:
    """目录结构通常不大，直接返回。极端超长时保留前 max_chars 字符。"""
    if len(content) <= max_chars:
        return content
    return content[:max_chars] + "\n...[文件列表已截断]"


def _摘要_文件内容(content: str, max_chars: int) -> str:
    """代码文件摘要：按文件类型提取关键结构，包含文件特征信息。"""
    # 处理可能的二进制乱码
    if _疑似二进制(content):
        return "[文件摘要] 内容疑似包含二进制数据，无法生成摘要"

    lines = content.split("\n")
    total_lines = len(lines)
    文件大小 = len(content.encode('utf-8'))
    文件类型 = _检测文件类型(content)

    if 文件类型 == "python":
        结构文本 = _提取Python结构(lines)
    elif 文件类型 == "javascript":
        结构文本 = _提取JS结构(lines)
    elif 文件类型 == "json":
        结构文本 = _提取JSON结构(content)
    elif 文件类型 == "markdown":
        结构文本 = _提取Markdown结构(lines)
    else:
        结构文本 = _提取纯文本结构(lines)

    # 文件大小可读格式
    大小描述 = _格式化文件大小(文件大小)

    # 组装摘要（包含文件特征：行数、大小、类型）
    摘要 = (
        f"文件 ({total_lines}行, {大小描述}, {文件类型})\n"
        f"--- 关键结构 ---\n"
        f"{结构文本}\n"
        f"--- 省略详细实现 ({total_lines}行) ---"
    )

    # 如果摘要本身还是太长，进行二次截断
    if len(摘要) > max_chars:
        摘要 = 摘要[:max_chars] + "\n...[摘要已截断]"

    return 摘要


def _摘要_写入结果(content: str, max_chars: int, tool_name: str = "") -> str:
    """write/edit 等写入操作的摘要：提取文件路径、操作类型和变更量。

    写入操作的返回值通常较短，但当多次调用时需要区分具体操作。
    摘要格式示例：✅ 已写入 plugin.py (新增 25 行)
    """
    if len(content) <= max_chars:
        # 即使不需要截断，也尝试增强摘要质量
        return _增强写入摘要(content, tool_name)
    # 超长时先增强再截断
    增强内容 = _增强写入摘要(content, tool_name)
    if len(增强内容) <= max_chars:
        return 增强内容
    return _智能通用截断(content, max_chars)


def _摘要_搜索结果(content: str, max_chars: int) -> str:
    """搜索结果摘要：提取匹配行核心信息。

    格式示例：
        🔍 在 xxx.py 中找到 5 处匹配 "pattern"
        --- 匹配行 ---
        L100: def foo():
        L200: foo()
        ...
    """
    if len(content) <= max_chars:
        return content
    lines = content.split("\n")
    # 保留头部概述行 + 匹配行（去重上下文行）
    结果行 = []
    for line in lines:
        stripped = line.strip()
        # 跳过纯上下文行（不以行号开头的缩进行），除非是头/尾标记
        if not stripped:
            continue
        if stripped.startswith("🔍") or stripped.startswith("📄"):
            结果行.append(line)
        elif stripped and (stripped[0].isdigit() or stripped.startswith("L")):
            结果行.append(line)
        elif "..." in stripped and ("省略" in stripped or "更多" in stripped):
            结果行.append(line)
    if not 结果行:
        # 退化：保留前 60% 行
        保留行数 = max(1, int(len(lines) * 0.6))
        结果行 = lines[:保留行数]
    结果 = "\n".join(结果行)
    if len(结果) > max_chars:
        结果 = 结果[:max_chars] + "\n...[搜索结果已截断]"
    return 结果


def _摘要_批量操作(content: str, max_chars: int) -> str:
    """批量操作结果摘要：保留汇总行 + 表格头 + 失败项。

    成功的操作只需计数，失败的操作保留完整行供排查。
    """
    if len(content) <= max_chars:
        return content
    lines = content.split("\n")
    结果行 = []
    for line in lines:
        stripped = line.strip()
        # 汇总行："## 批量操作完成"、"**成功: N | 失败: M | 总计: T**"
        if stripped.startswith("##") or stripped.startswith("**"):
            结果行.append(line)
            continue
        # 表格头
        if stripped.startswith("|") and ("文件路径" in stripped or "操作" in stripped):
            结果行.append(line)
            continue
        # 失败的操作（含 ❌）
        if "❌" in stripped:
            结果行.append(line)
            continue
        # 表格分隔线
        if stripped.startswith("|---"):
            结果行.append(line)
            continue
    if not 结果行:
        结果行 = lines[:max(1, int(len(lines) * 0.3))]
    结果 = "\n".join(结果行)
    if len(结果) > max_chars:
        结果 = 结果[:max_chars] + "\n...[批量操作摘要已截断]"
    return 结果


def _智能通用截断(content: str, max_chars: int) -> str:
    """未知工具类型的智能截断：保留头部概述 + 选择性保留错误/关键行。

    比硬截断更智能：尝试识别错误信息、关键结果行，优先保留。
    """
    if len(content) <= max_chars:
        return content

    lines = content.split("\n")
    total = len(lines)

    # 策略：提取包含关键信号的行的索引
    错误行索引 = []
    成功行索引 = []
    标题行索引 = []
    for i, line in enumerate(lines):
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith("❌") or "错误" in stripped or "失败" in stripped:
            错误行索引.append(i)
        elif stripped.startswith("✅") or "成功" in stripped:
            成功行索引.append(i)
        elif stripped.startswith("##") or stripped.startswith("#"):
            标题行索引.append(i)

    # 构建摘要：前 30% + 关键行 + 末尾 10%
    前部行数 = max(1, int(total * 0.3))
    后部行数 = max(1, int(total * 0.1))

    已包含 = set(range(前部行数)) | set(range(total - 后部行数, total))
    关键行 = []
    for idx in 标题行索引 + 错误行索引 + 成功行索引:
        if idx not in 已包含:
            关键行.append(idx)
            已包含.add(idx)
    关键行.sort()

    摘要行 = []
    摘要行.extend(lines[:前部行数])
    if 关键行:
        摘要行.append(f"\n...[中间省略 {total - 前部行数 - 后部行数 - len(关键行)} 行，以下为关键行]...\n")
        for idx in 关键行:
            摘要行.append(lines[idx])
    摘要行.append(f"\n...[省略 {total - 前部行数 - len(关键行) - 后部行数} 行]...\n")
    摘要行.extend(lines[-后部行数:])

    结果 = "\n".join(摘要行)
    if len(结果) > max_chars:
        结果 = 结果[:max_chars] + "\n...[摘要已截断]"
    return 结果


# ---------------------------------------------------------------------------
# 结构提取
# ---------------------------------------------------------------------------

def _提取Python结构(lines: list) -> str:
    """提取 Python 文件的关键结构：import、class/def 定义、装饰器、常量、docstring 首行。"""
    结果行 = []
    in_function_body = False

    for i, line in enumerate(lines):
        stripped = line.strip()

        # 空行：保留顶层空行作为分隔
        if not stripped:
            if not in_function_body:
                结果行.append("")
            continue

        # import 语句
        if stripped.startswith(("import ", "from ")):
            结果行.append(line)
            in_function_body = False
            continue

        # 装饰器
        if stripped.startswith("@"):
            结果行.append(line)
            in_function_body = False
            continue

        # class 定义
        if stripped.startswith("class "):
            结果行.append(line)
            in_function_body = False
            continue

        # function 定义
        if stripped.startswith("def "):
            结果行.append(line)
            in_function_body = True
            continue

        # 大写常量赋值 (如 MAX_TOKENS = 4096)
        if "=" in stripped and not stripped.startswith(("=", "!", "<", ">", "|")):
            变量名 = stripped.split("=")[0].strip()
            if 变量名 and 变量名.isupper() and 变量名.replace("_", "").isalnum():
                结果行.append(line)
                in_function_body = False
                continue

        # docstring 首行（紧跟在 def/class 后面的 """ 或 '''）
        if stripped.startswith(('"""', "'''")):
            结果行.append(line)
            # 单行 docstring（同一行内关闭）
            引号 = '"""' if stripped.startswith('"""') else "'''"
            if stripped.count(引号) >= 2 and len(stripped) > 3:
                in_function_body = True
            continue

        # 其他行：如果在函数体内，跳过；顶层代码保留
        if not in_function_body:
            # 顶层代码（无缩进或极小缩进）保留
            if len(line) - len(line.lstrip()) <= 4 and not line.startswith("\t\t"):
                结果行.append(line)

    return "\n".join(结果行)


def _提取JS结构(lines: list) -> str:
    """提取 JavaScript 文件的关键结构：import/require、定义行、export、注释。"""
    结果行 = []
    for line in lines:
        stripped = line.strip()
        if not stripped:
            continue
        if (stripped.startswith(("import ", "const ", "let ", "var ",
                                  "function ", "class ", "export "))
                or stripped.startswith("//")
                or "require(" in stripped):
            结果行.append(line)
    return "\n".join(结果行)


def _提取JSON结构(content: str) -> str:
    """JSON 文件：只保留第一层 key 结构 + value 类型提示。"""
    try:
        data = json.loads(content)
    except (json.JSONDecodeError, ValueError):
        # 解析失败，回退到纯文本
        return _提取纯文本结构(content.split("\n"))

    if isinstance(data, dict):
        条目 = []
        for key, value in data.items():
            类型提示 = type(value).__name__
            if isinstance(value, list):
                类型提示 = f"list[{len(value)}]"
            elif isinstance(value, dict):
                类型提示 = f"dict[{len(value)}keys]"
            elif isinstance(value, str):
                类型提示 = f"str({len(value)})"
            条目.append(f'  "{key}": {类型提示}')
        return "{\n" + ",\n".join(条目) + "\n}"
    elif isinstance(data, list):
        return f"[...共 {len(data)} 个元素]"
    else:
        return str(type(data).__name__)


def _提取Markdown结构(lines: list) -> str:
    """Markdown 文件：保留所有标题行 + 每段首行。"""
    结果行 = []
    in_paragraph = False

    for line in lines:
        stripped = line.strip()

        # 标题行始终保留
        if stripped.startswith("#"):
            结果行.append(line)
            in_paragraph = False
            continue

        # 空行结束段落
        if not stripped:
            in_paragraph = False
            continue

        # 段落首行
        if not in_paragraph:
            # 截断过长的行
            if len(stripped) > 100:
                结果行.append(stripped[:100] + "...")
            else:
                结果行.append(line)
            in_paragraph = True

    return "\n".join(结果行)


def _提取纯文本结构(lines: list) -> str:
    """其他类型：保留前 30% 行 + 末尾 10% 行。"""
    total = len(lines)
    if total <= 10:
        return "\n".join(lines)

    前部行数 = max(1, int(total * 0.3))
    后部行数 = max(1, int(total * 0.1))

    前部 = lines[:前部行数]
    后部 = lines[-后部行数:]

    结果 = "\n".join(前部)
    结果 += f"\n...[省略 {total - 前部行数 - 后部行数} 行]...\n"
    结果 += "\n".join(后部)
    return 结果


# ---------------------------------------------------------------------------
# 辅助函数
# ---------------------------------------------------------------------------

def _检测文件类型(content: str) -> str:
    """通过内容特征检测文件类型。"""
    sample_lines = content.split("\n", 20)

    for line in sample_lines:
        stripped = line.strip()
        # Python 特征
        if stripped.startswith(("from ", "def ", "class ")):
            return "python"
        if stripped.startswith("import ") and ":" not in stripped:
            # Python 的 import 没有冒号（JS 的 import ... from 有冒号的可能性也有，但 from 开头的肯定是 Python）
            return "python"
        # JS 特征
        if stripped.startswith(("const ", "let ", "var ", "function ")):
            return "javascript"
        if stripped.startswith("import ") and "from" in stripped:
            return "javascript"
        if "require(" in stripped:
            return "javascript"
        # JSON 特征
        if stripped.startswith(("{", "[")):
            return "json"
        # Markdown 特征
        if stripped.startswith("# "):
            return "markdown"

    # 更宽泛的判断
    head = content[:500]
    if "def " in head or "import " in content[:200]:
        return "python"
    if "function " in head or "const " in content[:200]:
        return "javascript"

    return "text"


def _疑似二进制(content: str) -> bool:
    """检测内容是否疑似二进制乱码。"""
    if not content:
        return False
    sample = content[:1000]
    # 计算不可打印字符比例
    不可打印数 = sum(1 for c in sample if c not in ('\n', '\r', '\t') and not c.isprintable())
    比例 = 不可打印数 / len(sample) if sample else 0
    return 比例 > 0.3


def _格式化文件大小(size_bytes: int) -> str:
    """将字节数转换为可读的文件大小描述。"""
    if size_bytes < 1024:
        return f"{size_bytes}B"
    elif size_bytes < 1024 * 1024:
        return f"{size_bytes / 1024:.1f}KB"
    else:
        return f"{size_bytes / (1024 * 1024):.1f}MB"


def _增强写入摘要(content: str, tool_name: str) -> str:
    """增强写入操作的摘要质量：提取文件路径、操作类型和变更量。

    尝试从写入结果文本中提取关键信息，若无法提取则原样返回。
    """
    if not content:
        return content

    # 尝试提取文件路径（常见模式：✅ 已写入 xxx.py 或 文件路径: xxx）
    文件名 = None
    行数信息 = ""

    # 匹配文件名：常见模式包括路径中的文件名
    文件名匹配 = _re.search(r'[`\'"\s]([\w\-/\\.\u4e00-\u9fff]+\.[a-zA-Z]{1,5})', content)
    if 文件名匹配:
        文件名 = 文件名匹配.group(1).split('/')[-1].split('\\')[-1]

    # 匹配行数信息：“新增 N 行”、“N lines”、“共 N 行”
    行数匹配 = _re.search(r'(\d+)\s*[行 lines]', content)
    if 行数匹配:
        行数信息 = f" ({行数匹配.group(0).strip()})"

    # 确定操作类型
    if tool_name == "edit_file":
        操作描述 = "已编辑"
    elif tool_name == "write_plugin_file":
        操作描述 = "已写入"
    elif tool_name == "update_readme":
        操作描述 = "已更新 README"
    elif tool_name == "rename_plugin_file":
        操作描述 = "已重命名"
    elif tool_name == "check_python_syntax":
        操作描述 = "语法检查"
    elif tool_name == "execute_command":
        操作描述 = "已执行"
    else:
        操作描述 = "已完成"

    # 如果提取到文件名，返回增强摘要；否则原样返回
    if 文件名:
        # 检查原内容是否已经包含足够信息（比如已经有文件名）
        if 文件名 in content and ("✅" in content or "✔" in content):
            return content  # 原内容已经足够信息丰富
        状态 = "✅" if "✅" in content or "成功" in content else "ℹ️"
        return f"{状态} {操作描述} {文件名}{行数信息}\n{content}"

    # 未提取到文件名，原样返回
    return content


def 构建轮次上限提示(最大轮次: int, 已执行工具统计: dict = None, 已修改文件=None) -> str:
    """构建工具调用轮次达到上限时的统一用户提示（四条路径共用）。

    API/本地 × 流式/非流式四处上限出口均引用本函数，避免文案不一致。
    附带当前进度摘要（已执行工具统计、已修改文件列表）与续接引导，
    让用户直接回复「继续」即可接续剩余工作，且模型续聊时能从会话历史
    中看到进度摘要，减少重复探索。

    :param 最大轮次: 本次生效的工具调用轮次上限
    :param 已执行工具统计: {工具名: 成功执行次数}，可为空
    :param 已修改文件: 已成功写入/编辑的文件路径集合，可为空
    :return: 以空行开头的多行提示文本（直接追加到已有正文后即可）
    """
    行 = [f"[提示] 工具调用已达最大轮次限制（{最大轮次} 轮），本次自动执行已暂停，任务可能未完全完成。"]
    if 已执行工具统计:
        统计文本 = "、".join(
            f"{名} ×{次}" for 名, 次 in sorted(已执行工具统计.items(), key=lambda x: -x[1])
        )
        行.append(f"已执行工具：{统计文本}")
    if 已修改文件:
        文件列表 = sorted(已修改文件)
        显示 = "、".join(文件列表[:10])
        if len(文件列表) > 10:
            显示 += f" 等共 {len(文件列表)} 个文件"
        行.append(f"已修改文件（磁盘改动已生效）：{显示}")
    行.append(
        "您可以直接回复「继续」，我会在上述进度基础上接着完成剩余工作；"
        "也可以在设置→高级设置中调高「工具调用最大轮次」后重试。"
    )
    return "\n\n" + "\n\n".join(行)
