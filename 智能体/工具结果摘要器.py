"""工具结果智能摘要模块

替代对 tool 消息的 2000 字符硬截断，根据工具类型和内容特征智能生成摘要。
支持 Python/JS/JSON/Markdown 等文件类型的结构化摘要，以及通用内容的智能截断。
"""

import json
import logging

logger = logging.getLogger("NodeCraftAI.工具结果摘要器")


def 摘要化工具结果(tool_name: str, raw_content: str, max_chars: int = 1500) -> str:
    """根据工具类型和内容特征，智能生成摘要。

    如果内容长度不超过 max_chars，直接返回原文。
    超过时按工具类型选择不同的摘要策略。
    """
    if not raw_content or len(raw_content) <= max_chars:
        return raw_content

    try:
        if tool_name == "list_plugin_files":
            return _摘要_文件列表(raw_content, max_chars)
        elif tool_name == "read_plugin_file":
            return _摘要_文件内容(raw_content, max_chars)
        elif tool_name == "write_plugin_file":
            return _摘要_写入结果(raw_content, max_chars)
        else:
            return _通用截断(raw_content, max_chars)
    except (ValueError, TypeError, OSError) as e:
        logger.warning("摘要化失败，回退到通用截断: %s", e)
        return _通用截断(raw_content, max_chars)


# ---------------------------------------------------------------------------
# 各工具策略
# ---------------------------------------------------------------------------

def _摘要_文件列表(content: str, max_chars: int) -> str:
    """目录结构通常不大，直接返回。极端超长时保留前 max_chars 字符。"""
    if len(content) <= max_chars:
        return content
    return content[:max_chars] + "\n...[文件列表已截断]"


def _摘要_文件内容(content: str, max_chars: int) -> str:
    """代码文件摘要：按文件类型提取关键结构。"""
    # 处理可能的二进制乱码
    if _疑似二进制(content):
        return "[文件摘要] 内容疑似包含二进制数据，无法生成摘要"

    lines = content.split("\n")
    total_lines = len(lines)
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

    # 组装摘要
    摘要 = (
        f"[文件摘要] 共 {total_lines} 行\n"
        f"--- 关键结构 ---\n"
        f"{结构文本}\n"
        f"--- 省略详细实现 ({total_lines}行) ---"
    )

    # 如果摘要本身还是太长，进行二次截断
    if len(摘要) > max_chars:
        摘要 = 摘要[:max_chars] + "\n...[摘要已截断]"

    return 摘要


def _摘要_写入结果(content: str, max_chars: int) -> str:
    """write_plugin_file 的返回值通常已经很短，直接返回。"""
    if len(content) <= max_chars:
        return content
    return _通用截断(content, max_chars)


def _通用截断(content: str, max_chars: int) -> str:
    """保留前 70% + 末尾 20% 的字符预算，中间插入省略标记。"""
    前部长度 = int(max_chars * 0.7)
    后部长度 = int(max_chars * 0.2)
    省略长度 = len(content) - 前部长度 - 后部长度

    if 省略长度 <= 0:
        return content

    前部 = content[:前部长度]
    后部 = content[-后部长度:] if 后部长度 > 0 else ""
    return f"{前部}\n...[已省略 {省略长度} 字符]...\n{后部}"


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
