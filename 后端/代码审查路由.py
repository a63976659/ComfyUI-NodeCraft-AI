"""代码审查路由模块

包含：
- POST /ai-coder/code-review               AI 代码审查（扫描源码→LLM→结构化报告）
- POST /ai-coder/benchmark/analyze         分析 Profiling 性能数据
"""
import ast
import asyncio
import json
import os
import re
import sys
from collections import defaultdict
from pathlib import Path

from aiohttp import web

from .文件读写操作 import load_settings
from .日志配置 import 获取日志器
from .系统环境映射 import get_custom_nodes_path, get_default_llm_path
from .路由公共 import (
    _error_response,
    _rate_limiter,
    llm_client,
    local_model_client,
    tool_router,
    服务器内部错误,
)

logger = 获取日志器("代码审查路由")


def _resolve_plugin_path(plugin_name: str) -> Path:
    """将用户传入的插件路径解析为绝对路径（相对路径基于 custom_nodes）"""
    plugin_path = Path(plugin_name)
    if not plugin_path.is_absolute():
        plugin_path = get_custom_nodes_path() / plugin_name
    return plugin_path


# ─── AI 代码审查 ─────────────────────────────────────────────

def _筛选审查文件(all_files: list, review_depth: str) -> list:
    """根据审查深度筛选需要审查的文件列表"""
    if review_depth == "quick":
        # 快速模式：__init__.py 和根目录 .py 文件
        result = []
        for f in all_files:
            if f.endswith("__init__.py"):
                result.append(f)
            elif f.endswith(".py") and "/" not in f:
                result.append(f)
        return result
    elif review_depth == "deep":
        # 深度模式：所有 .py 和 .js 文件
        return [f for f in all_files if f.endswith(".py") or f.endswith(".js")]
    else:
        # 标准模式：所有 .py 文件 + 前端 .js 文件（web/js 目录下，用于布局审查）
        result = [f for f in all_files if f.endswith(".py")]
        for f in all_files:
            norm = f.replace("\\", "/")
            if f.endswith(".js") and ("web/" in norm or norm.startswith("js/")):
                result.append(f)
        return result


def _构建审查提示词(file_summaries: list, review_knowledge: str, review_depth: str):
    """构建代码审查专用提示词，返回 (system_prompt, user_message)"""
    depth_desc = {
        "quick": "快速审查（仅核心文件）",
        "standard": "标准审查（所有 Python 文件 + 前端 JS 文件）",
        "deep": "深度审查（所有 Python 和 JavaScript 文件）",
    }.get(review_depth, "标准审查")

    system_prompt = (
        "你是一位 ComfyUI 插件代码审查专家。请对以下插件代码进行"
        + depth_desc
        + "。\n\n"
        "## 审查标准\n"
        + (review_knowledge or "参考通用代码质量审查清单")
        + "\n\n"
        "## 审查维度\n"
        "1. 安全性（security）：输入验证、路径遍历防护、代码注入防护、敏感信息处理\n"
        "   具体检查：\n"
        "   - os.path.join 后未校验路径是否在安全目录内（路径遍历）\n"
        "   - eval/exec/pickle.loads 的使用（代码注入）\n"
        "   - 硬编码的密钥、token、密码（信息泄露）\n"
        "   - 未验证的用户输入直接拼接命令（命令注入）\n"
        "   - 不安全的临时文件创建（竞态条件）\n"
        "2. 性能（performance）：异步 I/O、内存管理、GPU 利用率\n"
        "   特别关注以下 ComfyUI 常见性能模式：\n"
        "   - torch.cuda.empty_cache() 在循环内使用（应移出循环）\n"
        "   - 推理路径缺少 torch.no_grad()/inference_mode() 上下文\n"
        "   - CPU/GPU 间频繁的张量迁移\n"
        "   - 模型重复加载（应缓存）\n"
        "   - 同步IO阻塞（应异步化）\n"
        "   - 大张量拼接使用list+cat而非预分配\n"
        "3. 代码规范（style）：命名规范、类型提示、文档字符串\n"
        "4. ComfyUI 节点规范（comfyui）：INPUT_TYPES/RETURN_TYPES/Function 对齐、节点注册、IS_CHANGED 实现\n"
        "5. 可维护性（maintenance）：模块化、错误处理、日志使用、配置管理\n"
        "6. 前端 Widget 布局（frontend_layout）：多 Widget 混排排版正确性（存在 .js 前端文件时必查）\n"
        "   背景：ComfyUI 节点的 widgets 数组按顺序垂直排列，每个 widget 的 computeSize 决定占高，"
        "未设置 computeSize 的 DOM Widget 占高为 0，后续原生控件会直接叠压在它上面。\n"
        "   具体检查：\n"
        "   - addDOMWidget/自定义 DOM 面板未设置 computeSize（面板被原生滑块/文本框叠压）\n"
        "   - 用 position:absolute 让 DOM 元素脱离布局流覆盖节点其他控件（禁止）\n"
        "   - multiline STRING 文本框与自定义面板混排且未处理挤压（multiline 会抢占剩余空间）\n"
        "   - 隐藏 widget 未做三层幽灵化（type=\"hidden\" + computeSize=()=>[0,-4] + 空 draw）\n"
        "   - 添加/重排 widget 后未调用 node.setSize(node.computeSize()) 重算节点尺寸\n\n"
        "## 输出格式\n"
        "请严格输出以下 JSON 格式的审查报告（不要包含其他文本）：\n\n"
        "```json\n"
        "{\n"
        '    "summary": "总体评价（2-3句话概括代码质量）",\n'
        '    "score": 85,\n'
        '    "issues": [\n'
        '        {\n'
        '            "severity": "high",\n'
        '            "category": "security",\n'
        '            "file": "node.py",\n'
        '            "line": 42,\n'
        '            "description": "问题描述",\n'
        '            "suggestion": "改进建议"\n'
        '        }\n'
        '    ],\n'
        '    "strengths": ["代码优点1", "代码优点2"],\n'
        '    "performance_patterns": [\n'
        '        {\n'
        '            "pattern": "empty_cache_in_loop",\n'
        '            "file": "node.py",\n'
        '            "line": 42,\n'
        '            "description": "torch.cuda.empty_cache() 在推理循环内调用",\n'
        '            "impact": "high",\n'
        '            "fix_suggestion": "将 empty_cache() 移至循环结束后调用一次"\n'
        '        }\n'
        '    ],\n'
        '    "architecture_suggestions": [\n'
        '        {\n'
        '            "type": "circular_dependency|high_coupling|god_file|hardcoded_config",\n'
        '            "description": "问题描述",\n'
        '            "severity": "high|medium|low",\n'
        '            "suggestion": "改进建议"\n'
        '        }\n'
        '    ],\n'
        '    "complexity_notes": {\n'
        '        "high_risk_functions": ["函数名(McCabe>7)"],\n'
        '        "avg_complexity": "估算值",\n'
        '        "longest_function_lines": 0\n'
        '    }\n'
        "}\n"
        "```\n\n"
        "注意：\n"
        "- score 范围 0-100，90+优秀，70-89良好，60-69及格，60以下不合格\n"
        "- issues 按 severity 排序（high > medium > low）\n"
        "- category 取值：security/performance/style/comfyui/maintenance/frontend_layout\n"
        "- 如果没有发现问题，issues 为空数组\n"
        "- strengths 至少列出 1 个优点\n"
        "- line 字段尽量给出大致行号，无法确定时填 0\n\n"
        "请额外填充以下字段：\n"
        "- performance_patterns：识别到的性能反模式列表，每个包含 pattern（模式标识）、"
        "file、line、description、impact（high/medium/low）、fix_suggestion\n"
        "- architecture_suggestions：架构改进建议列表，每个包含 type、description、severity、suggestion\n"
        "- complexity_notes：复杂度估算，包含 high_risk_functions（估计McCabe>7的函数名列表）、"
        "avg_complexity（整体评估）、longest_function_lines（最长函数行数）\n"
        "如果某个字段无相关发现，请填空数组[]或合理默认值。\n"
    )

    code_parts = []
    for f in file_summaries:
        ext = "javascript" if f["path"].endswith(".js") else "python"
        code_parts.append(
            f"### 文件: {f['path']} ({f['size']} 字符)\n```{ext}\n{f['content']}\n```"
        )

    user_message = (
        f"## 待审查插件代码\n\n"
        f"共 {len(file_summaries)} 个文件：\n\n"
        + "\n\n".join(code_parts)
        + "\n\n请按照审查标准对以上代码进行全面审查，输出 JSON 格式的审查报告。"
    )
    return system_prompt, user_message


def _解析审查结果(reply: str) -> dict:
    """解析 LLM 返回的审查结果，提取 JSON"""
    import json as _json
    import re as _re

    if not reply:
        return {"summary": "审查结果为空", "score": 0, "issues": [], "strengths": []}

    # 1. 尝试直接 JSON 解析
    try:
        return _json.loads(reply)
    except _json.JSONDecodeError:
        pass

    # 2. 尝试从代码块中提取 JSON
    json_match = _re.search(r'```(?:json)?\s*(\{[\s\S]*?\})\s*```', reply)
    if json_match:
        try:
            return _json.loads(json_match.group(1))
        except _json.JSONDecodeError:
            pass

    # 3. 尝试从文本中提取最外层 JSON 对象
    brace_start = reply.find("{")
    brace_end = reply.rfind("}")
    if brace_start != -1 and brace_end != -1 and brace_end > brace_start:
        try:
            return _json.loads(reply[brace_start:brace_end + 1])
        except _json.JSONDecodeError:
            pass

    # 4. 降级：返回原始文本作为摘要
    return {
        "summary": reply[:500],
        "score": 0,
        "issues": [],
        "strengths": [],
        "raw_response": reply,
    }


async def _执行静态分析(plugin_path: Path) -> dict:
    """
    对插件目录执行静态分析，返回结构化结果。
    包含：Lint检查、复杂度分析、依赖安全扫描。
    任何工具不可用时优雅降级。
    """
    结果 = {
        "lint": None,       # {total_issues: int, errors: int, warnings: int, top_issues: [...]}
        "complexity": None, # {avg_complexity: float, high_risk_functions: [...], max_complexity: int}
        "security": None    # {vulnerabilities: int, details: [...]}
    }

    # 1. Lint 检查 (pylint 或 flake8)
    try:
        py_files = list(plugin_path.glob("**/*.py"))
        if py_files:
            file_paths = [str(f) for f in py_files[:20]]  # 限制文件数避免超时
            proc = await asyncio.create_subprocess_exec(
                sys.executable, "-m", "pylint",
                *file_paths,
                "--output-format=json",
                "--disable=C,R",  # 只报告 Error 和 Warning
                "--max-line-length=150",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=str(plugin_path)
            )
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=30)
            if stdout:
                issues = json.loads(stdout.decode("utf-8", errors="replace"))
                errors = sum(1 for i in issues if i.get("type") == "error")
                warnings = sum(1 for i in issues if i.get("type") == "warning")
                # 取前10个重要问题
                top_issues = [
                    f"{i.get('path','?')}:{i.get('line','?')} [{i.get('symbol','')}] {i.get('message','')}"
                    for i in issues[:10]
                ]
                结果["lint"] = {
                    "total_issues": len(issues),
                    "errors": errors,
                    "warnings": warnings,
                    "top_issues": top_issues
                }
    except (FileNotFoundError, asyncio.TimeoutError, json.JSONDecodeError, Exception):
        # pylint 不可用时尝试 flake8
        try:
            py_files = list(plugin_path.glob("**/*.py"))
            if py_files:
                file_paths = [str(f) for f in py_files[:20]]
                proc = await asyncio.create_subprocess_exec(
                    sys.executable, "-m", "flake8",
                    *file_paths,
                    "--max-line-length=150",
                    "--count",
                    "--statistics",
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE
                )
                stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=30)
                if stdout:
                    lines = stdout.decode("utf-8", errors="replace").strip().split("\n")
                    结果["lint"] = {
                        "total_issues": len(lines),
                        "errors": 0,
                        "warnings": len(lines),
                        "top_issues": lines[:10]
                    }
        except Exception:
            pass  # 两个工具都不可用，降级

    # 2. 复杂度分析 (radon)
    try:
        py_files = list(plugin_path.glob("**/*.py"))
        if py_files:
            file_paths = [str(f) for f in py_files[:20]]
            proc = await asyncio.create_subprocess_exec(
                sys.executable, "-m", "radon", "cc",
                *file_paths,
                "-j",  # JSON output
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=30)
            if stdout:
                cc_data = json.loads(stdout.decode("utf-8", errors="replace"))
                all_complexities = []
                high_risk = []
                for file_path, blocks in cc_data.items():
                    for block in blocks:
                        complexity = block.get("complexity", 0)
                        all_complexities.append(complexity)
                        if complexity > 7:
                            name = block.get("name", "unknown")
                            high_risk.append(f"{Path(file_path).name}:{name}(复杂度={complexity})")

                avg = sum(all_complexities) / len(all_complexities) if all_complexities else 0
                max_cc = max(all_complexities) if all_complexities else 0
                结果["complexity"] = {
                    "avg_complexity": round(avg, 2),
                    "high_risk_functions": high_risk[:10],
                    "max_complexity": max_cc
                }
    except (FileNotFoundError, asyncio.TimeoutError, json.JSONDecodeError, Exception):
        pass  # radon 不可用，降级

    # 3. 依赖安全扫描 (pip-audit)
    try:
        req_file = plugin_path / "requirements.txt"
        if req_file.exists():
            proc = await asyncio.create_subprocess_exec(
                sys.executable, "-m", "pip_audit",
                "--requirement", str(req_file),
                "--format=json",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=60)
            if stdout:
                audit_data = json.loads(stdout.decode("utf-8", errors="replace"))
                vulns = []
                if isinstance(audit_data, list):
                    for item in audit_data:
                        if item.get("vulns"):
                            for v in item["vulns"]:
                                vulns.append(
                                    f"{item.get('name','')}=={item.get('version','')}: "
                                    f"{v.get('id','')} - {v.get('description','')[:80]}"
                                )
                结果["security"] = {
                    "vulnerabilities": len(vulns),
                    "details": vulns[:5]
                }
    except (FileNotFoundError, asyncio.TimeoutError, json.JSONDecodeError, Exception):
        pass  # pip-audit 不可用，降级

    return 结果


def _检查前端布局(plugin_path: Path) -> list:
    """
    对插件前端 JS 文件做轻量启发式扫描，识别常见 Widget 布局反模式。
    返回发现列表 [{file, line, pattern, description}]，结果作为 LLM 审查的参考线索。
    同步函数，调用时应放入线程池。
    """
    发现 = []
    try:
        js_files = list(plugin_path.glob("**/*.js"))[:20]
        for js_file in js_files:
            # 跳过三方库目录
            rel = str(js_file.relative_to(plugin_path)).replace(os.sep, "/")
            if any(seg in rel for seg in ("node_modules/", "lib/", "vendor/", ".min.js")):
                continue
            try:
                content = js_file.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            lines = content.splitlines()

            有DOM面板 = bool(re.search(r"addDOMWidget|appendChild|createElement", content))
            有computeSize = "computeSize" in content

            # 1. 有 DOM 面板但全文没有 computeSize → 占高塌陷，原生控件会叠压
            if 有DOM面板 and re.search(r"addDOMWidget", content) and not 有computeSize:
                for i, line in enumerate(lines, 1):
                    if "addDOMWidget" in line:
                        发现.append({
                            "file": rel, "line": i,
                            "pattern": "dom_widget_no_compute_size",
                            "description": "addDOMWidget 但未设置 computeSize，DOM 面板占高为 0，后续原生控件会叠压在面板上",
                        })
                        break

            # 2. position:absolute 脱离布局流
            for i, line in enumerate(lines, 1):
                if re.search(r"position\s*[:=]\s*['\"]?absolute", line):
                    发现.append({
                        "file": rel, "line": i,
                        "pattern": "absolute_position_overlay",
                        "description": "使用 position:absolute 脱离节点布局流，可能覆盖其他 widget（应改用布局流 + computeSize）",
                    })

            # 3. 隐藏 widget 只改 type 未三层幽灵化
            if re.search(r"\.type\s*=\s*['\"]hidden['\"]", content) and not re.search(r"computeSize\s*=", content):
                for i, line in enumerate(lines, 1):
                    if re.search(r"\.type\s*=\s*['\"]hidden['\"]", line):
                        发现.append({
                            "file": rel, "line": i,
                            "pattern": "hidden_widget_incomplete",
                            "description": "隐藏 widget 仅设 type=hidden，未同时覆盖 computeSize=()=>[0,-4] 与空 draw，仍会占位",
                        })
                        break

            # 4. 动态增删/重排 widget 后未重算节点尺寸
            改动widgets = bool(re.search(r"widgets\.(push|pop|splice|unshift)|addDOMWidget|addWidget", content))
            if 改动widgets and "setSize" not in content:
                发现.append({
                    "file": rel, "line": 0,
                    "pattern": "missing_set_size",
                    "description": "添加/重排 widget 后未调用 node.setSize(node.computeSize())，节点高度可能容不下全部控件",
                })
    except Exception:
        pass  # 启发式检查失败不影响主流程

    return 发现[:15]


async def _分析模块架构(plugin_path: Path) -> dict:
    """
    分析插件的模块架构：导入关系、循环依赖、模块化指标。
    """
    结果 = {
        "modularity_score": None,       # 0-100 模块化评分
        "circular_dependencies": [],    # 循环依赖列表
        "coupling_metrics": None,       # {avg_fan_out, max_fan_out, tightly_coupled_pairs}
        "file_metrics": []              # [{file, lines, imports_count, exported_functions}]
    }

    try:
        py_files = list(plugin_path.glob("**/*.py"))
        if not py_files:
            return 结果

        # 超大项目（>50个py文件）时只分析前30个文件
        if len(py_files) > 50:
            py_files = py_files[:30]

        # 构建导入关系图
        import_graph = defaultdict(set)  # file -> set of imported files
        file_info = {}

        for py_file in py_files:
            relative = py_file.relative_to(plugin_path)
            module_name = str(relative).replace(os.sep, ".").replace(".py", "")

            try:
                content = py_file.read_text(encoding="utf-8", errors="replace")
                tree = ast.parse(content)

                # 统计文件信息
                lines = len(content.splitlines())
                functions = [
                    node.name for node in ast.walk(tree)
                    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
                    and not node.name.startswith("_")
                ]

                # 提取导入关系（只关注本地模块导入）
                local_imports = set()
                for node in ast.walk(tree):
                    if isinstance(node, ast.Import):
                        for alias in node.names:
                            # 检查是否是本地模块
                            for other_file in py_files:
                                other_module = (
                                    str(other_file.relative_to(plugin_path))
                                    .replace(os.sep, ".")
                                    .replace(".py", "")
                                )
                                if alias.name == other_module or alias.name.startswith(other_module + "."):
                                    local_imports.add(other_module)
                    elif isinstance(node, ast.ImportFrom):
                        if node.module and node.level == 0:
                            for other_file in py_files:
                                other_module = (
                                    str(other_file.relative_to(plugin_path))
                                    .replace(os.sep, ".")
                                    .replace(".py", "")
                                )
                                if node.module == other_module or node.module.startswith(other_module + "."):
                                    local_imports.add(other_module)
                        elif node.level > 0:
                            # 相对导入
                            local_imports.add(f"(相对导入: level={node.level}, module={node.module})")

                import_graph[module_name] = local_imports
                file_info[module_name] = {
                    "file": str(relative),
                    "lines": lines,
                    "imports_count": len(local_imports),
                    "exported_functions": len(functions)
                }

            except (SyntaxError, UnicodeDecodeError):
                continue

        # 检测循环依赖（DFS）
        def find_cycles(graph):
            cycles = []
            visited = set()
            rec_stack = set()
            path = []

            def dfs(node):
                visited.add(node)
                rec_stack.add(node)
                path.append(node)

                for neighbor in graph.get(node, set()):
                    if isinstance(neighbor, str) and not neighbor.startswith("("):
                        if neighbor not in visited:
                            cycle = dfs(neighbor)
                            if cycle:
                                return cycle
                        elif neighbor in rec_stack:
                            # 找到循环
                            cycle_start = path.index(neighbor)
                            cycles.append(path[cycle_start:] + [neighbor])
                            return True

                path.pop()
                rec_stack.discard(node)
                return False

            for node in graph:
                if node not in visited:
                    dfs(node)

            return cycles

        cycles = find_cycles(import_graph)
        结果["circular_dependencies"] = [
            " → ".join(cycle) for cycle in cycles[:5]  # 最多报告5个循环
        ]

        # 计算耦合度指标
        fan_outs = [len(imports) for imports in import_graph.values()]
        avg_fan_out = sum(fan_outs) / len(fan_outs) if fan_outs else 0
        max_fan_out = max(fan_outs) if fan_outs else 0

        # 识别紧耦合对（互相导入）
        tightly_coupled = []
        modules = list(import_graph.keys())
        for i, m1 in enumerate(modules):
            for m2 in modules[i+1:]:
                if m2 in import_graph.get(m1, set()) and m1 in import_graph.get(m2, set()):
                    tightly_coupled.append(f"{m1} ↔ {m2}")

        结果["coupling_metrics"] = {
            "avg_fan_out": round(avg_fan_out, 2),
            "max_fan_out": max_fan_out,
            "tightly_coupled_pairs": tightly_coupled[:5]
        }

        # 计算模块化评分 (0-100)
        # 扣分因素：循环依赖(-15/个)、高耦合对(-10/对)、高扇出(-5/超过5的文件)、超大文件(-5/超500行的文件)
        score = 100
        score -= len(cycles) * 15
        score -= len(tightly_coupled) * 10
        score -= sum(1 for fo in fan_outs if fo > 5) * 5
        score -= sum(1 for info in file_info.values() if info["lines"] > 500) * 5
        结果["modularity_score"] = max(0, min(100, score))

        # 文件指标（只取最重要的）
        结果["file_metrics"] = sorted(
            list(file_info.values()),
            key=lambda x: x["lines"],
            reverse=True
        )[:10]

    except Exception:
        # 完全降级
        pass

    return 结果


async def handle_code_review(request):
    """AI 代码审查：扫描插件源码 → 构建审查提示词 → 调用 LLM → 返回结构化审查报告"""
    try:
        client_ip = request.remote or "unknown"
        if not _rate_limiter.is_allowed(client_ip):
            return _error_response("请求过于频繁，请稍后重试", 429)

        data = await request.json()
        plugin_name = (data.get("plugin_path") or "").strip()
        review_depth = (data.get("review_depth") or "standard").strip()

        if not plugin_name:
            return web.json_response({"success": False, "error": "缺少 plugin_path 参数"}, status=400)

        if review_depth not in ("quick", "standard", "deep"):
            review_depth = "standard"

        plugin_path = _resolve_plugin_path(plugin_name)
        if not plugin_path.exists():
            return web.json_response({"success": False, "error": f"插件路径不存在: {plugin_path}"}, status=404)

        # 1. 扫描文件列表
        from .插件分析器 import scan_directory
        all_files = await asyncio.to_thread(scan_directory, plugin_path)

        review_files = _筛选审查文件(all_files, review_depth)
        if not review_files:
            return web.json_response({
                "success": False,
                "error": f"未找到可审查的文件（深度: {review_depth}）",
            }, status=400)

        # 2. 读取源码
        max_chars = 6000 if review_depth == "deep" else 10000
        file_summaries = []
        for rel_path in review_files:
            full_path = plugin_path / rel_path
            try:
                content = await asyncio.to_thread(
                    full_path.read_text, encoding="utf-8", errors="ignore"
                )
                if len(content) > max_chars:
                    content = content[:max_chars] + f"\n...[文件已截断，原始长度 {len(content)} 字符]"
                file_summaries.append({
                    "path": rel_path,
                    "content": content,
                    "size": len(content),
                })
            except Exception as e:
                logger.warning(f"读取审查文件 {rel_path} 失败: {e}")

        if not file_summaries:
            return web.json_response({"success": False, "error": "所有文件读取失败"}, status=500)

        # 3. RAG 检索审查清单知识库
        # 审查深度映射到知识库检索条数：quick=1 / standard=3 / deep=5
        review_knowledge = ""
        if tool_router is not None:
            知识库_top_k = {"quick": 1, "standard": 3, "deep": 5}.get(review_depth, 3)
            review_knowledge = tool_router.retrieve_knowledge(
                "代码质量审查 安全性 性能 代码规范 ComfyUI节点规范 可维护性 输入验证 路径遍历 "
                "前端Widget布局 computeSize 排版 DOM面板叠压",
                active_tab="optimize",
                top_k=知识库_top_k,
            )

        # 3.5 执行静态分析
        静态分析结果 = await _执行静态分析(plugin_path)

        # 前端 Widget 布局启发式检查
        布局发现 = await asyncio.to_thread(_检查前端布局, plugin_path)

        # 执行架构分析
        架构分析结果 = await _分析模块架构(plugin_path)

        # 4. 构建审查提示词
        system_prompt, user_message = _构建审查提示词(file_summaries, review_knowledge, review_depth)

        # 4.5 如果有静态分析数据，追加到提示词
        if 静态分析结果:
            分析参考 = "\n\n## 静态分析参考数据（供辅助判断，非强制要求）\n"

            if 静态分析结果.get("lint"):
                lint = 静态分析结果["lint"]
                分析参考 += "### Lint 检查结果\n"
                分析参考 += f"- 总问题数：{lint['total_issues']}（错误：{lint['errors']}，警告：{lint['warnings']}）\n"
                if lint.get("top_issues"):
                    分析参考 += "- 主要问题：\n"
                    for issue in lint["top_issues"][:5]:
                        分析参考 += f"  - {issue}\n"

            if 静态分析结果.get("complexity"):
                cc = 静态分析结果["complexity"]
                分析参考 += "### 复杂度分析\n"
                分析参考 += f"- 平均圈复杂度：{cc['avg_complexity']}\n"
                分析参考 += f"- 最高复杂度：{cc['max_complexity']}\n"
                if cc.get("high_risk_functions"):
                    分析参考 += "- 高风险函数（McCabe>7）：\n"
                    for func in cc["high_risk_functions"]:
                        分析参考 += f"  - {func}\n"

            if 静态分析结果.get("security"):
                sec = 静态分析结果["security"]
                分析参考 += "### 依赖安全扫描\n"
                分析参考 += f"- 已知漏洞数：{sec['vulnerabilities']}\n"
                if sec.get("details"):
                    for detail in sec["details"]:
                        分析参考 += f"  - {detail}\n"

            if 布局发现:
                分析参考 += "### 前端 Widget 布局启发式检查（疑似问题，请结合代码确认）\n"
                for 发现项 in 布局发现:
                    位置 = f"{发现项['file']}:{发现项['line']}" if 发现项.get("line") else 发现项["file"]
                    分析参考 += f"- [{发现项['pattern']}] {位置}：{发现项['description']}\n"
                分析参考 += "确认属实的请归入 category=frontend_layout 的 issue。\n"

            # 追加到系统提示词
            system_prompt += 分析参考

        # 4.6 如果有架构分析数据，追加到提示词
        if 架构分析结果 and 架构分析结果.get("modularity_score") is not None:
            分析参考 = "\n### 架构分析\n"
            分析参考 += f"- 模块化评分：{架构分析结果['modularity_score']}/100\n"

            if 架构分析结果.get("circular_dependencies"):
                分析参考 += "- 循环依赖检测：\n"
                for cycle in 架构分析结果["circular_dependencies"]:
                    分析参考 += f"  - {cycle}\n"

            if 架构分析结果.get("coupling_metrics"):
                cm = 架构分析结果["coupling_metrics"]
                分析参考 += f"- 平均扇出：{cm['avg_fan_out']}，最大扇出：{cm['max_fan_out']}\n"
                if cm.get("tightly_coupled_pairs"):
                    分析参考 += "- 紧耦合模块对：\n"
                    for pair in cm["tightly_coupled_pairs"]:
                        分析参考 += f"  - {pair}\n"

            if 架构分析结果.get("file_metrics"):
                大文件 = [f for f in 架构分析结果["file_metrics"] if f["lines"] > 300]
                if 大文件:
                    分析参考 += "- 大文件（>300行）：\n"
                    for f in 大文件[:5]:
                        分析参考 += f"  - {f['file']}（{f['lines']}行，导入{f['imports_count']}个模块）\n"

            system_prompt += 分析参考

        # 5. 加载设置并调用 LLM
        settings = await asyncio.to_thread(load_settings)
        model_source = settings.get("model_source", "api")
        messages = [{"role": "user", "content": user_message}]

        if model_source == "local" and local_model_client is not None:
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
                return web.json_response({
                    "success": False,
                    "error": "未检测到可用的本地模型，请在设置中配置模型路径",
                }, status=400)
            try:
                if local_model_client.当前模型名 != Path(local_path).name:
                    await local_model_client.异步加载模型(local_path)
            except Exception as e:
                return web.json_response({
                    "success": False,
                    "error": f"模型加载失败: {str(e)}",
                }, status=500)
            reply = await local_model_client.generate_response(system_prompt, messages)
        else:
            reply = await llm_client.generate_response(
                system_prompt, messages, response_format={"type": "json_object"}
            )

        # 6. 解析审查结果
        review_result = _解析审查结果(reply)

        # 附加审查元信息
        review_result["review_depth"] = review_depth
        review_result["files_reviewed"] = len(file_summaries)
        review_result["file_list"] = [f["path"] for f in file_summaries]

        # 云端知识库检索失败时附加降级提醒字段（正常时不新增字段，保持向后兼容）
        if tool_router is not None and getattr(tool_router, "kb_retrieval_failed", False):
            from 智能体.工具路由器 import kb_failure_message
            _kb_reason = getattr(tool_router, "kb_failure_reason", "error") or "error"
            review_result["kb_retrieval_status"] = "degraded"
            review_result["kb_message"] = kb_failure_message(_kb_reason)

        return web.json_response({"success": True, "review": review_result})
    except Exception as e:
        logger.error(f"代码审查异常: {e}", exc_info=True)
        return web.json_response({"success": False, "error": 服务器内部错误}, status=500)


# ─── Profiling 数据分析 ─────────────────────────────────────

async def _分析性能数据(profiling_text: str) -> dict:
    """
    解析用户粘贴的 cProfile/py-spy 输出，提取热点函数。
    返回结构化的性能热点信息。
    """
    热点 = {
        "top_functions": [],  # [{name, cumtime, calls, per_call}]
        "summary": "",
        "bottleneck": ""
    }

    try:
        lines = profiling_text.strip().split("\n")

        # 尝试解析 cProfile 格式
        # 格式: ncalls  tottime  percall  cumtime  percall filename:lineno(function)
        data_lines = []
        header_found = False
        for line in lines:
            if "ncalls" in line and "tottime" in line:
                header_found = True
                continue
            if header_found and line.strip():
                parts = line.split()
                if len(parts) >= 6:
                    try:
                        ncalls = parts[0].split("/")[0]  # 处理 "3/1" 格式
                        cumtime = float(parts[3])
                        func_name = " ".join(parts[5:])
                        data_lines.append({
                            "name": func_name,
                            "cumtime": cumtime,
                            "calls": int(ncalls) if ncalls.isdigit() else 0,
                            "per_call": float(parts[4]) if parts[4].replace('.', '').replace('-', '').isdigit() else 0
                        })
                    except (ValueError, IndexError):
                        continue

        # 按累计时间排序取 top 10
        data_lines.sort(key=lambda x: x["cumtime"], reverse=True)
        热点["top_functions"] = data_lines[:10]

        if data_lines:
            热点["bottleneck"] = data_lines[0]["name"]
            热点["summary"] = (
                f"最耗时函数: {data_lines[0]['name']} "
                f"({data_lines[0]['cumtime']:.3f}s, "
                f"{data_lines[0]['calls']}次调用)"
            )

    except Exception:
        热点["summary"] = "无法解析性能数据格式"

    return 热点


async def handle_analyze_profiling(request):
    """分析性能 Profiling 数据"""
    try:
        client_ip = request.remote or "unknown"
        if not _rate_limiter.is_allowed(client_ip):
            return _error_response("请求过于频繁，请稍后重试", 429)

        data = await request.json()
        profiling_text = (data.get("profiling_data") or "").strip()

        if not profiling_text:
            return web.json_response({"success": False, "error": "缺少 profiling_data 参数"}, status=400)

        if len(profiling_text) > 100000:
            return web.json_response({"success": False, "error": "数据过大，请截取关键部分（最大100KB）"}, status=400)

        热点 = await _分析性能数据(profiling_text)

        return web.json_response({
            "success": True,
            "analysis": 热点
        })
    except Exception as e:
        logger.error(f"分析性能数据异常: {e}", exc_info=True)
        return web.json_response({"success": False, "error": 服务器内部错误}, status=500)


def register_代码审查路由(routes):
    """注册代码审查与性能基准端点"""
    routes.post("/ai-coder/code-review")(handle_code_review)
    routes.post("/ai-coder/benchmark/analyze")(handle_analyze_profiling)
