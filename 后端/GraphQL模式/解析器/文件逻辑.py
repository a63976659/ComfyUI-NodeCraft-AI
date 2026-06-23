"""GraphQL 文件与插件 Resolver

实现文件操作、插件管理、可视化分析的业务逻辑函数：
- 获取本地插件列表（分页扫描 custom_nodes）
- 浏览文件夹（路径穿越防护）
- 获取插件文件树（递归扫描）
- 读取/写入文件（路径穿越防护 + 认证）
- 创建插件文件夹（脚手架生成）
- 分析插件（文件依赖图，不持久化）
- 执行可视化分析（文件/功能模式 + 落盘）
- 获取可视化数据（读取持久化结果）
- 打包插件（生成 ZIP）

所有写操作均执行 ``await 检查认证(info)``；所有路径参数均做穿越防护，
确保操作目标位于 custom_nodes 目录范围内。
"""

from __future__ import annotations

import json
import math
import shutil
from pathlib import Path
from typing import List, Optional

from strawberry.types import Info

from ..中间件 import 检查认证
from ..异常定义 import 验证错误, 资源不存在, 权限错误
from ..类型定义 import (
    分页信息,
    插件列表响应,
    文件夹项,
    文件树节点,
    文件内容,
    可视化数据,
)

# 复用现有同步业务逻辑
from ...系统环境映射 import get_custom_nodes_path, get_plugin_root
from ...文件读写操作 import (
    create_plugin_scaffold,
    scan_plugin_file_tree,
)


# ─── 内部工具 ──────────────────────────────────────────────

# Windows 保留文件名（与 文件路由.handle_create_folder 保持一致）
_WINDOWS_RESERVED = {
    "con", "prn", "aux", "nul",
    "com1", "com2", "com3", "com4", "com5",
    "com6", "com7", "com8", "com9",
    "lpt1", "lpt2", "lpt3", "lpt4", "lpt5",
    "lpt6", "lpt7", "lpt8", "lpt9",
}

# 单文件读取大小上限（与 文件读写操作.read_plugin_file 保持一致）
_MAX_READ_SIZE = 1024 * 1024


def _规范化页码(页数: int, 每页数量: int) -> tuple[int, int]:
    """规范化分页参数，限制范围"""
    if 页数 < 1:
        页数 = 1
    if 每页数量 < 1:
        每页数量 = 1
    if 每页数量 > 200:
        每页数量 = 200
    return 页数, 每页数量


def _构建分页信息(总数: int, 当前页: int, 页大小: int) -> 分页信息:
    """根据总数与分页参数构建分页信息对象"""
    if 页大小 <= 0:
        总页数 = 0
    else:
        总页数 = math.ceil(总数 / 页大小) if 总数 > 0 else 0
    return 分页信息(
        总数=总数,
        当前页=当前页,
        页大小=页大小,
        总页数=总页数,
    )


def _切片分页(数据: list, 页数: int, 每页数量: int) -> list:
    """对列表执行 offset 分页切片"""
    起始 = (页数 - 1) * 每页数量
    结束 = 起始 + 每页数量
    return 数据[起始:结束]


def _解析插件路径(插件路径: str) -> Path:
    """将用户传入的插件路径解析为绝对路径（相对路径基于 custom_nodes）"""
    if not 插件路径:
        raise 验证错误("插件路径不能为空")
    plugin_path = Path(插件路径)
    if not plugin_path.is_absolute():
        plugin_path = get_custom_nodes_path() / 插件路径
    return plugin_path


def _校验路径在custom_nodes内(候选路径: str) -> Path:
    """路径穿越防护：解析路径并确保位于 custom_nodes 目录之内。

    返回解析后的绝对路径；越界时抛出 ``权限错误``。
    """
    if not 候选路径:
        raise 验证错误("路径不能为空")

    target = Path(候选路径)
    if not target.is_absolute():
        target = get_custom_nodes_path() / 候选路径

    try:
        target = target.resolve()
    except (OSError, RuntimeError) as e:
        raise 验证错误(f"路径解析失败: {e}")

    allowed = get_custom_nodes_path().resolve()
    try:
        target.relative_to(allowed)
    except ValueError:
        raise 权限错误("路径超出允许范围")
    return target


def _字典转文件树节点(项: dict) -> 文件树节点:
    """将 scan_plugin_file_tree 输出的 dict 转换为 文件树节点 GraphQL 类型"""
    类型 = str(项.get("type", "file") or "file")
    大小 = int(项.get("size", 0) or 0)
    子节点列表: List[文件树节点] = []
    if 类型 == "dir":
        for 子项 in 项.get("children", []) or []:
            if isinstance(子项, dict):
                子节点列表.append(_字典转文件树节点(子项))
    return 文件树节点(
        名称=str(项.get("name", "") or ""),
        类型=类型,
        大小=大小,
        子节点=子节点列表,
    )


def _字典转可视化数据(数据: dict) -> 可视化数据:
    """将 graphData dict 转换为 可视化数据（节点/连线 序列化为 JSON 字符串）"""
    nodes = 数据.get("nodes", []) if isinstance(数据, dict) else []
    links = 数据.get("links", []) if isinstance(数据, dict) else []
    return 可视化数据(
        节点=json.dumps(nodes, ensure_ascii=False),
        连线=json.dumps(links, ensure_ascii=False),
    )


def _获取打包目录() -> Path:
    """打包文件存放目录：插件根目录/数据/打包文件"""
    return get_plugin_root() / "数据" / "打包文件"


# ─── Query Resolver ───────────────────────────────────────

async def 获取本地插件列表(
    info: Info,
    页数: int = 1,
    每页数量: int = 20,
) -> 插件列表响应:
    """扫描 custom_nodes 目录，返回所有插件目录名（分页）"""
    await 检查认证(info)

    页数, 每页数量 = _规范化页码(页数, 每页数量)

    nodes_path = get_custom_nodes_path()
    plugins: List[str] = []
    if nodes_path.exists():
        for item in sorted(nodes_path.iterdir()):
            if item.is_dir() and not item.name.startswith("."):
                plugins.append(item.name)

    总数 = len(plugins)
    当前页数据 = _切片分页(plugins, 页数, 每页数量)

    return 插件列表响应(
        插件列表=当前页数据,
        分页=_构建分页信息(总数, 页数, 每页数量),
    )


async def 浏览文件夹(info: Info, 初始目录: str = "") -> List[文件夹项]:
    """浏览指定目录下的子文件夹列表（路径穿越防护）"""
    await 检查认证(info)

    if not 初始目录:
        初始目录 = str(get_custom_nodes_path())

    target = _校验路径在custom_nodes内(初始目录)

    if not target.exists():
        raise 资源不存在("目录", 初始目录)
    if not target.is_dir():
        raise 验证错误("指定路径不是目录")

    folders: List[文件夹项] = []
    for item in sorted(target.iterdir()):
        if item.is_dir() and not item.name.startswith("."):
            folders.append(文件夹项(名称=item.name, 路径=str(item)))
    return folders


async def 获取插件文件树(info: Info, 插件路径: str) -> List[文件树节点]:
    """获取指定插件的文件树结构"""
    await 检查认证(info)

    plugin_path = _解析插件路径(插件路径)
    # 同样需在 custom_nodes 范围内（防止穿越）
    _校验路径在custom_nodes内(str(plugin_path))

    if not plugin_path.exists():
        raise 资源不存在("插件", 插件路径)
    if not plugin_path.is_dir():
        raise 验证错误("插件路径不是目录")

    raw_tree = scan_plugin_file_tree(str(plugin_path))
    return [_字典转文件树节点(项) for 项 in (raw_tree or []) if isinstance(项, dict)]


async def 读取文件(info: Info, 文件路径: str) -> 文件内容:
    """读取指定文件内容（路径穿越防护 + 大小限制）"""
    await 检查认证(info)

    target = _校验路径在custom_nodes内(文件路径)

    if not target.exists():
        raise 资源不存在("文件", 文件路径)
    if not target.is_file():
        raise 验证错误("指定路径不是文件")

    if target.stat().st_size > _MAX_READ_SIZE:
        raise 验证错误("文件过大（超过 1MB），不支持直接读取")

    try:
        content = target.read_text(encoding="utf-8")
        return 文件内容(内容=content, 是否二进制=False, 编码="utf-8")
    except UnicodeDecodeError:
        # 非 UTF-8 文本/二进制文件：返回空内容并标记
        return 文件内容(内容="", 是否二进制=True, 编码="binary")


async def 获取可视化数据(info: Info, 插件路径: str) -> Optional[可视化数据]:
    """读取已持久化的可视化分析数据。

    优先返回文件模式数据；若无则返回功能模式数据；若都没有则返回 None。
    """
    await 检查认证(info)

    plugin_path = _解析插件路径(插件路径)
    _校验路径在custom_nodes内(str(plugin_path))

    if not plugin_path.exists():
        return None

    from ...可视化分析 import load_visualization

    payload = load_visualization(plugin_path)
    if not payload or not payload.get("meta"):
        return None

    chosen = payload.get("file_mode_data") or payload.get("function_mode_data")
    if not chosen:
        return None

    return _字典转可视化数据(chosen)


# ─── Mutation Resolver ────────────────────────────────────

async def 写入文件(
    info: Info,
    文件路径: str,
    内容: str,
    编码: str = "utf-8",
) -> bool:
    """写入文件内容（自动备份原文件 + 路径穿越防护）"""
    await 检查认证(info)

    target = _校验路径在custom_nodes内(文件路径)

    if target.exists() and target.is_dir():
        raise 验证错误("目标路径是目录，无法写入文件")

    使用编码 = (编码 or "utf-8").strip() or "utf-8"

    try:
        # 备份已有文件
        if target.exists() and target.is_file():
            backup_path = target.with_suffix(target.suffix + ".bak")
            shutil.copy2(target, backup_path)

        # 确保父目录存在
        target.parent.mkdir(parents=True, exist_ok=True)

        # 父目录也必须在 custom_nodes 内（防止 mkdir 创建越界路径）
        _校验路径在custom_nodes内(str(target.parent))

        target.write_text(内容 if 内容 is not None else "", encoding=使用编码)
        return True
    except (UnicodeEncodeError, LookupError) as e:
        raise 验证错误(f"编码失败：{e}")
    except OSError as e:
        raise 验证错误(f"写入失败：{e}")


async def 创建插件文件夹(info: Info, 插件名称: str) -> str:
    """创建插件脚手架目录，返回创建的绝对路径"""
    await 检查认证(info)

    plugin_name = (插件名称 or "").strip()
    if not plugin_name:
        raise 验证错误("插件名称不能为空")
    if len(plugin_name) > 128:
        raise 验证错误("插件名称长度不能超过128个字符")
    if plugin_name.lower().split(".")[0] in _WINDOWS_RESERVED:
        raise 验证错误(f"插件名称不能使用Windows保留字: {plugin_name}")

    nodes_path = get_custom_nodes_path()
    success, message, path = create_plugin_scaffold(nodes_path, plugin_name)
    if not success:
        raise 验证错误(message)
    return path


async def 分析插件(info: Info, 插件路径: str) -> str:
    """分析插件依赖关系，返回 graphData JSON 字符串（不持久化）"""
    await 检查认证(info)

    plugin_path = _解析插件路径(插件路径)
    _校验路径在custom_nodes内(str(plugin_path))

    if not plugin_path.exists():
        raise 资源不存在("插件", 插件路径)

    from ...可视化分析 import run_file_mode_analysis

    result = run_file_mode_analysis(plugin_path, persist=False)
    return json.dumps(result, ensure_ascii=False)


async def 执行可视化分析(
    info: Info,
    插件路径: str,
    模式: str,
) -> 可视化数据:
    """执行可视化分析（文件/功能模式）并落盘"""
    await 检查认证(info)

    模式清理 = (模式 or "").strip()
    if 模式清理 not in ("file", "function"):
        raise 验证错误("模式必须为 'file' 或 'function'")

    plugin_path = _解析插件路径(插件路径)
    _校验路径在custom_nodes内(str(plugin_path))

    if not plugin_path.exists():
        raise 资源不存在("插件", 插件路径)

    from ...可视化分析 import (
        ensure_visualization_dir,
        run_file_mode_analysis,
        run_function_mode_analysis,
    )

    ensure_visualization_dir(plugin_path)

    if 模式清理 == "file":
        result = run_file_mode_analysis(plugin_path, persist=True)
    else:
        # 延迟导入 路由公共，避免在 GraphQL模式 顶层引入副作用
        from ...路由公共 import llm_client, local_model_client

        result = await run_function_mode_analysis(
            plugin_path,
            llm_client=llm_client,
            local_model_client=local_model_client,
            persist=True,
        )

    return _字典转可视化数据(result if isinstance(result, dict) else {})


async def 打包插件(info: Info, 插件路径: str) -> str:
    """打包插件为 ZIP 文件，返回生成的文件名"""
    await 检查认证(info)

    plugin_path = _解析插件路径(插件路径)
    _校验路径在custom_nodes内(str(plugin_path))

    if not plugin_path.exists():
        raise 资源不存在("插件", 插件路径)
    if not plugin_path.is_dir():
        raise 验证错误("插件路径不是目录")

    from ...插件打包 import PluginPackager

    packages_dir = _获取打包目录()
    packager = PluginPackager(packages_dir)
    options = {"generate_readme": True, "generate_pyproject": True}
    result = await packager.package_plugin(plugin_path, options)

    if not result.get("success"):
        raise 验证错误(result.get("message", "打包失败"))

    return str(result.get("filename", ""))


__all__ = [
    "获取本地插件列表",
    "浏览文件夹",
    "获取插件文件树",
    "读取文件",
    "获取可视化数据",
    "写入文件",
    "创建插件文件夹",
    "分析插件",
    "执行可视化分析",
    "打包插件",
]
