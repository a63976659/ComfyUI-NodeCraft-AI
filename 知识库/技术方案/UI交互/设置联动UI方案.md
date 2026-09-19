# 设置联动UI方案

## 来源
参考工具/项目：ComfyUI-Chinese-Translation（https://github.com/AIGODLIKE/AIGODLIKE-ComfyUI-Translation）
定位：通过后端配置文件 + 前端设置面板 + ComfyUI Settings API 实现设置变更即时影响 UI

## 适用场景
- 插件需要用户可配置的选项（开关、语言、样式等）
- 设置变更后需要即时或重载后生效
- 需要与 ComfyUI 原生设置面板集成

## 核心思路
后端使用 config.json 持久化配置，提供 GET/POST API 读写。前端通过 ComfyUI 的 `app.ui.settings.addSetting()` API 注册设置项到原生设置面板，监听 `onChange` 回调在变更时保存配置并触发 UI 刷新（部分即时生效，部分需要 reload）。同时兼容新旧两版菜单容器，并对设置读取做多级 fallback。

## 实现步骤

### 1. 后端配置管理
```python
GLOBAL_CONFIG = load_config()

@server.PromptServer.instance.routes.get("/translation_node/get_config")
async def get_config(request):
    return web.Response(status=200, body=json.dumps(GLOBAL_CONFIG))

@server.PromptServer.instance.routes.post("/translation_node/set_config")
async def set_config(request):
    post = await request.post()
    config_data = {
        "translation_enabled": post.get("translation_enabled", "true") == "true",
        "locale": post.get("locale", "zh-CN"),
        "button_style": post.get("button_style", "gradient"),
        "disabled_plugins": json.loads(post.get("disabled_plugins", "[]")),
        "translate_options": post.get("translate_options", "true") == "true"
    }
    
    # 持久化到文件
    with open(config_path, 'w', encoding='utf-8') as f:
        json.dump(config_data, f, indent=2, ensure_ascii=False)
    
    # 更新内存
    global GLOBAL_CONFIG
    GLOBAL_CONFIG = config_data
    return web.Response(status=200, body=json.dumps({"success": True}))
```

### 2. 前端集成 ComfyUI 设置面板
```javascript
export async function registerSettings(app) {
    // 语言选择器
    app.ui.settings.addSetting({
        id: "🌐Language翻译语言.Language",
        name: "🌐 Language settings for translation",
        type: "combo",
        options: availableLocales,
        defaultValue: currentConfig.locale,
        onChange: async (newVal) => {
            if (newVal !== currentConfig.locale) {
                await saveConfig(currentConfig.translation_enabled, newVal);
                location.reload();  // 语言切换需要重载
            }
        }
    });
    
    // 按钮样式（即时生效，无需重载）
    app.ui.settings.addSetting({
        id: "🌐Language翻译语言.ButtonStyle",
        name: "🎨 Button Style",
        type: "combo",
        options: ["gradient (七彩渐变)", "plain (原生低调)"],
        onChange: (newVal) => {
            // 即时更新 CSS 类
            const isPlain = newVal.includes("plain");
            document.querySelectorAll(".translation-btn").forEach(btn => {
                btn.className = `translation-btn ${isPlain ? "translation-active-plain" : "translation-active-gradient"}`;
            });
        }
    });
    
    // COMBO 选项翻译开关
    app.ui.settings.addSetting({
        id: "🌐Language翻译语言.TranslateOptions",
        name: "📋 Translate COMBO Options",
        type: "boolean",
        defaultValue: currentConfig.translate_options,
        onChange: async (newVal) => {
            await saveConfig(..., newVal);
            location.reload();  // 需要重新编译翻译数据
        }
    });
}
```

### 3. 翻译开关按钮（双按钮架构，兼容新旧 UI）
```javascript
export function addPanelButtons(app) {
    // 新版：顶部工具栏 .comfyui-menu
    const newMenu = document.querySelector(".comfyui-menu");
    if (newMenu) {
        newMenu.appendChild(makeTranslationButton());
    }

    // 旧版：左侧菜单 .comfy-menu
    if (app.ui.menuContainer) {  // 对应旧版 .comfy-menu
        app.ui.menuContainer.appendChild(makeTranslationButton());
    }
}

function makeTranslationButton() {
    return $el("button.translation-btn", {
        textContent: translationEnabled ? "翻译开启 (zh-CN)" : "翻译关闭",
        className: `translation-btn ${activeClass}`,
        onclick: async () => {
            await toggleTranslation();
            location.reload();
        },
    });
}
```

### 4. 设置读取多级 fallback
```javascript
function readSetting(id, defaultValue) {
    // 优先级 1：ComfyUI Settings API
    try {
        const v = app.ui.settings.getSettingValue(id);
        if (v !== undefined && v !== null) return v;
    } catch (e) { /* 老版本无此 API */ }

    // 优先级 2：localStorage fallback
    const local = localStorage.getItem(id);
    if (local !== null) return local;

    // 优先级 3：默认值
    return defaultValue;
}
```

## ComfyUI 适配要点

1. **`app.ui.settings.addSetting()`**：ComfyUI 原生设置注册 API
2. **设置 id 格式**：使用 `分类.设置名` 格式，带 emoji 前缀便于分组和辨识
3. **type 支持**：`combo`（下拉）、`boolean`（开关）、`number`（数值）、`text`（文本）、**函数型**（`() => DOM`，返回自定义面板，宿主用 CustomFormValue 包裹）
4. **双按钮架构**：新版按钮挂 `.comfyui-menu`（顶部工具栏），旧版挂 `.comfy-menu`（左侧菜单），两者同时兼容
5. **设置读取优先级**：ComfyUI Settings API → localStorage fallback → 默认值

## 函数型 type 自定义面板（⚠️ 高频踩坑）

当标准 type（combo/boolean/number/text）无法满足需求（如需要多行表单、卡片列表、行内编辑等复合 UI），可将 `type` 设为**函数**，返回自定义 DOM 元素：

```javascript
app.ui.settings.addSetting({
    id: "分类.设置名.配置面板",
    category: ["📦 插件名", "子分类名"],
    name: "面板显示名",
    type: () => {
        const panel = document.createElement("div");
        panel.id = "my-custom-panel";
        // ... 构建面板 DOM ...
        return panel;   // 宿主 CustomFormValue 组件会把此 DOM 挂进设置项
    },
    defaultValue: "",
});
```

### DOM 层级（DevTools 实测，ComfyUI 1.41.x）

宿主 Vue 的 `CustomFormValue` 组件会**额外包一层** `<div id="settingId" data-v-xxxxx>` 包裹层：

```
FormItem 根（.setting-item）
├── .form-label          ← 标签区（上方）
└── .form-input          ← flex 行容器（flex:1 1 auto，justify-content:flex-start）
    └── <div id="分类.设置名.配置面板" data-v-xxxxx>   ← ⚠️ 宿主包裹层（display:block）
        └── 你的面板 DOM（type 函数返回的元素）
```

### ⚠️ 包裹层宽度陷阱（必修）

`.form-input` 是 **flex 行容器**（`flex:1 1 auto`），而宿主包裹层是 `display:block` 子元素——在 flex 行容器里 block 子元素**默认按内容宽收缩**，不会自动撑满父宽。

**实测数据**（1400px 对话框）：
- `.form-input`：988px ✓
- 宿主包裹层：709px ✗（仅按面板内容宽收缩）
- 面板：709px ✗（被包裹层钳制）

**宿主对此包裹层无任何 CSS 规则**——函数型 type 自定义面板的包裹层满宽**无官方约定**，必须自己处理。

### 正确做法：四行 CSS + setTimeout 挂类

```javascript
const 行类 = "my-formrow";
const 包裹层类 = "my-hostwrap";

// ① 注入 CSS（在 type 函数或 setup 里一次性注入）
const style = document.createElement("style");
style.textContent = [
    `.${行类} { flex-direction:column !important; align-items:stretch !important; gap:6px !important; height:auto !important; }`,
    `.${行类} > .form-label { flex:0 0 auto !important; width:100% !important; }`,
    `.${行类} > .form-input { width:100% !important; flex:1 1 auto !important; justify-content:flex-start !important; box-sizing:border-box !important; }`,
    /* 关键：强制包裹层满宽 */
    `.${行类} > .form-input > .${包裹层类} { width:100% !important; box-sizing:border-box !important; }`,
].join("\n");
document.head.appendChild(style);

// ② type 函数里用 setTimeout(0) 给父层挂类
function 撑满设置行(面板) {
    setTimeout(() => {
        // 挂包裹层类（CustomFormValue 生成的 <div id=settingId>）
        if (面板.parentElement) 面板.parentElement.classList.add(包裹层类);
        // 向上找 .form-input，给它的父（FormItem 根）挂行类
        let 输入盒 = null;
        let p = 面板.parentElement;
        for (let i = 0; i < 8 && p; i++) {
            if (p.classList?.contains("form-input")) { 输入盒 = p; break; }
            p = p.parentElement;
        }
        if (输入盒?.parentElement) 输入盒.parentElement.classList.add(行类);
    }, 0);  // setTimeout 0：CustomFormValue 先调 type 函数、后 appendChild，调 type 时 parentElement 尚为 null
}
```

### 三种假修法（切勿重踏）

| 假修法 | 为什么错 |
|--------|----------|
| `grid-column: 1/-1` 跨列 | 宿主设置项**不是两列 grid**，是 flex column，跨列无效 |
| 解除中间层 `max-width` 钳制 | 实测 DOM 层级中**没有任何层**设了 max-width，治的是不存在的病 |
| 拉宽对话框 `max-w-[1400px]` | 对话框本身够宽（1260px），问题在**包裹层**不按父宽撑满 |

### 判据

> **只要用函数型 `type` 返回自定义 DOM，就必须在 CSS 里加 `.${行类} > .form-input > .包裹层类 { width:100% !important }` 这条规则，并在 setTimeout 0 里给 `面板.parentElement` 挂包裹层类。**
> 标准 type（combo/boolean 等）不受此影响——宿主为它们生成的是标准控件，不走 CustomFormValue 包裹层。

## 注意事项

1. **即时 vs 重载**：纯 CSS 变化可即时生效，数据变化（语言、翻译范围）需 reload
2. **保存时序**：确保 `saveConfig` 的 await 完成后再 `location.reload()`
3. **默认值处理**：`defaultValue` 应从已加载的配置中取，而非硬编码
4. **设置面板兼容**：新旧版 ComfyUI 的设置面板 DOM 结构不同，需做兼容判断
5. **双容器去重**：新旧菜单可能同时存在，避免重复添加按钮（可用 id 去重）
6. **⚠️ 函数型 type 的 setTimeout 0 时机**：`type` 函数被调用时返回的 DOM 尚未被宿主 appendChild，`面板.parentElement` 为 null——必须用 `setTimeout(fn, 0)` 延迟到宿主完成挂载后再操作父层级
7. **⚠️ 函数型 type 包裹层满宽**：宿主 CustomFormValue 包裹层默认按内容宽收缩，必须显式 CSS 强制 `width:100%`（详见上方「函数型 type 自定义面板」章节）
8. **⚠️ category 两段约定**：`category: ["一级分类", "二级分类"]`，第一段是左侧导航的折叠组名，第二段是组内子页签——写错会导致设置项出现在错误位置或找不到
