"""模型加载节点实现

演示 ComfyUI 中模型加载的最佳实践：
- 使用 folder_paths 获取模型目录
- 使用 COMBO widget 列出可用模型
- 路径安全校验
- 设备管理（CPU/GPU 自动选择）
- 简易模型缓存避免重复加载
"""

import os
import logging
from typing import Tuple, Dict, Any

import torch
import folder_paths

logger = logging.getLogger("ModelLoaderNode")

# ─── 模型缓存 ──────────────────────────────────────────────
# 避免重复加载同一模型，键为模型文件路径
_MODEL_CACHE: Dict[str, Any] = {}


def _get_available_models(model_type: str) -> list:
    """获取指定类型的可用模型文件列表

    参数:
        model_type: 模型类型，如 "checkpoints"、"vae"、"loras"

    返回:
        模型文件名列表（相对路径）
    """
    try:
        return folder_paths.get_filename_list(model_type)
    except Exception:
        return []


def _resolve_model_path(model_type: str, model_name: str) -> str:
    """解析模型文件的完整路径

    参数:
        model_type: 模型类型
        model_name: 模型文件名

    返回:
        模型文件的完整路径

    异常:
        FileNotFoundError: 模型文件不存在
    """
    try:
        full_path = folder_paths.get_full_path(model_type, model_name)
        if full_path and os.path.isfile(full_path):
            return full_path
    except Exception:
        pass
    raise FileNotFoundError(f"模型文件不存在: {model_type}/{model_name}")


def _get_device() -> torch.device:
    """获取当前可用的计算设备"""
    if torch.cuda.is_available():
        return torch.device("cuda")
    return torch.device("cpu")


class ModelLoaderNode:
    """模型加载节点模板

    支持 checkpoint、VAE、LoRA 三种模型类型的加载。
    使用 COMBO widget 让用户从下拉列表中选择模型文件。
    """

    @classmethod
    def INPUT_TYPES(cls):
        """定义输入端口和参数控件"""
        # 动态获取可用模型列表作为 COMBO 选项
        checkpoints = _get_available_models("checkpoints") or ["(无可用模型)"]
        vaes = _get_available_models("vae") or ["(无可用模型)"]
        loras = _get_available_models("loras") or ["(无可用模型)"]

        return {
            "required": {
                "checkpoint_name": (checkpoints,),
                "load_vae": ("BOOLEAN", {"default": False}),
                "vae_name": (vaes,),
                "load_lora": ("BOOLEAN", {"default": False}),
                "lora_name": (loras,),
                "lora_strength": (
                    "FLOAT",
                    {"default": 1.0, "min": -10.0, "max": 10.0, "step": 0.1},
                ),
            },
        }

    RETURN_TYPES = ("MODEL", "CLIP", "VAE", "STRING")
    RETURN_NAMES = ("model", "clip", "vae", "model_info")
    FUNCTION = "load_model"
    CATEGORY = "Custom/Model"

    def load_model(
        self,
        checkpoint_name: str,
        load_vae: bool,
        vae_name: str,
        load_lora: bool,
        lora_name: str,
        lora_strength: float,
    ) -> Tuple[Any, Any, Any, str]:
        """加载模型

        参数:
            checkpoint_name: checkpoint 文件名
            load_vae: 是否加载独立 VAE
            vae_name: VAE 文件名
            load_lora: 是否加载 LoRA
            lora_name: LoRA 文件名
            lora_strength: LoRA 强度系数

        返回:
            (model, clip, vae, model_info_text)
        """
        device = _get_device()
        info_parts = []

        # ── 1. 加载 checkpoint ──────────────────────────────
        ckpt_path = _resolve_model_path("checkpoints", checkpoint_name)
        cache_key = f"ckpt:{ckpt_path}"

        if cache_key in _MODEL_CACHE:
            model_data = _MODEL_CACHE[cache_key]
            logger.info("从缓存加载 checkpoint: %s", checkpoint_name)
        else:
            # 使用 ComfyUI 内置的 checkpoint 加载器
            from comfy.sd import load_checkpoint_guess_config

            model_data = load_checkpoint_guess_config(
                ckpt_path,
                output_vae=not load_vae,  # 如果要加载独立 VAE，则不加载 checkpoint 内置 VAE
                output_clip=True,
                embedding_directory=folder_paths.get_folder_paths("embeddings"),
            )
            _MODEL_CACHE[cache_key] = model_data
            logger.info("成功加载 checkpoint: %s (设备: %s)", checkpoint_name, device)

        model, clip, vae, clipvision = model_data[:4]
        info_parts.append(f"Checkpoint: {checkpoint_name}")

        # ── 2. 可选：加载独立 VAE ────────────────────────────
        if load_vae and vae_name and vae_name != "(无可用模型)":
            vae_path = _resolve_model_path("vae", vae_name)
            from comfy.sd import VAE

            vae_cache_key = f"vae:{vae_path}"
            if vae_cache_key in _MODEL_CACHE:
                vae = _MODEL_CACHE[vae_cache_key]
            else:
                vae = VAE(vae_path)
                _MODEL_CACHE[vae_cache_key] = vae
            info_parts.append(f"VAE: {vae_name}")
            logger.info("成功加载 VAE: %s", vae_name)

        # ── 3. 可选：加载 LoRA ───────────────────────────────
        if load_lora and lora_name and lora_name != "(无可用模型)":
            lora_path = _resolve_model_path("loras", lora_name)
            from comfy.sd import load_lora_for_models

            model, clip = load_lora_for_models(
                model, clip, lora_path, lora_strength, lora_strength
            )
            info_parts.append(f"LoRA: {lora_name} (强度: {lora_strength})")
            logger.info("成功加载 LoRA: %s (强度: %.2f)", lora_name, lora_strength)

        model_info = " | ".join(info_parts)
        return (model, clip, vae, model_info)

    @staticmethod
    def IS_CHANGED(
        checkpoint_name: str,
        load_vae: bool,
        vae_name: str,
        load_lora: bool,
        lora_name: str,
        lora_strength: float,
        **kwargs,
    ) -> str:
        """模型选择变化时需要重新加载"""
        return f"{checkpoint_name}|{load_vae}|{vae_name}|{load_lora}|{lora_name}|{lora_strength}"
