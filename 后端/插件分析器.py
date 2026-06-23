"""
插件分析器 — 解析插件目录结构与文件依赖关系
返回 3D 可视化所需的 graphData 格式
"""
import ast
import re
from pathlib import Path


# 忽略的目录
IGNORED_DIRS = {
    '__pycache__', '.git', 'node_modules', '.venv', 'venv',
    '.eggs', '*.egg-info', '.tox', '.mypy_cache'
}


def get_file_type(filepath: Path) -> str:
    """根据文件扩展名确定节点类型"""
    suffix = filepath.suffix.lower()
    name = filepath.name
    if name == '__init__.py':
        return 'entry'
    if suffix == '.py':
        return 'python'
    if suffix in ('.js', '.ts', '.jsx', '.tsx'):
        return 'javascript'
    if suffix in ('.json', '.yaml', '.yml', '.toml', '.cfg', '.ini'):
        return 'config'
    return 'other'


def should_ignore_dir(dir_name: str) -> bool:
    """检查目录是否应被忽略"""
    return dir_name in IGNORED_DIRS or dir_name.startswith('.')


def scan_directory(plugin_path: Path) -> list:
    """扫描目录树，返回所有有效文件的相对路径列表"""
    files = []
    for item in plugin_path.rglob('*'):
        # 检查是否在忽略目录中
        parts = item.relative_to(plugin_path).parts
        if any(should_ignore_dir(p) for p in parts[:-1]):
            continue
        if item.is_file():
            rel_path = str(item.relative_to(plugin_path)).replace('\\', '/')
            files.append(rel_path)
    return files


def parse_python_imports(filepath: Path, plugin_path: Path) -> list:
    """解析 Python 文件中的 import 语句，返回依赖列表"""
    imports = []
    try:
        source = filepath.read_text(encoding='utf-8', errors='ignore')
        tree = ast.parse(source)
    except (SyntaxError, UnicodeDecodeError):
        return imports

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                imports.append({
                    'module': alias.name,
                    'type': 'import'
                })
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                imports.append({
                    'module': node.module,
                    'type': 'from_import',
                    'level': node.level  # 相对导入层级
                })
    return imports


def parse_js_imports(filepath: Path) -> list:
    """解析 JavaScript/TypeScript 文件中的 import/require 语句"""
    imports = []
    try:
        source = filepath.read_text(encoding='utf-8', errors='ignore')
    except Exception:
        return imports

    # ES6 import: import ... from "..."  或  import "..."
    es6_pattern = r'''import\s+(?:[\w{}\s,*]+\s+from\s+)?['"]([^'"]+)['"]'''
    for match in re.finditer(es6_pattern, source):
        imports.append({'module': match.group(1), 'type': 'es6_import'})

    # CommonJS require: require("...")
    cjs_pattern = r'''require\s*\(\s*['"]([^'"]+)['"]\s*\)'''
    for match in re.finditer(cjs_pattern, source):
        imports.append({'module': match.group(1), 'type': 'require'})

    return imports


def resolve_python_import(import_info: dict, source_file: Path, plugin_path: Path, all_files: set) -> str | None:
    """尝试将 Python import 解析为插件内的具体文件路径"""
    module = import_info['module']
    level = import_info.get('level', 0)

    if level > 0:
        # 相对导入：从当前文件目录向上 level-1 层
        current_dir = source_file.parent
        for _ in range(level - 1):
            current_dir = current_dir.parent

        parts = module.split('.') if module else []
        target = current_dir
        for part in parts:
            target = target / part

        # 尝试匹配 .py 文件或 __init__.py
        candidates = [
            str((target.with_suffix('.py')).relative_to(plugin_path)).replace('\\', '/'),
            str((target / '__init__.py').relative_to(plugin_path)).replace('\\', '/'),
        ]
        for c in candidates:
            if c in all_files:
                return c
    else:
        # 绝对导入：尝试在插件内查找
        parts = module.split('.')
        # 尝试作为文件
        rel_path = '/'.join(parts) + '.py'
        if rel_path in all_files:
            return rel_path
        # 尝试作为包
        rel_path = '/'.join(parts) + '/__init__.py'
        if rel_path in all_files:
            return rel_path

    return None


def resolve_js_import(import_info: dict, source_file: Path, plugin_path: Path, all_files: set) -> str | None:
    """尝试将 JS import 解析为插件内的具体文件路径"""
    module = import_info['module']

    # 只处理相对路径（./或../开头）
    if not module.startswith('.'):
        return None  # 外部依赖

    # 解析相对路径
    source_dir = source_file.parent
    target = (source_dir / module).resolve()

    try:
        rel = str(target.relative_to(plugin_path)).replace('\\', '/')
    except ValueError:
        return None

    # 尝试各种扩展名
    candidates = [rel]
    if not Path(rel).suffix:
        candidates.extend([
            rel + '.js', rel + '.ts', rel + '.jsx', rel + '.tsx',
            rel + '/index.js', rel + '/index.ts'
        ])

    for c in candidates:
        if c in all_files:
            return c

    return None


def detect_circular_dependencies(links: list) -> list:
    """使用 DFS 检测循环依赖，返回参与循环的连线索引列表"""
    # 构建邻接表
    graph = {}
    for i, link in enumerate(links):
        source = link['source']
        if source not in graph:
            graph[source] = []
        graph[source].append((link['target'], i))

    circular_link_indices = set()
    visited = set()
    rec_stack = set()

    def dfs(node, path):
        visited.add(node)
        rec_stack.add(node)

        for neighbor, link_idx in graph.get(node, []):
            if neighbor not in visited:
                if dfs(neighbor, path + [link_idx]):
                    circular_link_indices.add(link_idx)
                    return True
            elif neighbor in rec_stack:
                circular_link_indices.add(link_idx)
                return True

        rec_stack.discard(node)
        return False

    for node in graph:
        if node not in visited:
            dfs(node, [])

    return list(circular_link_indices)


def analyze_plugin(plugin_path_str: str) -> dict:
    """
    分析插件依赖关系的主入口函数

    参数:
        plugin_path_str: 插件目录的完整路径字符串

    返回:
        {
            "nodes": [...],   # 节点列表
            "links": [...],   # 连线列表
            "stats": {        # 统计信息
                "total_files": int,
                "error_count": int,
                "circular_deps": int
            }
        }
    """
    plugin_path = Path(plugin_path_str)
    if not plugin_path.exists():
        return {"nodes": [], "links": [], "stats": {"total_files": 0, "error_count": 0, "circular_deps": 0}}

    # 1. 扫描所有文件
    all_file_paths = scan_directory(plugin_path)
    all_files_set = set(all_file_paths)

    # 2. 生成节点列表
    nodes = []
    for rel_path in all_file_paths:
        full_path = plugin_path / rel_path
        file_type = get_file_type(full_path)
        if file_type == 'other':
            continue  # 跳过不支持的文件类型

        try:
            size = full_path.stat().st_size
        except OSError:
            size = 0

        # 确定分组（第一层目录名）
        parts = rel_path.split('/')
        group = parts[0] if len(parts) > 1 else '根目录'

        nodes.append({
            "id": rel_path,
            "name": Path(rel_path).name,
            "type": file_type,
            "size": size,
            "status": "normal",
            "group": group
        })

    # 3. 解析依赖，生成连线
    links = []
    error_count = 0

    for rel_path in all_file_paths:
        full_path = plugin_path / rel_path
        file_type = get_file_type(full_path)

        if file_type in ('python', 'entry'):
            imports = parse_python_imports(full_path, plugin_path)
            for imp in imports:
                target = resolve_python_import(imp, full_path, plugin_path, all_files_set)
                if target:
                    links.append({
                        "source": rel_path,
                        "target": target,
                        "type": "import",
                        "status": "normal"
                    })
                elif imp.get('level', 0) > 0:
                    # 相对导入但找不到目标 = 错误
                    links.append({
                        "source": rel_path,
                        "target": f"[未找到] {imp['module']}",
                        "type": "import",
                        "status": "error"
                    })
                    error_count += 1

        elif file_type == 'javascript':
            imports = parse_js_imports(full_path)
            for imp in imports:
                target = resolve_js_import(imp, full_path, plugin_path, all_files_set)
                if target:
                    links.append({
                        "source": rel_path,
                        "target": target,
                        "type": "import",
                        "status": "normal"
                    })
                elif imp['module'].startswith('.'):
                    # 相对路径但找不到 = 错误
                    links.append({
                        "source": rel_path,
                        "target": f"[未找到] {imp['module']}",
                        "type": "import",
                        "status": "error"
                    })
                    error_count += 1

    # 4. 检测循环依赖
    circular_indices = detect_circular_dependencies(links)
    for idx in circular_indices:
        links[idx]['status'] = 'error'
    circular_count = len(circular_indices)
    error_count += circular_count

    # 5. 更新有错误连线的节点状态
    error_sources = set()
    for link in links:
        if link['status'] == 'error':
            error_sources.add(link['source'])

    for node in nodes:
        if node['id'] in error_sources:
            node['status'] = 'error'

    return {
        "nodes": nodes,
        "links": [l for l in links if not l['target'].startswith('[未找到]') or l['status'] == 'error'],
        "stats": {
            "total_files": len(nodes),
            "error_count": error_count,
            "circular_deps": circular_count
        }
    }
