// ═══════════════════════════════════════════════════════════════
// 优化面板.js — 优化插件面板（folder selector + 会话侧边栏 + 聊天 + 附件）
// NodeCraft AI — Luxury Terminal / Neo-Noir Hacker
// ═══════════════════════════════════════════════════════════════

import {
    el, NCA_STORAGE_KEYS, NCA_API_BASE, 简易Markdown渲染, 移除视觉能力警告, Toast,
} from "./工具函数.js";
import { 状态, 事件总线, 事件, 设置插件文件夹 } from "./交互与状态.js";
import { 创建文件夹选择器 } from "./文件夹选择器.js";
import { 创建附件组件 } from "./附件上传组件.js";
import { 创建模型切换栏副本 } from "./插件开发面板.js";
import { 创建会话列表面板 } from "./会话列表面板.js";

/**
 * 构建"优化插件"面板
 * @param {HTMLElement} panel - 面板容器
 * @param {Object} ctx - 上下文（如 { 显示设置面板Fn }）
 */
export function 构建优化面板(panel, ctx) {
    // ─── 顶部：文件夹选择器 + 状态栏 ─────────────────────────
    const selector = 创建文件夹选择器();
    panel.appendChild(selector);

    const status = el("div", { class: "nc-optimize-status", style: { padding: "6px 12px", fontSize: "11px", color: "#6b7280", borderBottom: "1px solid rgba(255,255,255,0.06)", flexShrink: "0", display: "flex", alignItems: "center", gap: "10px", flexWrap: "wrap" } });
    const statusMain = el("span", { class: "nc-optimize-status-main" });
    const statusCurrent = el("span", { class: "nc-optimize-status-current", style: { display: "none", color: "#9ca3af" } });
    status.appendChild(statusMain);
    status.appendChild(statusCurrent);
    panel.appendChild(status);
    
    // 跟踪当前会话、会话面板折叠状态以及最后操作来源，用于实现插件列表与会话的互斥选择
    let 当前会话 = null;
    let 会话面板折叠 = false;
    let 最后文件夹来源 = null; // 'plugin' | 'session' | null
    let 会话面板 = null;

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
            if (会话面板) 会话面板.clearSelection();
            msgArea.innerHTML = "";
        }
    }

    function 更新状态栏() {
        const effectiveFolder = 获取有效文件夹();
        statusMain.textContent = effectiveFolder ? `已选择: ${effectiveFolder}` : '已选择: 无';
        if (会话面板折叠 && 当前会话) {
            const 标题 = 当前会话.title || "未命名";
            statusCurrent.textContent = `· 当前: ${标题}`;
            statusCurrent.title = 标题;
            statusCurrent.style.display = "";
        } else {
            statusCurrent.textContent = "";
            statusCurrent.removeAttribute("title");
            statusCurrent.style.display = "none";
        }
    }
    更新状态栏();

    // ─── 主体区域：顶部会话列表（可折叠）+ 下方聊天区 ──────────────
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
        class: "nca-optimize-sidebar",
        style: {
            width: "100%",
            borderBottom: "1px solid rgba(255,255,255,0.06)",
            display: "flex",
            flexDirection: "column",
        },
    });
    mainArea.appendChild(sidebarContainer);

    // 右侧内容区（Tab 容器）
    const chatArea = el("div", {
        class: "nca-optimize-chat",
        style: {
            flex: "1",
            display: "flex",
            flexDirection: "column",
            minWidth: "0",
            overflow: "hidden",
        },
    });
    mainArea.appendChild(chatArea);

    // ─── 消息展示区 + 模型栏 ─────────────────────────────────
    const msgArea = el("div", { class: "nca-messages nc-optimize-messages", style: { flex: "1", overflowY: "auto", padding: "12px" } });
    chatArea.appendChild(msgArea);

    // 模型切换栏
    const modelSwitcher = 创建模型切换栏副本(ctx);
    chatArea.appendChild(modelSwitcher);

    // 输入区域
    const inputArea = el("div", { class: "nca-input-area" });
    const inputWrapper = el("div", { class: "nca-input-wrapper" });
    const input = el("textarea", { rows: "1", placeholder: "描述你想要优化的内容..." });
    const sendBtn = el("button", { class: "nca-send-btn", html: "▶", title: "发送" });

    // 附件上传组件
    const 附件 = 创建附件组件(panel, () => {
        sendBtn.classList.toggle("active", input.value.trim().length > 0 || 附件.有附件());
    });
    附件.设置输入区(inputArea);
    inputArea.appendChild(附件.预览区);

    inputWrapper.appendChild(附件.文件按钮);
    inputWrapper.appendChild(附件.文件输入);
    inputWrapper.appendChild(input);
    inputWrapper.appendChild(sendBtn);
    inputArea.appendChild(inputWrapper);
    chatArea.appendChild(inputArea);

    // 自适应高度 + 激活态
    input.addEventListener("input", () => {
        input.style.height = "auto";
        input.style.height = Math.min(input.scrollHeight, 100) + "px";
        sendBtn.classList.toggle("active", input.value.trim().length > 0 || 附件.有附件());
    });

    // ─── 创建会话列表面板（侧边栏） ──────────────────────────
    /**
     * 加载某会话的历史消息到 msgArea
     */
    async function 加载会话历史(sessionId) {
        msgArea.innerHTML = "";
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
                msgArea.appendChild(bubble);
            });
            msgArea.scrollTop = msgArea.scrollHeight;
        } catch (e) {
            console.warn("[节点梦工厂] 加载优化会话历史失败:", e);
        }
    }

    会话面板 = 创建会话列表面板({
        type: "optimize",
        container: sidebarContainer,
        showSearch: false,
        showCancelButton: true,
        onCancelClick: () => {
            最后文件夹来源 = null;
            当前会话 = null;
            设置插件文件夹('');
            会话面板.clearSelection();
            msgArea.innerHTML = "";
            更新状态栏();
        },
        onSessionSwitch: (session) => {
            if (session?.plugin_folder) {
                最后文件夹来源 = 'session';
                设置插件文件夹('');
            }
            当前会话 = session || null;
            更新状态栏();
            加载会话历史(session?.id);
        },
        onSessionDelete: () => {
            // 如果删除后没有活跃会话，清空消息区
            if (!会话面板.getCurrentSessionId()) {
                msgArea.innerHTML = "";
                当前会话 = null;
                if (最后文件夹来源 === 'session') 最后文件夹来源 = null;
            } else {
                当前会话 = 会话面板.getCurrentSession ? 会话面板.getCurrentSession() : 当前会话;
            }
            更新状态栏();
        },
        onToggleCollapse: (collapsed) => {
            会话面板折叠 = !!collapsed;
            更新状态栏();
        },
    });

    // 通过事件总线统一同步“已选择: xxx”标签，并实现插件列表与会话的互斥选择
    事件总线.on(事件.插件选择变更, (pluginName) => {
        if (pluginName) {
            最后文件夹来源 = 'plugin';
            清除会话选择();
        }
        更新状态栏();
    });

    // ─── 发送消息 ─────────────────────────────────────────────
    sendBtn.addEventListener('click', async () => {
        const content = input.value.trim();
        const 当前附件 = 附件.获取附件();
        if (!content && 当前附件.length === 0) return;
        const selectedPlugin = 获取有效文件夹();
        // 硬性阻止：未选择插件目录时禁止发送
        if (!selectedPlugin) {
            Toast.warning("请先选择一个插件目录");
            return;
        }
        // 确保有活跃会话
        let sessionId = 会话面板.getCurrentSessionId();
        if (!sessionId) {
            Toast.warning("请先新建或选择一个会话");
            return;
        }
        msgArea.appendChild(el("div", { class: "nca-msg nca-msg-user" }, [el("div", { class: "nca-msg-content", text: content || `[已附加 ${当前附件.length} 个文件]` })]));
        input.value = '';
        input.style.height = "auto";
        附件.清空();
        移除视觉能力警告(inputArea);
        sendBtn.classList.remove("active");
        msgArea.scrollTop = msgArea.scrollHeight;
        // AI 回复气泡（流式渲染目标）
        const aiBubble = el("div", { class: "nca-msg nca-msg-ai" });
        const aiBody = el("div", { class: "nca-msg-content", html: '<span class="nca-streaming-cursor"></span>' });
        aiBubble.appendChild(aiBody);
        msgArea.appendChild(aiBubble);
        msgArea.scrollTop = msgArea.scrollHeight;
        try {
            sendBtn.disabled = true;
            const requestBody = {
                message: content,
                session_id: sessionId,
                plugin_context: selectedPlugin,
                model_source: 状态.模型来源 || 'api',
                activeTab: 'optimize',
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
                            msgArea.scrollTop = msgArea.scrollHeight;
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
        }
        finally { sendBtn.disabled = false; msgArea.scrollTop = msgArea.scrollHeight; }
    });
    input.addEventListener('keydown', (e) => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); sendBtn.click(); } });
}
