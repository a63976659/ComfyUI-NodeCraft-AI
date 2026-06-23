# JS 钩子与 UI 组件

## 基础设置

### WEB_DIRECTORY 声明

在 `__init__.py` 中声明前端扩展目录：

```python
WEB_DIRECTORY = "./web"
```

### 文件结构

```
web/
├── my_extension.js       # 主扩展文件
├── widgets/              # 自定义 Widget
│   └── color_picker.js
└── styles/               # 自定义样式
    └── custom.css
```

### 导入 app

```javascript
import { app } from "../../scripts/app.js";
```

## 扩展注册

使用 `app.registerExtension` 注册前端扩展：

```javascript
import { app } from "../../scripts/app.js";

app.registerExtension({
    name: "MyCustomNodes.Extension",
    async init(app) {
        // 初始化阶段
    },
    async setup(app) {
        // 设置阶段
    },
    async beforeRegisterNodeDef(nodeType, nodeData, app) {
        // 节点类型注册前
    },
});
```

## 生命周期钩子

| 钩子 | 调用时机 | 参数 | 用途 |
|------|----------|------|------|
| `init` | 应用初始化，UI 加载前 | `app` | 全局初始化、注册自定义设置 |
| `setup` | UI 完全加载后 | `app` | DOM 操作、事件监听 |
| `addedNodeType` | 新节点类型注册时 | `nodeType, nodeData, app` | 修改节点类型默认行为 |
| `nodeCreated` | 节点实例创建时 | `node` | 添加自定义 Widget、修改实例 |
| `loadedGraphNode` | 从已保存工作流加载节点时 | `node` | 恢复自定义状态 |

### init - 应用初始化

```javascript
async init(app) {
    // 在 UI 加载之前执行
    // 适合注册全局设置、加载配置
    console.log("Extension initialized");
}
```

### setup - UI 设置

```javascript
async setup(app) {
    // UI 完全加载后执行
    // 适合 DOM 操作、事件监听
    const canvas = app.canvas;
    console.log("Canvas ready:", canvas);
}
```

### addedNodeType - 节点类型注册

```javascript
async addedNodeType(nodeType, nodeData, app) {
    // 当特定节点类型被注册时调用
    if (nodeData.name === "MyNode") {
        // 修改节点类型的默认行为
        const onNodeCreated = nodeType.prototype.onNodeCreated;
        nodeType.prototype.onNodeCreated = function () {
            const result = onNodeCreated?.apply(this, arguments);
            // 自定义逻辑...
            return result;
        };
    }
}
```

### nodeCreated - 节点实例创建

```javascript
async nodeCreated(node) {
    // 每个节点实例创建时调用
    if (node.comfyClass === "MyNode") {
        // 添加自定义 Widget
        const widget = node.addWidget(
            "combo",           // Widget 类型
            "custom_option",   // 名称
            "default_value",   // 默认值
            (value) => {       // 回调
                console.log("Selected:", value);
            },
            { values: ["A", "B", "C"] }  // 选项
        );
    }
}
```

### loadedGraphNode - 从工作流加载

```javascript
async loadedGraphNode(node) {
    // 从已保存的工作流恢复节点时调用
    if (node.comfyClass === "MyNode") {
        // 恢复自定义状态
        const savedState = node.properties?.myCustomState;
        if (savedState) {
            // 应用保存的状态...
        }
    }
}
```

## 自定义 Widget 示例

### 文本输入 Widget

```javascript
async nodeCreated(node) {
    if (node.comfyClass === "MyTextNode") {
        const widget = {
            name: "custom_text",
            type: "customtext",
            value: "",
            draw(ctx, node, widgetWidth, widgetY, height) {
                // 自定义绘制逻辑
                ctx.fillStyle = "#222";
                ctx.fillRect(0, widgetY, widgetWidth, height);
                ctx.fillStyle = "#fff";
                ctx.fillText(this.value || "Enter text...", 5, widgetY + 14);
            },
            mouse(event, pos, node) {
                // 鼠标事件处理
                if (event.type === "click") {
                    const input = prompt("Enter value:", this.value);
                    if (input !== null) {
                        this.value = input;
                    }
                }
            },
        };
        node.addCustomWidget(widget);
    }
}
```

### 进度条 Widget

```javascript
async nodeCreated(node) {
    if (node.comfyClass === "MyProgressNode") {
        const progressWidget = {
            name: "progress",
            type: "progressbar",
            value: 0,
            draw(ctx, node, widgetWidth, widgetY, height) {
                const progress = this.value;
                // 背景
                ctx.fillStyle = "#333";
                ctx.fillRect(0, widgetY, widgetWidth, height);
                // 进度
                ctx.fillStyle = "#4a9eff";
                ctx.fillRect(0, widgetY, widgetWidth * progress, height);
                // 文本
                ctx.fillStyle = "#fff";
                ctx.textAlign = "center";
                ctx.fillText(
                    `${Math.round(progress * 100)}%`,
                    widgetWidth / 2,
                    widgetY + height / 2 + 4
                );
            },
        };
        node.addCustomWidget(progressWidget);
    }
}
```

## 事件监听

### executed - 节点执行完成

```javascript
api.addEventListener("executed", (event) => {
    const { node_id, output } = event.detail;
    console.log(`Node ${node_id} executed:`, output);
});
```

### progress - 执行进度

```javascript
api.addEventListener("progress", (event) => {
    const { value, max } = event.detail;
    const percent = (value / max) * 100;
    console.log(`Progress: ${percent.toFixed(1)}%`);
});
```

### execution_start - 开始执行

```javascript
api.addEventListener("execution_start", (event) => {
    console.log("Execution started");
});
```

### execution_success - 执行成功

```javascript
api.addEventListener("execution_success", (event) => {
    console.log("Execution completed successfully");
});
```

### execution_error - 执行错误

```javascript
api.addEventListener("execution_error", (event) => {
    const { message, node_id } = event.detail;
    console.error(`Error in node ${node_id}: ${message}`);
});
```

### status - 状态变化

```javascript
api.addEventListener("status", (event) => {
    const status = event.detail;
    if (status.exec_info) {
        console.log(`Queue: ${status.exec_info.queue_remaining} remaining`);
    }
});
```

## Python → JavaScript 通信

### 从 Python 发送消息到前端

```python
from server import PromptServer

# 发送自定义事件
PromptServer.instance.send_sync(
    "my_custom_event",          # 事件名称
    {                           # 事件数据
        "node_id": node_id,
        "message": "Processing complete",
        "progress": 0.75,
    }
)
```

### 在 JavaScript 中接收

```javascript
api.addEventListener("my_custom_event", (event) => {
    const { node_id, message, progress } = event.detail;
    console.log(`Node ${node_id}: ${message} (${progress * 100}%)`);
});
```

### 完整通信示例

Python 端（节点执行中发送进度）：

```python
from server import PromptServer
from comfy_api.latest import ComfyExtension, io
from typing_extensions import override

class ProgressNode(io.ComfyNode):
    @classmethod
    def define_schema(cls):
        return io.Schema(
            node_id="ProgressNode",
            display_name="Progress Demo",
            category="example",
            inputs=[
                io.Int.Input("steps", default=10, min=1, max=100),
            ],
            outputs=[
                io.String.Output("RESULT"),
            ],
            hidden=[io.Hidden.unique_id],
        )

    @classmethod
    def execute(cls, steps):
        node_id = cls.hidden.unique_id
        for i in range(steps):
            # 处理逻辑...
            
            # 发送进度到前端
            PromptServer.instance.send_sync("my_progress", {
                "node_id": node_id,
                "step": i + 1,
                "total": steps,
            })
        
        return io.NodeOutput("done")
```

JavaScript 端（接收并更新 UI）：

```javascript
import { app } from "../../scripts/app.js";

app.registerExtension({
    name: "MyCustomNodes.ProgressUI",
    
    async nodeCreated(node) {
        if (node.comfyClass === "ProgressNode") {
            node._progressWidget = node.addWidget(
                "text", "progress", "0%", () => {}
            );
        }
    },
    
    async setup(app) {
        api.addEventListener("my_progress", (event) => {
            const { node_id, step, total } = event.detail;
            const node = app.graph.getNodeById(parseInt(node_id));
            if (node?._progressWidget) {
                node._progressWidget.value = `${step}/${total}`;
            }
        });
    },
});
```

## UI 通知

### Toast 通知

```javascript
import { app } from "../../scripts/app.js";

// 普通消息
app.ui.toast.showMessage("Operation completed");

// 带持续时间（毫秒）
app.ui.toast.showMessage("Saved!", 3000);
```

### 确认对话框

```javascript
const confirmed = await app.ui.dialog.showConfirm(
    "Confirm Action",
    "Are you sure you want to delete this?"
);
if (confirmed) {
    // 用户点击确认
}
```

### 输入对话框

```javascript
const value = await app.ui.dialog.showInput(
    "Enter Value",
    "Please enter a number:",
    "0"
);
if (value !== null) {
    console.log("User entered:", value);
}
```
