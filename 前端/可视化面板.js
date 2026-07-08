// ═══════════════════════════════════════════════════════════════
// 可视化面板.js — 功能可视化面板（双模式：文件 / 功能）
// NodeCraft AI — Luxury Terminal / Neo-Noir Hacker
// ═══════════════════════════════════════════════════════════════

import {
    el, Toast, NCA_API_BASE, NCA_STORAGE_KEYS, 安全存储读,
} from "./工具函数.js";
import { 事件总线, 事件, 设置插件文件夹, 获取会话列表, 创建会话 } from "./交互与状态.js";
import { 加载可视化库, 是否已加载可视化库, 创建可视化图, 销毁图, formatSize } from "./可视化引擎.js";
import { 创建文件夹选择器 } from "./文件夹选择器.js";
import { 创建模型切换栏副本 } from "./插件开发面板.js";
import { 创建会话列表面板 } from "./会话列表面板.js";
import { 获取有效文件夹, 加载会话消息, 发送面板消息, 创建面板输入区, 绑定输入事件 } from "./面板会话公共.js";
import { 执行代码审查, 渲染审查结果, 显示审查深度下拉菜单, 格式化审查为消息内容 } from "./代码审查面板.js";

// ─── 演示数据（含 normal/error/warning 三种状态） ──────────
    const 演示数据 = {
        nodes: [
            { id: "root", name: "我的插件", type: "root", size: 0, status: "normal", group: "核心" },
            { id: "init", name: "__init__.py", type: "entry", size: 2048, status: "normal", group: "核心" },
            { id: "nodes", name: "nodes/", type: "directory", size: 0, status: "normal", group: "节点" },
            { id: "node_img", name: "图片处理.py", type: "script", size: 5120, status: "normal", group: "节点" },
            { id: "node_text", name: "文本生成.py", type: "script", size: 3072, status: "warning", group: "节点" },
            { id: "node_filter", name: "滤镜节点.py", type: "script", size: 1024, status: "error", group: "节点" },
            { id: "utils", name: "utils/", type: "directory", size: 0, status: "normal", group: "工具" },
            { id: "util_io", name: "文件读写.py", type: "script", size: 2560, status: "normal", group: "工具" },
            { id: "util_cache", name: "缓存管理.py", type: "script", size: 1536, status: "warning", group: "工具" },
            { id: "config", name: "config.json", type: "config", size: 512, status: "normal", group: "配置" },
            { id: "readme", name: "README.md", type: "doc", size: 4096, status: "normal", group: "文档" },
            { id: "web", name: "web/", type: "directory", size: 0, status: "normal", group: "前端" },
            { id: "js_main", name: "主界面.js", type: "web", size: 8192, status: "normal", group: "前端" },
            { id: "css", name: "样式.css", type: "style", size: 2048, status: "error", group: "前端" },
        ],
        links: [
            { source: "root", target: "init", type: "containment", status: "normal" },
            { source: "root", target: "nodes", type: "containment", status: "normal" },
            { source: "root", target: "utils", type: "containment", status: "normal" },
            { source: "root", target: "config", type: "containment", status: "normal" },
            { source: "root", target: "readme", type: "containment", status: "normal" },
            { source: "root", target: "web", type: "containment", status: "normal" },
            { source: "nodes", target: "node_img", type: "containment", status: "normal" },
            { source: "nodes", target: "node_text", type: "containment", status: "normal" },
            { source: "nodes", target: "node_filter", type: "containment", status: "normal" },
            { source: "utils", target: "util_io", type: "containment", status: "normal" },
            { source: "utils", target: "util_cache", type: "containment", status: "normal" },
            { source: "web", target: "js_main", type: "containment", status: "normal" },
            { source: "web", target: "css", type: "containment", status: "normal" },
            { source: "node_img", target: "util_io", type: "dependency", status: "normal" },
            { source: "node_text", target: "util_io", type: "dependency", status: "normal" },
            { source: "node_filter", target: "util_cache", type: "dependency", status: "normal" },
            { source: "init", target: "node_img", type: "dependency", status: "normal" },
            { source: "init", target: "node_text", type: "dependency", status: "normal" },
            { source: "js_main", target: "css", type: "dependency", status: "normal" },
        ],
    };

    let 演示模式中标识 = false;

    // ─── 模式颜色配置 ─────────────────────────────────────────────
const 文件模式颜色 = {
    python: '#3b82f6',
    javascript: '#f59e0b',
    config: '#8b5cf6',
    entry: '#06b6d4',
    directory: '#6b7280',
    other: '#9ca3af',
};

const 功能模式颜色 = {
    route: '#3b82f6',      // 路由/API — 蓝
    model: '#8b5cf6',      // 模型/AI — 紫
    ui: '#f59e0b',         // 界面/交互 — 黄
    storage: '#10b981',    // 存储/文件 — 绿
    auth: '#ef4444',       // 认证/安全 — 红
    tool: '#6b7280',       // 工具/辅助 — 灰
    core: '#06b6d4',       // 核心/入口 — 青
};

/**
 * 构建"功能可视化"面板
 * @param {HTMLElement} panel - 面板容器
 * @param {Function} getGraph - 获取当前图实例
 * @param {Function} setGraph - 设置当前图实例
 * @param {Object} ctx - 上下文（如 { 显示设置面板Fn }）
 */
// ─── 模块级恢复入口（标签切换回来时由会话视图管理器调用） ──────
let _恢复Fn = null;
export async function 恢复可视化图形() {
    if (typeof _恢复Fn === 'function') await _恢复Fn();
}

export function 构建可视化面板(panel, getGraph, setGraph, ctx) {
    // ─── 局部状态 ─────────────────────────────────────────────
    const 状态 = {
        当前插件名: null,
        当前模式: 'file',           // 操作栏选中模式
        显示模式: null,             // 当前图形显示的模式
        分析中: false,
        缓存数据: { meta: null, file: null, function: null },
    };
    let 加载版本号 = 0;

    // 跟踪最后操作来源、当前会话以及会话面板实例，用于实现插件列表与会话的互斥选择
    let 最后文件夹来源 = null; // 'plugin' | 'session' | null
    let 当前会话 = null;
    let viz会话面板 = null;
    let 审查结果缓存 = null;

    function 清除会话选择() {
        if (当前会话) {
            当前会话 = null;
            if (viz会话面板) viz会话面板.clearSelection();
            vizMsgArea.innerHTML = "";
        }
    }

    function 同步当前插件名() {
        const effectiveFolder = 获取有效文件夹(当前会话, 最后文件夹来源);
        if (effectiveFolder === 状态.当前插件名) return;
        if (effectiveFolder) {
            加载历史可视化(effectiveFolder);
        } else {
            加载版本号++;  // 使正在进行的加载历史可视化失效
            状态.当前插件名 = null;
            状态.缓存数据 = { meta: null, file: null, function: null };
            状态.显示模式 = null;
            清空图形();
            加载演示可视化();
            更新状态栏(null);
            更新模式切换按钮可用状态();
        }
    }

    // ─── 文件夹选择器 ─────────────────────────────────────────
    const selector = 创建文件夹选择器(
        null,
        () => 获取有效文件夹(当前会话, 最后文件夹来源),
        (folder) => {
            if (!folder) {
                Toast.warning("请先选择插件目录");
                return;
            }
            const reviewBtn = selector.querySelector(".nc-folder-review-btn");
            显示审查深度下拉菜单(reviewBtn, async (depth) => {
                await 触发可视化代码审查(depth, folder);
            });
        }
    );
    panel.appendChild(selector);

    // 对调代码审查按钮和刷新按钮的位置
    {
        const rBtn = selector.querySelector(".nc-folder-review-btn");
        const fBtn = selector.querySelector(".nc-folder-reset");
        if (rBtn && fBtn) {
            const toggle = rBtn.parentNode;
            const searchRow = fBtn.parentNode;
            toggle.replaceChild(fBtn, rBtn);
            searchRow.appendChild(rBtn);
            fBtn.style.marginLeft = 'auto';
        }
    }

    // ─── 状态栏 ───────────────────────────────────────────────
    const vizStatus = el("div", {
        class: "nc-viz-status",
        style: {
            padding: "6px 12px", fontSize: "11px",
            borderBottom: "1px solid", flexShrink: "0",
        },
    });
    vizStatus.textContent = '请选择要分析的插件';
    panel.appendChild(vizStatus);

    // ─── 操作栏 ───────────────────────────────────────────────
    const toolbar = el("div", { class: "nca-viz-toolbar", style: { flexShrink: "0" } });

    const modeToggle = el("div", { class: "nca-viz-mode-toggle" });
    const fileBtn = el("button", {
        class: "nca-viz-mode-btn active",
        type: "button",
        html: "📁 文件模式",
        title: "按文件结构分析",
    });
    const funcBtn = el("button", {
        class: "nca-viz-mode-btn",
        type: "button",
        html: "⚙️ 功能模式",
        title: "按功能模块分析",
    });
    modeToggle.appendChild(fileBtn);
    modeToggle.appendChild(funcBtn);

    const analyzeBtn = el("button", {
        class: "nca-viz-analyze-btn",
        type: "button",
        html: "▶ 开始分析",
    });

    toolbar.appendChild(modeToggle);
    toolbar.appendChild(analyzeBtn);

    fileBtn.addEventListener('click', () => 选择操作模式('file'));
    funcBtn.addEventListener('click', () => 选择操作模式('function'));
    analyzeBtn.addEventListener('click', () => 开始分析());

    function 选择操作模式(mode) {
        if (状态.分析中) return;
        状态.当前模式 = mode;
        fileBtn.classList.toggle('active', mode === 'file');
        funcBtn.classList.toggle('active', mode === 'function');
    }

    // ─── 主体区域：顶部会话列表（可折叠）+ 切换模式栏 + 下方（可视化 + 消息 + 输入）──
    const mainArea = el("div", {
        class: "nca-panel-main-area",
        style: {
            display: "flex",
            flexDirection: "column",
            flex: "1",
            minHeight: "0",
            overflow: "hidden",
        },
    });
    panel.appendChild(mainArea);

    // 顶部会话列表容器（竖向展开，与开发插件界面一致）
    const sidebarContainer = el("div", {
        class: "nca-visualize-sidebar",
        style: {
            width: "100%",
            borderBottom: "1px solid rgba(255,255,255,0.06)",
            display: "flex",
            flexDirection: "column",
            maxHeight: "120px",
            overflowY: "auto",
            flexShrink: "1",
        },
    });
    mainArea.appendChild(sidebarContainer);

    // 右侧内容区（可视化图 + 消息 + 输入）
    const contentArea = el("div", {
        class: "nca-visualize-content",
        style: {
            flex: "1",
            display: "flex",
            flexDirection: "column",
            minHeight: "0",
            minWidth: "0",
            overflow: "auto",
        },
    });
    mainArea.appendChild(contentArea);

    // ─── 可视化容器（含折叠标题栏） ──────────────────────────
    const vizContainerWrapper = el("div", {
        class: "nc-viz-container-wrapper",
    });

    const vizContainerToggle = el("div", { class: "nc-viz-container-toggle" });
    vizContainerToggle.innerHTML = `<span class="arrow">▼</span><span>可视化效果</span>`;
    vizContainerWrapper.appendChild(vizContainerToggle);

    // 操作栏（文件/功能模式切换 + 开始分析）作为折叠面板内容的第一行
    vizContainerWrapper.appendChild(toolbar);

    const vizContainer = el("div", {
        class: "nc-viz-container",
        style: { height: "480px", flexShrink: "0", position: "relative", overflow: "hidden" },
    });
    vizContainerWrapper.appendChild(vizContainer);
    contentArea.appendChild(vizContainerWrapper);

    // 折叠切换
    vizContainerToggle.addEventListener("click", () => {
        vizContainerWrapper.classList.toggle("collapsed");
    });

    const overlay = el("div", { class: "nca-viz-overlay" });
    const overlayFileBtn = el("button", {
        class: "nca-viz-overlay-btn disabled",
        type: "button",
        html: "文件",
    });
    const overlayFuncBtn = el("button", {
        class: "nca-viz-overlay-btn disabled",
        type: "button",
        html: "功能",
    });
    overlay.appendChild(overlayFileBtn);
    overlay.appendChild(overlayFuncBtn);
    vizContainer.appendChild(overlay);

    overlayFileBtn.addEventListener('click', () => {
        if (overlayFileBtn.classList.contains('disabled')) return;
        切换显示模式('file');
    });
    overlayFuncBtn.addEventListener('click', () => {
        if (overlayFuncBtn.classList.contains('disabled')) return;
        切换显示模式('function');
    });

    // ─── HUD 覆盖层元素 ──────────────────────────────────────
    // 搜索框（左上角）
    const hudSearch = el("div", { class: "nc-viz-hud-search" });
    const hudSearchIcon = el("span", { class: "nc-viz-hud-search-icon", html: "🔍" });
    const hudSearchInput = el("input", { type: "text", placeholder: "搜索节点..." });
    hudSearch.appendChild(hudSearchIcon);
    hudSearch.appendChild(hudSearchInput);
    vizContainer.appendChild(hudSearch);

    // 标题（顶部居中）
    const hudTitle = el("div", {
        class: "nc-viz-hud-title",
        html: `<h1>功能结构分析仪</h1><div class="nc-viz-hud-subtitle">STRUCTURAL DIAGNOSTICS</div>`,
    });
    vizContainer.appendChild(hudTitle);

    // 统计栏（右上角）
    const hudStats = el("div", { class: "nc-viz-hud-stats" });
    const statTotal = el("div", { class: "nc-viz-hud-stat", html: `<span class="nc-viz-hud-dot c"></span><span>节点</span> <strong>0</strong>` });
    const statNormal = el("div", { class: "nc-viz-hud-stat", html: `<span class="nc-viz-hud-dot g"></span><span>正常</span> <strong>0</strong>` });
    const statError = el("div", { class: "nc-viz-hud-stat", html: `<span class="nc-viz-hud-dot r"></span><span>异常</span> <strong>0</strong>` });
    const statWarning = el("div", { class: "nc-viz-hud-stat", html: `<span class="nc-viz-hud-dot a"></span><span>警告</span> <strong>0</strong>` });
    hudStats.appendChild(statTotal);
    hudStats.appendChild(statNormal);
    hudStats.appendChild(statError);
    hudStats.appendChild(statWarning);
    vizContainer.appendChild(hudStats);

    // 全屏按钮（右上角统计栏下方）
    const hudFullscreen = el("button", {
        type: "button",
        class: "nc-viz-hud-fullscreen",
        html: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M4 8V4h6M20 8V4h-6M4 16v4h6M20 16v4h-6"/></svg>`,
        title: "全屏显示",
    });
    hudFullscreen.addEventListener("click", () => {
        if (document.fullscreenElement === vizContainer) {
            document.exitFullscreen();
        } else {
            vizContainer.requestFullscreen();
        }
    });
    document.addEventListener("fullscreenchange", () => {
        if (document.fullscreenElement === vizContainer) {
            hudFullscreen.classList.add("active");
            hudFullscreen.title = "退出全屏";
            hudFullscreen.innerHTML = `<span style="font-size:20px;line-height:1">&times;</span>`;
        } else {
            hudFullscreen.classList.remove("active");
            hudFullscreen.title = "全屏显示";
            hudFullscreen.innerHTML = `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M4 8V4h6M20 8V4h-6M4 16v4h6M20 16v4h-6"/></svg>`;
        }
    });
    vizContainer.appendChild(hudFullscreen);

    // 图例（左下角）
    const hudLegend = el("div", {
        class: "nc-viz-hud-legend",
        html: `
            <h3>图 例</h3>
            <div class="nc-viz-hud-legend-item"><span class="nc-viz-hud-legend-line green"></span>正常关联</div>
            <div class="nc-viz-hud-legend-item"><span class="nc-viz-hud-legend-line red"></span>异常关联</div>
            <div class="nc-viz-hud-legend-item"><span class="nc-viz-hud-legend-node ok"></span>正常节点</div>
            <div class="nc-viz-hud-legend-item"><span class="nc-viz-hud-legend-node err"></span>异常节点</div>
            <div class="nc-viz-hud-legend-item"><span class="nc-viz-hud-legend-node warn"></span>警告节点</div>
        `,
    });
    vizContainer.appendChild(hudLegend);

    // 过滤按钮（底部居中）
    const hudFilters = el("div", { class: "nc-viz-hud-filters" });
    const filterButtons = [
        { label: "全部", status: "all" },
        { label: "异常", status: "error" },
        { label: "警告", status: "warning" },
        { label: "正常", status: "normal" },
    ];
    let 当前过滤 = "all";
    filterButtons.forEach(cfg => {
        const btn = el("button", { type: "button", html: cfg.label });
        if (cfg.status === "all") btn.classList.add("active");
        btn.addEventListener("click", () => {
            当前过滤 = cfg.status;
            hudFilters.querySelectorAll("button").forEach(b => b.classList.remove("active"));
            btn.classList.add("active");
            const g = getGraph();
            if (g && typeof g.filterNodes === "function") {
                g.filterNodes(cfg.status);
            }
        });
        hudFilters.appendChild(btn);
    });
    vizContainer.appendChild(hudFilters);

    // 操作提示（右下角）
    const hudControls = el("div", {
        class: "nc-viz-hud-controls",
        html: `
            <div><kbd>左键拖拽</kbd> 旋转</div>
            <div><kbd>滚轮</kbd> 缩放</div>
            <div><kbd>右键拖拽</kbd> 平移</div>
            <div><kbd>点击节点</kbd> 详情</div>
        `,
    });
    vizContainer.appendChild(hudControls);

    // HUD 角标装饰
    const hudCorners = [];
    ['tl','tr','bl','br'].forEach(pos => {
        const corner = el("div", { class: `nc-viz-hud-corner ${pos}` });
        vizContainer.appendChild(corner);
        hudCorners.push(corner);
    });

    // 节点详情面板（右侧滑出）
    const detailPanel = el("div", { class: "nc-viz-detail-panel" });
    detailPanel.innerHTML = `
        <button class="nc-viz-detail-close">×</button>
        <div class="nc-viz-detail-name"></div>
        <div class="nc-viz-detail-type"></div>
        <div class="nc-viz-detail-status normal">正常</div>
        <div class="nc-viz-detail-desc"></div>
        <div class="nc-viz-detail-error-section" style="display:none">
            <div class="nc-viz-detail-error-title">⚠ 异常详情</div>
            <div class="nc-viz-detail-error-content"></div>
        </div>
        <div class="nc-viz-detail-warning-section" style="display:none">
            <div class="nc-viz-detail-warning-title">警告信息</div>
            <div class="nc-viz-detail-warning-content"></div>
        </div>
        <div class="nc-viz-detail-conn-title">关联列表</div>
        <ul class="nc-viz-detail-conn"></ul>
    `;
    vizContainer.appendChild(detailPanel);
    detailPanel.querySelector('.nc-viz-detail-close').addEventListener('click', () => {
        detailPanel.classList.remove('open');
    });

    // Tooltip（鼠标悬停）
    const tooltip = el("div", { class: "nc-viz-tooltip" });
    vizContainer.appendChild(tooltip);

    // HUD 统计更新函数（数字动画）
    function 更新HUD统计(graph) {
        if (!graph || typeof graph.getStats !== "function") return;
        const s = graph.getStats();
        动画数字(statTotal.querySelector("strong"), s.total);
        动画数字(statNormal.querySelector("strong"), s.normal);
        动画数字(statError.querySelector("strong"), s.error);
        动画数字(statWarning.querySelector("strong"), s.warning);
    }

    function 动画数字(el, target) {
        if (el._animTimer) clearInterval(el._animTimer);
        const steps = 20;
        const interval = 30;
        let step = 0;
        el.textContent = '0';
        el._animTimer = setInterval(() => {
            step++;
            el.textContent = Math.round(target * (step / steps));
            if (step >= steps) {
                el.textContent = target;
                clearInterval(el._animTimer);
                el._animTimer = null;
            }
        }, interval);
    }

    // 搜索事件
    hudSearchInput.addEventListener("input", () => {
        const g = getGraph();
        if (g && typeof g.searchNodes === "function") {
            g.searchNodes(hudSearchInput.value);
        }
    });

    // ─── 消息区 + 模型栏 + 输入区 ────────────────────────────
    const vizMsgArea = el("div", {
        class: "nca-messages nc-viz-messages",
        style: {
            height: "120px", overflowY: "auto", padding: "8px 12px",
            borderTop: "1px solid rgba(255,255,255,0.06)", flexShrink: "0",
        },
    });
    contentArea.appendChild(vizMsgArea);
    const modelSwitcher = 创建模型切换栏副本(ctx);
    contentArea.appendChild(modelSwitcher);

    // 输入区域（复用面板公共组件）
    const { inputArea: vizInputArea, input: vizInput, sendBtn: vizSendBtn, 附件: viz附件 } = 创建面板输入区(panel, { placeholder: "询问关于此插件的问题..." });
    contentArea.appendChild(vizInputArea);

    // ─── 审查视图容器（内嵌显示，初始隐藏） ──────────────────────
    const reviewView = el("div", {
        class: "nc-viz-review-view",
        style: {
            display: "none",
            flexDirection: "column",
            flex: "1",
            minHeight: "0",
            overflow: "hidden",
        },
    });
    contentArea.appendChild(reviewView);

    // ─── 状态栏 / 按钮辅助 ───────────────────────────────────
    function 更新状态栏(stats) {
        const pluginName = 状态.当前插件名 || 获取有效文件夹(当前会话, 最后文件夹来源);
        if (!pluginName) {
            vizStatus.textContent = '请选择要分析的插件';
            return;
        }
        const 模式标签 = 状态.显示模式 === 'function' ? '功能模式' : (状态.显示模式 === 'file' ? '文件模式' : '未分析');
        if (!stats) {
            vizStatus.textContent = `📦 ${pluginName} — ${模式标签}`;
            return;
        }
        const 节点数 = stats.total_files != null ? stats.total_files
            : (stats.total_nodes != null ? stats.total_nodes : '-');
        const 异常 = stats.error_count != null ? stats.error_count : 0;
        const 循环 = stats.circular_deps != null ? stats.circular_deps : 0;
        vizStatus.textContent = `📊 ${pluginName} — ${模式标签} | 节点: ${节点数} | 异常: ${异常} | 循环依赖: ${循环}`;
    }

    function 设置分析状态(loading) {
        状态.分析中 = loading;
        if (loading) {
            analyzeBtn.classList.add('loading');
            analyzeBtn.disabled = true;
            analyzeBtn.innerHTML = '<span class="nca-viz-spinner"></span> 分析中...';
            fileBtn.disabled = true;
            funcBtn.disabled = true;
        } else {
            analyzeBtn.classList.remove('loading');
            analyzeBtn.disabled = false;
            analyzeBtn.innerHTML = '▶ 开始分析';
            fileBtn.disabled = false;
            funcBtn.disabled = false;
        }
    }

    function 更新模式切换按钮可用状态() {
        const 有文件 = !!状态.缓存数据.file;
        const 有功能 = !!状态.缓存数据.function;
        overlayFileBtn.classList.toggle('disabled', !有文件);
        overlayFuncBtn.classList.toggle('disabled', !有功能);
        overlayFileBtn.classList.toggle('active', 状态.显示模式 === 'file');
        overlayFuncBtn.classList.toggle('active', 状态.显示模式 === 'function');
    }

    function 获取最近分析模式(meta) {
        if (!meta) return null;
        if (meta.file_mode_analyzed) return 'file';
        if (meta.function_mode_analyzed) return 'function';
        return null;
    }

    function 显示分析引导() {
        vizContainer.querySelectorAll('.nca-viz-empty-hint').forEach(n => n.remove());
        const hint = el("div", {
            class: "nca-viz-empty-hint",
            html: `
                <div class="nca-viz-empty-icon">◇</div>
                <div class="nca-viz-empty-title">尚未生成可视化数据</div>
                <div class="nca-viz-empty-desc">在上方选择「文件模式」或「功能模式」，点击「开始分析」生成依赖图</div>
            `,
        });
        vizContainer.appendChild(hint);
    }

    function 清空图形() {
        隐藏演示标签();
        const g = getGraph();
        if (g) 销毁图(g);
        setGraph(null);
        // 仅移除图形相关 DOM，保留 overlay 和 HUD 元素
        detailPanel.classList.remove('open');
        tooltip.classList.remove('visible');
        const hud保留 = new Set([hudSearch, hudTitle, hudStats, hudFullscreen, hudLegend, hudFilters, hudControls, detailPanel, tooltip, ...hudCorners]);
        Array.from(vizContainer.children).forEach(child => {
            if (child !== overlay && !hud保留.has(child)) vizContainer.removeChild(child);
        });
        // 重置统计
        statTotal.querySelector("strong").textContent = "0";
        statNormal.querySelector("strong").textContent = "0";
        statError.querySelector("strong").textContent = "0";
        statWarning.querySelector("strong").textContent = "0";
    }

    // ─── 渲染图形 ─────────────────────────────────────────────
    async function 渲染图形(data, mode) {
        // 首次加载 3D 库时显示加载提示（~2.5MB CDN 资源）
        if (!是否已加载可视化库()) {
            清空图形();
            const 加载提示 = el("div", {
                class: "nca-viz-empty-hint nca-viz-lib-loading",
                html: `
                    <div class="nca-viz-empty-icon"><span class="nca-viz-spinner"></span></div>
                    <div class="nca-viz-empty-title">正在加载 3D 可视化库...</div>
                    <div class="nca-viz-empty-desc">首次加载约 2.5MB，请稍候（后续将使用缓存）</div>
                `,
            });
            vizContainer.appendChild(加载提示);
            const 临时状态 = '⏳ 正在加载 3D 可视化库（~2.5MB）...';
            const 原状态 = vizStatus.textContent;
            vizStatus.textContent = 临时状态;
            try {
                await 加载可视化库();
            } catch (e) {
                清空图形();
                vizStatus.textContent = `❌ 3D 可视化库加载失败：${e.message}`;
                显示分析引导();
                throw e;
            }
            if (vizStatus.textContent === 临时状态) {
                vizStatus.textContent = 原状态;
            }
        } else {
            await 加载可视化库();
        }
        清空图形();

        const 颜色映射 = mode === 'function' ? 功能模式颜色 : 文件模式颜色;
        const 默认色 = mode === 'function' ? 功能模式颜色.tool : 文件模式颜色.other;

        const graph = 创建可视化图(vizContainer, { nodes: data.nodes || [], links: data.links || [] }, {
            onNodeClick: (node) => {
                if (!node) return;
                const 类别 = node.type || node.category || '';

                // 填充详情面板
                const 状态文本 = node.status === 'error' ? '异常' : node.status === 'warning' ? '警告' : '正常';
                const 状态类 = node.status || 'normal';
                detailPanel.querySelector('.nc-viz-detail-name').textContent = node.name || node.id;
                detailPanel.querySelector('.nc-viz-detail-type').textContent = 类别;
                const statusBadge = detailPanel.querySelector('.nc-viz-detail-status');
                statusBadge.className = `nc-viz-detail-status ${状态类}`;
                statusBadge.textContent = 状态文本;
                detailPanel.querySelector('.nc-viz-detail-desc').textContent = node.reason || node.id;

                // 详细异常/警告信息区域（每次点击先重置，避免残留上一个节点的信息）
                const errorSection = detailPanel.querySelector('.nc-viz-detail-error-section');
                const warningSection = detailPanel.querySelector('.nc-viz-detail-warning-section');
                const errorContent = detailPanel.querySelector('.nc-viz-detail-error-content');
                const warningContent = detailPanel.querySelector('.nc-viz-detail-warning-content');
                errorContent.textContent = '';
                warningContent.textContent = '';
                errorSection.style.display = 'none';
                warningSection.style.display = 'none';

                // 收集与当前节点相关的连线级异常/警告
                const 关联异常 = [];
                const 关联警告 = [];
                if (graph && graph._edges) {
                    graph._edges.forEach(e => {
                        if ((e.source === node.id || e.target === node.id) && e.reason) {
                            const sName = (graph._nodeMap[e.source] && (graph._nodeMap[e.source].name || graph._nodeMap[e.source].id)) || e.source;
                            const tName = (graph._nodeMap[e.target] && (graph._nodeMap[e.target].name || graph._nodeMap[e.target].id)) || e.target;
                            const 文本 = `关联异常：${sName}→${tName}：${e.reason}`;
                            if (e.status === 'error') 关联异常.push(文本);
                            else if (e.status === 'warning') 关联警告.push(文本);
                        }
                    });
                }

                if (node.status === 'error') {
                    const 段落 = [];
                    段落.push(node.reason || '暂无详细信息');
                    if (关联异常.length) 段落.push(关联异常.join('\n'));
                    errorContent.textContent = 段落.join('\n');
                    errorSection.style.display = '';
                    if (关联警告.length) {
                        warningContent.textContent = 关联警告.join('\n');
                        warningSection.style.display = '';
                    }
                } else if (node.status === 'warning') {
                    const 段落 = [];
                    段落.push(node.reason || '暂无详细信息');
                    if (关联警告.length) 段落.push(关联警告.join('\n'));
                    warningContent.textContent = 段落.join('\n');
                    warningSection.style.display = '';
                    if (关联异常.length) {
                        errorContent.textContent = 关联异常.join('\n');
                        errorSection.style.display = '';
                    }
                } else {
                    // 正常节点：仅在存在关联连线异常/警告时才显示对应区域
                    if (关联异常.length) {
                        errorContent.textContent = 关联异常.join('\n');
                        errorSection.style.display = '';
                    }
                    if (关联警告.length) {
                        warningContent.textContent = 关联警告.join('\n');
                        warningSection.style.display = '';
                    }
                }

                // 关联列表
                const connList = detailPanel.querySelector('.nc-viz-detail-conn');
                connList.innerHTML = '';
                if (graph && graph._edges) {
                    graph._edges.forEach(e => {
                        if (e.source === node.id) {
                            const tNode = graph._nodeMap[e.target];
                            if (tNode) {
                                const li = el("li", { html: `<span class="conn-arrow">→</span><span>${tNode.name || tNode.id}</span>` });
                                connList.appendChild(li);
                            }
                        } else if (e.target === node.id) {
                            const sNode = graph._nodeMap[e.source];
                            if (sNode) {
                                const li = el("li", { html: `<span class="conn-arrow">←</span><span>${sNode.name || sNode.id}</span>` });
                                connList.appendChild(li);
                            }
                        }
                    });
                }
                detailPanel.classList.add('open');
            },
            onNodeHover: (node, event) => {
                if (node) {
                    const 状态文本 = node.status === 'error' ? '异常' : node.status === 'warning' ? '警告' : '正常';
                    const 状态类 = node.status || 'normal';
                    tooltip.innerHTML = `<div class="tt-name">${node.name || node.id}</div><div class="tt-status ${状态类}">${状态文本}</div>`;
                    tooltip.classList.add('visible');
                    if (event) {
                        tooltip.style.left = (event.clientX + 14) + 'px';
                        tooltip.style.top = (event.clientY + 14) + 'px';
                    }
                } else {
                    tooltip.classList.remove('visible');
                }
            },
        });

        if (graph) {
            try {
                const isLight = !!vizContainer.closest('.nca-theme-light');
                graph.nodeColor(node => {
                    if (node.status === 'error') return isLight ? '#dc2626' : '#ff2255';
                    if (node.status === 'warning') return isLight ? '#d97706' : '#ffaa00';
                    return isLight ? '#059669' : '#00ff88';
                });
                if (mode === 'function') {
                    graph.linkLabel(link => link.label || link.relation || '');
                    graph.linkDirectionalParticles(link => (link.label || link.relation) ? 2 : 0);
                }
            } catch (e) { /* 图实例尚未支持时忽略 */ }
        }
        setGraph(graph);
        更新HUD统计(graph);
    }

    // ─── 加载历史可视化数据 ───────────────────────────────────
    async function 加载历史可视化(pluginName) {
        const 当前版本 = ++加载版本号;
        状态.当前插件名 = pluginName;
        状态.缓存数据 = { meta: null, file: null, function: null };
        状态.显示模式 = null;
        清空图形();
        vizStatus.textContent = `🔍 加载 ${pluginName} 历史数据...`;

        try {
            const url = `${NCA_API_BASE}/visualization/load?plugin_path=${encodeURIComponent(pluginName)}`;
            const resp = await fetch(url);
            if (当前版本 !== 加载版本号) return;
            const json = await resp.json().catch(() => ({}));
            if (当前版本 !== 加载版本号) return;
            const data = (json && json.data) ? json.data : json || {};

            if (data && data.meta) {
                状态.缓存数据.meta = data.meta;
                if (data.file_mode_data) 状态.缓存数据.file = data.file_mode_data;
                if (data.function_mode_data) 状态.缓存数据.function = data.function_mode_data;

                const 最近模式 = 获取最近分析模式(data.meta);
                if (最近模式 && 状态.缓存数据[最近模式]) {
                    await 切换显示模式(最近模式);
                } else {
                    显示分析引导();
                    更新状态栏(null);
                }
                更新模式切换按钮可用状态();
            } else {
                显示分析引导();
                更新状态栏(null);
                更新模式切换按钮可用状态();
            }
        } catch (e) {
            if (当前版本 !== 加载版本号) return;
            vizStatus.textContent = `❌ 加载失败: ${e.message}`;
            显示分析引导();
            更新模式切换按钮可用状态();
        }
    }

    // ─── 切换显示模式 ─────────────────────────────────────────
    async function 切换显示模式(mode) {
        const data = 状态.缓存数据[mode];
        if (!data) return;
        状态.显示模式 = mode;
        await 渲染图形(data, mode);
        更新状态栏(data.stats);
        更新模式切换按钮可用状态();
    }

    // ─── 开始分析 ─────────────────────────────────────────────
    async function 开始分析() {
        const 当前版本 = ++加载版本号;
        const pluginName = 状态.当前插件名 || 获取有效文件夹(当前会话, 最后文件夹来源);
        if (!pluginName) {
            vizStatus.textContent = '⚠️ 请先选择要分析的插件';
            return;
        }
        if (状态.分析中) return;

        const mode = 状态.当前模式;
        设置分析状态(true);
        vizStatus.textContent = `⏳ 正在以${mode === 'function' ? '功能' : '文件'}模式分析: ${pluginName}...`;
        状态.当前插件名 = pluginName;

        try {
            const resp = await fetch(`${NCA_API_BASE}/visualization/analyze`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ plugin_path: pluginName, mode }),
            });
            if (当前版本 !== 加载版本号) return;
            const result = await resp.json().catch(() => ({}));

            if (result && result.status === 'success' && result.data) {
                状态.缓存数据[mode] = result.data;
                if (result.meta) {
                    状态.缓存数据.meta = result.meta;
                }

                状态.显示模式 = mode;
                await 渲染图形(result.data, mode);
                更新状态栏(result.data.stats);
                更新模式切换按钮可用状态();
            } else {
                const 错误 = (result && (result.error || result.message)) || '未知错误';
                vizStatus.textContent = `❌ 分析失败: ${错误}`;
            }
        } catch (e) {
            vizStatus.textContent = `❌ 分析失败: ${e.message}`;
        } finally {
            设置分析状态(false);
        }
    }

    // ─── 演示标签 ──────────────────────────────────────────────
    let 演示标签El = null;
    function 显示演示标签() {
        if (演示标签El) return;
        演示标签El = el("div", {
            class: "nca-viz-demo-badge",
            style: {
                position: "absolute", top: "8px", left: "50%", transform: "translateX(-50%)",
                zIndex: "10", padding: "5px 14px", borderRadius: "6px",
                fontSize: "11px", whiteSpace: "nowrap",
                backdropFilter: "blur(6px)", pointerEvents: "none",
            },
            html: "📎 演示数据 — 选择插件后点击「开始分析」查看实际结构",
        });
        vizContainer.appendChild(演示标签El);
    }
    function 隐藏演示标签() {
        if (演示标签El) {
            演示标签El.remove();
            演示标签El = null;
        }
    }

    // ─── 加载演示数据 ───────────────────────────────────────────
    async function 加载演示可视化() {
        if (演示模式中标识) return;
        演示模式中标识 = true;
        try {
            await 渲染图形(演示数据, 'file');
            vizStatus.textContent = '📎 演示模式 — 选择插件并点击「开始分析」查看实际结构';
        } catch (e) {
            console.warn('[可视化面板] 演示数据加载失败:', e);
            显示分析引导();
        }
        演示模式中标识 = false;
    }

    // ─── 代码审查（内嵌展示，复用共享的执行代码审查与渲染审查结果）──
    async function 触发可视化代码审查(depth, folder) {
        const reviewBtn = selector.querySelector(".nc-folder-review-btn");
        if (reviewBtn) {
            reviewBtn.disabled = true;
            reviewBtn.innerHTML = '<span class="nca-review-loading-spinner"></span> 审查中...';
        }
    
        // 隐藏可视化内容，显示审查视图
        vizContainer.style.display = 'none';
        vizMsgArea.style.display = 'none';
        modelSwitcher.style.display = 'none';
        vizInputArea.style.display = 'none';
        reviewView.style.display = 'flex';
        reviewView.innerHTML = '<div class="nca-review-loading" style="display:flex;align-items:center;justify-content:center;gap:8px;padding:48px;color:var(--nca-fg-dim,#9ca3af);font-size:13px;"><div class="nca-loading-dot" style="width:6px;height:6px;border-radius:50%;background:var(--nca-accent,#5b9fff);animation:nca-loading-pulse 1.4s infinite ease-in-out;"></div><div class="nca-loading-dot" style="width:6px;height:6px;border-radius:50%;background:var(--nca-accent,#5b9fff);animation:nca-loading-pulse 1.4s infinite ease-in-out;animation-delay:0.2s;"></div><div class="nca-loading-dot" style="width:6px;height:6px;border-radius:50%;background:var(--nca-accent,#5b9fff);animation:nca-loading-pulse 1.4s infinite ease-in-out;animation-delay:0.4s;"></div><span>正在分析代码，请稍候...</span></div>';
    
        try {
            const review = await 执行代码审查(folder, depth);
            审查结果缓存 = review;
            渲染审查结果到面板(review);
        } catch (e) {
            reviewView.innerHTML = '';
            const errorBox = el("div", {
                class: "nca-review-error",
                style: { padding: "40px", textAlign: "center", color: "var(--nca-text-danger, #ff2255)" },
            });
            errorBox.appendChild(el("div", { text: `⚠ 审查失败: ${e.message}` }));
    
            const backBtn = el("button", {
                text: "← 返回可视化",
                style: {
                    marginTop: "16px", padding: "6px 16px",
                    background: "transparent",
                    border: "1px solid rgba(255,255,255,0.1)",
                    borderRadius: "6px",
                    color: "var(--nca-fg-dim, #9ca3af)",
                    cursor: "pointer", fontSize: "12px",
                    fontFamily: "var(--nca-font-mono, monospace)",
                },
            });
            backBtn.addEventListener("click", 返回可视化);
            errorBox.appendChild(backBtn);
            reviewView.appendChild(errorBox);
        } finally {
            if (reviewBtn) {
                reviewBtn.disabled = false;
                reviewBtn.innerHTML = '<span class="nca-review-icon">🔍</span> 代码审查';
            }
        }
    }
    
    function 渲染审查结果到面板(reviewData) {
        reviewView.innerHTML = "";
    
        const reviewContent = el("div", {
            class: "nca-review-content",
            style: {
                flex: "1",
                overflowY: "auto",
                padding: "16px",
                minHeight: "0",
            },
        });
        渲染审查结果(reviewData, reviewContent);
        reviewView.appendChild(reviewContent);
    
        const actionBar = el("div", {
            class: "nca-review-action-bar",
            style: {
                display: "flex",
                gap: "8px",
                flexShrink: "0",
                borderTop: "1px solid var(--nca-border, rgba(255,255,255,0.08))",
                padding: "12px 16px",
                background: "var(--nca-bg-secondary, #13131f)",
            },
        });
    
        const backBtn = el("button", {
            class: "nca-review-back-btn",
            text: "← 返回可视化",
            style: {
                padding: "6px 16px",
                background: "transparent",
                border: "1px solid rgba(255,255,255,0.1)",
                borderRadius: "6px",
                color: "var(--nca-fg-dim, #9ca3af)",
                cursor: "pointer",
                fontSize: "12px",
                fontFamily: "var(--nca-font-mono, monospace)",
                transition: "all 0.2s",
            },
        });
        backBtn.addEventListener("mouseenter", () => {
            backBtn.style.borderColor = "rgba(255,255,255,0.2)";
            backBtn.style.color = "var(--nca-fg, #e5e7eb)";
        });
        backBtn.addEventListener("mouseleave", () => {
            backBtn.style.borderColor = "rgba(255,255,255,0.1)";
            backBtn.style.color = "var(--nca-fg-dim, #9ca3af)";
        });
        backBtn.addEventListener("click", 返回可视化);
        actionBar.appendChild(backBtn);

        const startBtn = el("button", {
            class: "nca-review-start-optimize-btn",
            text: "✨ 开始优化",
            style: {
                padding: "6px 20px",
                background: "var(--nca-accent, #5b9fff)",
                border: "none",
                borderRadius: "6px",
                color: "#fff",
                cursor: "pointer",
                fontSize: "12px",
                fontFamily: "var(--nca-font-mono, monospace)",
                fontWeight: "600",
                letterSpacing: "0.5px",
                transition: "all 0.2s",
                boxShadow: "0 0 12px rgba(91,159,255,0.2)",
            },
        });
        startBtn.addEventListener("mouseenter", () => {
            startBtn.style.filter = "brightness(1.15)";
            startBtn.style.transform = "translateY(-1px)";
        });
        startBtn.addEventListener("mouseleave", () => {
            startBtn.style.filter = "brightness(1)";
            startBtn.style.transform = "translateY(0)";
        });
        startBtn.addEventListener("click", () => 开始优化());
        actionBar.appendChild(startBtn);

        reviewView.appendChild(actionBar);
    }

    async function 开始优化() {
        if (!审查结果缓存) return;

        const folder = 状态.当前插件名 || 获取有效文件夹(当前会话, 最后文件夹来源);
        if (!folder) {
            Toast.warning('请先选择插件目录');
            return;
        }

        // === 查找/创建关联会话 ===
        try {
            const sessions = await 获取会话列表('visualize');
            const existing = sessions.find(s => s.plugin_folder === folder);

            if (existing) {
                if (viz会话面板 && viz会话面板.setCurrentSessionId) {
                    viz会话面板.setCurrentSessionId(existing.id);
                }
                当前会话 = existing;
            } else {
                const folderName = folder.split(/[\\/]/).pop();
                const newSession = await 创建会话(`${folderName} 优化`, folder, 'visualize');
                if (!newSession) {
                    Toast.error('创建会话失败');
                    return;
                }
                当前会话 = newSession;
                if (viz会话面板) {
                    await viz会话面板.refresh();
                    if (viz会话面板.setCurrentSessionId) {
                        viz会话面板.setCurrentSessionId(newSession.id);
                    }
                }
            }
        } catch (e) {
            Toast.error('准备会话失败: ' + e.message);
            return;
        }

        // === 格式化审查结果为消息 ===
        const message = 格式化审查为消息内容(审查结果缓存);

        // 切换回可视化视图
        返回可视化();

        // 填充输入框
        vizInput.value = message;
        if (vizInput.tagName === 'TEXTAREA') {
            vizInput.style.height = 'auto';
            vizInput.style.height = Math.min(vizInput.scrollHeight, 100) + 'px';
        }
        vizSendBtn.classList.add("active");
        vizInput.focus();
    }
    
    function 返回可视化() {
        reviewView.style.display = 'none';
        vizContainer.style.display = '';
        vizMsgArea.style.display = '';
        modelSwitcher.style.display = '';
        vizInputArea.style.display = '';
    }

    // ─── 注册恢复入口：切回标签时重新渲染缓存的图形 ─────────
    _恢复Fn = async () => {
        // 强制清理可能残留的旧实例（含部分销毁的对象），确保重新渲染
        const oldGraph = getGraph();
        if (oldGraph) {
            try { 销毁图(oldGraph); } catch (_) {}
            setGraph(null);
        }
        const mode = 状态.显示模式;
        const data = mode ? 状态.缓存数据[mode] : null;
        if (data && mode) {
            await 渲染图形(data, mode);
            更新状态栏(data.stats);
            更新模式切换按钮可用状态();
        } else if (状态.缓存数据.meta) {
            // 有 meta 但无具体模式数据，尝试最近分析模式
            const 最近模式 = 获取最近分析模式(状态.缓存数据.meta);
            if (最近模式 && 状态.缓存数据[最近模式]) {
                await 切换显示模式(最近模式);
            }
        } else {
            // 无缓存数据时恢复演示模式
            await 加载演示可视化();
        }
    };

    // ─── 初始引导：检查持久化选择，决定显示历史数据或演示 ────
    const 持久化插件 = 安全存储读(NCA_STORAGE_KEYS.plugin);
    // 等待DOM布局完成后再渲染，避免画布以 0×0 尺寸初始化
    (async () => {
        await new Promise(r => requestAnimationFrame(() => requestAnimationFrame(r)));
        if (持久化插件) {
            加载历史可视化(持久化插件);
        } else {
            加载演示可视化();
        }
    })();

    // 通过事件总线统一同步插件选择状态与"已选择"标签，并实现插件列表与会话的互斥选择
    事件总线.on(事件.插件选择变更, (pluginName) => {
        if (pluginName) {
            最后文件夹来源 = 'plugin';
            清除会话选择();
        }
        同步当前插件名();
    });

    // ─── 创建会话列表面板（侧边栏） ──────────────────────────
    viz会话面板 = 创建会话列表面板({
        type: "visualize",
        container: sidebarContainer,
        showSearch: false,
        showCancelButton: true,
        onCancelClick: () => {
            最后文件夹来源 = null;
            当前会话 = null;
            设置插件文件夹('');
            viz会话面板.clearSelection();
            vizMsgArea.innerHTML = "";
            同步当前插件名();
        },
        onSessionSwitch: (session) => {
            if (session?.plugin_folder) {
                最后文件夹来源 = 'session';
                设置插件文件夹('');
            }
            当前会话 = session || null;
            同步当前插件名();
            加载会话消息(session?.id, vizMsgArea, {
                input: vizInput,
                sendBtn: vizSendBtn,
                msgArea: vizMsgArea,
                附件: viz附件,
                activeTab: 'visualize',
                getSessionId: () => 当前会话?.id || viz会话面板.getCurrentSessionId(),
                getPluginFolder: () => 状态.当前插件名 || 获取有效文件夹(当前会话, 最后文件夹来源),
            });
        },
        onSessionDelete: () => {
            if (!viz会话面板.getCurrentSessionId()) {
                vizMsgArea.innerHTML = "";
                当前会话 = null;
                if (最后文件夹来源 === 'session') 最后文件夹来源 = null;
                同步当前插件名();
            }
        },
    });

    // ─── 输入区交互：发送问答（SSE 流式，必须选择插件目录） ────────────────────────────
    绑定输入事件(vizInput, vizSendBtn, viz附件, async () => {
        await 发送面板消息({
            input: vizInput,
            sendBtn: vizSendBtn,
            msgArea: vizMsgArea,
            附件: viz附件,
            activeTab: 'visualize',
            getSessionId: () => 当前会话?.id || viz会话面板.getCurrentSessionId(),
            getPluginFolder: () => 状态.当前插件名 || 获取有效文件夹(当前会话, 最后文件夹来源),
            onSessionReady: async (session, isNew) => {
                当前会话 = session;
                if (viz会话面板) {
                    if (isNew) await viz会话面板.refresh();
                    if (viz会话面板.setCurrentSessionId) viz会话面板.setCurrentSessionId(session.id);
                }
            },
        });
    });
}
