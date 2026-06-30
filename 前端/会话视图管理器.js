// ═══════════════════════════════════════════════════════════════
// 会话视图管理器.js — 轻量协调器（入口 + 标签页路由）
// NodeCraft AI — Luxury Terminal / Neo-Noir Hacker
// ═══════════════════════════════════════════════════════════════

import {
    事件总线, 事件, 状态,
    获取会话列表,
    加载设置, 保存设置, 更新模型选择, 设置插件文件夹,
    获取当前模型名称, 获取会话序号,
    获取本地模型列表, 推导平台名称,
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
} from "./消息渲染器.js";
import { 显示设置面板, 显示创建项目对话框 } from "./设置面板.js";
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
        if (await 自动登录().catch(() => false)) return;
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
    渲染欢迎页(refs.消息区域, refs);
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
                    btn.addEventListener("click", () => {
                        const next = 切换主题(container);
                        btn.innerHTML = next === 'light' ? "☾" : "☀";
                        btn.title = next === 'light' ? "切换到深色主题" : "切换到浅色主题";
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
                id: "chat",
                icon: "🧠",
                title: "智能 AI 对话",
                subtitle: "三界面工作流",
                tone: "accent",
                featured: true,
                desc: "为 ComfyUI 插件开发的全流程提供独立 AI 工作空间——开发插件、优化插件、功能可视化三个专属对话界面，分别配备专业知识库与差异化系统提示词，让每一次对话都贴合当前任务语境。",
                features: [
                    { color: "accent", text: "三个专属界面：开发 / 优化 / 可视化分别独立" },
                    { color: "purple", text: "差异化系统提示词，对话风格按任务自动切换" },
                    { color: "green",  text: "SSE 流式响应，回复边生成边渲染" },
                    { color: "accent", text: "自动注入项目上下文、知识库与踩坑记录" },
                ],
                tip: "在顶部 Tab 切换工作场景，每个界面保留独立的会话历史。",
            },
            {
                id: "knowledge",
                icon: "📚",
                title: "知识库系统",
                subtitle: "精准检索增强",
                tone: "purple",
                desc: "基于 BM25 多实例检索引擎的知识增强系统——按 Tab 隔离的知识文档自动参与每一次对话，让 AI 不再凭空发挥，而是站在结构化知识之上回答。",
                features: [
                    { color: "accent", text: "BM25 多实例检索引擎，毫秒级匹配" },
                    { color: "purple", text: "按 Tab 隔离知识文档，互不污染" },
                    { color: "green",  text: "中英文混合分词，技术术语精准识别" },
                    { color: "accent", text: "RAG 检索增强生成，自动注入对话上下文" },
                ],
                tip: "将文档放入 知识库/<Tab>/ 子目录，重启后即被检索引擎索引。",
            },
            {
                id: "faq",
                icon: "🔧",
                title: "踩坑记录",
                subtitle: "AI 自动检索",
                tone: "green",
                desc: "结构化的踩坑 FAQ 管理——每一条都包含标题、问题、解决方案与标签，AI 在对话时会自动检索相关记录作为参考，让每一次踩过的坑都成为下一次的护栏。",
                features: [
                    { color: "accent", text: "结构化字段：标题 / 问题 / 解决方案 / 标签" },
                    { color: "purple", text: "完整 CRUD：随时新增、编辑、删除、检索" },
                    { color: "green",  text: "AI 对话时自动召回相关条目" },
                    { color: "accent", text: "标签体系，按主题快速归档" },
                ],
                tip: "在 Tab 内打开「踩坑记录」面板，记录的每一条都将被 AI 共享。",
            },
            {
                id: "sync",
                icon: "☁️",
                title: "云端同步",
                subtitle: "数据不丢失",
                tone: "accent",
                desc: "基于 ModelScope 数据集的双向同步通道——会话、知识、踩坑记录全量加密上行，多端拉取自动合并，再也不必担心因切换电脑或重装环境丢失开发记忆。",
                features: [
                    { color: "accent", text: "ModelScope 数据集双向同步" },
                    { color: "purple", text: "增量上传 + 自动拉取，节省带宽" },
                    { color: "green",  text: "时间戳冲突合并，新版本胜出" },
                    { color: "accent", text: "墓碑删除机制 + 7 天过期自动清理" },
                ],
                tip: "在「设置 → 云端同步」中绑定 ModelScope Token 即可启用。",
            },
            {
                id: "settings",
                icon: "⚙️",
                title: "设置",
                subtitle: "模型与配置",
                tone: "purple",
                desc: "一站式模型与运行环境配置——在线 API 与本地模型一键切换，所有第三方凭证均加密存储于本地，不上传、不泄漏。",
                features: [
                    { color: "accent", text: "在线 API & 本地模型一键切换" },
                    { color: "purple", text: "API 地址 / 密钥 / 模型名灵活配置" },
                    { color: "green",  text: "本地模型自动扫描 LLM 目录" },
                    { color: "accent", text: "推荐 qwen2.5-coder-32b 获得最佳代码体验" },
                ],
                tip: "首次使用请进入设置完成模型配置，否则对话功能将不可用。",
                action: { label: "打开设置 →", handler: () => 显示设置面板(覆盖层容器, 更新状态栏) },
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
        document.body.appendChild(backdrop);
    }

    // 取消当前会话：清空消息区域、显示欢迎页、清除当前会话ID、同步取消插件文件夹选中并刷新列表
    function 恢复欢迎页() {
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
        const API信息 = el("span", { class: "nca-api-info" });
        API信息.style.display = 状态.模型来源 === "api" ? "" : "none";
        切换栏.appendChild(本地下拉);
        切换栏.appendChild(释放显存按钮);
        切换栏.appendChild(API信息);

        function 刷新本地下拉() {
            本地下拉.innerHTML = "";
            if (状态.本地模型列表.length === 0) {
                const opt = el("option", { value: "", text: "未检测到本地模型" }); opt.disabled = true; 本地下拉.appendChild(opt);
            } else {
                状态.本地模型列表.forEach(m => { const opt = el("option", { value: m.name, text: m.name }); if (m.name === 状态.选中本地模型) opt.selected = true; 本地下拉.appendChild(opt); });
            }
        }
        function 刷新API信息() {
            const platform = 推导平台名称(状态.设置.base_url); const model = 状态.设置.model_name || "未配置";
            API信息.textContent = `${platform} / ${model}`; API信息.title = `${platform} / ${model}`;
        }
        function 更新模型选择UI() {
            本地按钮.className = `nca-switch-btn ${状态.模型来源 === "local" ? "active" : ""}`;
            API按钮.className = `nca-switch-btn ${状态.模型来源 === "api" ? "active" : ""}`;
            本地下拉.style.display = 状态.模型来源 === "local" ? "" : "none";
            释放显存按钮.style.display = 状态.模型来源 === "local" ? "" : "none";
            API信息.style.display = 状态.模型来源 === "api" ? "" : "none";
            刷新本地下拉(); 刷新API信息();
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
        API信息.addEventListener("click", () => 显示设置面板(覆盖层容器, 更新状态栏)); API信息.style.cursor = "pointer";
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
