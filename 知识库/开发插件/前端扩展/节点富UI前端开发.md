# ComfyUI 节点富 UI 前端开发指南

## 概述
ComfyUI 节点支持在画布上显示富 UI（按钮、预览区、视频占位、进度条等），无需执行即可看到效果。
实现方式基于三层架构：LiteGraph.js → ComfyUI Extension → 自定义 Widget。

## 前置条件
1. `__init__.py` 中声明 `WEB_DIRECTORY = "./前端"` 或 `"./web"`
2. 前端目录下放置 `.js` 扩展文件
3. JS 文件通过 `app.registerExtension()` 注册

## 核心方式一：Canvas Widget（画布内原生绘制）

### 适用场景
按钮、进度条、图标、预览区域、状态指示器等需要高性能的简单UI。

### 完整规范
Widget 对象必须包含以下属性/方法：

| 属性/方法 | 必须 | 说明 |
|-----------|------|------|
| name | ✅ | Widget 名称，唯一标识 |
| type | ✅ | Widget 类型标识（自定义字符串） |
| value | ✅ | 当前值 |
| draw(ctx, node, width, y, height) | ✅ | Canvas 绘制方法 |
| mouse(event, pos, node) | 可选 | 鼠标交互处理 |
| computeSize(width) | 可选 | 返回 [width, height] 指定大小 |
| serialize() | 可选 | 序列化到工作流 |
| configure(data) | 可选 | 从工作流恢复状态 |

### draw 方法参数说明
- `ctx`: CanvasRenderingContext2D - Canvas 2D 上下文
- `node`: 当前节点实例
- `width`: widget 区域宽度（像素）
- `y`: widget 区域 Y 坐标（相对节点内部）
- `height`: widget 区域高度（默认约 20px，可通过 computeSize 覆盖）

### 按钮示例
```javascript
const buttonWidget = {
    name: "my_button",
    type: "custom_button",
    value: "",
    computeSize(width) {
        return [width, 40]; // 40px 高度
    },
    draw(ctx, node, width, y, height) {
        const margin = 10;
        const btnWidth = width - 2 * margin;
        const btnHeight = height - 10;
        const btnX = margin;
        const btnY = y + 5;
        
        // 圆角矩形背景
        ctx.fillStyle = "#4a69bd";
        ctx.beginPath();
        ctx.roundRect(btnX, btnY, btnWidth, btnHeight, 6);
        ctx.fill();
        
        // 文字
        ctx.fillStyle = "#ffffff";
        ctx.font = "bold 13px Arial";
        ctx.textAlign = "center";
        ctx.textBaseline = "middle";
        ctx.fillText("▶ 开始规划", width / 2, y + height / 2);
    },
    mouse(event, pos, node) {
        if (event.type === "pointerdown") {
            console.log("按钮被点击");
            // 触发自定义逻辑
            return true; // 返回 true 表示事件已处理
        }
    }
};

// 添加到节点
node.addCustomWidget(buttonWidget);
```

### 预览区域示例
```javascript
const previewWidget = {
    name: "preview_area",
    type: "canvas_preview",
    value: "",
    computeSize(width) {
        return [width, 180]; // 预览区高度
    },
    draw(ctx, node, width, y, height) {
        const margin = 8;
        const x = margin;
        const w = width - 2 * margin;
        const h = height - 2 * margin;
        
        // 深色背景
        ctx.fillStyle = "#1a1a2e";
        ctx.fillRect(x, y + margin, w, h);
        
        // 边框
        ctx.strokeStyle = "#00d4ff";
        ctx.lineWidth = 1.5;
        ctx.strokeRect(x, y + margin, w, h);
        
        // 中心占位文字
        ctx.fillStyle = "rgba(255,255,255,0.5)";
        ctx.font = "12px Arial";
        ctx.textAlign = "center";
        ctx.textBaseline = "middle";
        ctx.fillText("🎬 视频预览区域", width / 2, y + height / 2);
        ctx.fillText("(拖入画面后显示)", width / 2, y + height / 2 + 18);
    }
};

node.addCustomWidget(previewWidget);
```

### 进度条示例
```javascript
const progressWidget = {
    name: "progress",
    type: "canvas_progress",
    value: 0, // 0-100
    computeSize(width) {
        return [width, 30];
    },
    draw(ctx, node, width, y, height) {
        const margin = 10;
        const barHeight = 8;
        const barY = y + (height - barHeight) / 2;
        
        // 背景槽
        ctx.fillStyle = "#333";
        ctx.beginPath();
        ctx.roundRect(margin, barY, width - 2 * margin, barHeight, 4);
        ctx.fill();
        
        // 进度条
        if (this.value > 0) {
            const progressWidth = ((width - 2 * margin) * this.value) / 100;
            ctx.fillStyle = "#00d4ff";
            ctx.beginPath();
            ctx.roundRect(margin, barY, progressWidth, barHeight, 4);
            ctx.fill();
        }
        
        // 百分比文字
        ctx.fillStyle = "#aaa";
        ctx.font = "10px Arial";
        ctx.textAlign = "right";
        ctx.fillText(`${Math.round(this.value)}%`, width - margin, barY + barHeight + 12);
    }
};
```

## 核心方式二：DOM Widget（HTML/CSS UI）

### 适用场景
复杂交互 UI、视频播放器、富文本编辑、3D 预览、需要标准 HTML 元素的场景。

### addDOMWidget 参数
```javascript
node.addDOMWidget(name, type, element, options)
```
- `name`: 字符串，widget 名称
- `type`: 字符串，通常为 "dom" 或自定义类型
- `element`: HTMLElement，要嵌入的 DOM 元素
- `options`: 对象 `{ getValue(), setValue(v), getMinHeight?(), getMaxHeight?() }`

### 视频占位区示例
```javascript
const container = document.createElement("div");
container.style.cssText = `
    width: 100%;
    height: 200px;
    background: linear-gradient(135deg, #1a1a2e, #16213e);
    border: 1px solid rgba(0,212,255,0.3);
    border-radius: 6px;
    display: flex;
    flex-direction: column;
    align-items: center;
    justify-content: center;
    color: #fff;
    font-size: 13px;
    position: relative;
    overflow: hidden;
`;
container.innerHTML = `
    <div style="font-size: 32px; margin-bottom: 8px;">🎬</div>
    <div>视频预览</div>
    <small style="color: rgba(255,255,255,0.5); margin-top: 4px;">320 × 240</small>
    <div style="position: absolute; bottom: 0; left: 0; right: 0; height: 3px; background: linear-gradient(90deg, #00d4ff, #4a69bd);"></div>
`;

const widget = node.addDOMWidget("video_preview", "dom", container, {
    getValue() { return ""; },
    setValue(v) {}
});

// 必须设置 computeSize 控制高度
widget.computeSize = function(width) {
    return [width, 210]; // [宽度（自适应），高度]
};
```

### 带按钮的 DOM 控制面板
```javascript
const panel = document.createElement("div");
panel.style.cssText = "width: 100%; padding: 8px; box-sizing: border-box;";
panel.innerHTML = `
    <div style="display: flex; gap: 6px;">
        <button id="btn-play" style="flex:1; padding: 6px; background: #228B22; border: none; color: white; border-radius: 4px; cursor: pointer;">▶ 播放</button>
        <button id="btn-pause" style="flex:1; padding: 6px; background: #cc6600; border: none; color: white; border-radius: 4px; cursor: pointer;">⏸ 暂停</button>
        <button id="btn-reset" style="flex:1; padding: 6px; background: #cc3333; border: none; color: white; border-radius: 4px; cursor: pointer;">↺ 重置</button>
    </div>
`;

// 绑定事件
panel.querySelector("#btn-play").onclick = () => { console.log("播放"); };
panel.querySelector("#btn-pause").onclick = () => { console.log("暂停"); };
panel.querySelector("#btn-reset").onclick = () => { console.log("重置"); };

const controlWidget = node.addDOMWidget("controls", "dom", panel, {
    getValue() { return ""; },
    setValue(v) {}
});
controlWidget.computeSize = (w) => [w, 50];
```

## 完整注册模板

### JavaScript 扩展文件 (extension.js)
```javascript
import { app } from "../../scripts/app.js";
import { api } from "../../scripts/api.js";

app.registerExtension({
    name: "MyPlugin.RichUI",
    
    async nodeCreated(node) {
        // 按节点类型筛选（对应 Python 中的节点类名）
        if (node.comfyClass !== "MyNodeClassName") return;
        
        // === 在这里添加各种 Widget ===
        
        // 1. Canvas 按钮
        // ... (参考上面的示例)
        
        // 2. DOM 预览区
        // ... (参考上面的示例)
        
        // 3. 调整节点初始大小
        node.setSize([350, 400]);
    },
    
    async beforeRegisterNodeDef(nodeType, nodeData, app) {
        if (nodeData.name !== "MyNodeClassName") return;
        
        // 拦截执行完成回调
        const onExecuted = nodeType.prototype.onExecuted;
        nodeType.prototype.onExecuted = function(message) {
            if (onExecuted) onExecuted.apply(this, arguments);
            // 更新 UI（如刷新预览图）
        };
    }
});
```

### Python __init__.py 配置
```python
# 声明前端目录（ComfyUI 会自动加载该目录下的 .js 文件）
WEB_DIRECTORY = "./前端"  # 或 "./web"

# 节点映射（如果不使用 V3 API）
NODE_CLASS_MAPPINGS = {
    "MyNodeClassName": MyNodeClass,
}
NODE_DISPLAY_NAME_MAPPINGS = {
    "MyNodeClassName": "我的富UI节点",
}

__all__ = ["NODE_CLASS_MAPPINGS", "NODE_DISPLAY_NAME_MAPPINGS", "WEB_DIRECTORY"]
```

## 前后端通信

### Python 端发送事件
```python
from server import PromptServer

# 在节点 execute 方法中
PromptServer.instance.send_sync("my_event_name", {
    "node_id": unique_id,
    "data": {"status": "完成", "progress": 0.75}
})
```

### JavaScript 端监听
```javascript
import { api } from "../../scripts/api.js";

api.addEventListener("my_event_name", (event) => {
    const { node_id, data } = event.detail;
    // 更新对应节点的 UI
    const node = app.graph.getNodeById(node_id);
    if (node && node._myWidget) {
        node._myWidget.value = data.progress;
        node.setDirtyCanvas(true, false); // 触发重绘
    }
});
```

## 排版与布局规则（多 Widget 混排，必读）

> 违反本节规则的典型症状：自定义面板被原生控件（滑块/文本框）压住、前端界面"跑到节点背后"、控件互相重叠。

### 布局流原理
1. ComfyUI 节点内所有 widget 按 `node.widgets` 数组顺序**从上到下垂直排列**，每个 widget 的 y 坐标 = 前面所有 widget 高度之和。
2. 每个 widget 占多高，完全由它的 `computeSize(width)` 返回值决定。**不设置 computeSize 的 DOM Widget 高度视为 0，后面的原生控件会直接叠压在它的 DOM 元素上**——这是重叠问题的第一大来源。
3. Python 端 `INPUT_TYPES` 里的每个输入（INT/FLOAT/STRING 等）都会自动生成一个原生 widget，也在 `node.widgets` 数组里，与 JS 添加的自定义 widget **共用同一条布局流**。

### 强制规则
1. **禁止绝对定位覆盖节点**：DOM 元素不要用 `position:absolute/fixed` 铺满节点，必须通过 `addDOMWidget` 注册进布局流，让 ComfyUI 管理它的位置。
2. **DOM 元素实际高度 ≤ computeSize 预留高度**：`element.style.height` 要与 `computeSize` 返回的高度一致（或用 `overflow:hidden` 兜底），元素比预留空间高就会盖住下一个 widget。
3. **multiline STRING 是"大个子"**：Python 端 `"multiline": True` 的字符串输入会生成一个可拉伸的 DOM 文本框（默认占据剩余空间）。若节点同时有自定义面板，文本框极易把面板挤压/覆盖。对策：
   - 能用自定义 DOM 输入替代时，Python 端就不要再声明 multiline STRING（避免双重输入框）；
   - 必须共存时，把自定义面板放在 `node.widgets` 数组中 multiline 文本框**之前**，并给文本框设置合理初始高度。
4. **调整 widget 顺序**：JS 添加的 widget 默认排在原生控件之后。需要提前显示时重排数组：
   ```javascript
   // 把刚添加的自定义 widget 移到最前
   const w = node.widgets.pop();
   node.widgets.unshift(w);
   node.setSize(node.computeSize());
   ```
5. **节点总高度必须容纳所有 widget**：添加/删除 widget 后调用 `node.setSize(node.computeSize())` 让节点按内容自动撑高；手写 `setSize([w, h])` 时 h 必须 ≥ 所有 widget computeSize 高度之和 + 端口区高度，否则底部 widget 溢出节点边界。
6. **隐藏不用的原生控件要"三层幽灵化"**：`w.type = "hidden"` + `w.computeSize = () => [0, -4]` + `w.draw = () => {}`，只隐藏不缩高度同样会留下空洞或造成重叠（详见《模式切换动态UI方案》）。

### 混排自查清单（生成代码后逐条核对）
- [ ] 每个 addDOMWidget 后都设置了 `widget.computeSize`？
- [ ] DOM 元素没有 position:absolute/fixed 全覆盖？
- [ ] Python 端没有与自定义输入功能重复的 multiline STRING？
- [ ] widget 顺序符合期望的从上到下视觉顺序？
- [ ] 最后调用了 `node.setSize(node.computeSize())`？

## onDrawForeground 直绘面板（占位控件模式，必读）

> 适用场景：面板不用 Widget/DOM，而是在 `onDrawForeground` 里直接往节点 canvas 上画（如时间轴、波形图、色块轨道等纯展示区域）。
> 违反本节规则的典型症状：**面板画在节点边界外（节点下方）、节点高度不断自动增高、缩放节点后面板越界**。用 API 模型反复重写也修不好，因为错在方案而不是细节。

### 为什么"测量控件底部再往下画"必然失败

1. **multiline 会吃掉全部剩余高度**：新版前端（comfyui-frontend-package 1.41+）的 `_arrangeWidgets` 布局引擎会把节点体的全部剩余高度分配给 multiline 文本框，控件永远拉伸到节点底部。因此 `offsetY = 控件底部 + N` 计算出的位置**必然在节点外**——无论 N 取多少。
2. **高度正反馈**：若 `computeSize` 里再把"控件底部坐标 + 面板预留"叠加进节点高度，节点变高 → multiline 拉得更长 → 控件底部更低 → 下次 computeSize 更高，形成正反馈式增高。
3. 结论：**任何基于"测量控件底部"的定位方案在该布局引擎下都无解**，必须让布局引擎本身为面板预留空间。

### 正确方案：占位控件（Spacer Widget）

核心思路：向 `node.widgets` 末尾追加一个**固定高度、不绘制、不序列化**的占位控件，让布局引擎把面板区域"预订"出来；multiline 只能分到扣除面板预留后的空间。绘制时以占位控件的 `last_y`（布局引擎每帧写回的权威位置）定位面板。

```javascript
const PANEL_H = 90;              // 面板可视高度
const PANEL_RESERVE = PANEL_H + 20; // 预留高度（含上下间距）

// 1. onNodeCreated：追加占位控件（防重 guard 必加）
const origOnNodeCreated = nodeType.prototype.onNodeCreated;
nodeType.prototype.onNodeCreated = function () {
    const r = origOnNodeCreated?.apply(this, arguments);
    if (!this._panelSpacer) {
        const spacer = {
            type: "MYPLUGIN.PANEL_SPACER",
            name: "__panel_spacer__",
            value: "",
            serialize: false,                                 // 不写入 widgets_values
            options: { serialize: false },
            draw() {},                                        // 实际绘制在 onDrawForeground
            computeSize() { return [0, PANEL_RESERVE - 4]; }, // 布局引擎会再 +4 间距
        };
        this._panelSpacer = this.addCustomWidget
            ? this.addCustomWidget(spacer)
            : ((this.widgets ||= []).push(spacer), spacer);
    }
    this.setSize(this.computeSize()); // 占位控件已把面板预留计入原生 computeSize
    return r;
};

// 2. onDrawForeground：只读位置并绘制，绝不修改 this.size（否则形成反馈环）
const origOnDrawForeground = nodeType.prototype.onDrawForeground;
nodeType.prototype.onDrawForeground = function (ctx) {
    const r = origOnDrawForeground?.apply(this, arguments);
    if (this.flags?.collapsed) return r;      // 折叠态必须跳过
    const spacer = this._panelSpacer;
    let offsetY = spacer?.last_y;             // 布局引擎写回的权威位置（内容区坐标）
    if (typeof offsetY !== "number" || offsetY <= 0) return r; // 首帧未布局则跳过本帧
    offsetY = Math.min(offsetY + 6, this.size[1] - PANEL_H - 4); // 边界钳制，任何情况不画出节点
    if (offsetY < 0) return r;
    drawMyPanel(ctx, this.size[0], offsetY);  // 具体绘制逻辑
    return r;
};
```

### 占位控件模式强制规则

1. **禁止**用"测量最后一个控件底部 + 偏移"定位面板（含 multiline 的节点必然越界）。
2. **禁止**在 `onDrawForeground` 内修改 `this.size` 或调用 `setSize`（每帧触发 → 反馈环）。`computeSize` 是节点高度的唯一权威，`onDrawForeground` 只读不写。
3. 若需重写 `computeSize`，**直接采用原生计算结果**（占位控件已参与其中），禁止把"控件底部的节点内 Y 坐标"当高度与结果叠加——绝对坐标与相对高度语义混用是正反馈增高的根源。
4. 占位控件必须 `serialize:false` 且排在 widgets 末尾，保证 `widgets_values` 长度与旧工作流一致，新旧工作流互载不受影响。
5. 绘制前判断 `this.flags?.collapsed`，折叠态跳过。
6. 定位统一用占位控件 `last_y`（"内容区顶部为 0"坐标系，与 `size[1]`、绘制上下文一致），并用 `Math.min(offsetY, size[1] - PANEL_H - margin)` 钳制兜底。
7. 依赖前端 1.41+ 的 `_arrangeWidgets` 写回 `last_y`/`computedHeight` 行为；前端大版本升级后需回归验证一次。

### 占位模式自查清单
- [ ] 面板定位来自占位控件 `last_y`，而非"控件底部测量值"？
- [ ] onDrawForeground 只绘制、不改 size？
- [ ] computeSize 无"绝对坐标当高度"的叠加计算？
- [ ] 占位控件 serialize:false + 防重 guard？
- [ ] 有折叠态跳过 + 边界钳制？
- [ ] 手动缩放节点（缩小/放大）后面板均不越界？

## 关键注意事项

1. **WEB_DIRECTORY 必须声明**：否则前端 JS 文件不会被加载
2. **node.comfyClass 匹配**：必须与 Python 端 NODE_CLASS_MAPPINGS 的键名一致
3. **computeSize 必须设置**：DOM Widget 不设置会高度为 0
4. **setDirtyCanvas 触发重绘**：修改 Canvas Widget 值后需调用此方法
5. **事件清理**：在 `node.onRemoved` 中移除 addEventListener
6. **不需要执行即可看到 UI**：Widget 在节点被放到画布时就会渲染

## 常见问题排查

| 问题 | 原因 | 解决方案 |
|------|------|---------|
| 自定义面板被原生控件压住/前端在节点背后 | DOM Widget 未设置 computeSize，或 DOM 元素绝对定位未进布局流 | 见"排版与布局规则"：设置 computeSize + 通过 addDOMWidget 注册 |
| 原生 multiline 文本框盖住自定义 UI | multiline STRING 默认占据剩余空间 | 移除重复的 multiline 输入，或调整 widget 顺序并限制文本框高度 |
| onDrawForeground 直绘面板显示在节点下方（画出边界外） | multiline 拉伸占满剩余高度，"测量控件底部再往下画"必然越界 | 见"onDrawForeground 直绘面板"：改用占位控件模式，以 spacer.last_y 定位 |
| 节点高度不断自动增高 | computeSize 把控件底部坐标当高度叠加，形成正反馈 | 移除叠加计算，直接采用原生 computeSize 结果 |
| 底部控件溢出节点边界 | setSize 高度小于所有 widget 高度之和 | 改用 node.setSize(node.computeSize()) |
| 节点上看不到任何自定义UI | WEB_DIRECTORY 未声明或路径错误 | 检查 __init__.py |
| JS 文件未加载 | 文件不在 WEB_DIRECTORY 目录下 | 检查路径和文件名 |
| Canvas Widget 不显示 | 缺少 computeSize 或高度为 0 | 添加 computeSize 返回合适高度 |
| DOM Widget 看不到 | computeSize 未设置 | 必须设置 widget.computeSize |
| 点击按钮无反应 | mouse() 没有返回 true | 返回 true 阻止事件冒泡 |
| UI 不随数据更新 | 没调用 setDirtyCanvas | 数据变更后调用 node.setDirtyCanvas(true, false) |
| 重新打开工作流UI丢失 | 使用了 nodeCreated 但没处理 loadedGraphNode | 在两个钩子中都初始化 UI |
