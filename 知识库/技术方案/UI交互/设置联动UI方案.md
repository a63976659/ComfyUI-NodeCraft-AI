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
3. **type 支持**：`combo`（下拉）、`boolean`（开关）、`number`（数值）、`text`（文本）
4. **双按钮架构**：新版按钮挂 `.comfyui-menu`（顶部工具栏），旧版挂 `.comfy-menu`（左侧菜单），两者同时兼容
5. **设置读取优先级**：ComfyUI Settings API → localStorage fallback → 默认值

## 注意事项

1. **即时 vs 重载**：纯 CSS 变化可即时生效，数据变化（语言、翻译范围）需 reload
2. **保存时序**：确保 `saveConfig` 的 await 完成后再 `location.reload()`
3. **默认值处理**：`defaultValue` 应从已加载的配置中取，而非硬编码
4. **设置面板兼容**：新旧版 ComfyUI 的设置面板 DOM 结构不同，需做兼容判断
5. **双容器去重**：新旧菜单可能同时存在，避免重复添加按钮（可用 id 去重）
