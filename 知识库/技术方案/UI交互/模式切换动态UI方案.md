# 模式切换动态UI方案

## 来源
参考工具/项目：ComfyUI-prompt-formula（https://www.bilibili.com/video/BV1UBB2YhEpJ）
定位：根据模式选择动态显示/隐藏不同组件组，实现前端UI跟随模式切换变化

## 适用场景
- 节点有多种工作模式（如"使用预设"/"手动输入"）
- 需要根据选择动态显隐不同widget组
- 需要在隐藏原生widget的同时用自定义DOM替代

## 核心思路
采用"幽灵化"策略：三层隐藏机制（type="hidden" + computeSize返回负高度 + 空draw方法）完全消除未使用组件的视觉占位，同时用 DOM Widget 替代原生输入框提供更好的交互体验。

## 实现步骤

### 1. 幽灵化隐藏/恢复机制
```javascript
const toggleCanvasW = (name, show) => {
    const w = getW(name);
    if (!w) return;
    
    if (show) {
        w.type = w._origType;        // 恢复原始类型
        // 恢复原生绘制方法
    } else {
        w.type = "hidden";           // 第一层：禁用渲染
        w.computeSize = () => [0, -4]; // 第二层：负高度消除占位
        w.draw = () => {};           // 第三层：空绘制
        w.mouse = () => false;       // 禁用鼠标交互
    }
};
```

### 2. 模式切换显隐逻辑
```javascript
node.toggleVisibility = () => {
    const isPreset = modeW.value === "使用预设";
    
    if (isPreset) {
        // 预设模式：显示预设下拉 + 预览框
        toggleCanvasW("预设_主体描述", true);
        node.previewSub.style.display = "flex";
        node.manSub.wrap.style.display = "none";
    } else {
        // 手动模式：显示手动输入框
        toggleCanvasW("预设_主体描述", false);
        node.previewSub.style.display = "none";
        node.manSub.wrap.style.display = "flex";
    }
    
    node.setDirtyCanvas(true, true);
};
```

### 3. DOM 替代 + 数据同步
```javascript
// 手动输入的 DOM input 实时同步到原生 widget value
inputElement.addEventListener("input", () => {
    原生Widget.value = inputElement.value;
});
```
后端 Python 处理时读取 widget value 而非 DOM 内容，保证数据一致性。

## ComfyUI 适配要点

1. **保存原始类型**：隐藏前必须 `w._origType = w.type` 保存，恢复时还原
2. **computeSize 返回 [0, -4]**：-4px 抵消 ComfyUI 默认的 widget 间距
3. **数据流不变**：无论前端如何显隐，后端始终通过 widget.value 获取数据
4. **addDOMWidget 注入**：自定义 DOM 输入需通过此方法注册到节点

## 注意事项

1. **序列化兼容**：隐藏状态不影响节点保存/加载，模式值会被序列化
2. **加载恢复**：`onConfigure` 中需重新执行 `toggleVisibility()` 恢复正确显隐状态
3. **widget 索引稳定性**：不要删除 widget，只做隐藏，避免索引错乱
4. **多模式扩展**：使用 switch-case 结构而非 if-else 便于添加新模式
