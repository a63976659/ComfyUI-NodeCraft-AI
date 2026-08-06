"""
项目级上下文感知分析模块。
自动分析插件目录结构，生成结构化摘要注入对话上下文。
"""

import ast
import logging
import re
from pathlib import Path

logger = logging.getLogger("NodeCraftAI")


class 项目上下文分析器:
    """分析插件项目结构，生成 AI 可用的上下文摘要"""

    def __init__(self):
        self._缓存 = {}  # 路径 -> (摘要文本, 修改时间)

    def 分析项目(self, plugin_path: Path, max_tokens: int = 800) -> str:
        """
        分析插件项目，返回结构化上下文摘要。

        Args:
            plugin_path: 插件根目录路径
            max_tokens: 摘要最大 token 数（约等于字符数/3.5）

        Returns:
            结构化摘要文本，可注入系统提示词
        """
        # 检查缓存（路径+修改时间）
        cache_key = str(plugin_path)
        try:
            latest_mtime = max(
                f.stat().st_mtime for f in plugin_path.rglob("*") if f.is_file()
            )
        except (ValueError, OSError):
            latest_mtime = 0

        if cache_key in self._缓存:
            cached_text, cached_mtime = self._缓存[cache_key]
            if cached_mtime == latest_mtime:
                return cached_text

        # 分析项目
        摘要部分 = []

        # 1. 文件树结构
        文件树 = self._生成文件树(plugin_path)
        摘要部分.append(f"## 项目结构\n{文件树}")

        # 2. 节点信息（从 __init__.py 提取）
        节点信息 = self._提取节点信息(plugin_path)
        if 节点信息:
            摘要部分.append(f"## 自定义节点\n{节点信息}")

        # 3. 依赖关系（import 分析）
        依赖关系 = self._分析依赖(plugin_path)
        if 依赖关系:
            摘要部分.append(f"## 模块依赖\n{依赖关系}")

        # 4. 关键函数/类（从主文件提取）
        关键定义 = self._提取关键定义(plugin_path)
        if 关键定义:
            摘要部分.append(f"## 关键定义\n{关键定义}")

        # 5. 前端文件（如果有）
        前端信息 = self._分析前端文件(plugin_path)
        if 前端信息:
            摘要部分.append(f"## 前端文件\n{前端信息}")

        # 合并并控制长度
        摘要文本 = "\n\n".join(摘要部分)
        max_chars = int(max_tokens * 3.5)  # 粗略 token 到字符的转换

        if len(摘要文本) > max_chars:
            摘要文本 = 摘要文本[:max_chars] + "\n\n[... 项目摘要已截断 ...]"

        # 缓存
        self._缓存[cache_key] = (摘要文本, latest_mtime)

        return 摘要文本

    def _生成文件树(self, plugin_path: Path, max_depth: int = 3) -> str:
        """生成简化的文件树（自动跳过虚拟环境目录）"""
        lines = []

        # 检测虚拟环境目录
        env_dirs = self._检测虚拟环境(plugin_path)

        def _递归遍历(path: Path, prefix: str, depth: int):
            if depth > max_depth:
                return

            try:
                entries = sorted(
                    path.iterdir(), key=lambda p: (not p.is_dir(), p.name)
                )
            except OSError:
                return

            # 过滤不需要的目录/文件（含运行时数据目录与分析产物目录）
            skip = {
                "__pycache__",
                ".git",
                "node_modules",
                ".venv",
                "venv",
                "数据",
                "logs",
                "可视化",
            }
            entries = [
                e for e in entries if e.name not in skip and not e.name.startswith(".")
            ]

            for i, entry in enumerate(entries):
                is_last = i == len(entries) - 1
                connector = "└── " if is_last else "├── "

                if entry.is_dir():
                    # 虚拟环境目录折叠为单行（支持嵌套路径）
                    entry_rel = entry.relative_to(plugin_path).as_posix()
                    if entry_rel in env_dirs:
                        file_count = sum(1 for _ in entry.rglob('*') if _.is_file())
                        lines.append(f"{prefix}{connector}{entry.name}/ (虚拟环境, {file_count} 文件, 已折叠)")
                        continue
                    lines.append(f"{prefix}{connector}{entry.name}/")
                    _递归遍历(
                        entry, prefix + ("    " if is_last else "│   "), depth + 1
                    )
                else:
                    size = entry.stat().st_size
                    size_str = f" ({size // 1024}KB)" if size > 1024 else ""
                    lines.append(f"{prefix}{connector}{entry.name}{size_str}")

        _递归遍历(plugin_path, "", 0)
        return "\n".join(lines) if lines else "（空目录）"

    def _提取节点信息(self, plugin_path: Path) -> str:
        """从 __init__.py 提取 NODE_CLASS_MAPPINGS 和 NODE_DISPLAY_NAME_MAPPINGS"""
        init_file = plugin_path / "__init__.py"
        if not init_file.exists():
            return ""

        try:
            content = init_file.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            return ""

        lines = []

        # 尝试 AST 解析提取 NODE_CLASS_MAPPINGS
        try:
            tree = ast.parse(content)
            for node in ast.walk(tree):
                if isinstance(node, ast.Assign):
                    for target in node.targets:
                        if (
                            isinstance(target, ast.Name)
                            and target.id == "NODE_CLASS_MAPPINGS"
                        ):
                            if isinstance(node.value, ast.Dict):
                                lines.append("注册的节点:")
                                for key, value in zip(
                                    node.value.keys, node.value.values
                                ):
                                    node_name = (
                                        ast.literal_eval(key)
                                        if isinstance(key, ast.Constant)
                                        else str(key)
                                    )
                                    node_class = (
                                        value.id
                                        if isinstance(value, ast.Name)
                                        else ast.dump(value)
                                    )
                                    lines.append(f"  - {node_name} → {node_class}")
        except SyntaxError:
            # AST 解析失败，回退到正则
            match = re.search(
                r"NODE_CLASS_MAPPINGS\s*=\s*\{([^}]+)\}", content, re.DOTALL
            )
            if match:
                lines.append("注册的节点:")
                for line in match.group(1).strip().split("\n"):
                    line = line.strip().rstrip(",")
                    if line:
                        lines.append(f"  - {line}")

        # WEB_DIRECTORY
        web_match = re.search(r'WEB_DIRECTORY\s*=\s*["\']([^"\']+)["\']', content)
        if web_match:
            lines.append(f"前端目录: {web_match.group(1)}")

        return "\n".join(lines) if lines else ""

    def _分析依赖(self, plugin_path: Path) -> str:
        """分析 Python 文件之间的 import 依赖"""
        lines = []

        for py_file in sorted(plugin_path.glob("*.py")):
            try:
                content = py_file.read_text(encoding="utf-8")
                tree = ast.parse(content)
            except (OSError, UnicodeDecodeError, SyntaxError):
                continue

            imports = []
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        imports.append(alias.name)
                elif isinstance(node, ast.ImportFrom):
                    if node.module:
                        imports.append(node.module)

            # 只记录项目内部依赖和重要外部依赖
            local_imports = [
                i for i in imports if not i.startswith("_") and not i.startswith(".")
            ]
            if local_imports:
                # 去重
                unique = sorted(set(local_imports))
                lines.append(
                    f"  {py_file.name}: {', '.join(unique[:8])}"
                )  # 最多8个

        return "\n".join(lines) if lines else ""

    def _提取关键定义(self, plugin_path: Path) -> str:
        """从 Python 文件中提取类和函数定义"""
        lines = []

        for py_file in sorted(plugin_path.glob("*.py")):
            if py_file.name == "__init__.py":
                continue

            try:
                content = py_file.read_text(encoding="utf-8")
                tree = ast.parse(content)
            except (OSError, UnicodeDecodeError, SyntaxError):
                continue

            definitions = []
            for node in ast.iter_child_nodes(tree):
                if isinstance(node, ast.ClassDef):
                    # 提取类的 docstring
                    docstring = ast.get_docstring(node) or ""
                    docstring = docstring.split("\n")[0][
                        :80
                    ]  # 第一行，最多80字符
                    definitions.append(f"  class {node.name}")
                    if docstring:
                        definitions.append(f"    # {docstring}")

                    # 提取方法
                    for item in node.body:
                        if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                            definitions.append(f"    def {item.name}()")

                elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    definitions.append(f"  def {node.name}()")

            if definitions:
                lines.append(f"### {py_file.name}")
                lines.extend(definitions)

        return "\n".join(lines) if lines else ""

    def _分析前端文件(self, plugin_path: Path) -> str:
        """分析前端 JS 文件（排除虚拟环境目录）"""
        env_dirs = self._检测虚拟环境(plugin_path)
        js_files = list(plugin_path.rglob("*.js"))
        if not js_files:
            return ""

        lines = []
        for js_file in sorted(js_files):
            # 跳过虚拟环境目录下的文件（支持嵌套路径前缀匹配）
            rel_parts = js_file.relative_to(plugin_path).parts
            if any("/".join(rel_parts[:i]) in env_dirs for i in range(1, len(rel_parts))):
                continue
            if js_file.name.startswith(".") or "node_modules" in str(js_file):
                continue

            try:
                content = js_file.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue

            # 提取函数定义
            funcs = re.findall(r"(?:async\s+)?function\s+(\w+)", content)
            # 提取类定义
            classes = re.findall(r"class\s+(\w+)", content)

            rel_path = js_file.relative_to(plugin_path)
            info_parts = []
            if classes:
                info_parts.append(f"classes: {', '.join(classes[:5])}")
            if funcs:
                info_parts.append(f"functions: {', '.join(funcs[:8])}")

            if info_parts:
                lines.append(f"  {rel_path} ({', '.join(info_parts)})")

        return "\n".join(lines) if lines else ""

    def _检测虚拟环境(self, plugin_path: Path) -> set:
        """检测插件目录下的虚拟环境子目录（支持任意嵌套深度），返回相对路径集合"""
        env_dirs = set()

        def _walk(current: Path, rel_prefix: str):
            try:
                entries = sorted(current.iterdir())
            except OSError:
                return
            for item in entries:
                if not item.is_dir() or item.name.startswith('.'):
                    continue
                if item.name in {"__pycache__", "node_modules", ".git", "venv", ".venv"}:
                    continue
                rel = f"{rel_prefix}/{item.name}" if rel_prefix else item.name
                # 锚点：pyvenv.cfg（任意深度有效）
                if (item / 'pyvenv.cfg').exists():
                    env_dirs.add(rel)
                    continue
                # 结构启发仅限一级目录
                if not rel_prefix:
                    if (item / 'Lib' / 'site-packages').exists():
                        env_dirs.add(rel)
                        continue
                    lib_dir = item / 'lib'
                    if lib_dir.exists():
                        try:
                            has_python = any(
                                d.name.startswith('python')
                                for d in lib_dir.iterdir()
                                if d.is_dir()
                            )
                        except OSError:
                            has_python = False
                        if has_python:
                            env_dirs.add(rel)
                            continue
                _walk(item, rel)

        _walk(plugin_path, "")
        return env_dirs

    def 清除缓存(self, plugin_path: str = None):
        """清除缓存"""
        if plugin_path:
            self._缓存.pop(plugin_path, None)
        else:
            self._缓存.clear()
