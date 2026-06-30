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


class TextProcessNode:
    """文本处理节点模板

    提供多种文本操作模式：
    - template: 模板变量替换
    - encode: 编码（Base64 / URL）
    - decode: 解码（Base64 / URL）
    - concat: 文本拼接
    """

    @classmethod
    def INPUT_TYPES(cls):
        """定义输入端口和参数控件"""
        return {
            "required": {
                "text": (
                    "STRING",
                    {
                        "default": "",
                        "multiline": True,  # 多行文本输入框
                    },
                ),
                "mode": (
                    ["template", "encode", "decode", "concat"],
                    {"default": "template"},
                ),
                "encoding_type": (
                    ["base64", "url"],
                    {"default": "base64"},
                ),
            },
            "optional": {
                "template_vars": ("STRING", {"default": "", "multiline": True}),
                "extra_text": ("STRING", {"default": ""}),
            },
        }

    RETURN_TYPES = ("STRING",)
    RETURN_NAMES = ("result",)
    FUNCTION = "process"
    CATEGORY = "Custom/Text"

    def process(
        self,
        text: str,
        mode: str = "template",
        encoding_type: str = "base64",
        template_vars: str = "",
        extra_text: str = "",
    ) -> Tuple[str]:
        """处理文本

        参数:
            text: 主输入文本（多行）
            mode: 处理模式
                - template: 模板变量替换
                - encode: 文本编码
                - decode: 文本解码
                - concat: 文本拼接
            encoding_type: 编码类型（base64 / url）
            template_vars: 模板变量，每行一个 key=value
            extra_text: 拼接模式下的附加文本

        返回:
            (result,) 处理后的文本
        """
        if mode == "template":
            result = self._apply_template(text, template_vars)
        elif mode == "encode":
            result = self._encode_text(text, encoding_type)
        elif mode == "decode":
            result = self._decode_text(text, encoding_type)
        elif mode == "concat":
            separator = "\n"
            result = separator.join([text, extra_text]) if extra_text else text
        else:
            result = text

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
                return f"[解码失败] 输入不是有效的 Base64 文本"
        elif encoding_type == "url":
            try:
                return urllib.parse.unquote(text)
            except Exception:
                return f"[解码失败] 输入不是有效的 URL 编码文本"
        return text

    @staticmethod
    def IS_CHANGED(text: str, mode: str, **kwargs) -> str:
        """文本内容变化时需要重新处理"""
        return f"{text}|{mode}"
