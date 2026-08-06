// ═══════════════════════════════════════════════════════════════
// 面板会话公共.js — 优化面板与可视化面板共有的会话逻辑
// NodeCraft AI — Luxury Terminal / Neo-Noir Hacker
// ═══════════════════════════════════════════════════════════════

import {
    el, NCA_API_BASE, NCA_STORAGE_KEYS, 插件存储键, 简易Markdown渲染, 移除视觉能力警告, Toast, 工具名显示, 追加附件缩略图, 创建行内编辑器,
} from "./工具函数.js";
import { 状态, 事件总线, 事件, 获取会话列表, 创建会话 } from "./交互与状态.js";
import { 创建流式聊天 } from "./流式聊天管理器.js";
import { t, 获取当前语言 } from "./i18n.js";
import { 创建附件组件 } from "./附件上传组件.js";
import { 绑定代码块复制按钮 } from "./消息渲染器.js";
import { 显示知识库通知 } from "./知识库通知条.js";

// 面板发送并发保护：防止重复点击导致多个流式请求同时进行
let _面板正在发送 = false;

// ─── 工具循环噪音折叠 ─────────────────────────────────────────
// 扩展覆盖更多中文高频噪音模式：分段读取、继续查看、用XX方式等
const _面板噪音模式 = /^(让我|我先|好的[，,]让我|现在(创建|更新|修复|实现|读取|写入|设计|安装|配置|检查|验证)|我来(设计|实现|创建|编写|修改)|文件.*截断|Let me |I'll |Now let|First let|让我用|让我查看|让我搜索|让我先看|让我重新|让我完整|让我精确|让我分段|让我全面|我来看看|我先看看|好的[，,]让我先|(读取|继续读取|先查看|先了解|先检查|分段读取|完整读取|获取|用分段|用更|用更精|先读取|先完整|先全面|先获取|查看当前|查看完整|了解当前|了解完整|检查当前|检查完整).*)/;
let _面板工具已执行 = false;
let _面板噪音计数 = 0;

/**
 * 1. 获取有效文件夹 — 解决插件选择与会话互斥
 * @param {Object|null} 当前会话 - 当前选中会话
 * @param {string|null} 最后文件夹来源 - 'plugin' | 'session' | null
 * @param {string} [scope] - 界面作用域 'develop' | 'optimize' | 'visualize'，决定读取哪个插件键
 * @returns {string|null}
 */
export function 获取有效文件夹(当前会话, 最后文件夹来源, scope = 'develop') {
    const pluginFolder = localStorage.getItem(插件存储键(scope));
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
    // 服务端强制分页后，首条消息在全量历史中的绝对索引（truncate_at 换算用）
    msgArea._nca起始偏移 = 0;
    if (!sessionId) {
        msgArea.appendChild(el("div", { class: "nca-empty-state", text: t("chat.select_or_create") }));
        return;
    }
    try {
        // 显式窗口模式：仅加载最近一窗消息（首屏性能优化），start_index 写入 _nca起始偏移
        const res = await fetch(`${NCA_API_BASE}/sessions/${sessionId}/messages?recent=1`);
        if (!res.ok) return;
        const data = await res.json();
        const messages = data.messages || [];
        msgArea._nca消息列表 = messages;
        msgArea._nca起始偏移 = data.start_index || 0;
        if (messages.length === 0) {
            msgArea.appendChild(el("div", { class: "nca-empty-state", text: t("chat.no_messages") }));
            return;
        }
        messages.forEach((msg, index) => {
            const isUser = msg.role === "user";
            const role = isUser ? "user" : "assistant";
            const roleLabel = isUser ? t("chat.role_user") : "AI";
            const headerChildren = [
                el("span", { class: "msg-role", text: `─ ${roleLabel}` }),
            ];
            const timeStr = msg.timestamp ? new Date(msg.timestamp).toLocaleTimeString('zh-CN', {hour:'2-digit', minute:'2-digit'}) : '';
            if (timeStr) headerChildren.push(el("span", { class: "msg-time", text: timeStr }));
            if (isUser) {
                const editBtn = el("button", { class: "nca-msg-edit-btn", text: "✎", title: t("chat.edit_regen") });
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
            // 用户消息附带附件图片缩略图
            if (isUser && msg.attachments && msg.attachments.length > 0) {
                追加附件缩略图(body, msg.attachments);
            }
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

    // 本地 API Key 直连后不再做前置余额检查

    let stopBtn = null;
    let _活跃检查定时器 = null;

    try {
        const 来自编辑 = typeof 指定内容 === "string" && 指定内容.length > 0;
        const content = 来自编辑 ? 指定内容 : input.value.trim();
        const 当前附件 = 来自编辑 ? [] : 附件.获取附件();
        if (!content && 当前附件.length === 0) return;

        const selectedPlugin = getPluginFolder();
        if (!selectedPlugin) {
            Toast.warning(t("optimize.select_dir"));
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
                    const 会话标题 = activeTab === 'optimize' ? t('session.title_optimize', { name: folderBasename }) : (activeTab === 'visualize' ? t('session.title_visualize', { name: folderBasename }) : folderBasename);
                    const newSession = await 创建会话(会话标题, selectedPlugin, activeTab);
                    if (!newSession) {
                        Toast.error(t('session.create_fail'));
                        return;
                    }
                    sessionId = newSession.id;
                    if (typeof options.onSessionReady === 'function') {
                        await options.onSessionReady(newSession, true);
                    }
                }
            } catch (e) {
                Toast.error(t('session.prepare_fail_msg', { error: e.message }));
                return;
            }
        }

        const nowStr = new Date().toLocaleTimeString('zh-CN', {hour:'2-digit', minute:'2-digit'});

        // 清除欢迎页：优化面板在无会话时会把 .nca-welcome 渲染进 msgArea，
        // 而本函数全程用 appendChild 追加，不清掉的话欢迎页会残留在会话消息上方。
        // 与 消息渲染器.js 的 追加消息DOM 同一处理方式。
        if (msgArea.querySelector(".nca-welcome")) {
            msgArea.innerHTML = "";
        }

        // 渲染用户消息
        const 当前消息数 = msgArea.querySelectorAll('.nca-msg').length;
        const userBubble = el("div", { class: "nca-msg user" }, [
            el("div", { class: "nca-msg-header" }, [
                el("span", { class: "msg-role", text: `─ ${t("chat.role_user")}` }),
                el("span", { class: "msg-time", text: nowStr }),
                el("button", { class: "nca-msg-edit-btn", text: "✎", title: t("chat.edit_regen") }),
            ]),
        ]);
        const userBody = el("div", { class: "nca-msg-body" });
        userBody.innerHTML = 简易Markdown渲染(content || t('chat.attached_files', { n: 当前附件.length }));
        userBubble.appendChild(userBody);
        // 用户消息附带附件图片缩略图
        if (当前附件.length > 0) {
            追加附件缩略图(userBody, 当前附件);
        }
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
        _面板工具已执行 = false;
        _面板噪音计数 = 0;
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
        stopBtn = el("button", { class: "nca-stop-btn", text: "■ " + t("chat.stop") });
        stopBtn.addEventListener("click", () => {
            // 手动停止：顺便清理活跃定时器，避免 abort 后定时器空转重复 abort
            if (_活跃检查定时器) { clearInterval(_活跃检查定时器); _活跃检查定时器 = null; }
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
            language: 获取当前语言(),
        };
        if (当前附件.length > 0) requestBody.attachments = 当前附件;
        // 编辑重生成：本地索引需叠加起始偏移换算为服务端绝对索引（首屏仅加载最近50条）
        if (truncate_at !== undefined && truncate_at !== null) {
            requestBody.truncate_at = truncate_at + (msgArea._nca起始偏移 || 0);
        }

        // 发送
        const abortCtrl = new AbortController();
        // 滑动活跃超时：仅在持续无数据时中止，防止长工具循环被误杀
        // 每收到任何 SSE 事件都刷新活跃时间，仅当持续无数据超过阈值才自动中止
        let _最后活跃时间 = Date.now();
        // 滑动活跃超时阈值：显式数值化，非法配置（NaN/负数）回退默认 120000
        let _基础超时 = Number(状态.设置?.chat_timeout ?? 120000);
        if (!Number.isFinite(_基础超时) || _基础超时 <= 0) _基础超时 = 120000;
        const _活跃超时阈值 = _基础超时 + 5000; // 比后端超时多5秒
        _活跃检查定时器 = setInterval(() => {
            if (Date.now() - _最后活跃时间 > _活跃超时阈值) {
                clearInterval(_活跃检查定时器);
                _活跃检查定时器 = null;
                abortCtrl.abort();
            }
        }, 5000);
        sendBtn.disabled = true;
        await 创建流式聊天({
            消息容器: aiBubble,
            消息体: aiBody,
            滚动容器: msgArea,
            请求体: requestBody,
            数据处理: (data, 状态引用) => {
                // 滑动活跃超时：任何事件到达都刷新活跃时间
                _最后活跃时间 = Date.now();
                // ── 上下文健康度指标 - 不渲染为聊天消息 ──
                if (data.type === 'context_health') {
                    if (data.warning) {
                        if (data.usage_percent >= 95) {
                            Toast.error(t('chat.context_full', { percent: data.usage_percent }));
                        } else if (data.usage_percent >= 85) {
                            Toast.warning(t('chat.context_high', { percent: data.usage_percent }));
                        }
                    }
                    return 'skip';
                }
                // ── 知识库检索状态 - 改为侧边栏常驻通知条，不渲染为聊天消息 ──
                if (data.type === 'kb_status') {
                    if (data.status === 'degraded' && data.message) {
                        显示知识库通知(data.message);
                    }
                    return 'skip';
                }
                // ── 思考状态消息（K3 等深度思考模型的 reasoning 阶段进度，不渲染为正文）──
                if (data.type === 'thinking') {
                    const charMatch = (data.content || '').match(/\[思考中:\s*(\d+)/);
                    const charCount = charMatch ? charMatch[1] : '0';
                    if (!状态引用.el) {
                        状态引用.el = document.createElement('div');
                        状态引用.el.className = 'nca-tool-executing';
                        aiBody.appendChild(状态引用.el);
                    }
                    状态引用.el.innerHTML = `<span class="tool-exec-icon">🤔</span> ${t('tool.thinking')} <code></code><span class="tool-exec-dots"></span>`;
                    状态引用.el.querySelector('code').textContent = t('tool.thinking_chars', { n: charCount });
                    msgArea.scrollTop = msgArea.scrollHeight;
                    try { 事件总线.emit(事件.状态栏更新, t('tool.thinking')); } catch(_) {}
                    return 'skip';
                }
                // ── 工具执行状态消息 ──
                if (data.type === 'tool_executing') {
                    _面板工具已执行 = true;
                    const toolMatch = (data.content || '').match(/\[正在执行:\s*(.+?)\.{3}\]/);
                    const toolName = 工具名显示(toolMatch ? toolMatch[1] : t('tool.generic'));
                    if (!状态引用.el) {
                        状态引用.el = document.createElement('div');
                        状态引用.el.className = 'nca-tool-executing';
                        aiBody.appendChild(状态引用.el);
                    }
                    状态引用.el.innerHTML = `<span class="tool-exec-icon">⚙️</span> ${t('tool.executing')} <code></code><span class="tool-exec-dots"></span>`;
                    状态引用.el.querySelector('code').textContent = toolName;
                    msgArea.scrollTop = msgArea.scrollHeight;
                    try { 事件总线.emit(事件.状态栏更新, t('tool.status_bar', { name: toolName })); } catch(_) {}
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
                    // 过滤掉混入正文的 [正在执行: xxx...] / [思考中: xxx...] 标记
                    data.content = data.content.replace(/\n?\[正在执行:\s*.+?\.{3}\]\n?/g, '');
                    data.content = data.content.replace(/\n?\[思考中:\s*[^\]]*\.{3}\]\n?/g, '');
                    data.content = data.content.replace(/\n?\[自动继续执行[^\]]*\]\n?/g, '');
                    // ── 工具循环噪音过滤 ──
                    if (_面板工具已执行) {
                        const lines = data.content.split('\n');
                        const 保留行 = [];
                        for (const line of lines) {
                            const trimmed = line.trim();
                            if (!trimmed) { 保留行.push(line); continue; }
                            if (/^```/.test(trimmed)) { 保留行.push(line); continue; }
                            if (/^[-*+]\s|^\d+[.)]\s/.test(trimmed)) { 保留行.push(line); continue; }
                            if (/^#{1,6}\s/.test(trimmed)) { 保留行.push(line); continue; }
                            // 匹配噪音模式（任何长度都检测，长行检测前40字符）
                            if (_面板噪音模式.test(trimmed) || (trimmed.length > 80 && _面板噪音模式.test(trimmed.slice(0, 40)))) {
                                _面板噪音计数++;
                                if (_面板噪音计数 % 5 === 1) {
                                    保留行.push(`\n\n**${t('chat.auto_steps', { n: _面板噪音计数 })}**\n`);
                                }
                                continue;
                            }
                            if (trimmed.length > 80) { 保留行.push(line); continue; }
                            保留行.push(line);
                        }
                        data.content = 保留行.join('\n');
                    }
                }
            },
            中止信号: abortCtrl.signal,
            完成回调: ({ fullContent, hasError }) => {
                try { 事件总线.emit(事件.状态栏更新, t('common.ready')); } catch(_) {}
                // 始终执行最终渲染（移除流式光标、显示错误或无回复文本）
                const 内容 = fullContent || t('chat.no_reply');
                if (hasError && !fullContent) {
                    aiBody.innerHTML = '';
                    aiBubble.classList.remove('assistant');
                    aiBubble.classList.add('system');
                    aiBody.textContent = `❌ ${t('common.network_error')}`;
                } else if (hasError) {
                    aiBody.innerHTML = 简易Markdown渲染(内容);
                    绑定代码块复制按钮(aiBubble);
                } else {
                    aiBody.innerHTML = 简易Markdown渲染(内容);
                    绑定代码块复制按钮(aiBubble);
                }
                // 本地 API Key 直连后不再显示本次消耗
            },
        });
        msgArea.scrollTop = msgArea.scrollHeight;
    } finally {
        // 滑动活跃超时：正常结束/异常/手动停止均在此清理，避免定时器泄漏
        if (_活跃检查定时器) {
            clearInterval(_活跃检查定时器);
            _活跃检查定时器 = null;
        }
        _面板工具已执行 = false;
        _面板噪音计数 = 0;
        if (stopBtn && stopBtn.parentNode) stopBtn.remove();
        sendBtn.disabled = false;
        _面板正在发送 = false;
    }
}

// ─── 面板消息内编辑模式：行内 textarea + 确认/取消 ───────────────
function _进入面板编辑模式(msgEl, originalContent, index, msgArea, options) {
    if (_面板正在发送) return;

    创建行内编辑器(msgEl, originalContent, {
        // 确认：截断后续消息并重新生成
        onConfirm: async (newContent) => {
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
        },
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
    const { onSend, placeholder = t("chat.input_placeholder") } = options;

    const inputArea = el("div", { class: "nca-input-area" });
    const inputWrapper = el("div", { class: "nca-input-wrapper" });
    const input = el("textarea", { rows: "1", placeholder, class: "nca-input-text" });
    const sendBtn = el("button", { class: "nca-send-btn", html: "▶", title: t("chat.send") });

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
