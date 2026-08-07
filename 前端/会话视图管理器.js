// ═══════════════════════════════════════════════════════════════
// 会话视图管理器.js — 轻量协调器（入口 + 标签页路由）
// NodeCraft AI — Luxury Terminal / Neo-Noir Hacker
// ═══════════════════════════════════════════════════════════════

import {
    事件总线, 事件, 状态,
    获取会话列表,
    加载设置, 保存设置, 更新模型选择, 切换API配置, 设置插件文件夹,
    支持思考深度, 当前思考深度, 切换思考深度,
    获取当前模型名称, 获取会话序号,
    获取本地模型列表,
    请求,
} from "./交互与状态.js";
import {
    el, LOGO_SVG, NCA_STORAGE_KEYS, Toast,
    cleanupAllEvents, clearPanelTimers,
    获取主题, 应用主题, 切换主题, 标记禁止翻译, 注入关键样式,
} from "./工具函数.js";
import { 存储 } from "./存储引擎.js";
import {
    渲染欢迎页, 渲染所有消息, 追加消息DOM, 显示加载动画, 移除加载动画,
    渲染输入区域, 发送消息流式,
    创建流式恢复气泡, 是否流式中,
} from "./消息渲染器.js";
import { 显示设置面板, 显示创建项目对话框 } from "./设置面板.js";
import { 创建会话列表面板 } from "./会话列表面板.js";
import { t, 监听语言切换, 获取当前语言, 切换语言 } from "./i18n.js";

// 版本信标：浏览器端读 window.__NCA_VIEW_BUILD 即可确定执行的是否为最新模块
window.__NCA_VIEW_BUILD = "r10-20260807-1500";

// 语言切换监听器取消句柄（跨序于 renderSidebarUI 多次调用，需在重渲染前取消以避免重复注册）
let _unsubLang = null;

// 介绍页（欢迎页）可见状态：跨 renderSidebarUI 重建保持。
// 语言切换会整体重建侧边栏，若用户正停留在介绍页，重建后需恢复显示，
// 否则介绍页被静默关闭，表现为“欢迎页上切换语言不生效”
let _介绍页可见 = false;

// 可视化引擎“销毁图”函数的懒加载占位（首次切换离开可视化标签时按需导入）
let 销毁图 = null;

// 事件总线订阅追踪：renderSidebarUI 可能被多次调用（如语言切换重渲染），
// 事件总线订阅不在 cleanupAllEvents 注册表内，须单独追踪并在重渲染前统一退订，
// 避免闭包引用旧 refs/DOM 的监听器累积泄漏
const _总线订阅取消 = [];
function _追踪订阅(event, fn) {
    事件总线.on(event, fn);
    _总线订阅取消.push(() => 事件总线.off(event, fn));
}

// 上一次渲染创建的会话列表面板实例（重渲染前调用 destroy 退订其内部全局事件订阅）
let _上次会话面板 = null;


// ─── 登录界面 overlay（默认入口 + 星星按钮入口） ────────────────
async function 显示登录弹窗(rootContainer, opts) {
    const asEntry = opts && opts.asEntry === true;
    const skipIfLoggedIn = opts && opts.skipIfLoggedIn === true;
    // 若已存在登录 overlay，不重复创建
    const existed = rootContainer.querySelector(".nca-login-entry-overlay");
    if (existed) return;

    // 动态导入登录模块（提前导入以便检查登录状态）
    const { 创建登录面板, 获取登录状态, 自动登录 } = await import("./登录面板.js");

    // 入口模式且要求跳过已登录：检查登录状态并尝试自动登录
    // 已登录（内存中有 token+user）直接跳过；否则尝试从 RanKing 存储引导凭证并验证
    if (asEntry && skipIfLoggedIn) {
        if (获取登录状态().loggedIn) return;
    }

    // 与设置面板一致：全屏 overlay + nca-panel
    const overlay = el("div", { class: "nca-overlay nca-login-entry-overlay" });
    const panel = el("div", { class: "nca-panel" });

    // header：作为入口时不显示关闭按钮；星星按钮调出时显示
    const headerChildren = [el("h3", { text: t("account.title") })];
    if (!asEntry) {
        const closeBtn = el("button", { class: "nca-icon-btn", text: "✕" });
        closeBtn.addEventListener("click", () => overlay.remove());
        headerChildren.push(closeBtn);
    }
    panel.appendChild(el("div", { class: "nca-panel-header" }, headerChildren));

    // 全屏 panel-body 下增加居中包裹层，使登录内容在全屏中合理居中展示
    const body = el("div", { class: "nca-panel-body nca-login-body" });
    const centerWrap = el("div", { class: "nca-login-center-wrap" });

    // 当作为入口时，传入"进入"回调，供登录面板替换退出登录按钮为"进入 →"
    const onEnter = asEntry ? function() { overlay.remove(); } : null;
    body.appendChild(centerWrap);
    panel.appendChild(body);

    overlay.appendChild(panel);
    rootContainer.appendChild(overlay);

    创建登录面板(centerWrap, { onEnter: onEnter });
}

// ─── 主渲染函数 ────────────────────────────────
export function renderSidebarUI(container) {
    // 关键伪元素规则双保险：JS 注入 <style>，防 @import 链 CSS 未进 CSSOM 不渲染
    注入关键样式();
    注入样式资源();

    container.style.height = '100%';
    container.style.display = 'flex';
    container.style.flexDirection = 'column';
    container.style.position = 'relative';
    container.innerHTML = "";
    cleanupAllEvents();
    clearPanelTimers();
    // 退订上一次渲染注册的事件总线订阅与会话面板内部订阅（避免重复渲染累积）
    _总线订阅取消.splice(0).forEach(fn => { try { fn(); } catch (_) {} });
    if (_上次会话面板) {
        try { _上次会话面板.destroy(); } catch (_) {}
        _上次会话面板 = null;
    }
    if (typeof _unsubLang === 'function') {
        try { _unsubLang(); } catch (_) {}
        _unsubLang = null;
    }

    // 本插件自有根包裹层：避免主题类、CSS 变量、后代选择器泄漏到 ComfyUI 共享侧边栏容器。
    // 所有插件内容及覆盖层均挂在该根下；其被移除时静态样式随之失效。
    const sidebarRoot = el("div", { class: "nca-sidebar-root" });
    // 翻译豁免：防止外部翻译插件/浏览器翻译改写本插件 DOM 文本（UI 语言只受自身 i18n 控制）
    标记禁止翻译(sidebarRoot);
    sidebarRoot.style.flex = '1';
    sidebarRoot.style.display = 'flex';
    sidebarRoot.style.flexDirection = 'column';
    sidebarRoot.style.minHeight = '0';
    sidebarRoot.style.position = 'relative';
    sidebarRoot.style.overflow = 'hidden';
    container.appendChild(sidebarRoot);

    // 应用持久化主题（默认 dark）—— 主题类仅加在 sidebarRoot 上
    应用主题(sidebarRoot, 获取主题());

    // 引用存储
    const refs = {};
    let 根容器;

    // ─── 顶部品牌栏 ─────────────────────────────────────────
    sidebarRoot.appendChild(渲染顶部品牌栏());

    // ─── 标签栏 ─────────────────────────────────────────────
    // 按钮文字经 data-nca-label + CSS 伪元素渲染（.nc-tab::before），外部翻译插件
    // 只改写文本节点/innerText/title，碰不到 CSS content，从机制上免疫改写
    const tabBar = el("div", { class: "nc-tab-bar" }, [
        el("button", { class: "nc-tab active", "data-tab": "develop", "data-nca-label": t("tab.develop"), onClick: () => switchTab("develop") }),
        el("button", { class: "nc-tab", "data-tab": "optimize", "data-nca-label": t("tab.optimize"), onClick: () => switchTab("optimize") }),
        el("button", { class: "nc-tab", "data-tab": "visualize", "data-nca-label": t("tab.visualize"), onClick: () => switchTab("visualize") }),
    ]);
    sidebarRoot.appendChild(tabBar);

    // ─── 面板容器 ────────────────────────────────────────────
    const panelDevelop = el("div", { class: "nc-panel", id: "panel-develop", style: { display: "flex", flexDirection: "column", flex: "1", overflow: "hidden" } });
    const panelOptimize = el("div", { class: "nc-panel", id: "panel-optimize", style: { display: "none", flexDirection: "column", flex: "1", overflow: "hidden" } });
    const panelVisualize = el("div", { class: "nc-panel", id: "panel-visualize", style: { display: "none", flexDirection: "column", flex: "1", overflow: "hidden" } });
    sidebarRoot.appendChild(panelDevelop);
    sidebarRoot.appendChild(panelOptimize);
    sidebarRoot.appendChild(panelVisualize);

    // ─── 构建"优化插件"面板（懒加载，首次切换时初始化）──────────────────
    const panelCtx = { 显示设置面板Fn: () => 显示设置面板(覆盖层容器, 更新状态栏) };
    let vizGraphInstance = null;
    let 优化面板已构建 = false;
    let 可视化面板已构建 = false;

    // ─── 标签切换 ────────────────────────────────────────────
    async function switchTab(tabId) {
        clearPanelTimers();
        if (vizGraphInstance && tabId !== 'visualize') {
            await 安全销毁图(vizGraphInstance);
            vizGraphInstance = null;
        }
        if (tabId === 'optimize' && !优化面板已构建) {
            const { 构建优化面板 } = await import("./优化面板.js");
            构建优化面板(panelOptimize, panelCtx);
            优化面板已构建 = true;
        }
        if (tabId === 'visualize' && !可视化面板已构建) {
            const { 构建可视化面板 } = await import("./可视化面板.js");
            构建可视化面板(panelVisualize, () => vizGraphInstance, (v) => { vizGraphInstance = v; }, panelCtx);
            可视化面板已构建 = true;
        }
        tabBar.querySelectorAll('.nc-tab').forEach(btn => {
            btn.classList.toggle('active', btn.dataset.tab === tabId);
        });
        [panelDevelop, panelOptimize, panelVisualize].forEach(panel => {
            panel.style.display = panel.id === `panel-${tabId}` ? 'flex' : 'none';
        });
        // 切回可视化标签时，若图形已被销毁则从缓存数据恢复
        // 等一帧让浏览器完成 display:none→flex 的布局重排，避免容器宽度为 0 导致画布不可见
        if (tabId === 'visualize' && 可视化面板已构建 && !vizGraphInstance) {
            const { 恢复可视化图形 } = await import("./可视化面板.js");
            // 等待两帧确保容器布局重排完成，避免宽高为 0 导致画布初始化失败
            await new Promise(r => requestAnimationFrame(() => requestAnimationFrame(r)));
            await 恢复可视化图形();
        }
        // 高频写入：异步落盘到 IndexedDB，不阻塞主线程；存储引擎同步刷新影子缓存以供同步读取
        存储.写入(NCA_STORAGE_KEYS.activeTab, tabId).catch(() => {});
    }

    async function 安全销毁图(instance) {
        if (!instance) return;
        if (typeof 销毁图 !== 'function') {
            try {
                const mod = await import("./可视化引擎.js");
                销毁图 = mod.销毁图;
            } catch (_) {}
        }
        if (typeof 销毁图 === 'function') {
            try { 销毁图(instance); } catch (_) {}
        }
    }

    // ─── 构建"开发插件"面板 ──────────────────────────────────
    // 覆盖层锚点：使用 sidebarRoot，让 overlay 在任意标签页均可显示且不泄漏到宿主容器
    const 覆盖层容器 = sidebarRoot;
    根容器 = el("div", { class: "nca-container" });
    refs.根容器 = 根容器;
    panelDevelop.appendChild(根容器);

    refs.主内容区 = el("div", { class: "nca-main-content" });
    // 复用公共会话列表组件（开发面板专属配置：默认折叠、无搜索框、含取消按钮、当前会话标签、自定义新建菜单、接入全局会话状态）
    refs.会话面板 = 创建会话列表面板({
        type: "develop",
        container: refs.主内容区,
        useGlobalState: true,
        defaultCollapsed: true,
        showSearch: false,
        showCancelButton: true,
        showCurrentSessionLabel: true,
        onCancelClick: () => 恢复欢迎页(),
        onNewClick: (newBtn) => 显示新建菜单(newBtn),
        onPackageClick: async (session) => {
            // 与旧逻辑保持一致：临时写入 localStorage 后唤起打包对话框
            try { localStorage.setItem(NCA_STORAGE_KEYS.plugin, session.plugin_folder); } catch (_) {}
            const { 显示打包对话框 } = await import("./插件打包对话框.js");
            显示打包对话框(覆盖层容器);
        },
    });
    _上次会话面板 = refs.会话面板;
    refs.消息区域 = el("div", { class: "nca-messages" });
    refs.主内容区.appendChild(refs.消息区域);
    // 若流式输出进行中则恢复显示，否则渲染欢迎页
    if (是否流式中()) {
        创建流式恢复气泡(refs.消息区域, refs.消息区域);
    } else {
        渲染欢迎页(refs.消息区域, refs);
    }
    refs.主内容区.appendChild(渲染模型切换栏());
    refs.主内容区.appendChild(渲染输入区域(refs, 根容器));
    根容器.appendChild(refs.主内容区);

    // 为消息渲染器提供对话框入口（自定义创建仍由侧边栏“+”菜单直接调用，无需挂在 refs 上）
    refs.显示模板市场对话框 = async () => {
        const { 显示模板市场对话框 } = await import("./模板市场对话框.js");
        显示模板市场对话框(覆盖层容器);
    };

    // 模型状态栏 + 底部版本信息
    sidebarRoot.appendChild(渲染状态栏());
    sidebarRoot.appendChild(渲染底部版本栏());

    // 工具介绍页（位于 sidebarRoot 顶层，全屏覆盖 Tab 栏与所有面板）
    refs.介绍页 = 渲染工具介绍页();
    sidebarRoot.appendChild(refs.介绍页);
    // 重建前若介绍页处于打开状态（如在介绍页上切换语言），恢复其可见性
    if (_介绍页可见) refs.介绍页.classList.add("visible");

    // 恢复标签页（仅恢复历史选中状态，介绍页关闭后呈现对应工作区）
    const savedTab = localStorage.getItem(NCA_STORAGE_KEYS.activeTab) || 'develop';
    switchTab(savedTab);

    // 默认显示登录界面作为入口；已登录或可自动登录（RanKing 凭证有效）时跳过
    显示登录弹窗(sidebarRoot, { asEntry: true, skipIfLoggedIn: true });

    绑定全局事件();

    // ── 流式恢复：若刷新前有进行中的流式输出，恢复 UI 状态 ──
    if (是否流式中()) {
        // 禁用输入框和发送按钮
        if (refs.输入框) refs.输入框.disabled = true;
        if (refs.发送按钮) refs.发送按钮.disabled = true;
        // 显示停止按钮
        const stopBtn = el("button", { class: "nca-stop-btn", text: "■ " + t("chat.stop") });
        stopBtn.addEventListener("click", () => {
            const ctrl = 状态.流式状态.中止控制器;
            if (ctrl) ctrl.abort();
        });
        refs.停止按钮容器 = stopBtn;
        // 同步到全局流式状态：流结束时 消息渲染器 据此移除新按钮并解禁新输入区
        状态.流式状态.停止按钮引用 = stopBtn;
        const inputArea = refs.输入框?.closest(".nca-input-area");
        if (inputArea) inputArea.insertBefore(stopBtn, inputArea.firstChild);
        // 广播状态
        事件总线.emit(事件.发送状态变更, true);
        事件总线.emit(事件.连接状态变更, "busy");
        更新状态栏(t("chat.generating"));
    }

    初始化();

    // 语言切换后整体重新渲染侧边栏，以刷新所有 t() 文本
    const 语言切换处理器 = () => {
        try { renderSidebarUI(container); } catch (_) {}
    };
    // 先移除旧监听器再绑定新监听器，避免 renderSidebarUI 多次调用时事件泄漏
    if (typeof _unsubLang === 'function') {
        try { _unsubLang(); } catch (_) {}
    }
    _unsubLang = 监听语言切换(语言切换处理器);

    // ═══════════════════════════════════════════════════════════
    // 以下为协调器内部函数
    // ═══════════════════════════════════════════════════════════

    function 注入样式资源() {
        if (!document.querySelector('link[data-nca-style]')) {
            const link = document.createElement("link");
            link.rel = "stylesheet";
            link.type = "text/css";
            link.dataset.ncaStyle = "true";
            link.href = new URL("./样式表.css", import.meta.url).href;
            document.head.appendChild(link);
        }
        if (!document.querySelector('link[data-nca-font]')) {
            const fontLink = document.createElement("link");
            fontLink.rel = "stylesheet";
            fontLink.dataset.ncaFont = "true";
            fontLink.href = "https://fonts.googleapis.com/css2?family=JetBrains+Mono:wght@400;500;600;700&display=swap";
            document.head.appendChild(fontLink);
        }
    }

    function 渲染顶部品牌栏() {
        // 语言切换按钮（低调小字按钮，紧贴品牌名右侧）
        // 按钮显示“目标语言”：中文界面显示 en、英文界面显示 中文，
        // 保证任何语言下用户都能找到唯一认识的文字（MDN/Wikipedia 同款模式）
        const langBtn = el("button", {
            class: "nca-lang-btn",
            title: "切换语言 / Toggle Language",
            text: 获取当前语言() === 'zh-CN' ? 'en' : '中文',
        });
        langBtn.addEventListener("click", (e) => {
            e.stopPropagation();
            const next = 切换语言();
            langBtn.textContent = next === 'zh-CN' ? 'en' : '中文';
        });

        const brand = el("div", { class: "nca-header-brand" }, [
            el("div", { class: "nca-logo", html: LOGO_SVG }),
            el("span", { class: "nca-brand-text", text: t("brand.name") }),
            langBtn,
        ]);
        brand.style.cursor = "pointer";
        brand.addEventListener("click", () => 切换介绍页(true));

        return el("div", { class: "nca-header" }, [
            brand,
            el("div", { class: "nca-header-actions" }, [
                (() => {
                    const 初始主题 = 获取主题();
                    const 太阳El = el("span", { class: "nca-celestial nca-sun", text: "☀" });
                    const 月亮El = el("span", { class: "nca-celestial nca-moon", text: "☾" });
                    const btn = el("button", {
                        class: "nca-icon-btn theme-btn",
                        title: 初始主题 === 'light' ? t("header.theme_to_dark") : t("header.theme_to_light"),
                    });
                    btn.appendChild(el("span", { class: "nca-theme-stage" }, [太阳El, 月亮El]));

                    // ── 日月交替动画（天体弧线交换参考 Jhey Tompkins 的 sun/moon toggle 模式）──
                    // 进度 p∈[0,1]：0=静止（当前主题天体居中）；0.5=悬停预览（两天体各占一半）；
                    // 1=完整交替（此时才真正切换主题）。弧线用极坐标驱动：
                    // 出场天体 θ: 0°→+100°（向右落下），入场天体 θ: -100°→0°（从左升起）。
                    const ARC_R = 10;          // 弧线半径 px（30px 按钮内保证悬停态两天体各占一半）
                    const ARC_FLAT = 0.45;     // 垂直压扁系数（模拟地平线弧）
                    const ARC_MAX_DEG = 100;   // 天体交换最大角度
                    const reduced = window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches;

                    // 离场 = 当前主题天体（静止时居中），入场 = 另一天体；切换完成后角色互换
                    let 离场El = 初始主题 === 'light' ? 太阳El : 月亮El;
                    let 入场El = 初始主题 === 'light' ? 月亮El : 太阳El;
                    let 当前p = 0, 目标p = 0, rafId = null, 正在切换 = false;
                    let 悬停抑制 = false, 指针在内 = false, 上一帧时刻 = 0;

                    const clamp01 = v => Math.min(1, Math.max(0, v));
                    function place(elm, deg, op, sc) {
                        const rad = deg * Math.PI / 180;
                        const x = ARC_R * Math.sin(rad);
                        const y = ARC_R * ARC_FLAT * (1 - Math.cos(rad));
                        elm.style.transform = `translate(-50%, -50%) translate(${x.toFixed(2)}px, ${y.toFixed(2)}px) scale(${sc.toFixed(3)})`;
                        elm.style.opacity = op.toFixed(3);
                    }
                    function render(p) {
                        // 离场：40% 进度后逐渐隐去；入场：60% 进度前完全浮现；缩放同步增减
                        place(离场El, ARC_MAX_DEG * p, clamp01(1 - (p - 0.4) / 0.55), 1 - 0.25 * p);
                        place(入场El, -ARC_MAX_DEG * (1 - p), clamp01((p - 0.05) / 0.55), 0.75 + 0.25 * p);
                    }
                    function tick(now) {
                        rafId = null;
                        if (!btn.isConnected) return;   // 按钮已被重渲染移除，停止动画
                        const dt = 上一帧时刻 ? Math.min(now - 上一帧时刻, 50) : 16;
                        上一帧时刻 = now;
                        // 指数收敛逼近目标：等效 ease-out，且天然可打断（悬停中途点击/移开都平滑）
                        当前p += (目标p - 当前p) * Math.min(1, dt / 100);
                        if (Math.abs(目标p - 当前p) < 0.002) 当前p = 目标p;
                        render(当前p);
                        if (当前p !== 目标p) { rafId = requestAnimationFrame(tick); return; }
                        上一帧时刻 = 0;
                        if (正在切换 && 目标p === 1) 完成切换();
                    }
                    function 前往(p) {
                        目标p = p;
                        if (rafId === null) { 上一帧时刻 = 0; rafId = requestAnimationFrame(tick); }
                    }
                    async function 完成切换() {
                        正在切换 = false;
                        const next = 切换主题(container);
                        // 角色互换：入场天体成为新主场天体，p=0 重置与 p=1 终态位置重合，无缝衔接
                        [离场El, 入场El] = [入场El, 离场El];
                        当前p = 0;
                        render(0);
                        btn.title = next === 'light' ? t("header.theme_to_dark") : t("header.theme_to_light");
                        // 目标p 归零保持状态机干净（当前p 已重置为 0）
                        目标p = 0;
                        // 仅当指针仍在按钮内时才抑制悬停预览（避免刚切完立即重播）；
                        // 动画期间指针已离开时不能置 true，否则 pointerleave 不会再触发，
                        // 悬停抑制无法解除，导致下次移入时预览失效
                        悬停抑制 = 指针在内;
                        // 主题切换后重建 3D 图形（画布颜色在创建时确定，CSS 变量无法驱动 WebGL）
                        // 防护：重建失败仅告警，不影响主题切换本身
                        try {
                            if (可视化面板已构建 && vizGraphInstance) {
                                await 安全销毁图(vizGraphInstance);
                                vizGraphInstance = null;
                                const mod = await import("./可视化面板.js");
                                if (mod && typeof mod.恢复可视化图形 === 'function') {
                                    await mod.恢复可视化图形();
                                }
                            }
                        } catch (err) {
                            console.warn('[节点梦工厂] 主题切换后重建 3D 图形失败（不影响主题生效）:', err);
                        }
                    }

                    render(0);

                    btn.addEventListener("pointerenter", () => {
                        指针在内 = true;
                        if (!reduced && !悬停抑制 && !正在切换) 前往(0.5);
                    });
                    btn.addEventListener("pointerleave", () => {
                        指针在内 = false;
                        悬停抑制 = false;
                        if (!reduced && !正在切换) 前往(0);
                    });
                    btn.addEventListener("click", () => {
                        if (正在切换) return;
                        if (reduced) { 完成切换(); return; }   // 减少动效偏好：直接瞬时切换
                        正在切换 = true;
                        前往(1);
                    });
                    return btn;
                })(),
                el("button", { class: "nca-icon-btn settings-btn", title: t("header.settings"), html: "⚙", onClick: () => 显示设置面板(覆盖层容器, 更新状态栏) }),
                el("button", { class: "nca-icon-btn new-session-btn", title: t("header.account"), html: "✦", onClick: () => 显示登录弹窗(覆盖层容器, { asEntry: true }) }),
            ]),
        ]);
    }

    function 渲染工具介绍页() {
        const page = el("div", { class: "nca-about-page" });

        // ── 模块清单（5 个模块）────────────────────────────────
        // 文案全部走 i18n 词条（welcome.*）；本函数在每次 renderSidebarUI 时重建，
        // 语言切换后整体重渲染，t() 在此处求值即为最新语言
        const modules = [
            {
                id: "features",
                icon: "🚀",
                title: t("welcome.features_title"),
                subtitle: t("welcome.features_sub"),
                tone: "accent",
                featured: true,
                desc: t("welcome.features_desc"),
                features: [
                    { color: "accent", text: t("welcome.features_item_1") },
                    { color: "purple", text: t("welcome.features_item_2") },
                    { color: "green",  text: t("welcome.features_item_3") },
                    { color: "accent", text: t("welcome.features_item_4") },
                ],
                tip: t("welcome.features_tip"),
            },
            {
                id: "usage",
                icon: "📖",
                title: t("welcome.usage_title"),
                subtitle: t("welcome.usage_sub"),
                tone: "green",
                featured: false,
                desc: t("welcome.usage_desc"),
                features: [
                    { color: "accent", text: t("welcome.usage_item_1") },
                    { color: "purple", text: t("welcome.usage_item_2") },
                    { color: "green",  text: t("welcome.usage_item_3") },
                    { color: "accent", text: t("welcome.usage_item_4") },
                    { color: "purple", text: t("welcome.usage_item_5") },
                ],
                tip: t("welcome.usage_tip"),
            },
            {
                id: "settings",
                icon: "⚙️",
                title: t("welcome.settings_title"),
                subtitle: t("welcome.settings_sub"),
                tone: "purple",
                featured: false,
                desc: t("welcome.settings_desc"),
                features: [
                    { color: "accent", text: t("welcome.settings_item_1") },
                    { color: "purple", text: t("welcome.settings_item_2") },
                    { color: "green",  text: t("welcome.settings_item_3") },
                    { color: "accent", text: t("welcome.settings_item_4") },
                    { color: "purple", text: t("welcome.settings_item_5") },
                    { color: "green",  text: t("welcome.settings_item_6") },
                    { color: "accent", text: t("welcome.settings_item_7") },
                ],
                tip: t("welcome.settings_tip"),
                action: { label: t("welcome.settings_action"), handler: () => 显示设置面板(覆盖层容器, 更新状态栏) },
            },
            {
                id: "security",
                icon: "🔒",
                title: t("welcome.security_title"),
                subtitle: t("welcome.security_sub"),
                tone: "accent",
                featured: false,
                desc: t("welcome.security_desc"),
                features: [
                    { color: "accent", text: t("welcome.security_item_1") },
                    { color: "purple", text: t("welcome.security_item_2") },
                    { color: "green",  text: t("welcome.security_item_3") },
                ],
                tip: t("welcome.security_tip"),
            },
            {
                id: "pricing",
                icon: "💰",
                title: t("welcome.pricing_title"),
                subtitle: t("welcome.pricing_sub"),
                tone: "green",
                featured: false,
                desc: t("welcome.pricing_desc"),
                features: [
                    { color: "accent", text: t("welcome.pricing_item_1") },
                    { color: "purple", text: t("welcome.pricing_item_2") },
                    { color: "green",  text: t("welcome.pricing_item_3") },
                ],
                tip: t("welcome.pricing_tip"),
            },
        ];

        // ── 主页视图 ──────────────────────────────────────────
        const homeView = el("div", { class: "nca-home-view" });
        homeView.appendChild(el("div", { class: "nca-about-brand" }, [
            el("div", { class: "nca-about-logo", html: LOGO_SVG }),
            el("h2", { class: "nca-about-title", text: t("brand.name") }),
            el("span", { class: "nca-about-version", text: "v0.1.0" }),
        ]));
        homeView.appendChild(el("div", { class: "nca-about-divider" }));
        homeView.appendChild(el("p", { class: "nca-about-desc", text: t("brand.about_desc") }));

        const grid = el("div", { class: "nca-about-grid" });
        modules.forEach(m => {
            const card = el("div", {
                class: `nca-about-card tone-${m.tone}${m.featured ? " featured" : ""}`,
                "data-module": m.id,
            }, [
                el("span", { class: "nca-about-card-icon", text: m.icon }),
                el("div", { class: "nca-about-card-meta" }, [
                    el("span", { class: "nca-about-card-title", text: m.title }),
                    el("span", { class: "nca-about-card-desc", text: m.subtitle }),
                ]),
                el("span", { class: "nca-about-card-arrow", text: "→" }),
            ]);
            card.addEventListener("click", () => showDetail(m.id));
            grid.appendChild(card);
        });
        homeView.appendChild(grid);

        homeView.appendChild(el("a", {
            class: "nca-about-github",
            href: "https://github.com/a63976659/ComfyUI-NodeCraft-AI",
            target: "_blank",
            text: "GitHub: a63976659/ComfyUI-NodeCraft-AI",
        }));

        const enterBtn = el("button", { class: "nca-btn nca-about-back-btn", text: t("brand.enter") });
        enterBtn.addEventListener("click", () => 切换介绍页(false));
        homeView.appendChild(enterBtn);

        // ── 详情视图 ──────────────────────────────────────────
        const detailView = el("div", { class: "nca-detail-view" });
        const detailBackBtn = el("button", { class: "nca-detail-back", text: t("welcome.back") });
        detailBackBtn.addEventListener("click", showHome);
        const detailCrumb = el("span", { class: "nca-detail-crumb", text: "MODULE / —" });
        const detailTitle = el("span", { class: "nca-detail-title" });
        const detailHeader = el("div", { class: "nca-detail-header" }, [
            detailBackBtn,
            detailTitle,
            detailCrumb,
        ]);
        const detailContent = el("div", { class: "nca-detail-content" });
        detailView.appendChild(detailHeader);
        detailView.appendChild(detailContent);

        page.appendChild(homeView);
        page.appendChild(detailView);

        function renderDetail(m) {
            detailContent.innerHTML = "";
            detailTitle.textContent = m.title;
            detailCrumb.textContent = `MODULE / ${m.id.toUpperCase()}`;

            const hero = el("div", { class: `nca-detail-hero tone-${m.tone}` }, [
                el("span", { class: "nca-detail-icon", text: m.icon }),
                el("div", { class: "nca-detail-hero-meta" }, [
                    el("span", { class: "nca-detail-subtitle", text: m.subtitle }),
                    el("h3", { class: "nca-detail-name", text: m.title }),
                ]),
            ]);
            detailContent.appendChild(hero);
            detailContent.appendChild(el("p", { class: "nca-detail-desc", text: m.desc }));

            const featList = el("ul", { class: "nca-detail-features" });
            m.features.forEach((f, i) => {
                featList.appendChild(el("li", { class: `nca-detail-feature tone-${f.color}` }, [
                    el("span", { class: "nca-detail-feature-index", text: String(i + 1).padStart(2, "0") }),
                    el("span", { class: "nca-detail-feature-bullet" }),
                    el("span", { class: "nca-detail-feature-text", text: f.text }),
                ]));
            });
            detailContent.appendChild(featList);

            if (m.tip) {
                detailContent.appendChild(el("div", { class: "nca-detail-tip" }, [
                    el("span", { class: "nca-detail-tip-label", text: "TIP" }),
                    el("span", { class: "nca-detail-tip-text", text: m.tip }),
                ]));
            }

            if (m.action) {
                const actionBtn = el("button", { class: "nca-detail-action", text: m.action.label });
                actionBtn.addEventListener("click", m.action.handler);
                detailContent.appendChild(actionBtn);
            }
        }

        function showDetail(id) {
            const m = modules.find(x => x.id === id);
            if (!m) return;
            renderDetail(m);
            page.classList.add("show-detail");
            requestAnimationFrame(() => { detailContent.scrollTop = 0; });
        }

        function showHome() {
            page.classList.remove("show-detail");
        }

        return page;
    }

    function 切换介绍页(show) {
        // 介绍页已上提至顶层 container，作为绝对定位的全屏遮罩
        // 覆盖 Tab 切换栏与下方面板，无需再切换主内容区显隐
        _介绍页可见 = !!show;
        if (show) {
            refs.介绍页.classList.add("visible");
        } else {
            refs.介绍页.classList.remove("visible");
        }
    }

    function 渲染底部版本栏() {
        return el("div", { class: "nca-footer-version" }, [
            el("span", { text: `${t("brand.name")} v0.1.0 | MIT License` }),
        ]);
    }

    // 新建菜单（小型下拉）—— 从公共会话列表面板的“+”按钮 onNewClick 回调唤起
    function 显示新建菜单(锚点) {
        // 已存在则关闭（再次点击切换）
        const existed = document.querySelector(".nca-create-menu-backdrop");
        if (existed) { existed.remove(); return; }

        const backdrop = el("div", { class: "nca-create-menu-backdrop" });
        const menu = el("div", { class: "nca-create-menu" });
        // 阻止点击菜单空白处冒泡到 backdrop 导致关闭
        menu.addEventListener("click", (ev) => ev.stopPropagation());

        const newItem = el("div", { class: "nca-create-menu-item", text: t("project.new") });
        newItem.addEventListener("click", () => {
            backdrop.remove();
            显示创建项目对话框(覆盖层容器, refs);
        });

        const tplItem = el("div", { class: "nca-create-menu-item", text: t("project.from_template") });
        tplItem.addEventListener("click", async () => {
            backdrop.remove();
            const { 显示模板市场对话框 } = await import("./模板市场对话框.js");
            显示模板市场对话框(覆盖层容器);
        });

        menu.appendChild(newItem);
        menu.appendChild(tplItem);

        // 定位到按钮右下方（fixed，相对视口）
        const rect = 锚点.getBoundingClientRect();
        menu.style.position = "fixed";
        menu.style.top = `${rect.bottom + 6}px`;
        // 右对齐到按钮右边沿，最小左边距 8px
        const left = Math.max(8, rect.right - 168);
        menu.style.left = `${left}px`;

        backdrop.addEventListener("click", () => backdrop.remove());
        backdrop.appendChild(menu);
        // 挂载到侧边栏根容器内部，确保继承 .nca-theme-light 主题变量（适配深浅色）
        const sidebarRoot = document.querySelector(".nca-sidebar-root");
        (sidebarRoot || document.body).appendChild(backdrop);
    }

    // 取消当前会话：清空消息区域、显示欢迎页、清除当前会话ID、同步取消插件文件夹选中并刷新列表
    function 恢复欢迎页() {
        // 流式输出进行中时不重置会话
        if (是否流式中()) return;
        状态.当前会话ID = null;
        设置插件文件夹("");
        渲染欢迎页(refs.消息区域, refs);
        // 触发公共会话列表面板重渲染（更新激活态、取消按钮可用性、当前会话标签）
        事件总线.emit(事件.会话列表更新, 状态.会话列表);
        更新状态栏("就绪");
    }

    function 渲染模型切换栏() {
        const 切换栏 = el("div", { class: "nca-model-switcher" });
        const 按钮组 = el("div", { class: "nca-switch-group" });
        // 按钮/徽章文字经 data-nca-label + CSS 伪元素渲染，免疫外部翻译插件改写（参见标签栏注释）
        const 本地按钮 = el("button", { class: `nca-switch-btn ${状态.模型来源 === "local" ? "active" : ""}`, "data-nca-label": t("settings.local") });
        const API按钮 = el("button", { class: `nca-switch-btn ${状态.模型来源 === "api" ? "active" : ""}`, "data-nca-label": t("settings.api") });
        按钮组.appendChild(本地按钮);
        按钮组.appendChild(API按钮);
        切换栏.appendChild(按钮组);

        const 本地下拉 = el("select", { class: "nca-model-select" });
        本地下拉.style.display = 状态.模型来源 === "local" ? "" : "none";
        // 释放显存按钮：仅本地模式可见，调用 REST 卸载模型接口以释放本地模型占用的显存/内存
        const 释放显存按钮 = el("button", {
            class: "nca-unload-btn",
            "data-nca-label": t("model.release_vram"),
            title: t("model.release_vram_title"),
        });
        释放显存按钮.style.display = 状态.模型来源 === "local" ? "" : "none";
        // API 模型下拉框：原生 select
        const API下拉 = el("select", { class: "nca-model-select nca-api-model-select" });
        API下拉.style.display = 状态.模型来源 === "api" ? "" : "none";
        API下拉.title = "选择 API 模型";
        // 思考深度按钮：仅 API 模式且模型支持（Kimi K3 等）时可见，点击循环切换档位（与释放显存按钮同位置复用）
        const 思考深度按钮 = el("button", {
            class: "nca-unload-btn nca-reasoning-btn",
            title: t("model.reasoning_title"),
        });
        function 刷新思考深度按钮() {
            const 支持 = 状态.模型来源 === "api" && 支持思考深度(状态.设置.model_name);
            思考深度按钮.style.display = 支持 ? "" : "none";
            if (!支持) return;
            const 档位 = 当前思考深度();
            思考深度按钮.dataset.ncaLabel = `${t("model.reasoning")}: ${档位 || t("model.reasoning_default")}`;
            思考深度按钮.classList.toggle("active", !!档位);
        }
        切换栏.appendChild(本地下拉);
        切换栏.appendChild(释放显存按钮);
        切换栏.appendChild(API下拉);
        切换栏.appendChild(思考深度按钮);

        // 用占位选项设置下拉框的单一提示态（加载中 / 未配置 / 云端不可达）
        function 设置API占位(文本) {
            API下拉.innerHTML = "";
            const opt = el("option", { value: "", text: 文本 });
            opt.disabled = true; opt.selected = true;
            API下拉.appendChild(opt);
        }

        function 刷新本地下拉() {
            本地下拉.innerHTML = "";
            if (状态.本地模型列表.length === 0) {
                const opt = el("option", { value: "", text: "未检测到本地模型" }); opt.disabled = true; 本地下拉.appendChild(opt);
            } else {
                状态.本地模型列表.forEach(m => { const opt = el("option", { value: m.name, text: m.name }); if (m.name === 状态.选中本地模型) opt.selected = true; 本地下拉.appendChild(opt); });
            }
        }
        // 从设置中的 API 配置方案列表填充下拉框（value=配置 id，选中项为激活配置）
        function 刷新API下拉() {
            const 配置列表 = Array.isArray(状态.设置.api_profiles) ? 状态.设置.api_profiles : [];
            if (配置列表.length === 0) {
                设置API占位("请到设置 > 模型 > API 服务中添加配置");
                return;
            }
            API下拉.innerHTML = "";
            配置列表.forEach(p => {
                const opt = el("option", { value: p.id, text: p.name || p.model_name || "未命名配置" });
                if (p.id === 状态.设置.active_api_profile_id) opt.selected = true;
                API下拉.appendChild(opt);
            });
        }
        function 更新模型选择UI() {
            本地按钮.className = `nca-switch-btn ${状态.模型来源 === "local" ? "active" : ""}`;
            API按钮.className = `nca-switch-btn ${状态.模型来源 === "api" ? "active" : ""}`;
            本地下拉.style.display = 状态.模型来源 === "local" ? "" : "none";
            释放显存按钮.style.display = 状态.模型来源 === "local" ? "" : "none";
            API下拉.style.display = 状态.模型来源 === "api" ? "" : "none";
            刷新本地下拉();
            // 仅在 API 模式下拉取/刷新云端模型列表，避免本地模式下无谓请求
            if (状态.模型来源 === "api") 刷新API下拉();
            刷新思考深度按钮();
        }

        本地按钮.addEventListener("click", async () => {
            await 更新模型选择({ 模型来源: "local" });
            更新状态栏("就绪");
        });
        API按钮.addEventListener("click", async () => {
            await 更新模型选择({ 模型来源: "api" });
            更新状态栏("就绪");
        });
        本地下拉.addEventListener("change", async () => {
            await 更新模型选择({ 选中本地模型: 本地下拉.value });
            更新状态栏("就绪");
        });
        API下拉.addEventListener("change", async () => {
            if (!API下拉.value) return;
            await 切换API配置(API下拉.value);
            更新状态栏("就绪");
        });
        思考深度按钮.addEventListener("click", async () => {
            if (思考深度按钮.disabled) return;
            思考深度按钮.disabled = true;
            try { await 切换思考深度(); } finally { 思考深度按钮.disabled = false; }
            刷新思考深度按钮();  // 设置已保存事件也会刷新，此处确保持久化失败时标签也即时回显
        });
        释放显存按钮.addEventListener("click", async () => {
            if (释放显存按钮.disabled) return;
            // 按钮文字由 CSS 伪元素渲染，状态文本同步改为读写 dataset.ncaLabel
            const 原文本 = 释放显存按钮.dataset.ncaLabel;
            释放显存按钮.disabled = true;
            释放显存按钮.classList.add("loading");
            释放显存按钮.dataset.ncaLabel = "释放中…";
            try {
                const 结果 = await 请求("POST", "/unload-model", {});
                if (结果 && 结果.success === true) {
                    释放显存按钮.dataset.ncaLabel = "已释放";
                    try { Toast && Toast.success && Toast.success("已释放本地模型显存"); } catch (_) {}
                } else {
                    释放显存按钮.dataset.ncaLabel = 原文本;
                    try { Toast && Toast.error && Toast.error("释放显存失败"); } catch (_) {}
                }
            } catch (e) {
                释放显存按钮.dataset.ncaLabel = 原文本;
                try { Toast && Toast.error && Toast.error(`释放显存失败: ${e && e.message ? e.message : e}`); } catch (_) {}
            } finally {
                释放显存按钮.classList.remove("loading");
                setTimeout(() => {
                    释放显存按钮.disabled = false;
                    if (释放显存按钮.dataset.ncaLabel === "已释放") 释放显存按钮.dataset.ncaLabel = 原文本;
                }, 1500);
            }
        });
        更新模型选择UI();

        // 任一面板修改模型选择、或加载/保存设置时，此面板随之同步刷新
        _追踪订阅(事件.模型选择变更, () => 更新模型选择UI());
        _追踪订阅(事件.设置已加载, () => 更新模型选择UI());
        _追踪订阅(事件.设置已保存, () => 更新模型选择UI());
        return 切换栏;
    }

    function 渲染状态栏() {
        refs.状态栏 = el("div", { class: "nca-status-bar" }, [
            el("div", { class: "status-left" }, [el("span", { class: "status-dot" }), el("span", { class: "status-text", text: "就绪" })]),
            el("div", { class: "status-right" }, [el("span", { class: "status-model", text: "" }), el("span", { class: "status-separator", text: "•" }), el("span", { class: "status-session", text: "" })]),
        ]);
        return refs.状态栏;
    }

    function 更新状态栏(text) {
        const statusText = refs.状态栏.querySelector(".status-text");
        const statusDot = refs.状态栏.querySelector(".status-dot");
        const modelText = refs.状态栏.querySelector(".status-model");
        const sessionText = refs.状态栏.querySelector(".status-session");
        if (statusText) statusText.textContent = text || "就绪";
        if (statusDot) { statusDot.classList.remove("busy", "error"); if (状态.连接状态 === "busy") statusDot.classList.add("busy"); else if (状态.连接状态 === "error") statusDot.classList.add("error"); }
        if (modelText) modelText.textContent = `模型: ${获取当前模型名称()}`;
        const seq = 获取会话序号();
        if (sessionText) sessionText.textContent = seq ? `会话 #${seq}` : "";
    }

    // ─── 事件绑定 ────────────────────────────────────────────
    // 注意：会话列表更新 / 会话切换 事件已由公共组件「会话列表面板」内部订阅并自动重渲染，此处不再重复处理列表渲染。
    // 仅保留消息渲染、状态栏与发送态等与会话视图协调器自身职责相关的全局事件。
    function 绑定全局事件() {
        _追踪订阅(事件.会话切换, () => { /* 状态栏由 切换会话 内部更新为"加载会话..." */ });
        _追踪订阅(事件.消息列表更新, (messages) => { 渲染所有消息(refs, messages); 更新状态栏("就绪"); });
        _追踪订阅(事件.新消息追加, (msg) => { 移除加载动画(refs); 追加消息DOM(refs, msg); });
        _追踪订阅(事件.发送状态变更, (isSending) => { refs.输入框.disabled = isSending; refs.发送按钮.disabled = isSending; if (isSending && !refs.消息区域.querySelector('#nca-loader') && !refs.消息区域.querySelector('.nca-streaming-cursor')) 显示加载动画(refs); });
        _追踪订阅(事件.状态栏更新, (text) => 更新状态栏(text));
        _追踪订阅(事件.连接状态变更, () => 更新状态栏());
        _追踪订阅(事件.设置已保存, () => 更新状态栏("就绪"));
    }

    async function 初始化() {
        await Promise.all([获取会话列表("develop"), 加载设置()]);
        更新状态栏("就绪");
    }
}
