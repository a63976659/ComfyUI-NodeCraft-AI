# DOM动态翻译方案

## 来源
参考工具/项目：ComfyUI-Chinese-Translation（https://github.com/AIGODLIKE/AIGODLIKE-ComfyUI-Translation）
定位：使用 MutationObserver 实时监听 DOM 变化，对新增的 UI 元素自动翻译并防抖合并

## 适用场景
- 需要翻译动态生成的 DOM 内容（对话框、弹窗、菜单等）
- 需要对 SPA 应用中不断变化的 UI 文本进行实时翻译
- 需要在不修改源代码的情况下实现 UI 本地化

## 核心思路
通过 MutationObserver 监听 body 及关键容器的 DOM 变化（childList + subtree），当检测到新增节点时，使用微任务防抖合并同一轮回调中的多个节点，然后对叶子文本节点递归翻译。使用 Set 数据结构 O(1) 判断是否已翻译，避免重复处理。叶子节点保护是**双层安全**：旧版靠回调包装修复，新版靠叶子保护修复，两者缺一不可。

## 实现步骤

### 1. 多级 MutationObserver 监听
```javascript
export function applyMenuTranslation(T) {
    texe.T = T;
    
    // 监听 body：捕获模态框、对话框等全局新增
    const bodyObserver = observeFactory(
        document.querySelector("body.litegraph"),
        (mutationsList) => {
            for (const mutation of mutationsList) {
                for (const node of mutation.addedNodes) {
                    texe.translateAllText(node);
                }
            }
        },
        true  // subtree=true 监听所有后代
    );
    
    // 监听菜单栏：捕获菜单项变化
    const menuObserver = observeFactory(
        document.querySelector(".comfyui-menu"),
        handleComfyNewUIMenu,
        true
    );
}

function observeFactory(target, callback, subtree = false) {
    if (!target) return null;
    const observer = new MutationObserver(callback);
    observer.observe(target, {
        childList: true,
        subtree: subtree
    });
    return observer;
}
```

### 2. 微任务防抖合并
```javascript
class TExe {
    constructor() {
        this.pendingNodes = new Set();
        this.isScheduling = false;
    }
    
    translateAllText(node) {
        this.pendingNodes.add(node);
        
        if (!this.isScheduling) {
            this.isScheduling = true;
            
            // queueMicrotask 合并同一轮 MutationObserver 的多次回调
            queueMicrotask(() => {
                const nodesToTranslate = Array.from(this.pendingNodes);
                this.pendingNodes.clear();
                this.isScheduling = false;
                
                for (const targetNode of nodesToTranslate) {
                    if (!document.contains(targetNode)) continue;  // 跳过已移除节点
                    this.replaceText(targetNode);
                }
            });
        }
    }
}
```

### 3. 安全的递归文本替换
```javascript
replaceText(target) {
    // 关键：只对无子元素的节点修改 innerText（保护 Vue 事件绑定）
    if (target.innerText && 
        !isAlreadyTranslatedText(target.innerText) &&
        (!target.children || target.children.length === 0)) {
        
        const translated = this.MT(target.innerText);
        if (translated) {
            target.innerText = translated;
        }
    }
    
    // 递归处理子元素
    if (target.childNodes && target.childNodes.length) {
        const childNodes = Array.from(target.childNodes);
        for (const childNode of childNodes) {
            this.replaceText(childNode);
        }
    }
}
```

### 4. O(1) 去重检查
```javascript
const translatedValueSet = new Set();  // 存储所有已翻译的中文文本

function isAlreadyTranslatedText(text) {
    return translatedValueSet.has(text);
}

// 初始化时将所有翻译目标值加入 Set
function buildTranslatedSet(translations) {
    for (const [key, value] of Object.entries(translations)) {
        translatedValueSet.add(value);
    }
}
```

## ComfyUI 适配要点

1. **body.litegraph**：ComfyUI 的 body 元素带有此 class，用于精确定位
2. **`.comfyui-menu`**：新版 UI 的菜单容器选择器
3. **`.p-dialog-mask`**：PrimeVue 对话框遮罩层，新增时需翻译内容
4. **PrimeVue 菜单结构**：`<li> → <div> → <a> → <span> → 文本节点`，只有最内层 span（`children.length === 0`）才允许修改
5. **queueMicrotask vs setTimeout**：微任务在同一事件循环内执行，延迟更低

## 注意事项

1. **叶子节点检查**：修改有子元素的 `innerText` 会销毁子节点及其事件绑定（双层安全：新版靠叶子保护，旧版靠回调包装）
2. **Vue 组件保护**：Vue 通过 `textContent` 绑定数据，修改 `innerText` 可能破坏响应式
3. **性能瓶颈**：频繁 DOM 变化时防抖合并至关重要，否则可能卡顿
4. **已移除节点**：翻译前需 `document.contains()` 检查节点是否仍在 DOM 中
5. **MutationObserver 断开**：页面卸载时应调用 `observer.disconnect()` 释放资源
