"""
插件分析器 — 解析插件目录结构与文件依赖关系
返回 3D 可视化所需的 graphData 格式
"""
import ast
import re
import sys
from pathlib import Path

# 忽略的目录
IGNORED_DIRS = {
    '__pycache__', '.git', 'node_modules', '.venv', 'venv',
    '.eggs', '*.egg-info', '.tox', '.mypy_cache'
}

# ComfyUI 节点类必需的属性
NODE_REQUIRED_ATTRS = ['INPUT_TYPES', 'RETURN_TYPES', 'FUNCTION', 'CATEGORY']

# 过大文件阈值（字节）
LARGE_FILE_THRESHOLD = 500 * 1024

# 常见 Python 标准库白名单（用于 sys.stdlib_module_names 不可用时的回退）
STDLIB_WHITELIST = {
    'os', 'sys', 'ast', 're', 'json', 'pathlib', 'typing', 'collections',
    'functools', 'itertools', 'math', 'datetime', 'time', 'threading',
    'asyncio', 'logging', 'io', 'hashlib', 'base64', 'abc', 'copy',
    'shutil', 'subprocess', 'traceback', 'inspect', 'importlib',
    'contextlib', 'dataclasses', 'enum', 'uuid', 'tempfile', 'glob',
    'struct', 'operator', 'textwrap', 'urllib', 'http', 'socket',
    'pickle', 'sqlite3', 'csv', 'xml', 'html', 'configparser',
    'argparse', 'unittest', 'pdb', 'string', 'codecs', 'warnings',
    'random', 'decimal', 'fractions', 'statistics', 'queue', 'select',
    'signal', 'platform', 'ctypes', 'array', 'bisect', 'heapq', 'weakref',
    'gc', 'types', 'numbers', 'zlib', 'gzip', 'zipfile', 'tarfile',
    'binascii', 'secrets', 'hmac', 'ssl', 'ftplib', 'smtplib', 'email',
    'mimetypes', 'json', 'concurrent', 'multiprocessing', 'ipaddress',
    'difflib', 'pprint', 'shlex', 'fnmatch', 'stat', 'errno', 'locale',
    'gettext', 'calendar', 'zoneinfo', 'builtins', '__future__',
}

# ComfyUI 运行环境自带的包，插件不需要在 requirements.txt 中声明
COMFYUI_ENV_PACKAGES = {
    # PyTorch 生态
    'torch', 'torchvision', 'torchaudio', 'einops',
    # 科学计算
    'numpy', 'scipy', 'sklearn', 'scikit_learn',
    # 图像处理
    'PIL', 'pillow', 'cv2', 'skimage',
    # ComfyUI 内部模块
    'comfy', 'comfy_extras', 'folder_paths', 'nodes', 'execution',
    'server', 'model_management', 'latent_preview',
    # ComfyUI 常用依赖
    'aiohttp', 'yaml', 'tqdm', 'safetensors', 'transformers',
    'diffusers', 'accelerate', 'huggingface_hub',
    # 其他常见预装
    'requests', 'websocket', 'kornia', 'spandrel',
}


def get_stdlib_modules() -> set:
    """返回标准库模块名集合（小写），优先使用 sys.stdlib_module_names"""
    names = set(getattr(sys, 'stdlib_module_names', ()) or ())
    names |= STDLIB_WHITELIST
    return {n.lower() for n in names}


def _mark_node(node: dict, status: str, reason: str) -> None:
    """按优先级规则标记节点状态与原因。
    优先级 error > warning > normal：status 只升级不降级；
    reason 追加（用 ； 分隔）。"""
    priority = {'normal': 0, 'warning': 1, 'error': 2}
    if reason:
        if node.get('reason'):
            node['reason'] = node['reason'] + '；' + reason
        else:
            node['reason'] = reason
    if priority.get(status, 0) > priority.get(node.get('status', 'normal'), 0):
        node['status'] = status


def read_text_with_encoding(full_path: Path) -> tuple:
    """读取文本文件并检测编码问题（维度 4d）。
    返回 (source, status, reason)。status 取值：'ok' | 'warning' | 'error'。"""
    try:
        raw = full_path.read_bytes()
    except Exception:
        return None, 'ok', ''
    try:
        return raw.decode('utf-8'), 'ok', ''
    except UnicodeDecodeError:
        pass
    try:
        source = raw.decode('gbk')
        return source, 'warning', '文件编码为 GBK 而非 UTF-8，建议转换为 UTF-8 以避免跨平台问题'
    except (UnicodeDecodeError, LookupError):
        return None, 'error', '文件编码异常，无法解析'


def check_node_spec(tree: ast.AST) -> str | None:
    """维度 2：检查文件中的类是否为完整的 ComfyUI 节点。
    仅当类定义了 FUNCTION 或 RETURN_TYPES 之一（说明它尝试做节点）
    但缺少其他必需属性时才报警。返回警告 reason 或 None。"""
    messages = []
    for cls in [n for n in ast.walk(tree) if isinstance(n, ast.ClassDef)]:
        present = set()
        for item in cls.body:
            if isinstance(item, ast.FunctionDef) and item.name == 'INPUT_TYPES':
                present.add('INPUT_TYPES')
            elif isinstance(item, ast.Assign):
                for t in item.targets:
                    if isinstance(t, ast.Name) and t.id in NODE_REQUIRED_ATTRS:
                        present.add(t.id)
            elif isinstance(item, ast.AnnAssign):
                if isinstance(item.target, ast.Name) and item.target.id in NODE_REQUIRED_ATTRS:
                    present.add(item.target.id)
        # 判断是否像节点类
        if 'FUNCTION' in present or 'RETURN_TYPES' in present:
            missing = [a for a in NODE_REQUIRED_ATTRS if a not in present]
            if missing:
                messages.append(f"类 {cls.name} 缺少 {', '.join(missing)}")
    if messages:
        return "节点规范不完整：" + "；".join(messages)
    return None


def extract_top_imports(tree: ast.AST) -> set:
    """提取顶层第三方导入的一级模块名（跳过相对导入）"""
    modules = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                top = alias.name.split('.')[0]
                if top:
                    modules.add(top)
        elif isinstance(node, ast.ImportFrom):
            if node.level and node.level > 0:
                continue  # 相对导入
            if node.module:
                top = node.module.split('.')[0]
                if top:
                    modules.add(top)
    return modules


def parse_requirements(plugin_path: Path) -> set:
    """解析 requirements.txt 中已声明的包名（小写，去掉版本后缀）"""
    declared = set()
    req = plugin_path / 'requirements.txt'
    if not req.exists():
        return declared
    try:
        for line in req.read_text(encoding='utf-8', errors='ignore').splitlines():
            line = line.strip()
            if not line or line.startswith('#') or line.startswith('-'):
                continue
            name = re.split(r'[<>=!~;\[\s]', line, 1)[0].strip()
            if name:
                declared.add(name.lower())
                declared.add(name.lower().replace('-', '_'))
    except Exception:
        pass
    return declared


def check_undeclared_deps(tree: ast.AST, declared: set, internal: set, stdlib: set) -> str | None:
    """维度 4a：检测未在 requirements.txt 中声明的第三方依赖"""
    # ComfyUI 环境自带包白名单（小写，含常见别名）
    env_packages = {p.lower() for p in COMFYUI_ENV_PACKAGES}
    undeclared = []
    for m in sorted(extract_top_imports(tree)):
        low = m.lower()
        if low in stdlib:
            continue
        # 过滤 ComfyUI 环境自带包
        if low in env_packages:
            continue
        if m in internal or low in internal:
            continue
        if low in declared:
            continue
        undeclared.append(m)
    if undeclared:
        return f"未声明的第三方依赖：{', '.join(undeclared)}（未在 requirements.txt 中声明）"
    return None


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


def detect_virtualenv_dirs(plugin_path: Path) -> list:
    """检测插件目录下的虚拟环境目录。

    检测策略（满足任一即判定为虚拟环境）：
    1. 目录内直接包含 pyvenv.cfg
    2. 目录内直接包含 Lib/site-packages 或 lib/python* 结构

    返回直接子目录名称列表（如 ['asr_env', 'gemma_env', 'llm_env']）
    """
    env_dirs = []
    try:
        for item in plugin_path.iterdir():
            if not item.is_dir():
                continue
            if item.name in IGNORED_DIRS or item.name.startswith('.'):
                continue
            # 策略1：pyvenv.cfg 标记文件
            if (item / 'pyvenv.cfg').exists():
                env_dirs.append(item.name)
                continue
            # 策略2：典型虚拟环境结构
            if (item / 'Lib' / 'site-packages').exists() or (item / 'lib').exists():
                # 进一步确认：lib 下是否有 python* 子目录
                lib_dir = item / 'lib'
                if lib_dir.exists():
                    has_python = any(
                        d.name.startswith('python')
                        for d in lib_dir.iterdir()
                        if d.is_dir()
                    )
                    if has_python:
                        env_dirs.append(item.name)
                        continue
                # Lib/site-packages 已存在即判定
                if (item / 'Lib' / 'site-packages').exists():
                    env_dirs.append(item.name)
    except OSError:
        pass
    return env_dirs


def _count_dir_files(dir_path: Path) -> int:
    """统计目录下的文件数量（快速计数）"""
    try:
        return sum(1 for _ in dir_path.rglob('*') if _.is_file())
    except OSError:
        return 0


def scan_directory(plugin_path: Path, env_dirs: list = None) -> list:
    """扫描目录树，返回所有有效文件的相对路径列表

    Args:
        plugin_path: 插件根目录
        env_dirs: 虚拟环境目录名列表，这些目录将被跳过
    """
    env_set = set(env_dirs or [])
    files = []
    for item in plugin_path.rglob('*'):
        # 检查是否在忽略目录中
        parts = item.relative_to(plugin_path).parts
        if any(should_ignore_dir(p) for p in parts[:-1]):
            continue
        # 跳过虚拟环境目录下的文件
        if env_set and any(p in env_set for p in parts[:-1]):
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


def detect_circular_dependencies(links: list) -> tuple:
    """使用 DFS 检测循环依赖，返回 (参与循环的连线索引列表, 循环路径列表)"""
    # 构建邻接表
    graph = {}
    for i, link in enumerate(links):
        source = link['source']
        if source not in graph:
            graph[source] = []
        graph[source].append((link['target'], i))

    circular_link_indices = set()
    circular_paths = []
    visited = set()
    rec_stack = {}

    def dfs(node, path):
        visited.add(node)
        rec_stack[node] = len(path)
        path.append(node)

        for neighbor, link_idx in graph.get(node, []):
            if neighbor not in visited:
                if dfs(neighbor, path):
                    circular_link_indices.add(link_idx)
                    return True
            elif neighbor in rec_stack:
                circular_link_indices.add(link_idx)
                cycle_start = rec_stack[neighbor]
                cycle_path = path[cycle_start:]
                circular_paths.append(list(cycle_path))
                return True

        path.pop()
        del rec_stack[node]
        return False

    for node in graph:
        if node not in visited:
            dfs(node, [])

    return list(circular_link_indices), circular_paths


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

    # 0. 检测虚拟环境目录（如 asr_env、gemma_env、llm_env 等）
    env_dir_names = detect_virtualenv_dirs(plugin_path)

    # 1. 扫描所有文件（跳过虚拟环境目录）
    all_file_paths = scan_directory(plugin_path, env_dir_names)
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
            "group": group,
            "reason": ""
        })

    # 1.5 静态健壮性检测（语法/编码/文件大小/节点规范/关键文件/依赖）
    node_map = {n['id']: n for n in nodes}
    declared_reqs = parse_requirements(plugin_path)
    internal_modules = {Path(p).stem for p in all_file_paths if p.endswith('.py')}
    stdlib_modules = get_stdlib_modules()
    syntax_error_count = 0

    for rel_path in all_file_paths:
        node = node_map.get(rel_path)
        if node is None:
            continue
        full_path = plugin_path / rel_path
        file_type = node['type']

        # 维度 4b：空文件检测 - 排除 __init__.py（空的 init 是正常的 Python 包规范）
        try:
            if node['size'] == 0 and node['name'] != '__init__.py':
                _mark_node(node, 'warning', '文件为空（0 字节），可能是无效占位符')
        except Exception:
            pass

        # 维度 4c：过大文件检测
        try:
            if node['size'] > LARGE_FILE_THRESHOLD:
                kb = node['size'] // 1024
                _mark_node(node, 'warning', f'文件过大（{kb} KB），建议拆分以提高可维护性')
        except Exception:
            pass

        # 仅对文本文件做编码检测；仅对 Python 文件做语法/规范/依赖检测
        if file_type in ('python', 'entry', 'javascript', 'config'):
            source, enc_status, enc_reason = read_text_with_encoding(full_path)
            if enc_status == 'error':
                # 维度 4d：无法解析，标记 error 并跳过后续 AST 检测
                _mark_node(node, 'error', enc_reason)
                continue
            elif enc_status == 'warning':
                _mark_node(node, 'warning', enc_reason)

            if file_type in ('python', 'entry') and source is not None:
                # 维度 1：Python 语法错误检测
                tree = None
                try:
                    tree = ast.parse(source)
                except SyntaxError as e:
                    lineno = e.lineno if e.lineno is not None else '?'
                    msg = e.msg or '语法错误'
                    _mark_node(node, 'error', f'语法错误（第 {lineno} 行）：{msg}')
                    syntax_error_count += 1
                except Exception:
                    tree = None

                if tree is not None:
                    # 维度 2：ComfyUI 节点规范检查（排除 __init__.py）
                    if node['name'] != '__init__.py':
                        try:
                            spec_reason = check_node_spec(tree)
                            if spec_reason:
                                _mark_node(node, 'warning', spec_reason)
                        except Exception:
                            pass

                    # 维度 4a：第三方依赖未声明检测
                    try:
                        dep_reason = check_undeclared_deps(
                            tree, declared_reqs, internal_modules, stdlib_modules)
                        if dep_reason:
                            _mark_node(node, 'warning', dep_reason)
                    except Exception:
                        pass

    # 1.6 维度 3：缺失关键文件/注册检测（整体级警告）
    try:
        if '__init__.py' in all_files_set:
            init_node = node_map.get('__init__.py')
            init_src, _st, _r = read_text_with_encoding(plugin_path / '__init__.py')
            has_mappings = bool(init_src) and re.search(r'\bNODE_CLASS_MAPPINGS\b', init_src)
            if init_node is not None and not has_mappings:
                _mark_node(init_node, 'warning', '__init__.py 中未找到 NODE_CLASS_MAPPINGS 节点注册')
        else:
            # 找 entry 类型节点，或第一个根目录 Python 文件
            target_node = next((n for n in nodes if n['type'] == 'entry'), None)
            if target_node is None:
                target_node = next(
                    (n for n in nodes if n['type'] in ('python', 'entry') and '/' not in n['id']),
                    None)
            if target_node is not None:
                _mark_node(target_node, 'warning', '插件缺少 __init__.py 入口文件')
    except Exception:
        pass

    # 3. 解析依赖，生成连线
    links = []
    error_count = 0
    import_failure_sources = {}

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
                    # 相对导入但找不到目标 = 警告
                    links.append({
                        "source": rel_path,
                        "target": f"[未找到] {imp['module']}",
                        "type": "import",
                        "status": "warning",
                        "reason": f"导入目标未找到：{imp['module']}"
                    })
                    import_failure_sources.setdefault(rel_path, []).append(imp['module'])

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
                    # 相对路径但找不到 = 警告
                    links.append({
                        "source": rel_path,
                        "target": f"[未找到] {imp['module']}",
                        "type": "import",
                        "status": "warning",
                        "reason": f"导入目标未找到：{imp['module']}"
                    })
                    import_failure_sources.setdefault(rel_path, []).append(imp['module'])

    # 4. 检测循环依赖
    circular_indices, circular_paths = detect_circular_dependencies(links)
    for idx in circular_indices:
        links[idx]['status'] = 'error'
        links[idx]['reason'] = '循环依赖'
    circular_count = len(circular_indices)
    error_count += circular_count

    # 5. 更新有错误连线的节点状态
    error_sources = set()
    for link in links:
        if link['status'] == 'error':
            error_sources.add(link['source'])

    for node in nodes:
        if node['id'] in error_sources:
            involved = [p for p in circular_paths if node['id'] in p]
            if involved:
                reasons = []
                for cycle in involved:
                    path_str = ' → '.join(cycle + [cycle[0]])
                    reasons.append(path_str)
                reason = f"循环依赖链：{'; '.join(reasons)}"
            else:
                reason = "循环依赖链：检测到循环依赖"
            # 使用 _mark_node：若节点已被新检测标记，循环依赖 reason 追加而不覆盖
            _mark_node(node, 'error', reason)

    # 6. 标记有导入失败但非循环依赖的节点为 warning
    for node in nodes:
        if node['id'] in import_failure_sources and node['status'] != 'error':
            modules = import_failure_sources[node['id']]
            _mark_node(node, 'warning', f"未解析的导入：{', '.join(modules)}")

    warning_count = sum(1 for n in nodes if n['status'] == 'warning')

    # 7. 为虚拟环境目录生成折叠节点（每个环境目录 → 1个节点）
    env_node_count = 0
    for env_name in env_dir_names:
        env_path = plugin_path / env_name
        file_count = _count_dir_files(env_path)
        # 计算总大小
        total_size = 0
        try:
            total_size = sum(
                f.stat().st_size
                for f in env_path.rglob('*')
                if f.is_file()
            )
        except OSError:
            pass

        nodes.append({
            "id": f"{env_name}/",
            "name": env_name,
            "type": "env",
            "size": total_size,
            "status": "normal",
            "group": "运行环境",
            "reason": f"虚拟环境目录（{file_count} 个文件，{total_size // (1024*1024)} MB），仅做基础关联不展开分析"
        })
        env_node_count += 1

    return {
        "nodes": nodes,
        "links": [lnk for lnk in links if not lnk['target'].startswith('[未找到]')],
        "stats": {
            "total_files": len(nodes),
            "error_count": error_count,
            "circular_deps": circular_count,
            "warning_count": warning_count,
            "syntax_errors": syntax_error_count,
            "env_dirs_collapsed": env_node_count
        }
    }
