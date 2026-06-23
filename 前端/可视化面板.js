// ═══════════════════════════════════════════════════════════════
// 可视化面板.js — 功能可视化面板（双模式：文件 / 功能）
// NodeCraft AI — Luxury Terminal / Neo-Noir Hacker
// ═══════════════════════════════════════════════════════════════

import {
    el, NCA_API_BASE, NCA_STORAGE_KEYS, 简易Markdown渲染, 移除视觉能力警告, Toast,
} from "./工具函数.js";
import { 状态 as 全局状态, 事件总线, 事件, 设置插件文件夹 } from "./交互与状态.js";
import { 加载可视化库, 是否已加载可视化库, 创建可视化图, 销毁图 } from "./可视化引擎.js";
import { 创建文件夹选择器 } from "./文件夹选择器.js";
import { 创建附件组件 } from "./附件上传组件.js";
import { 创建模型切换栏副本 } from "./插件开发面板.js";
import { 创建会话列表面板 } from "./会话列表面板.js";

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
export function 构建可视化面板(panel, getGraph, setGraph, ctx) {
    // ─── 局部状态 ─────────────────────────────────────────────
    const 状态 = {
        当前插件名: null,
        当前模式: 'file',           // 操作栏选中模式
        显示模式: null,             // 当前图形显示的模式
        分析中: false,
        缓存数据: { meta: null, file: null, function: null },
    };

    // 跟踪最后操作来源、当前会话以及会话面板实例，用于实现插件列表与会话的互斥选择
    let 最后文件夹来源 = null; // 'plugin' | 'session' | null
    let 当前会话 = null;
    let viz会话面板 = null;

    function 获取有效文件夹() {
        const pluginFolder = localStorage.getItem(NCA_STORAGE_KEYS.plugin) || "";
        const sessionFolder = 当前会话?.plugin_folder || "";
        if (!pluginFolder && !sessionFolder) return null;
        if (pluginFolder && sessionFolder) {
            return 最后文件夹来源 === 'session' ? sessionFolder : pluginFolder;
        }
        return pluginFolder || sessionFolder;
    }

    function 清除会话选择() {
        if (当前会话) {
            当前会话 = null;
            if (viz会话面板) viz会话面板.clearSelection();
            vizMsgArea.innerHTML = "";
        }
    }

    function 同步当前插件名() {
        const effectiveFolder = 获取有效文件夹();
        if (effectiveFolder === 状态.当前插件名) return;
        if (effectiveFolder) {
            加载历史可视化(effectiveFolder);
        } else {
            状态.当前插件名 = null;
            状态.缓存数据 = { meta: null, file: null, function: null };
            状态.显示模式 = null;
            清空图形();
            显示分析引导();
            更新状态栏(null);
            更新模式切换按钮可用状态();
        }
    }

    // ─── 文件夹选择器 ─────────────────────────────────────────
    const selector = 创建文件夹选择器();
    panel.appendChild(selector);

    // ─── 状态栏 ───────────────────────────────────────────────
    const vizStatus = el("div", {
        class: "nc-viz-status",
        style: {
            padding: "6px 12px", fontSize: "11px", color: "#6b7280",
            borderBottom: "1px solid rgba(255,255,255,0.06)", flexShrink: "0",
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
    panel.appendChild(toolbar);

    fileBtn.addEventListener('click', () => 选择操作模式('file'));
    funcBtn.addEventListener('click', () => 选择操作模式('function'));
    analyzeBtn.addEventListener('click', () => 开始分析());

    function 选择操作模式(mode) {
        if (状态.分析中) return;
        状态.当前模式 = mode;
        fileBtn.classList.toggle('active', mode === 'file');
        funcBtn.classList.toggle('active', mode === 'function');
    }

    // ─── 主体区域：顶部会话列表（可折叠）+ 下方（可视化 + 消息 + 输入）──
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
            minWidth: "0",
            overflow: "hidden",
        },
    });
    mainArea.appendChild(contentArea);

    // ─── 可视化容器（含左上角浮层） ──────────────────────────
    const vizContainer = el("div", {
        class: "nc-viz-container",
        style: { flex: "1", minHeight: "200px", position: "relative", overflow: "hidden" },
    });
    contentArea.appendChild(vizContainer);

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

    // ─── 消息区 + 模型栏 + 输入区 ────────────────────────────
    const vizMsgArea = el("div", {
        class: "nca-messages nc-viz-messages",
        style: {
            height: "120px", overflowY: "auto", padding: "8px 12px",
            borderTop: "1px solid rgba(255,255,255,0.06)", flexShrink: "0",
        },
    });
    contentArea.appendChild(vizMsgArea);
    contentArea.appendChild(创建模型切换栏副本(ctx));

    const vizInputArea = el("div", { class: "nca-input-area" });
    const vizInputWrapper = el("div", { class: "nca-input-wrapper" });
    const vizInput = el("textarea", { rows: "1", placeholder: "询问关于此插件的问题..." });
    const vizSendBtn = el("button", { class: "nca-send-btn", html: "▶", title: "发送" });

    const viz附件 = 创建附件组件(panel, () => {
        vizSendBtn.classList.toggle("active", vizInput.value.trim().length > 0 || viz附件.有附件());
    });
    viz附件.设置输入区(vizInputArea);
    vizInputArea.appendChild(viz附件.预览区);

    vizInputWrapper.appendChild(viz附件.文件按钮);
    vizInputWrapper.appendChild(viz附件.文件输入);
    vizInputWrapper.appendChild(vizInput);
    vizInputWrapper.appendChild(vizSendBtn);
    vizInputArea.appendChild(vizInputWrapper);
    contentArea.appendChild(vizInputArea);

    vizInput.addEventListener("input", () => {
        vizInput.style.height = "auto";
        vizInput.style.height = Math.min(vizInput.scrollHeight, 100) + "px";
        vizSendBtn.classList.toggle("active", vizInput.value.trim().length > 0 || viz附件.有附件());
    });

    function vizFormatSize(bytes) {
        if (!bytes) return '0B';
        if (bytes < 1024) return `${bytes}B`;
        if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)}KB`;
        return `${(bytes / (1024 * 1024)).toFixed(1)}MB`;
    }

    // ─── 状态栏 / 按钮辅助 ───────────────────────────────────
    function 更新状态栏(stats) {
        const pluginName = 状态.当前插件名 || 获取有效文件夹();
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
        const fileTs = meta.file_mode_updated_at || 0;
        const funcTs = meta.function_mode_updated_at || 0;
        if (!fileTs && !funcTs) return null;
        return funcTs > fileTs ? 'function' : 'file';
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
        const g = getGraph();
        if (g) 销毁图(g);
        setGraph(null);
        // 仅移除图形相关 DOM，保留 overlay
        Array.from(vizContainer.children).forEach(child => {
            if (child !== overlay) vizContainer.removeChild(child);
        });
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
                const 大小行 = node.size != null ? ` — ${vizFormatSize(node.size)}` : '';
                const 类别 = node.type || node.category || '';
                vizMsgArea.appendChild(el("div", {
                    class: "nca-msg nca-msg-system",
                    html: `<strong>${node.name || node.id}</strong> (${类别})${大小行}<br>路径: ${node.id}`,
                }));
                vizMsgArea.scrollTop = vizMsgArea.scrollHeight;
            },
        });

        if (graph) {
            try {
                graph.nodeColor(node => 颜色映射[node.type] || 颜色映射[node.category] || 默认色);
                if (mode === 'function') {
                    graph.linkLabel(link => link.label || link.relation || '');
                    graph.linkDirectionalParticles(link => (link.label || link.relation) ? 2 : 0);
                }
            } catch (e) { /* 图实例尚未支持时忽略 */ }
        }
        setGraph(graph);
    }

    // ─── 加载历史可视化数据 ───────────────────────────────────
    async function 加载历史可视化(pluginName) {
        状态.当前插件名 = pluginName;
        状态.缓存数据 = { meta: null, file: null, function: null };
        状态.显示模式 = null;
        清空图形();
        vizStatus.textContent = `🔍 加载 ${pluginName} 历史数据...`;

        try {
            const url = `${NCA_API_BASE}/visualization/load?plugin_path=${encodeURIComponent(pluginName)}`;
            const resp = await fetch(url);
            const json = await resp.json().catch(() => ({}));
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
        const pluginName = 状态.当前插件名 || 获取有效文件夹();
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
            const result = await resp.json().catch(() => ({}));

            if (result && result.status === 'success' && result.data) {
                状态.缓存数据[mode] = result.data;
                if (!状态.缓存数据.meta) 状态.缓存数据.meta = {};
                状态.缓存数据.meta[`${mode}_mode_updated_at`] = Date.now();

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

    // 初始引导
    显示分析引导();

    // 通过事件总线统一同步插件选择状态与"已选择"标签，并实现插件列表与会话的互斥选择
    事件总线.on(事件.插件选择变更, (pluginName) => {
        if (pluginName) {
            最后文件夹来源 = 'plugin';
            清除会话选择();
        }
        同步当前插件名();
    });

    // ─── 创建会话列表面板（侧边栏） ──────────────────────────
    async function 加载可视化会话历史(sessionId) {
        vizMsgArea.innerHTML = "";
        if (!sessionId) return;
        try {
            const res = await fetch(`${NCA_API_BASE}/sessions/${sessionId}/messages`);
            if (!res.ok) return;
            const data = await res.json();
            const messages = data.messages || [];
            messages.forEach(msg => {
                const isUser = msg.role === "user";
                const bubble = el("div", { class: `nca-msg ${isUser ? "nca-msg-user" : "nca-msg-ai"}` });
                const body = el("div", { class: "nca-msg-content" });
                body.innerHTML = isUser ? (msg.content || "") : 简易Markdown渲染(msg.content || "");
                bubble.appendChild(body);
                vizMsgArea.appendChild(bubble);
            });
            vizMsgArea.scrollTop = vizMsgArea.scrollHeight;
        } catch (e) {
            console.warn("[节点梦工厂] 加载可视化会话历史失败:", e);
        }
    }

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
            加载可视化会话历史(session?.id);
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
    vizSendBtn.addEventListener('click', async () => {
        const selectedPlugin = 状态.当前插件名 || 获取有效文件夹() || "";
        // 硬性阻止：未选择插件目录时禁止发送
        if (!selectedPlugin) {
            Toast.warning("请先选择一个插件目录");
            return;
        }
        const content = vizInput.value.trim();
        const 当前附件 = viz附件.获取附件();
        if (!content && 当前附件.length === 0) return;
        // 确保有活跃会话
        const vizSessionId = viz会话面板.getCurrentSessionId();
        if (!vizSessionId) {
            Toast.warning("请先新建或选择一个会话");
            return;
        }
        const 显示文本 = content || `[已附加 ${当前附件.length} 个文件]`;
        vizMsgArea.appendChild(el("div", { class: "nca-msg nca-msg-user" }, [
            el("div", { class: "nca-msg-content", text: 显示文本 }),
        ]));
        vizInput.value = '';
        vizInput.style.height = "auto";
        viz附件.清空();
        移除视觉能力警告(vizInputArea);
        vizSendBtn.classList.remove("active");
        vizMsgArea.scrollTop = vizMsgArea.scrollHeight;

        // AI 回复气泡（流式渲染目标）
        const aiBubble = el("div", { class: "nca-msg nca-msg-ai" });
        const aiBody = el("div", { class: "nca-msg-content", html: '<span class="nca-streaming-cursor"></span>' });
        aiBubble.appendChild(aiBody);
        vizMsgArea.appendChild(aiBubble);
        vizMsgArea.scrollTop = vizMsgArea.scrollHeight;
        try {
            vizSendBtn.disabled = true;
            const requestBody = {
                message: content,
                session_id: vizSessionId,
                plugin_context: selectedPlugin,
                model_source: 全局状态.模型来源 || 'api',
                activeTab: 'visualize',
            };
            if (当前附件.length > 0) requestBody.attachments = 当前附件;
            const response = await fetch(`${NCA_API_BASE}/chat-stream`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(requestBody),
            });
            if (!response.ok) {
                let errorMsg = `HTTP ${response.status}`;
                try { const errData = await response.json(); if (errData.error) errorMsg = errData.error; } catch (_) {}
                throw new Error(errorMsg);
            }
            const reader = response.body.getReader();
            const decoder = new TextDecoder();
            let fullContent = '';
            let buffer = '';
            let hasError = false;
            while (true) {
                const { done, value } = await reader.read();
                if (done) break;
                buffer += decoder.decode(value, { stream: true });
                const lines = buffer.split('\n');
                buffer = lines.pop();
                for (const line of lines) {
                    if (!line.startsWith('data: ')) continue;
                    try {
                        const evt = JSON.parse(line.slice(6));
                        if (evt.error) { hasError = true; fullContent += `\n⚠ 错误: ${evt.error}`; break; }
                        if (evt.done) break;
                        if (evt.content) {
                            fullContent += evt.content;
                            aiBody.innerHTML = 简易Markdown渲染(fullContent) + '<span class="nca-streaming-cursor"></span>';
                            vizMsgArea.scrollTop = vizMsgArea.scrollHeight;
                        }
                    } catch (_) { /* 跳过无法解析的行 */ }
                }
                if (hasError) break;
            }
            aiBody.innerHTML = 简易Markdown渲染(fullContent || "（无回复）");
        } catch (e) {
            aiBody.innerHTML = '';
            aiBubble.classList.remove('nca-msg-ai');
            aiBubble.classList.add('nca-msg-system');
            aiBody.textContent = `❌ 网络错误: ${e.message}`;
        } finally {
            vizSendBtn.disabled = false;
            vizMsgArea.scrollTop = vizMsgArea.scrollHeight;
        }
    });
    vizInput.addEventListener('keydown', (e) => {
        if (e.key === 'Enter' && !e.shiftKey) {
            e.preventDefault();
            vizSendBtn.click();
        }
    });
}
