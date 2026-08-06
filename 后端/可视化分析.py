"""可视化分析模块

为可视化界面提供两种分析模式的持久化与构建逻辑：
- 文件模式：复用 插件分析器.analyze_plugin 解析 import 依赖图
- 功能模式：收集源码摘要 → 构造 prompt → 调用 AI 生成功能模块图

落盘结构（位于 <plugin_path>/可视化/）：
- meta.json          元信息（已分析模式、时间戳、版本）
- 文件模式.json      文件依赖图 graphData
- 功能模式.json      AI 生成的功能模块图 graphData
"""
import ast
import asyncio
import json
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Optional

from .文件读写操作 import load_settings
from .日志配置 import 获取日志器
from .系统环境映射 import get_default_llm_path, get_plugin_root

logger = 获取日志器("可视化分析")

# 可视化目录与忽略规则
VISUALIZATION_DIR_NAME = "可视化"
META_FILENAME = "meta.json"
FILE_MODE_FILENAME = "文件模式.json"
FUNCTION_MODE_FILENAME = "功能模式.json"
META_VERSION = 1

# 收集源码摘要时跳过的目录
_SKIP_DIRS = {
    "__pycache__", "node_modules", ".git", ".venv", "venv",
    ".eggs", ".tox", ".mypy_cache", ".pytest_cache",
    VISUALIZATION_DIR_NAME,
}

# 单文件摘要最大字符数（避免给 AI 喂太多）
_MAX_SUMMARY_CHARS_PER_FILE = 1500
# 总摘要最大字符数
_MAX_TOTAL_SUMMARY_CHARS = 30000


# ─── 路径与目录管理 ──────────────────────────────────────────

def get_visualization_dir(plugin_path: Path) -> Path:
    """返回 <plugin_path>/可视化/ 路径（不会自动创建）"""
    return plugin_path / VISUALIZATION_DIR_NAME


def ensure_visualization_dir(plugin_path: Path) -> Path:
    """创建并返回 <plugin_path>/可视化/ 目录"""
    vis_dir = get_visualization_dir(plugin_path)
    vis_dir.mkdir(parents=True, exist_ok=True)
    return vis_dir


def _now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _read_json(filepath: Path) -> Optional[dict]:
    try:
        return json.loads(filepath.read_text(encoding="utf-8"))
    except Exception:
        return None


def _write_json(filepath: Path, data: dict) -> None:
    filepath.write_text(
        json.dumps(data, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


# ─── meta.json 读写 ───────────────────────────────────────

def load_meta(plugin_path: Path) -> Optional[dict]:
    """读取 meta.json，不存在返回 None"""
    vis_dir = get_visualization_dir(plugin_path)
    meta_path = vis_dir / META_FILENAME
    if not meta_path.exists():
        return None
    return _read_json(meta_path)


def _default_meta(plugin_name: str) -> dict:
    return {
        "plugin_name": plugin_name,
        "file_mode_analyzed": False,
        "function_mode_analyzed": False,
        "file_mode_time": None,
        "function_mode_time": None,
        "version": META_VERSION,
    }


def update_meta(plugin_path: Path, mode: str) -> dict:
    """更新 meta.json 中对应模式的状态与时间戳，返回新 meta"""
    vis_dir = ensure_visualization_dir(plugin_path)
    meta_path = vis_dir / META_FILENAME

    meta = _read_json(meta_path) or _default_meta(plugin_path.name)
    meta.setdefault("plugin_name", plugin_path.name)
    meta.setdefault("version", META_VERSION)

    now = _now_iso()
    if mode == "file":
        meta["file_mode_analyzed"] = True
        meta["file_mode_time"] = now
    elif mode == "function":
        meta["function_mode_analyzed"] = True
        meta["function_mode_time"] = now

    _write_json(meta_path, meta)
    return meta


# ─── 文件模式分析 ────────────────────────────────────────────

def run_file_mode_analysis(plugin_path: Path, persist: bool = True) -> dict:
    """运行文件模式分析（复用 插件分析器.analyze_plugin）

    Args:
        plugin_path: 插件目录
        persist: 是否落盘到 可视化/文件模式.json 并更新 meta.json

    Returns:
        graphData: {nodes, links, stats}
    """
    from .插件分析器 import analyze_plugin
    result = analyze_plugin(str(plugin_path))

    if persist:
        vis_dir = ensure_visualization_dir(plugin_path)
        _write_json(vis_dir / FILE_MODE_FILENAME, result)
        update_meta(plugin_path, "file")

    return result


# ─── 功能模式：源码摘要收集 ─────────────────────────────────

def _should_skip_path(rel_parts: tuple) -> bool:
    """检查路径任意一段是否在跳过目录集合中"""
    for p in rel_parts:
        if p in _SKIP_DIRS or p.startswith('.'):
            return True
    return False


def _summarize_python_file(source: str) -> str:
    """使用 ast 提取 Python 文件中的类/方法/函数签名"""
    lines = []
    try:
        tree = ast.parse(source)
    except (SyntaxError, ValueError):
        return ""

    for node in tree.body:
        if isinstance(node, ast.ClassDef):
            lines.append(f"class {node.name}:")
            for child in node.body:
                if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    args = ", ".join(a.arg for a in child.args.args)
                    prefix = "async def" if isinstance(child, ast.AsyncFunctionDef) else "def"
                    lines.append(f"    {prefix} {child.name}({args})")
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            args = ", ".join(a.arg for a in node.args.args)
            prefix = "async def" if isinstance(node, ast.AsyncFunctionDef) else "def"
            lines.append(f"{prefix} {node.name}({args})")

    summary = "\n".join(lines)
    if len(summary) > _MAX_SUMMARY_CHARS_PER_FILE:
        summary = summary[:_MAX_SUMMARY_CHARS_PER_FILE] + "\n... (摘要截断)"
    return summary


# JS 摘要正则：匹配 export class/function/const、function、class
_JS_SIGNATURE_PATTERNS = [
    re.compile(r'export\s+(?:default\s+)?class\s+(\w+)'),
    re.compile(r'export\s+(?:default\s+)?(?:async\s+)?function\s+(\w+)\s*\(([^)]*)\)'),
    re.compile(r'export\s+(?:const|let|var)\s+(\w+)\s*='),
    re.compile(r'^\s*class\s+(\w+)', re.MULTILINE),
    re.compile(r'^\s*(?:async\s+)?function\s+(\w+)\s*\(([^)]*)\)', re.MULTILINE),
]


def _summarize_js_file(source: str) -> str:
    """使用正则提取 JS 文件中的 export/class/function 声明"""
    found = []
    seen = set()

    # 类
    for m in _JS_SIGNATURE_PATTERNS[0].finditer(source):
        key = ("class", m.group(1))
        if key not in seen:
            seen.add(key)
            found.append(f"class {m.group(1)}")
    for m in _JS_SIGNATURE_PATTERNS[3].finditer(source):
        key = ("class", m.group(1))
        if key not in seen:
            seen.add(key)
            found.append(f"class {m.group(1)}")

    # 函数
    for m in _JS_SIGNATURE_PATTERNS[1].finditer(source):
        key = ("function", m.group(1))
        if key not in seen:
            seen.add(key)
            found.append(f"function {m.group(1)}({m.group(2).strip()})")
    for m in _JS_SIGNATURE_PATTERNS[4].finditer(source):
        key = ("function", m.group(1))
        if key not in seen:
            seen.add(key)
            found.append(f"function {m.group(1)}({m.group(2).strip()})")

    # 常量导出
    for m in _JS_SIGNATURE_PATTERNS[2].finditer(source):
        key = ("const", m.group(1))
        if key not in seen:
            seen.add(key)
            found.append(f"export const {m.group(1)}")

    summary = "\n".join(found)
    if len(summary) > _MAX_SUMMARY_CHARS_PER_FILE:
        summary = summary[:_MAX_SUMMARY_CHARS_PER_FILE] + "\n... (摘要截断)"
    return summary


def collect_code_summary(plugin_path: Path) -> tuple:
    """遍历插件目录，返回 (file_tree_str, code_summary_str)

    - file_tree_str: 类似 tree 命令的目录结构（虚拟环境目录折叠为单行）
    - code_summary_str: 拼接所有 .py/.js 摘要（带文件路径分隔，排除虚拟环境）
    """
    # 延迟导入避免循环依赖
    from .插件分析器 import detect_virtualenv_dirs

    if not plugin_path.exists():
        return "", ""

    # 检测虚拟环境目录
    env_dir_names = set(detect_virtualenv_dirs(plugin_path))

    tree_lines = []
    summary_blocks = []
    total_summary_chars = 0

    # 收集所有有效相对路径（排除虚拟环境目录下的文件）
    all_files = []
    for item in sorted(plugin_path.rglob("*")):
        rel_parts = item.relative_to(plugin_path).parts
        if not rel_parts:
            continue
        if _should_skip_path(rel_parts):
            continue
        # 跳过虚拟环境目录下的文件
        if env_dir_names and any(p in env_dir_names for p in rel_parts[:-1]):
            continue
        if item.is_file():
            all_files.append(item.relative_to(plugin_path))

    # 构建 tree 字符串（虚拟环境目录折叠为单行）
    tree_lines.append(f"{plugin_path.name}/")

    # 记录已在树中显示的虚拟环境目录（避免重复显示）
    shown_env_dirs = set()

    for rel in all_files:
        # 检查是否为虚拟环境目录下的文件（应被折叠）
        top_dir = rel.parts[0] if len(rel.parts) > 1 else None
        if top_dir and top_dir in env_dir_names:
            if top_dir not in shown_env_dirs:
                shown_env_dirs.add(top_dir)
                # 统计环境目录文件数
                env_path = plugin_path / top_dir
                env_file_count = sum(1 for _ in env_path.rglob('*') if _.is_file())
                tree_lines.append(f"  {top_dir}/ (虚拟环境, {env_file_count} 个文件, 已折叠)")
            continue

        depth = len(rel.parts) - 1
        indent = "  " * (depth + 1)
        tree_lines.append(f"{indent}{rel.parts[-1]}")
    file_tree_str = "\n".join(tree_lines)

    # 收集 .py 和 .js 文件摘要（排除虚拟环境）
    for rel in all_files:
        suffix = rel.suffix.lower()
        if suffix not in (".py", ".js"):
            continue

        full_path = plugin_path / rel
        try:
            source = full_path.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            continue

        if suffix == ".py":
            summary = _summarize_python_file(source)
        else:
            summary = _summarize_js_file(source)

        if not summary.strip():
            continue

        rel_str = str(rel).replace("\\", "/")
        block = f"=== {rel_str} ===\n{summary}\n"

        if total_summary_chars + len(block) > _MAX_TOTAL_SUMMARY_CHARS:
            summary_blocks.append("... (其余文件摘要省略)")
            break

        summary_blocks.append(block)
        total_summary_chars += len(block)

    # 添加虚拟环境基础关联信息（仅目录名 + 类型，不展开内容）
    if env_dir_names:
        env_notes = ["\n=== 虚拟环境目录（仅基础关联，不展开分析） ==="]
        for env_name in sorted(env_dir_names):
            env_notes.append(f"  {env_name}/ → 隔离运行环境，包含该功能所需的第三方依赖")
        summary_blocks.append("\n".join(env_notes))

    return file_tree_str, "\n".join(summary_blocks)


# ─── 功能模式：Prompt 与 AI 调用 ─────────────────────────

_FUNCTION_MODE_PROMPT_TEMPLATE = """分析以下 ComfyUI 插件的功能架构，输出 JSON 格式的功能模块图。

要求：
- 每个节点代表一个独立的功能模块（不是文件，而是逻辑功能单元）
- 连线表示功能模块之间的调用或依赖关系
- type 字段必须是以下之一：route, model, ui, storage, auth, tool, core
- 严格只输出 JSON，不要输出其他内容

输出格式：
{{"nodes": [{{"id": "唯一标识", "name": "模块名称", "type": "类型", "description": "功能描述", "group": "所属分组"}}],
 "links": [{{"source": "源节点id", "target": "目标节点id", "label": "关系描述"}}],
 "stats": {{"total_count": 数量}}}}

插件文件结构：
{file_tree}

关键文件内容摘要：
{code_summary}
"""


def build_function_mode_prompt(file_tree: str, code_summary: str) -> str:
    """构建功能模式 prompt"""
    return _FUNCTION_MODE_PROMPT_TEMPLATE.format(
        file_tree=file_tree,
        code_summary=code_summary,
    )


def _strip_code_fence(text: str) -> str:
    """去除可能的 markdown 代码块包裹与 <thinking> 标签"""
    text = re.sub(r'<thinking>[\s\S]*?</thinking>', '', text).strip()
    # 去除 ```json ... ``` 或 ``` ... ```
    fence_match = re.match(r'^\s*```(?:json|JSON)?\s*\n([\s\S]*?)\n```\s*$', text)
    if fence_match:
        return fence_match.group(1).strip()
    return text


def parse_ai_json(raw_text: str) -> Optional[dict]:
    """解析 AI 输出的 JSON，支持 markdown 包裹与截断修复"""
    if not raw_text:
        return None

    cleaned = _strip_code_fence(raw_text)

    # 直接解析
    try:
        return json.loads(cleaned)
    except (json.JSONDecodeError, ValueError):
        pass

    # 截断修复
    try:
        if str(get_plugin_root()) not in sys.path:
            sys.path.insert(0, str(get_plugin_root()))
        from 智能体.JSON修复工具 import _修复截断JSON
        repaired = _修复截断JSON(cleaned)
        if repaired:
            return json.loads(repaired)
    except Exception:
        pass

    return None


async def _call_ai_for_function_mode(prompt: str, llm_client, local_model_client) -> str:
    """根据当前 settings 选择模型，累积流式响应并返回完整文本"""
    # P1-1：同步 I/O 放入线程池
    settings = await asyncio.to_thread(load_settings)
    model_source = settings.get("model_source", "api")

    messages = [
        {"role": "system", "content": "你是 ComfyUI 插件架构分析助手，擅长输出严格的 JSON。"},
        {"role": "user", "content": prompt},
    ]

    # 功能模式需生成完整 JSON 节点图，需更大 token 预算，防止输出截断
    viz_settings = {**settings, "max_tokens": max(settings.get("max_tokens", 4096) or 4096, 16384)}

    full_text = ""

    if model_source == "local" and local_model_client is not None:
        # 本地模型：确保已加载
        local_model_name = settings.get("local_model_name", "")
        local_path = settings.get("local_path", "")

        if not local_path and local_model_name:
            local_path = str(get_default_llm_path() / local_model_name)

        if not local_path:
            llm_dir = get_default_llm_path()
            if llm_dir.exists():
                for item in llm_dir.iterdir():
                    if item.is_dir() and (item / "config.json").exists():
                        local_path = str(item)
                        break

        if not local_path:
            raise RuntimeError("未检测到可用的本地模型，请先配置或放置模型")

        if local_model_client.当前模型名 != Path(local_path).name:
            # P0-4: 使用异步加载（asyncio.Lock + 双重检查）防止并发重复加载
            await local_model_client.异步加载模型(local_path)

        async for chunk in local_model_client.流式对话(messages, viz_settings):
            full_text += chunk
    else:
        if llm_client is None:
            raise RuntimeError("API 模型客户端未初始化")
        async for chunk in llm_client.流式对话(messages, viz_settings):
            full_text += chunk

    return full_text


async def run_function_mode_analysis(plugin_path: Path, llm_client, local_model_client,
                                     persist: bool = True) -> dict:
    """运行功能模式分析

    流程：
        1. 收集源码摘要
        2. 构造 prompt
        3. 调用当前选中 AI 模型（累积流式响应）
        4. 解析 JSON
        5. 落盘 + 更新 meta

    Returns:
        {nodes, links, stats} 或包含 error 字段的 dict
    """
    # P1-1：源码摘要遇文件递归读取，放入线程池
    file_tree, code_summary = await asyncio.to_thread(collect_code_summary, plugin_path)
    if not file_tree:
        return {"nodes": [], "links": [], "stats": {"total_count": 0}, "error": "插件目录为空或不可读"}

    prompt = build_function_mode_prompt(file_tree, code_summary)

    try:
        raw_text = await _call_ai_for_function_mode(prompt, llm_client, local_model_client)
    except Exception as e:
        return {"nodes": [], "links": [], "stats": {"total_count": 0}, "error": f"AI 调用失败: {e}"}

    parsed = parse_ai_json(raw_text)
    if parsed is None:
        return {
            "nodes": [], "links": [], "stats": {"total_count": 0},
            "error": "AI 输出无法解析为 JSON",
            "raw": raw_text[:500],
        }

    # 字段兜底
    parsed.setdefault("nodes", [])
    parsed.setdefault("links", [])
    parsed.setdefault("stats", {"total_count": len(parsed["nodes"])})

    if persist:
        # P1-1： mkdir + JSON 写入 + meta 更新均为同步 I/O，放入线程池
        def _落盘():
            vis_dir = ensure_visualization_dir(plugin_path)
            _write_json(vis_dir / FUNCTION_MODE_FILENAME, parsed)
            update_meta(plugin_path, "function")

        await asyncio.to_thread(_落盘)

    return parsed


# ─── 加载已持久化的可视化数据 ──────────────────────────

def load_visualization(plugin_path: Path) -> dict:
    """读取 可视化/ 目录下的 meta + 文件模式 + 功能模式

    Returns:
        {meta, file_mode_data?, function_mode_data?}
        若 可视化/ 目录不存在 → {meta: None}
    """
    vis_dir = get_visualization_dir(plugin_path)
    if not vis_dir.exists():
        return {"meta": None}

    meta = load_meta(plugin_path)
    if meta is None:
        return {"meta": None}

    payload = {"meta": meta}

    if meta.get("file_mode_analyzed"):
        file_mode_data = _read_json(vis_dir / FILE_MODE_FILENAME)
        if file_mode_data is not None:
            payload["file_mode_data"] = file_mode_data

    if meta.get("function_mode_analyzed"):
        function_mode_data = _read_json(vis_dir / FUNCTION_MODE_FILENAME)
        if function_mode_data is not None:
            payload["function_mode_data"] = function_mode_data

    return payload


__all__ = [
    "VISUALIZATION_DIR_NAME",
    "get_visualization_dir",
    "ensure_visualization_dir",
    "load_meta",
    "update_meta",
    "run_file_mode_analysis",
    "run_function_mode_analysis",
    "load_visualization",
    "collect_code_summary",
    "build_function_mode_prompt",
    "parse_ai_json",
]
