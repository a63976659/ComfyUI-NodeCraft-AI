"""基础图像处理节点实现

演示 ComfyUI V3 节点规范：
- INPUT_TYPES 定义 required / optional 输入
- RETURN_TYPES / RETURN_NAMES 定义输出
- FUNCTION 指定执行方法名
- CATEGORY 指定节点分类
- IS_CHANGED 实现缓存控制
"""

import torch
from typing import Tuple, Optional


class ImageProcessNode:
    """基础图像处理节点模板

    接收 IMAGE 输入，通过 intensity 参数控制处理强度，
    可选 MASK 遮罩输入实现局部处理。
    """

    @classmethod
    def INPUT_TYPES(cls):
        """定义节点的输入端口和参数控件"""
        return {
            "required": {
                "image": ("IMAGE",),
                "intensity": (
                    "FLOAT",
                    {
                        "default": 1.0,
                        "min": 0.0,
                        "max": 10.0,
                        "step": 0.1,
                        "display": "slider",  # 在 UI 中显示为滑块
                    },
                ),
            },
            "optional": {
                "mask": ("MASK",),
            },
        }

    RETURN_TYPES = ("IMAGE",)
    RETURN_NAMES = ("processed_image",)
    FUNCTION = "process"
    CATEGORY = "Custom/Image"

    def process(
        self,
        image: torch.Tensor,
        intensity: float = 1.0,
        mask: Optional[torch.Tensor] = None,
    ) -> Tuple[torch.Tensor]:
        """处理图像

        参数:
            image: 输入图像张量，形状 [B, H, W, C]，值域 [0, 1]
            intensity: 处理强度，1.0 为原始图像
            mask: 可选遮罩，形状 [B, H, W] 或 [H, W]，值域 [0, 1]

        返回:
            (processed_image,) 处理后的图像张量
        """
        # TODO: 在此实现具体的图像处理逻辑
        # 示例：简单的亮度调整（乘以强度系数）
        processed = image * intensity

        # 如果提供了遮罩，仅在遮罩区域应用处理效果
        if mask is not None:
            # 将遮罩扩展到与图像相同的通道维度
            if mask.dim() == 2:
                mask = mask.unsqueeze(0)  # [H, W] -> [1, H, W]
            if mask.dim() == 3:
                mask = mask.unsqueeze(-1)  # [B, H, W] -> [B, H, W, 1]
            # 混合原始图像和处理后图像
            processed = image * (1 - mask) + processed * mask

        # 确保值域在 [0, 1] 之间
        processed = processed.clamp(0, 1)

        return (processed,)

    @staticmethod
    def IS_CHANGED(intensity: float, **kwargs) -> float:
        """告诉 ComfyUI 何时需要重新执行此节点

        返回浮点数，当值变化时触发重算。
        intensity 变化时需要重新处理。
        """
        return intensity
