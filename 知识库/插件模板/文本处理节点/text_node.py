"""文本处理节点实现

演示 ComfyUI 中 STRING 类型的输入输出：
- multiline text widget（多行文本输入框）
- 文本编码/解码（Base64、URL 编码）
- 模板替换（变量插值）
- 字符串操作（拼接、分割、裁剪）
"""

import base64
import urllib.parse
from typing import Tuple


class 文本处理_Node:
    """文本处理节点模板

    提供多种文本操作模式：
    - 模板替换: 模板变量替换
    - 编码: 编码（Base64 / URL）
    - 解码: 解码（Base64 / URL）
    - 文本拼接: 文本拼接
    """

    @classmethod
    def INPUT_TYPES(cls):
        """定义输入端口和参数控件"""
        return {
            "required": {
                "文本": (
                    "STRING",
                    {
                        "default": "",
                        "multiline": True,  # 多行文本输入框
                    },
                ),
                "处理模式": (
                    ["模板替换", "编码", "解码", "文本拼接"],
                    {"default": "模板替换"},
                ),
                "编码类型": (
                    ["base64", "url"],
                    {"default": "base64"},
                ),
            },
            "optional": {
                "模板变量": ("STRING", {"default": "", "multiline": True}),
                "附加文本": ("STRING", {"default": ""}),
            },
        }

    RETURN_TYPES = ("STRING",)
    RETURN_NAMES = ("处理结果",)
    FUNCTION = "process"
    CATEGORY = "🔧 自定义工具/文本处理"
    DESCRIPTION = "提供模板替换、Base64/URL 编码解码、文本拼接等多种文本处理功能。"

    def process(
        self,
        文本: str,
        处理模式: str = "模板替换",
        编码类型: str = "base64",
        模板变量: str = "",
        附加文本: str = "",
    ) -> Tuple[str]:
        """处理文本

        参数:
            文本: 主输入文本（多行）
            处理模式: 处理模式
                - 模板替换: 模板变量替换
                - 编码: 文本编码
                - 解码: 文本解码
                - 文本拼接: 文本拼接
            编码类型: 编码类型（base64 / url）
            模板变量: 模板变量，每行一个 key=value
            附加文本: 拼接模式下的附加文本

        返回:
            (处理结果,) 处理后的文本
        """
        if 处理模式 == "模板替换":
            result = self._apply_template(文本, 模板变量)
        elif 处理模式 == "编码":
            result = self._encode_text(文本, 编码类型)
        elif 处理模式 == "解码":
            result = self._decode_text(文本, 编码类型)
        elif 处理模式 == "文本拼接":
            separator = "\n"
            result = separator.join([文本, 附加文本]) if 附加文本 else 文本
        else:
            result = 文本

        return (result,)

    @staticmethod
    def _apply_template(text: str, template_vars: str) -> str:
        """模板变量替换

        将 template_vars 中每行的 key=value 解析为变量，
        然后替换 text 中的 {key} 占位符。

        示例:
            template_vars:
                name=ComfyUI
                version=0.1
            text:
                Hello {name} v{version}
            结果:
                Hello ComfyUI v0.1
        """
        if not template_vars.strip():
            return text

        variables = {}
        for line in template_vars.splitlines():
            line = line.strip()
            if "=" in line:
                key, value = line.split("=", 1)
                variables[key.strip()] = value.strip()

        try:
            return text.format(**variables)
        except (KeyError, IndexError, ValueError):
            # 格式化失败时返回原文本
            return text

    @staticmethod
    def _encode_text(text: str, encoding_type: str) -> str:
        """文本编码"""
        if encoding_type == "base64":
            return base64.b64encode(text.encode("utf-8")).decode("ascii")
        elif encoding_type == "url":
            return urllib.parse.quote(text, safe="")
        return text

    @staticmethod
    def _decode_text(text: str, encoding_type: str) -> str:
        """文本解码"""
        if encoding_type == "base64":
            try:
                return base64.b64decode(text).decode("utf-8")
            except Exception:
                return "[解码失败] 输入不是有效的 Base64 文本"
        elif encoding_type == "url":
            try:
                return urllib.parse.unquote(text)
            except Exception:
                return "[解码失败] 输入不是有效的 URL 编码文本"
        return text

    @staticmethod
    def IS_CHANGED(文本: str, 处理模式: str, **kwargs) -> str:
        """文本内容变化时需要重新处理"""
        return f"{文本}|{处理模式}"
