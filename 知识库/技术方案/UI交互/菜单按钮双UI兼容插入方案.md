# 菜单按钮双UI兼容插入方案

## 来源
参考工具/项目：ComfyUI-Chinese-Translation（https://github.com/AIGODLIKE/AIGODLIKE-ComfyUI-Translation）
生产验证：ComfyUI-Any-Path-Repair（修复按钮新旧 UI 均正常显示）
定位：在 ComfyUI 顶部/侧部菜单注入自定义按钮，一套代码同时兼容旧版左侧浮动菜单、新版 Vue 顶栏（含 `window.comfyAPI` 缺失的中间态环境）

## 适用场景
- 插件需要向 ComfyUI 菜单注入按钮/开关等入口控件
- 要求同时兼容旧版 UI（左侧 `.comfy-menu` 浮动菜单）与新版 UI（顶部工具栏）
- 用户环境横跨多个 ComfyUI 发行版/前端版本，无法假设某一 API 必然存在

## 核心思路
**不依赖单一 API 判断新旧 UI，而是按优先级探测"真实可见"的 DOM 锚点**：旧版可见菜单 → `app.menu.settingsGroup` → Vue 顶栏右侧按钮区 → 顶栏容器。命中即插入，全部未命中则轮询重试，插入成功后启动看门狗防止节点被 Vue 重渲染吞掉。

> **关键认知**：新版 ComfyUI 中 `.comfy-menu` 与 `app.ui.menuContainer` 的 DOM **依然存在**，只是被 `display:none` 隐藏。只判断"存在"不判断"可见"，按钮会插进隐藏容器导致永远不显示——这是最常见的兼容失败根因。

## 实现步骤

### 1. 可见性判断（元素自身 + 父容器双重检测）

```javascript
// 判断元素及其父容器是否真实可见（排除 display:none 的隐藏容器）
function isVisibleEl(el) {
    if (!el || !el.isConnected) return false;
    const r = el.getBoundingClientRect();
    if (r.width <= 0 || r.height <= 0) return false;
    const pr = el.parentElement?.getBoundingClientRect();
    return !!pr && pr.width > 0 && pr.height > 0;
}
```

### 2. 四级可见锚点探测（按优先级返回）

```javascript
function findVisibleAnchor(app) {
    // ① 旧版 UI：.comfy-menu 与 menuContainer 同时可见才算旧版菜单在用
    const comfyMenu = document.querySelector(".comfy-menu");
    if (comfyMenu && isVisibleEl(comfyMenu)
        && app.ui?.menuContainer && isVisibleEl(app.ui.menuContainer)) {
        return { kind: "legacy", anchor: app.ui.menuContainer };
    }
    // ② 新版经典顶栏：设置按钮组前方
    const settingsGroupEl = app.menu?.settingsGroup?.element;
    if (settingsGroupEl && isVisibleEl(settingsGroupEl)) {
        return { kind: "settingsGroup", anchor: settingsGroupEl };
    }
    // ③ 新版 Vue 顶栏：右侧按钮区（无 comfyAPI 时的降级锚点）
    const topRight = document.querySelector(".workflow-tabs-container .ml-auto");
    if (topRight && isVisibleEl(topRight)) {
        return { kind: "topRight", anchor: topRight };
    }
    // ④ 兜底：顶栏容器本体
    const topBar = document.querySelector(".workflow-tabs-container");
    if (topBar && isVisibleEl(topBar)) {
        return { kind: "topBar", anchor: topBar };
    }
    return null;
}
```

### 3. 按锚点类型差异化插入

```javascript
function insertButton(app, makeLegacyBtn, makeNewBtn) {
    const hit = findVisibleAnchor(app);
    if (!hit) return false;

    let el;
    if (hit.kind === "legacy") {
        el = makeLegacyBtn();          // 原生 <button> 即可，挂自定义 class
        hit.anchor.appendChild(el);
    } else {
        // 新版：优先 window.comfyAPI 的 ComfyButton（融入原生样式），
        // API 缺失时降级原生按钮——绝不能假设 comfyAPI 存在
        el = (window?.comfyAPI?.button?.ComfyButton) ? makeNewBtn() : makeLegacyBtn();
        if (hit.kind === "settingsGroup") hit.anchor.before(el);   // 设置组前方
        else if (hit.kind === "topRight") hit.anchor.prepend(el);  // 右侧按钮区首部
        else hit.anchor.appendChild(el);                           // 顶栏末尾
    }
    return true;
}
```

### 4. 轮询重试（锚点可能尚未就绪）

```javascript
function addPanelButtons(app) {
    if (insertButton(app)) { startWatchdog(app); return; }
    // setup 阶段顶栏可能还没渲染完：每 500ms 重试，最多 30 秒
    let tries = 0;
    const timer = setInterval(() => {
        tries++;
        if (insertButton(app)) {
            clearInterval(timer);
            startWatchdog(app);
        } else if (tries >= 60) {
            clearInterval(timer);
            console.error("未找到可用的菜单锚点，按钮未插入");
        }
    }, 500);
}
```

### 5. 看门狗（防 Vue 重渲染吞节点）

```javascript
// 新版 Vue 顶栏会不定期重渲染，注入的 DOM 节点可能被整体替换掉；
// 2 秒巡检一次：节点丢失或所在容器变不可见时自动重新插入
function startWatchdog(app) {
    setInterval(() => {
        if (!btnEl || !btnEl.isConnected || !isVisibleEl(btnEl)) {
            insertedEl?.remove?.();     // 移除以完整插入节点为准（含包裹层）
            insertedEl = null; btnEl = null;
            insertButton(app);
        }
    }, 2000);
}
```

## 按钮状态更新的兼容写法

按钮文字/状态动态更新时，需同时兼容两种按钮形态：

```javascript
function setButtonState(buttonElement, isProcessing, text) {
    if (!buttonElement) return;
    // ComfyButton 挂有自定义 setLabel；原生按钮降级走 innerText
    if (buttonElement.setLabel) buttonElement.setLabel(text);
    else buttonElement.innerText = text;
    // ComfyButton 的状态节点可能套了一层 .element 包裹
    const el = buttonElement.element || buttonElement;
    el.classList.toggle("fixer-processing", isProcessing);
}
```

注意"插入节点"与"状态更新目标"要分开保存：ComfyButtonGroup 包裹层是插入节点，内部 `btn.element`（带 setLabel）才是状态更新目标；看门狗移除时必须移除包裹层，否则残留空壳。

## ComfyUI 适配要点

1. **可见性优先于存在性**：所有锚点判断必须过 `isVisibleEl()`，`.comfy-menu` 在新版 UI 是"存在但隐藏"的陷阱
2. **锚点优先级**：旧版可见菜单 → `settingsGroup` → `.workflow-tabs-container .ml-auto` → `.workflow-tabs-container`，与 ComfyUI-Chinese-Translation 生产顺序一致
3. **不信任单一 API**：`window.comfyAPI`、`app.menu.settingsGroup`、`app.ui.menuContainer` 都可能缺失，每一级都要可选链探测
4. **调用时机**：在扩展 `setup()` 内延迟约 500ms 首次尝试，失败交给轮询兜底，不要阻塞 setup
5. **CSS 作用域**：降级原生按钮的样式用自己的 class 前缀（如 `.fixer-btn-legacy-ui`），处理中动画用 `!important` 保证两种形态都生效

## 注意事项

1. **隐藏容器陷阱**：只查 `document.querySelector(".comfy-menu")` 存在就插入，是"按钮加了但不显示"的头号原因
2. **幂等插入**：插入前先查 `buttonElement?.isConnected`，避免轮询/看门狗叠加出多个按钮
3. **看门狗与隐藏菜单的循环**：用户手动隐藏旧版菜单时，看门狗会反复"移除→重插同一锚点"，行为无害（与参考实现一致），无需特殊处理
4. **移除节点用包裹层**：ComfyButton 场景下 remove 内部元素会留下空 group 壳，必须记录并移除完整插入节点
5. **去重 id**：降级原生按钮固定 id（如 `fixer-legacy-btn`），同一时刻只存在一个实例，重复调用天然幂等
