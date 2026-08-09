import asyncio
import re
import sys
from pathlib import Path

_plugin_root = str(Path(__file__).parent.parent.resolve())
if _plugin_root not in sys.path:
    sys.path.insert(0, _plugin_root)
from 后端.文件读写操作 import (
    _备份文件 as _备份文件,
)
from 后端.文件读写操作 import (
    apply_patch as _apply_patch,
)
from 后端.文件读写操作 import (
    check_python_syntax as _check_python_syntax,
)
from 后端.文件读写操作 import (
    ruff_quick_lint as _ruff_quick_lint,
)
from 后端.文件读写操作 import (
    delete_plugin_file as _delete_plugin_file,
)
from 后端.文件读写操作 import (
    execute_command as _execute_command,
)
from 后端.文件读写操作 import (
    find_plugin_files as _find_plugin_files,
)
from 后端.文件读写操作 import (
    grep_plugin_files as _grep_plugin_files,
)
from 后端.文件读写操作 import (
    read_plugin_file as _read_plugin_file,
)
from 后端.文件读写操作 import (
    rename_plugin_file as _rename_plugin_file,
)
from 后端.文件读写操作 import (
    scan_plugin_file_tree as _scan_plugin_file_tree,
)
from 后端.文件读写操作 import (
    search_plugin_file as _search_plugin_file,
)
from 后端.文件读写操作 import (
    update_readme_changelog as _update_readme_changelog,
)
from 后端.文件读写操作 import (
    write_plugin_file as _write_plugin_file,
)
from 后端.日志配置 import 获取日志器
from 智能体.工具结果摘要器 import MAX_FILE_READ_RESULT_CHARS, MAX_TOOL_RESULT_CHARS
from 智能体.知识库检索 import (  # noqa: F401  re-export 兼容
    BM25Index,
    COMFYUI_TERMS,
    KB_FAILURE_MESSAGES,
    SYNONYMS,
    TAB_FOLDER_MAP,
    _cached_bm25_search,
    _cached_tfidf_search,
    _generate_index_signature,
    _get_bm25_index,
    _get_tfidf_index,
    _merge_bm25_tfidf,
    _rerank_results,
    _split_markdown_sections,
    _加载本地知识库Markdown,
    _加载知识库文档,
    _扩展查询,
    _提取重排关键词,
    _计算知识库版本签名,
    invalidate_bm25_cache,
    invalidate_faq_index,
    kb_failure_message,
    retrieve_pitfalls_proactive,
)

logger = 获取日志器("工具路由器")


def _补丁变更摘要(patch: str) -> str:
    """生成补丁变更统计摘要（自动区分 SEARCH/REPLACE 与 unified diff 格式）"""
    if '<<<<<<< SEARCH' in patch:
        blocks = patch.count('<<<<<<< SEARCH')
        return f"{blocks} 个替换块"
    added = sum(1 for line in patch.splitlines()
                if line.startswith('+') and not line.startswith('+++'))
    removed = sum(1 for line in patch.splitlines()
                  if line.startswith('-') and not line.startswith('---'))
    return f"新增 {added} 行，删除 {removed} 行"


# 检索意图门控：短消息且不含技术词时跳过知识库检索（闲聊/寡言不注入参考文档）
_KB_TECH_HINT = re.compile(
    r'[a-zA-Z_]{3,}'
    r'|节点|插件|代码|报错|错误|异常|优化|文件|函数|接口|前端|后端'
    r'|工作流|模型|参数|安装|配置|调试|修改|实现|开发|分析|可视化|模板'
)


# ─── 文件操作工具定义（用于优化插件模式） ───────────────
FILE_TOOLS = [
    {
        "name": "read_plugin_file",
        "description": "读取当前插件中指定文件的内容。支持 start_line/end_line 分段读取大文件。",
        "parameters": {
            "type": "object",
            "properties": {
                "file_path": {"type": "string", "description": "相对于插件根目录的文件路径"},
                "start_line": {"type": "integer", "description": "起始行号（1-based，可选，默认从头开始）"},
                "end_line": {"type": "integer", "description": "结束行号（1-based，包含该行，可选，默认到文件末尾）"}
            },
            "required": ["file_path"]
        }
    },
    {
        "name": "write_plugin_file",
        "description": "全量写入当前插件中指定文件的内容（覆盖整个文件，适用于新建文件或大幅重写）",
        "parameters": {
            "type": "object",
            "properties": {
                "file_path": {"type": "string", "description": "相对于插件根目录的文件路径"},
                "content": {"type": "string", "description": "要写入的完整文件内容"}
            },
            "required": ["file_path", "content"]
        }
    },
    {
        "name": "edit_file",
        "description": (
            "增量编辑文件。仅修改需要变更的部分，无需输出完整文件内容，适用于局部修改。\n"
            "补丁使用 SEARCH/REPLACE 块格式：<<<<<<< SEARCH 与 ======= 之间是文件中"
            "已存在的原文（需与文件内容逐行一致），======= 与 >>>>>>> REPLACE 之间是替换后的新内容；"
            "一次补丁可包含多个块，按顺序应用。格式示例见系统提示中的「文件修改策略」。"
            "也兼容 unified diff 格式输入。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "file_path": {
                    "type": "string",
                    "description": "要编辑的文件路径（相对于插件目录）"
                },
                "patch": {
                    "type": "string",
                    "description": "SEARCH/REPLACE 块格式的补丁内容（兼容 unified diff）"
                }
            },
            "required": ["file_path", "patch"]
        }
    },
    {
        "name": "search_plugin_file",
        "description": (
            "在文件中搜索关键词或正则表达式，返回匹配行的行号和上下文。"
            "用于快速定位代码段，配合 read_plugin_file 的分段读取使用。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "file_path": {"type": "string", "description": "相对于插件根目录的文件路径"},
                "pattern": {"type": "string", "description": "搜索关键词或正则表达式"},
                "use_regex": {
                    "type": "boolean",
                    "description": "是否使用正则表达式模式（默认false，即普通关键词搜索）"
                },
                "context_lines": {"type": "integer", "description": "每个匹配前后显示的上下文行数（默认3，最大10）"}
            },
            "required": ["file_path", "pattern"]
        }
    },
    {
        "name": "list_plugin_files",
        "description": "列出当前插件的所有文件和目录结构",
        "parameters": {
            "type": "object",
            "properties": {}
        }
    },
    {
        "name": "update_readme",
        "description": (
            '在插件的 README.md 的“更新记录”模块追加一条更新记录（自动兼容旧版“更新介绍”/英文“Changelog”模块）。'
            '仅当本次任务实际修改了插件文件时才调用；纯阅读/分析/问答任务不要调用。'
            '更新说明必须是一句简洁易懂的大白话，禁止使用专业术语。'
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "changelog_entry": {
                    "type": "string",
                    "description": "一条简洁的更新说明，例如：'新增了图片风格转换功能'、'修复了文字处理乱码的问题'"
                }
            },
            "required": ["changelog_entry"]
        }
    },
    {
        "name": "batch_edit",
        "description": (
            "批量编辑多个文件，适用于一次操作多个文件（如创建完整插件项目结构），单次最多 20 个文件操作。\n"
            "操作类型：create=创建新文件（已存在则跳过）、write=全量写入、"
            "edit=增量补丁（content 为 SEARCH/REPLACE 块，格式同 edit_file）、delete=删除（自动备份 .bak）。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "operations": {
                    "type": "array",
                    "description": "文件操作列表",
                    "items": {
                        "type": "object",
                        "properties": {
                            "action": {
                                "type": "string",
                                "enum": ["create", "write", "edit", "delete"],
                                "description": (
                                    "操作类型：create=创建新文件, write=全量写入, "
                                    "edit=增量补丁, delete=删除文件"
                                )
                            },
                            "file_path": {
                                "type": "string",
                                "description": "文件路径（相对于插件目录）"
                            },
                            "content": {
                                "type": "string",
                                "description": "文件内容（create/write 时必填）或 unified diff 补丁（edit 时必填）"
                            }
                        },
                        "required": ["action", "file_path"]
                    }
                }
            },
            "required": ["operations"]
        }
    },
    {
        "name": "grep_plugin_files",
        "description": (
            "跨文件搜索关键词或正则表达式。一次性搜索插件目录下所有匹配的文件，"
            "返回每个匹配的行号和上下文。支持 glob 过滤文件类型（如'*.py'仅搜索 Python 文件）。"
            "适用于快速定位某个函数/类/变量在所有文件中的引用位置。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "pattern": {"type": "string", "description": "搜索关键词或正则表达式"},
                "glob": {"type": "string", "description": "文件过滤模式（可选），如 '*.py'、'*.{js,ts}'、'**/*.md'"},
                "use_regex": {
                    "type": "boolean",
                    "description": "是否使用正则表达式（默认 false = 不区分大小写关键词搜索）"
                },
                "context_lines": {"type": "integer", "description": "每个匹配前后显示的上下文行数（默认 2，最大 5）"}
            },
            "required": ["pattern"]
        }
    },
    {
        "name": "find_plugin_files",
        "description": (
            "按 glob 模式查找文件并列出。如 '*.py' 找所有 Python 文件，"
            "'**/test_*.py' 找所有测试文件。返回文件路径、类型、大小。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "glob_pattern": {
                    "type": "string",
                    "description": "文件 glob 模式，如 '*.py'、'**/*.js'、'**/node_*.py'"
                }
            },
            "required": ["glob_pattern"]
        }
    },
    {
        "name": "rename_plugin_file",
        "description": "重命名或移动文件。会自动创建目标目录、备份原文件。不允许覆盖已有文件。",
        "parameters": {
            "type": "object",
            "properties": {
                "source_path": {"type": "string", "description": "源文件相对路径"},
                "dest_path": {"type": "string", "description": "目标文件相对路径"}
            },
            "required": ["source_path", "dest_path"]
        }
    },
    {
        "name": "delete_plugin_file",
        "description": (
            "删除插件目录内的指定文件。删除前自动备份为 .bak（可手动恢复）。"
            "仅允许删除插件目录内的文件，不允许删除目录。"
            "需要删除文件时请使用此工具，不要通过 execute_command 执行删除命令。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "file_path": {"type": "string", "description": "要删除的文件相对路径"}
            },
            "required": ["file_path"]
        }
    },
    {
        "name": "check_python_syntax",
        "description": (
            "检查 Python 文件的语法是否正确（使用 py_compile 编译检查，不真正执行代码）。"
            "适用于写入文件后验证代码能否正常导入。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "file_path": {"type": "string", "description": "相对于插件根目录的 Python 文件路径"}
            },
            "required": ["file_path"]
        }
    },
    {
        "name": "execute_command",
        "description": (
            "在安全的受限环境中执行 shell 命令。支持的命令：pip install（安装依赖）、"
            "git status/diff/log（查看版本状态）、python -c（快速验证代码）。"
            "自动拒绝 rm/del/curl/管道/重定向等危险操作。pip install 自动追加 --quiet --no-input。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "command": {
                    "type": "string",
                    "description": (
                        "要执行的命令，如 'pip install numpy'、'git status'、"
                        "'python -c \"import my_module\"'"
                    )
                },
                "timeout": {"type": "integer", "description": "超时秒数（默认 60，最大 120）"}
            },
            "required": ["command"]
        }
    },
    {
        "name": "update_plan",
        "description": (
            "维护当前任务的执行计划清单（仅内存态，不写入任何文件）。"
            "涉及 3 步以上的任务开始前先列出全部步骤，每完成一步立即再次调用更新状态（覆盖式：每次传入完整清单）。"
            "简单任务（1-2 步）无需调用。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "steps": {
                    "type": "array",
                    "description": "任务步骤完整列表（最多 20 步）",
                    "items": {
                        "type": "object",
                        "properties": {
                            "step": {"type": "string", "description": "步骤描述（一句话）"},
                            "status": {
                                "type": "string",
                                "enum": ["pending", "in_progress", "completed"],
                                "description": "步骤状态：pending=待做, in_progress=进行中, completed=已完成"
                            }
                        },
                        "required": ["step", "status"]
                    }
                }
            },
            "required": ["steps"]
        }
    },
    {
        "name": "analyze_profiling",
        "description": (
            "解析 cProfile 的 print_stats() 输出文本，按累计耗时排序返回 Top10 热点函数表格。"
            "适用于用户已经粘贴了 profile 结果、需要定位性能瓶颈的场景。"
            "仅识别 cProfile 表格格式（需含 ncalls/tottime/cumtime 表头），"
            "py-spy 与 torch.profiler 的输出格式不受支持。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "profiling_data": {
                    "type": "string",
                    "description": (
                        "cProfile print_stats() 的原始输出文本（需包含 ncalls 表头行，"
                        "最大 100000 字符）"
                    )
                }
            },
            "required": ["profiling_data"]
        }
    },
    {
        "name": "ask_user",
        "description": (
            "向用户提出一个必须由用户本人回答的问题。调用后必须立即结束本回合等待用户回复，"
            "严禁自行假设答案或继续调用其他工具。仅在需求不明确、存在多种方向且必须由用户拍板时使用；"
            "可自行解决的技术问题不要使用。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "question": {
                    "type": "string",
                    "description": "要问用户的问题（简洁明确，一句话）"
                },
                "options": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "可选的候选选项列表，供用户快速选择（2-4 个，可不提供）"
                }
            },
            "required": ["question"]
        }
    }
]


# 可视化 Tab 只读工具子集（分析/解读场景不需要写操作，裁剪 schema 减少每轮 payload）
_READONLY_TOOL_NAMES = {
    "read_plugin_file", "search_plugin_file", "list_plugin_files",
    "grep_plugin_files", "find_plugin_files", "check_python_syntax",
    "analyze_profiling", "ask_user",
}


# ─── 工具路由器 ─────────────────────────────────────────────

class ToolRouter:
    """知识库检索与意图路由"""

    def __init__(self):
        self.文件工具 = FILE_TOOLS
        # 云端知识库检索失败信号（每次 retrieve_knowledge 调用会重置）
        self.kb_retrieval_failed = False
        self.kb_failure_reason = ""

    def retrieve_knowledge(self, user_message, active_tab="develop", top_k=3):
        """
        检索相关知识库文档与踩坑记录（纯本地 BM25 + TF-IDF 混合检索）

        流程：
          1. 同义词扩展查询
          2. 本地 BM25 + TF-IDF 混合检索（知识库 Markdown + FAQ 统一索引）
          3. 合并重排后按 kb:// / faq:// 前缀分离
          4. 格式化返回（## 参考文档 + ## 踩坑记录参考）

        Args:
            top_k: 知识库文档返回条数（默认 3）；
                   代码审查按深度映射 quick=1/standard=3/deep=5

        Returns:
            str: 检索到的相关文档段落；无相关内容时返回空字符串
        """
        if not user_message or not user_message.strip():
            return ""

        # 本地检索不涉及云端，失败信号恒为 False
        self.kb_retrieval_failed = False
        self.kb_failure_reason = ""

        原始查询 = user_message.strip()

        # 意图门控：短消息（<15 字）且无技术词时跳过检索，避免闲聊也注入大段参考文档
        if len(原始查询) < 15 and not _KB_TECH_HINT.search(原始查询):
            return ""

        扩展查询 = _扩展查询(原始查询)

        # 本地 BM25 + TF-IDF 混合检索（统一索引已包含 kb:// 与 faq://）
        bm25_results = _cached_bm25_search(扩展查询, tab=active_tab, top_k=5, min_score=1.0)
        tfidf_results = _cached_tfidf_search(扩展查询, tab=active_tab, top_k=5)
        merged = _merge_bm25_tfidf(bm25_results, tfidf_results)
        reranked = _rerank_results(原始查询, merged)

        # 按前缀分离知识库文档与踩坑记录
        kb_结果 = [(doc_id, title, score, snippet)
                  for doc_id, title, score, snippet in reranked
                  if isinstance(doc_id, str) and doc_id.startswith("kb://")][:top_k]
        faq_结果 = [(doc_id, title, score, snippet)
                   for doc_id, title, score, snippet in reranked
                   if isinstance(doc_id, str) and doc_id.startswith("faq://")][:2]

        # 格式化合并
        parts = []
        for doc_id, title, score, snippet in kb_结果:
            parts.append(
                f"## 参考文档: {title}\n"
                f"(相关度: {score:.2f})\n\n"
                f"{snippet}"
            )
        if faq_结果:
            faq_块 = ["## 踩坑记录参考\n以下是历史踩坑记录，请参考避免相同问题："]
            for doc_id, title, score, snippet in faq_结果:
                faq_块.append(
                    f"### 踩坑记录: {title}\n"
                    f"(相关度: {score:.2f})\n\n"
                    f"{snippet}"
                )
            parts.append("\n\n".join(faq_块))

        return "\n\n---\n\n".join(parts) if parts else ""

    def get_file_tools(self, active_tab="develop"):
        """获取文件操作工具定义（按 Tab 裁剪：visualize 仅下发只读工具）"""
        if active_tab == "visualize":
            return [t for t in self.文件工具 if t["name"] in _READONLY_TOOL_NAMES]
        return self.文件工具

    def _预热知识库索引(self):
        """启动时预热 BM25/TFIDF 索引，避免首次查询延迟"""
        try:
            logger.debug("[工具路由器] 开始预热知识库索引...")
            # 触发一次轻量查询以初始化索引
            self.retrieve_knowledge("预热测试", active_tab="develop")
            logger.info("[工具路由器] 知识库索引预热完成")
        except Exception as e:
            logger.debug(f"[工具路由器] 索引预热失败（非致命）: {e}")


# ─── 工具执行逻辑（对接 后端/文件读写操作.py） ─────────────

def _格式化文件树(tree: list, indent: int = 0) -> str:
    """将文件树列表格式化为缩进文本"""
    lines = []
    for item in tree:
        prefix = "  " * indent
        item_type = item.get("type")
        # 兼容 "directory" 与 "dir" 两种类型标识
        if item_type in ("directory", "dir"):
            lines.append(f"{prefix}📁 {item.get('name', '')}/")
            children = item.get("children")
            if children:
                lines.append(_格式化文件树(children, indent + 1))
        else:
            lines.append(f"{prefix}📄 {item.get('name', '')}")
    return "\n".join(lines)


# 工具执行结果最大字符数（超出则使用智能摘要器压缩，避免上下文溢出）
# 阈值单一来源为 工具结果摘要器.MAX_TOOL_RESULT_CHARS，API模型客户端与此对齐且对已截断内容短路，
# 不再对同一内容重复压缩（仅跨轮次历史消息保留 800 字符瘦身）
_MAX_TOOL_RESULT_CHARS = MAX_TOOL_RESULT_CHARS

# 截断标记与行动指引的附加长度容差（不计入 4000 阈值，避免把指引本身截掉）
_截断附加容差 = 200


def _截断工具结果(result: str, tool_name: str = "unknown") -> str:
    """若结果超过阈值字符数则使用智能摘要器压缩。

    文件读取类工具使用放宽阈值 MAX_FILE_READ_RESULT_CHARS（读取内容是模型工作的
    直接素材，压得过狠会迫使模型分页反复重读），其余工具沿用 _MAX_TOOL_RESULT_CHARS。
    优先使用 摘要化工具结果 按实际工具名选择对应摘要策略；
    如果摘要后仍超限，做二次硬截断作为兜底保护。
    """
    # 延迟导入避免循环依赖
    from 智能体.工具结果摘要器 import _文件读取类工具, 分段读取指引, 摘要化工具结果
    阈值 = MAX_FILE_READ_RESULT_CHARS if tool_name in _文件读取类工具 else _MAX_TOOL_RESULT_CHARS
    if len(result) <= 阈值:
        return result
    total = len(result)
    try:
        # 使用智能摘要器压缩（透传实际工具名，触发对应工具的摘要策略）
        summarized = 摘要化工具结果(tool_name, result, max_chars=阈值)
        # 摘要器会额外追加截断标记/行动指引，允许小幅超出阈值
        if len(summarized) <= 阈值 + _截断附加容差:
            return summarized
        # 兜底：二次硬截断
        truncated = summarized[:阈值] + f"\n...[结果已截断，原始共 {total} 字符]"
    except Exception:
        # 摘要器异常时回退到硬截断
        truncated = result[:阈值] + f"\n...[结果已截断，共 {total} 字符]"
    # 文件读取类工具兜底截断后补回行动指引（指引可能已被截掉）
    if tool_name in _文件读取类工具 and 分段读取指引 not in truncated:
        truncated += "\n" + 分段读取指引
    return truncated


# ─── 工具执行超时配置 ───────────────────────────────────────
# 不同类型工具的超时秒数（防止工具执行卡死阻塞整个对话流程）
_工具超时配置: dict[str, float] = {
    # 文件读写类工具：可能涉及大文件，给予更长超时
    "read_plugin_file": 15.0,
    "write_plugin_file": 15.0,
    "edit_file": 15.0,
    "batch_edit": 30.0,
    "rename_plugin_file": 15.0,
    "delete_plugin_file": 15.0,
    # 搜索与检索类工具
    "search_plugin_file": 10.0,
    "grep_plugin_files": 10.0,
    "find_plugin_files": 10.0,
    "list_plugin_files": 10.0,
    # 其他工具
    "update_readme": 10.0,
    "check_python_syntax": 10.0,
    "update_plan": 5.0,  # 纯内存操作，无 I/O
    "execute_command": 120.0,  # execute_command 自带 timeout 参数，此处设为宽松上限
}
_默认超时秒数: float = 10.0


def _获取工具超时(tool_name: str) -> float:
    """获取指定工具的超时秒数，未配置的工具使用默认值"""
    return _工具超时配置.get(tool_name, _默认超时秒数)


# update_plan 步骤状态图标（completed/in_progress/pending）
_计划状态图标 = {"completed": "☑", "in_progress": "▶", "pending": "☐"}


def _格式化任务计划(steps) -> str:
    """校验并格式化 update_plan 的步骤清单为带状态图标的文本。

    覆盖式语义：每次调用传入完整清单，直接格式化回传，不做增量合并。
    非法输入返回 ❌ 错误说明（走统一的错误恢复流程引导模型自纠）。
    """
    if not isinstance(steps, list) or not steps:
        return "❌ 错误：steps 必须是非空数组，每项形如 {\"step\": \"步骤描述\", \"status\": \"pending\"}"
    if len(steps) > 20:
        return f"❌ 错误：步骤过多（{len(steps)} 步，上限 20），请合并为更粗粒度的步骤"
    行列表 = []
    完成数 = 0
    for i, item in enumerate(steps):
        if not isinstance(item, dict) or not str(item.get("step", "")).strip():
            return f"❌ 错误：第 {i + 1} 项缺少 step 描述，每项形如 {{\"step\": \"步骤描述\", \"status\": \"pending\"}}"
        status = str(item.get("status", "pending"))
        if status not in _计划状态图标:
            return f"❌ 错误：第 {i + 1} 项 status 非法（{status}），仅支持 pending/in_progress/completed"
        if status == "completed":
            完成数 += 1
        描述 = str(item["step"]).strip()[:100]
        行列表.append(f"{_计划状态图标[status]} {i + 1}. {描述}")
    结果 = [f"📋 任务计划已更新（{完成数}/{len(steps)} 已完成）："] + 行列表
    if 完成数 < len(steps):
        结果.append("[系统提示] 每完成一步请再次调用 update_plan 更新状态（传入完整清单）。")
    else:
        结果.append("[系统提示] 全部步骤已完成，无需再调用 update_plan。")
    return "\n".join(结果)


# 文件写操作锁（防止并发写同一文件）
_文件写锁_dict: dict[str, asyncio.Lock] = {}

def _获取文件写锁(file_path: str) -> asyncio.Lock:
    """按文件名获取独立的写锁，避免并发写同一文件"""
    if file_path not in _文件写锁_dict:
        _文件写锁_dict[file_path] = asyncio.Lock()
    return _文件写锁_dict[file_path]


def _解析安全路径(plugin_path: str, relative_path: str) -> Path:
    """解析相对路径为绝对路径并进行安全校验

    确保解析后的路径仍在插件目录范围内，防止路径穿越攻击。

    Args:
        plugin_path: 插件根目录的完整路径
        relative_path: 相对于插件根目录的文件路径

    Returns:
        Path: 解析后的安全绝对路径

    Raises:
        ValueError: 路径超出插件目录范围
    """
    plugin_root = Path(plugin_path).resolve()
    full_path = (plugin_root / relative_path).resolve()
    try:
        full_path.relative_to(plugin_root)
    except ValueError:
        raise ValueError(f"安全错误：文件路径 '{relative_path}' 超出插件目录范围")
    return full_path


def _目录合规警告(file_paths: list, plugin_path: str) -> str:
    """写入落点的目录合规软校验（只追加警告，不阻断写入）

    检测两类常见跑偏：
    1. 节点代码写成根目录 nodes.py（应放入 节点/ 或 nodes/）
    2. 前端 JS 未放入 网页资源/ 或 web/（含与项目既有目录语言不匹配的情况）
    让模型在写入当轮即收到提示并自行迁移，不破坏已写入内容。
    """
    try:
        root = Path(plugin_path)
        has_zh_node = (root / "节点").is_dir()
        has_zh_web = (root / "网页资源").is_dir()
        has_en_web = (root / "web").is_dir()
        warnings = []
        for fp in file_paths:
            norm = str(fp).replace("\\", "/")
            while norm.startswith("./"):
                norm = norm[2:]
            # 根目录 nodes.py 在任何语言规范下都不合规；
            # nodes/ 路径仅在项目已有中文 `节点/` 目录时才视为跑偏（英文项目 nodes/ 是合规目录）
            if norm == "nodes.py" or (norm.startswith("nodes/") and has_zh_node and not (root / "nodes").is_dir()):
                warnings.append(
                    f"⚠️ [目录合规] `{fp}` 落在插件根目录/nodes 路径：节点 Python 文件应放入 "
                    "`节点/` 目录（英文项目为 `nodes/`），并在根 __init__.py 中 `from .节点.XX import ...` 注册。"
                    "请后续将其迁移到正确目录，勿在根目录继续扩展节点代码。"
                )
            elif norm.endswith(".js") and not norm.startswith(("网页资源/", "web/")):
                if has_zh_web:
                    warnings.append(
                        f"⚠️ [目录合规] `{fp}` 未放入前端目录：本项目前端 JS 应放在 `网页资源/` 下"
                        "（WEB_DIRECTORY 指向该目录），请后续迁移。"
                    )
                elif has_en_web:
                    warnings.append(
                        f"⚠️ [目录合规] `{fp}` 未放入前端目录：本项目前端 JS 应放在 `web/` 下"
                        "（WEB_DIRECTORY 指向该目录），请后续迁移。"
                    )
        return "\n".join(warnings)
    except Exception:
        return ""


async def _附加语法检查(result: str, py_files: list, plugin_path: str) -> str:
    """写入成功后对 .py 文件自动附加语法检查结果

    让模型在同一轮即可感知语法错误并立即修复，
    省去额外一轮 check_python_syntax 调用。
    """
    loop = asyncio.get_running_loop()
    checks = []
    for fp in py_files[:5]:
        try:
            ok, msg = await loop.run_in_executor(None, _check_python_syntax, plugin_path, fp)
            checks.append(f"{'✅' if ok else '❌'} [自动语法检查] {fp}: {msg}")
        except Exception as e:
            checks.append(f"⚠️ [自动语法检查] {fp}: 检查异常 {type(e).__name__}: {e}")
    # 语法均通过时追加 ruff 快速 lint（捕获未定义名/未用导入等 py_compile 报不出的错）
    # 语法本身就错时 ruff 信息冗余，且 ruff 未安装/超时时静默跳过
    if checks and all(c.startswith("✅") for c in checks):
        try:
            available, report = await loop.run_in_executor(
                None, _ruff_quick_lint, plugin_path, py_files[:5], 10
            )
            if available and report:
                checks.append(report)
        except Exception as e:
            logger.warning(f"ruff 写后 lint 异常，已忽略: {type(e).__name__}: {e}")
    if checks:
        return result + "\n" + "\n".join(checks)
    return result


# Profiling 文本上限（与后端 /ai-coder/benchmark/analyze 的限制保持一致）
_PROFILING最大字符数 = 100000


async def _执行Profiling分析(tool_args: dict) -> str:
    """解析用户粘贴的 cProfile 输出，格式化为热点表格交由模型解读。

    纯文本解析、无文件 IO，不依赖 plugin_path，因此不走 _执行工具内部 的线程池分发。
    复用 后端/代码审查路由.py 的 _分析性能数据，与 HTTP 接口共用同一套解析口径。
    """
    原始文本 = str(tool_args.get("profiling_data") or "").strip()
    if not 原始文本:
        return "❌ 错误：缺少必需参数 profiling_data，请提供 cProfile 的 print_stats() 输出文本"
    if len(原始文本) > _PROFILING最大字符数:
        return (
            f"❌ 错误：数据过大（{len(原始文本)} 字符，上限 {_PROFILING最大字符数}），"
            f"请只粘贴 ncalls 表头之后的前几十行"
        )

    # 延迟导入：后端.代码审查路由 → 路由公共 → 智能体.工具路由器，顶层导入会形成循环。
    # 路由公共 在模块顶层读 PromptServer.instance.app，ComfyUI 运行时已入 sys.modules 缓存故不重执；
    # 非 ComfyUI 环境（单测/脚本）下会抛 ImportError 或 AttributeError，统一兜住不让它中断对话。
    try:
        from 后端.代码审查路由 import _分析性能数据
    except Exception as e:
        logger.error(f"Profiling 解析模块导入失败: {type(e).__name__}: {e}")
        return f"❌ 工具不可用：Profiling 解析模块导入失败（{type(e).__name__}: {e}）"

    # 此处刻意不做超时包装：_分析性能数据 虽是 async def，但内部无任何 await（纯同步文本解析），
    # 协程没有让出点，asyncio.wait_for 无法中断它 —— 加了也永不触发，只会留下误导性的死代码。
    # 实测 98KB（贴近 _PROFILING最大字符数 上限）解析仅约 2ms，对事件循环的占用可忽略，无需下放线程池。
    try:
        热点 = await _分析性能数据(原始文本)
    except Exception as e:
        logger.error(f"Profiling 分析异常: {type(e).__name__}: {e}")
        return f"❌ 工具执行异常：{type(e).__name__}: {str(e)}"

    热点函数 = 热点.get("top_functions") or []
    if not 热点函数:
        return (
            "⚠️ 未能解析出任何数据行。本工具只识别 cProfile `print_stats()` 的表格格式（需包含 "
            "`ncalls tottime percall cumtime percall filename:lineno(function)` 表头）。\n"
            "py-spy 与 torch.profiler 的输出格式不同，请直接阅读原始文本分析，不要重复调用本工具。"
        )

    行列表 = [
        f"## Profiling 热点（按累计耗时排序，共 {len(热点函数)} 项）",
        "",
        "| # | 累计耗时(s) | 调用次数 | 单次(s) | 函数 |",
        "|---|---|---|---|---|",
    ]
    for i, 项 in enumerate(热点函数, 1):
        行列表.append(
            f"| {i} | {项['cumtime']:.3f} | {项['calls']} | {项['per_call']:.4f} | `{项['name']}` |"
        )
    if 热点.get("summary"):
        行列表 += ["", f"**{热点['summary']}**"]
    行列表 += [
        "",
        "[系统提示] 表格仅反映耗时排序，不代表该函数就能优化。请结合插件源码判断成因"
        "（重复计算 / IO 阻塞 / GPU 同步等待 / 首次加载开销），再给出优化建议。",
    ]
    return _截断工具结果("\n".join(行列表), "analyze_profiling")


async def 执行工具(tool_name: str, tool_args: dict, plugin_path: str) -> str:
    """
    根据工具名和参数执行对应的文件操作。
    写操作加锁保护，防止并行工具调用时并发写同一文件。
    所有工具调用均有全局超时保护，防止单个工具卡死阻塞整个对话。

    Args:
        tool_name: 工具名称 (read_plugin_file / write_plugin_file / list_plugin_files)
        tool_args: 工具参数字典
        plugin_path: 插件的绝对路径

    Returns:
        执行结果的文本描述
    """
    # 语义化日志：只显示操作摘要，不输出具体内容
    from 智能体.模型客户端工具 import _tool_log_summary
    logger.info(f"执行工具: {tool_name} {_tool_log_summary(tool_name, tool_args)}")

    # Profiling 分析不读写插件文件，放在 plugin_path 校验之前
    if tool_name == "analyze_profiling":
        return await _执行Profiling分析(tool_args or {})

    # ask_user 正常由聊天路由的 tool_executor 闭包拦截（推送事件并结束流），
    # 这里是兜底分支：防止其他调用路径（如 WebSocket）直达时崩溃
    if tool_name == "ask_user":
        return (
            "[ask_user] 问题已提交给用户，正在等待用户回复。"
            "请立即结束本回合，不要再调用任何工具，也不要自行假设答案。"
        )

    if not plugin_path:
        return "❌ 错误：未指定插件路径（plugin_path 为空），无法执行文件操作。"

    tool_args = tool_args or {}
    超时秒数 = _获取工具超时(tool_name)

    # 写操作加锁保护（write_plugin_file、edit_file、update_readme、rename_plugin_file、delete_plugin_file 均为写操作）
    if tool_name in ("write_plugin_file", "edit_file", "update_readme", "rename_plugin_file", "delete_plugin_file"):
        file_path = tool_args.get("file_path", "") if tool_name != "update_readme" else "README.md"
        lock = _获取文件写锁(file_path)
        async with lock:
            result = await _带超时执行工具(tool_name, tool_args, plugin_path, 超时秒数)
        # 写后自动语法检查：write/edit 成功且目标为 .py 时附加编译检查结果
        if (tool_name in ("write_plugin_file", "edit_file")
                and result.startswith("✅") and file_path.endswith(".py")):
            result = await _附加语法检查(result, [file_path], plugin_path)
        # 目录合规软校验：写入成功时检查落点是否符合节点/前端目录规范（仅警告不阻断）
        if tool_name == "write_plugin_file" and result.startswith("✅"):
            合规警告 = _目录合规警告([file_path], plugin_path)
            if 合规警告:
                result = result + "\n" + 合规警告
        return result
    elif tool_name == "batch_edit":
        # 批量操作使用专用锁，覆盖整个批量操作（非每个子操作单独加锁）
        lock = _获取文件写锁("__batch_edit__")
        async with lock:
            result = await _带超时执行工具(tool_name, tool_args, plugin_path, 超时秒数)
        # 写后自动语法检查：批量操作中成功写入的 .py 文件附加编译检查结果
        if "✅" in result:
            _py_files = [
                str(op.get("file_path", "")) for op in (tool_args.get("operations") or [])
                if isinstance(op, dict) and op.get("action") in ("create", "write", "edit")
                and str(op.get("file_path", "")).endswith(".py")
            ]
            if _py_files:
                result = await _附加语法检查(result, _py_files, plugin_path)
        # 目录合规软校验：批量写入成功的文件检查落点规范（仅警告不阻断）
        # 成功判定优先用结果头部「成功: N」计数（头部位于结果开头，不受尾部截断影响），
        # 取不到头部时降级用 ✅ 标记（超长结果被截断/摘要后可能丢失行内 ✅）
        _成功数匹配 = re.search(r"成功: (\d+)", result)
        _有成功写入 = (int(_成功数匹配.group(1)) > 0) if _成功数匹配 else ("✅" in result)
        if _有成功写入:
            _all_paths = [
                str(op.get("file_path", "")) for op in (tool_args.get("operations") or [])
                if isinstance(op, dict) and op.get("action") in ("create", "write", "edit")
                and op.get("file_path")
            ]
            合规警告 = _目录合规警告(_all_paths, plugin_path)
            if 合规警告:
                result = result + "\n" + 合规警告
        return result
    else:
        return await _带超时执行工具(tool_name, tool_args, plugin_path, 超时秒数)


async def _带超时执行工具(tool_name: str, tool_args: dict, plugin_path: str, 超时秒数: float) -> str:
    """在线程池中执行工具并应用超时保护

    将同步阻塞的工具执行放入线程池（run_in_executor），
    再通过 asyncio.wait_for 施加超时限制。
    超时后返回结构化错误信息，不会中断已完成的文件写入。
    """
    loop = asyncio.get_running_loop()
    try:
        result = await asyncio.wait_for(
            loop.run_in_executor(None, _执行工具内部, tool_name, tool_args, plugin_path),
            timeout=超时秒数
        )
        return result
    except asyncio.TimeoutError:
        logger.warning(
            f"⏱️ 工具执行超时: {tool_name}（超时阈值={超时秒数}s）"
        )
        return (
            f"❌ 工具执行超时：{tool_name} 在 {超时秒数} 秒内未完成。"
            f"请检查操作是否涉及过大文件或外部依赖阻塞。"
        )
    except Exception as e:
        logger.error(f"工具执行异常（超时包装层）: {tool_name}, {type(e).__name__}: {e}")
        return f"❌ 工具执行异常：{type(e).__name__}: {str(e)}"


def _执行工具内部(tool_name: str, tool_args: dict, plugin_path: str) -> str:
    """内部工具执行逻辑（同步函数，由线程池调度执行，写操作由外层加锁保护）"""
    try:
        if tool_name == "update_plan":
            # 任务计划清单（对标 Claude Code TodoWrite / Codex update_plan）：
            # 纯内存态格式化回传，计划靠 tool 消息留在上下文里；压缩/删除历史后
            # 由操作台账（P2-12）保活最新一份，防止长任务跑偏
            return _格式化任务计划(tool_args.get("steps"))

        if tool_name == "read_plugin_file":
            file_path = tool_args.get("file_path")
            if not file_path:
                return "❌ 错误：缺少必需参数 file_path，请提供要读取的文件路径"
            start_line = tool_args.get("start_line")
            end_line = tool_args.get("end_line")
            success, result = _read_plugin_file(plugin_path, file_path, start_line, end_line)
            if success:
                raw = f"📄 文件 {file_path} 内容：\n\n{result}"
                return _截断工具结果(raw, "read_plugin_file")
            return f"❌ 读取失败：{result}"

        if tool_name == "write_plugin_file":
            file_path = tool_args.get("file_path")
            content = tool_args.get("content")
            if not file_path:
                return "❌ 错误：缺少必需参数 file_path，请提供要写入的文件路径"
            if content is None:
                return "❌ 错误：缺少必需参数 content，请提供要写入的文件内容"
            success, message = _write_plugin_file(plugin_path, file_path, content)
            return ("✅ " if success else "❌ ") + str(message)

        if tool_name == "edit_file":
            file_path = tool_args.get("file_path")
            patch = tool_args.get("patch")
            if not file_path:
                return "❌ 错误：缺少必需参数 file_path，请提供要编辑的文件路径"
            if not patch or not patch.strip():
                return "❌ 错误：缺少必需参数 patch，请提供 SEARCH/REPLACE 格式的补丁内容"
            # 构建完整文件路径并调用 apply_patch
            full_path = (Path(plugin_path) / file_path).resolve()
            try:
                full_path.relative_to(Path(plugin_path).resolve())
            except ValueError:
                return "❌ 错误：文件路径超出插件目录范围"
            try:
                _apply_patch(full_path, patch)
                summary = f"✅ 增量修改成功: {file_path}（{_补丁变更摘要(patch)}）"
                return _截断工具结果(summary, "edit_file")
            except FileNotFoundError:
                return f"❌ 文件不存在: {file_path}，请先用 read_plugin_file 确认文件路径"
            except ValueError as ve:
                return f"❌ 补丁应用失败: {str(ve)}"

        if tool_name == "list_plugin_files":
            tree = _scan_plugin_file_tree(plugin_path)
            if not tree:
                return "📁 插件目录为空或不存在"
            formatted = _格式化文件树(tree)
            raw = f"📁 插件文件结构：\n\n{formatted}"
            return _截断工具结果(raw, "list_plugin_files")

        if tool_name == "search_plugin_file":
            file_path = tool_args.get("file_path")
            pattern = tool_args.get("pattern")
            if not file_path:
                return "❌ 错误：缺少必需参数 file_path"
            if not pattern:
                return "❌ 错误：缺少必需参数 pattern"
            use_regex = tool_args.get("use_regex", False)
            context_lines = min(tool_args.get("context_lines", 3), 10)
            success, result = _search_plugin_file(plugin_path, file_path, pattern, use_regex, context_lines)
            if success:
                return _截断工具结果(f"🔍 {result}", "search_plugin_file")
            return f"❌ 搜索失败：{result}"

        if tool_name == "grep_plugin_files":
            pattern = tool_args.get("pattern")
            if not pattern:
                return "❌ 错误：缺少必需参数 pattern"
            glob_filter = tool_args.get("glob")
            use_regex = tool_args.get("use_regex", False)
            context_lines = min(tool_args.get("context_lines", 2), 5)
            success, result = _grep_plugin_files(plugin_path, pattern, glob_filter, use_regex, 30, context_lines)
            if success:
                return _截断工具结果(result, "grep_plugin_files")
            return f"❌ 跨文件搜索失败：{result}"

        if tool_name == "find_plugin_files":
            glob_pattern = tool_args.get("glob_pattern")
            if not glob_pattern:
                return "❌ 错误：缺少必需参数 glob_pattern"
            success, result = _find_plugin_files(plugin_path, glob_pattern)
            if success:
                return _截断工具结果(result, "find_plugin_files")
            return f"❌ 文件查找失败：{result}"

        if tool_name == "rename_plugin_file":
            source_path = tool_args.get("source_path")
            dest_path = tool_args.get("dest_path")
            if not source_path:
                return "❌ 错误：缺少必需参数 source_path"
            if not dest_path:
                return "❌ 错误：缺少必需参数 dest_path"
            success, message = _rename_plugin_file(plugin_path, source_path, dest_path)
            return ("✅ " if success else "❌ ") + str(message)

        if tool_name == "delete_plugin_file":
            file_path = tool_args.get("file_path")
            if not file_path:
                return "❌ 错误：缺少必需参数 file_path"
            success, message = _delete_plugin_file(plugin_path, file_path)
            return ("✅ " if success else "❌ ") + str(message)

        if tool_name == "check_python_syntax":
            file_path = tool_args.get("file_path")
            if not file_path:
                return "❌ 错误：缺少必需参数 file_path"
            success, result = _check_python_syntax(plugin_path, file_path)
            if success:
                return result
            return f"❌ {result}"

        if tool_name == "execute_command":
            command = tool_args.get("command")
            if not command:
                return "❌ 错误：缺少必需参数 command"
            timeout = tool_args.get("timeout", 60)
            success, result = _execute_command(plugin_path, command, timeout)
            return _截断工具结果(result, "execute_command")

        if tool_name == "update_readme":
            changelog_entry = tool_args.get("changelog_entry")
            if not changelog_entry:
                return "❌ 错误：缺少必需参数 changelog_entry，请提供更新说明"
            result = _update_readme_changelog(plugin_path, changelog_entry)
            return ("✅ " if result["success"] else "❌ ") + result["message"]

        if tool_name == "batch_edit":
            operations = tool_args.get("operations", [])
            if not operations:
                return "❌ 错误：未提供任何操作"
            if len(operations) > 20:
                return f"❌ 错误：单次批量操作不能超过 20 个文件（当前: {len(operations)}）"

            results = []  # [(status, path, action, message)]
            success_count = 0
            fail_count = 0

            for i, op in enumerate(operations):
                action = op.get("action", "")
                file_path_str = op.get("file_path", "")
                content = op.get("content", "")

                if not file_path_str:
                    results.append(("❌", "(路径为空)", action, "缺少 file_path 参数"))
                    fail_count += 1
                    continue

                # 路径安全校验
                try:
                    full_path = _解析安全路径(plugin_path, file_path_str)
                except ValueError as e:
                    results.append(("❌", file_path_str, action, str(e)))
                    fail_count += 1
                    continue

                try:
                    if action == "create":
                        if full_path.exists():
                            results.append(("❌", file_path_str, "create", "文件已存在，已跳过"))
                            fail_count += 1
                            continue
                        success, message = _write_plugin_file(plugin_path, file_path_str, content)
                        if success:
                            results.append(("✅", file_path_str, "create", "已创建"))
                            success_count += 1
                        else:
                            results.append(("❌", file_path_str, "create", message))
                            fail_count += 1

                    elif action == "write":
                        success, message = _write_plugin_file(plugin_path, file_path_str, content)
                        if success:
                            results.append(("✅", file_path_str, "write", "已写入"))
                            success_count += 1
                        else:
                            results.append(("❌", file_path_str, "write", message))
                            fail_count += 1

                    elif action == "edit":
                        if not content or not content.strip():
                            results.append(("❌", file_path_str, "edit", "缺少补丁内容"))
                            fail_count += 1
                            continue
                        try:
                            _apply_patch(full_path, content)
                            results.append(("✅", file_path_str, "edit", f"已修改（{_补丁变更摘要(content)}）"))
                            success_count += 1
                        except FileNotFoundError:
                            results.append(("❌", file_path_str, "edit", "文件不存在"))
                            fail_count += 1
                        except ValueError as ve:
                            results.append(("❌", file_path_str, "edit", str(ve)))
                            fail_count += 1

                    elif action == "delete":
                        if not full_path.exists():
                            results.append(("❌", file_path_str, "delete", "文件不存在"))
                            fail_count += 1
                            continue
                        # 删除前创建 .bak 备份
                        _备份文件(full_path)
                        try:
                            full_path.unlink()
                            results.append(("✅", file_path_str, "delete", "已删除（已备份 .bak）"))
                            success_count += 1
                        except OSError as oe:
                            results.append(("❌", file_path_str, "delete", str(oe)))
                            fail_count += 1

                    else:
                        results.append(("❌", file_path_str, action, "未知操作类型"))
                        fail_count += 1

                except (OSError, ValueError, TypeError) as e:
                    results.append(("❌", file_path_str, action, f"{type(e).__name__}: {str(e)}"))
                    fail_count += 1

            # 构建 Markdown 表格汇总
            summary = "## 批量操作完成\n\n"
            summary += f"**成功: {success_count} | 失败: {fail_count} | 总计: {len(operations)}**\n\n"
            summary += "| # | 文件路径 | 操作 | 结果 | 状态 |\n"
            summary += "|---|---------|------|------|------|\n"
            for i, (status, path, action, message) in enumerate(results):
                summary += f"| {i + 1} | `{path}` | {action} | {message} | {status} |\n"

            return _截断工具结果(summary, "batch_edit")

        return f"❌ 错误：未知的工具名称 '{tool_name}'"

    except (OSError, ValueError, TypeError) as e:
        return f"❌ 工具执行异常：{type(e).__name__}: {str(e)}"
