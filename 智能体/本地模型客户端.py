"""本地 transformers 模型客户端（代理层）

通过 subprocess 与 本地模型worker.py 通信，不直接 import transformers/torch。
负责 worker 生命周期管理、命令发送/响应读取、工具调用循环。
JSON 修复函数从 智能体.JSON修复工具 导入。
共享数据类（性能统计）从 智能体.模型客户端 导入。
"""
import asyncio
import collections
import concurrent.futures
import json
import re
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Optional

_plugin_root = str(Path(__file__).parent.parent.resolve())
if _plugin_root not in sys.path:
    sys.path.insert(0, _plugin_root)
from 后端.文件读写操作 import load_settings
from 后端.日志配置 import 获取日志器
from 智能体.JSON修复工具 import _修复截断JSON, _智能截断修复  # noqa: F401  re-export 兼容
from 智能体.工具结果摘要器 import 构建轮次上限提示
from 智能体.本地模型工具解析器 import (
    format_tool_result,
    has_tool_calls,
    separate_content_and_calls,
    strip_tool_calls,
)
from 智能体.模型客户端 import 性能统计
from 智能体.本地模型系列档案 import 匹配系列档案

logger = 获取日志器("本地模型客户端")


class _复读检测器:
    """流式生成复读退化检测（滑动窗口，坑 107/108）

    本地小模型（尤其量化后）在结构排比内容上易进入复读退化：
    连续大量行共享同一前缀（如 from xxx import AAA/BBB/CCC...）。
    只看“是否在产出新内容”而非“是否还在动”，与工具循环空转熔断（坑 98）同一设计哲学。

    仅统计有效长度 >= _最小行长 的行，避免 ``` / } 等合法短行误报；
    阈值取“窗口 24 行中 >= 20 行同前缀”，正常代码（含导入块）几乎不会命中。
    """

    _窗口行数 = 24    # 滑动窗口大小（行）
    _触发行数 = 20    # 窗口内同前缀行数达到该值则触发
    _前缀长度 = 20    # 参与比对的行前缀字符数
    _最小行长 = 10    # 短于此长度的行不参与统计

    def __init__(self):
        self._行缓冲 = ""
        self._窗口 = collections.deque(maxlen=self._窗口行数)

    def 喂入(self, text: str) -> bool:
        """累积流式文本，按完整行统计；检测到复读返回 True"""
        self._行缓冲 += text
        while "\n" in self._行缓冲:
            行, self._行缓冲 = self._行缓冲.split("\n", 1)
            行 = 行.strip()
            if len(行) < self._最小行长:
                continue
            self._窗口.append(行[: self._前缀长度])
            if len(self._窗口) == self._窗口行数:
                _, 最高频次 = collections.Counter(self._窗口).most_common(1)[0]
                if 最高频次 >= self._触发行数:
                    return True  # 触发即返回，剩余行无需继续统计
        return False


# ============================================================
#  工具调用指令模板（保留在代理层，不依赖 transformers）
# ============================================================

_TOOL_INSTRUCTION_TEMPLATE = """
## 可用工具

你拥有文件操作工具。当需要读取、创建或修改文件时，你**必须**使用工具而非输出代码文本。

### 工具列表

1. `list_plugin_files` - 列出当前插件的文件结构
2. `read_plugin_file` - 读取指定文件内容（参数：file_path）
3. `write_plugin_file` - 写入完整文件内容（参数：file_path, content）
4. `edit_file` - 增量编辑文件（参数：file_path, patch。patch 为 unified diff 补丁）
5. `batch_edit` - 批量操作多个文件（参数：operations 数组，每个元素含 action/file_path/content）

### ⚠️ 严格格式要求（必读，违反将导致调用失败）

**你只允许使用一种格式调用工具：`<tool_call>` 包裹的 JSON 格式。**

正确格式（唯一合法）：
```
<tool_call>{"name": "read_plugin_file", "arguments": {"file_path": "__init__.py"}}</tool_call>
```

核心规则：
- `<tool_call>` 标签内**只能是完整的 JSON 对象**，不能是任何其他内容
- JSON 对象必须包含 `"name"`（字符串）和 `"arguments"`（对象）两个字段
- 每条消息最多包含一个工具调用，调用后立即停止输出
- **禁止**用代码块展示文件内容代替实际写入操作

### ❌ 绝对禁止的格式

以下格式会导致调用**完全失败**，因为系统无法从中提取参数：

- `<function=read_plugin_file>` — **禁止！** 这是部分模型（如 Qwen）的原生格式，但本系统不支持
- `<function=write_plugin_file>...</function>` — **禁止！** 不要用 XML 标签包裹
- `</arguments: {}` — **禁止！** 畸形格式
- `<tool_call>{"file_path": "x.py"}</tool_call>` — **禁止！** 缺少 name 字段

> **为什么 `<function=xxx>` 不行？** 因为这个格式没有 JSON 参数，系统无法知道你要操作哪个文件、
> 写入什么内容。你必须用 `<tool_call>` + JSON 格式提供完整的参数。

### ✅ 正确调用示例

列出文件：
<tool_call>{"name": "list_plugin_files", "arguments": {}}</tool_call>

读取文件：
<tool_call>{"name": "read_plugin_file", "arguments": {"file_path": "__init__.py"}}</tool_call>

写入文件：
<tool_call>{"name": "write_plugin_file", "arguments": {"file_path": "hello.py",
"content": "print('Hello World!')\\n"}}</tool_call>

### 工作流程

当用户要求你创建或修改文件时：
1. 先用 list_plugin_files 了解当前结构
2. 如需参考已有文件，用 read_plugin_file 读取
3. 用 write_plugin_file 写入完整文件内容，或用 edit_file 做局部修改
4. 创建完整项目时用 batch_edit 一次创建多个文件
5. 完成后告诉用户已创建/修改了哪些文件

**最后提醒：你唯一可用的工具调用格式是 `<tool_call>JSON</tool_call>`。不要使用 `<function=xxx>` 或任何其他格式。**
"""


def _记录工具进度(tool_name: str, tool_args: dict, 工具统计: dict, 修改文件集: set) -> None:
    """工具成功执行后记录进度（轮次上限时的进度摘要用）。

    非流式与流式工具循环共用：统计各工具成功次数，并收集写入/编辑类
    工具触碰过的文件路径（含 batch_edit 的多文件操作）。
    """
    工具统计[tool_name] = 工具统计.get(tool_name, 0) + 1
    if tool_name in ("write_plugin_file", "edit_file", "batch_edit"):
        fp = (tool_args or {}).get("file_path", "")
        if fp:
            修改文件集.add(fp)
        if tool_name == "batch_edit":
            for op in (tool_args or {}).get("operations", []):
                op_fp = op.get("file_path", "") if isinstance(op, dict) else ""
                if op_fp:
                    修改文件集.add(op_fp)


class LocalModelClient:
    """本地模型客户端（代理层）

    通过 subprocess 与 本地模型worker.py 通信，不直接 import transformers/torch。

    特性：
    - 异步锁保护并发通信（同一时间只有一个请求与 worker 交互）
    - 失败黑名单机制（5分钟自动解除）
    - Worker 崩溃自动重启
    - 600 秒推理超时保护（load: 300s）
    - P0: thinking 标签剥离（代理层处理）
    - 完整的性能监控统计
    - stderr 转发到主进程日志
    """

    # 推理超时秒数（与 worker ModelWorker._INFER_TIMEOUT 保持一致）：
    # 4bit 大模型 + 长上下文 + 思考通道内容耗时较长，180s 不够用
    _INFER_TIMEOUT = 600

    def __init__(self):
        self._worker_process: Optional[subprocess.Popen] = None
        self._worker_lock: Optional[asyncio.Lock] = None  # 保护 worker 通信
        self.当前模型名: Optional[str] = None
        self._性能 = 性能统计()
        self.加载失败历史 = {}  # {模型名: 失败时间戳}
        self.失败黑名单过期秒 = 300
        self._current_quantization = "none"
        self._current_dtype = "float32"
        self._is_multimodal = False  # 当前加载的模型是否支持视觉多模态
        # stderr 转发线程
        self._stderr_thread: Optional[threading.Thread] = None
        # stdout 读取专用单线程 executor：避免与默认线程池争用
        # （默认池被 asyncio.to_thread 的会话保存/知识检索等任务共享，
        # 高并发时逐行 readline 排队会放大流式 token 延迟）
        self._stdout_executor: Optional[concurrent.futures.ThreadPoolExecutor] = None
        # Worker 停止信号哨兵文件路径
        self._stop_flag_path = Path(__file__).resolve().parent.parent / "数据" / ".stop_stream"

    def _signal_worker_stop(self):
        """创建停止信号哨兵文件，通知 worker 提前终止生成"""
        try:
            self._stop_flag_path.parent.mkdir(parents=True, exist_ok=True)
            self._stop_flag_path.write_text("stop", encoding="utf-8")
        except OSError as e:
            logger.debug(f"创建停止信号文件失败（忽略）: {e}")

    # ============================================================
    #  异步锁（延迟创建）
    # ============================================================

    def _获取异步锁(self) -> asyncio.Lock:
        """延迟创建并返回 asyncio.Lock（必须在运行的事件循环中创建）"""
        if self._worker_lock is None:
            self._worker_lock = asyncio.Lock()
        return self._worker_lock

    def _获取stdout_executor(self) -> concurrent.futures.ThreadPoolExecutor:
        """延迟创建 stdout 读取专用单线程 executor（客户端生命周期内复用）。

        worker 通信由 _worker_lock 串行化，同一时刻至多一个 readline 在读，
        单线程即可满足需求且保证行顺序（FIFO 排队）。
        """
        if self._stdout_executor is None:
            self._stdout_executor = concurrent.futures.ThreadPoolExecutor(
                max_workers=1, thread_name_prefix="本地模型stdout读取"
            )
        return self._stdout_executor

    # ============================================================
    #  Worker 子进程管理
    # ============================================================

    async def _确保worker运行(self):
        """启动 worker 子进程（如果未运行或已退出）。

        1. 调用 本地模型环境.确保环境就绪()
        2. 启动 subprocess: venv_python 本地模型worker.py
        3. 等待 {"type": "ready"} 响应

        Raises:
            RuntimeError: 环境未就绪、worker 启动失败或超时
        """
        if self._worker_process is not None:
            # 检查 worker 是否仍然存活
            if self._worker_process.poll() is None:
                return  # 仍在运行
            # 已退出，记录并重启
            exit_code = self._worker_process.returncode
            logger.warning(f"Worker 进程已退出（返回码 {exit_code}），将重新启动")
            self._worker_process = None

        from 智能体.本地模型环境 import 确保环境就绪, 获取venv_python

        # 确保环境就绪
        await 确保环境就绪()

        worker_script = Path(__file__).parent / "本地模型worker.py"
        venv_python = 获取venv_python()

        if not worker_script.exists():
            raise RuntimeError(f"Worker 脚本不存在: {worker_script}")

        logger.info(f"正在启动 Worker 子进程: {venv_python} {worker_script}")

        try:
            self._worker_process = subprocess.Popen(
                [str(venv_python), str(worker_script)],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                bufsize=1,  # 行缓冲
            )
        except OSError as e:
            raise RuntimeError(f"Worker 子进程启动失败: {e}") from e

        # 启动 stderr 转发线程
        self._启动stderr转发()

        # 等待 worker 发送 {"type": "ready"}
        try:
            resp = await asyncio.wait_for(
                self._read_worker_response(),
                timeout=30,
            )
        except asyncio.TimeoutError:
            self._终止worker()
            raise RuntimeError("Worker 启动超时（30秒未收到 ready 响应）")

        if resp.get("type") != "ready":
            self._终止worker()
            raise RuntimeError(f"Worker 启动响应异常: {resp}")

        logger.info("Worker 子进程已启动并就绪")

    def _启动stderr转发(self):
        """启动后台线程将 worker stderr 转发到主进程日志"""
        if self._worker_process is None or self._worker_process.stderr is None:
            return

        def _转发():
            try:
                for line in self._worker_process.stderr:
                    line = line.rstrip("\n\r")
                    if line:
                        logger.info(f"[Worker] {line}")
            except (OSError, ValueError) as e:
                logger.debug(f"stderr 转发线程退出: {e}")

        self._stderr_thread = threading.Thread(target=_转发, daemon=True)
        self._stderr_thread.start()

    def _send_to_worker(self, data: dict):
        """向 worker stdin 写入一行 JSON 命令。

        Raises:
            RuntimeError: worker 未运行或写入失败
        """
        if self._worker_process is None or self._worker_process.poll() is not None:
            raise RuntimeError("Worker 进程未运行")
        try:
            line = json.dumps(data, ensure_ascii=False) + "\n"
            self._worker_process.stdin.write(line)
            self._worker_process.stdin.flush()
        except (BrokenPipeError, OSError) as e:
            raise RuntimeError(f"向 Worker 写入失败（进程可能已崩溃）: {e}") from e

    async def _read_worker_response(self) -> dict:
        """从 worker stdout 读取一行 JSON 响应（阻塞读取，通过 executor 异步化）。

        Returns:
            解析后的 JSON 字典

        Raises:
            RuntimeError: worker 已退出或 JSON 解析失败
        """
        if self._worker_process is None or self._worker_process.stdout is None:
            raise RuntimeError("Worker 进程未运行")

        loop = asyncio.get_running_loop()
        line = await loop.run_in_executor(
            self._获取stdout_executor(), self._worker_process.stdout.readline
        )

        if not line:
            # EOF - worker 已退出
            exit_code = self._worker_process.poll()
            raise RuntimeError(f"Worker 进程已退出（返回码 {exit_code}）")

        line = line.strip()
        if not line:
            raise RuntimeError("Worker 返回空行")

        try:
            return json.loads(line)
        except json.JSONDecodeError as e:
            raise RuntimeError(f"Worker 响应 JSON 解析失败: {line!r} ({e})") from e

    async def _drain_until_done(self):
        """消费 worker stdout 直到读到 done/error/EOF，用于提前中断流后清理残留消息。

        当 _read_worker_stream 的调用者通过 break 提前退出 async for 循环时，
        generator 被关闭但 worker 可能还未发送 done 消息。此方法确保在下一轮
        通信开始前管道中没有残留数据。
        """
        loop = asyncio.get_running_loop()
        drained = 0
        while True:
            if self._worker_process is None or self._worker_process.stdout is None:
                break
            try:
                line = await asyncio.wait_for(
                    loop.run_in_executor(
                        self._获取stdout_executor(),
                        self._worker_process.stdout.readline,
                    ),
                    timeout=30,
                )
            except asyncio.TimeoutError:
                logger.warning("[drain] 等待done消息超时(30s)，管道可能已阻塞")
                break
            if not line:
                logger.debug(f"[drain] stdout EOF, 已消耗{drained}条残留消息")
                break
            line_stripped = line.strip()
            if not line_stripped:
                continue
            try:
                data = json.loads(line_stripped)
            except json.JSONDecodeError:
                drained += 1
                continue
            msg_type = data.get("type")
            drained += 1
            if msg_type == "done":
                logger.debug(f"[drain] 已消耗done消息, 共消耗{drained}条残留")
                return
            elif msg_type == "error":
                logger.debug(f"[drain] 消耗到error消息: {data.get('message','')}, 共消耗{drained}条")
                return
            else:
                # 仅在残留消息超过 5 条时打一次 DEBUG 警告，避免日志噪音
                if drained == 6:
                    logger.debug(f"[drain] 残留消息超过5条，继续消耗中（type={msg_type}）")

    async def _read_worker_stream(self):
        """异步逐行读取 worker stdout，yield token。

        读取流式响应：
        - {"type": "token", "content": "..."} -> yield content
        - {"type": "done", "full_text": "..."} -> 结束迭代
        - {"type": "error", "message": "..."} -> 抛出异常

        Yields:
            str: 每个 token 文本片段
        """
        loop = asyncio.get_running_loop()
        chunk_count = 0
        复读检测 = _复读检测器()
        _已触发复读停止 = False
        while True:
            if self._worker_process is None or self._worker_process.stdout is None:
                raise RuntimeError("Worker 进程未运行")

            line = await loop.run_in_executor(
                self._获取stdout_executor(), self._worker_process.stdout.readline
            )
            if not line:
                # EOF
                logger.debug(f"[流式对话] Worker stdout EOF, 已收到chunks数={chunk_count}")
                break

            if chunk_count == 0:
                logger.debug(f"[流式对话] 收到第一个原始行: {repr(line[:100])}")

            line = line.strip()
            if not line:
                continue

            try:
                data = json.loads(line)
            except json.JSONDecodeError:
                logger.warning(f"Worker 流式响应 JSON 解析失败: {line!r}")
                continue

            msg_type = data.get("type")
            if msg_type == "token":
                content = data.get("content", "")
                if content:
                    chunk_count += 1
                    if _已触发复读停止:
                        # 已发停止信号：吞掉 worker 停止前（每 10 token 检查一次）的残余 token
                        continue
                    if 复读检测.喂入(content):
                        _已触发复读停止 = True
                        logger.warning(
                            f"[复读熔断] 检测到流式输出复读退化，已发送停止信号（已输出 chunks={chunk_count}）"
                        )
                        self._signal_worker_stop()
                        yield "\n\n[已检测到模型复读输出，自动停止本次生成]\n"
                        continue
                    yield content
            elif msg_type == "done":
                logger.debug(f"[流式对话] 收到done消息, 总token chunks={chunk_count}")
                return
            elif msg_type == "error":
                raise RuntimeError(data.get("message", "Worker 推理错误"))
            elif msg_type == "progress":
                # 进度消息，记录日志但不 yield
                logger.debug(f"[Worker进度] {data.get('message', '')}")
            else:
                logger.warning(f"Worker 未知消息类型: {msg_type}")

    def _终止worker(self):
        """强制终止 worker 进程"""
        if self._worker_process is not None:
            try:
                # 尝试发送 exit 命令优雅退出
                if self._worker_process.poll() is None and self._worker_process.stdin:
                    try:
                        self._worker_process.stdin.write(json.dumps({"action": "exit"}) + "\n")
                        self._worker_process.stdin.flush()
                        # 等待 2 秒
                        try:
                            self._worker_process.wait(timeout=2)
                        except subprocess.TimeoutExpired:
                            pass
                    except (BrokenPipeError, OSError):
                        pass

                # 如果还没退出，强制终止
                if self._worker_process.poll() is None:
                    self._worker_process.terminate()
                    try:
                        self._worker_process.wait(timeout=3)
                    except subprocess.TimeoutExpired:
                        self._worker_process.kill()
            except OSError as e:
                logger.debug(f"终止 Worker 时异常（忽略）: {e}")
            finally:
                self._worker_process = None

    def __del__(self):
        """析构时终止 worker 进程"""
        self._终止worker()

    async def shutdown(self):
        """优雅关闭 Worker 子进程并释放资源"""
        if self._worker_process is None:
            return
        try:
            self._worker_process.terminate()
            # 等待进程退出（最多3秒）
            try:
                await asyncio.wait_for(
                    asyncio.to_thread(self._worker_process.wait),
                    timeout=3.0
                )
            except asyncio.TimeoutError:
                self._worker_process.kill()
                logger.warning("[本地模型客户端] Worker 进程未响应 terminate，已强制 kill")
            logger.info("[本地模型客户端] Worker 进程已清理")
        except Exception as e:
            logger.error(f"[本地模型客户端] 清理 Worker 失败: {e}")
        finally:
            self._worker_process = None
            self.当前模型名 = None
            # 关闭 stdout 读取 executor（不等待可能卡住的 readline，
            # 进程已终止后其 readline 会返回 EOF，线程自然退出）
            if self._stdout_executor is not None:
                self._stdout_executor.shutdown(wait=False)
                self._stdout_executor = None

    # ============================================================
    #  模型加载
    # ============================================================

    async def 异步加载模型(self, 模型路径: str):
        """异步加载模型（通过 worker 子进程）。

        双重检查锁定：如果模型已加载且匹配，直接返回。

        Raises:
            RuntimeError: 模型在黑名单中、加载失败或 worker 异常
            FileNotFoundError: 模型路径不存在
        """
        模型名 = Path(模型路径).name

        # 第一次检查：模型已加载且匹配则直接返回
        if self.当前模型名 is not None and self.当前模型名 == 模型名:
            # 需要确认 worker 仍在运行
            if self._worker_process is not None and self._worker_process.poll() is None:
                return

        async with self._获取异步锁():
            # 第二次检查：获得锁后再次确认
            if self.当前模型名 is not None and self.当前模型名 == 模型名:
                if self._worker_process is not None and self._worker_process.poll() is None:
                    return

            # 黑名单检查
            if self._在黑名单中(模型名):
                raise RuntimeError(f"模型 {模型名} 最近加载失败，请等待5分钟后重试")

            if not Path(模型路径).exists():
                self.加载失败历史[模型名] = time.time()
                raise FileNotFoundError(f"模型路径不存在: {模型路径}")

            # 确保 worker 运行
            await self._确保worker运行()

            # 读取设置并构建加载命令
            settings = load_settings()

            self._send_to_worker({
                "action": "load",
                "model_path": 模型路径,
                "settings": settings,
            })

            # 等待加载结果（可能收到多条 progress 消息，最终是 loaded 或 error）
            try:
                resp = await asyncio.wait_for(
                    self._等待worker加载结果(模型名),
                    timeout=300,  # 模型加载最长 5 分钟
                )
            except asyncio.TimeoutError:
                self.加载失败历史[模型名] = time.time()
                raise RuntimeError(f"模型加载超时（300秒）: {模型名}")

            if resp.get("type") == "error":
                self.加载失败历史[模型名] = time.time()
                raise RuntimeError(resp.get("message", "模型加载失败"))

            # 加载成功
            self.当前模型名 = resp.get("model_name", 模型名)
            info = resp.get("info", "")
            # 解析 worker 返回的加速信息，更新本地状态
            self._解析加速信息(info)
            self._is_multimodal = resp.get("is_multimodal", False)
            logger.info(f"✅ 本地模型加载成功: {self.当前模型名} ({info})")

    async def _等待worker加载结果(self, 模型名: str) -> dict:
        """等待 worker 加载模型的最终结果（跳过 progress 消息）"""
        while True:
            resp = await self._read_worker_response()
            msg_type = resp.get("type")
            if msg_type == "progress":
                logger.info(f"[Worker] {resp.get('message', '')}")
                continue
            if msg_type == "loaded":
                return resp
            if msg_type == "error":
                return resp
            # 未知类型，也返回
            logger.warning(f"Worker 加载过程中收到未知消息: {resp}")
            return resp

    def _解析加速信息(self, info: str):
        """从 worker 返回的加速信息字符串中解析量化/dtype 状态"""
        if "4bit" in info:
            self._current_quantization = "4bit"
        elif "8bit" in info:
            self._current_quantization = "8bit"
        else:
            self._current_quantization = "none"

        if "bfloat16" in info:
            self._current_dtype = "bfloat16"
        elif "float16" in info:
            self._current_dtype = "float16"
        elif "int8" in info:
            self._current_dtype = "int8"
        elif "nf4" in info:
            self._current_dtype = "float16(nf4)"
        else:
            self._current_dtype = "float32"

    async def 异步卸载模型(self):
        """异步卸载模型（通过 worker）"""
        async with self._获取异步锁():
            await self._卸载模型_internal()

    async def _卸载模型_internal(self):
        """内部卸载实现（需在锁保护下调用）

        直接终止 worker 进程而非仅发送 unload：仅卸载模型仍会残留
        CUDA context（约 300~500MB 显存）与 torch 进程内存（约 2GB），
        进程退出才能彻底归零；下次聊天时自动重启 worker（约 6 秒）。
        _终止worker 会先发送 exit 指令优雅退出，超时才强制 terminate。
        """
        if self._worker_process is not None and self._worker_process.poll() is None:
            await asyncio.to_thread(self._终止worker)

        self.当前模型名 = None
        self._current_quantization = "none"
        self._current_dtype = "float32"
        self._is_multimodal = False

    def 卸载模型(self):
        """主动卸载模型释放显存（同步版本）"""
        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                # 如果在事件循环中，创建一个 task
                asyncio.ensure_future(self.异步卸载模型())
            else:
                loop.run_until_complete(self.异步卸载模型())
        except RuntimeError:
            # 没有事件循环，直接终止 worker
            self._终止worker()
            self.当前模型名 = None

        logger.info("本地模型已卸载，显存已释放")

    def 重置模型状态(self, 模型名称=None):
        """清除失败黑名单

        Args:
            模型名称: 指定要清除的模型名，为 None 时清除全部

        Returns:
            bool: 操作是否成功
        """
        if 模型名称:
            if 模型名称 in self.加载失败历史:
                del self.加载失败历史[模型名称]
                return True
            return False
        else:
            self.加载失败历史.clear()
            return True

    def _在黑名单中(self, 模型名):
        """检查是否在失败黑名单（5分钟过期）"""
        if 模型名 not in self.加载失败历史:
            return False
        if time.time() - self.加载失败历史[模型名] > self.失败黑名单过期秒:
            del self.加载失败历史[模型名]
            return False
        return True

    # ============================================================
    #  视觉能力检测
    # ============================================================

    def _supports_vision(self, model_name: str = None) -> bool:
        """检测当前本地模型是否支持视觉多模态

        优先使用 Worker 加载时返回的 is_multimodal 标志。
        若模型未加载（或被空闲卸载），则读取 config.json 离线检测，
        避免模型未加载时误报"不支持图片分析"。

        Args:
            model_name: 可选，外部传入的模型名（如路由层从请求中读取的）。
                        优先于设置文件中的值，避免刚切换模型但设置尚未落盘时读到旧值。
        """
        if self._is_multimodal:
            return True
        # 模型未加载时，从 config.json 离线检测
        model_path = self._获取模型路径(model_name)
        if model_path is None:
            logger.debug(f"[视觉检测] 无法解析模型路径 (model_name={model_name!r})")
            return False
        config_path = Path(model_path) / "config.json"
        if not config_path.exists():
            logger.debug(f"[视觉检测] config.json 不存在: {config_path}")
            return False
        try:
            with open(config_path, 'r', encoding='utf-8') as f:
                config = json.load(f)
            # 检查 architectures 字段是否包含视觉模型类名
            for arch in config.get("architectures", []):
                arch_lower = str(arch).lower()
                if any(kw in arch_lower for kw in ["vl", "vision", "visual", "imagetext", "multimodal"]):
                    return True
            # 检查是否存在 vision_config（Qwen3.5 等模型的核心标志）
            if "vision_config" in config:
                return True
            # 检查 preprocessor_config.json 是否包含视觉处理器类名
            preprocessor_path = Path(model_path) / "preprocessor_config.json"
            if preprocessor_path.exists():
                try:
                    with open(preprocessor_path, 'r', encoding='utf-8') as f:
                        pre_config = json.load(f)
                    proc_class = str(pre_config.get("processor_class", "")).lower()
                    if any(kw in proc_class for kw in ["vl", "vision", "visual", "multimodal"]):
                        return True
                except Exception:
                    pass
        except Exception:
            pass
        return False

    def _supports_audio(self, model_name: str = None) -> bool:
        """检测当前本地模型是否支持音频输入

        两重判定（任一命中即支持）：
        - config.json 含 audio_config（多模态音频模型的核心标志）
        - 系列档案的「音频支持」规格：如 Gemma 4 仅 12B/E2B/E4B 支持，26B/31B 不支持
        """
        model_path = self._获取模型路径(model_name)
        if model_path is not None:
            config_path = Path(model_path) / "config.json"
            if config_path.exists():
                try:
                    with open(config_path, 'r', encoding='utf-8') as f:
                        config = json.load(f)
                    if "audio_config" in config:
                        return True
                    for arch in config.get("architectures", []):
                        if "audio" in str(arch).lower():
                            return True
                except Exception:
                    pass
        # 系列档案兜底（config 无音频标志但家族规格明确支持时）
        name = (model_name or "").lower()
        音频支持 = 匹配系列档案(model_path or "", model_name or "").get("音频支持", ())
        if 音频支持 and any(k in name for k in 音频支持):
            return True
        return False

    def _获取模型路径(self, model_name: str = None) -> Optional[str]:
        """从设置中获取当前配置的本地模型路径（不依赖模型是否已加载）

        Args:
            model_name: 外部传入的模型名，优先于设置文件中的值。
        """
        try:
            settings = load_settings()
        except Exception:
            settings = {}
        # 有自定义路径时直接返回（优先级最高）
        local_path = (settings.get("local_path") or "").strip()
        if local_path:
            return local_path
        # 使用模型名拼接默认路径
        local_model_name = (model_name or "").strip()
        if not local_model_name:
            # 回退到设置文件中的值
            local_model_name = (settings.get("local_model_name") or "").strip()
        if local_model_name:
            from 后端.系统环境映射 import get_default_llm_path
            return str(get_default_llm_path() / local_model_name)
        return None

    # ============================================================
    #  非流式推理
    # ============================================================

    async def generate_response(self, system_prompt, messages_history):
        """本地推理生成回复（通过 worker，超时保护 + thinking剥离）

        Args:
            system_prompt: 系统提示词
            messages_history: 消息历史列表 [{"role": ..., "content": ...}]

        Returns:
            str: 生成的回复文本，或 "[错误]: ..." / "[推理超时]: ..." 格式的错误信息
        """
        开始时间 = time.time()

        # 检查 worker 是否运行
        if self._worker_process is None or self._worker_process.poll() is not None:
            self._性能.记录请求(time.time() - 开始时间, False)
            return "[错误]: 本地模型未加载，请先在设置中选择模型"

        # 构建消息
        formatted_messages = [{"role": "system", "content": system_prompt}]
        for msg in messages_history:
            entry = {"role": msg["role"], "content": msg.get("content", "")}
            if "name" in msg:
                entry["name"] = msg["name"]
            if "tool_calls" in msg:
                entry["tool_calls"] = msg["tool_calls"]
            if "tool_call_id" in msg:
                entry["tool_call_id"] = msg["tool_call_id"]
            if "_image_attachments" in msg:
                entry["_image_attachments"] = msg["_image_attachments"]
            if "_audio_attachments" in msg:
                entry["_audio_attachments"] = msg["_audio_attachments"]
            formatted_messages.append(entry)

        async with self._获取异步锁():
            try:
                self._send_to_worker({
                    "action": "generate",
                    "messages": formatted_messages,
                    "settings": {},
                })

                # 等待推理结果
                resp = await asyncio.wait_for(
                    self._read_worker_response(),
                    timeout=self._INFER_TIMEOUT,
                )

                if resp.get("type") == "error":
                    self._性能.记录请求(time.time() - 开始时间, False)
                    return f"[推理错误]: {resp.get('message', '未知错误')}"

                if resp.get("type") == "done":
                    result = resp.get("full_text", "")
                    # 代理层双保险：与 worker._strip_thinking 保持一致，
                    # 支持 Qwen3.5 的 <think>、Qwen3 的 <thinking> 和 Gemma 4 的通道标记
                    result = re.sub(r'<think>[\s\S]*?</think>', '', result)
                    result = re.sub(r'<thinking>[\s\S]*?</thinking>', '', result)
                    # 清理孤立闭合标签（tokenizer skip_special_tokens 已移除 <think>）
                    result = re.sub(r'</think>', '', result)
                    result = re.sub(r'</thinking>', '', result)
                    # Gemma 4：通道块、未闭合孤立开头（剥到末尾）与孤立闭合标记
                    result = re.sub(r'<\|channel>thought[\s\S]*?<channel\|>', '', result)
                    result = re.sub(r'<\|channel>thought[\s\S]*$', '', result)
                    result = result.replace('<channel|>', '').strip()
                    self._性能.记录请求(time.time() - 开始时间, True)
                    return result

                # 未知响应类型
                self._性能.记录请求(time.time() - 开始时间, False)
                return f"[推理错误]: Worker 返回未知响应类型: {resp.get('type', '?')}"

            except asyncio.TimeoutError:
                self._性能.记录请求(time.time() - 开始时间, False)
                return f"[推理超时]: 本地推理耗时超过{self._INFER_TIMEOUT}秒，可能是模型过大或显存不足"
            except RuntimeError as e:
                self._性能.记录请求(time.time() - 开始时间, False)
                return f"[推理错误]: {str(e)}"
            except (OSError, ValueError) as e:
                self._性能.记录请求(time.time() - 开始时间, False)
                return f"[推理错误]: {str(e)}"

    # ============================================================
    #  代码补全（complete action）
    # ============================================================

    async def generate_completion(self, system_prompt, prefix, suffix=None, settings=None):
        """代码补全专用方法。

        通过 worker 的 complete action 进行低温度、短输出的代码补全推理。
        超时 15 秒，出错时返回空字符串（不抛异常）。

        :param system_prompt: 补全系统提示词
        :param prefix: 光标前的代码内容
        :param suffix: 光标后的代码内容（可选）
        :param settings: 设置字典，含 temperature, max_new_tokens, stop_sequences
        :return: 补全文本字符串
        """
        # 构建用户消息（与 API 客户端相同格式）
        user_content = f"<|prefix|>\n{prefix}\n<|cursor|>"
        if suffix:
            user_content += f"\n<|suffix|>\n{suffix}"

        formatted_messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_content},
        ]

        # 合并 settings（提供默认值）
        _settings = settings or {}
        complete_settings = {
            "temperature": _settings.get("temperature", 0.1),
            "max_new_tokens": _settings.get("max_new_tokens", 100),
            "stop_sequences": _settings.get(
                "stop_sequences", ["\n\n\n", "class ", "def ", "\n#"]
            ),
        }

        # 检查 worker 是否运行
        if self._worker_process is None or self._worker_process.poll() is not None:
            return ""

        async with self._获取异步锁():
            try:
                self._send_to_worker({
                    "action": "complete",
                    "messages": formatted_messages,
                    "settings": complete_settings,
                })

                resp = await asyncio.wait_for(
                    self._read_worker_response(),
                    timeout=15,
                )

                if resp.get("status") == "success":
                    result = resp.get("response", "")
                    # thinking 标签剥离（双保险，与 generate_response 一致）
                    result = re.sub(r'<think>[\s\S]*?</think>', '', result)
                    result = re.sub(r'<thinking>[\s\S]*?</thinking>', '', result)
                    result = re.sub(r'</think>', '', result)
                    result = re.sub(r'</thinking>', '', result)
                    # Gemma 4：通道块、未闭合孤立开头（剥到末尾）与孤立闭合标记
                    result = re.sub(r'<\|channel>thought[\s\S]*?<channel\|>', '', result)
                    result = re.sub(r'<\|channel>thought[\s\S]*$', '', result)
                    result = result.replace('<channel|>', '').strip()
                    return result
                else:
                    return ""

            except (asyncio.TimeoutError, RuntimeError, OSError, ValueError):
                return ""

    # ============================================================
    #  非流式工具调用循环
    # ============================================================

    async def generate_response_with_tools(self, system_prompt, messages_history, tools=None, tool_executor=None):
        """非流式工具调用循环（最多15轮）

        Args:
            system_prompt: 系统提示词
            messages_history: 消息历史列表
            tools: 工具定义列表（用于注入说明）
            tool_executor: 异步工具执行函数 async (name, args) -> str

        Returns:
            最终的纯文本回复（已去除tool_call标记）
        """
        # 工具调用最大轮次可配置（默认25；-1/0/"无限" 表示不限制，兜底9999防死循环）
        _settings = load_settings()
        _配置轮次 = _settings.get("max_tool_rounds", 25)
        try:
            max_rounds = 9999 if _配置轮次 in (-1, 0, "无限", "unlimited") else int(_配置轮次)
        except (TypeError, ValueError):
            max_rounds = 25

        # 构建完整消息列表（含 system），并健壮地注入工具说明
        working_messages = [{"role": "system", "content": system_prompt}]
        working_messages.extend(list(messages_history))

        if tools:
            tool_desc = _TOOL_INSTRUCTION_TEMPLATE
            if working_messages and working_messages[0].get("role") == "system":
                working_messages[0] = {
                    **working_messages[0],
                    "content": working_messages[0]["content"] + "\n" + tool_desc
                }
            else:
                working_messages.insert(0, {"role": "system", "content": tool_desc})
            logger.debug(
                f"[工具调用] 工具指令已注入，messages[0] role={working_messages[0]['role']}, "
                f"长度={len(working_messages[0]['content'])}"
            )

        enhanced_prompt = working_messages[0]["content"]
        # 复制消息历史避免修改原始数据
        history_messages = working_messages[1:]

        reply = ""
        _进度工具统计 = {}     # 工具名 → 成功执行次数（轮次上限时的进度摘要用）
        _进度修改文件 = set()  # 已成功写入/编辑的文件路径
        for round_idx in range(max_rounds):
            # 调用改造后的生成方法
            reply = await self.generate_response(enhanced_prompt, history_messages)

            logger.debug(
                f"[工具调用] 第{round_idx+1}轮完成，回复长度={len(reply)}，"
                f"包含tool_call={has_tool_calls(reply)}"
            )

            # 检查是否包含工具调用
            if not has_tool_calls(reply):
                # 无工具调用，返回清理后的文本
                return strip_tool_calls(reply)

            # 解析工具调用
            content, calls = separate_content_and_calls(reply)

            if not calls:
                return strip_tool_calls(reply)

            # 将模型回复加入历史（结构化 tool_calls）
            assistant_msg = {"role": "assistant", "content": content.strip()}
            if calls:
                assistant_msg["tool_calls"] = [
                    {
                        "id": f"call_{round_idx}_{i}",
                        "type": "function",
                        "function": {"name": call["name"], "arguments": call.get("arguments", {})}
                    }
                    for i, call in enumerate(calls)
                ]
            history_messages.append(assistant_msg)

            # 执行每个工具调用
            for i, call in enumerate(calls):
                tool_name = call.get("name", "")
                tool_args = call.get("arguments", {})

                # 前置检查：空参数 + 非无参工具 → 直接拒绝
                _NO_ARGS_TOOLS = {"list_plugin_files"}
                if not tool_args and tool_name not in _NO_ARGS_TOOLS:
                    logger.warning(f"[非流式工具调用] 工具 {tool_name} 未提供参数，跳过执行")
                    err = f"❌ 错误：工具 {tool_name} 调用缺少参数。请使用 <tool_call> 标签格式调用工具。"
                    tool_result_text = format_tool_result(tool_name, err, success=False)
                    history_messages.append({
                        "role": "tool", "name": tool_name,
                        "content": tool_result_text,
                        "tool_call_id": f"call_{round_idx}_{i}",
                    })
                    continue

                try:
                    if tool_executor:
                        result = await tool_executor(tool_name, tool_args)
                    else:
                        result = f"工具 {tool_name} 不可用"
                    tool_result_text = format_tool_result(tool_name, result, success=True)
                    _记录工具进度(tool_name, tool_args, _进度工具统计, _进度修改文件)
                except (RuntimeError, OSError, ValueError, TypeError) as e:
                    tool_result_text = format_tool_result(tool_name, str(e), success=False)

                # 工具结果作为 tool 角色消息反馈
                history_messages.append({
                    "role": "tool", "name": tool_name,
                    "content": tool_result_text,
                    "tool_call_id": f"call_{round_idx}_{i}",
                })

        # 超过最大轮次：返回最后的回复 + 统一的上限提示（含进度摘要与续接引导）
        logger.warning(f"工具调用超过最大轮次 {max_rounds}")
        _正文 = strip_tool_calls(reply)
        上限提示 = 构建轮次上限提示(max_rounds, _进度工具统计, _进度修改文件)
        return (_正文 + 上限提示) if _正文.strip() else 上限提示.lstrip()

    # ============================================================
    #  流式对话
    # ============================================================

    async def 流式对话(self, messages: list, settings: dict):
        """本地模型流式对话 - yield 每个 token chunk

        通过 worker 子进程的 stream 命令获取流式输出，
        逐行读取 worker stdout 中的 token 消息。

        Args:
            messages: 消息列表（含 system）
            settings: 设置字典

        Yields:
            str: 每个 token 文本片段
        """
        # 检查 worker 是否运行
        if self._worker_process is None or self._worker_process.poll() is not None:
            yield "[错误]: 本地模型未加载，请先在设置中选择模型"
            return

        # 清理消息中的非标准字段（保留 name/tool_calls/tool_call_id 以支持 Qwen3.5 tool 角色）
        sanitized_messages = []
        _has_images = False
        for m in messages:
            if not isinstance(m, dict):
                continue
            msg = {"role": m.get("role"), "content": m.get("content", "")}
            if "name" in m:
                msg["name"] = m["name"]
            if "tool_calls" in m:
                msg["tool_calls"] = m["tool_calls"]
            if "tool_call_id" in m:
                msg["tool_call_id"] = m["tool_call_id"]
            if "_image_attachments" in m:
                msg["_image_attachments"] = m["_image_attachments"]
                _has_images = True
            if "_audio_attachments" in m:
                msg["_audio_attachments"] = m["_audio_attachments"]
            sanitized_messages.append(msg)
        logger.info(
            f"[流式对话] messages总数={len(sanitized_messages)}, "
            f"_has_images={_has_images}, "
            f"_is_multimodal={self._is_multimodal}"
        )

        async with self._获取异步锁():
            try:
                logger.debug(f"[流式对话] 开始从Worker读取流, messages数={len(sanitized_messages)}")
                self._send_to_worker({
                    "action": "stream",
                    "messages": sanitized_messages,
                    "settings": settings or {},
                })

                # 逐行读取流式 token
                _stream_chunk_count = 0
                async for token in self._read_worker_stream():
                    _stream_chunk_count += 1
                    yield token
                logger.debug(f"[流式对话] Worker流读取结束, 总chunks数={_stream_chunk_count}")

            except RuntimeError as e:
                yield f"\n\n[推理错误]: {str(e)}"
            except asyncio.TimeoutError:
                yield f"\n\n[错误: 本地推理超时({self._INFER_TIMEOUT}秒)]"
            except (OSError, ValueError) as e:
                yield f"\n\n[推理错误]: {str(e)}"

    # ============================================================
    #  流式工具调用循环
    # ============================================================

    async def 流式对话_with_tools(self, messages: list, settings: dict, tools=None, tool_executor=None):
        """流式工具调用循环

        Args:
            messages: 消息列表（含system）
            settings: 设置字典
            tools: 工具定义列表
            tool_executor: 异步工具执行函数

        Yields:
            文本片段（已过滤tool_call标记，含工具执行进度提示）
        """
        # 工具调用最大轮次可配置（默认25；-1/0/"无限" 表示不限制，兜底9999防死循环）
        _配置轮次 = settings.get("max_tool_rounds", 25)
        try:
            max_rounds = 9999 if _配置轮次 in (-1, 0, "无限", "unlimited") else int(_配置轮次)
        except (TypeError, ValueError):
            max_rounds = 25

        # 将历史平文回复添加到累积内容，超限时作为备用输出
        accumulated_content = ""
        _进度工具统计 = {}     # 工具名 → 成功执行次数（轮次上限时的进度摘要用）
        _进度修改文件 = set()  # 已成功写入/编辑的文件路径
        # 连续失败熔断：检测相同工具+参数的重复失败，避免死循环
        _last_failed_call = None  # (tool_name, args_json_str)
        _consecutive_failures = 0
        # 裸标签检测标志：上一轮检测到裸 <function=xxx> 格式时为 True，
        # 用于在下一轮重新启用 thinking 帮助模型纠正格式
        _bare_tag_detected = False
        # 跨轮裸标签熔断计数：某一轮以裸标签格式错误结束时 +1，
        # 任意一轮正常成功则清零；连续达到 3 轮立即中断主循环
        _consecutive_bare_tag_rounds = 0

        # 注入工具说明到系统消息
        working_messages = list(messages)
        if tools:
            tool_desc = _TOOL_INSTRUCTION_TEMPLATE
            if working_messages and working_messages[0].get("role") == "system":
                working_messages[0] = {
                    **working_messages[0],
                    "content": working_messages[0]["content"] + "\n" + tool_desc
                }
            else:
                working_messages.insert(0, {"role": "system", "content": tool_desc})
            logger.debug(
                f"[工具调用] 工具指令已注入，messages[0] role={working_messages[0]['role']}, "
                f"长度={len(working_messages[0]['content'])}"
            )

        _OPEN_TAG = '<tool_call>'
        _CLOSE_TAG = '</tool_call>'
        # Gemma 4 原生通道格式标记（4bit 量化下模型易回退到预训练模板格式，
        # 如 <|tool_call>call:名字{json}<tool_call|><|tool_response>），不拦截会整段泄漏给前端
        _GEMMA_OPEN_TAG = '<|tool_call>'
        _GEMMA_CLOSE_TAG = '<tool_call|>'
        _GEMMA_RESP_TAG = '<|tool_response>'

        # 轮间分隔：多轮工具循环的叙述文本在前端渲染时会粘连成一段，
        # 新一轮的首段正文前补空行与上一轮叙述隔开
        _曾输出正文 = False
        _本轮已输出正文 = False

        def _带轮间分隔(text: str) -> str:
            nonlocal _曾输出正文, _本轮已输出正文
            if not text:
                return text
            if not _本轮已输出正文:
                _本轮已输出正文 = True
                if _曾输出正文:
                    text = "\n\n" + text.lstrip("\n")
                _曾输出正文 = True
            return text

        for round_idx in range(max_rounds):
            full_reply = ""
            buffer = ""
            in_tool_call = False  # 是否已确认进入 tool_call 区域
            _bare_tag_warned = False  # P2：本轮裸标签 warning 是否已记（降噪，每轮重置）
            _本轮已输出正文 = False  # 轮间分隔标记（每轮重置）

            # 工具调用后续轮次：禁用 thinking 模式 + 降低 max_new_tokens
            # 首轮保持 thinking=True 让模型充分思考，后续轮次只需输出简短的 JSON 工具调用
            # 例外：上一轮检测到裸标签格式错误时——仅第 1 次纠正启用 thinking；
            # 若之后仍失败，则切换为 enable_thinking=False 的简化模式（长思考反而加重截断）
            round_settings = dict(settings) if settings else {}
            if round_idx > 0:
                if _bare_tag_detected:
                    if _consecutive_bare_tag_rounds <= 1:
                        # 第 1 次纠正：启用 thinking + 提高 token 上限，给模型纠正空间
                        round_settings["enable_thinking"] = True
                        round_settings["is_tool_round"] = False
                        logger.info("[工具调用] 上轮裸标签格式错误，本轮首次纠正：启用 thinking 模式")
                    else:
                        # 已多次纠正仍失败：thinking 反而加重截断，切换 enable_thinking=False 简化模式重试
                        round_settings["enable_thinking"] = False
                        round_settings["is_tool_round"] = True
                        logger.info(
                            "[工具调用] 上轮裸标签格式错误且已多次纠正，"
                            "切换 enable_thinking=False 简化模式重试"
                        )
                else:
                    round_settings["enable_thinking"] = False
                    round_settings["is_tool_round"] = True
            _bare_tag_detected = False  # 重置，由本轮流式循环重新设置

            logger.debug(f"[工具调用] 第{round_idx+1}轮流式生成开始, messages数={len(working_messages)}")
            for _di, _dm in enumerate(working_messages):
                _drole = _dm.get("role", "?")
                _dclen = len(str(_dm.get("content", "")))
                _dhas_tc = "tool_calls" in _dm
                _dhas_name = "name" in _dm
                logger.debug(
                    f"  [{_di}] role={_drole}, content_len={_dclen}, "
                    f"has_tool_calls={_dhas_tc}, has_name={_dhas_name}"
                )

            try:
                async with asyncio.timeout(self._INFER_TIMEOUT):
                    async for chunk in self.流式对话(working_messages, round_settings):
                        full_reply += chunk
                        buffer += chunk

                        if in_tool_call:
                            # 已在 tool_call 区域内，累积直到看到 close tag（含 Gemma 通道闭合标记）
                            if (_CLOSE_TAG in buffer or _GEMMA_CLOSE_TAG in buffer
                                    or _GEMMA_RESP_TAG in buffer):
                                break  # 工具调用完整，中断流
                            # 检测 <tool_call> 内混入裸 <function=xxx> 格式（无JSON参数的混合格式）
                            # 注意：只在确认是混合格式时标记，不 break
                            # （避免误判 thinking 内容中提到的 <function=xxx> 文字）
                            if '<function=' in buffer and '>' in buffer[buffer.index('<function='):]:
                                # 检查 function= 和 > 之间是否有 { （有则说明是混合格式而非纯文字提及）
                                _func_start = buffer.index('<function=')
                                _gt_pos = buffer.index('>', _func_start)
                                _between = buffer[_func_start:_gt_pos]
                                _after_gt = buffer[_gt_pos+1:_gt_pos+20]  # > 后面的内容片段
                                # 排除: JSON格式(有{或"), XML参数格式(>后有<parameter=)
                                if '{' not in _between and '"' not in _between and '<parameter=' not in _after_gt:
                                    # 纯裸标签（如 <function=read_plugin_file>），无 JSON 或 XML 参数
                                    _bare_tag_detected = True
                                    # P2 降噪：仅本轮首次检测到时记 warning，同轮后续不重复
                                    if not _bare_tag_warned:
                                        _bare_tag_warned = True
                                        logger.warning(
                                            "[工具调用] 检测到 <tool_call> 内混入裸 <function=> 标签"
                                        )
                                    else:
                                        logger.debug("[工具调用] 同轮再次检测到裸 <function=> 标签（已降噪）")
                            continue

                        # 检查 buffer 是否可能是 <tool_call> 的开头
                        # 关键改进：只有 buffer 末尾严格匹配 _OPEN_TAG 前缀时才暂缓
                        pending_start = buffer.rfind('<')

                        if pending_start == -1:
                            # buffer 中没有 '<'，全部安全输出
                            yield _带轮间分隔(buffer)
                            buffer = ""
                        else:
                            # 有 '<'，检查从 '<' 开始的部分是否是 _OPEN_TAG 的合法前缀
                            tail = buffer[pending_start:]

                            if _OPEN_TAG.startswith(tail) or _GEMMA_OPEN_TAG.startswith(tail):
                                # tail 是 '<tool_call>' 或 '<|tool_call>' 的有效前缀（如 '<', '<t', '<|', ...）
                                # 输出 '<' 之前的部分，保留 tail 继续观察
                                safe_part = buffer[:pending_start]
                                if safe_part:
                                    yield _带轮间分隔(safe_part)
                                buffer = tail

                                # 如果 tail 已经完全匹配开标记
                                if tail in (_OPEN_TAG, _GEMMA_OPEN_TAG):
                                    in_tool_call = True
                                    # 不 yield，继续累积 tool_call 内容

                            elif _OPEN_TAG in buffer or _GEMMA_OPEN_TAG in buffer:
                                # buffer 包含完整的 <tool_call> 或 <|tool_call>
                                in_tool_call = True
                                if (_CLOSE_TAG in buffer or _GEMMA_CLOSE_TAG in buffer
                                        or _GEMMA_RESP_TAG in buffer):
                                    break  # 完整的 tool_call 对
                            else:
                                # tail 不是 <tool_call> 的有效前缀
                                # 不在此处 break（避免误判 thinking 内容中提到的 <function=xxx> 文字）
                                # 安全输出全部 buffer，让解析器后续处理
                                yield _带轮间分隔(buffer)
                                buffer = ""
                    else:
                        # for-else: 流正常结束（没有 break）
                        # 输出剩余 buffer（如果不包含未完成的 tool_call）
                        if buffer:
                            if not in_tool_call:
                                yield _带轮间分隔(buffer)
                            # 如果 in_tool_call 为 True 但没有 close tag，说明是不完整的调用
                            # 这种情况也输出（当作普通文本）
                            elif (_CLOSE_TAG not in buffer and _GEMMA_CLOSE_TAG not in buffer
                                    and _GEMMA_RESP_TAG not in buffer):
                                yield _带轮间分隔(buffer)

                        logger.debug(f"[工具调用] 第{round_idx+1}轮正常完成(无工具调用)，回复长度={len(full_reply)}")
                        return  # 没有工具调用，正常结束
            except (TimeoutError, asyncio.TimeoutError):
                logger.error(f"[工具调用] 第{round_idx+1}轮流式生成超时({self._INFER_TIMEOUT}s)")
                # 通知 worker 停止生成并清空管道残留，避免生成线程空烧显存
                # 及残留消息污染下一次请求
                self._signal_worker_stop()
                try:
                    await asyncio.wait_for(self._drain_until_done(), timeout=15)
                except asyncio.TimeoutError:
                    logger.warning("[工具调用] 超时后 drain 残留消息超时")
                yield "\n\n[错误: 本轮推理超时，已中断]\n"
                break

            # break 到这里：检测到完整的 tool_call
            logger.debug(f"[工具调用] 第{round_idx+1}轮完成，回复长度={len(full_reply)}，包含tool_call=True")

            # 发送停止信号给 worker，让它提前终止生成（减少无用 token 和 drain 等待时间）
            self._signal_worker_stop()

            # drain 该轮 worker 可能未发送的 done 消息，防止残留到下一轮
            # （_read_worker_stream 的调用者 break 提前中断了 generator，worker done 可能还在 pipe 里）
            # 增加总超时保护：防止 worker 持续生成大量残留 token 导致长时间阻塞
            try:
                await asyncio.wait_for(self._drain_until_done(), timeout=15)
            except asyncio.TimeoutError:
                logger.warning("[drain] 总超时(15s)，强制跳过残留清理")

            # 解析工具调用
            if not has_tool_calls(full_reply):
                # 误判，输出全部内容
                yield _带轮间分隔(buffer)
                return

            content, calls = separate_content_and_calls(full_reply)

            if not calls:
                yield _带轮间分隔(buffer)
                return

            # 检查是否所有调用都是空参数失败（裸标签格式错误）
            _NO_ARGS_TOOLS = {"list_plugin_files"}
            _all_empty_args = all(
                not c.get("arguments", {}) and c.get("name", "") not in _NO_ARGS_TOOLS
                for c in calls
            )

            if _all_empty_args:
                # 所有调用都是空参数失败（裸标签格式错误）
                # 不添加 assistant 消息，避免模型在下一轮看到自己的错误格式并重复
                logger.info("[工具调用] 所有调用都是空参数失败（裸标签），不保留错误的 assistant 消息")
                # 向用户输出可见提示（否则用户看到的是空白输出）
                _tool_names = ', '.join(c.get('name', '?') for c in calls)
                yield f"\n⚠️ 模型使用了非标准格式调用工具（{_tool_names}），正在自动纠正...\n"
            else:
                # 正常情况：添加 assistant 消息（含工具调用结构）
                assistant_msg = {"role": "assistant", "content": content.strip()}
                assistant_msg["tool_calls"] = [
                    {
                        "id": f"call_{round_idx}_{i}",
                        "type": "function",
                        "function": {"name": call["name"], "arguments": call.get("arguments", {})}
                    }
                    for i, call in enumerate(calls)
                ]
                working_messages.append(assistant_msg)

            # 执行工具
            for i, call in enumerate(calls):
                tool_name = call.get("name", "")
                tool_args = call.get("arguments", {})

                # 前置检查：空参数 + 非无参工具 → 直接拒绝，避免裸标签无JSON导致的死循环
                # list_plugin_files 是无参工具，允许空 arguments
                _NO_ARGS_TOOLS = {"list_plugin_files"}
                if not tool_args and tool_name not in _NO_ARGS_TOOLS:
                    logger.warning(
                        f"[工具调用] 工具 {tool_name} 未提供参数（模型可能输出了裸标签无JSON），"
                        f"跳过执行并注入错误反馈"
                    )
                    _bare_tag_detected = True
                    _ot = '<tool_call>'
                    _ct = '</tool_call>'
                    _example_json = '{"name":"' + tool_name + '","arguments":{"file_path":"__init__.py"}}'
                    err = (
                        f"❌ 你的工具调用格式错误，{tool_name} 缺少参数。"
                        f"你使用了错误的 <function=xxx> 格式，必须用 JSON 格式。\n"
                        f"正确示例（注意替换为你自己的参数）：{_ot}{_example_json}{_ct}"
                    )
                    if not _all_empty_args:
                        tool_result_text = format_tool_result(tool_name, err, success=False)
                        working_messages.append({
                            "role": "tool", "name": tool_name,
                            "content": tool_result_text,
                            "tool_call_id": f"call_{round_idx}_{i}",
                        })
                    continue

                yield f"\n[正在执行: {tool_name}...]\n"
                logger.info(f"[工具调用] 执行工具: {tool_name}, 参数keys={list(tool_args.keys())}")

                try:
                    if tool_executor:
                        result = await tool_executor(tool_name, tool_args)
                    else:
                        result = f"工具 {tool_name} 不可用"
                    tool_result_text = format_tool_result(tool_name, result, success=True)
                    _记录工具进度(tool_name, tool_args, _进度工具统计, _进度修改文件)
                    logger.debug(f"[工具调用] 工具 {tool_name} 执行成功, 结果摘要={str(result)[:100]}")
                except (RuntimeError, OSError, ValueError, TypeError) as e:
                    error_msg = str(e)
                    logger.error(f"[工具调用] 工具 {tool_name} 执行异常: {error_msg}")
                    # 将错误信息以 tool role 注入，让模型知道工具失败并调整策略
                    tool_result_text = format_tool_result(tool_name, f"工具执行失败：{error_msg}", success=False)
                    yield f"\n[工具执行失败: {tool_name} - {error_msg}]\n"

                working_messages.append({
                    "role": "tool", "name": tool_name,
                    "content": tool_result_text,
                    "tool_call_id": f"call_{round_idx}_{i}",
                })
                logger.debug(f"[工具调用] 工具结果已注入, working_messages长度={len(working_messages)}")

            # 裸标签格式错误恢复：注入 user 角色的格式指导
            # 比 tool 角色的错误消息更容易被模型理解和遵循
            if _all_empty_args and _bare_tag_detected:
                _first_tool = calls[0].get("name", "write_plugin_file") if calls else "write_plugin_file"
                # 使用完整、具体的示例 JSON，不用占位符（模型会复制占位符）
                _example_json = '{"name":"' + _first_tool + '","arguments":{"file_path":"__init__.py"}}'
                _guidance = (
                    "[系统格式纠正] 你上一轮的工具调用使用了错误的格式，参数丢失了。\n"
                    "错误原因：你使用了 <function=xxx> 格式，这个格式是错误的。\n"
                    "正确做法：在 <tool_call> 标签内写完整的 JSON。\n"
                    "下面是格式示例（你需要把 file_path 换成你实际要操作的文件路径）：\n"
                    f"<tool_call>{_example_json}"
                )
                working_messages.append({"role": "user", "content": _guidance})
                logger.info("[工具调用] 已注入格式指导（user角色），下轮将启用thinking模式帮助纠正")

            # 连续失败熔断检测：相同工具+相同参数连续失败 2 次则中断循环
            # 工具可能通过返回❌错误消息（非异常）报告失败，也可能是异常捕获
            # 额外检测：裸标签格式错误重复发生（_all_empty_args 时没有 tool 消息，需单独检测）
            _bare_tag_format_error = _all_empty_args and _bare_tag_detected

            # P1-1 跨轮裸标签熔断：本轮以裸标签格式错误结束则 +1，正常成功则清零；
            # 连续达到 3 轮立即中断主循环，避免“跨轮持续裸标签”空转至超时
            if _bare_tag_format_error:
                _consecutive_bare_tag_rounds += 1
            else:
                _consecutive_bare_tag_rounds = 0
            if _consecutive_bare_tag_rounds >= 3:
                logger.warning(
                    f"[熔断] 连续 {_consecutive_bare_tag_rounds} 轮裸标签格式错误，立即中断循环"
                )
                _ft = calls[0].get("name", "write_plugin_file") if calls else "write_plugin_file"
                _fj = '{"name":"' + _ft + '","arguments":{"file_path":"你的文件路径"}}'
                yield (
                    f"\n抱歉，工具调用格式连续出错，已中断。\n"
                    f"正确的调用格式是在 <tool_call> 标签内写完整JSON，例如：\n"
                    f"<tool_call>{_fj}"
                )
                return

            _tail = working_messages[-len(calls):] if calls else []
            _this_round_failed = _bare_tag_format_error or any(
                "❌" in (m.get("content", "")) or "工具执行失败" in (m.get("content", ""))
                for m in _tail
                if m.get("role") == "tool"
            )
            logger.debug(
                f"[熔断检测] round={round_idx}, failed={_this_round_failed}, "
                f"consecutive={_consecutive_failures}, last_sig={_last_failed_call}"
            )
            if _this_round_failed:
                _fail_sig = ",".join(
                    f"{c.get('name','')}:{json.dumps(c.get('arguments',{}), sort_keys=True, ensure_ascii=False)}"
                    for c in calls
                )
                if _fail_sig == _last_failed_call:
                    _consecutive_failures += 1
                else:
                    _consecutive_failures = 1
                    _last_failed_call = _fail_sig

                if _consecutive_failures >= 2:
                    logger.warning(
                        f"[熔断] 工具调用连续失败 {_consecutive_failures} 次: {_fail_sig}，中断循环"
                    )
                    if _bare_tag_format_error:
                        # 裸标签格式错误反复发生，输出格式指导作为用户可见内容
                        _ft = calls[0].get("name", "write_plugin_file") if calls else "write_plugin_file"
                        _fj = '{"name":"' + _ft + '","arguments":{"file_path":"你的文件路径"}}'
                        yield (
                            f"\n抱歉，工具调用格式连续出错，已中断。\n"
                            f"正确的调用格式是在 <tool_call> 标签内写完整JSON，例如：\n"
                            f"<tool_call>{_fj}"
                        )
                    else:
                        yield "\n[错误: 工具调用重复失败，已中断。请检查指令或换一种方式描述需求]\n"
                    return
            else:
                _consecutive_failures = 0
                _last_failed_call = None

            # 记录本轮的平文内容到累积内容（超限时作为备用输出）
            if content.strip():
                accumulated_content += content.strip() + "\n"

            logger.debug(f"[工具调用] 工具执行完毕，进入第{round_idx+2}轮")

        # 超过最大轮次。先输出已累积的平文内容（如果有），再附加统一的上限提示（含进度摘要与续接引导）
        logger.warning(f"[工具调用] 达到最大轮次 {max_rounds}，停止循环")
        if accumulated_content.strip():
            yield accumulated_content.strip()
        yield 构建轮次上限提示(max_rounds, _进度工具统计, _进度修改文件) + "\n"

    # ============================================================
    #  性能监控
    # ============================================================

    def get_acceleration_info(self) -> dict:
        """返回当前加速状态信息（基于代理层缓存的状态）

        注意：不直接与 worker 通信，避免与异步请求竞争。
        加速状态在模型加载成功后从 worker 响应中更新。
        """
        worker_alive = self._worker_process is not None and self._worker_process.poll() is None
        return {
            "quantization_available": False,  # 需 worker 报告，代理层不检测
            "flash_attention_available": False,  # 需 worker 报告，代理层不检测
            "current_quantization": self._current_quantization,
            "current_dtype": str(self._current_dtype),
            "cuda_available": worker_alive,
            "bf16_supported": self._current_dtype == "bfloat16",
        }

    def 获取性能报告(self) -> dict:
        """返回本地推理性能统计"""
        return {
            "总请求数": self._性能.总请求数,
            "成功数": self._性能.成功数,
            "失败数": self._性能.失败数,
            "平均响应时间(秒)": round(self._性能.平均响应时间, 2),
            "最近请求数": len(self._性能._响应时间列表),
            "模型已加载": self._worker_process is not None and self._worker_process.poll() is None,
            "当前模型": self.当前模型名 or "无",
        }


__all__ = [
    "LocalModelClient",
]
