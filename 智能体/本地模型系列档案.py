"""本地模型系列档案表 - 多系列适配的单一数据源

把原先散落在 本地模型worker.py / 本地模型客户端.py 中的「模型系列判定 +
系列相关参数」收敛到这里，数据驱动。新增一个系列 = 加一个字典条目，
无需改动 worker 的加载/推理骨架。

识别优先级：
    1) config.json 的 model_type 前缀（transformers 权威标识，改文件夹名不影响）
    2) 模型名关键词（config 读不到时的回退）
    3) 默认档案（未登记系列，沿用 Qwen3 参数并 WARNING 一次）

本模块刻意不依赖 torch / transformers，可独立单测。
逻辑差异（thinking token 注入、流式通道过滤、复读检测）仍留在 worker，
本表只用字符串标记引用（如 "思考通道": "channel"），不搬运逻辑。
"""
import json
import logging
from pathlib import Path

logger = logging.getLogger("NodeCraftAI.本地模型系列档案")


# ============================================================
#  系列档案表
# ============================================================
# 各字段含义：
#   model_type前缀 : 与 config.json 的 model_type 做 startswith 匹配
#   名字关键词     : model_type 读不到时，对模型名（小写）做子串匹配
#   采样           : 官方推荐采样参数（含 repetition_penalty）
#   采样thinking   : 仅思考模式与非思考模式参数不同的系列才提供（如 Qwen3）
#   8bit系数       : 纯文本模型 8bit 量化占用 / 权重体积 的经验比值；
#                    多模态模型的未量化模块（大词表 embedding、视觉/音频塔）
#                    占用更高，由 worker 的 _detect_multimodal 统一覆盖为 0.65
#   思考通道       : 思考内容的承载形式，供 worker 选择剥离/过滤逻辑
#                    "channel"=Gemma4 通道标记  "think_tag"=Qwen <think>  None=无
#   音频支持       : 该系列中支持音频输入的规格关键词（空元组表示整系列不支持）
#   注意力实现     : 强制的 attn_implementation；None=有 flash_attn 时用 flash_attention_2。
#                    Gemma4 的 vision 塔 head_dim>256 与 flash 不兼容，固定 "sdpa"
_系列档案 = {
    "gemma4": {
        "model_type前缀": ("gemma4",),          # 实测 gemma-4-12B-it → gemma4_unified
        "名字关键词": ("gemma-4", "gemma4"),
        "采样": {
            "temperature": 1.0, "top_p": 0.95, "top_k": 64,
            "repetition_penalty": 1.05,
        },
        "采样thinking": None,
        "8bit系数": 0.65,       # gemma4 恒多模态，实际走 _detect_multimodal 分支
        "思考通道": "channel",  # <|channel>thought ... <channel|>
        "音频支持": ("12b", "e2b", "e4b"),   # 26B/31B 不支持
        "注意力实现": "sdpa",   # vision 塔 head_dim>256，flash_attention_2 不兼容（坑 109）
    },
    "llama": {
        "model_type前缀": ("llama",),           # 实测 Llama-3.1-* → llama
        "名字关键词": ("llama",),
        # Llama 3.x 官方推荐：temperature=0.6, top_p=0.9（见模型自带
        # generation_config.json）；官方未约束 top_k，取社区通行 40。
        # repetition_penalty 1.15：小参数量模型长上下文易结构级复读（坑 107/108）
        "采样": {
            "temperature": 0.6, "top_p": 0.9, "top_k": 40,
            "repetition_penalty": 1.15,
        },
        "采样thinking": None,
        "8bit系数": 0.55,
        "思考通道": None,
        "音频支持": (),
        "注意力实现": None,
    },
    "qwen3": {
        # "qwen3" 前缀同时命中 qwen3 与 qwen3_5（Qwen3.5-9B）
        "model_type前缀": ("qwen3",),
        "名字关键词": ("qwen3",),
        # Qwen3 官方推荐：non-thinking 0.7/0.8，thinking 0.6/0.95，top_k=20
        "采样": {
            "temperature": 0.7, "top_p": 0.8, "top_k": 20,
            "repetition_penalty": 1.15,
        },
        "采样thinking": {
            "temperature": 0.6, "top_p": 0.95, "top_k": 20,
            "repetition_penalty": 1.15,
        },
        "8bit系数": 0.55,       # Qwen3.5 多模态由 _detect_multimodal 覆盖为 0.65
        "思考通道": "think_tag",
        "音频支持": (),
        "注意力实现": None,
    },
    "qwen2": {
        # "qwen2" 前缀不会误匹配 qwen3，且覆盖 qwen2_5_vl 等变体
        "model_type前缀": ("qwen2",),
        "名字关键词": ("qwen2",),
        # Qwen2.5-Instruct 无 thinking 模式，官方推荐 0.7/0.8/top_k=20；
        # 故不提供 采样thinking（此前被误套 Qwen3 thinking 参数，本次修正）
        "采样": {
            "temperature": 0.7, "top_p": 0.8, "top_k": 20,
            "repetition_penalty": 1.15,
        },
        "采样thinking": None,
        "8bit系数": 0.55,
        "思考通道": "think_tag",
        "音频支持": (),
        "注意力实现": None,
    },
}

# 未登记系列的默认档案：沿用 Qwen3（含 thinking 分支），行为与重构前
# 「非 gemma4 非 llama 一律按 Qwen3」一致
_默认档案 = {
    "采样": {
        "temperature": 0.7, "top_p": 0.8, "top_k": 20,
        "repetition_penalty": 1.15,
    },
    "采样thinking": {
        "temperature": 0.6, "top_p": 0.95, "top_k": 20,
        "repetition_penalty": 1.15,
    },
    "8bit系数": 0.55,
    "思考通道": "think_tag",
    "音频支持": (),
    "注意力实现": None,
}

# 未登记系列只警告一次，避免工具循环每轮刷屏（参照 模型能力注册表 的处理）
_已警告 = set()


def _读取model_type(model_path: str) -> str:
    """读取模型目录 config.json 的 model_type（小写）；读不到返回空串"""
    if not model_path:
        return ""
    try:
        config_path = Path(model_path) / "config.json"
        if not config_path.exists():
            return ""
        with open(config_path, "r", encoding="utf-8") as f:
            return str(json.load(f).get("model_type", "")).lower()
    except Exception:
        return ""


def 匹配系列档案(model_path: str, model_name: str = "") -> dict:
    """匹配模型所属系列档案

    Args:
        model_path: 模型目录（用于读 config.json 的 model_type）
        model_name: 模型名/文件夹名（config 读不到时的回退依据）

    Returns:
        档案 dict（含 "系列" 键）；未命中任何系列时返回默认档案并 WARNING 一次
    """
    # 1) model_type 前缀匹配（权威）
    model_type = _读取model_type(model_path)
    if model_type:
        for 系列, 档案 in _系列档案.items():
            if any(model_type.startswith(p) for p in 档案["model_type前缀"]):
                return {"系列": 系列, **档案}

    # 2) 模型名关键词回退
    name = (model_name or "").lower()
    if name:
        for 系列, 档案 in _系列档案.items():
            if any(kw in name for kw in 档案["名字关键词"]):
                return {"系列": 系列, **档案}

    # 3) 默认档案 + WARNING（避免静默套用错误参数）
    _key = model_type or name or (model_path or "")
    if _key and _key not in _已警告:
        _已警告.add(_key)
        logger.warning(
            f"模型未匹配到系列档案（model_type={model_type!r}, name={name!r}），"
            f"回退默认档案（Qwen3 参数）；如需精准适配请在 本地模型系列档案 中登记"
        )
    return {"系列": "default", **_默认档案}
