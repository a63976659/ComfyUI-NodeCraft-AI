"""
插件模板市场 - 预置 ComfyUI 节点开发模板
"""
import json
import re
import shutil
from pathlib import Path
from typing import List, Dict

# 内置模板定义
BUILTIN_TEMPLATES = [
    {
        "id": "basic_node",
        "name": "基础节点",
        "description": "最简单的 ComfyUI 自定义节点模板，包含一个输入输出节点",
        "category": "入门",
        "files": {
            "__init__.py": '''from .nodes import NODE_CLASS_MAPPINGS, NODE_DISPLAY_NAME_MAPPINGS
WEB_DIRECTORY = "./web"
__all__ = ["NODE_CLASS_MAPPINGS", "NODE_DISPLAY_NAME_MAPPINGS", "WEB_DIRECTORY"]
''',
            "nodes.py": '''class 基础节点_Node:
    """基础自定义节点 - 字符串处理"""

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "文本": ("STRING", {"default": "Hello", "multiline": True}),
                "前缀": ("STRING", {"default": "[输出] "}),
                "转大写": ("BOOLEAN", {"default": False}),
            }
        }

    RETURN_TYPES = ("STRING",)
    RETURN_NAMES = ("处理结果",)
    FUNCTION = "execute"
    CATEGORY = "{custom_category}"
    DESCRIPTION = "基础字符串处理节点，支持添加前缀和大写转换。"

    def execute(self, 文本, 前缀, 转大写):
        result = 文本.upper() if 转大写 else 文本
        result = f"{前缀}{result}"
        return (result,)


NODE_CLASS_MAPPINGS = {"基础节点": 基础节点_Node}
NODE_DISPLAY_NAME_MAPPINGS = {"基础节点": "📦 基础节点 (Basic Node)"}
''',
            "requirements.txt": "# 无额外依赖\n",
            "README.md": "# My Basic Node\\n\\n基础 ComfyUI 自定义节点，支持字符串处理。\\n",
        }
    },
    {
        "id": "image_processor",
        "name": "图像处理节点",
        "description": "图像输入/输出节点模板，包含 PIL 图像处理示例",
        "category": "图像",
        "files": {
            "__init__.py": '''from .nodes import NODE_CLASS_MAPPINGS, NODE_DISPLAY_NAME_MAPPINGS
__all__ = ["NODE_CLASS_MAPPINGS", "NODE_DISPLAY_NAME_MAPPINGS"]
''',
            "nodes.py": '''import torch
import numpy as np
from PIL import Image, ImageEnhance


class ImageBrightnessContrast:
    """图像亮度/对比度调整节点"""

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "image": ("IMAGE",),
                "brightness": ("FLOAT", {"default": 1.0, "min": 0.0, "max": 3.0, "step": 0.05}),
                "contrast": ("FLOAT", {"default": 1.0, "min": 0.0, "max": 3.0, "step": 0.05}),
            }
        }

    RETURN_TYPES = ("IMAGE",)
    RETURN_NAMES = ("image",)
    FUNCTION = "execute"
    CATEGORY = "Custom/Image"

    def execute(self, image, brightness, contrast):
        # image: [B, H, W, C] torch tensor, float32, 0-1
        results = []
        for i in range(image.shape[0]):
            img_np = (image[i].cpu().numpy() * 255).astype(np.uint8)
            pil_img = Image.fromarray(img_np)

            # 调整亮度
            if brightness != 1.0:
                enhancer = ImageEnhance.Brightness(pil_img)
                pil_img = enhancer.enhance(brightness)

            # 调整对比度
            if contrast != 1.0:
                enhancer = ImageEnhance.Contrast(pil_img)
                pil_img = enhancer.enhance(contrast)

            result_np = np.array(pil_img).astype(np.float32) / 255.0
            results.append(torch.from_numpy(result_np))

        return (torch.stack(results),)


NODE_CLASS_MAPPINGS = {"ImageBrightnessContrast": ImageBrightnessContrast}
NODE_DISPLAY_NAME_MAPPINGS = {"ImageBrightnessContrast": "Image Brightness/Contrast"}
''',
            "requirements.txt": "Pillow>=9.0.0\n",
            "README.md": "# Image Brightness/Contrast\\n\\n调整图像亮度和对比度的 ComfyUI 节点。\\n",
        }
    },
    {
        "id": "model_loader",
        "name": "模型加载节点",
        "description": "支持加载自定义模型的节点模板，含模型缓存逻辑",
        "category": "模型",
        "files": {
            "__init__.py": '''from .nodes import NODE_CLASS_MAPPINGS, NODE_DISPLAY_NAME_MAPPINGS
__all__ = ["NODE_CLASS_MAPPINGS", "NODE_DISPLAY_NAME_MAPPINGS"]
''',
            "nodes.py": '''import os
import torch
import folder_paths


# 模型缓存（避免重复加载）
_model_cache = {}


class CustomModelLoader:
    """自定义模型加载节点 - 支持 safetensors/pt 文件"""

    @classmethod
    def INPUT_TYPES(cls):
        # 扫描 models 目录获取可用模型文件
        model_files = []
        models_dir = folder_paths.models_dir
        custom_dir = os.path.join(models_dir, "custom")
        if os.path.exists(custom_dir):
            for f in os.listdir(custom_dir):
                if f.endswith((".safetensors", ".pt", ".pth", ".bin")):
                    model_files.append(f)
        if not model_files:
            model_files = ["none"]

        return {
            "required": {
                "model_name": (model_files, {"default": model_files[0]}),
                "device": (["cuda", "cpu", "auto"], {"default": "auto"}),
            }
        }

    RETURN_TYPES = ("MODEL",)
    RETURN_NAMES = ("model",)
    FUNCTION = "load_model"
    CATEGORY = "Custom/Model"

    def load_model(self, model_name, device):
        if model_name == "none":
            raise ValueError("没有可用的模型文件，请将模型放入 models/custom/ 目录")

        # 检查缓存
        cache_key = f"{model_name}_{device}"
        if cache_key in _model_cache:
            print(f"[CustomModelLoader] 使用缓存模型: {model_name}")
            return (_model_cache[cache_key],)

        # 确定设备
        if device == "auto":
            device = "cuda" if torch.cuda.is_available() else "cpu"

        # 加载模型
        model_path = os.path.join(folder_paths.models_dir, "custom", model_name)
        print(f"[CustomModelLoader] 加载模型: {model_path} -> {device}")

        if model_name.endswith(".safetensors"):
            from safetensors.torch import load_file
            state_dict = load_file(model_path, device=device)
        else:
            state_dict = torch.load(model_path, map_location=device, weights_only=True)

        # 缓存模型
        _model_cache[cache_key] = state_dict
        return (state_dict,)


class CustomModelUnloader:
    """释放已缓存的模型"""

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "model": ("MODEL",),
                "clear_cache": ("BOOLEAN", {"default": True}),
            }
        }

    RETURN_TYPES = ()
    FUNCTION = "unload"
    CATEGORY = "Custom/Model"
    OUTPUT_NODE = True

    def unload(self, model, clear_cache):
        if clear_cache:
            _model_cache.clear()
            if torch.cuda.is_available():
                torch.cuda.empty_cache()
            print("[CustomModelUnloader] 模型缓存已清空")
        return ()


NODE_CLASS_MAPPINGS = {
    "CustomModelLoader": CustomModelLoader,
    "CustomModelUnloader": CustomModelUnloader,
}
NODE_DISPLAY_NAME_MAPPINGS = {
    "CustomModelLoader": "Custom Model Loader",
    "CustomModelUnloader": "Custom Model Unloader",
}
''',
            "requirements.txt": "safetensors>=0.3.0\ntorch>=2.0.0\n",
            "README.md": "# Custom Model Loader\\n\\n支持加载 safetensors/pt 模型文件，含缓存逻辑。\\n将模型文件放入 `ComfyUI/models/custom/` 目录即可使用。\\n",
        }
    },
    {
        "id": "workflow_node",
        "name": "工作流节点",
        "description": "多输入多输出的工作流节点模板，支持条件分支",
        "category": "工作流",
        "files": {
            "__init__.py": '''from .nodes import NODE_CLASS_MAPPINGS, NODE_DISPLAY_NAME_MAPPINGS
__all__ = ["NODE_CLASS_MAPPINGS", "NODE_DISPLAY_NAME_MAPPINGS"]
''',
            "nodes.py": '''class ConditionalRouter:
    """条件路由节点 - 根据条件将输入路由到不同输出"""

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "condition": ("BOOLEAN", {"default": True}),
                "input_a": ("*",),
            },
            "optional": {
                "input_b": ("*",),
            }
        }

    RETURN_TYPES = ("*", "*")
    RETURN_NAMES = ("output_true", "output_false")
    FUNCTION = "route"
    CATEGORY = "Custom/Workflow"

    def route(self, condition, input_a, input_b=None):
        if condition:
            return (input_a, input_b)
        else:
            return (input_b, input_a)


class ValueSwitch:
    """数值开关节点 - 根据索引选择不同输入"""

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "select_index": ("INT", {"default": 0, "min": 0, "max": 3}),
                "input_0": ("*",),
            },
            "optional": {
                "input_1": ("*",),
                "input_2": ("*",),
                "input_3": ("*",),
            }
        }

    RETURN_TYPES = ("*",)
    RETURN_NAMES = ("selected",)
    FUNCTION = "switch"
    CATEGORY = "Custom/Workflow"

    def switch(self, select_index, input_0, input_1=None, input_2=None, input_3=None):
        inputs = [input_0, input_1, input_2, input_3]
        index = min(select_index, len(inputs) - 1)
        result = inputs[index]
        if result is None:
            result = input_0  # fallback 到第一个输入
        return (result,)


class LoopCounter:
    """循环计数器 - 配合 ComfyUI 循环使用"""

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "start": ("INT", {"default": 0}),
                "end": ("INT", {"default": 10}),
                "step": ("INT", {"default": 1, "min": 1}),
                "current": ("INT", {"default": 0}),
            }
        }

    RETURN_TYPES = ("INT", "INT", "BOOLEAN")
    RETURN_NAMES = ("current_value", "next_value", "is_done")
    FUNCTION = "count"
    CATEGORY = "Custom/Workflow"

    def count(self, start, end, step, current):
        next_val = current + step
        is_done = next_val >= end
        return (current, next_val, is_done)


NODE_CLASS_MAPPINGS = {
    "ConditionalRouter": ConditionalRouter,
    "ValueSwitch": ValueSwitch,
    "LoopCounter": LoopCounter,
}
NODE_DISPLAY_NAME_MAPPINGS = {
    "ConditionalRouter": "Conditional Router",
    "ValueSwitch": "Value Switch",
    "LoopCounter": "Loop Counter",
}
''',
            "requirements.txt": "# 无额外依赖\n",
            "README.md": "# Workflow Nodes\\n\\n工作流控制节点集合：条件路由、数值开关、循环计数器。\\n",
        }
    },
    {
        "id": "ui_extension",
        "name": "UI 扩展节点",
        "description": "包含前端 JavaScript 扩展的节点模板，支持自定义 Widget",
        "category": "界面",
        "files": {
            "__init__.py": '''from .nodes import NODE_CLASS_MAPPINGS, NODE_DISPLAY_NAME_MAPPINGS
WEB_DIRECTORY = "./web"
__all__ = ["NODE_CLASS_MAPPINGS", "NODE_DISPLAY_NAME_MAPPINGS", "WEB_DIRECTORY"]
''',
            "nodes.py": '''class ColorPickerNode:
    """颜色选择器节点 - 带有前端自定义 Widget"""

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "color_hex": ("STRING", {"default": "#FF0000"}),
                "opacity": ("FLOAT", {"default": 1.0, "min": 0.0, "max": 1.0, "step": 0.01}),
            }
        }

    RETURN_TYPES = ("STRING", "INT", "INT", "INT", "FLOAT")
    RETURN_NAMES = ("hex_color", "red", "green", "blue", "opacity")
    FUNCTION = "parse_color"
    CATEGORY = "Custom/UI"

    def parse_color(self, color_hex, opacity):
        # 解析 hex 颜色
        color_hex = color_hex.lstrip("#")
        if len(color_hex) == 3:
            color_hex = "".join(c * 2 for c in color_hex)
        r = int(color_hex[0:2], 16)
        g = int(color_hex[2:4], 16)
        b = int(color_hex[4:6], 16)
        return (f"#{color_hex.upper()}", r, g, b, opacity)


class TextPreviewNode:
    """文本预览节点 - 在节点上显示文本内容"""

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "text": ("STRING", {"forceInput": True}),
            }
        }

    RETURN_TYPES = ("STRING",)
    RETURN_NAMES = ("text",)
    FUNCTION = "preview"
    CATEGORY = "Custom/UI"
    OUTPUT_NODE = True

    def preview(self, text):
        # 返回给前端展示
        return {"ui": {"text": [text]}, "result": (text,)}


NODE_CLASS_MAPPINGS = {
    "ColorPickerNode": ColorPickerNode,
    "TextPreviewNode": TextPreviewNode,
}
NODE_DISPLAY_NAME_MAPPINGS = {
    "ColorPickerNode": "Color Picker",
    "TextPreviewNode": "Text Preview",
}
''',
            "web/js/extensions.js": '''import { app } from "../../../scripts/app.js";

app.registerExtension({
    name: "Custom.UIExtension",
    async beforeRegisterNodeDef(nodeType, nodeData, app) {
        // 为 ColorPickerNode 添加颜色选择器 Widget
        if (nodeData.name === "ColorPickerNode") {
            const origOnNodeCreated = nodeType.prototype.onNodeCreated;
            nodeType.prototype.onNodeCreated = function () {
                if (origOnNodeCreated) origOnNodeCreated.apply(this, arguments);

                const colorWidget = this.widgets.find(w => w.name === "color_hex");
                if (colorWidget) {
                    // 添加颜色预览
                    const origDraw = colorWidget.draw;
                    colorWidget.draw = function (ctx, node, width, y, height) {
                        if (origDraw) origDraw.apply(this, arguments);
                        // 在 widget 旁绘制颜色预览方块
                        const color = this.value || "#FF0000";
                        ctx.fillStyle = color;
                        ctx.fillRect(width - 30, y + 2, 20, height - 4);
                        ctx.strokeStyle = "#666";
                        ctx.strokeRect(width - 30, y + 2, 20, height - 4);
                    };
                }
            };
        }

        // 为 TextPreviewNode 添加文本展示
        if (nodeData.name === "TextPreviewNode") {
            const origOnExecuted = nodeType.prototype.onExecuted;
            nodeType.prototype.onExecuted = function (message) {
                if (origOnExecuted) origOnExecuted.apply(this, arguments);
                if (message && message.text && message.text[0]) {
                    // 显示预览文本
                    if (!this.previewWidget) {
                        this.previewWidget = this.addWidget("text", "preview", "", () => {}, {
                            multiline: true,
                            inputEl: null,
                        });
                    }
                    this.previewWidget.value = message.text[0].substring(0, 200);
                    this.setSize(this.computeSize());
                }
            };
        }
    },
});
''',
            "requirements.txt": "# 无额外依赖\n",
            "README.md": "# UI Extension Nodes\\n\\n包含前端自定义 Widget 的 ComfyUI 节点：\\n- Color Picker: 带颜色预览的选色器\\n- Text Preview: 在节点上显示文本\\n",
        }
    },
]


# ─── UI 扩展入口类型代码骨架（entry_type -> web/js/extensions.js 内容）───
ENTRY_TYPE_JS_TEMPLATES = {
    "sidebar": '''import { app } from "../../scripts/app.js";

app.registerExtension({
    name: "com.example.{project_name}",
    async setup() {
        app.extensionManager.registerSidebarTab({
            id: "{project_name}-sidebar",
            icon: "pi pi-code",
            title: "{sidebar_title}",
            tooltip: "{sidebar_title}",
            type: "custom",
            render: (container) => {
                container.innerHTML = `<div style="padding:16px;"><h3>{sidebar_title}</h3><p>\u5728\u6b64\u5904\u6784\u5efa\u4f60\u7684\u4fa7\u8fb9\u680f\u754c\u9762</p></div>`;
            },
        });
    },
});
''',
    "topMenu": '''import { app } from "../../scripts/app.js";

app.registerExtension({
    name: "com.example.{project_name}",
    async setup() {
        // \u6ce8\u518c\u9876\u90e8\u83dc\u5355\u547d\u4ee4
        app.registerExtension({
            name: "com.example.{project_name}.commands",
            commands: [
                {
                    id: "{project_name}.action",
                    label: "{menu_label}",
                    function: () => {
                        console.log("\u83dc\u5355\u547d\u4ee4\u5df2\u6267\u884c");
                    },
                },
            ],
        });
    },
});
''',
    "statusBar": '''import { app } from "../../scripts/app.js";

app.registerExtension({
    name: "com.example.{project_name}",
    async setup() {
        // \u7b49\u5f85 UI \u52a0\u8f7d\u5b8c\u6bd5\u540e\u6ce8\u5165\u72b6\u6001\u680f\u5143\u7d20
        const statusBar = document.querySelector(".comfyui-body-bottom");
        if (statusBar) {
            const indicator = document.createElement("div");
            indicator.style.cssText = "display:flex;align-items:center;gap:4px;padding:0 8px;font-size:12px;";
            indicator.innerHTML = `<span style="width:6px;height:6px;border-radius:50%;background:#4caf50;"></span><span>{status_label}</span>`;
            statusBar.appendChild(indicator);
        }
    },
});
''',
}

# 入口类型中文标签
ENTRY_TYPE_LABELS = {
    "canvas": "\u57fa\u7840\u8282\u70b9",
    "sidebar": "\u4fa7\u8fb9\u680f Tab",
    "topMenu": "\u9876\u90e8\u83dc\u5355\u680f",
    "statusBar": "\u5e95\u90e8\u72b6\u6001\u680f",
}

# options 附加代码块（追加到 web/js/extensions.js 末尾）
OPTION_JS_SNIPPETS = {
    "shortcuts": '''
// \u5feb\u6377\u952e\u6ce8\u518c
document.addEventListener("keydown", (e) => {
    if (e.ctrlKey && e.shiftKey && e.key === "P") {
        e.preventDefault();
        console.log("\u81ea\u5b9a\u4e49\u5feb\u6377\u952e\u89e6\u53d1");
        // \u5728\u6b64\u5904\u6dfb\u52a0\u5feb\u6377\u952e\u903b\u8f91
    }
});
''',
    "settings": '''
// \u8bbe\u7f6e\u9762\u677f\u6ce8\u518c
app.ui?.settings?.addSetting({
    id: "{project_name}.my_setting",
    name: "\u6211\u7684\u8bbe\u7f6e\u9879",
    type: "boolean",
    defaultValue: true,
    onChange: (value) => { console.log("\u8bbe\u7f6e\u5df2\u66f4\u6539:", value); },
});
''',
}


class TemplateMarket:
    """模板市场管理器"""

    def __init__(self, custom_nodes_dir: Path):
        self.custom_nodes_dir = custom_nodes_dir

    def list_templates(self) -> List[Dict]:
        """获取所有可用模板"""
        return [
            {"id": t["id"], "name": t["name"], "description": t["description"], "category": t["category"]}
            for t in BUILTIN_TEMPLATES
        ]

    def get_template(self, template_id: str) -> Dict:
        """获取模板详情"""
        for t in BUILTIN_TEMPLATES:
            if t["id"] == template_id:
                return t
        return None

    def create_from_template(self, template_id: str, project_name: str,
                             entry_type: str = "canvas", options: list = None,
                             custom_names: dict = None) -> Dict:
        """从模板创建新项目

        entry_type: UI 扩展入口类型（canvas/sidebar/topMenu/statusBar），仅对 ui_extension 模板生效
        options: 附加能力列表（如 shortcuts/settings），在生成的 JS 中追加对应代码块
        custom_names: 用户自定义名称，根据 entry_type 包含不同键：
            - canvas: {"category": "节点分类名称"}
            - sidebar: {"sidebar_title": "侧边栏显示名称"}
            - topMenu: {"menu_label": "菜单显示名称"}
            - statusBar: {"status_label": "状态栏显示名称"}
        """
        if options is None:
            options = []
        if custom_names is None:
            custom_names = {}

        template = self.get_template(template_id)
        if not template:
            return {"success": False, "message": f"模板 '{template_id}' 不存在"}

        # 验证项目名
        if not re.match(r'^[a-zA-Z0-9_-]+$', project_name):
            return {"success": False, "message": "项目名称只能包含字母、数字、下划线和连字符"}

        if len(project_name) > 128:
            return {"success": False, "message": "项目名称过长（最大128字符）"}

        target_dir = self.custom_nodes_dir / project_name
        if target_dir.exists():
            return {"success": False, "message": f"目录 '{project_name}' 已存在"}

        # 复制一份模板文件字典，避免修改内置模板
        files = dict(template["files"])

        # 仅 ui_extension 模板根据 entry_type/options 生成差异化 JS
        if template_id == "ui_extension":
            js_content = ENTRY_TYPE_JS_TEMPLATES.get(entry_type)
            if js_content is not None:
                # sidebar/topMenu/statusBar 使用差异化骨架替换默认 JS
                js_content = js_content.replace("{project_name}", project_name)
                # 替换自定义显示名称占位符
                js_content = js_content.replace("{sidebar_title}", custom_names.get("sidebar_title", "我的插件"))
                js_content = js_content.replace("{menu_label}", custom_names.get("menu_label", "我的命令"))
                js_content = js_content.replace("{status_label}", custom_names.get("status_label", "我的状态"))
            else:
                # canvas 或未知类型：使用现有默认 JS
                js_content = files.get("web/js/extensions.js", "")

            # 根据 options 在 JS 末尾追加附加代码块
            for opt in options:
                snippet = OPTION_JS_SNIPPETS.get(opt)
                if snippet:
                    js_content += snippet.replace("{project_name}", project_name)

            if js_content:
                files["web/js/extensions.js"] = js_content

        # basic_node 模板：替换自定义 CATEGORY
        if template_id == "basic_node" and "nodes.py" in files:
            custom_category = custom_names.get("category", "🔧 自定义工具/基础处理")
            files["nodes.py"] = files["nodes.py"].replace("{custom_category}", custom_category)

        # 创建项目目录和文件
        target_dir.mkdir(parents=True)
        for file_path, content in files.items():
            full_path = target_dir / file_path
            full_path.parent.mkdir(parents=True, exist_ok=True)
            full_path.write_text(content, encoding='utf-8')

        entry_type_label = ENTRY_TYPE_LABELS.get(entry_type, entry_type)
        return {
            "success": True,
            "message": f"项目 '{project_name}' 已创建（入口类型：{entry_type_label}）",
            "path": str(target_dir),
            "entry_type": entry_type,
            "options": options,
        }
