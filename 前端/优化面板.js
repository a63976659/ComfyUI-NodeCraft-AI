// ═══════════════════════════════════════════════════════════════
// 优化面板.js — 优化插件面板（folder selector + 会话侧边栏 + 聊天 + 附件）
// NodeCraft AI — Luxury Terminal / Neo-Noir Hacker
// ═══════════════════════════════════════════════════════════════

import {
    el, Toast,
} from "./工具函数.js";
import { 事件总线, 事件, 设置插件文件夹, 获取会话列表, 创建会话 } from "./交互与状态.js";
import { 创建文件夹选择器 } from "./文件夹选择器.js";
import { 创建附件组件 } from "./附件上传组件.js";
import { 创建模型切换栏副本 } from "./插件开发面板.js";
import { 创建会话列表面板 } from "./会话列表面板.js";
import { 执行代码审查, 渲染审查结果, 格式化审查为消息内容, 显示审查深度下拉菜单 } from "./代码审查面板.js";
import { 获取有效文件夹, 加载会话消息, 发送面板消息 } from "./面板会话公共.js";

/**
 * 构建"优化插件"面板
 * @param {HTMLElement} panel - 面板容器
 * @param {Object} ctx - 上下文（如 { 显示设置面板Fn }）
 */
export function 构建优化面板(panel, ctx) {
    // ─── 顶部：文件夹选择器 + 状态栏 ─────────────────────────
    const status = el("div", { class: "nc-optimize-status", style: { padding: "6px 12px", fontSize: "11px", color: "#6b7280", borderBottom: "1px solid rgba(255,255,255,0.06)", flexShrink: "0", display: "flex", alignItems: "center", gap: "10px", flexWrap: "wrap" } });
    const statusMain = el("span", { class: "nc-optimize-status-main" });
    const statusCurrent = el("span", { class: "nc-optimize-status-current", style: { display: "none", color: "#9ca3af" } });
    status.appendChild(statusMain);
    status.appendChild(statusCurrent);

    // 跟踪当前会话、会话面板折叠状态以及最后操作来源，用于实现插件列表与会话的互斥选择
    let 当前会话 = null;
    let 会话面板折叠 = false;
    let 最后文件夹来源 = null; // 'plugin' | 'session' | null
    let 会话面板 = null;
    let 当前视图模式 = 'chat'; // 'chat' | 'review'
    let 审查结果缓存 = null;

    function 清除会话选择() {
        if (当前会话) {
            当前会话 = null;
            if (会话面板) 会话面板.clearSelection();
            msgArea.innerHTML = "";
            msgArea.appendChild(el("div", { class: "nca-empty-state", text: "请先选择一个插件目录" }));
        }
    }

    function 更新状态栏() {
        const effectiveFolder = 获取有效文件夹(当前会话, 最后文件夹来源);
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

    // 创建选择器（现在可以安全调用获取有效文件夹了，当前会话 已初始化）
    const selector = 创建文件夹选择器(null, () => 获取有效文件夹(当前会话, 最后文件夹来源), (folder) => {
        if (!folder) {
            Toast.warning("请先选择插件目录");
            return;
        }
        const reviewBtn = selector.querySelector(".nc-folder-review-btn");
        显示审查深度下拉菜单(reviewBtn, async (depth) => {
            await 触发代码审查(depth);
        });
    });
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

    panel.appendChild(status);

    // ─── 主体区域：双视图容器（chatView + reviewView） ──────────────
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

    // 对话视图容器（包裹会话列表 + 聊天区）
    const chatView = el("div", {
        class: "nca-optimize-chat-view",
        style: {
            display: "flex",
            flexDirection: "column",
            flex: "1",
            minHeight: "0",
            overflow: "hidden",
        },
    });
    mainArea.appendChild(chatView);

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
    chatView.appendChild(sidebarContainer);

    // 聊天区（消息 + 模型栏 + 输入）
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
    chatView.appendChild(chatArea);

    // 审查视图容器（初始隐藏，与 chatView 互斥显示）
    const reviewView = el("div", {
        class: "nca-optimize-review-view",
        style: {
            display: "none",
            flexDirection: "column",
            flex: "1",
            minHeight: "0",
            overflow: "hidden",
        },
    });
    mainArea.appendChild(reviewView);

    // ─── 消息展示区 + 模型栏 ─────────────────────────────────
    const msgArea = el("div", { class: "nca-messages nc-optimize-messages", style: { flex: "1", overflowY: "auto", padding: "12px" } });
    chatArea.appendChild(msgArea);

    // 空状态提示
    msgArea.appendChild(el("div", { class: "nca-empty-state", text: "请先选择一个插件目录" }));

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
            加载会话消息(session?.id, msgArea, {
                input, sendBtn, msgArea, 附件,
                activeTab: 'optimize',
                getSessionId: () => 当前会话?.id || 会话面板.getCurrentSessionId(),
                getPluginFolder: () => 获取有效文件夹(当前会话, 最后文件夹来源),
            });
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

    // ─── 双视图切换 + 代码审查 + 开始优化 ──────────────────────
    function 切换到审查视图(reviewData) {
        审查结果缓存 = reviewData;
        当前视图模式 = 'review';
        reviewView.style.display = 'flex';
        chatView.style.display = 'none';
        reviewView.innerHTML = '';
        渲染审查结果到面板(reviewData);
    }

    function 切换到对话视图() {
        当前视图模式 = 'chat';
        reviewView.style.display = 'none';
        chatView.style.display = 'flex';
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
            text: "← 返回对话",
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
        backBtn.addEventListener("click", () => 切换到对话视图());
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

    async function 触发代码审查(depth) {
        const folder = 获取有效文件夹(当前会话, 最后文件夹来源);
        if (!folder) {
            Toast.warning("请先选择插件目录");
            return;
        }

        const reviewBtn = selector.querySelector(".nc-folder-review-btn");
        if (reviewBtn) {
            reviewBtn.disabled = true;
            reviewBtn.innerHTML = '<span class="nca-review-loading-spinner"></span> 审查中...';
        }

        当前视图模式 = 'review';
        reviewView.style.display = 'flex';
        chatView.style.display = 'none';
        reviewView.innerHTML = '<div class="nca-review-loading" style="display:flex;align-items:center;justify-content:center;gap:8px;padding:48px;color:var(--nca-fg-dim,#9ca3af);font-size:13px;"><div class="nca-loading-dot" style="width:6px;height:6px;border-radius:50%;background:var(--nca-accent,#5b9fff);animation:nca-loading-pulse 1.4s infinite ease-in-out;"></div><div class="nca-loading-dot" style="width:6px;height:6px;border-radius:50%;background:var(--nca-accent,#5b9fff);animation:nca-loading-pulse 1.4s infinite ease-in-out;animation-delay:0.2s;"></div><div class="nca-loading-dot" style="width:6px;height:6px;border-radius:50%;background:var(--nca-accent,#5b9fff);animation:nca-loading-pulse 1.4s infinite ease-in-out;animation-delay:0.4s;"></div><span>正在分析代码，请稍候...</span></div>';

        try {
            const review = await 执行代码审查(folder, depth);
            切换到审查视图(review);
        } catch (e) {
            Toast.error(`审查失败: ${e.message || e}`);
            切换到对话视图();
        } finally {
            if (reviewBtn) {
                reviewBtn.disabled = false;
                reviewBtn.innerHTML = '<span class="nca-review-icon">🔍</span> 代码审查';
            }
        }
    }

    async function 开始优化() {
        if (!审查结果缓存) return;

        const folder = 获取有效文件夹(当前会话, 最后文件夹来源);
        if (!folder) {
            Toast.warning('请先选择插件目录');
            return;
        }

        // === 查找/创建关联会话 ===
        try {
            const sessions = await 获取会话列表('optimize');
            const existing = sessions.find(s => s.plugin_folder === folder);

            if (existing) {
                // 已有关联会话，切换到它
                if (会话面板 && 会话面板.setCurrentSessionId) {
                    会话面板.setCurrentSessionId(existing.id);
                }
                当前会话 = existing;
            } else {
                // 没有关联会话，创建新的
                const folderName = folder.split(/[\\/]/).pop();
                const newSession = await 创建会话(`${folderName} 优化`, folder, 'optimize');
                if (!newSession) {
                    Toast.error('创建会话失败');
                    return;
                }
                当前会话 = newSession;
                if (会话面板) {
                    await 会话面板.refresh();
                    if (会话面板.setCurrentSessionId) {
                        会话面板.setCurrentSessionId(newSession.id);
                    }
                }
            }
        } catch (e) {
            Toast.error('准备会话失败: ' + e.message);
            return;
        }

        // === 填充输入框 ===
        // 格式化审查结果为消息文本
        const message = 格式化审查为消息内容(审查结果缓存);

        // 切换到对话视图
        切换到对话视图();

        // 将内容填充到输入框
        input.value = message;
        // 如果输入框是 textarea，自动调整高度
        if (input.tagName === 'TEXTAREA') {
            input.style.height = 'auto';
            input.style.height = Math.min(input.scrollHeight, 100) + 'px';
        }
        // 激活发送按钮
        sendBtn.classList.add("active");
        // 聚焦输入框
        input.focus();
    }

    // ─── 发送消息 ─────────────────────────────────────────────
    sendBtn.addEventListener('click', async () => {
        await 发送面板消息({
            input, sendBtn, msgArea, 附件,
            activeTab: 'optimize',
            getSessionId: () => 当前会话?.id || 会话面板.getCurrentSessionId(),
            getPluginFolder: () => 获取有效文件夹(当前会话, 最后文件夹来源),
            onSessionReady: async (session, isNew) => {
                当前会话 = session;
                if (会话面板) {
                    if (isNew) await 会话面板.refresh();
                    if (会话面板.setCurrentSessionId) 会话面板.setCurrentSessionId(session.id);
                }
                更新状态栏();
            },
        });
    });
    input.addEventListener('keydown', (e) => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); sendBtn.click(); } });
}
