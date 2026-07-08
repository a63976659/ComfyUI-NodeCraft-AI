// ═══════════════════════════════════════════════════════════════
// 工具函数.js — 公共工具、常量定义、事件清理注册表
// NodeCraft AI — Luxury Terminal / Neo-Noir Hacker
// ═══════════════════════════════════════════════════════════════

// ─── 常量定义 ─────────────────────────────────────────────────
const NCA_COLORS = {
    accent: '#00ffc8',
    blue: '#00d4ff',
    purple: '#8b5cf6',
    bg: '#0a0a0f',
    error: '#ff5050',
};

export const NCA_STORAGE_KEYS = {
    plugin: 'NodeCraftAI_selectedPlugin',
    session: 'NodeCraftAI_sessions',
    activeTab: 'NodeCraftAI_activeTab',
    theme: 'NodeCraftAI_theme',
};

// ─── 工具名中文映射（仅用于前端显示，后端工具名保持英文）────────
export const 工具名映射 = {
    "read_plugin_file": "读取文件",
    "search_plugin_file": "搜索文件",
    "write_plugin_file": "写入文件",
    "edit_file": "编辑文件",
    "batch_edit": "批量编辑",
    "list_plugin_files": "列出文件",
};

// 将英文工具名转为中文显示名；未收录时原样返回
export function 工具名显示(工具名) {
    return 工具名映射[工具名] || 工具名;
}

// ─── 主题切换（深色/浅色）──────────────────────────────
// 默认 dark（保持现有用户体验）；light 仅多一个可选项。
// 主题类施加在本插件自有根 .nca-sidebar-root 上，避免污染 ComfyUI 共享侧边栏容器。
// CSS 变量沿 DOM 继承覆盖 :root 默认值，无需重新加载。
export function 获取主题() {
    let v = null;
    try { v = localStorage.getItem(NCA_STORAGE_KEYS.theme); } catch (_) {}
    return v === 'light' ? 'light' : 'dark';
}

export function 应用主题(target, theme) {
    const t = theme === 'light' ? 'light' : 'dark';
    if (!target) return t;
    // 找到本插件的实际根容器（.nca-sidebar-root），而非 ComfyUI 共享容器
    const root = (target.classList && target.classList.contains('nca-sidebar-root'))
        ? target
        : ((target.querySelector && target.querySelector('.nca-sidebar-root')) || target);
    root.classList.remove('nca-theme-dark', 'nca-theme-light');
    root.classList.add(`nca-theme-${t}`);
    return t;
}

export function 切换主题(container) {
    const next = 获取主题() === 'light' ? 'dark' : 'light';
    应用主题(container, next);
    try { localStorage.setItem(NCA_STORAGE_KEYS.theme, next); } catch (_) {}
    return next;
}

// ─── 安全 localStorage 读取 ───────────────────────────────────
// 隐私模式或存储空间已满时 localStorage 可能不可用，统一防护
export function 安全存储读(key, 默认值 = "") {
    try { const v = localStorage.getItem(key); return v !== null ? v : 默认值; } catch (_) { return 默认值; }
}

export const NCA_API_BASE = '/ai-coder';

// SVG 图标 — 菱形品牌标志
export const LOGO_SVG = `<svg viewBox="0 0 24 24" xmlns="http://www.w3.org/2000/svg"><path d="M12 2L22 12L12 22L2 12Z"/></svg>`;

// ─── 事件监听清理注册表 ────────────────────────────────────────
const _eventCleanups = [];

export function registerCleanup(element, event, handler) {
    element.addEventListener(event, handler);
    _eventCleanups.push({ element, event, handler });
}

export function cleanupAllEvents() {
    _eventCleanups.forEach(({ element, event, handler }) => {
        element.removeEventListener(event, handler);
    });
    _eventCleanups.length = 0;
}

// ─── 面板定时器追踪 ──────────────────────────────────────────
const _panelTimers = new Set();

export function trackPanelTimer(id) {
    _panelTimers.add(id);
    return id;
}

export function clearPanelTimers() {
    _panelTimers.forEach(id => clearTimeout(id));
    _panelTimers.clear();
}

export function removePanelTimer(id) {
    _panelTimers.delete(id);
}

// ─── DOM 工厂函数 ─────────────────────────────────────────────
export function el(tag, attrs = {}, children = []) {
    const node = document.createElement(tag);
    // 自动禁用输入框历史记录（autocomplete）
    if (tag === "input" || tag === "textarea" || tag === "form") {
        node.setAttribute("autocomplete", "off");
    }
    for (const [k, v] of Object.entries(attrs)) {
        if (k === "class") node.className = v;
        else if (k === "style" && typeof v === "object") Object.assign(node.style, v);
        else if (k.startsWith("on") && k.length > 2) node.addEventListener(k.slice(2).toLowerCase(), v);
        else if (k === "html") node.innerHTML = v;
        else if (k === "text") node.textContent = v;
        else node.setAttribute(k, v);
    }
    for (const child of Array.isArray(children) ? children : [children]) {
        if (!child) continue;
        node.appendChild(typeof child === "string" ? document.createTextNode(child) : child);
    }
    return node;
}

// ─── Markdown 渲染 ───────────────────────────────────────────
// HTML 转义工具
export function _转义HTML(s) {
    return String(s).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
        .replace(/"/g, "&quot;").replace(/'/g, "&#39;");
}

/**
 * 安全纯文本渲染：DOMPurify 未加载完成时使用
 * 转义所有 HTML 标签，仅保留纯文本 + 换行
 */
function _安全纯文本渲染(text) {
    if (!text) return "";
    return _转义HTML(text).replace(/\n/g, "<br>");
}

// ─── Diff 代码渲染辅助 ─────────────────────────────────────

/**
 * 渲染 diff 代码块（用于 raw 未转义内容，如 marked.js 输出）
 * 逐行检测前缀并包裹对应 CSS 类 span
 */
function _渲染Diff代码(code) {
    if (!code) return '';
    const lines = code.split('\n');
    return lines.map(line => {
        const escaped = _转义HTML(line);
        if (line.startsWith('+++') || line.startsWith('---')) {
            return `<span class="nca-diff-meta">${escaped}</span>`;
        } else if (line.startsWith('@@')) {
            return `<span class="nca-diff-hunk">${escaped}</span>`;
        } else if (line.startsWith('+')) {
            return `<span class="nca-diff-add">${escaped}</span>`;
        } else if (line.startsWith('-')) {
            return `<span class="nca-diff-del">${escaped}</span>`;
        } else {
            return `<span class="nca-diff-ctx">${escaped}</span>`;
        }
    }).join('\n');
}

/**
 * 包装已转义的 diff 行（用于 _正则Markdown渲染，内容已被整体转义）
 * 仅添加 span 标签，不做额外转义
 */
function _包装Diff行(code) {
    if (!code) return '';
    const lines = code.split('\n');
    return lines.map(line => {
        if (line.startsWith('+++') || line.startsWith('---')) {
            return `<span class="nca-diff-meta">${line}</span>`;
        } else if (line.startsWith('@@')) {
            return `<span class="nca-diff-hunk">${line}</span>`;
        } else if (line.startsWith('+')) {
            return `<span class="nca-diff-add">${line}</span>`;
        } else if (line.startsWith('-')) {
            return `<span class="nca-diff-del">${line}</span>`;
        } else {
            return `<span class="nca-diff-ctx">${line}</span>`;
        }
    }).join('\n');
}

// 原有正则渲染——作为 marked.js 加载前/失败时的降级方案
function _正则Markdown渲染(text) {
    if (!text) return "";
    let html = text
        .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");

    // 代码块 — 带语言标签和复制按钮
    html = html.replace(/```(\w*)\n?([\s\S]*?)```/g, (_, lang, code) => {
        const langLabel = lang ? `<span class="nca-code-lang">${lang}</span>` : "";
        // diff 语言特殊渲染：逐行着色（code 已被转义，仅需包装 span）
        if (lang === 'diff' || lang === 'patch') {
            return `<pre>${langLabel}<code class="lang-diff nca-diff-block">${_包装Diff行(code.trim())}</code><button class="nca-code-copy" title="复制">复制</button></pre>`;
        }
        return `<pre>${langLabel}<code class="language-${lang}">${code.trim()}</code><button class="nca-code-copy" title="复制">复制</button></pre>`;
    });

    // 行内代码
    html = html.replace(/`([^`]+)`/g, "<code>$1</code>");
    // 粗体
    html = html.replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>");
    // 斜体
    html = html.replace(/\*(.+?)\*/g, "<em>$1</em>");
    // 链接
    html = html.replace(/\[([^\]]+)\]\(([^)]+)\)/g, '<a href="$2" target="_blank">$1</a>');
    // 段落
    html = html.replace(/\n\n/g, "</p><p>");
    // 单换行
    html = html.replace(/\n/g, "<br>");
    html = `<p>${html}</p>`;

    return html;
}

// ─── marked.js 高性能渲染 ────────────────────────────────────
let _markedLib = null;
let _markedLoading = null;
let _markedRenderer = null;

// ─── Prism.js 语法高亮 ──────────────────────────────────────
let _prismLoaded = null; // null=未加载, Promise=加载中

/**
 * 通用脚本加载器（支持自定义属性，如 data-manual）
 * 返回 Promise，加载成功 resolve，失败 reject 并移除 script 标签
 */
function _加载脚本(url, attrs = {}) {
    return new Promise((resolve, reject) => {
        const script = document.createElement('script');
        script.src = url;
        script.async = true;
        for (const [k, v] of Object.entries(attrs)) script.setAttribute(k, v);
        script.onload = () => resolve();
        script.onerror = () => { script.remove(); reject(new Error('加载失败: ' + url)); };
        document.head.appendChild(script);
    });
}

// ─── DOMPurify XSS 消毒 ──────────────────────────────────────
// 三级降级防线：DOMPurify(CDN) → _本地消毒(DOMParser 白名单) → _转义HTML(全转义)
let _domPurifyLib = null;
let _domPurifyLoading = null;
let _cdnDegradedNotified = false;

// 本地消毒白名单——仅放行 Markdown 渲染所需安全标签/属性
const _本地允许标签 = new Set([
    'p','br','strong','em','b','i','u','s','del','code','pre',
    'ul','ol','li','a','h1','h2','h3','h4','h5','h6','blockquote',
    'table','thead','tbody','tr','td','th','span','div','img','hr'
]);
const _本地允许属性 = new Set(['href','src','alt','title','class','id','target','rel']);

// 在生成/处理链接时，验证 href 协议白名单
function _验证链接安全(href) {
    if (!href) return '#';
    const trimmed = href.trim().toLowerCase();
    // 只允许 http、https 和相对路径
    if (trimmed.startsWith('http://') || trimmed.startsWith('https://') ||
        trimmed.startsWith('/') || trimmed.startsWith('./') || trimmed.startsWith('../') ||
        trimmed.startsWith('#')) {
        return href;
    }
    // 危险协议 (javascript:, data:, vbscript: 等)
    return '#';
}

/**
 * 本地降级消毒：DOMPurify CDN 不可用时使用
 * 依赖浏览器原生 DOMParser + TreeWalker，无外部依赖
 * 策略：白名单标签/属性 + 协议过滤 + 移除 on* 事件处理器
 */
function _本地消毒(html) {
    if (typeof DOMParser === 'undefined' || typeof NodeFilter === 'undefined') {
        return _转义HTML(html); // 三级兜底：极端环境无 DOM API
    }
    try {
        const doc = new DOMParser().parseFromString(`<div>${html}</div>`, 'text/html');
        const root = doc.body.firstChild;
        if (!root) return '';
        // 先收集所有元素节点，再统一处理（避免修改时影响遍历）
        const walker = doc.createTreeWalker(root, NodeFilter.SHOW_ELEMENT);
        const nodes = [];
        for (let n = walker.nextNode(); n; n = walker.nextNode()) nodes.push(n);
        for (const node of nodes) {
            if (!node.parentNode) continue; // 父节点已被移除
            const tag = node.tagName.toLowerCase();
            if (!_本地允许标签.has(tag)) {
                // script/iframe/object/embed/form/input 等危险标签直接删除
                node.parentNode.removeChild(node);
                continue;
            }
            for (const attr of Array.from(node.attributes)) {
                const name = attr.name.toLowerCase();
                const value = (attr.value || '').trim();
                if (name.startsWith('on') || !_本地允许属性.has(name)) {
                    node.removeAttribute(attr.name); // 事件处理器与未授权属性
                } else if (name === 'href') {
                    node.setAttribute(attr.name, _验证链接安全(value)); // 阻断 javascript:/data: 等协议
                } else if (name === 'src' && !/^https?:/i.test(value)) {
                    node.removeAttribute(attr.name);
                }
            }
        }
        return root.innerHTML;
    } catch (e) {
        return _转义HTML(html);
    }
}

// DOMPurify 白名单配置——仅允许 Markdown 渲染必需的标签与属性
const _purifyConfig = {
    ALLOWED_TAGS: ['h1','h2','h3','h4','h5','h6','p','br','hr',
                   'strong','em','b','i','u','s','del',
                   'ul','ol','li',
                   'a','img',
                   'code','pre','span',
                   'blockquote',
                   'table','thead','tbody','tr','th','td',
                   'div','details','summary'],
    ALLOWED_ATTR: ['href','target','rel','src','alt','class','title','open'],
    ALLOW_DATA_ATTR: false,
    ADD_ATTR: ['target'],
};

/**
 * 异步加载 DOMPurify（CDN 延迟加载模式）
 * 加载完成后 简易Markdown渲染() 自动启用消毒
 */
export function 加载DOMPurify库() {
    if (_domPurifyLib) return Promise.resolve(_domPurifyLib);
    if (window.DOMPurify) {
        _domPurifyLib = window.DOMPurify;
        return Promise.resolve(_domPurifyLib);
    }
    if (_domPurifyLoading) return _domPurifyLoading;

    // 本地优先，CDN 回退；import.meta.url 可正确解析 ComfyUI 模块路径
    const _purifyLocalPath = new URL('./lib/purify.min.js', import.meta.url).href;
    const _purifyCdnPath = 'https://cdn.jsdelivr.net/npm/dompurify@3.2.4/dist/purify.min.js';

    _domPurifyLoading = new Promise((resolve, reject) => {
        const onLoad = () => {
            _domPurifyLib = window.DOMPurify;
            // 通知消息渲染器：DOMPurify 已就绪，可安全重新渲染
            window.dispatchEvent(new CustomEvent('nca-dompurify-ready'));
            resolve(_domPurifyLib);
        };
        const onError = (e) => {
            _domPurifyLoading = null;
            console.warn('[节点梦工厂] DOMPurify 加载失败，降级使用本地消毒（DOMParser 白名单）');
            // 通知消息渲染器：DOMPurify 加载失败，用 _本地消毒 重渲染待处理消息
            window.dispatchEvent(new CustomEvent('nca-dompurify-failed'));
            reject(e);
        };
        const script = document.createElement('script');
        script.src = _purifyLocalPath;
        script.async = true;
        script.onload = onLoad;
        script.onerror = () => {
            // 本地加载失败，回退到 CDN
            console.warn('[节点梦工厂] DOMPurify 本地加载失败，回退到 CDN');
            script.remove();
            const cdnScript = document.createElement('script');
            cdnScript.src = _purifyCdnPath;
            cdnScript.async = true;
            cdnScript.onload = onLoad;
            cdnScript.onerror = onError;
            document.head.appendChild(cdnScript);
        };
        document.head.appendChild(script);
    });
    return _domPurifyLoading;
}

/**
 * 异步加载 marked.js（CDN 延迟加载模式）
 * 加载完成后后续 Markdown 调用自动切换到高性能渲染
 */
export function 加载Marked库() {
    if (_markedLib) return Promise.resolve(_markedLib);
    if (window.marked) {
        _markedLib = window.marked;
        _配置Marked();
        return Promise.resolve(_markedLib);
    }
    if (_markedLoading) return _markedLoading;

    // 本地优先，CDN 回退；import.meta.url 可正确解析 ComfyUI 模块路径
    const _markedLocalPath = new URL('./lib/marked.min.js', import.meta.url).href;
    const _markedCdnPath = 'https://cdn.jsdelivr.net/npm/marked@12.0.2/marked.min.js';

    _markedLoading = new Promise((resolve, reject) => {
        const onLoad = () => {
            _markedLib = window.marked;
            try { _配置Marked(); } catch (e) { console.warn('[节点梦工厂] marked 配置失败:', e); }
            resolve(_markedLib);
        };
        const onError = (e) => {
            _markedLoading = null;
            console.warn('[节点梦工厂] marked.js 加载失败，降级使用正则渲染');
            reject(e);
        };
        const script = document.createElement('script');
        script.src = _markedLocalPath;
        script.async = true;
        script.onload = onLoad;
        script.onerror = () => {
            // 本地加载失败，回退到 CDN
            console.warn('[节点梦工厂] marked.js 本地加载失败，回退到 CDN');
            script.remove();
            const cdnScript = document.createElement('script');
            cdnScript.src = _markedCdnPath;
            cdnScript.async = true;
            cdnScript.onload = onLoad;
            cdnScript.onerror = onError;
            document.head.appendChild(cdnScript);
        };
        document.head.appendChild(script);
    });
    return _markedLoading;
}

function _配置Marked() {
    if (!_markedLib) return;
    // 自定义 renderer——保持代码块语言标签和复制按钮 UI 一致
    _markedRenderer = new _markedLib.Renderer();
    _markedRenderer.code = function(code, language) {
        // marked@12 部分版本会传入对象
        if (typeof code === 'object' && code !== null) {
            language = code.lang || language;
            code = code.text || '';
        }
        const lang = (language || '').toString().trim();
        const langLabel = lang ? `<span class="nca-code-lang">${_转义HTML(lang)}</span>` : '';
        // diff 语言特殊渲染：逐行着色
        if (lang === 'diff' || lang === 'patch') {
            return `<pre>${langLabel}<code class="lang-diff nca-diff-block">${_渲染Diff代码(code)}</code><button class="nca-code-copy" title="复制">复制</button></pre>`;
        }
        return `<pre>${langLabel}<code class="language-${_转义HTML(lang)}">${_转义HTML(code)}</code><button class="nca-code-copy" title="复制">复制</button></pre>`;
    };
    // XSS 安全：marked 默认透传原始 HTML，强制转义
    _markedRenderer.html = function(html) {
        if (typeof html === 'object' && html !== null) html = html.text || html.raw || '';
        return _转义HTML(html);
    };
    // 链接默认新窗口打开
    _markedRenderer.link = function(href, title, text) {
        if (typeof href === 'object' && href !== null) {
            title = href.title;
            text = href.text;
            href = href.href;
        }
        const t = title ? ` title="${_转义HTML(title)}"` : '';
        return `<a href="${_转义HTML(href || '')}" target="_blank" rel="noopener noreferrer"${t}>${text}</a>`;
    };

    _markedLib.setOptions({
        breaks: true,       // 换行符转 <br>
        gfm: true,          // GitHub Flavored Markdown（含表格）
        headerIds: false,   // 不添加 header ID
        mangle: false,      // 不转义邮箱
        renderer: _markedRenderer,
    });
}

/**
 * 主 Markdown 渲染入口
 * - 已加载 marked.js → 高性能渲染（GFM 表格等）
 * - 未加载 → 降级到正则渲染（保持首屏可用）
 */
export function 简易Markdown渲染(text) {
    if (!text) return "";
    // DOMPurify 正在异步加载中：使用纯文本渲染（转义所有 HTML）避免 XSS 风险
    // 加载完成后派发 nca-dompurify-ready 事件，消息渲染器监听后重新渲染
    // 加载失败时 _domPurifyLoading 被置 null，降级到 markdown + _本地消毒
    if (!_domPurifyLib && _domPurifyLoading) {
        return _安全纯文本渲染(text);
    }
    let result;
    if (_markedLib) {
        try {
            result = _markedLib.parse(text, { renderer: _markedRenderer });
        } catch (e) {
            console.warn('[节点梦工厂] marked 渲染失败，降级:', e);
            result = _正则Markdown渲染(text);
        }
    } else {
        result = _正则Markdown渲染(text);
    }
    // DOMPurify 已加载完成时直接消毒；否则降级到 _本地消毒
    if (_domPurifyLib) {
        try {
            return _domPurifyLib.sanitize(result, _purifyConfig);
        } catch (e) {
            console.warn('[节点梦工厂] DOMPurify 消毒失败，降级本地消毒:', e);
        }
    }
    return _本地消毒(result);
}

// ─── Prism.js 语法高亮加载与应用 ────────────────────────────
/**
 * 异步加载 Prism.js（本地优先，CDN 回退）
 * 使用 autoloader 插件按需加载语言组件，无需手动管理语言包
 * 加载失败时降级为纯文本渲染，不影响功能
 */
export function 加载Prism库() {
    if (typeof Prism !== 'undefined') return Promise.resolve(true);
    if (_prismLoaded) return _prismLoaded;

    const _prismLocalPath = new URL('./lib/prism.min.js', import.meta.url).href;
    const _prismAutoloaderLocalPath = new URL('./lib/prism-autoloader.min.js', import.meta.url).href;
    const _prismCssLocalPath = new URL('./lib/prism-theme.css', import.meta.url).href;
    const _prismCdnPath = 'https://cdnjs.cloudflare.com/ajax/libs/prism/1.29.0/prism.min.js';
    const _prismAutoloaderCdnPath = 'https://cdnjs.cloudflare.com/ajax/libs/prism/1.29.0/plugins/autoloader/prism-autoloader.min.js';
    const _prismComponentsCdn = 'https://cdnjs.cloudflare.com/ajax/libs/prism/1.29.0/components/';

    // 加载 Prism CSS 主题（仅 token 着色，容器样式由项目 CSS 控制）
    const link = document.createElement('link');
    link.rel = 'stylesheet';
    link.href = _prismCssLocalPath;
    document.head.appendChild(link);

    _prismLoaded = (async () => {
        // 本地优先，CDN 回退；data-manual 禁用 Prism 自动高亮（由 应用Prism高亮 手动控制）
        for (const [corePath, autoloaderPath] of [
            [_prismLocalPath, _prismAutoloaderLocalPath],
            [_prismCdnPath, _prismAutoloaderCdnPath],
        ]) {
            try {
                await _加载脚本(corePath, { 'data-manual': '' });
                if (typeof Prism !== 'undefined') Prism.manual = true;
                await _加载脚本(autoloaderPath);
                if (typeof Prism !== 'undefined') {
                    // 配置 autoloader 语言组件 CDN 路径（本地加载时需要）
                    if (Prism.plugins && Prism.plugins.autoloader) {
                        Prism.plugins.autoloader.languages_path = _prismComponentsCdn;
                    }
                    return true;
                }
            } catch (_) { /* 尝试下一个源 */ }
        }
        _prismLoaded = null;
        console.warn('[节点梦工厂] Prism.js 加载失败，代码块保持纯文本渲染');
        return false;
    })();
    return _prismLoaded;
}

/**
 * 对容器内所有带 language- class 的代码块应用 Prism 高亮
 * 幂等：通过 data-prism-highlighted 属性防止重复高亮
 * 异步：Prism 未加载时先触发加载，加载完成后自动高亮
 */
export function 应用Prism高亮(container) {
    if (!container) return;
    const codes = container.querySelectorAll('pre code[class*="language-"]:not([data-prism-highlighted])');
    if (codes.length === 0) return;
    加载Prism库().then(loaded => {
        if (!loaded || typeof Prism === 'undefined') return;
        codes.forEach(el => {
            if (el.hasAttribute('data-prism-highlighted')) return;
            el.setAttribute('data-prism-highlighted', '1');
            try { Prism.highlightElement(el); } catch (_) {}
        });
    });
}

// 模块加载即触发 marked.js、DOMPurify 与 Prism 异步预加载（不阻塞）
if (typeof window !== 'undefined') {
    加载Marked库().catch(() => { /* 失败时静默降级 */ });
    加载DOMPurify库().catch(() => {
        // 失败时由 _本地消毒 / _转义HTML 兜底，并提示用户一次
        if (!_cdnDegradedNotified) {
            _cdnDegradedNotified = true;
            try { Toast.info("部分资源使用离线模式加载"); } catch (_) {}
        }
    });
    加载Prism库().catch(() => { /* 失败时静默降级为纯文本 */ });
}

// ─── 全局 Toast 通知系统 ─────────────────────────────────────
// 类型：success / warning / error / info
// 多 Toast 自动堆叠；error 默认 5s，其它 3s
export const Toast = {
    _container: null,

    _getContainer() {
        if (!this._container || !this._container.parentNode) {
            this._container = document.createElement('div');
            this._container.className = 'nca-toast-container';
            // 优先挂载到侧边栏根容器内部
            const sidebarRoot = document.querySelector('.nca-sidebar-root');
            if (sidebarRoot) {
                sidebarRoot.appendChild(this._container);
            } else {
                document.body.appendChild(this._container);
            }
        }
        return this._container;
    },

    show(message, type = 'info', duration) {
        if (duration === undefined) duration = type === 'error' ? 5000 : 3000;
        const container = this._getContainer();
        const icons = { success: '✓', warning: '⚠', error: '✗', info: 'ℹ' };

        const toast = document.createElement('div');
        toast.className = `nca-toast nca-toast-${type}`;

        const iconEl = document.createElement('span');
        iconEl.className = 'nca-toast-icon';
        iconEl.textContent = icons[type] || icons.info;

        const msgEl = document.createElement('span');
        msgEl.className = 'nca-toast-msg';
        msgEl.textContent = String(message ?? '');

        const closeBtn = document.createElement('button');
        closeBtn.className = 'nca-toast-close';
        closeBtn.type = 'button';
        closeBtn.setAttribute('aria-label', '关闭');
        closeBtn.textContent = '×';
        closeBtn.onclick = () => this._dismiss(toast);

        toast.appendChild(iconEl);
        toast.appendChild(msgEl);
        toast.appendChild(closeBtn);
        container.appendChild(toast);

        // 入场动画
        requestAnimationFrame(() => toast.classList.add('nca-toast-show'));

        // 自动消失
        if (duration > 0) {
            const tid = setTimeout(() => this._dismiss(toast), duration);
            toast._ncaTimer = tid;
        }
        return toast;
    },

    _dismiss(toast) {
        if (!toast || toast._ncaDismissed) return;
        toast._ncaDismissed = true;
        if (toast._ncaTimer) clearTimeout(toast._ncaTimer);
        toast.classList.add('nca-toast-hide');
        setTimeout(() => toast.remove(), 300);
    },

    success(msg, duration) { return this.show(msg, 'success', duration); },
    warning(msg, duration) { return this.show(msg, 'warning', duration); },
    error(msg, duration)   { return this.show(msg, 'error', duration); },
    info(msg, duration)    { return this.show(msg, 'info', duration); },
};

// ─── 通用提示条（向后兼容入口，统一路由到 Toast.info） ─────────
// container 参数已弃用，保留仅为向后兼容；新代码请直接使用 Toast.<type>(...)
/** @deprecated 使用 Toast 替代 */
export function 显示提示(_container, text, type = 'info') {
    return Toast.show(text, type);
}

// ─── 模型视觉能力检测 ─────────────────────────────────────────
const _visionCapabilityCache = {};
let _visionWarningDismissed = false;

export async function 检查模型视觉能力() {
    try {
        const resp = await fetch(`${NCA_API_BASE}/model-capabilities`);
        if (!resp.ok) return { supports_vision: false, model_source: '', model_name: '' };
        const json = await resp.json();
        const data = json.data || json;
        // 缓存结果
        const key = `${data.model_source || ''}:${data.model_name || ''}`;
        _visionCapabilityCache[key] = data.supports_vision;
        return data;
    } catch (e) {
        return { supports_vision: false, model_source: '', model_name: '' };
    }
}

/**
 * 在 inputArea（.nca-input-area）上方显示视觉不支持警告横幅
 * @param {HTMLElement} inputArea - 输入区域的 DOM 元素
 * @returns {Function} 移除横幅的函数
 */
export async function 显示视觉能力警告(inputArea) {
    if (_visionWarningDismissed) return null;
    // 已有警告则不重复
    const existing = inputArea.parentElement?.querySelector('.nca-vision-warning');
    if (existing) return null;

    const data = await 检查模型视觉能力();
    if (data.supports_vision) return null;
    if (_visionWarningDismissed) return null;

    const banner = el("div", { class: "nca-vision-warning" }, [
        el("span", { text: "⚠ 当前模型不支持图片分析，图片将仅作为文件名传递" }),
        el("span", { class: "nca-warning-close", text: "×" }),
    ]);
    banner.querySelector('.nca-warning-close').addEventListener('click', () => {
        _visionWarningDismissed = true;
        banner.remove();
    });
    // 插入到 inputArea 之前
    inputArea.parentElement.insertBefore(banner, inputArea);
    return () => banner.remove();
}

export function 移除视觉能力警告(inputArea) {
    const banner = inputArea.parentElement?.querySelector('.nca-vision-warning');
    if (banner) banner.remove();
}

function 重置视觉警告状态() {
    _visionWarningDismissed = false;
}

// ─── 全局禁用输入框历史记录 ────────────────────────────────────
// 捕获所有动态创建的 input/textarea（模板字符串、innerHTML 等）
(function _禁用全局自动填充() {
    const _禁用 = (root) => {
        if (!root || !root.querySelectorAll) return;
        for (const el of root.querySelectorAll('input, textarea')) {
            if (el.getAttribute('autocomplete') !== 'off') {
                el.setAttribute('autocomplete', 'off');
            }
        }
    };
    // 处理已有元素
    _禁用(document);
    // 监听后续动态添加的元素
    const observer = new MutationObserver((mutations) => {
        for (const m of mutations) {
            for (const node of m.addedNodes) {
                if (node.nodeType === Node.ELEMENT_NODE) {
                    if (node.tagName === 'INPUT' || node.tagName === 'TEXTAREA') {
                        node.setAttribute('autocomplete', 'off');
                    }
                    _禁用(node);
                }
            }
        }
    });
    observer.observe(document.body || document.documentElement, { childList: true, subtree: true });
})();
