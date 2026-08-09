// ═══════════════════════════════════════════════════════════════
// 优化面板.js — 优化插件面板（folder selector + 会话侧边栏 + 聊天 + 附件）
// NodeCraft AI — Luxury Terminal / Neo-Noir Hacker
// ═══════════════════════════════════════════════════════════════

import {
    el, Toast, LOGO_SVG,
} from "./工具函数.js";
import { 事件总线, 事件, 设置插件文件夹, 获取会话列表, 创建会话, 请求 } from "./交互与状态.js";
import { 创建文件夹选择器 } from "./文件夹选择器.js";
import { 创建模型切换栏副本 } from "./插件开发面板.js";
import { 创建会话列表面板 } from "./会话列表面板.js";
import { 执行代码审查, 渲染审查结果, 格式化审查为消息内容, 显示审查深度下拉菜单 } from "./代码审查面板.js";
import { 获取有效文件夹, 加载会话消息, 发送面板消息, 创建面板输入区, 绑定输入事件 } from "./面板会话公共.js";
import { t } from "./i18n.js";

/**
 * 构建"优化插件"面板
 * @param {HTMLElement} panel - 面板容器
 * @param {Object} ctx - 上下文（如 { 显示设置面板Fn }）
 */
// 面板可能随 renderSidebarUI 重建（如语言切换）而多次构建；模块级退订句柄
// 保证事件总线订阅不随重建累积泄漏
let _上次插件选择退订 = null;

// ─── 优化欢迎页：无会话内容时的默认背景 + 知识库优化方法快捷入口 ───
// 可点击项来自 知识库/优化插件 目录（后端 /optimize-methods 接口动态扫描），
// 点击后将提示词快速填入输入框，供用户确认后发送
const _优化分类图标 = { "性能优化": "⚡", "代码质量": "◇", "优化流程": "▸" };

// 按钮文案双语映射：后端返回的 display 为中文硬编码，此表将文档名映射到 i18n 键，
// 未配置的文档回退到后端 display（与 _优化方法展示名 键集一致性由 测试/test_优化方法映射一致性.py 断言）
const _优化方法文案键 = {
    "重复代码率降低方法论": "optimize.method_dedupe",
    "项目规模分级优化方案": "optimize.method_by_scale",
    "ComfyUI性能调优实战": "optimize.method_perf",
    "性能基准与Profiling指南": "optimize.method_benchmark",
    "启动与导入优化": "optimize.method_startup",
    "复杂度与大文件拆分": "optimize.method_split",
};

function 渲染优化欢迎页(msgArea, input) {
    msgArea.innerHTML = "";
    const actions = el("div", { class: "nca-quick-actions" });
    const welcome = el("div", { class: "nca-welcome" }, [
        el("div", { class: "nca-welcome-icon", html: LOGO_SVG }),
        el("h3", { text: t("brand.name") }),
        el("p", { text: t("optimize.welcome_subtitle") }),
        actions,
    ]);
    msgArea.appendChild(welcome);
    请求("GET", "/optimize-methods").then((data) => {
        // 面板可能已重建或已切到会话视图，欢迎页被移除时不再填充
        if (!actions.isConnected) return;
        (data.methods || []).forEach((m) => {
            // 按钮文案优先取 i18n 双语文案，回退到后端 display；提示词仍用文档原名（name）以命中知识库
            const 文案键 = _优化方法文案键[m.name];
            const btn = el("button", { class: "nca-quick-action" }, [
                el("span", { class: "qa-icon", text: _优化分类图标[m.category] || "◆" }),
                el("span", { text: 文案键 ? t(文案键) : (m.display || m.name) }),
            ]);
            btn.addEventListener("click", () => {
                input.value = t("optimize.qa_prompt", { name: m.name });
                input.dispatchEvent(new Event("input"));
                input.focus();
            });
            actions.appendChild(btn);
        });
    }).catch(() => { /* 获取失败时仅显示欢迎页，不影响正常使用 */ });
}

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
            渲染优化欢迎页(msgArea, input);
        }
    }

    function 更新状态栏() {
        const effectiveFolder = 获取有效文件夹(当前会话, 最后文件夹来源, 'optimize');
        statusMain.textContent = effectiveFolder ? t('optimize.selected', { folder: effectiveFolder }) : t('optimize.selected', { folder: t('common.none') });
        if (会话面板折叠 && 当前会话) {
            const 标题 = 当前会话.title || t("session.untitled");
            statusCurrent.textContent = t('optimize.current_session', { title: 标题 });
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
    const selector = 创建文件夹选择器(null, () => 获取有效文件夹(当前会话, 最后文件夹来源, 'optimize'), (folder) => {
        if (!folder) {
            Toast.warning(t('optimize.select_plugin_dir'));
            return;
        }
        const reviewBtn = selector.querySelector(".nc-folder-review-btn");
        显示审查深度下拉菜单(reviewBtn, async (depth) => {
            await 触发代码审查(depth);
        });
    }, 'optimize');
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

    // 模型切换栏
    const modelSwitcher = 创建模型切换栏副本(ctx);
    chatArea.appendChild(modelSwitcher);

    // 输入区域（复用面板公共组件）
    const { inputArea, input, sendBtn, 附件 } = 创建面板输入区(panel, { placeholder: t("optimize.placeholder") });
    chatArea.appendChild(inputArea);

    // 无会话内容时的默认背景：欢迎页 + 优化方法快捷入口（需在 input 创建后渲染）
    渲染优化欢迎页(msgArea, input);

    // ─── 创建会话列表面板（侧边栏） ──────────────────────────
    会话面板 = 创建会话列表面板({
        type: "optimize",
        container: sidebarContainer,
        showSearch: false,
        showCancelButton: true,
        onCancelClick: () => {
            最后文件夹来源 = null;
            当前会话 = null;
            设置插件文件夹('', 'optimize');
            // 会话已关闭：上下文指示器回到空闲占位态（显示 —，避免残留上个会话数值）
            事件总线.emit(事件.上下文健康更新, { idle: true });
            会话面板.clearSelection();
            渲染优化欢迎页(msgArea, input);
            更新状态栏();
        },
        onSessionSwitch: (session) => {
            if (session?.plugin_folder) {
                最后文件夹来源 = 'session';
                设置插件文件夹('', 'optimize');
            }
            当前会话 = session || null;
            更新状态栏();
            加载会话消息(session?.id, msgArea, {
                input, sendBtn, msgArea, 附件,
                activeTab: 'optimize',
                getSessionId: () => 当前会话?.id || 会话面板.getCurrentSessionId(),
                getPluginFolder: () => 获取有效文件夹(当前会话, 最后文件夹来源, 'optimize'),
            });
        },
        onSessionDelete: () => {
            // 如果删除后没有活跃会话，清空消息区
            if (!会话面板.getCurrentSessionId()) {
                msgArea.innerHTML = "";
                当前会话 = null;
                if (最后文件夹来源 === 'session') 最后文件夹来源 = null;
                // 当前会话已删除：上下文指示器回到空闲占位态（同 develop 面板删除行为）
                事件总线.emit(事件.上下文健康更新, { idle: true });
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
    // 仅响应本界面(optimize)作用域的插件变更，忽略开发/可视化界面的选择，保证三界面互不干扰。
    // 面板重建时先退订旧实例的订阅，避免闭包引用旧 DOM 的监听器累积
    if (_上次插件选择退订) { try { _上次插件选择退订(); } catch (_) {} }
    const _on插件选择变更 = (payload) => {
        const ev = (payload && typeof payload === 'object') ? payload : { scope: 'develop', value: payload };
        if ((ev.scope || 'develop') !== 'optimize') return;
        if (ev.value) {
            最后文件夹来源 = 'plugin';
            清除会话选择();
        }
        更新状态栏();
    };
    事件总线.on(事件.插件选择变更, _on插件选择变更);
    _上次插件选择退订 = () => 事件总线.off(事件.插件选择变更, _on插件选择变更);

    // 加载会话消息的公共参数集；setCurrentSessionId 不触发 onSessionSwitch，
    // 「开始优化」匹配/创建会话后需手动复用同一参数集加载历史，保证消息区与实际会话一致
    const _会话加载参数 = () => ({
        input, sendBtn, msgArea, 附件,
        activeTab: 'optimize',
        getSessionId: () => 当前会话?.id || 会话面板.getCurrentSessionId(),
        getPluginFolder: () => 获取有效文件夹(当前会话, 最后文件夹来源, 'optimize'),
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
            text: t("optimize.back"),
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
            text: t("optimize.start"),
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
        const folder = 获取有效文件夹(当前会话, 最后文件夹来源, 'optimize');
        if (!folder) {
            Toast.warning(t('optimize.select_plugin_dir'));
            return;
        }

        const reviewBtn = selector.querySelector(".nc-folder-review-btn");
        if (reviewBtn) {
            reviewBtn.disabled = true;
            reviewBtn.innerHTML = '<span class="nca-review-loading-spinner"></span> ' + t('optimize.reviewing');
        }

        当前视图模式 = 'review';
        reviewView.style.display = 'flex';
        chatView.style.display = 'none';
        reviewView.innerHTML = '<div class="nca-review-loading" style="display:flex;align-items:center;justify-content:center;gap:8px;padding:48px;color:var(--nca-fg-dim,#9ca3af);font-size:13px;"><div class="nca-loading-dot" style="width:6px;height:6px;border-radius:50%;background:var(--nca-accent,#5b9fff);animation:nca-loading-pulse 1.4s infinite ease-in-out;"></div><div class="nca-loading-dot" style="width:6px;height:6px;border-radius:50%;background:var(--nca-accent,#5b9fff);animation:nca-loading-pulse 1.4s infinite ease-in-out;animation-delay:0.2s;"></div><div class="nca-loading-dot" style="width:6px;height:6px;border-radius:50%;background:var(--nca-accent,#5b9fff);animation:nca-loading-pulse 1.4s infinite ease-in-out;animation-delay:0.4s;"></div><span>' + t('optimize.analyzing_code') + '</span></div>';

        try {
            const review = await 执行代码审查(folder, depth);
            切换到审查视图(review);
        } catch (e) {
            Toast.error(t('optimize.review_fail_msg', { error: e.message || e }));
            切换到对话视图();
        } finally {
            if (reviewBtn) {
                reviewBtn.disabled = false;
                reviewBtn.innerHTML = '<span class="nca-review-icon">🔍</span> ' + t('optimize.code_review');
            }
        }
    }

    async function 开始优化() {
        if (!审查结果缓存) return;

        const folder = 获取有效文件夹(当前会话, 最后文件夹来源, 'optimize');
        if (!folder) {
            Toast.warning(t('optimize.select_plugin_dir'));
            return;
        }

        // === 查找/创建关联会话 ===
        try {
            const sessions = await 获取会话列表('optimize');
            const existing = sessions.find(s => s.plugin_folder === folder);

            if (existing) {
                // 已有关联会话，切换到它：setCurrentSessionId 仅同步高亮（不触发 onSessionSwitch），
                // 需手动对齐文件夹来源语义并加载该会话历史，否则消息区与实际会话脱节
                if (会话面板 && 会话面板.setCurrentSessionId) {
                    会话面板.setCurrentSessionId(existing.id);
                }
                最后文件夹来源 = 'session';
                设置插件文件夹('', 'optimize');
                当前会话 = existing;
                更新状态栏();
                await 加载会话消息(existing.id, msgArea, _会话加载参数());
            } else {
                // 没有关联会话，创建新的
                const folderName = folder.split(/[\\/]/).pop();
                const newSession = await 创建会话(t('session.title_optimize', { name: folderName }), folder, 'optimize');
                if (!newSession) {
                    Toast.error(t('session.create_fail'));
                    return;
                }
                当前会话 = newSession;
                if (会话面板) {
                    await 会话面板.refresh();
                    if (会话面板.setCurrentSessionId) {
                        会话面板.setCurrentSessionId(newSession.id);
                    }
                }
                // 手动加载新会话（清空欢迎页，渲染空会话状态），与已有分支行为一致
                加载会话消息(newSession.id, msgArea, _会话加载参数());
            }
        } catch (e) {
            Toast.error(t('session.prepare_fail_msg', { error: e.message }));
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
    绑定输入事件(input, sendBtn, 附件, async () => {
        await 发送面板消息({
            input, sendBtn, msgArea, 附件,
            activeTab: 'optimize',
            getSessionId: () => 当前会话?.id || 会话面板.getCurrentSessionId(),
            getPluginFolder: () => 获取有效文件夹(当前会话, 最后文件夹来源, 'optimize'),
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
}
