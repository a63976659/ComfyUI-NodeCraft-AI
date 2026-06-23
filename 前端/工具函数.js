// ═══════════════════════════════════════════════════════════════
// 工具函数.js — 公共工具、常量定义、事件清理注册表
// NodeCraft AI — Luxury Terminal / Neo-Noir Hacker
// ═══════════════════════════════════════════════════════════════

// ─── 常量定义 ─────────────────────────────────────────────────
export const NCA_COLORS = {
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

// ─── 主题切换（深色/浅色）──────────────────────────────
// 默认 dark（保持现有用户体验）；light 仅多一个可选项。
// 主题类施加在本插件自有根 .nca-sidebar-root 上，避免污染 ComfyUI 共享侧边栏容器。
// CSS 变量沿 DOM 继承覆盖 :root 默认值，无需重新加载。
export function 获取主题() {
    const v = localStorage.getItem(NCA_STORAGE_KEYS.theme);
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
    localStorage.setItem(NCA_STORAGE_KEYS.theme, next);
    return next;
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
function _转义HTML(s) {
    return String(s).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
        .replace(/"/g, "&quot;").replace(/'/g, "&#39;");
}

// 原有正则渲染——作为 marked.js 加载前/失败时的降级方案
function _正则Markdown渲染(text) {
    if (!text) return "";
    let html = text
        .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");

    // 代码块 — 带语言标签和复制按钮
    html = html.replace(/```(\w*)\n?([\s\S]*?)```/g, (_, lang, code) => {
        const langLabel = lang ? `<span class="nca-code-lang">${lang}</span>` : "";
        return `<pre>${langLabel}<code class="lang-${lang}">${code.trim()}</code><button class="nca-code-copy" title="复制">复制</button></pre>`;
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

    _domPurifyLoading = new Promise((resolve, reject) => {
        const script = document.createElement('script');
        script.src = 'https://cdn.jsdelivr.net/npm/dompurify@3/dist/purify.min.js';
        script.async = true;
        script.onload = () => {
            _domPurifyLib = window.DOMPurify;
            resolve(_domPurifyLib);
        };
        script.onerror = (e) => {
            _domPurifyLoading = null;
            console.warn('[节点梦工厂] DOMPurify 加载失败，降级使用本地消毒（DOMParser 白名单）');
            reject(e);
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

    _markedLoading = new Promise((resolve, reject) => {
        const script = document.createElement('script');
        script.src = 'https://cdn.jsdelivr.net/npm/marked@12/marked.min.js';
        script.async = true;
        script.onload = () => {
            _markedLib = window.marked;
            try { _配置Marked(); } catch (e) { console.warn('[节点梦工厂] marked 配置失败:', e); }
            resolve(_markedLib);
        };
        script.onerror = (e) => {
            _markedLoading = null;
            console.warn('[节点梦工厂] marked.js 加载失败，降级使用正则渲染');
            reject(e);
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
        return `<pre>${langLabel}<code class="lang-${_转义HTML(lang)}">${_转义HTML(code)}</code><button class="nca-code-copy" title="复制">复制</button></pre>`;
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
    // 消毒最终防线（三级降级）：
    //   1) DOMPurify（CDN 已加载）— 最严格、最完整
    //   2) _本地消毒（DOMParser 白名单）— 离线/CDN 失败时的精确过滤
    //   3) _转义HTML（_本地消毒 内部兜底）— 极端环境无 DOM API
    if (_domPurifyLib) {
        try {
            return _domPurifyLib.sanitize(result, _purifyConfig);
        } catch (e) {
            console.warn('[节点梦工厂] DOMPurify 消毒失败，降级本地消毒:', e);
        }
    }
    return _本地消毒(result);
}

// 模块加载即触发 marked.js 与 DOMPurify 异步预加载（不阻塞）
if (typeof window !== 'undefined') {
    加载Marked库().catch(() => { /* 失败时静默降级 */ });
    加载DOMPurify库().catch(() => {
        // 失败时由 _本地消毒 / _转义HTML 兜底，并提示用户一次
        if (!_cdnDegradedNotified) {
            _cdnDegradedNotified = true;
            try { Toast.info("部分资源使用离线模式加载"); } catch (_) {}
        }
    });
}

// ─── 全局 Toast 通知系统 ─────────────────────────────────────
// 类型：success / warning / error / info
// 多 Toast 自动堆叠；error 默认 5s，其它 3s
export const Toast = {
    _container: null,

    _getContainer() {
        if (!this._container || !document.body.contains(this._container)) {
            this._container = document.createElement('div');
            this._container.className = 'nca-toast-container';
            document.body.appendChild(this._container);
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

export function 重置视觉警告状态() {
    _visionWarningDismissed = false;
}
