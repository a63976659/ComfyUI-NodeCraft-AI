#!/usr/bin/env python
"""本地模型推理 Worker - 子进程独立运行

通过 stdin/stdout JSON Lines 与主进程通信。
本脚本由 venv 中的 python.exe 执行，拥有独立的 transformers 版本。

输入协议（每行一个 JSON）：
  {"action": "load",     "model_path": "...", "settings": {...}}
  {"action": "generate", "messages": [...],  "settings": {...}}
  {"action": "complete", "messages": [...],  "settings": {...}}
  {"action": "stream",   "messages": [...],  "settings": {...}}
  {"action": "unload"}
  {"action": "status"}
  {"action": "exit"}

输出协议（stdout，每行一个 JSON）：
  {"type": "ready"}
  {"type": "loaded",   "model_name": "...", "info": "..."}
  {"type": "token",    "content": "..."}
  {"type": "done",     "full_text": "..."}
  {"status": "success", "response": "..."}    # complete action 专用
  {"status": "error",   "response": ""}       # complete action 错误
  {"type": "error",    "message": "..."}
  {"type": "status",   "model_loaded": bool, "model_name": "..."}
  {"type": "progress", "message": "..."}
  {"type": "exiting"}
"""

# === 环境路径隔离（必须在所有其他 import 之前） ===
# venv 通过 .pth 文件复用主环境的 torch/cuda，但 .pth 可能在 site 模块初始化时
# 就把主环境整个 site-packages 加入 sys.path（比用户代码更早）。
# 必须在这里彻底重新排序，确保 venv 的包始终优先于主环境。
import sys as _sys
import os as _os

# 1. 获取 venv site-packages 路径
_venv_site = _os.path.join(
    _os.path.dirname(_os.path.dirname(_sys.executable)),
    'Lib', 'site-packages'
)

# 2. 获取主环境 site-packages 路径（从路径修正模块或 .pth 文件读取）
_主环境_site = None
_path_fix_file = _os.path.join(_venv_site, '_nodecraft_path_fix.py')
_pth_file = _os.path.join(_venv_site, '_comfyui_packages.pth')

if _os.path.isfile(_path_fix_file):
    # 从路径修正模块中提取主环境路径
    with open(_path_fix_file, 'r', encoding='utf-8') as _f:
        for _line in _f:
            if '_主环境 = r"' in _line:
                _主环境_site = _line.split('r"')[1].rstrip('"\n')
                break
elif _os.path.isfile(_pth_file):
    # 回退：从旧版 .pth 文件读取
    with open(_pth_file, 'r', encoding='utf-8') as _f:
        for _line in _f:
            _line = _line.strip()
            if _line and not _line.startswith('import') and not _line.startswith('#'):
                _主环境_site = _line
                break

# 3. 从 sys.path 中完全移除主环境 site-packages
if _主环境_site:
    _主_norm = _os.path.normcase(_os.path.normpath(_主环境_site))
    _sys.path[:] = [
        p for p in _sys.path
        if _os.path.normcase(_os.path.normpath(p)) != _主_norm
    ]

# 4. 确保 venv site-packages 在 sys.path 最前面
if _os.path.isdir(_venv_site):
    if _venv_site in _sys.path:
        _sys.path.remove(_venv_site)
    _sys.path.insert(0, _venv_site)

# 5. 把主环境放到 sys.path 最后面（仅为 torch/cuda 服务）
if _主环境_site and _os.path.isdir(_主环境_site):
    _sys.path.append(_主环境_site)

# 6. 清理临时变量
del _venv_site, _主环境_site, _path_fix_file, _pth_file
try:
    del _f, _line, _主_norm
except NameError:
    pass
# === 环境路径隔离结束 ===

import gc
import json
import logging
import re
import sys
import threading
import time
from pathlib import Path

# ============================================================
#  配置 logging 输出到 stderr（不影响 stdout 的 JSON 通信）
# ============================================================
logging.basicConfig(
    level=logging.INFO,
    format="[Worker] %(levelname)s: %(message)s",
    stream=sys.stderr,
)
logger = logging.getLogger("local_model_worker")


# ============================================================
#  可选依赖检测
# ============================================================

_HAS_BITSANDBYTES = False
_HAS_FLASH_ATTN = False
_HAS_INFERENCE_MODE = False

try:
    import bitsandbytes  # noqa: F401
    _HAS_BITSANDBYTES = True
except ImportError:
    pass

try:
    import flash_attn  # noqa: F401
    _HAS_FLASH_ATTN = True
except ImportError:
    pass

try:
    import torch as _torch_check
    _HAS_INFERENCE_MODE = hasattr(_torch_check, "inference_mode")
    del _torch_check
except ImportError:
    pass

# ============================================================
#  transformers 导入验证：确保加载的是 venv 版本而非主环境版本
# ============================================================
try:
    import transformers as _tf_verify
    _tf_path = getattr(_tf_verify, '__file__', '')
    # 如果 transformers 不是来自 venv（包含 "\u672c\u5730\u6a21\u578b\u73af\u5883" 路径），强制重新导入
    if _tf_path and '本地模型环境' not in _tf_path and 'ComfyUI-NodeCraft-AI' not in _tf_path:
        # 清除 transformers 及其所有子模块的缓存
        _keys_to_del = [k for k in sys.modules if k == 'transformers' or k.startswith('transformers.')]
        for _k in _keys_to_del:
            del sys.modules[_k]
        # 重新导入，此时 sys.path 已修正，会从 venv 加载
        import importlib
        import transformers as _tf_verify
        _tf_path = getattr(_tf_verify, '__file__', '')
    del _tf_verify, _tf_path
except ImportError:
    pass
except Exception:
    pass

# ============================================================
#  transformers 版本检测（>=4.40 用 'dtype'，旧版本用 'torch_dtype'）
# ============================================================
_DTYPE_KWARG = "torch_dtype"
try:
    import transformers as _tf_check
    _TF_VERSION = tuple(
        int(x) for x in _tf_check.__version__.split(".")[:2] if x.isdigit()
    )
    if _TF_VERSION >= (4, 40):
        _DTYPE_KWARG = "dtype"
    del _tf_check
except Exception:
    pass


# ============================================================
#  stdout JSON 输出工具函数
# ============================================================

def send_response(data: dict):
    """向 stdout 发送一行 JSON 响应（线程安全）"""
    line = json.dumps(data, ensure_ascii=False) + "\n"
    sys.stdout.write(line)
    sys.stdout.flush()


# ============================================================
#  ModelWorker 主类
# ============================================================

class ModelWorker:
    """本地模型推理 Worker

    管理模型加载、推理、流式生成、自动卸载等生命周期。
    """

    _IDLE_TTL = 600         # 空闲 10 分钟后自动卸载
    _BLACKLIST_TTL = 300    # 加载失败黑名单 5 分钟
    # 停止信号哨兵文件：客户端在工具调用中断流后创建，worker 检测后提前停止生成
    _STOP_FLAG_PATH = Path(__file__).resolve().parent.parent / "数据" / ".stop_stream"

    def __init__(self):
        self.model = None
        self.tokenizer = None
        self.model_name: str | None = None
        self._lock = threading.Lock()
        self._current_quantization = "none"
        self._current_dtype = "float32"
        self._idle_timer: threading.Timer | None = None
        self._blacklist: dict[str, float] = {}  # {model_name: fail_timestamp}

    # ----------------------------------------------------------
    #  黑名单管理
    # ----------------------------------------------------------

    def _in_blacklist(self, name: str) -> bool:
        """检查模型是否在加载失败黑名单中（超时自动解除）"""
        if name not in self._blacklist:
            return False
        if time.time() - self._blacklist[name] > self._BLACKLIST_TTL:
            del self._blacklist[name]
            return False
        return True

    def _add_to_blacklist(self, name: str):
        self._blacklist[name] = time.time()

    # ----------------------------------------------------------
    #  停止信号（哨兵文件）
    # ----------------------------------------------------------

    def _check_stop_flag(self) -> bool:
        """检查停止信号哨兵文件是否存在，存在则删除并返回 True"""
        try:
            if self._STOP_FLAG_PATH.exists():
                self._STOP_FLAG_PATH.unlink()
                return True
        except OSError:
            pass
        return False

    def _clear_stop_flag(self):
        """清除停止信号文件"""
        try:
            if self._STOP_FLAG_PATH.exists():
                self._STOP_FLAG_PATH.unlink()
        except OSError:
            pass

    # ----------------------------------------------------------
    #  空闲定时器管理
    # ----------------------------------------------------------

    def _reset_idle_timer(self):
        """推理完成后重置空闲卸载定时器"""
        self._cancel_idle_timer()
        timer = threading.Timer(self._IDLE_TTL, self._on_idle_timeout)
        timer.daemon = True
        timer.start()
        self._idle_timer = timer

    def _cancel_idle_timer(self):
        if self._idle_timer is not None:
            self._idle_timer.cancel()
            self._idle_timer = None

    def _on_idle_timeout(self):
        """空闲超时回调：自动卸载模型"""
        if self.model is not None:
            logger.info(f"模型空闲超过 {self._IDLE_TTL // 60} 分钟，自动卸载释放显存...")
            with self._lock:
                self._cleanup_memory()
            logger.info("空闲卸载完成，显存已释放")

    # ----------------------------------------------------------
    #  显存清理（内部，需在 lock 保护下调用）
    # ----------------------------------------------------------

    def _cleanup_memory(self):
        """释放模型显存（调用前请确保线程安全）"""
        self._cancel_idle_timer()
        if self.model is not None:
            del self.model
            self.model = None
        self.tokenizer = None
        self.model_name = None
        try:
            import torch
            if torch.cuda.is_available():
                torch.cuda.empty_cache()
        except Exception:
            pass
        gc.collect()

    # ----------------------------------------------------------
    #  构建模型加载参数
    # ----------------------------------------------------------

    def _build_model_kwargs(self, settings: dict) -> dict:
        """根据 settings 构建量化/dtype/Flash Attention 等加载参数"""
        import torch

        kwargs: dict = {}
        quantization = settings.get("quantization", "none")

        if quantization == "4bit" and _HAS_BITSANDBYTES:
            from transformers import BitsAndBytesConfig
            kwargs["quantization_config"] = BitsAndBytesConfig(
                load_in_4bit=True,
                bnb_4bit_compute_dtype=torch.float16,
                bnb_4bit_use_double_quant=True,
                bnb_4bit_quant_type="nf4",
            )
            self._current_quantization = "4bit"
            self._current_dtype = "float16(nf4)"
        elif quantization == "8bit" and _HAS_BITSANDBYTES:
            from transformers import BitsAndBytesConfig
            kwargs["quantization_config"] = BitsAndBytesConfig(load_in_8bit=True)
            self._current_quantization = "8bit"
            self._current_dtype = "int8"
        else:
            # 自动选择最优 dtype
            if torch.cuda.is_available():
                if torch.cuda.is_bf16_supported():
                    kwargs[_DTYPE_KWARG] = torch.bfloat16
                    self._current_dtype = "bfloat16"
                else:
                    kwargs[_DTYPE_KWARG] = torch.float16
                    self._current_dtype = "float16"
            else:
                kwargs[_DTYPE_KWARG] = torch.float32
                self._current_dtype = "float32"
            self._current_quantization = "none"

        # Flash Attention 2
        if _HAS_FLASH_ATTN and torch.cuda.is_available():
            kwargs["attn_implementation"] = "flash_attention_2"

        return kwargs

    # ----------------------------------------------------------
    #  Chat Template 辅助
    # ----------------------------------------------------------

    def _apply_chat_template(self, messages: list, enable_thinking: bool = True) -> str:
        """应用 chat template，支持动态控制 thinking 模式

        Args:
            messages: 消息列表
            enable_thinking: 是否启用 thinking 模式。工具调用轮次建议关闭，
                            避免生成大量思考 token 浪费生成时间。
        """
        try:
            text = self.tokenizer.apply_chat_template(
                messages,
                tokenize=False,
                add_generation_prompt=True,
                enable_thinking=enable_thinking,
            )
        except TypeError:
            # 不支持 enable_thinking 参数的模型
            text = self.tokenizer.apply_chat_template(
                messages, tokenize=False, add_generation_prompt=True
            )
            if self.model_name and "Qwen3" in self.model_name and enable_thinking:
                logger.warning(
                    "⚠️ Qwen3 模型不支持 enable_thinking，建议升级 transformers >= 4.51.0"
                )
        return text

    # ----------------------------------------------------------
    #  动态 token 上限计算
    # ----------------------------------------------------------

    def _calc_max_new_tokens(self, prompt_length: int, is_tool_round: bool = False) -> int:
        """按模型上下文窗口动态计算 max_new_tokens，占满模型允许的最高额度

        Args:
            prompt_length: prompt token 数
            is_tool_round: 是否为工具调用轮次（非首轮）。工具轮与 thinking 轮
                均使用模型允许的最高额度，彻底消除截断（以延迟换效果）

        公式：
            max_new_tokens = 模型上下文长度 − prompt_length − 安全余量
        上下文长度来源（按优先级取合理值，避免硬编码）：
            1. model.config.max_position_embeddings（模型真实上下文窗口）
            2. tokenizer.model_max_length（过滤 int 哨兵超大值，如 1e30）
            3. 该模型系列已知上限常量（Qwen3.5-9B 约 32768）
        """
        _SAFETY_MARGIN = 256    # 安全余量，防止边界溢出
        _FLOOR = 512            # 下限保护：算出过小/为负时兜底
        _FALLBACK_CTX = 32768   # 读不到上下文长度时的 Qwen3.5 系列已知上限

        ctx_len = None
        # 1) 优先从 model.config 读取真实上下文窗口
        try:
            _mpe = getattr(self.model.config, "max_position_embeddings", None)
            if isinstance(_mpe, int) and _mpe > 0:
                ctx_len = _mpe
        except Exception:
            pass
        # 2) 退回 tokenizer.model_max_length（过滤 int 哨兵超大值）
        if ctx_len is None:
            try:
                _tml = getattr(self.tokenizer, "model_max_length", None)
                if isinstance(_tml, int) and 0 < _tml <= 1_000_000:
                    ctx_len = _tml
            except Exception:
                pass
        # 3) 退回该模型系列已知上限常量
        if ctx_len is None:
            ctx_len = _FALLBACK_CTX

        available = ctx_len - prompt_length - _SAFETY_MARGIN
        return max(_FLOOR, available)

    # ----------------------------------------------------------
    #  thinking 标签剥离
    # ----------------------------------------------------------

    @staticmethod
    def _strip_thinking(text: str) -> str:
        """剥离思考内容，兼容 Qwen3 的 thinking 和 Qwen3.5 的 think 标签"""
        # Qwen3.5 使用 <think>...</think>，Qwen3 使用 <thinking>...</thinking>
        text = re.sub(r"<think>[\s\S]*?</think>", "", text)
        text = re.sub(r"<thinking>[\s\S]*?</thinking>", "", text)
        return text.strip()

    def handle_load(self, request: dict):
        """加载本地模型（带重试、黑名单、量化配置）"""
        import torch
        from transformers import AutoModelForCausalLM, AutoTokenizer

        model_path = request.get("model_path", "")
        settings = request.get("settings") or {}
        name = Path(model_path).name

        # 黑名单检查
        if self._in_blacklist(name):
            remaining = int(
                self._BLACKLIST_TTL - (time.time() - self._blacklist[name])
            )
            send_response({
                "type": "error",
                "message": f"模型 {name} 最近加载失败，请等待约 {remaining} 秒后重试",
            })
            return

        # 路径检查
        if not Path(model_path).exists():
            self._add_to_blacklist(name)
            send_response({"type": "error", "message": f"模型路径不存在: {model_path}"})
            return

        # 如果已加载相同模型，直接返回
        if self.model is not None and self.model_name == name:
            accel = f"量化={self._current_quantization}, dtype={self._current_dtype}"
            send_response({"type": "loaded", "model_name": name, "info": accel})
            return

        send_response({"type": "progress", "message": f"正在加载模型: {name}..."})

        model_kwargs = self._build_model_kwargs(settings)
        last_exc = None

        for attempt in range(2):
            try:
                # 清理旧模型
                with self._lock:
                    if self.model is not None:
                        self._cleanup_memory()

                send_response({"type": "progress", "message": "正在加载分词器..."})
                tokenizer = AutoTokenizer.from_pretrained(
                    model_path, trust_remote_code=True
                )
                if tokenizer.pad_token is None:
                    tokenizer.pad_token = tokenizer.eos_token

                # 获取显存上限
                max_memory = None
                if torch.cuda.is_available():
                    total_mem = (
                        torch.cuda.get_device_properties(0).total_memory / (1024 ** 3)
                    )
                    max_memory = {0: f"{int(total_mem)}GiB"}

                send_response({"type": "progress", "message": "正在加载模型权重..."})
                model = AutoModelForCausalLM.from_pretrained(
                    model_path,
                    device_map="auto",
                    trust_remote_code=True,
                    low_cpu_mem_usage=True,
                    max_memory=max_memory,
                    **model_kwargs,
                )

                with self._lock:
                    self.model = model
                    self.tokenizer = tokenizer
                    self.model_name = name

                accel = f"量化={self._current_quantization}, dtype={self._current_dtype}"
                if _HAS_FLASH_ATTN and torch.cuda.is_available():
                    accel += ", FlashAttn2=✓"

                logger.info(f"✅ 模型加载成功: {name} ({accel})")
                send_response({"type": "loaded", "model_name": name, "info": accel})
                return

            except Exception as exc:
                last_exc = exc
                if attempt < 1:
                    logger.warning(f"加载失败（第{attempt + 1}次），3秒后重试: {exc}")
                    send_response({
                        "type": "progress",
                        "message": f"加载失败，3秒后重试（第{attempt + 1}次）: {exc}",
                    })
                    time.sleep(3)
                else:
                    logger.error(f"❌ 加载失败（已重试），加入黑名单: {exc}", exc_info=True)
                    self._add_to_blacklist(name)
                    with self._lock:
                        self._cleanup_memory()

        send_response({
            "type": "error",
            "message": f"模型加载失败（已重试2次）: {last_exc}",
        })

    # ----------------------------------------------------------
    #  handle_generate（非流式）
    # ----------------------------------------------------------

    def handle_generate(self, request: dict):
        """非流式完整推理"""
        import torch

        if self.model is None or self.tokenizer is None:
            send_response({"type": "error", "message": "模型未加载，请先发送 load 指令"})
            return

        messages = request.get("messages", [])
        settings = request.get("settings") or {}
        # 从 settings 读取轮次控制参数
        enable_thinking = settings.get("enable_thinking", True)
        is_tool_round = settings.get("is_tool_round", False)

        # 取消空闲定时器
        self._cancel_idle_timer()

        # 清理消息中的非标准字段（保留 name/tool_calls/tool_call_id 支持 tool 角色）
        sanitized = []
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
            sanitized.append(msg)

        text = self._apply_chat_template(sanitized, enable_thinking=enable_thinking)

        with self._lock:
            inputs = self.tokenizer(text, return_tensors="pt").to(self.model.device)

        prompt_length = inputs["input_ids"].shape[1]
        max_new_tokens = self._calc_max_new_tokens(prompt_length, is_tool_round=is_tool_round)

        def _generate():
            ctx = (
                torch.inference_mode() if _HAS_INFERENCE_MODE else torch.no_grad()
            )
            with ctx:
                outputs = self.model.generate(
                    inputs["input_ids"],
                    attention_mask=inputs["attention_mask"],
                    max_new_tokens=max_new_tokens,
                    temperature=0.6,
                    top_p=0.95,
                    top_k=20,
                    repetition_penalty=1.0,
                    do_sample=True,
                    pad_token_id=self.tokenizer.eos_token_id,
                    use_cache=True,
                )
            new_tokens = outputs[0][prompt_length:]
            return self.tokenizer.decode(new_tokens, skip_special_tokens=True)

        try:
            result = _generate_with_timeout(_generate, timeout=180)
            result = self._strip_thinking(result)
            self._reset_idle_timer()
            send_response({"type": "done", "full_text": result})
        except TimeoutError:
            self._reset_idle_timer()
            send_response({"type": "error", "message": "推理超时（180秒），模型过大或显存不足"})
        except Exception as exc:
            self._reset_idle_timer()
            send_response({"type": "error", "message": f"推理异常: {exc}"})

    # ----------------------------------------------------------
    #  handle_complete（代码补全专用）
    # ----------------------------------------------------------

    def handle_complete(self, request: dict):
        """代码补全专用推理（低温度、短输出、stop_sequences 截断）

        与 handle_generate 的区别：
        - temperature 从 settings 读取（默认 0.1，而非硬编码 0.6）
        - max_new_tokens 从 settings 读取（默认 100，而非动态计算）
        - 生成后检查 stop_sequences 并截断到首次出现位置
        - 输出格式为 {status, response} 而非 {type, full_text}
        - 关闭 thinking 模式（补全不需要思考过程）
        """
        import torch

        if self.model is None or self.tokenizer is None:
            send_response({"status": "error", "response": ""})
            return

        messages = request.get("messages", [])
        settings = request.get("settings") or {}

        # 自定义推理参数（从 settings 读取，不使用硬编码的 0.6）
        temperature = settings.get("temperature", 0.1)
        max_new_tokens = settings.get("max_new_tokens", 100)
        stop_sequences = settings.get(
            "stop_sequences", ["\n\n\n", "class ", "def "]
        )

        # 取消空闲定时器
        self._cancel_idle_timer()

        # 应用 chat template（关闭 thinking，补全不需要思考过程）
        text = self._apply_chat_template(messages, enable_thinking=False)

        with self._lock:
            inputs = self.tokenizer(text, return_tensors="pt").to(self.model.device)

        prompt_length = inputs["input_ids"].shape[1]

        def _generate():
            ctx = (
                torch.inference_mode() if _HAS_INFERENCE_MODE else torch.no_grad()
            )
            with ctx:
                outputs = self.model.generate(
                    inputs["input_ids"],
                    attention_mask=inputs["attention_mask"],
                    max_new_tokens=max_new_tokens,
                    temperature=temperature,
                    top_p=0.95,
                    top_k=20,
                    repetition_penalty=1.0,
                    do_sample=True,
                    pad_token_id=self.tokenizer.eos_token_id,
                    use_cache=True,
                )
            new_tokens = outputs[0][prompt_length:]
            return self.tokenizer.decode(new_tokens, skip_special_tokens=True)

        try:
            result = _generate_with_timeout(_generate, timeout=15)
            result = self._strip_thinking(result)

            # 检查 stop_sequences 并截断到首次出现位置
            for seq in stop_sequences:
                idx = result.find(seq)
                if idx != -1:
                    result = result[:idx]
                    break

            self._reset_idle_timer()
            send_response({"status": "success", "response": result})
        except TimeoutError:
            self._reset_idle_timer()
            send_response({"status": "error", "response": ""})
        except Exception as exc:
            logger.warning(f"代码补全推理异常: {exc}")
            self._reset_idle_timer()
            send_response({"status": "error", "response": ""})

    # ----------------------------------------------------------
    #  handle_stream（流式）
    # ----------------------------------------------------------

    def handle_stream(self, request: dict):
        """流式推理：逐 token 输出 JSON，结束后输出 done"""
        import torch

        if self.model is None or self.tokenizer is None:
            send_response({"type": "error", "message": "模型未加载，请先发送 load 指令"})
            return

        messages = request.get("messages", [])
        settings = request.get("settings") or {}
        # 从 settings 读取轮次控制参数
        enable_thinking = settings.get("enable_thinking", True)
        is_tool_round = settings.get("is_tool_round", False)

        # 取消空闲定时器
        self._cancel_idle_timer()

        # 清除上一轮可能残留的停止信号
        self._clear_stop_flag()

        # 尝试导入 TextIteratorStreamer
        try:
            from transformers import TextIteratorStreamer
            streamer_available = True
        except ImportError:
            streamer_available = False

        sanitized = []
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
            sanitized.append(msg)

        text = self._apply_chat_template(sanitized, enable_thinking=enable_thinking)
        logger.info(f"[handle_stream] 模板渲染完成, prompt长度={len(text)}, enable_thinking={enable_thinking}, is_tool_round={is_tool_round}")

        with self._lock:
            inputs = self.tokenizer(text, return_tensors="pt").to(self.model.device)

        prompt_length = inputs["input_ids"].shape[1]
        max_new_tokens = self._calc_max_new_tokens(prompt_length, is_tool_round=is_tool_round)
        logger.info(f"[handle_stream] 开始生成, input_ids形状={inputs['input_ids'].shape}, max_new_tokens={max_new_tokens}")

        if streamer_available:
            self._do_stream(inputs, prompt_length, max_new_tokens)
        else:
            # 退化为非流式，一次性输出
            logger.warning("TextIteratorStreamer 不可用，退化为非流式模式")
            self._do_stream_fallback(inputs, prompt_length, max_new_tokens)

    def _do_stream(self, inputs, prompt_length: int, max_new_tokens: int):
        """使用 TextIteratorStreamer 进行流式推理"""
        import torch
        from transformers import TextIteratorStreamer

        streamer = TextIteratorStreamer(
            self.tokenizer, skip_prompt=True, skip_special_tokens=True
        )

        generate_kwargs = {
            "input_ids": inputs["input_ids"],
            "attention_mask": inputs["attention_mask"],
            "max_new_tokens": max_new_tokens,
            "temperature": 0.6,
            "top_p": 0.95,
            "top_k": 20,
            "repetition_penalty": 1.0,
            "do_sample": True,
            "pad_token_id": self.tokenizer.eos_token_id,
            "streamer": streamer,
            "use_cache": True,
        }

        # 子线程运行 generate
        gen_thread = threading.Thread(
            target=self._generate_thread, args=(generate_kwargs,), daemon=True
        )
        gen_thread.start()

        full_text = ""
        deadline = time.time() + 180  # 180 秒超时
        _token_count = 0
        _stop_check_interval = 10  # 每 10 个 token 检查一次停止信号

        try:
            for token_text in streamer:
                if time.time() > deadline:
                    send_response({"type": "error", "message": "流式推理超时（180秒）"})
                    self._reset_idle_timer()
                    return
                # 定期检查停止信号（客户端在工具调用后设置，避免无用生成）
                if _token_count > 0 and _token_count % _stop_check_interval == 0:
                    if self._check_stop_flag():
                        logger.info(f"[_do_stream] 收到停止信号，提前终止（已生成 {_token_count} tokens）")
                        break
                if token_text:
                    if _token_count == 0:
                        logger.info(f"[_do_stream] 收到第一个token: {repr(token_text[:80])}")
                    _token_count += 1
                    full_text += token_text
                    send_response({"type": "token", "content": token_text})
        except Exception as exc:
            logger.exception(f"流式迭代异常: {exc}")
            send_response({"type": "error", "message": f"流式推理异常: {exc}"})
            self._reset_idle_timer()
            return
        finally:
            gen_thread.join(timeout=5)

        logger.info(f"[_do_stream] 生成完成, 新token数={_token_count}")
        full_text = self._strip_thinking(full_text)
        self._reset_idle_timer()
        send_response({"type": "done", "full_text": full_text})

    def _do_stream_fallback(self, inputs, prompt_length: int, max_new_tokens: int):
        """TextIteratorStreamer 不可用时的退化：非流式一次性输出"""
        import torch

        def _generate():
            ctx = (
                torch.inference_mode() if _HAS_INFERENCE_MODE else torch.no_grad()
            )
            with ctx:
                outputs = self.model.generate(
                    inputs["input_ids"],
                    attention_mask=inputs["attention_mask"],
                    max_new_tokens=max_new_tokens,
                    temperature=0.6,
                    top_p=0.95,
                    top_k=20,
                    repetition_penalty=1.0,
                    do_sample=True,
                    pad_token_id=self.tokenizer.eos_token_id,
                    use_cache=True,
                )
            new_tokens = outputs[0][prompt_length:]
            return self.tokenizer.decode(new_tokens, skip_special_tokens=True)

        try:
            result = _generate_with_timeout(_generate, timeout=180)
            result = self._strip_thinking(result)
            self._reset_idle_timer()
            # 退化模式：只输出 done，不输出 token
            send_response({"type": "done", "full_text": result})
        except TimeoutError:
            self._reset_idle_timer()
            send_response({"type": "error", "message": "推理超时（180秒）"})
        except Exception as exc:
            self._reset_idle_timer()
            send_response({"type": "error", "message": f"推理异常: {exc}"})

    def _generate_thread(self, generate_kwargs: dict):
        """子线程运行 model.generate()（流式模式）"""
        import torch
        try:
            ctx = (
                torch.inference_mode() if _HAS_INFERENCE_MODE else torch.no_grad()
            )
            with ctx:
                self.model.generate(**generate_kwargs)
        except Exception as exc:
            logger.exception(f"流式生成线程异常: {exc}")

    # ----------------------------------------------------------
    #  handle_unload
    # ----------------------------------------------------------

    def handle_unload(self, request: dict | None = None):
        """主动卸载模型，释放显存"""
        with self._lock:
            self._cleanup_memory()
        logger.info("模型已主动卸载，显存已释放")
        send_response({"type": "done", "full_text": "模型已卸载"})

    # ----------------------------------------------------------
    #  handle_status
    # ----------------------------------------------------------

    def handle_status(self, request: dict | None = None):
        send_response({
            "type": "status",
            "model_loaded": self.model is not None,
            "model_name": self.model_name or "",
            "quantization": self._current_quantization,
            "dtype": self._current_dtype,
            "has_bitsandbytes": _HAS_BITSANDBYTES,
            "has_flash_attn": _HAS_FLASH_ATTN,
        })

    # ----------------------------------------------------------
    #  主循环
    # ----------------------------------------------------------

    def run(self):
        """从 stdin 读取 JSON Lines 请求并处理"""
        send_response({"type": "ready"})
        logger.info("Worker 已启动，等待指令...")

        for raw_line in sys.stdin:
            line = raw_line.strip()
            if not line:
                continue

            # 解析 JSON
            try:
                request = json.loads(line)
            except json.JSONDecodeError as exc:
                send_response({"type": "error", "message": f"JSON解析错误: {exc}"})
                continue

            action = request.get("action", "")
            logger.info(f"收到指令: {action}")

            try:
                if action == "load":
                    self.handle_load(request)
                elif action == "generate":
                    self.handle_generate(request)
                elif action == "complete":
                    self.handle_complete(request)
                elif action == "stream":
                    self.handle_stream(request)
                elif action == "unload":
                    self.handle_unload(request)
                elif action == "status":
                    self.handle_status(request)
                elif action == "exit":
                    # 卸载模型后退出循环
                    with self._lock:
                        self._cleanup_memory()
                    send_response({"type": "exiting"})
                    logger.info("Worker 收到 exit 指令，正常退出")
                    break
                else:
                    send_response({
                        "type": "error",
                        "message": f"未知 action: {action!r}",
                    })
            except Exception as exc:
                logger.exception(f"处理 action={action!r} 时发生未预期异常")
                send_response({"type": "error", "message": str(exc)})


# ============================================================
#  超时工具（使用线程实现跨平台 timeout）
# ============================================================

def _generate_with_timeout(fn, timeout: float):
    """在子线程中执行 fn()，若超时则抛出 TimeoutError。

    注意：子线程无法被强制终止，超时后仍会继续运行直到 generate 返回。
    这是 Python 线程的固有限制，无法避免。
    """
    result_container: list = [None]
    exc_container: list = [None]
    done_event = threading.Event()

    def _worker():
        try:
            result_container[0] = fn()
        except Exception as exc:
            exc_container[0] = exc
        finally:
            done_event.set()

    t = threading.Thread(target=_worker, daemon=True)
    t.start()
    finished = done_event.wait(timeout=timeout)

    if not finished:
        raise TimeoutError(f"推理超时（{timeout}秒）")

    if exc_container[0] is not None:
        raise exc_container[0]

    return result_container[0]


# ============================================================
#  入口
# ============================================================

if __name__ == "__main__":
    # 确保 stdout 使用 UTF-8 编码（Windows 下默认可能是 GBK）
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8")

    worker = ModelWorker()
    worker.run()
