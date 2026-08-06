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
import os as _os
import sys as _sys
import sysconfig as _sysconfig

# 1. 获取 venv site-packages 路径
# 本脚本由 venv 解释器执行，sysconfig 直接给出 venv 自身的 site-packages，
# 全平台通用（Win 为 Lib/site-packages，POSIX 为 lib/pythonX.Y/site-packages）
_venv_site = _sysconfig.get_paths()["purelib"]

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

# 系列档案表（与本文件同目录）：worker 子进程重排过 sys.path，故用绝对
# 路径加载以免受影响；该模块纯 Python，无 torch/transformers 依赖
import importlib.util as _ilu

_档案文件 = Path(__file__).resolve().parent / "本地模型系列档案.py"
_档案规格 = _ilu.spec_from_file_location("本地模型系列档案", _档案文件)
_档案模块 = _ilu.module_from_spec(_档案规格)
_档案规格.loader.exec_module(_档案模块)
匹配系列档案 = _档案模块.匹配系列档案


# ============================================================
#  可选依赖检测
# ============================================================

_HAS_BITSANDBYTES = False
_HAS_FLASH_ATTN = False
_HAS_INFERENCE_MODE = False
_HAS_PIL = False

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

try:
    from PIL import Image as _PIL_check  # noqa: F401
    _HAS_PIL = True
    del _PIL_check
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

class _ThoughtFilter:
    """Gemma 4 思考通道流式过滤（<|channel>thought ... <channel|>）

    Gemma 4 的通道标记 <|channel> 和 <channel|> 均被注册为 special token。
    为确保过滤器能匹配，streamer 对 gemma4 使用 skip_special_tokens=False，
    使标记以原始文本形式出现在流中。过滤器通过 buffer 暂缓机制识别开闭标记，
    丢弃思考通道内容，仅下发正式回复。

    容错：量化后模型可能输出残缺的开标记（如丢失 <|channel> 只剩裸文本 thought），
    此时思考过滤不会触发，但孤立的闭合标记 <channel|> 作为 special token
    不应出现在正文中，在非思考区直接剥除（开标记残缺无法可靠识别，不做猜测）。
    """

    _OPEN = "<|channel>thought"
    _CLOSE = "<channel|>"

    def __init__(self):
        self._buf = ""
        self._in_thought = False

    def feed(self, text: str) -> str:
        """喂入原始 token 文本，返回可安全下发的部分（思考内容被丢弃）"""
        self._buf += text
        out = ""
        while self._buf:
            if self._in_thought:
                idx = self._buf.find(self._CLOSE)
                if idx == -1:
                    # 未见闭合：只保留可能构成 _CLOSE 前缀的尾部，其余丢弃
                    self._buf = self._buf[-(len(self._CLOSE) - 1):]
                    break
                self._buf = self._buf[idx + len(self._CLOSE):]
                self._in_thought = False
                continue
            idx = self._buf.find(self._OPEN)
            if idx != -1:
                out += self._buf[:idx]
                self._buf = self._buf[idx + len(self._OPEN):]
                self._in_thought = True
                continue
            # 容错：非思考区内的孤立闭合标记（开标记残缺导致思考过滤未触发）直接剥除
            if self._CLOSE in self._buf:
                self._buf = self._buf.replace(self._CLOSE, "")
            # 检查尾部是否是 _OPEN/_CLOSE 的前缀（是则暂缓，等下个 token 确认）
            hold = 0
            _max_hold = max(len(self._OPEN), len(self._CLOSE)) - 1
            for k in range(min(len(self._buf), _max_hold), 0, -1):
                _tail = self._buf[-k:]
                if self._OPEN.startswith(_tail) or self._CLOSE.startswith(_tail):
                    hold = k
                    break
            if hold:
                out += self._buf[:-hold]
                self._buf = self._buf[-hold:]
            else:
                out += self._buf
                self._buf = ""
            break
        return out

    def flush(self) -> str:
        """流结束：补发被暂缓的非思考尾部（思考区内的残留直接丢弃）"""
        if self._in_thought:
            self._buf = ""
            return ""
        out, self._buf = self._buf, ""
        return out


class ModelWorker:
    """本地模型推理 Worker

    管理模型加载、推理、流式生成、自动卸载等生命周期。
    """

    _IDLE_TTL = 600         # 空闲 10 分钟后自动卸载
    _BLACKLIST_TTL = 300    # 加载失败黑名单 5 分钟
    # 推理超时秒数（与客户端 LocalModelClient._INFER_TIMEOUT 保持一致）：
    # 4bit 大模型 + 长上下文 + 思考通道内容耗时较长，180s 不够用
    _INFER_TIMEOUT = 600
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
        self.is_multimodal = False
        self.processor = None  # 多模态处理器（AutoProcessor），文本模型为 None
        self._stream_gen_error = None  # 流式生成线程异常信息（非 None 表示崩溃）
        self._series: dict | None = None  # 加载时匹配的系列档案（本地模型系列档案）

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
    #  多模态检测
    # ----------------------------------------------------------

    def _detect_multimodal(self, model_path: str) -> bool:
        """检测模型是否为多模态视觉语言模型（VLM）"""
        try:
            config_path = Path(model_path) / "config.json"
            if not config_path.exists():
                return False
            with open(config_path, 'r', encoding='utf-8') as f:
                config = json.load(f)
            # 检查 architectures 字段是否包含视觉模型类名
            architectures = config.get("architectures", [])
            for arch in architectures:
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
            return False
        except Exception:
            return False

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
        """空闲超时回调：卸载模型后退出整个进程

        仅 del model + empty_cache 仍会残留 CUDA context（约 300~500MB
        显存）与 torch 进程内存（约 2GB），进程退出才能彻底归零。
        客户端在下次请求时通过 poll() 检测到退出并自动重启 worker。
        """
        # 竞态保护：Timer 即 Thread，回调运行在该 Timer 线程中。
        # 若 _idle_timer 已被新请求取消或重建（不再指向当前线程），
        # 说明并非真正空闲，放弃卸载退出。
        if self._idle_timer is not threading.current_thread():
            return
        if self.model is not None:
            logger.info(f"模型空闲超过 {self._IDLE_TTL // 60} 分钟，卸载并退出 worker 进程...")
            with self._lock:
                self._cleanup_memory()
            logger.info("空闲卸载完成，worker 进程退出（CUDA context 与进程内存一并释放）")
        else:
            # 加载失败/从未加载的空驻留 worker 同样占用进程内存与
            # CUDA context，空闲超时后一并退出
            logger.info(f"worker 空闲超过 {self._IDLE_TTL // 60} 分钟且无已加载模型，退出进程")
        # 定时器线程中 sys.exit 只能退出当前线程，必须用 os._exit
        _os._exit(0)

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
        self.processor = None
        self.is_multimodal = False
        self.model_name = None
        self._series = None
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

    @staticmethod
    def _estimate_weight_size_gb(model_path: str) -> float:
        """估算模型权重体积（GB）：累加目录下权重文件的磁盘大小

        safetensors/bin 通常以 bf16/fp16 存储，磁盘大小 ≈ 全精度加载所需显存。
        估算失败时返回 0（调用方回退为不量化，保持原行为）。
        """
        total = 0
        try:
            for pattern in ("*.safetensors", "*.bin", "*.pt"):
                for f in Path(model_path).glob(pattern):
                    total += f.stat().st_size
        except OSError:
            return 0.0
        return total / (1024 ** 3)

    def _auto_select_quantization(self, model_path: str) -> str:
        """量化自适应：按「模型权重体积 vs 当前可用显存」自动选择量化档位

        决策规则（预留余量给激活值/KV cache）：
        - 全精度权重放得下 → none（无损）
        - 8bit（≈0.55 倍权重）放得下 → 8bit（近无损）
        - 否则 → 4bit NF4（≈0.30 倍权重，轻微损失但杜绝磁盘卸载）
        """
        import torch

        _VRAM_HEADROOM_GB = 2.0  # 为激活值/KV cache 预留的显存余量

        if not torch.cuda.is_available() or not _HAS_BITSANDBYTES:
            return "none"  # CPU 推理或缺 bitsandbytes 时量化不可用
        weight_gb = self._estimate_weight_size_gb(model_path)
        if weight_gb <= 0:
            return "none"  # 估不出体积时保持原行为

        free_bytes, _ = torch.cuda.mem_get_info(0)
        budget_gb = free_bytes / (1024 ** 3) - _VRAM_HEADROOM_GB

        # 多模态模型的大词表 embedding/视觉音频塔不被 8bit 量化，实际
        # 占用高于纯文本模型，统一用 0.65；纯文本模型取系列档案基线
        # （默认 0.55）。避免压线选档后溢出到 CPU 触发 bitsandbytes 拒绝加载
        if self._detect_multimodal(model_path):
            _8bit_factor = 0.65
        else:
            _8bit_factor = 匹配系列档案(model_path)["8bit系数"]
        if weight_gb <= budget_gb:
            choice = "none"
        elif weight_gb * _8bit_factor <= budget_gb:
            choice = "8bit"
        else:
            choice = "4bit"
        logger.info(
            f"[量化自适应] 权重≈{weight_gb:.1f}GB, 可用显存≈{budget_gb + 2.0:.1f}GB "
            f"(预留{_VRAM_HEADROOM_GB}GB余量) → 选择 {choice}"
        )
        return choice

    def _build_model_kwargs(self, settings: dict, model_path: str = "") -> dict:
        """根据 settings 构建量化/dtype/Flash Attention 等加载参数

        quantization 设置值：auto（默认，按显存自适应）/ none / 8bit / 4bit
        """
        import torch

        kwargs: dict = {}
        quantization = settings.get("quantization", "auto")
        if quantization == "auto":
            quantization = self._auto_select_quantization(model_path)

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

        # 注意力实现：默认在有 flash_attn 的 GPU 上用 flash_attention_2；
        # 但部分系列（如 Gemma 4，vision 塔 head_dim>256）与 flash 不兼容，
        # 由系列档案显式指定回退实现（sdpa），否则 forward 报
        # "FlashAttention forward only supports head dimension at most 256"（坑 109）
        if torch.cuda.is_available():
            _forced_attn = 匹配系列档案(model_path).get("注意力实现")
            if _forced_attn:
                kwargs["attn_implementation"] = _forced_attn
            elif _HAS_FLASH_ATTN:
                kwargs["attn_implementation"] = "flash_attention_2"

        return kwargs

    # ----------------------------------------------------------
    #  Chat Template 辅助
    # ----------------------------------------------------------

    # ----------------------------------------------------------
    #  图片附件提取（多模态）
    # ----------------------------------------------------------

    def _extract_images_from_messages(self, messages: list) -> list:
        """将含图/含音频消息的 content 转换为多模态 list 格式，媒体直接嵌入 content。

        processor.apply_chat_template() 会自动处理媒体 tokenization。
        格式：{"type": "image", "image": PIL_Image} / {"type": "audio", "audio": ndarray}

        模态顺序按 Gemma 4 官方最佳实践：图片在文本之前，音频在文本之后。

        Returns:
            messages: 转换后的消息列表（含媒体消息 content 变为 list 格式）
        """
        if not self.is_multimodal:
            logger.info(
                f"[_extract_images] is_multimodal=False，丢弃图片/音频附件 "
                f"(model_name={self.model_name!r})"
            )
            for m in messages:
                m.pop("_image_attachments", None)
                m.pop("_audio_attachments", None)
            return messages

        import base64
        import io

        from PIL import Image

        _AUDIO_SR = 16000  # 重采样目标采样率（主流语音模型的特征提取器标准值）
        _AUDIO_MAX_SECONDS = 30  # Gemma 4 音频硬上限 30 秒，超长截断

        new_messages = []
        total_images = 0
        total_audios = 0
        for m in messages:
            msg = dict(m)
            img_attachments = msg.pop("_image_attachments", None)
            audio_attachments = msg.pop("_audio_attachments", None)

            if img_attachments or audio_attachments:
                logger.info(
                    f"[_extract_images] 发现 {len(img_attachments or [])} 个图片、"
                    f"{len(audio_attachments or [])} 个音频附件，"
                    f"开始解码 (role={msg.get('role')!r})"
                )
                content_parts = []
                # 图片在文本之前（官方模态顺序要求）
                for att in img_attachments or []:
                    try:
                        raw_data = att.get("data", "")
                        # 前端 readAsDataURL 返回完整 data URL（含 data:image/...;base64, 前缀），
                        # 需剥离前缀再 base64 解码
                        if raw_data.startswith("data:") and ";base64," in raw_data:
                            raw_data = raw_data.split(";base64,", 1)[1]
                        img_data = base64.b64decode(raw_data)
                        pil_img = Image.open(io.BytesIO(img_data)).convert("RGB")
                        content_parts.append({"type": "image", "image": pil_img})
                        total_images += 1
                        logger.info(
                            f"[_extract_images] 图片解码成功: "
                            f"size={pil_img.size}, mode={pil_img.mode}"
                        )
                    except Exception as e:
                        logger.warning(f"[_extract_images] 图片解码失败（跳过）: {e}")
                content_parts.append({"type": "text", "text": msg.get("content", "")})
                # 音频在文本之后（官方模态顺序要求）
                for att in audio_attachments or []:
                    try:
                        import librosa
                    except ImportError:
                        logger.warning("[_extract_images] librosa 未安装，丢弃音频附件")
                        break
                    try:
                        raw_data = att.get("data", "")
                        if raw_data.startswith("data:") and ";base64," in raw_data:
                            raw_data = raw_data.split(";base64,", 1)[1]
                        audio_bytes = base64.b64decode(raw_data)
                        wav, _sr = librosa.load(
                            io.BytesIO(audio_bytes), sr=_AUDIO_SR, mono=True
                        )
                        _max_samples = _AUDIO_SR * _AUDIO_MAX_SECONDS
                        if len(wav) > _max_samples:
                            logger.warning(
                                f"[_extract_images] 音频超过 {_AUDIO_MAX_SECONDS}s 硬上限，"
                                f"已截断（原长 {len(wav) / _AUDIO_SR:.1f}s）"
                            )
                            wav = wav[:_max_samples]
                        content_parts.append({"type": "audio", "audio": wav})
                        total_audios += 1
                        logger.info(
                            f"[_extract_images] 音频解码成功: "
                            f"时长={len(wav) / _AUDIO_SR:.1f}s, sr={_AUDIO_SR}"
                        )
                    except Exception as e:
                        logger.warning(f"[_extract_images] 音频解码失败（跳过）: {e}")
                msg["content"] = content_parts

            new_messages.append(msg)

        logger.info(
            f"[_extract_images] 完成: total_images={total_images}, "
            f"total_audios={total_audios}"
        )
        return new_messages

    # ----------------------------------------------------------
    #  Chat Template 辅助
    # ----------------------------------------------------------

    def _apply_chat_template(self, messages: list, enable_thinking: bool = True) -> str:
        """应用 chat template（文本模式），返回渲染后的文本字符串。

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
            if self._is_gemma4():
                # Gemma 4 原生开关兜底：system 提示词以 <|think|> 开头即启用 thinking
                messages = self._gemma4_apply_think_token(messages, enable_thinking)
            text = self.tokenizer.apply_chat_template(
                messages, tokenize=False, add_generation_prompt=True
            )
            if self.model_name and "Qwen3" in self.model_name and enable_thinking:
                logger.warning(
                    "⚠️ Qwen3 模型不支持 enable_thinking，建议升级 transformers >= 4.51.0"
                )
        return text

    def _prepare_inputs(
        self, messages: list, enable_thinking: bool = True, is_tool_round: bool = False
    ) -> dict:
        """推理输入准备：VLM 用 processor.apply_chat_template 一步法，
        文本模型用 tokenizer 直接编码。

        一步法同时处理文本 tokenization 和图片 pixel_values 提取，
        保证图片占位符与视觉特征正确对齐。
        """
        if self.is_multimodal:
            # VLM：processor 一步完成模板渲染 + tokenization + 图片编码
            try:
                inputs = self.processor.apply_chat_template(
                    messages,
                    tokenize=True,
                    add_generation_prompt=True,
                    return_dict=True,
                    return_tensors="pt",
                    enable_thinking=enable_thinking,
                )
            except TypeError:
                if self._is_gemma4():
                    # Gemma 4 原生开关兜底：system 提示词以 <|think|> 开头即启用 thinking
                    messages = self._gemma4_apply_think_token(messages, enable_thinking)
                inputs = self.processor.apply_chat_template(
                    messages,
                    tokenize=True,
                    add_generation_prompt=True,
                    return_dict=True,
                    return_tensors="pt",
                )
            logger.info(
                f"[_prepare_inputs] VLM一步法, prompt长度={inputs['input_ids'].shape[1]}, "
                f"keys={list(inputs.keys())}, enable_thinking={enable_thinking}, "
                f"is_tool_round={is_tool_round}"
            )
        else:
            text = self._apply_chat_template(messages, enable_thinking=enable_thinking)
            inputs = self.tokenizer(text, return_tensors="pt")
            logger.info(
                f"[_prepare_inputs] 文本模式, prompt长度={inputs['input_ids'].shape[1]}, "
                f"enable_thinking={enable_thinking}, is_tool_round={is_tool_round}"
            )

        with self._lock:
            inputs = {k: v.to(self.model.device) for k, v in inputs.items()}
        return inputs

    # ----------------------------------------------------------
    #  动态 token 上限计算
    # ----------------------------------------------------------

    def _calc_max_new_tokens(self, prompt_length: int, is_tool_round: bool = False) -> int:
        """按模型上下文窗口动态计算 max_new_tokens，并施加硬上限保护

        Args:
            prompt_length: prompt token 数
            is_tool_round: 是否为工具调用轮次（非首轮）。工具轮与 thinking 轮
                均使用同一规则计算

        公式：
            max_new_tokens = min(上下文长度 − prompt_length − 安全余量, 硬上限)
        硬上限的必要性：KV cache 随生成长度线性增长，超长生成额度
        （如 Qwen3.5 的 26 万上下文）会在长回复时耗尽显存导致 OOM。
        上下文长度来源（按优先级取合理值，避免硬编码）：
            1. model.config.max_position_embeddings（模型真实上下文窗口）
            2. tokenizer.model_max_length（过滤 int 哨兵超大值，如 1e30）
            3. 该模型系列已知上限常量（Qwen3.5-9B 约 32768）
        """
        _SAFETY_MARGIN = 256    # 安全余量，防止边界溢出
        _FLOOR = 512            # 下限保护：算出过小/为负时兜底
        _CEILING = 8192         # 硬上限：限制 KV cache 增长，防显存耗尽
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
        return max(_FLOOR, min(available, _CEILING))

    # ----------------------------------------------------------
    #  thinking 标签剥离
    # ----------------------------------------------------------

    @staticmethod
    def _strip_thinking(text: str) -> str:
        """剥离思考内容，兼容 Qwen3 的 thinking 和 Qwen3.5 的 think 标签

        注意：TextIteratorStreamer 的 skip_special_tokens=True 会跳过 <think> 特殊 token，
        但 </think> 不是特殊 token 会保留。此函数也清理这种孤立闭合标签。
        """
        # Qwen3.5 使用 <think>...</think>，Qwen3 使用 <thinking>...</thinking>
        text = re.sub(r"<think>[\s\S]*?</think>", "", text)
        text = re.sub(r"<thinking>[\s\S]*?</thinking>", "", text)
        # 清理孤立闭合标签（<think> 开头已被 tokenizer 跳过，只剩 </think>）
        text = re.sub(r"</think>", "", text)
        text = re.sub(r"</thinking>", "", text)
        # Gemma 4 使用 <|channel>thought ... <channel|> 通道标记
        # （关闭 thinking 时大模型也可能输出空 thought 块，同样需要剥离）
        text = re.sub(r"<\|channel>thought[\s\S]*?<channel\|>", "", text)
        # 孤立开头（未闭合，截断/停止场景）：思考内容延伸到文本末尾，整体剥离
        text = re.sub(r"<\|channel>thought[\s\S]*$", "", text)
        # 孤立闭合标记：只删标记本身
        text = text.replace("<channel|>", "")
        # Gemma 4 skip_special_tokens=False 后生成结束标记可能残留
        text = text.replace("<eos>", "").replace("<end_of_turn>", "")
        return text.strip()

    # ----------------------------------------------------------
    #  模型系列适配（Qwen / Gemma 4）
    # ----------------------------------------------------------

    def _is_gemma4(self) -> bool:
        """模型是否属于 Gemma 4 系列（读加载时匹配的系列档案）"""
        return bool(self._series and self._series.get("系列") == "gemma4")

    def _is_llama(self) -> bool:
        """模型是否属于 Llama 系列（读加载时匹配的系列档案）"""
        return bool(self._series and self._series.get("系列") == "llama")

    @staticmethod
    def _detect_repetition_loop(text: str) -> bool:
        """检测退化重复循环：尾部窗口内同一长片段高频复现

        小参数量模型在长上下文下会锁死在模板里刷屏（如「问题 N：
        未定义的 X」逐条重复、import 行无限展开），变化部分只有变量名，
        仅靠 repetition_penalty 压不住，需在流式侧硬止损。
        """
        _WINDOW = 1200   # 仅扫尾部窗口，避免长文本全量扫描开销
        _SEG = 24        # 候选片段长度（足以覆盖一行模板的固定前缀）
        # 阈值 10 经历史会话实测标定：复读样本窗口内复现 14~29 次，
        # 而正常回复最高仅 8 次（连续枚举映射这类合法重复代码）；
        # 片段改长到 40 反而分不开两者（复读仅 2~4 次），不可调大
        _MIN_HITS = 10   # 窗口内复现次数阈值
        if len(text) < _SEG * _MIN_HITS:
            return False
        window = text[-_WINDOW:]
        # 多个偏移量采样：尾部片段可能恰好落在模板的变化部分（变量
        # 名）上，退回几步取样才能命中不变的模板骨架
        for offset in (0, 16, 32, 48, 64):
            end = len(window) - offset
            if end < _SEG:
                break
            pattern = window[end - _SEG:end]
            # 纯缩进/分隔符填充天然高频，字符多样性不足时跳过免误杀
            if len(set(pattern)) < 8:
                continue
            if window.count(pattern) >= _MIN_HITS:
                return True
        return False

    @staticmethod
    def _gemma4_apply_think_token(messages: list, enable_thinking: bool) -> list:
        """Gemma 4 原生 thinking 开关：system 提示词以 <|think|> 开头即启用。

        仅在 chat template 不接受 enable_thinking kwarg（TypeError 降级）时调用，
        避免降级后 thinking 永远开启，工具轮次浪费大量思考 token。
        """
        out = []
        has_system = False
        for m in messages:
            m = dict(m)
            if m.get("role") == "system" and not has_system:
                has_system = True
                content = str(m.get("content", ""))
                # 先移除已有开关再按需重加，保证幂等
                content = content.removeprefix("<|think|>").lstrip("\n")
                if enable_thinking:
                    content = "<|think|>\n" + content
                m["content"] = content
            out.append(m)
        if enable_thinking and not has_system:
            out.insert(0, {"role": "system", "content": "<|think|>"})
        return out

    def _sampling_params(self, enable_thinking: bool = True) -> dict:
        """按模型系列档案返回官方推荐采样参数（含 repetition_penalty）

        参数值集中在 本地模型系列档案 维护；仅思考/非思考参数不同的系列
        （如 Qwen3）才提供「采样thinking」，其余系列忽略 enable_thinking。
        repetition_penalty 一并按系列给出：小参数量模型长上下文下会锁死在
        模板里复读（1.05 压不住结构级复读，非 Gemma4 系列提到 1.15）。
        """
        series = self._series
        if series is None:
            # 理论上推理必在加载后，防御性回退默认档案
            series = 匹配系列档案("", self.model_name or "")
        if enable_thinking and series.get("采样thinking"):
            return dict(series["采样thinking"])
        return dict(series["采样"])

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
            send_response({"type": "loaded", "model_name": name, "info": accel, "is_multimodal": self.is_multimodal})
            return

        send_response({"type": "progress", "message": f"正在加载模型: {name}..."})

        last_exc = None
        forced_quant = None  # 显存不足降档重试时覆盖量化档位

        for attempt in range(2):
            try:
                # 清理旧模型（先释放显存，再做量化自适应/显存预算测量）
                with self._lock:
                    if self.model is not None:
                        self._cleanup_memory()

                # 量化自适应依赖实时可用显存，必须在清理旧模型之后构建
                _load_settings = settings
                if forced_quant:
                    _load_settings = {**settings, "quantization": forced_quant}
                model_kwargs = self._build_model_kwargs(_load_settings, model_path)

                is_vlm = self._detect_multimodal(model_path)
                if is_vlm and not _HAS_PIL:
                    logger.warning("模型为多模态视觉模型，但 Pillow 未安装，将以文本模式加载（图片功能不可用）")
                    is_vlm = False

                if is_vlm:
                    send_response({"type": "progress", "message": "正在加载多模态处理器..."})
                    from transformers import AutoProcessor
                    processor = AutoProcessor.from_pretrained(
                        model_path, trust_remote_code=True
                    )
                    tokenizer = getattr(processor, "tokenizer", processor)
                    if tokenizer.pad_token is None:
                        tokenizer.pad_token = tokenizer.eos_token
                else:
                    send_response({"type": "progress", "message": "正在加载分词器..."})
                    processor = None
                    tokenizer = AutoTokenizer.from_pretrained(
                        model_path, trust_remote_code=True
                    )
                    if tokenizer.pad_token is None:
                        tokenizer.pad_token = tokenizer.eos_token

                # 显存预算：按「当前实际可用显存 − 余量」分配，而非总显存。
                # 旧实现给满总显存会把权重塞到显存全满，激活值/KV cache 无处
                # 分配直接 OOM；且未声明 cpu 预算时溢出权重会被卸载到磁盘
                # （推理时换页极慢并二次 OOM）。现在显式给出 cpu 预算，放不下
                # 的权重卸载到内存而非磁盘。
                max_memory = None
                if torch.cuda.is_available():
                    _VRAM_HEADROOM_GB = 2.0  # 预留给激活值/KV cache
                    free_bytes, _ = torch.cuda.mem_get_info(0)
                    gpu_budget = max(
                        1.0, free_bytes / (1024 ** 3) - _VRAM_HEADROOM_GB
                    )
                    max_memory = {0: f"{gpu_budget:.1f}GiB"}
                    try:
                        import psutil
                        cpu_budget = int(
                            psutil.virtual_memory().available / (1024 ** 3) * 0.8
                        )
                        max_memory["cpu"] = f"{max(4, cpu_budget)}GiB"
                    except ImportError:
                        pass  # 缺 psutil 时交给 accelerate 自行推断 cpu 预算

                send_response({"type": "progress", "message": "正在加载模型权重..."})
                if is_vlm:
                    # 多模态模型：按 Auto 映射从新到旧链式尝试。
                    # Gemma 4 统一模型只注册在 MultimodalLM（any-to-any）映射，
                    # 不在 ImageTextToText 内，用错类会抛 ValueError（映射查找
                    # 阶段即抛，不会重复加载权重，链式尝试代价小）
                    import transformers as _tf
                    _vlm_classes = [
                        getattr(_tf, _n)
                        for _n in (
                            "AutoModelForMultimodalLM",
                            "AutoModelForImageTextToText",
                        )
                        if hasattr(_tf, _n)
                    ]
                    _vlm_classes.append(AutoModelForCausalLM)
                    model = None
                    _cls_errors = []
                    for _cls in _vlm_classes:
                        try:
                            model = _cls.from_pretrained(
                                model_path,
                                device_map="auto",
                                trust_remote_code=True,
                                low_cpu_mem_usage=True,
                                max_memory=max_memory,
                                **model_kwargs,
                            )
                            logger.info(f"[load] VLM 加载类: {_cls.__name__}")
                            break
                        except ValueError as _ve:
                            # 仅「配置类不在该 Auto 映射内」才换下一个类；
                            # 其余 ValueError（如量化模型被分派到 CPU/磁盘）
                            # 与类选择无关，直接抛出避免误报和重复加载权重
                            if "Unrecognized configuration class" not in str(_ve):
                                raise
                            _cls_errors.append(f"{_cls.__name__}: {_ve}")
                            logger.warning(
                                f"[load] {_cls.__name__} 不适用，尝试下一个加载类"
                            )
                    if model is None:
                        raise RuntimeError(
                            "VLM 加载失败，所有 Auto 类均不适用："
                            + "；".join(_cls_errors)
                        )
                else:
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
                    self.processor = processor
                    self.is_multimodal = is_vlm
                    self.model_name = name
                    self._series = 匹配系列档案(model_path, name)

                # Gemma 4 诊断：通道标记若被注册为 special token，流式
                # skip_special_tokens=True 会吞掉标记、思考内容裸漏
                # （_ThoughtFilter 看不到标记就无从过滤），实测时看此日志定位
                if self._is_gemma4():
                    try:
                        _probe = tokenizer.tokenize("<|channel>thought")
                        logger.info(
                            f"[load][gemma4] 通道标记分词探针: {_probe!r}（"
                            f"单 token ≈ special，多 token ≈ 普通文本）"
                        )
                    except Exception as _pe:
                        logger.warning(f"[load][gemma4] 分词探针失败: {_pe}")

                accel = f"量化={self._current_quantization}, dtype={self._current_dtype}"
                if is_vlm:
                    accel += ", 多模态=✓"
                if _HAS_FLASH_ATTN and torch.cuda.is_available():
                    accel += ", FlashAttn2=✓"

                logger.info(f"✅ 模型加载成功: {name} ({accel})")
                send_response({"type": "loaded", "model_name": name, "info": accel, "is_multimodal": is_vlm})
                return

            except Exception as exc:
                last_exc = exc
                if attempt < 1:
                    # bitsandbytes 量化模型被 accelerate 分派到 CPU/磁盘时
                    # 直接报错（8bit 默认禁止 CPU 卸载），降档 4bit 重试
                    retry_hint = ""
                    if ("dispatched on the CPU" in str(exc)
                            and self._current_quantization != "4bit"):
                        forced_quant = "4bit"
                        retry_hint = "显存不足降档 4bit，"
                    logger.warning(f"加载失败（第{attempt + 1}次），{retry_hint}3秒后重试: {exc}")
                    send_response({
                        "type": "progress",
                        "message": f"加载失败，{retry_hint}3秒后重试（第{attempt + 1}次）: {exc}",
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
            if "_image_attachments" in m:
                msg["_image_attachments"] = m["_image_attachments"]
            if "_audio_attachments" in m:
                msg["_audio_attachments"] = m["_audio_attachments"]
            sanitized.append(msg)

        # 图片/音频附件转换（多模态：解码嵌入 content；文本模型：丢弃）
        sanitized = self._extract_images_from_messages(sanitized)

        # 推理输入准备（VLM：processor.apply_chat_template 一步法；文本：tokenizer）
        inputs = self._prepare_inputs(sanitized, enable_thinking=enable_thinking, is_tool_round=is_tool_round)

        prompt_length = inputs["input_ids"].shape[1]
        max_new_tokens = self._calc_max_new_tokens(prompt_length, is_tool_round=is_tool_round)

        def _generate():
            # 按模型系列取官方推荐采样参数（含 repetition_penalty）
            sampling = self._sampling_params(enable_thinking)
            ctx = (
                torch.inference_mode() if _HAS_INFERENCE_MODE else torch.no_grad()
            )
            with ctx:
                outputs = self.model.generate(
                    **inputs,
                    max_new_tokens=max_new_tokens,
                    **sampling,
                    do_sample=True,
                    pad_token_id=self.tokenizer.eos_token_id,
                    use_cache=True,
                )
            new_tokens = outputs[0][prompt_length:]
            return self.tokenizer.decode(new_tokens, skip_special_tokens=True)

        try:
            result = _generate_with_timeout(_generate, timeout=self._INFER_TIMEOUT)
            result = self._strip_thinking(result)
            self._reset_idle_timer()
            send_response({"type": "done", "full_text": result})
        except TimeoutError:
            self._reset_idle_timer()
            send_response({
                "type": "error",
                "message": f"推理超时（{self._INFER_TIMEOUT}秒），模型过大或显存不足",
            })
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
        # top_p/top_k 按模型系列推荐值，temperature 保留补全专用低温
        _sampling = self._sampling_params()

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
                    top_p=_sampling["top_p"],
                    top_k=_sampling["top_k"],
                    repetition_penalty=_sampling["repetition_penalty"],
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
        if self.model is None or self.tokenizer is None:
            send_response({"type": "error", "message": "模型未加载，请先发送 load 指令"})
            return

        messages = request.get("messages", [])
        settings = request.get("settings") or {}
        # 从 settings 读取轮次控制参数
        enable_thinking = settings.get("enable_thinking", True)
        is_tool_round = settings.get("is_tool_round", False)

        # 诊断：检查消息中是否包含图片/音频附件
        _img_msg_indices = []
        _audio_msg_indices = []
        for i, m in enumerate(messages):
            if isinstance(m, dict) and m.get("_image_attachments"):
                _img_msg_indices.append(i)
            if isinstance(m, dict) and m.get("_audio_attachments"):
                _audio_msg_indices.append(i)
        logger.info(
            f"[handle_stream] 收到 {len(messages)} 条消息, "
            f"含图片附件的消息索引={_img_msg_indices}, "
            f"含音频附件的消息索引={_audio_msg_indices}, "
            f"is_multimodal={self.is_multimodal}, "
            f"model_name={self.model_name!r}"
        )

        # 取消空闲定时器
        self._cancel_idle_timer()

        # 清除上一轮可能残留的停止信号
        self._clear_stop_flag()

        # 尝试导入 TextIteratorStreamer
        try:
            from transformers import TextIteratorStreamer  # noqa: F401  可用性检测：确认 transformers 支持流式
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
            if "_image_attachments" in m:
                msg["_image_attachments"] = m["_image_attachments"]
            if "_audio_attachments" in m:
                msg["_audio_attachments"] = m["_audio_attachments"]
            sanitized.append(msg)

        # 图片/音频附件转换（多模态：解码嵌入 content；文本模型：丢弃）
        sanitized = self._extract_images_from_messages(sanitized)

        # 推理输入准备（VLM：processor.apply_chat_template 一步法；文本：tokenizer）
        inputs = self._prepare_inputs(sanitized, enable_thinking=enable_thinking, is_tool_round=is_tool_round)

        prompt_length = inputs["input_ids"].shape[1]
        max_new_tokens = self._calc_max_new_tokens(prompt_length, is_tool_round=is_tool_round)
        logger.info(
            f"[handle_stream] 开始生成, input_ids形状={inputs['input_ids'].shape}, "
            f"max_new_tokens={max_new_tokens}"
        )

        if streamer_available:
            self._do_stream(inputs, prompt_length, max_new_tokens, enable_thinking=enable_thinking)
        else:
            # 退化为非流式，一次性输出
            logger.warning("TextIteratorStreamer 不可用，退化为非流式模式")
            self._do_stream_fallback(inputs, prompt_length, max_new_tokens)

    def _do_stream(self, inputs, prompt_length: int, max_new_tokens: int, enable_thinking: bool = True):
        """使用 TextIteratorStreamer 进行流式推理

        生成参数按模型系列取官方推荐（_sampling_params）：
        - Qwen3 Thinking：temperature=0.6, top_p=0.95, top_k=20
        - Qwen3 Non-thinking：temperature=0.7, top_p=0.8, top_k=20
        - Gemma 4：temperature=1.0, top_p=0.95, top_k=64
        - Llama 3.x：temperature=0.6, top_p=0.9, top_k=40
        """
        from transformers import (
            StoppingCriteria,
            StoppingCriteriaList,
            TextIteratorStreamer,
        )

        # Gemma 4 的 <|channel>/<channel|> 是 special token，若 skip 会吞掉
        # 标记导致 _ThoughtFilter 匹配失效——改为不跳过，让标记以文本形式出现。
        # Qwen 保持 skip=True：<think> 被跳过，孤立 </think> 由 _strip_thinking 清理。
        _skip_special = not self._is_gemma4()
        streamer = TextIteratorStreamer(
            self.tokenizer, skip_prompt=True, skip_special_tokens=_skip_special
        )

        # 按模型系列取官方推荐采样参数
        sampling = self._sampling_params(enable_thinking)

        generate_kwargs = {
            **inputs,
            "max_new_tokens": max_new_tokens,
            **sampling,
            "do_sample": True,
            "pad_token_id": self.tokenizer.eos_token_id,
            "streamer": streamer,
            "use_cache": True,
        }

        # 提前终止事件：任何提前退出路径（超时/停止信号/复读熔断/异常）都会
        # 在 finally 中置位，让生成线程内的 model.generate 尽快返回，
        # 避免退出后仍空烧显存直到跑满 max_new_tokens
        _gen_stop_event = threading.Event()

        class _EventStopCriteria(StoppingCriteria):
            def __call__(self, input_ids, scores, **kwargs) -> bool:
                return _gen_stop_event.is_set()

        generate_kwargs["stopping_criteria"] = StoppingCriteriaList([_EventStopCriteria()])

        # 子线程运行 generate
        gen_thread = threading.Thread(
            target=self._generate_thread, args=(generate_kwargs,), daemon=True
        )
        gen_thread.start()

        full_text = ""
        deadline = time.time() + self._INFER_TIMEOUT  # 流式推理超时
        _token_count = 0
        _stop_check_interval = 10  # 每 10 个 token 检查一次停止信号
        _repeat_check_interval = 32  # 每 32 个 token 检查一次复读循环
        _repetition_stopped = False
        # Gemma 4 思考通道过滤（Qwen 路径不受影响：其思考内容不含通道标记，
        # <think> 标记本身是 special token 已由 skip_special_tokens 跳过）
        _thought_filter = _ThoughtFilter()

        try:
            for token_text in streamer:
                if time.time() > deadline:
                    logger.error(
                        f"[_do_stream] 流式推理超时（{self._INFER_TIMEOUT}秒），提前终止"
                    )
                    send_response({
                        "type": "error",
                        "message": f"流式推理超时（{self._INFER_TIMEOUT}秒）",
                    })
                    self._reset_idle_timer()
                    return
                # 定期检查停止信号（客户端在工具调用后设置，避免无用生成）
                if _token_count > 0 and _token_count % _stop_check_interval == 0:
                    if self._check_stop_flag():
                        logger.info(f"[_do_stream] 收到停止信号，提前终止（已生成 {_token_count} tokens）")
                        break
                if token_text:
                    # Gemma 4 skip_special_tokens=False 时，生成结束标记也会出现
                    # 在文本中（<eos>/<end_of_turn>），需手动移除避免泄漏到前端/记录
                    if not _skip_special:
                        token_text = token_text.replace("<eos>", "").replace("<end_of_turn>", "")
                        if not token_text:
                            continue
                    if _token_count == 0:
                        logger.info(f"[_do_stream] 收到第一个token: {repr(token_text[:80])}")
                    _token_count += 1
                    full_text += token_text
                    # 思考通道内容不下发（full_text 保留原始文本，done 时统一剥离）
                    _safe_text = _thought_filter.feed(token_text)
                    if _safe_text:
                        send_response({"type": "token", "content": _safe_text})
                    # 退化重复循环硬止损：不拦截就只能等跑满 max_new_tokens
                    # 或推理超时，白白消耗数分钟
                    if _token_count % _repeat_check_interval == 0:
                        if self._detect_repetition_loop(full_text):
                            logger.warning(
                                f"[_do_stream] 检测到复读循环，提前终止（已生成 "
                                f"{_token_count} tokens）"
                            )
                            _repetition_stopped = True
                            break
        except Exception as exc:
            logger.exception(f"流式迭代异常: {exc}")
            send_response({"type": "error", "message": f"流式推理异常: {exc}"})
            self._reset_idle_timer()
            return
        finally:
            # 置位停止事件：确保生成线程尽快终止（含超时/停止信号/复读路径）
            _gen_stop_event.set()
            gen_thread.join(timeout=5)

        # 检查生成线程是否崩溃（_generate_thread 设置 _stream_gen_error 后调用 streamer.end()）
        if self._stream_gen_error is not None:
            error_msg = self._stream_gen_error
            self._stream_gen_error = None  # 重置
            logger.error(f"[_do_stream] 生成线程崩溃: {error_msg}")
            send_response({"type": "error", "message": f"推理异常: {error_msg}"})
            self._reset_idle_timer()
            return

        # 过滤器可能暂缓了尾部字符（疑似标记前缀），流结束后补发
        _tail = _thought_filter.flush()
        if _tail:
            send_response({"type": "token", "content": _tail})

        logger.info(f"[_do_stream] 生成完成, 新token数={_token_count}")
        full_text = self._strip_thinking(full_text)
        if _repetition_stopped:
            full_text += (
                "\n\n[系统提示] 检测到模型陷入重复循环，已自动中断。建议缩短"
                "上下文、换用更大参数量的模型或切到 API 模型重试。"
            )
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
                    **inputs,
                    max_new_tokens=max_new_tokens,
                    # 退化路径无 enable_thinking 上下文，按 thinking 默认参数取系列推荐值
                    **self._sampling_params(True),
                    do_sample=True,
                    pad_token_id=self.tokenizer.eos_token_id,
                    use_cache=True,
                )
            new_tokens = outputs[0][prompt_length:]
            return self.tokenizer.decode(new_tokens, skip_special_tokens=True)

        try:
            result = _generate_with_timeout(_generate, timeout=self._INFER_TIMEOUT)
            result = self._strip_thinking(result)
            self._reset_idle_timer()
            # 退化模式：只输出 done，不输出 token
            send_response({"type": "done", "full_text": result})
        except TimeoutError:
            self._reset_idle_timer()
            send_response({
                "type": "error",
                "message": f"推理超时（{self._INFER_TIMEOUT}秒）",
            })
        except Exception as exc:
            self._reset_idle_timer()
            send_response({"type": "error", "message": f"推理异常: {exc}"})

    def _generate_thread(self, generate_kwargs: dict):
        """子线程运行 model.generate()（流式模式）

        异常处理：崩溃时设置 _stream_gen_error 并调用 streamer.end() 解除
        _do_stream 中 for token_text in streamer 的永久阻塞，使主线程能
        够退出并发送错误响应给客户端。
        """
        import torch
        try:
            ctx = (
                torch.inference_mode() if _HAS_INFERENCE_MODE else torch.no_grad()
            )
            with ctx:
                self.model.generate(**generate_kwargs)
        except Exception as exc:
            logger.exception(f"流式生成线程异常: {exc}")
            self._stream_gen_error = str(exc)
            # 解除 streamer 永久阻塞，让 _do_stream 的 for 循环退出
            streamer = generate_kwargs.get("streamer")
            if streamer is not None:
                try:
                    streamer.end()
                except Exception:
                    pass

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
            "is_multimodal": self.is_multimodal,
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
                # 请求开始即取消空闲定时器，避免定时器在推理进行中触发
                # 卸载模型并退出进程（推理结束后各 handler 会重新计时）
                if action in ("load", "generate", "complete", "stream"):
                    self._cancel_idle_timer()

                if action == "load":
                    self.handle_load(request)
                    # 无论加载成功与否都重新计时：覆盖加载后长期无推理
                    # 请求、以及加载失败后 worker 空驻留的兜底退出
                    self._reset_idle_timer()
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
