// ═══════════════════════════════════════════════════════════════
// 可视化面板.js — 功能可视化面板（双模式：文件 / 功能）
// NodeCraft AI — Luxury Terminal / Neo-Noir Hacker
// ═══════════════════════════════════════════════════════════════

import {
    el, NCA_API_BASE, NCA_STORAGE_KEYS, 插件存储键,
} from "./工具函数.js";
import { 事件总线, 事件, 设置插件文件夹 } from "./交互与状态.js";
import { 加载可视化库, 是否已加载可视化库, 创建可视化图, 销毁图, formatSize } from "./可视化引擎.js";
import { 创建文件夹选择器 } from "./文件夹选择器.js";
import { 创建模型切换栏副本 } from "./插件开发面板.js";
import { 创建会话列表面板 } from "./会话列表面板.js";
import { 获取有效文件夹, 加载会话消息, 发送面板消息, 创建面板输入区, 绑定输入事件 } from "./面板会话公共.js";
import { 隐藏演示标签, 加载演示可视化 as _加载演示可视化 } from "./可视化演示.js";
import { 创建HUD覆盖层 } from "./可视化/HUD覆盖层.js";
import { t } from "./i18n.js";

    // ─── 模式颜色配置 ─────────────────────────────────────────────
const 文件模式颜色 = {
    python: '#3b82f6',
    javascript: '#f59e0b',
    config: '#8b5cf6',
    entry: '#06b6d4',
    env: '#ec4899',           // 虚拟环境 — 粉
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

// 面板可能随 renderSidebarUI 重建（如语言切换）而多次构建；模块级退订句柄
// 保证事件总线订阅不随重建累积泄漏（document 级全屏监听退订已移至 可视化/HUD覆盖层.js）
let _上次插件选择退订 = null;

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

    function 清除会话选择() {
        if (当前会话) {
            当前会话 = null;
            if (viz会话面板) viz会话面板.clearSelection();
            vizMsgArea.innerHTML = "";
        }
    }

    function 同步当前插件名() {
        const effectiveFolder = 获取有效文件夹(当前会话, 最后文件夹来源, 'visualize');
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
        }
    }

    // ─── 文件夹选择器 ─────────────────────────────────────────
    const selector = 创建文件夹选择器(
        null,
        () => 获取有效文件夹(当前会话, 最后文件夹来源, 'visualize'),
        null,
        'visualize'
    );
    // selector 不直接挂到 panel，稍后放入统一滚动区 mainArea（见下方主体区域）

    // ─── 状态栏 ───────────────────────────────────────────────
    const vizStatus = el("div", {
        class: "nc-viz-status",
        style: {
            padding: "6px 12px", fontSize: "11px",
            borderBottom: "1px solid", flexShrink: "0",
        },
    });
    vizStatus.textContent = t("visual.no_plugin");
    // vizStatus 同样放入滚动区 mainArea，随内容整体滚动

    // ─── 操作栏 ───────────────────────────────────────────────
    const toolbar = el("div", { class: "nca-viz-toolbar", style: { flexShrink: "0" } });

    const modeToggle = el("div", { class: "nca-viz-mode-toggle" });
    // 模式按钮文字经 data-nca-label + CSS 伪元素渲染（.nca-viz-mode-btn::before），
    // 外部翻译插件碰不到 CSS content，免疫改写（title 保留，被改写危害小）
    const fileBtn = el("button", {
        class: "nca-viz-mode-btn active",
        type: "button",
        "data-nca-label": t("visual.file_mode"),
        title: t("visual.by_structure"),
    });
    const funcBtn = el("button", {
        class: "nca-viz-mode-btn",
        type: "button",
        "data-nca-label": t("visual.func_mode"),
        title: t("visual.by_module"),
    });
    modeToggle.appendChild(fileBtn);
    modeToggle.appendChild(funcBtn);

    const analyzeBtn = el("button", {
        class: "nca-viz-analyze-btn",
        type: "button",
        html: t("visual.analyze_cta"),
    });

    toolbar.appendChild(modeToggle);
    toolbar.appendChild(analyzeBtn);

    fileBtn.addEventListener('click', () => 选择操作模式('file'));
    funcBtn.addEventListener('click', () => 选择操作模式('function'));
    analyzeBtn.addEventListener('click', () => 开始分析());

    // 移除代码审查按钮（可视化界面不提供代码审查，优化插件界面保留），
    // 刷新按钮移至标题栏；「开始分析」按钮固定放在操作栏（模式切换旁），
    // 不再放入文件夹选择器搜索行（选择器默认折叠会导致按钮不可见）
    {
        const rBtn = selector.querySelector(".nc-folder-review-btn");
        const fBtn = selector.querySelector(".nc-folder-reset");
        if (rBtn && fBtn) {
            const toggle = rBtn.parentNode;
            toggle.replaceChild(fBtn, rBtn);
            fBtn.style.marginLeft = 'auto';
        }
    }

    function 设置当前模式(mode) {
        状态.当前模式 = mode;
        fileBtn.classList.toggle('active', mode === 'file');
        funcBtn.classList.toggle('active', mode === 'function');
    }

    // 模式按钮兼具「分析模式选择」与「显示模式切换」：有缓存数据即按对应模式加载显示
    async function 选择操作模式(mode) {
        if (状态.分析中) return;
        设置当前模式(mode);
        if (!状态.当前插件名) return;      // 演示模式：仅更新选中态，不动演示图
        if (状态.显示模式 === mode) return; // 已在显示该模式，无需重渲染
        if (状态.缓存数据[mode]) {
            await 切换显示模式(mode);       // 有缓存 → 按对应数据加载显示
        } else {
            状态.显示模式 = null;           // 无缓存 → 清图并引导点击「开始分析」
            清空图形();
            显示分析引导();
            更新状态栏(null);
        }
    }

    // ─── 主体区域（统一滚动区）：插件选择器 + 状态栏 + 会话列表 + 可视化 + 消息区 ──
    // 模型切换栏与输入区固定在面板底部，不随滚动（见下方 panel.appendChild）
    const mainArea = el("div", {
        class: "nca-panel-main-area",
        style: {
            display: "flex",
            flexDirection: "column",
            flex: "1",
            minHeight: "0",
            overflowY: "auto",
        },
    });
    panel.appendChild(mainArea);

    // 插件选择器与状态栏作为滚动区顶部内容
    mainArea.appendChild(selector);
    mainArea.appendChild(vizStatus);

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
            flexShrink: "0",
        },
    });
    mainArea.appendChild(sidebarContainer);

    // 内容区（可视化图 + 消息）：不再自身滚动，高度随内容撑开，由 mainArea 统一滚动
    const contentArea = el("div", {
        class: "nca-visualize-content",
        style: {
            flex: "1 0 auto",
            display: "flex",
            flexDirection: "column",
            minWidth: "0",
        },
    });
    mainArea.appendChild(contentArea);

    // ─── 可视化容器（含折叠标题栏） ──────────────────────────
    const vizContainerWrapper = el("div", {
        class: "nc-viz-container-wrapper",
    });

    const vizContainerToggle = el("div", { class: "nc-viz-container-toggle" });
    vizContainerToggle.innerHTML = `<span class="arrow">▼</span><span>${t("visual.container_title")}</span>`;
    vizContainerWrapper.appendChild(vizContainerToggle);

    // 操作栏（文件/功能模式切换）作为折叠面板内容的第一行
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

    // ─── HUD 覆盖层元素（已提取至 ./可视化/HUD覆盖层.js）────────
    const {
        hudSearch, hudTitle, hudStats,
        statTotal, statNormal, statError, statWarning,
        hudFullscreen, hudLegend, hudFilters, hudControls, hudCorners,
        detailPanel, tooltip,
        更新HUD统计,
    } = 创建HUD覆盖层(vizContainer, getGraph);

    // ─── 消息区 + 模型栏 + 输入区 ────────────────────────────
    const vizMsgArea = el("div", {
        class: "nca-messages nc-viz-messages",
        style: {
            height: "120px", overflowY: "auto", padding: "8px 12px",
            borderTop: "1px solid rgba(255,255,255,0.06)", flexShrink: "0",
        },
    });
    contentArea.appendChild(vizMsgArea);
    // 模型切换栏与输入区直接挂在 panel 上（滚动区 mainArea 之外），始终固定在底部
    const modelSwitcher = 创建模型切换栏副本(ctx);
    panel.appendChild(modelSwitcher);

    // 输入区域（复用面板公共组件）
    const { inputArea: vizInputArea, input: vizInput, sendBtn: vizSendBtn, 附件: viz附件 } = 创建面板输入区(panel, { placeholder: t("visual.placeholder") });
    panel.appendChild(vizInputArea);

    // ─── 状态栏 / 按钮辅助 ───────────────────────────────────
    function 更新状态栏(stats) {
        const pluginName = 状态.当前插件名 || 获取有效文件夹(当前会话, 最后文件夹来源, 'visualize');
        if (!pluginName) {
            vizStatus.textContent = t("visual.no_plugin");
            return;
        }
        const 模式标签 = 状态.显示模式 === 'function' ? t("visual.mode_function") : (状态.显示模式 === 'file' ? t("visual.mode_file") : t("visual.mode_none"));
        if (!stats) {
            vizStatus.textContent = `📦 ${pluginName} — ${模式标签}`;
            return;
        }
        const 节点数 = stats.total_files != null ? stats.total_files
            : (stats.total_nodes != null ? stats.total_nodes : '-');
        const 异常 = stats.error_count != null ? stats.error_count : 0;
        const 循环 = stats.circular_deps != null ? stats.circular_deps : 0;
        vizStatus.textContent = t("visual.stats", { plugin: pluginName, mode: 模式标签, nodes: 节点数, errors: 异常, cycles: 循环 });
    }

    function 设置分析状态(loading) {
        状态.分析中 = loading;
        if (loading) {
            analyzeBtn.classList.add('loading');
            analyzeBtn.disabled = true;
            analyzeBtn.innerHTML = '<span class="nca-viz-spinner"></span> ' + t("visual.analyzing");
            fileBtn.disabled = true;
            funcBtn.disabled = true;
        } else {
            analyzeBtn.classList.remove('loading');
            analyzeBtn.disabled = false;
            analyzeBtn.innerHTML = t("visual.analyze_cta");
            fileBtn.disabled = false;
            funcBtn.disabled = false;
        }
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
                <div class="nca-viz-empty-title">${t('visual.empty_title')}</div>
                <div class="nca-viz-empty-desc">${t('visual.empty_desc')}</div>
            `,
        });
        vizContainer.appendChild(hint);
    }

    function 清空图形() {
        隐藏演示标签();
        const g = getGraph();
        if (g) 销毁图(g);
        setGraph(null);
        // 仅移除图形相关 DOM，保留 HUD 元素
        detailPanel.classList.remove('open');
        tooltip.classList.remove('visible');
        const hud保留 = new Set([hudSearch, hudTitle, hudStats, hudFullscreen, hudLegend, hudFilters, hudControls, detailPanel, tooltip, ...hudCorners]);
        Array.from(vizContainer.children).forEach(child => {
            if (!hud保留.has(child)) vizContainer.removeChild(child);
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
                    <div class="nca-viz-empty-title">${t('visual.loading3d')}</div>
                    <div class="nca-viz-empty-desc">${t('visual.lib_loading_desc')}</div>
                `,
            });
            vizContainer.appendChild(加载提示);
            const 临时状态 = t('visual.lib_loading_status');
            const 原状态 = vizStatus.textContent;
            vizStatus.textContent = 临时状态;
            try {
                await 加载可视化库();
            } catch (e) {
                清空图形();
                vizStatus.textContent = t('visual.lib_load_fail', { error: e.message });
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
                const 状态文本 = node.status === 'error' ? t('visual.status_error') : node.status === 'warning' ? t('visual.status_warning') : t('visual.status_normal');
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
                            const 文本 = t('visual.edge_issue', { source: sName, target: tName, reason: e.reason });
                            if (e.status === 'error') 关联异常.push(文本);
                            else if (e.status === 'warning') 关联警告.push(文本);
                        }
                    });
                }

                if (node.status === 'error') {
                    const 段落 = [];
                    段落.push(node.reason || t('visual.no_detail'));
                    if (关联异常.length) 段落.push(关联异常.join('\n'));
                    errorContent.textContent = 段落.join('\n');
                    errorSection.style.display = '';
                    if (关联警告.length) {
                        warningContent.textContent = 关联警告.join('\n');
                        warningSection.style.display = '';
                    }
                } else if (node.status === 'warning') {
                    const 段落 = [];
                    段落.push(node.reason || t('visual.no_detail'));
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
                    const 状态文本 = node.status === 'error' ? t('visual.status_error') : node.status === 'warning' ? t('visual.status_warning') : t('visual.status_normal');
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
        vizStatus.textContent = t('visual.loading_history', { plugin: pluginName });

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
            } else {
                显示分析引导();
                更新状态栏(null);
            }
        } catch (e) {
            if (当前版本 !== 加载版本号) return;
            vizStatus.textContent = t('visual.load_fail', { error: e.message });
            显示分析引导();
        }
    }

    // ─── 切换显示模式 ─────────────────────────────────────────
    async function 切换显示模式(mode) {
        const data = 状态.缓存数据[mode];
        if (!data) return;
        设置当前模式(mode);   // 历史加载/标签恢复路径下同步操作栏按钮高亮
        状态.显示模式 = mode;
        await 渲染图形(data, mode);
        更新状态栏(data.stats);
    }

    // ─── 开始分析 ─────────────────────────────────────────────
    async function 开始分析() {
        const 当前版本 = ++加载版本号;
        const pluginName = 状态.当前插件名 || 获取有效文件夹(当前会话, 最后文件夹来源, 'visualize');
        if (!pluginName) {
            vizStatus.textContent = t("visual.select_first");
            return;
        }
        if (状态.分析中) return;

        const mode = 状态.当前模式;
        设置分析状态(true);
        vizStatus.textContent = t("visual.analyzing_status", { mode: mode === 'function' ? t("visual.mode_function") : t("visual.mode_file"), plugin: pluginName });
        状态.当前插件名 = pluginName;

        // 功能模式 AI 分析可能耗时 30-120s，超时中止请求
        const 超时控制器 = new AbortController();
        const 超时句柄 = setTimeout(() => 超时控制器.abort(), 120000);

        try {
            const resp = await fetch(`${NCA_API_BASE}/visualization/analyze`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ plugin_path: pluginName, mode }),
                signal: 超时控制器.signal,
            });
            if (当前版本 !== 加载版本号) return;
            const result = await resp.json().catch(() => ({}));

            if (result && result.status === 'success' && result.data) {
                if (result.data.error) {
                    // 后端 200 但携带 error（AI 输出截断/JSON 解析失败）：不渲染空图，显示错误
                    vizStatus.textContent = t("visual.analyze_fail", { error: result.data.error });
                    return;
                }
                状态.缓存数据[mode] = result.data;
                if (result.meta) {
                    状态.缓存数据.meta = result.meta;
                }

                状态.显示模式 = mode;
                await 渲染图形(result.data, mode);
                更新状态栏(result.data.stats);
            } else {
                const 错误 = (result && (result.error || result.message)) || t("visual.unknown_error");
                vizStatus.textContent = t("visual.analyze_fail", { error: 错误 });
            }
        } catch (e) {
            const 错误 = e && e.name === 'AbortError' ? t("visual.analyze_timeout") : e.message;
            vizStatus.textContent = t("visual.analyze_fail", { error: 错误 });
        } finally {
            clearTimeout(超时句柄);
            设置分析状态(false);
        }
    }

    // ─── 演示模块（已提取至 ./可视化演示.js）──────────────────────
    async function 加载演示可视化() {
        await _加载演示可视化({ 渲染图形, vizStatus, 显示分析引导 });
    }

    // ─── 注册恢复入口：切回标签时重新渲染缓存的图形 ─────────
    _恢复Fn = async () => {
        // 强制清理可能残留的旧实例（含部分销毁的对象），确保重新渲染
        const oldGraph = getGraph();
        if (oldGraph) {
            try { 销毁图(oldGraph); } catch (_) {}
            setGraph(null);
        }
        // 坑5：面板刚从 display:none 切回，等待两帧布局完成再渲染，
        // 避免容器尺寸未稳定时画布以极小尺寸初始化（详见 技术文档/12 坑5）
        await new Promise(r => requestAnimationFrame(() => requestAnimationFrame(r)));
        const mode = 状态.显示模式;
        const data = mode ? 状态.缓存数据[mode] : null;
        if (data && mode) {
            await 渲染图形(data, mode);
            更新状态栏(data.stats);
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
    const 持久化插件 = localStorage.getItem(插件存储键('visualize'));
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
    // 仅响应本界面(visualize)作用域的插件变更，忽略开发/优化界面的选择，保证三界面互不干扰。
    // 面板重建时先退订旧实例的订阅，避免闭包引用旧 DOM 的监听器累积
    if (_上次插件选择退订) { try { _上次插件选择退订(); } catch (_) {} }
    const _on插件选择变更 = (payload) => {
        const ev = (payload && typeof payload === 'object') ? payload : { scope: 'develop', value: payload };
        if ((ev.scope || 'develop') !== 'visualize') return;
        if (ev.value) {
            最后文件夹来源 = 'plugin';
            清除会话选择();
        }
        同步当前插件名();
    };
    事件总线.on(事件.插件选择变更, _on插件选择变更);
    _上次插件选择退订 = () => 事件总线.off(事件.插件选择变更, _on插件选择变更);

    // ─── 创建会话列表面板（侧边栏） ──────────────────────────
    viz会话面板 = 创建会话列表面板({
        type: "visualize",
        container: sidebarContainer,
        showSearch: false,
        showCancelButton: true,
        onCancelClick: () => {
            最后文件夹来源 = null;
            当前会话 = null;
            设置插件文件夹('', 'visualize');
            viz会话面板.clearSelection();
            vizMsgArea.innerHTML = "";
            同步当前插件名();
        },
        onSessionSwitch: (session) => {
            if (session?.plugin_folder) {
                最后文件夹来源 = 'session';
                设置插件文件夹('', 'visualize');
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
                getPluginFolder: () => 状态.当前插件名 || 获取有效文件夹(当前会话, 最后文件夹来源, 'visualize'),
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
            getPluginFolder: () => 状态.当前插件名 || 获取有效文件夹(当前会话, 最后文件夹来源, 'visualize'),
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

// ═══════════════════════════════════════════════════════════════
// Re-export 演示模块，保持外部导入兼容
// ═══════════════════════════════════════════════════════════════
export { 演示数据, 加载演示可视化, 隐藏演示标签, 显示演示标签 } from "./可视化演示.js";
