// ═══════════════════════════════════════════════════════════════
// 面板会话公共.js — 优化面板与可视化面板共有的会话逻辑
// NodeCraft AI — Luxury Terminal / Neo-Noir Hacker
// ═══════════════════════════════════════════════════════════════

import {
    el, NCA_API_BASE, NCA_STORAGE_KEYS, 简易Markdown渲染, 移除视觉能力警告, Toast, 安全存储读, 工具名显示,
} from "./工具函数.js";
import { 状态, 事件总线, 事件, 创建消耗信息DOM, 检查余额, 获取会话列表, 创建会话 } from "./交互与状态.js";
import { 创建流式聊天 } from "./流式聊天管理器.js";
import { 创建附件组件 } from "./附件上传组件.js";
import { 绑定代码块复制按钮 } from "./消息渲染器.js";
import { 显示知识库通知 } from "./知识库通知条.js";

// 面板发送并发保护：防止重复点击导致多个流式请求同时进行
let _面板正在发送 = false;

/**
 * 1. 获取有效文件夹 — 解决插件选择与会话互斥
 * @param {Object|null} 当前会话 - 当前选中会话
 * @param {string|null} 最后文件夹来源 - 'plugin' | 'session' | null
 * @returns {string|null}
 */
export function 获取有效文件夹(当前会话, 最后文件夹来源) {
    const pluginFolder = 安全存储读(NCA_STORAGE_KEYS.plugin);
    const sessionFolder = 当前会话?.plugin_folder || "";
    if (!pluginFolder && !sessionFolder) return null;
    if (pluginFolder && sessionFolder) {
        return 最后文件夹来源 === 'session' ? sessionFolder : pluginFolder;
    }
    return pluginFolder || sessionFolder;
}

/**
 * 2. 加载会话消息 — 从API加载消息历史并渲染到容器
 * @param {string} sessionId
 * @param {HTMLElement} msgArea - 消息容器
 */
export async function 加载会话消息(sessionId, msgArea, options = {}) {
    msgArea.innerHTML = "";
    msgArea._nca消息列表 = [];
    if (!sessionId) {
        msgArea.appendChild(el("div", { class: "nca-empty-state", text: "请选择或创建会话" }));
        return;
    }
    try {
        const res = await fetch(`${NCA_API_BASE}/sessions/${sessionId}/messages`);
        if (!res.ok) return;
        const data = await res.json();
        const messages = data.messages || [];
        msgArea._nca消息列表 = messages;
        if (messages.length === 0) {
            msgArea.appendChild(el("div", { class: "nca-empty-state", text: "暂无消息记录" }));
            return;
        }
        messages.forEach((msg, index) => {
            const isUser = msg.role === "user";
            const role = isUser ? "user" : "assistant";
            const roleLabel = isUser ? "用户" : "AI";
            const headerChildren = [
                el("span", { class: "msg-role", text: `─ ${roleLabel}` }),
            ];
            const timeStr = msg.timestamp ? new Date(msg.timestamp).toLocaleTimeString('zh-CN', {hour:'2-digit', minute:'2-digit'}) : '';
            if (timeStr) headerChildren.push(el("span", { class: "msg-time", text: timeStr }));
            if (isUser) {
                const editBtn = el("button", { class: "nca-msg-edit-btn", text: "✎", title: "编辑并重新生成" });
                editBtn.addEventListener("click", (e) => {
                    e.stopPropagation();
                    const host = editBtn.closest(".nca-msg");
                    if (!host) return;
                    _进入面板编辑模式(host, msg.content || "", host._消息索引, msgArea, options);
                });
                headerChildren.push(editBtn);
            }
            const bubble = el("div", { class: `nca-msg ${role}` }, [
                el("div", { class: "nca-msg-header" }, headerChildren),
            ]);
            bubble._消息索引 = index;
            const body = el("div", { class: "nca-msg-body" });
            body.innerHTML = 简易Markdown渲染(msg.content || "");
            bubble.appendChild(body);
            bubble._原始内容 = msg.content || "";
            if (!isUser) 绑定代码块复制按钮(bubble);
            msgArea.appendChild(bubble);
        });
        msgArea.scrollTop = msgArea.scrollHeight;
    } catch (e) {
        console.warn("[节点梦工厂] 加载会话消息失败:", e);
    }
}

/**
 * 3. 发送面板消息 — 统一的发送+流式渲染逻辑
 * @param {Object} options
 * @param {HTMLTextAreaElement} options.input - 输入框
 * @param {HTMLButtonElement} options.sendBtn - 发送按钮
 * @param {HTMLElement} options.msgArea - 消息容器
 * @param {Object} options.附件 - 附件组件实例
 * @param {string} options.activeTab - 'optimize' | 'visualize'
 * @param {Function} options.getSessionId - () => sessionId
 * @param {Function} options.getPluginFolder - () => folderPath
 */
export async function 发送面板消息(options) {
    const { input, sendBtn, msgArea, 附件, activeTab, getSessionId, getPluginFolder, content: 指定内容, truncate_at } = options;

    // 并发保护：避免重复点击发送
    if (_面板正在发送) return;
    _面板正在发送 = true;

    // 发送前余额检查（余额不足则阻断；查询失败降级放行）
    const 余额检查 = await 检查余额();
    if (!余额检查.sufficient && 余额检查.balance >= 0) {
        try { Toast.warning("额度已耗尽，请充值后继续使用"); } catch (_) {}
        _面板正在发送 = false;
        sendBtn.disabled = false;
        return;
    }

    let stopBtn = null;

    try {
        const 来自编辑 = typeof 指定内容 === "string" && 指定内容.length > 0;
        const content = 来自编辑 ? 指定内容 : input.value.trim();
        const 当前附件 = 来自编辑 ? [] : 附件.获取附件();
        if (!content && 当前附件.length === 0) return;

        const selectedPlugin = getPluginFolder();
        if (!selectedPlugin) {
            Toast.warning("请先选择一个插件目录");
            return;
        }

        let sessionId = getSessionId();
        if (!sessionId) {
            // 没有会话 → 自动匹配或创建会话
            try {
                const sessions = await 获取会话列表(activeTab);
                const existing = sessions.find(s => s.plugin_folder === selectedPlugin);

                if (existing) {
                    // 匹配到关联会话 → 使用它
                    sessionId = existing.id;
                    if (typeof options.onSessionReady === 'function') {
                        await options.onSessionReady(existing, false);
                    }
                } else {
                    // 没有匹配 → 创建新会话
                    const folderBasename = selectedPlugin.split(/[\\/]/).pop();
                    const suffix = activeTab === 'optimize' ? '优化' : (activeTab === 'visualize' ? '可视化' : '');
                    const newSession = await 创建会话(`${folderBasename} ${suffix}`, selectedPlugin, activeTab);
                    if (!newSession) {
                        Toast.error('创建会话失败');
                        return;
                    }
                    sessionId = newSession.id;
                    if (typeof options.onSessionReady === 'function') {
                        await options.onSessionReady(newSession, true);
                    }
                }
            } catch (e) {
                Toast.error('准备会话失败: ' + e.message);
                return;
            }
        }

        const nowStr = new Date().toLocaleTimeString('zh-CN', {hour:'2-digit', minute:'2-digit'});

        // 渲染用户消息
        const 当前消息数 = msgArea.querySelectorAll('.nca-msg').length;
        const userBubble = el("div", { class: "nca-msg user" }, [
            el("div", { class: "nca-msg-header" }, [
                el("span", { class: "msg-role", text: "─ 用户" }),
                el("span", { class: "msg-time", text: nowStr }),
                el("button", { class: "nca-msg-edit-btn", text: "✎", title: "编辑并重新生成" }),
            ]),
        ]);
        const userBody = el("div", { class: "nca-msg-body" });
        userBody.innerHTML = 简易Markdown渲染(content || `[已附加 ${当前附件.length} 个文件]`);
        userBubble.appendChild(userBody);
        userBubble._原始内容 = content || "";
        userBubble._消息索引 = 来自编辑 ? truncate_at : 当前消息数;
        const editBtn = userBubble.querySelector(".nca-msg-edit-btn");
        editBtn.addEventListener("click", (e) => {
            e.stopPropagation();
            _进入面板编辑模式(userBubble, content, userBubble._消息索引, msgArea, options);
        });
        msgArea.appendChild(userBubble);
        if (msgArea._nca消息列表) {
            msgArea._nca消息列表.push({ role: "user", content });
        }
        if (!来自编辑) {
            input.value = '';
            input.style.height = "auto";
            附件.清空();
            // 移除视觉能力警告：通过 input 向上查找 .nca-input-area
            const inputAreaEl = input.closest('.nca-input-area');
            if (inputAreaEl) 移除视觉能力警告(inputAreaEl);
            sendBtn.classList.remove("active");
        }
        msgArea.scrollTop = msgArea.scrollHeight;

        // AI回复气泡（流式渲染目标）
        const aiBubble = el("div", { class: "nca-msg assistant" }, [
            el("div", { class: "nca-msg-header" }, [
                el("span", { class: "msg-role", text: "─ AI" }),
                el("span", { class: "msg-time", text: nowStr }),
            ]),
        ]);
        const aiBody = el("div", { class: "nca-msg-body", html: '<span class="nca-streaming-cursor"></span>' });
        aiBubble.appendChild(aiBody);
        msgArea.appendChild(aiBubble);
        msgArea.scrollTop = msgArea.scrollHeight;

        // 显示停止按钮
        stopBtn = el("button", { class: "nca-stop-btn", text: "■ 停止生成" });
        stopBtn.addEventListener("click", () => {
            abortCtrl.abort();
        });
        // 插入到输入区域前面
        const inputArea = input.closest(".nca-input-area");
        if (inputArea) inputArea.insertBefore(stopBtn, inputArea.firstChild);

        // 构建请求体
        // model_source 多源读取：全局状态 → 设置对象 → 默认值 'api'
        // 确保即使全局状态未同步，也能从设置中获取正确的模型来源
        const _modelSource = 状态.模型来源 || 状态.设置?.model_source || 'api';
        const requestBody = {
            message: content,
            session_id: sessionId,
            plugin_context: selectedPlugin,
            model_source: _modelSource,
            local_model_name: 状态.选中本地模型 || 状态.设置?.local_model_name || "",
            activeTab: activeTab,
        };
        if (当前附件.length > 0) requestBody.attachments = 当前附件;
        if (truncate_at !== undefined && truncate_at !== null) {
            requestBody.truncate_at = truncate_at;
        }

        // 发送
        const abortCtrl = new AbortController();
        // 前端超时保护：若超过设定时间（默认120秒）仍无响应数据，自动中止
        const _frontendTimeout = setTimeout(() => {
            abortCtrl.abort();
        }, (状态.设置?.chat_timeout || 120000) + 5000); // 比后端超时多5秒
        sendBtn.disabled = true;
        await 创建流式聊天({
            消息容器: aiBubble,
            消息体: aiBody,
            滚动容器: msgArea,
            请求体: requestBody,
            数据处理: (data, 状态引用) => {
                // ── 上下文健康度指标 - 不渲染为聊天消息 ──
                if (data.type === 'context_health') {
                    return 'skip';
                }
                // ── 知识库检索状态 - 改为侧边栏常驻通知条，不渲染为聊天消息 ──
                if (data.type === 'kb_status') {
                    if (data.status === 'degraded' && data.message) {
                        显示知识库通知(data.message);
                    }
                    return 'skip';
                }
                // ── 工具执行状态消息 ──
                if (data.type === 'tool_executing') {
                    const toolMatch = (data.content || '').match(/\[正在执行:\s*(.+?)\.{3}\]/);
                    const toolName = 工具名显示(toolMatch ? toolMatch[1] : '工具');
                    if (!状态引用.el) {
                        状态引用.el = document.createElement('div');
                        状态引用.el.className = 'nca-tool-executing';
                        aiBody.appendChild(状态引用.el);
                    }
                    状态引用.el.innerHTML = '<span class="tool-exec-icon">⚙️</span> 正在执行 <code></code><span class="tool-exec-dots"></span>';
                    状态引用.el.querySelector('code').textContent = toolName;
                    msgArea.scrollTop = msgArea.scrollHeight;
                    try { 事件总线.emit(事件.状态栏更新, `执行工具: ${toolName}...`); } catch(_) {}
                    return 'skip';
                }
                // ── 自动续跑状态 ──
                if (data.type === 'tool_executing' && (data.content || '').includes('[自动继续执行')) {
                    return 'skip';
                }
                // 收到正常文本内容时，移除工具执行指示器
                if (data.content) {
                    if (状态引用.el) {
                        状态引用.el.remove();
                        状态引用.el = null;
                    }
                    // 过滤掉混入正文的 [正在执行: xxx...] 标记
                    data.content = data.content.replace(/\n?\[正在执行:\s*.+?\.{3}\]\n?/g, '');
                    data.content = data.content.replace(/\n?\[自动继续执行[^\]]*\]\n?/g, '');
                }
            },
            中止信号: abortCtrl.signal,
            完成回调: ({ fullContent, hasError, billing }) => {
                try { 事件总线.emit(事件.状态栏更新, "就绪"); } catch(_) {}
                // 始终执行最终渲染（移除流式光标、显示错误或无回复文本）
                const 内容 = fullContent || "（无回复）";
                if (hasError && !fullContent) {
                    aiBody.innerHTML = '';
                    aiBubble.classList.remove('assistant');
                    aiBubble.classList.add('system');
                    aiBody.textContent = `❌ 网络错误`;
                } else if (hasError) {
                    aiBody.innerHTML = 简易Markdown渲染(内容);
                } else {
                    aiBody.innerHTML = 简易Markdown渲染(内容);
                    绑定代码块复制按钮(aiBubble);
                }
                // 显示本次消耗
                if (billing) {
                    aiBubble.appendChild(创建消耗信息DOM(billing.cost, billing.balance));
                }
            },
        });
        clearTimeout(_frontendTimeout);
        msgArea.scrollTop = msgArea.scrollHeight;
    } finally {
        if (stopBtn && stopBtn.parentNode) stopBtn.remove();
        sendBtn.disabled = false;
        _面板正在发送 = false;
    }
}

// ─── 面板消息内编辑模式：行内 textarea + 确认/取消 ───────────────
function _进入面板编辑模式(msgEl, originalContent, index, msgArea, options) {
    if (_面板正在发送) return;
    if (msgEl.classList.contains("nca-msg-editing")) return;

    msgEl.classList.add("nca-msg-editing");
    const body = msgEl.querySelector(".nca-msg-body");
    if (!body) return;
    const originalHTML = body.innerHTML;

    const textarea = el("textarea", { class: "nca-edit-textarea", spellcheck: "false" });
    textarea.value = originalContent || "";

    const cancelBtn = el("button", { class: "nca-edit-cancel", text: "取消" });
    const confirmBtn = el("button", { class: "nca-edit-confirm", text: "确认并重新生成" });
    const actions = el("div", { class: "nca-edit-actions" }, [cancelBtn, confirmBtn]);

    body.innerHTML = "";
    body.appendChild(textarea);
    body.appendChild(actions);

    const 自适应高度 = () => {
        textarea.style.height = "auto";
        textarea.style.height = Math.min(textarea.scrollHeight, 300) + "px";
    };
    requestAnimationFrame(() => {
        textarea.focus();
        try { textarea.setSelectionRange(textarea.value.length, textarea.value.length); } catch (_) {}
        自适应高度();
    });
    textarea.addEventListener("input", 自适应高度);

    // ESC 取消、Ctrl/Cmd+Enter 确认
    textarea.addEventListener("keydown", (e) => {
        if (e.key === "Escape") { e.preventDefault(); cancelBtn.click(); }
        else if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) { e.preventDefault(); confirmBtn.click(); }
    });

    // 取消：恢复原始内容
    cancelBtn.addEventListener("click", () => {
        msgEl.classList.remove("nca-msg-editing");
        body.innerHTML = originalHTML;
    });

    // 确认：截断后续消息并重新生成
    confirmBtn.addEventListener("click", async () => {
        const newContent = textarea.value.trim();
        if (!newContent) {
            try { Toast.warning("消息内容不能为空"); } catch (_) {}
            return;
        }
        if (_面板正在发送) return;
        msgEl.classList.remove("nca-msg-editing");

        // 截断：移除当前消息及之后的所有消息 DOM
        const allMsgs = Array.from(msgArea.querySelectorAll('.nca-msg'));
        const currentIdx = allMsgs.indexOf(msgEl);
        if (currentIdx >= 0) {
            for (let i = allMsgs.length - 1; i >= currentIdx; i--) {
                allMsgs[i].remove();
            }
        }

        // 同步维护消息列表引用
        if (msgArea._nca消息列表) {
            msgArea._nca消息列表.length = index;
        }

        // 触发重新生成，携带 truncate_at
        await 发送面板消息({
            ...options,
            content: newContent,
            truncate_at: index,
        });
    });
}

/**
 * 4. 创建面板输入区 — 统一输入区DOM构建
 * @param {HTMLElement} panelContainer - 面板容器（附件组件根容器）
 * @param {Object} options
 * @param {Function} options.onSend - 发送回调
 * @param {string} [options.placeholder] - 占位文字
 * @returns { inputArea, input, sendBtn, 附件 }
 */
export function 创建面板输入区(panelContainer, options = {}) {
    const { onSend, placeholder = "输入消息..." } = options;

    const inputArea = el("div", { class: "nca-input-area" });
    const inputWrapper = el("div", { class: "nca-input-wrapper" });
    const input = el("textarea", { rows: "1", placeholder, class: "nca-input-text" });
    const sendBtn = el("button", { class: "nca-send-btn", html: "▶", title: "发送" });

    // 附件上传组件
    const 附件 = 创建附件组件(panelContainer, () => {
        sendBtn.classList.toggle("active", input.value.trim().length > 0 || 附件.有附件());
    });
    附件.设置输入区(inputArea);
    inputArea.appendChild(附件.预览区);

    inputWrapper.appendChild(附件.文件按钮);
    inputWrapper.appendChild(附件.文件输入);
    inputWrapper.appendChild(input);
    inputWrapper.appendChild(sendBtn);
    inputArea.appendChild(inputWrapper);

    绑定输入事件(input, sendBtn, 附件, onSend);

    return { inputArea, input, sendBtn, 附件 };
}

/**
 * 5. 绑定输入事件 — 高度自适应、Enter发送、按钮激活
 * @param {HTMLTextAreaElement} input
 * @param {HTMLButtonElement} sendBtn
 * @param {Object} 附件 - 附件组件实例
 * @param {Function} [onSend] - 发送回调
 */
export function 绑定输入事件(input, sendBtn, 附件, onSend) {
    input.addEventListener("input", () => {
        input.style.height = "auto";
        input.style.height = Math.min(input.scrollHeight, 100) + "px";
        sendBtn.classList.toggle("active", input.value.trim().length > 0 || (附件 && 附件.有附件 && 附件.有附件()));
    });

    sendBtn.addEventListener("click", () => { if (onSend) onSend(); });

    input.addEventListener("keydown", (e) => {
        if (e.key === 'Enter' && !e.shiftKey) {
            e.preventDefault();
            sendBtn.click();
        }
    });
}
