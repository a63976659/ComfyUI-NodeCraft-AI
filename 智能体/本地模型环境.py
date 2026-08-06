"""本地模型独立 venv 环境管理模块

NodeCraft-AI 插件运行在 ComfyUI 的 Python 环境中，但其他插件锁定了
transformers<5.0。为了加载 Qwen3.5 等新模型，本模块在插件目录下创建
一个独立的 venv，并通过 .pth 文件复用主环境中的 torch、cuda 等大型包，
避免重复安装约 2GB 的依赖。
"""

import asyncio
import inspect
import json
import shutil
import subprocess
import sys
import sysconfig
from datetime import datetime
from pathlib import Path
from typing import Callable, Optional

_plugin_root = Path(__file__).parent.parent.resolve()
_venv根目录 = _plugin_root / "数据" / "本地模型环境"
# 主环境 site-packages：用 sysconfig 而非硬拼 Lib/site-packages，
# 兼容 macOS/Linux（lib/pythonX.Y/site-packages）与桌面版 .venv 布局
_主环境site_packages = Path(sysconfig.get_paths()["purelib"])

# 延迟导入日志器，避免在仅导入模块时触发日志系统初始化
from 后端.日志配置 import 获取日志器

logger = 获取日志器("本地模型环境")

# venv 内部路径（平台分支：Windows 为 Scripts/python.exe + Lib/site-packages，
# POSIX 为 bin/python + lib/pythonX.Y/site-packages；venv 的 Python 版本与
# 创建它的主环境一致，故可确定性推导）
if sys.platform == "win32":
    _venv_python = _venv根目录 / "Scripts" / "python.exe"
    _venv_site_packages = _venv根目录 / "Lib" / "site-packages"
else:
    _venv_python = _venv根目录 / "bin" / "python"
    _venv_site_packages = (
        _venv根目录 / "lib"
        / f"python{sys.version_info.major}.{sys.version_info.minor}"
        / "site-packages"
    )
_pth文件 = _venv_site_packages / "_comfyui_packages.pth"
_路径修正模块 = _venv_site_packages / "_nodecraft_path_fix.py"
_标志文件 = _venv根目录 / ".installed"

# 本地模型推理所需的最低依赖版本
_默认依赖 = [
    "transformers>=5.2.0",
    "huggingface_hub>=1.5.0",
    "accelerate",
]


def 获取venv路径() -> Path:
    """返回本地模型 venv 的根目录路径。

    Returns:
        venv 根目录的绝对路径。
    """
    return _venv根目录


def 获取venv_python() -> Path:
    """返回 venv 中的 Python 解释器路径。

    Returns:
        Windows 下为 ``Scripts/python.exe``，macOS/Linux 下为 ``bin/python``
        的绝对路径。
    """
    return _venv_python


def 环境已就绪() -> bool:
    """检查 venv 环境是否已就绪。

    同时检查 venv 的 python.exe 和 ``.installed`` 标志文件是否存在，
    避免仅目录存在但初始化未完成的情况。

    Returns:
        True 表示环境可用，False 表示需要初始化。
    """
    return _venv_python.exists() and _标志文件.exists()


def _报告进度(
    消息: str,
    循环: Optional[asyncio.AbstractEventLoop],
    回调: Optional[Callable[[str], None]],
) -> None:
    """线程安全地向进度回调报告消息。

    支持同步回调（直接调用）和异步回调（通过 ``run_coroutine_threadsafe``
    提交到事件循环）。
    """
    if 回调 is None:
        return
    try:
        if inspect.iscoroutinefunction(回调):
            if 循环 is not None and 循环.is_running():
                asyncio.run_coroutine_threadsafe(回调(消息), 循环)
        else:
            回调(消息)
    except Exception:
        # 进度回调不应阻塞主流程
        pass


def _运行命令(
    命令: list,
    超时: int,
    阶段名: str,
    循环: Optional[asyncio.AbstractEventLoop] = None,
    回调: Optional[Callable[[str], None]] = None,
) -> subprocess.CompletedProcess:
    """运行子进程命令并检查返回码。

    Args:
        命令: 子进程命令行参数列表。
        超时: 命令最大允许执行时间（秒）。
        阶段名: 当前阶段的中文名称，用于日志和异常信息。
        循环: 调用者的事件循环，用于线程安全地触发进度回调。
        回调: 进度回调函数。

    Returns:
        subprocess.CompletedProcess 结果。

    Raises:
        RuntimeError: 命令超时或返回非零退出码。
    """
    logger.info(f"[{阶段名}] 执行: {' '.join(str(c) for c in 命令)}")
    _报告进度(f"开始: {阶段名}", 循环, 回调)

    try:
        结果 = subprocess.run(
            命令,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=超时,
            check=False,
        )
    except subprocess.TimeoutExpired as e:
        raise RuntimeError(f"{阶段名} 超时（{超时}秒），请检查网络或磁盘空间") from e
    except Exception as e:
        raise RuntimeError(f"{阶段名} 启动失败: {e}") from e

    if 结果.returncode != 0:
        错误信息 = 结果.stderr.strip() or 结果.stdout.strip() or "无输出"
        logger.error(f"[{阶段名}] 失败（返回码 {结果.returncode}）: {错误信息}")
        raise RuntimeError(
            f"{阶段名} 失败（返回码 {结果.returncode}）: {错误信息}"
        )

    logger.info(f"[{阶段名}] 成功")
    _报告进度(f"完成: {阶段名}", 循环, 回调)
    return 结果


def _检查venv能力() -> None:
    """检查当前 Python 是否具备创建 venv 的能力。

    便携版的嵌入式 Python（python_embeded）不带 venv/ensurepip 模块，
    提前预检并抛出可读提示，避免跑到子进程阶段才报晦涩错误。

    Raises:
        RuntimeError: 当前环境缺少 venv 或 ensurepip 模块。
    """
    import importlib.util
    缺失 = [模块 for 模块 in ("venv", "ensurepip") if importlib.util.find_spec(模块) is None]
    if 缺失:
        raise RuntimeError(
            f"当前 Python 环境缺少 {'/'.join(缺失)} 模块，无法创建独立环境。"
            "便携版（python_embeded）的嵌入式 Python 不支持此功能，"
            "请改用 API 模型，或在秋叶整合包/桌面版/手动安装版中使用本地模型"
        )


def _同步确保环境就绪(
    循环: Optional[asyncio.AbstractEventLoop],
    回调: Optional[Callable[[str], None]],
) -> None:
    """同步执行环境创建和依赖安装（应在 executor 线程中运行）。"""
    if 环境已就绪():
        _报告进度("本地模型环境已就绪，跳过初始化", 循环, 回调)
        return

    _报告进度("开始初始化本地模型环境...", 循环, 回调)

    # 0. 能力预检：便携版嵌入式 Python 无 venv/ensurepip，尽早给出明确提示
    _检查venv能力()

    # 1. 清理可能存在的旧环境
    if _venv根目录.exists():
        _报告进度("清理旧环境...", 循环, 回调)
        try:
            shutil.rmtree(_venv根目录)
        except Exception as e:
            raise RuntimeError(f"清理旧环境失败: {e}") from e

    # 2. 创建 venv（不带 pip，避免依赖网络）
    _venv根目录.mkdir(parents=True, exist_ok=True)
    创建命令 = [
        sys.executable,
        "-m", "venv",
        str(_venv根目录),
        "--without-pip",
    ]
    _运行命令(创建命令, 60, "创建虚拟环境", 循环, 回调)

    # 3. 通过 ensurepip 安装 pip
    pip安装命令 = [
        str(_venv_python),
        "-m", "ensurepip",
        "--upgrade",
        "--default-pip",
    ]
    _运行命令(pip安装命令, 120, "安装 pip", 循环, 回调)

    # 4. 安装 transformers 及核心依赖（在创建 .pth 之前，避免主环境干扰）
    transformers_pip命令 = [
        str(_venv_python),
        "-m", "pip",
        "install",
        "transformers>=5.2.0",
        "huggingface_hub>=1.5.0",
        "tokenizers",
        "safetensors",
    ]
    _运行命令(transformers_pip命令, 600, "安装 transformers 及核心依赖", 循环, 回调)

    # 5. 创建路径修正机制：.pth 文件 + _nodecraft_path_fix.py
    # 利用 .pth 文件的特性：以 import 开头的行会被 site 模块执行
    # 这样主环境只会被加到 sys.path 末尾，venv 的包始终优先
    _pth文件.parent.mkdir(parents=True, exist_ok=True)
    主环境路径 = str(_主环境site_packages.resolve())

    # 创建路径修正模块
    路径修正内容 = f'''"""NodeCraft AI venv 路径修正（由 .pth 文件触发执行）

确保 venv site-packages 优先于主环境，主环境仅添加到 sys.path 末尾。
这样 venv 安装的 transformers 5.x 会覆盖主环境的 4.x。
"""
import sys as _sys
import os as _os

_主环境 = r"{主环境路径}"
_venv_site = _os.path.dirname(_os.path.abspath(__file__))

# 移除 sys.path 中所有指向主环境 site-packages 的条目
_主环境_norm = _os.path.normcase(_os.path.normpath(_主环境))
_sys.path[:] = [
    p for p in _sys.path
    if _os.path.normcase(_os.path.normpath(p)) != _主环境_norm
]

# 确保 venv site-packages 在最前面（标准库路径之后）
if _venv_site in _sys.path:
    _sys.path.remove(_venv_site)
_sys.path.insert(0, _venv_site)

# 把主环境添加到 sys.path 末尾（仅为 torch/cuda 等大包服务）
if _os.path.isdir(_主环境):
    _sys.path.append(_主环境)
'''
    _路径修正模块.write_text(路径修正内容, encoding="utf-8")
    logger.info(f"已创建路径修正模块: {_路径修正模块}")

    # .pth 文件只包含 import 指令（不直接写路径）
    _pth文件.write_text("import _nodecraft_path_fix\n", encoding="utf-8")
    logger.info(f"已创建 .pth 文件: {_pth文件} -> import _nodecraft_path_fix")
    _报告进度("已配置主环境包复用（torch、cuda 等）", 循环, 回调)

    # 6. 安装需要 torch 的包（此时 .pth 已存在，可以找到主环境的 torch）
    accel_pip命令 = [
        str(_venv_python),
        "-m", "pip",
        "install",
        "accelerate",
    ]
    _运行命令(accel_pip命令, 300, "安装 accelerate", 循环, 回调)

    # 7. 写入安装标志文件
    安装信息 = {
        "installed_at": datetime.now().isoformat(),
        "python": str(_venv_python),
        "main_site_packages": 主环境路径,
        "packages": _默认依赖,
    }
    _标志文件.write_text(
        json.dumps(安装信息, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    _报告进度("本地模型环境初始化完成", 循环, 回调)
    logger.info("本地模型环境初始化完成")


async def 确保环境就绪(进度回调: Optional[Callable[[str], None]] = None) -> None:
    """确保本地模型 venv 环境已就绪，不存在则自动创建并安装依赖。

    整个过程在 executor 中运行，不会阻塞事件循环。如果环境已就绪且提供了
    回调，会立即报告 "环境已就绪"。

    Args:
        进度回调: 可选的回调函数，接收字符串进度消息。可以是同步函数或
            异步协程函数。

    Raises:
        RuntimeError: 环境创建或依赖安装失败。
    """
    if 环境已就绪():
        if 进度回调:
            if inspect.iscoroutinefunction(进度回调):
                await 进度回调("本地模型环境已就绪")
            else:
                进度回调("本地模型环境已就绪")
        return

    循环 = asyncio.get_running_loop()
    await 循环.run_in_executor(None, _同步确保环境就绪, 循环, 进度回调)


def _同步升级依赖(
    循环: Optional[asyncio.AbstractEventLoop],
    回调: Optional[Callable[[str], None]],
) -> None:
    """同步执行依赖升级（应在 executor 线程中运行）。"""
    if not _venv_python.exists():
        raise RuntimeError("虚拟环境不存在，请先调用 确保环境就绪()")

    # 1. 先升级 transformers 及核心依赖（不需要 torch，避免 .pth 引入的主环境旧版干扰）
    transformers_pip命令 = [
        str(_venv_python),
        "-m", "pip",
        "install", "--upgrade",
        "transformers>=5.2.0",
        "huggingface_hub>=1.5.0",
        "tokenizers",
        "safetensors",
    ]
    _运行命令(transformers_pip命令, 600, "升级 transformers 及核心依赖", 循环, 回调)

    # 2. 再升级需要 torch 的包（.pth 已存在，可以找到主环境的 torch）
    accel_pip命令 = [
        str(_venv_python),
        "-m", "pip",
        "install", "--upgrade",
        "accelerate",
    ]
    _运行命令(accel_pip命令, 300, "升级 accelerate", 循环, 回调)

    # 更新标志文件，记录升级时间
    安装信息 = {
        "installed_at": _读取安装时间(),
        "upgraded_at": datetime.now().isoformat(),
        "python": str(_venv_python),
        "main_site_packages": str(_主环境site_packages.resolve()),
        "packages": _默认依赖,
        "note": "已升级",
    }
    _标志文件.write_text(
        json.dumps(安装信息, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    _报告进度("依赖升级完成", 循环, 回调)


def _读取安装时间() -> str:
    """从标志文件中读取安装时间，失败时返回当前时间。"""
    try:
        if _标志文件.exists():
            数据 = json.loads(_标志文件.read_text(encoding="utf-8"))
            return 数据.get("installed_at", datetime.now().isoformat())
    except Exception:
        pass
    return datetime.now().isoformat()


async def 升级依赖(进度回调: Optional[Callable[[str], None]] = None) -> None:
    """升级 venv 中的 transformers 等依赖到最新版。

    Args:
        进度回调: 可选的回调函数，接收字符串进度消息。

    Raises:
        RuntimeError: 虚拟环境不存在或升级失败。
    """
    循环 = asyncio.get_running_loop()
    await 循环.run_in_executor(None, _同步升级依赖, 循环, 进度回调)


def 删除环境() -> None:
    """删除整个 venv 目录，用于重置环境。

    删除后再次调用 ``确保环境就绪()`` 会重新创建环境。
    """
    if _venv根目录.exists():
        try:
            shutil.rmtree(_venv根目录)
            logger.info(f"已删除本地模型环境: {_venv根目录}")
        except Exception as e:
            raise RuntimeError(f"删除环境失败: {e}") from e
    else:
        logger.info("本地模型环境不存在，无需删除")


__all__ = [
    "获取venv路径",
    "获取venv_python",
    "环境已就绪",
    "确保环境就绪",
    "升级依赖",
    "删除环境",
]
