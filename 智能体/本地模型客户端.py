"""本地 transformers 模型客户端（代理层）

通过 subprocess 与 本地模型worker.py 通信，不直接 import transformers/torch。
负责 worker 生命周期管理、命令发送/响应读取、工具调用循环。
JSON 修复函数从 智能体.JSON修复工具 导入。
共享数据类（性能统计）从 智能体.模型客户端 导入。
"""
import asyncio
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
from 智能体.模型客户端 import 性能统计
from 智能体.本地模型工具解析器 import (
    extract_tool_calls, separate_content_and_calls,
    strip_tool_calls, has_tool_calls, format_tool_result
)

logger = 获取日志器("本地模型客户端")


# ============================================================
#  工具调用指令模板（保留在代理层，不依赖 transformers）
# ============================================================

_TOOL_INSTRUCTION_TEMPLATE = """
## 可用工具

你拥有文件操作工具。当需要读取、创建或修改文件时，你**必须**使用工具而非输出代码文本。

### 工具列表

1. `list_plugin_files` - 列出当前插件的文件结构
   调用示例：<tool_call>{"name": "list_plugin_files", "arguments": {}}</tool_call>

2. `read_plugin_file` - 读取指定文件内容
   调用示例：<tool_call>{"name": "read_plugin_file", "arguments": {"file_path": "__init__.py"}}</tool_call>

3. `write_plugin_file` - 写入完整文件内容（创建或覆盖）
   调用示例：<tool_call>{"name": "write_plugin_file", "arguments": {"file_path": "__init__.py", "content": "# 文件完整内容\\nimport os\\n"}}</tool_call>

### 严格格式要求

- 工具调用格式固定为：`<tool_call>JSON对象</tool_call>`
- JSON 对象必须包含 `name` 和 `arguments` 两个字段
- 每条消息最多包含一个工具调用
- 工具调用后停止输出，等待 [工具结果] 反馈再继续
- **禁止**用代码块展示文件内容代替实际写入操作

### 工作流程

当用户要求你创建或修改文件时：
1. 先用 list_plugin_files 了解当前结构
2. 如需参考已有文件，用 read_plugin_file 读取
3. 用 write_plugin_file 写入完整文件内容
4. 完成后告诉用户已创建/修改了哪些文件

示例对话：
用户：帮我创建一个 hello.py
助手：我来为你创建 hello.py 文件。
<tool_call>{"name": "write_plugin_file", "arguments": {"file_path": "hello.py", "content": "print('Hello World!')\\n"}}</tool_call>
"""


class LocalModelClient:
    """本地模型客户端（代理层）

    通过 subprocess 与 本地模型worker.py 通信，不直接 import transformers/torch。

    特性：
    - 异步锁保护并发通信（同一时间只有一个请求与 worker 交互）
    - 失败黑名单机制（5分钟自动解除）
    - Worker 崩溃自动重启
    - 180 秒推理超时保护（load: 300s）
    - P0: thinking 标签剥离（代理层处理）
    - 完整的性能监控统计
    - stderr 转发到主进程日志
    """

    def __init__(self):
        self._worker_process: Optional[subprocess.Popen] = None
        self._worker_lock: Optional[asyncio.Lock] = None  # 保护 worker 通信
        self.当前模型名: Optional[str] = None
        self._性能 = 性能统计()
        self.加载失败历史 = {}  # {模型名: 失败时间戳}
        self.失败黑名单过期秒 = 300
        self._current_quantization = "none"
        self._current_dtype = "float32"
        # P3: 后台预热任务（消除首次推理 30s+ 加载延迟）
        self._预热任务: Optional[asyncio.Task] = None
        self._预热延迟 = 30
        self._预热已调度 = False
        # stderr 转发线程
        self._stderr_thread: Optional[threading.Thread] = None

    # ============================================================
    #  异步锁（延迟创建）
    # ============================================================

    def _获取异步锁(self) -> asyncio.Lock:
        """延迟创建并返回 asyncio.Lock（必须在运行的事件循环中创建）"""
        if self._worker_lock is None:
            self._worker_lock = asyncio.Lock()
        return self._worker_lock

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

        from 智能体.本地模型环境 import 获取venv_python, 确保环境就绪

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
        except Exception as e:
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
            except Exception:
                pass

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
        line = await loop.run_in_executor(None, self._worker_process.stdout.readline)

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
        while True:
            if self._worker_process is None or self._worker_process.stdout is None:
                raise RuntimeError("Worker 进程未运行")

            line = await loop.run_in_executor(None, self._worker_process.stdout.readline)
            if not line:
                # EOF
                break

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
                    yield content
            elif msg_type == "done":
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
            except Exception as e:
                logger.debug(f"终止 Worker 时异常（忽略）: {e}")
            finally:
                self._worker_process = None

    def __del__(self):
        """析构时终止 worker 进程"""
        self._终止worker()

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
        """内部卸载实现（需在锁保护下调用）"""
        if self._worker_process is not None and self._worker_process.poll() is None:
            try:
                self._send_to_worker({"action": "unload"})
                # 等待卸载完成
                resp = await asyncio.wait_for(
                    self._read_worker_response(), timeout=30
                )
                if resp.get("type") == "error":
                    logger.warning(f"Worker 卸载返回错误: {resp.get('message', '')}")
            except Exception as e:
                logger.warning(f"Worker 卸载失败（忽略）: {e}")

        self.当前模型名 = None
        self._current_quantization = "none"
        self._current_dtype = "float32"

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
    #  P3: 后台预热（消除首次推理 30s+ 模型加载延迟）
    # ============================================================

    async def 启动预热(self):
        """应用启动后延迟预热最近使用的本地模型。

        关键约束：
        1. 延迟 30 秒后才开始，不阻塞应用启动；
        2. 仅当 model_source=="local" 且 local_path 有效时执行；
        3. 异常被吞掉仅记录日志，不影响正常功能；
        4. 共享 异步加载模型 内的 asyncio.Lock，与用户请求不会重复加载；
        5. 仅加载到内存，不执行任何推理。
        """
        try:
            await asyncio.sleep(self._预热延迟)
        except asyncio.CancelledError:
            return

        最近模型 = self._获取最近使用模型()
        if not 最近模型:
            logger.info("[预热] 未配置本地模型或当前为 API 模式，跳过预热")
            return

        # 已加载相同模型则无需预热
        if self.当前模型名 is not None and self.当前模型名 == Path(最近模型).name:
            if self._worker_process is not None and self._worker_process.poll() is None:
                logger.info("[预热] 模型已加载，跳过预热")
                return

        try:
            logger.info(f"[预热] 开始后台加载模型: {最近模型}")
            await self.异步加载模型(最近模型)
            logger.info("[预热] 模型加载完成，首次推理将无 30s+ 延迟")
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.warning(f"[预热] 模型预热失败（不影响正常使用）: {e}")

    def _获取最近使用模型(self) -> Optional[str]:
        """从设置文件中读取最近使用的本地模型路径。

        仅在 ``model_source == "local"`` 且 ``local_path`` 非空且实际存在
        时返回路径，否则返回 None（API 模式或未配置时跳过预热）。
        """
        try:
            settings = load_settings()
        except Exception as e:
            logger.debug(f"[预热] 读取设置失败: {e}")
            return None

        if settings.get("model_source") != "local":
            return None

        local_path = (settings.get("local_path") or "").strip()
        if not local_path:
            return None

        try:
            if not Path(local_path).exists():
                logger.debug(f"[预热] 配置的本地模型路径不存在: {local_path}")
                return None
        except Exception:
            return None

        return local_path

    def 调度预热(self):
        """在运行中的事件循环里调度预热任务（非阻塞，幂等）。

        - 仅在事件循环运行时调度，否则静默跳过；
        - 重复调用不会创建多个预热任务；
        - 任何异常均被吞掉，确保启动流程不受影响。
        """
        if self._预热已调度:
            return
        try:
            loop = asyncio.get_event_loop()
        except RuntimeError:
            return
        try:
            if loop.is_running():
                self._预热任务 = asyncio.ensure_future(self.启动预热())
                self._预热已调度 = True
                logger.info(
                    f"[预热] 已调度后台预热任务（{self._预热延迟} 秒后启动）"
                )
        except RuntimeError:
            pass
        except Exception as e:
            logger.debug(f"[预热] 调度失败（忽略）: {e}")

    # ============================================================
    #  视觉能力检测
    # ============================================================

    def _supports_vision(self) -> bool:
        """本地推理客户端多模态占位

        本地 transformers 多模态推理（如 Qwen2-VL、LLaVA）需额外的
        AutoProcessor + image_processor + Chat Template。当前未接入，
        统一返回 False，让有附件的请求退回为文本描述。
        后续如需启用，可根据 self.当前模型名 判断并通过 worker 调用 processor。
        """
        return False

    # ============================================================
    #  非流式推理
    # ============================================================

    async def generate_response(self, system_prompt, messages_history):
        """本地推理生成回复（通过 worker，180秒超时保护 + thinking剥离）

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
            formatted_messages.append({"role": msg["role"], "content": msg["content"]})

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
                    timeout=180,
                )

                if resp.get("type") == "error":
                    self._性能.记录请求(time.time() - 开始时间, False)
                    return f"[推理错误]: {resp.get('message', '未知错误')}"

                if resp.get("type") == "done":
                    result = resp.get("full_text", "")
                    # 代理层再做一次 thinking 剥离（双保险）
                    result = re.sub(r'<thinking>[\s\S]*?</thinking>', '', result).strip()
                    self._性能.记录请求(time.time() - 开始时间, True)
                    return result

                # 未知响应类型
                self._性能.记录请求(time.time() - 开始时间, False)
                return f"[推理错误]: Worker 返回未知响应类型: {resp.get('type', '?')}"

            except asyncio.TimeoutError:
                self._性能.记录请求(time.time() - 开始时间, False)
                return "[推理超时]: 本地推理耗时超过180秒，可能是模型过大或显存不足"
            except RuntimeError as e:
                self._性能.记录请求(time.time() - 开始时间, False)
                return f"[推理错误]: {str(e)}"
            except Exception as e:
                self._性能.记录请求(time.time() - 开始时间, False)
                return f"[推理错误]: {str(e)}"

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
        max_rounds = 15

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
            logger.debug(f"[工具调用] 工具指令已注入，messages[0] role={working_messages[0]['role']}, 长度={len(working_messages[0]['content'])}")

        enhanced_prompt = working_messages[0]["content"]
        # 复制消息历史避免修改原始数据
        history_messages = working_messages[1:]

        reply = ""
        for round_idx in range(max_rounds):
            # 调用改造后的生成方法
            reply = await self.generate_response(enhanced_prompt, history_messages)

            logger.debug(f"[工具调用] 第{round_idx+1}轮完成，回复长度={len(reply)}，包含tool_call={has_tool_calls(reply)}")

            # 检查是否包含工具调用
            if not has_tool_calls(reply):
                # 无工具调用，返回清理后的文本
                return strip_tool_calls(reply)

            # 解析工具调用
            content, calls = separate_content_and_calls(reply)

            if not calls:
                return strip_tool_calls(reply)

            # 将模型回复加入历史
            history_messages.append({"role": "assistant", "content": reply})

            # 执行每个工具调用
            for call in calls:
                tool_name = call.get("name", "")
                tool_args = call.get("arguments", {})

                try:
                    if tool_executor:
                        result = await tool_executor(tool_name, tool_args)
                    else:
                        result = f"工具 {tool_name} 不可用"
                    tool_result_text = format_tool_result(tool_name, result, success=True)
                except Exception as e:
                    tool_result_text = format_tool_result(tool_name, str(e), success=False)

                # 工具结果作为用户消息反馈
                history_messages.append({"role": "user", "content": tool_result_text})

        # 超过最大轮次，返回最后的回复
        logger.warning(f"工具调用超过最大轮次 {max_rounds}")
        return strip_tool_calls(reply)

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

        # 清理消息中的非标准字段（本地推理不支持多模态）
        sanitized_messages = [
            {"role": m.get("role"), "content": m.get("content", "")}
            for m in messages if isinstance(m, dict)
        ]

        async with self._获取异步锁():
            try:
                self._send_to_worker({
                    "action": "stream",
                    "messages": sanitized_messages,
                    "settings": settings or {},
                })

                # 逐行读取流式 token
                async for token in self._read_worker_stream():
                    yield token

            except RuntimeError as e:
                yield f"\n\n[推理错误]: {str(e)}"
            except asyncio.TimeoutError:
                yield "\n\n[错误: 本地推理超时(180秒)]"
            except Exception as e:
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
        max_rounds = 15

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
            logger.debug(f"[工具调用] 工具指令已注入，messages[0] role={working_messages[0]['role']}, 长度={len(working_messages[0]['content'])}")

        _OPEN_TAG = '<tool_call>'
        _CLOSE_TAG = '</tool_call>'

        for round_idx in range(max_rounds):
            full_reply = ""
            buffer = ""
            in_tool_call = False  # 是否已确认进入 tool_call 区域

            logger.debug(f"[工具调用] 第{round_idx+1}轮流式生成开始")

            async for chunk in self.流式对话(working_messages, settings):
                full_reply += chunk
                buffer += chunk

                if in_tool_call:
                    # 已在 tool_call 区域内，累积直到看到 close tag
                    if _CLOSE_TAG in buffer:
                        break  # 工具调用完整，中断流
                    continue

                # 检查 buffer 是否可能是 <tool_call> 的开头
                # 关键改进：只有 buffer 末尾严格匹配 _OPEN_TAG 前缀时才暂缓
                pending_start = buffer.rfind('<')

                if pending_start == -1:
                    # buffer 中没有 '<'，全部安全输出
                    yield buffer
                    buffer = ""
                else:
                    # 有 '<'，检查从 '<' 开始的部分是否是 _OPEN_TAG 的合法前缀
                    tail = buffer[pending_start:]

                    if _OPEN_TAG.startswith(tail):
                        # tail 是 '<tool_call>' 的有效前缀（如 '<', '<t', '<to', ...）
                        # 输出 '<' 之前的部分，保留 tail 继续观察
                        safe_part = buffer[:pending_start]
                        if safe_part:
                            yield safe_part
                        buffer = tail

                        # 如果 tail 已经完全匹配 _OPEN_TAG
                        if tail == _OPEN_TAG:
                            in_tool_call = True
                            # 不 yield，继续累积 tool_call 内容

                    elif _OPEN_TAG in buffer:
                        # buffer 包含完整的 <tool_call>
                        in_tool_call = True
                        if _CLOSE_TAG in buffer:
                            break  # 完整的 tool_call 对
                    else:
                        # tail 不是有效前缀（如 '<div', '<span' 等）
                        # 安全输出全部 buffer
                        yield buffer
                        buffer = ""
            else:
                # for-else: 流正常结束（没有 break）
                # 输出剩余 buffer（如果不包含未完成的 tool_call）
                if buffer:
                    if not in_tool_call:
                        yield buffer
                    # 如果 in_tool_call 为 True 但没有 close tag，说明是不完整的调用
                    # 这种情况也输出（当作普通文本）
                    elif _CLOSE_TAG not in buffer:
                        yield buffer

                logger.debug(f"[工具调用] 第{round_idx+1}轮完成，回复长度={len(full_reply)}，包含tool_call=False")
                return  # 没有工具调用，正常结束

            # break 到这里：检测到完整的 tool_call
            logger.debug(f"[工具调用] 第{round_idx+1}轮完成，回复长度={len(full_reply)}，包含tool_call=True")

            # 解析工具调用
            if not has_tool_calls(full_reply):
                # 误判，输出全部内容
                yield buffer
                return

            content, calls = separate_content_and_calls(full_reply)

            if not calls:
                yield buffer
                return

            # 将 assistant 回复加入 working_messages
            working_messages.append({"role": "assistant", "content": full_reply})

            # 执行工具
            for call in calls:
                tool_name = call.get("name", "")
                tool_args = call.get("arguments", {})

                yield f"\n[正在执行: {tool_name}...]\n"

                try:
                    if tool_executor:
                        result = await tool_executor(tool_name, tool_args)
                    else:
                        result = f"工具 {tool_name} 不可用"
                    tool_result_text = format_tool_result(tool_name, result, success=True)
                except Exception as e:
                    tool_result_text = format_tool_result(tool_name, str(e), success=False)

                working_messages.append({"role": "user", "content": tool_result_text})

            logger.debug(f"[工具调用] 工具执行完毕，进入第{round_idx+2}轮")

        # 超过最大轮次
        logger.warning(f"[工具调用] 达到最大轮次 {max_rounds}，停止循环")

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
