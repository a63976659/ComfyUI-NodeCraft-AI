// ═══════════════════════════════════════════════════════════════
// 会话视图管理器.js — 轻量协调器（入口 + 标签页路由）
// NodeCraft AI — Luxury Terminal / Neo-Noir Hacker
// ═══════════════════════════════════════════════════════════════

import {
    事件总线, 事件, 状态,
    获取会话列表,
    加载设置, 保存设置, 更新模型选择, 设置插件文件夹,
    获取当前模型名称, 获取会话序号,
    获取本地模型列表,
    请求,
} from "./交互与状态.js";
import {
    el, LOGO_SVG, NCA_STORAGE_KEYS, Toast,
    cleanupAllEvents, clearPanelTimers,
    获取主题, 应用主题, 切换主题, 安全存储读,
} from "./工具函数.js";
import { 存储 } from "./存储引擎.js";
import {
    渲染欢迎页, 渲染所有消息, 追加消息DOM, 显示加载动画, 移除加载动画,
    渲染输入区域, 发送消息流式,
    创建流式恢复气泡, 是否流式中,
} from "./消息渲染器.js";
import { 显示设置面板, 显示创建项目对话框, 加载云端模型列表 } from "./设置面板.js";
import { 创建会话列表面板 } from "./会话列表面板.js";
import { t, 监听语言切换, 获取当前语言, 切换语言 } from "./i18n.js";

// 语言切换监听器取消句柄（跨序于 renderSidebarUI 多次调用，需在重渲染前取消以避免重复注册）
let _unsubLang = null;

// 可视化引擎“销毁图”函数的懒加载占位（首次切换离开可视化标签时按需导入）
let 销毁图 = null;


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
    注入样式资源();

    container.style.height = '100%';
    container.style.display = 'flex';
    container.style.flexDirection = 'column';
    container.style.position = 'relative';
    container.innerHTML = "";
    cleanupAllEvents();
    clearPanelTimers();
    if (typeof _unsubLang === 'function') {
        try { _unsubLang(); } catch (_) {}
        _unsubLang = null;
    }

    // 本插件自有根包裹层：避免主题类、CSS 变量、后代选择器泄漏到 ComfyUI 共享侧边栏容器。
    // 所有插件内容及覆盖层均挂在该根下；其被移除时静态样式随之失效。
    const sidebarRoot = el("div", { class: "nca-sidebar-root" });
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
    const tabBar = el("div", { class: "nc-tab-bar" }, [
        el("button", { class: "nc-tab active", "data-tab": "develop", text: t("tab.develop"), onClick: () => switchTab("develop") }),
        el("button", { class: "nc-tab", "data-tab": "optimize", text: t("tab.optimize"), onClick: () => switchTab("optimize") }),
        el("button", { class: "nc-tab", "data-tab": "visualize", text: t("tab.visualize"), onClick: () => switchTab("visualize") }),
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

    // 为消息渲染器提供对话框入口
    refs.显示创建项目对话框 = () => 显示创建项目对话框(覆盖层容器, refs);

    // 模型状态栏 + 底部版本信息
    sidebarRoot.appendChild(渲染状态栏());
    sidebarRoot.appendChild(渲染底部版本栏());

    // 工具介绍页（位于 sidebarRoot 顶层，全屏覆盖 Tab 栏与所有面板）
    refs.介绍页 = 渲染工具介绍页();
    sidebarRoot.appendChild(refs.介绍页);

    // 恢复标签页（仅恢复历史选中状态，介绍页关闭后呈现对应工作区）
    const savedTab = 安全存储读(NCA_STORAGE_KEYS.activeTab, 'develop');
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
        const stopBtn = el("button", { class: "nca-stop-btn", text: "■ 停止生成" });
        stopBtn.addEventListener("click", () => {
            const ctrl = 状态.流式状态.中止控制器;
            if (ctrl) ctrl.abort();
        });
        refs.停止按钮容器 = stopBtn;
        const inputArea = refs.输入框?.closest(".nca-input-area");
        if (inputArea) inputArea.insertBefore(stopBtn, inputArea.firstChild);
        // 广播状态
        事件总线.emit(事件.发送状态变更, true);
        事件总线.emit(事件.连接状态变更, "busy");
        更新状态栏("生成中...");
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
        const langBtn = el("button", {
            class: "nca-lang-btn",
            title: "切换语言 / Toggle Language",
            text: 获取当前语言() === 'zh-CN' ? 'cn' : 'en',
        });
        langBtn.addEventListener("click", (e) => {
            e.stopPropagation();
            const next = 切换语言();
            langBtn.textContent = next === 'zh-CN' ? 'cn' : 'en';
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
                    const 初始 = 获取主题();
                    const btn = el("button", {
                        class: "nca-icon-btn theme-btn",
                        title: 初始 === 'light' ? "切换到深色主题" : "切换到浅色主题",
                        html: 初始 === 'light' ? "☾" : "☀",
                    });
                    btn.addEventListener("click", async () => {
                        const next = 切换主题(container);
                        btn.innerHTML = next === 'light' ? "☾" : "☀";
                        btn.title = next === 'light' ? "切换到深色主题" : "切换到浅色主题";
                        // 主题切换后重建 3D 图形（画布颜色在创建时确定，CSS 变量无法驱动 WebGL）
                        if (可视化面板已构建 && vizGraphInstance) {
                            await 安全销毁图(vizGraphInstance);
                            vizGraphInstance = null;
                            const { 恢复可视化图形 } = await import("./可视化面板.js");
                            await 恢复可视化图形();
                        }
                    });
                    return btn;
                })(),
                el("button", { class: "nca-icon-btn settings-btn", title: "设置", html: "⚙", onClick: () => 显示设置面板(覆盖层容器, 更新状态栏) }),
                el("button", { class: "nca-icon-btn new-session-btn", title: "账号", html: "✦", onClick: () => 显示登录弹窗(覆盖层容器, { asEntry: true }) }),
            ]),
        ]);
    }

    function 渲染工具介绍页() {
        const page = el("div", { class: "nca-about-page" });

        // ── 模块清单（5 个模块）────────────────────────────────
        const modules = [
            {
                id: "features",
                icon: "🚀",
                title: "功能介绍",
                subtitle: "四大核心能力",
                tone: "accent",
                featured: true,
                desc: "NodeCraft AI 面向 ComfyUI 插件开发全流程，提供开发、优化、可视化与打包四大核心能力，让插件从创意到发布一气呵成。",
                features: [
                    { color: "accent", text: "开发插件：AI 驱动的 ComfyUI 自定义节点开发，支持从零创建或基于模板快速生成插件代码" },
                    { color: "purple", text: "优化插件：AI 分析现有插件代码，提供性能优化、代码质量改进建议并自动重构" },
                    { color: "green",  text: "功能可视化：以交互式图谱展示插件结构、节点依赖关系，并检测语法错误/规范问题/缺失依赖" },
                    { color: "accent", text: "打包发布：一键将插件打包为标准 ComfyUI 自定义节点格式，可直接安装使用" },
                ],
                tip: "在顶部 Tab 切换开发/优化/可视化三种工作模式。",
            },
            {
                id: "usage",
                icon: "📖",
                title: "使用方法",
                subtitle: "快速上手指南",
                tone: "green",
                featured: false,
                desc: "从创建到发布的完整工作流，帮助你快速上手 NodeCraft AI 的每一个环节。",
                features: [
                    { color: "accent", text: "模板创建：选择预设模板（基础图像处理/文本处理/API调用/模型加载/自定义UI），自动生成项目骨架" },
                    { color: "purple", text: "AI 会话：与 AI 对话描述需求，自动生成或修改代码，支持工具调用（读写文件、搜索替换）" },
                    { color: "green",  text: "代码优化：选择目标插件文件夹，AI 分析后给出重构方案，确认后自动执行" },
                    { color: "accent", text: "功能可视化：选择插件文件夹后一键生成依赖图谱，点击节点查看详情与异常" },
                    { color: "purple", text: "打包导出：在开发 Tab 的菜单中选择打包，生成可分发的插件压缩包" },
                ],
                tip: "首次使用建议从模板创建开始，熟悉后可直接通过对话完成所有操作。",
            },
            {
                id: "settings",
                icon: "⚙️",
                title: "设置介绍",
                subtitle: "模型与配置",
                tone: "purple",
                featured: false,
                desc: "灵活配置 AI 模型与开发环境，在线与本地、参数与凭证均可按需调整。",
                features: [
                    { color: "accent", text: "模型切换：支持多种 API 模型（DeepSeek/GPT/Claude/Qwen 等），在底部状态栏快速切换" },
                    { color: "purple", text: "本地模型：设置本地模型读取路径，支持离线使用（需下载模型文件）" },
                    { color: "green",  text: "模型下载：内置模型下载管理，一键获取推荐的本地模型" },
                    { color: "accent", text: "高级设置：温度/最大 Token/系统提示词等参数调整" },
                    { color: "purple", text: "GitHub 设置：配置 Token 后支持插件代码同步到 GitHub 仓库" },
                    { color: "green",  text: "记忆管理：查看与管理 AI 的跨会话记忆，支持手动编辑或清除" },
                    { color: "accent", text: "监控面板：实时查看 Token 消耗、工具调用次数、模型响应性能等" },
                ],
                tip: "点击底部状态栏的模型名称可快速切换 API 模型。",
                action: { label: "打开设置 →", handler: () => 显示设置面板(覆盖层容器, 更新状态栏) },
            },
            {
                id: "security",
                icon: "🔒",
                title: "安全说明",
                subtitle: "数据与隐私保护",
                tone: "accent",
                featured: false,
                desc: "了解插件的数据访问范围与安全机制，让每一次操作都清晰可控、有迹可循。",
                features: [
                    { color: "accent", text: "本地文件操作：AI 可读写 ComfyUI custom_nodes 目录下的插件文件，所有操作均有审计日志记录" },
                    { color: "purple", text: "网络访问：仅在调用 API 模型、GitHub 同步、云端同步时访问网络，不会上传您的插件代码" },
                    { color: "green",  text: "用户信息：账号信息仅用于会员鉴权与 Token 计费，密钥本地加密存储，不会明文传输" },
                ],
                tip: "所有文件操作记录可在数据/审计日志目录中查看。",
            },
            {
                id: "pricing",
                icon: "💎",
                title: "付费介绍",
                subtitle: "会员与计费",
                tone: "green",
                featured: false,
                desc: "分层会员体系，按需解锁功能与 API 模型权限",
                features: [
                    { color: "accent", text: "会员权限：非会员可登录但无法使用任何功能；基础会员可使用 API 模型以外的所有功能（本地模型、开发/优化/可视化/打包等）；进阶版和高级版拥有全部权限，包括 API 模型调用" },
                    { color: "purple", text: "费用价格：API 模型按实际 Token 用量计费，不同模型单价不同，用量明细实时可查" },
                    { color: "green",  text: "Token 时效：充值的 Token 余额长期有效无过期，会员赠送额度按自然月重置" },
                ],
                tip: "点击底部状态栏的余额可查看详细用量与充值。",
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
        const detailBackBtn = el("button", { class: "nca-detail-back", text: "← 返回" });
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
        const 本地按钮 = el("button", { class: `nca-switch-btn ${状态.模型来源 === "local" ? "active" : ""}`, text: "本地" });
        const API按钮 = el("button", { class: `nca-switch-btn ${状态.模型来源 === "api" ? "active" : ""}`, text: "API" });
        按钮组.appendChild(本地按钮);
        按钮组.appendChild(API按钮);
        切换栏.appendChild(按钮组);

        const 本地下拉 = el("select", { class: "nca-model-select" });
        本地下拉.style.display = 状态.模型来源 === "local" ? "" : "none";
        // 释放显存按钮：仅本地模式可见，调用 REST 卸载模型接口以释放本地模型占用的显存/内存
        const 释放显存按钮 = el("button", {
            class: "nca-unload-btn",
            text: "释放显存",
            title: "卸载本地模型，释放显存/内存",
        });
        释放显存按钮.style.display = 状态.模型来源 === "local" ? "" : "none";
        // API 模型下拉框：原生 select + 右侧淡蓝色免费徽章
        const API下拉 = el("select", { class: "nca-model-select nca-api-model-select" });
        API下拉.style.display = 状态.模型来源 === "api" ? "" : "none";
        API下拉.title = "选择 API 模型";
        // 免费模型徽章：淡蓝色图标，select 框右侧
        const 免费徽章 = el("span", { class: "nca-free-badge", text: "免费" });
        免费徽章.style.display = "none";
        切换栏.appendChild(本地下拉);
        切换栏.appendChild(释放显存按钮);
        切换栏.appendChild(API下拉);
        切换栏.appendChild(免费徽章);

        // 根据当前选中选项切换免费徽章显示
        function 刷新免费徽章() {
            const sel = API下拉.options[API下拉.selectedIndex];
            if (sel && sel.dataset.isFree === "true") {
                免费徽章.style.display = "";
            } else {
                免费徽章.style.display = "none";
            }
        }
        API下拉.addEventListener("change", 刷新免费徽章);

        // 用占位选项设置下拉框的单一提示态（加载中 / 未配置 / 云端不可达）
        function 设置API占位(文本) {
            API下拉.innerHTML = "";
            const opt = el("option", { value: "", text: 文本 });
            opt.disabled = true; opt.selected = true;
            API下拉.appendChild(opt);
            免费徽章.style.display = "none";
        }

        function 刷新本地下拉() {
            本地下拉.innerHTML = "";
            if (状态.本地模型列表.length === 0) {
                const opt = el("option", { value: "", text: "未检测到本地模型" }); opt.disabled = true; 本地下拉.appendChild(opt);
            } else {
                状态.本地模型列表.forEach(m => { const opt = el("option", { value: m.name, text: m.name }); if (m.name === 状态.选中本地模型) opt.selected = true; 本地下拉.appendChild(opt); });
            }
        }
        // 从云端加载 API 模型列表并填充下拉框，处理未配置/加载/错误三种状态
        async function 刷新API下拉() {
            const cloudUrl = 状态.设置.cloud_url || "";
            if (!cloudUrl) {
                设置API占位("未配置云端地址");
                return;
            }
            // 无真实选项时先展示加载态（缓存命中时几乎瞬时完成）
            const 有真实选项 = Array.from(API下拉.options).some(o => o.value);
            if (!有真实选项) 设置API占位("加载中…");
            try {
                await 加载云端模型列表(API下拉, 状态.设置);
            } catch (_) { /* 下方统一按结果兜底 */ }
            const 加载成功 = Array.from(API下拉.options).some(o => o.value);
            if (!加载成功) {
                设置API占位("云端不可达");
            } else if (状态.设置.model_name) {
                API下拉.value = 状态.设置.model_name;
            }
            刷新免费徽章();
        }
        function 更新模型选择UI() {
            本地按钮.className = `nca-switch-btn ${状态.模型来源 === "local" ? "active" : ""}`;
            API按钮.className = `nca-switch-btn ${状态.模型来源 === "api" ? "active" : ""}`;
            本地下拉.style.display = 状态.模型来源 === "local" ? "" : "none";
            释放显存按钮.style.display = 状态.模型来源 === "local" ? "" : "none";
            API下拉.style.display = 状态.模型来源 === "api" ? "" : "none";
            免费徽章.style.display = 状态.模型来源 === "api" ? 免费徽章.style.display : "none";
            刷新本地下拉();
            // 仅在 API 模式下拉取/刷新云端模型列表，避免本地模式下无谓请求
            if (状态.模型来源 === "api") 刷新API下拉();
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
            await 更新模型选择({ API模型名: API下拉.value });
            更新状态栏("就绪");
        });
        释放显存按钮.addEventListener("click", async () => {
            if (释放显存按钮.disabled) return;
            const 原文本 = 释放显存按钮.textContent;
            释放显存按钮.disabled = true;
            释放显存按钮.classList.add("loading");
            释放显存按钮.textContent = "释放中…";
            try {
                const 结果 = await 请求("POST", "/unload-model", {});
                if (结果 && 结果.success === true) {
                    释放显存按钮.textContent = "已释放";
                    try { Toast && Toast.success && Toast.success("已释放本地模型显存"); } catch (_) {}
                } else {
                    释放显存按钮.textContent = 原文本;
                    try { Toast && Toast.error && Toast.error("释放显存失败"); } catch (_) {}
                }
            } catch (e) {
                释放显存按钮.textContent = 原文本;
                try { Toast && Toast.error && Toast.error(`释放显存失败: ${e && e.message ? e.message : e}`); } catch (_) {}
            } finally {
                释放显存按钮.classList.remove("loading");
                setTimeout(() => {
                    释放显存按钮.disabled = false;
                    if (释放显存按钮.textContent === "已释放") 释放显存按钮.textContent = 原文本;
                }, 1500);
            }
        });
        更新模型选择UI();

        // 任一面板修改模型选择、或加载/保存设置时，此面板随之同步刷新
        事件总线.on(事件.模型选择变更, () => 更新模型选择UI());
        事件总线.on(事件.设置已加载, () => 更新模型选择UI());
        事件总线.on(事件.设置已保存, () => 更新模型选择UI());
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
        事件总线.on(事件.会话切换, () => { /* 状态栏由 切换会话 内部更新为"加载会话..." */ });
        事件总线.on(事件.消息列表更新, (messages) => { 渲染所有消息(refs, messages); 更新状态栏("就绪"); });
        事件总线.on(事件.新消息追加, (msg) => { 移除加载动画(refs); 追加消息DOM(refs, msg); });
        事件总线.on(事件.发送状态变更, (isSending) => { refs.输入框.disabled = isSending; refs.发送按钮.disabled = isSending; if (isSending && !refs.消息区域.querySelector('#nca-loader') && !refs.消息区域.querySelector('.nca-streaming-cursor')) 显示加载动画(refs); });
        事件总线.on(事件.状态栏更新, (text) => 更新状态栏(text));
        事件总线.on(事件.连接状态变更, () => 更新状态栏());
        事件总线.on(事件.设置已保存, () => 更新状态栏("就绪"));
    }

    async function 初始化() {
        await Promise.all([获取会话列表("develop"), 加载设置()]);
        更新状态栏("就绪");
    }
}
