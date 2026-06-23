"""
插件导出打包模块 - 将开发完成的插件打包为可发布格式
"""
import zipfile
import json
import re
import ast
import time
import asyncio
from pathlib import Path
from datetime import datetime
from typing import Dict, List, Optional


class PluginPackager:
    """插件打包器"""

    def __init__(self, packages_dir: Path):
        self.packages_dir = packages_dir
        self.packages_dir.mkdir(parents=True, exist_ok=True)

    async def package_plugin(self, plugin_dir: Path, options: dict) -> dict:
        """
        打包插件
        options: {generate_readme: bool, generate_pyproject: bool}
        返回: {success, filename, path, size, contents: [...]}
        """
        generate_readme = options.get("generate_readme", True)
        generate_pyproject = options.get("generate_pyproject", True)

        plugin_name = plugin_dir.name

        # 1. 验证插件完整性
        validation = self._validate_plugin(plugin_dir)
        if not validation["valid"]:
            return {"success": False, "message": "; ".join(validation["errors"])}

        # 2. 扫描源文件
        source_files = self._scan_source_files(plugin_dir)

        # 3. 提取节点信息
        node_info = self._extract_node_info(plugin_dir)

        # 4. 如果 generate_readme: 自动从代码提取节点信息生成 README.md
        readme_path = plugin_dir / "README.md"
        generated_readme = False
        if generate_readme and not readme_path.exists():
            readme_content = self._generate_readme(plugin_name, node_info)
            # P1-1: 同步文件写入放入线程池，避免阻塞事件循环
            await asyncio.to_thread(readme_path.write_text, readme_content, encoding='utf-8')
            generated_readme = True

        # 5. 如果 generate_pyproject: 生成 pyproject.toml（ComfyUI 注册表格式）
        pyproject_path = plugin_dir / "pyproject.toml"
        generated_pyproject = False
        if generate_pyproject and not pyproject_path.exists():
            pyproject_content = self._generate_pyproject(plugin_name, node_info)
            # P1-1: 同步文件写入放入线程池，避免阻塞事件循环
            await asyncio.to_thread(pyproject_path.write_text, pyproject_content, encoding='utf-8')
            generated_pyproject = True

        # 6. 打包为 .zip，存放到 packages_dir
        timestamp = time.strftime("%Y%m%d_%H%M%S")
        zip_filename = f"{plugin_name}_{timestamp}.zip"
        zip_path = self.packages_dir / zip_filename

        contents = []

        def _create_zip():
            with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as zf:
                for file in plugin_dir.rglob("*"):
                    if file.is_file() and not self._should_exclude(file, plugin_dir):
                        arcname = f"{plugin_name}/{file.relative_to(plugin_dir)}"
                        zf.write(file, arcname)
                        contents.append(str(file.relative_to(plugin_dir)).replace('\\', '/'))

        # 将阻塞的文件 IO 操作放到线程池执行，避免阻塞事件循环
        await asyncio.to_thread(_create_zip)

        # 清理临时生成的文件
        if generated_readme:
            readme_path.unlink(missing_ok=True)
        if generated_pyproject:
            pyproject_path.unlink(missing_ok=True)

        file_size = zip_path.stat().st_size

        return {
            "success": True,
            "message": f"打包完成: {zip_filename}",
            "filename": zip_filename,
            "path": str(zip_path),
            "size": file_size,
            "size_mb": round(file_size / 1024 / 1024, 2),
            "contents": contents,
            "nodes": node_info,
        }

    def _scan_source_files(self, plugin_dir: Path) -> List[Path]:
        """扫描插件目录下的源文件"""
        files = []
        for f in plugin_dir.rglob("*"):
            if f.is_file() and not self._should_exclude(f, plugin_dir):
                files.append(f)
        return files

    def _extract_node_info(self, plugin_dir: Path) -> List[dict]:
        """从 __init__.py 或 py 文件中提取 NODE_CLASS_MAPPINGS 信息"""
        nodes = []

        # 首先尝试从 __init__.py 中解析 NODE_CLASS_MAPPINGS 的键名
        init_file = plugin_dir / "__init__.py"
        if init_file.exists():
            try:
                content = init_file.read_text(encoding='utf-8', errors='ignore')
                # 解析 NODE_CLASS_MAPPINGS = {"NodeName": NodeClass, ...}
                mappings_match = re.search(
                    r'NODE_CLASS_MAPPINGS\s*=\s*\{([^}]+)\}', content, re.DOTALL
                )
                if mappings_match:
                    mapping_str = mappings_match.group(1)
                    # 提取字符串键（节点显示名称）
                    for match in re.finditer(r'["\']([\w\s]+)["\']\s*:', mapping_str):
                        node_name = match.group(1)
                        nodes.append({"name": node_name, "file": "__init__.py"})
            except Exception:
                pass

        # 扫描 .py 文件中包含 INPUT_TYPES 方法的类（真正的节点类）
        for py_file in plugin_dir.rglob("*.py"):
            try:
                content = py_file.read_text(encoding='utf-8', errors='ignore')
                tree = ast.parse(content)
                for node in ast.walk(tree):
                    if isinstance(node, ast.ClassDef):
                        # 检查类中是否定义了 INPUT_TYPES 方法
                        has_input_types = any(
                            isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef))
                            and item.name == 'INPUT_TYPES'
                            for item in node.body
                        )
                        if has_input_types:
                            # 尝试提取 CATEGORY 类属性
                            category = ""
                            for item in node.body:
                                if isinstance(item, ast.Assign):
                                    for target in item.targets:
                                        if isinstance(target, ast.Name) and target.id == 'CATEGORY':
                                            if isinstance(item.value, ast.Constant):
                                                category = str(item.value.value)

                            nodes.append({
                                "name": node.name,
                                "file": str(py_file.relative_to(plugin_dir)).replace('\\', '/'),
                                "category": category,
                            })
            except (SyntaxError, UnicodeDecodeError):
                pass

        return nodes

    def _generate_readme(self, plugin_name: str, nodes: List[dict]) -> str:
        """生成 README.md 内容"""
        if nodes:
            node_list = "\n".join([
                f"- **{n['name']}**"
                + (f" — {n.get('category', '')}" if n.get('category') else "")
                + f"  (`{n['file']}`)"
                for n in nodes
            ])
        else:
            node_list = "暂无节点信息"

        return f"""# {plugin_name}

ComfyUI 自定义节点插件。

## 节点列表

{node_list}

## 安装

将此文件夹复制到 `ComfyUI/custom_nodes/` 目录下，重启 ComfyUI 即可使用。

## 依赖

请查看 `requirements.txt` 了解额外依赖。

---
由 NodeCraft AI 自动生成
"""

    def _generate_pyproject(self, plugin_name: str, nodes: List[dict]) -> str:
        """生成 pyproject.toml"""

        return f"""[project]
name = "{plugin_name}"
description = "ComfyUI custom node plugin"
version = "1.0.0"
license = "MIT"

[project.urls]
Repository = "https://github.com/username/{plugin_name}"

[tool.comfy]
PublisherId = ""
DisplayName = "{plugin_name}"
Icon = ""
"""

    def _validate_plugin(self, plugin_dir: Path) -> dict:
        """验证插件完整性，返回 {valid, errors: [...]}"""
        errors = []

        if not plugin_dir.exists():
            return {"valid": False, "errors": ["插件目录不存在"]}

        init_file = plugin_dir / "__init__.py"
        if not init_file.exists():
            errors.append("缺少 __init__.py 文件")
        else:
            init_content = init_file.read_text(encoding='utf-8', errors='ignore')
            if "NODE_CLASS_MAPPINGS" not in init_content:
                errors.append("__init__.py 中缺少 NODE_CLASS_MAPPINGS")

        return {"valid": len(errors) == 0, "errors": errors}

    def _should_exclude(self, file: Path, root: Path) -> bool:
        """判断文件是否应排除"""
        rel = str(file.relative_to(root))
        excludes = ['__pycache__', '.git', '.pyc', '.pyo', '.egg-info',
                    '.sync_progress.json', '.cipher_key', '.密钥']
        return any(ex in rel for ex in excludes)

    def list_packages(self) -> List[Dict]:
        """列出已打包的文件"""
        packages = []
        for f in sorted(self.packages_dir.glob("*.zip"), reverse=True):
            packages.append({
                "filename": f.name,
                "size_mb": round(f.stat().st_size / 1024 / 1024, 2),
                "created": time.strftime("%Y-%m-%d %H:%M", time.localtime(f.stat().st_ctime))
            })
        return packages[:20]
