// ═══════════════════════════════════════════════════════════════
// 面板会话公共.js — 优化面板与可视化面板共有的会话逻辑
// NodeCraft AI — Luxury Terminal / Neo-Noir Hacker
//
// 会话管线（发送/SSE处理/完成分支/编辑重生成）已统一至 会话核心.js，
// 本模块通过 _面板适配器 声明面板独有能力（会话自动匹配/创建、插件目录校验）
// 后委托 发送会话消息；历史渲染复用 会话核心.创建消息DOM 气泡工厂。
// ═══════════════════════════════════════════════════════════════

import {
    el, NCA_API_BASE, 插件存储键, 移除视觉能力警告, Toast,
} from "./工具函数.js";
import { 状态, 获取会话列表, 创建会话 } from "./交互与状态.js";
import { t } from "./i18n.js";
import { 创建附件组件 } from "./附件上传组件.js";
import { 绑定代码块复制按钮 } from "./消息渲染/代码复制.js";
import {
    创建消息DOM as _核心创建消息DOM, 进入编辑模式, 发送会话消息,
} from "./会话核心.js";

// 面板发送并发保护：覆盖会话自动创建等前置异步窗口（会话核心锁仅覆盖管线本体）
let _面板正在发送 = false;

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

// ─── 面板视图适配器 ──────────────────────────────────────────
// 面板独有能力：插件目录必选校验、会话自动匹配/创建（含 onSessionReady 回调）；
// 消息列表与起始偏移挂在 msgArea 上（与主聊天的全局状态对应物）
function _面板适配器构建(options) {
    const { input, sendBtn, msgArea, 附件, activeTab, getSessionId, getPluginFolder } = options;
    return {
        消息区域: msgArea,
        输入框: input,
        发送按钮: sendBtn,
        activeTab,
        // 附件来源二态：数组直传（规划重发透传）或附件组件实例
        附件获取: () => (Array.isArray(options.附件) ? options.附件 : (附件 ? 附件.获取附件() : [])),
        发送前校验: () => {
            if (!getPluginFolder()) {
                Toast.warning(t("optimize.select_dir"));
                return false;
            }
            return true;
        },
        获取插件目录: () => getPluginFolder(),
        获取会话id: async () => {
            let sessionId = getSessionId();
            if (sessionId) return sessionId;
            // 没有会话 → 自动匹配或创建会话
            const selectedPlugin = getPluginFolder();
            try {
                const sessions = await 获取会话列表(activeTab);
                const existing = sessions.find(s => s.plugin_folder === selectedPlugin);
                if (existing) {
                    // 匹配到关联会话 → 使用它
                    if (typeof options.onSessionReady === 'function') {
                        await options.onSessionReady(existing, false);
                    }
                    return existing.id;
                }
                // 没有匹配 → 创建新会话
                const folderBasename = selectedPlugin.split(/[\\/]/).pop();
                const 会话标题 = activeTab === 'optimize'
                    ? t('session.title_optimize', { name: folderBasename })
                    : (activeTab === 'visualize' ? t('session.title_visualize', { name: folderBasename }) : folderBasename);
                const newSession = await 创建会话(会话标题, selectedPlugin, activeTab);
                if (!newSession) {
                    Toast.error(t('session.create_fail'));
                    return null;
                }
                if (typeof options.onSessionReady === 'function') {
                    await options.onSessionReady(newSession, true);
                }
                return newSession.id;
            } catch (e) {
                Toast.error(t('session.prepare_fail_msg', { error: e.message }));
                return null;
            }
        },
        发送后清理: () => {
            input.value = '';
            input.style.height = "auto";
            if (附件 && !Array.isArray(options.附件)) 附件.清空();
            // 移除视觉能力警告：通过 input 向上查找 .nca-input-area
            const inputAreaEl = input.closest('.nca-input-area');
            if (inputAreaEl) 移除视觉能力警告(inputAreaEl);
            sendBtn.classList.remove("active");
        },
        消息列表获取: () => msgArea._nca消息列表,
        起始偏移获取: () => msgArea._nca起始偏移 || 0,
        // 面板不随流式切换会话，无需会话校验/虚拟滚动类钩子
    };
}

/**
 * 2. 加载会话消息 — 从API加载消息历史并渲染到容器
 *    气泡工厂复用会话核心（长消息折叠/附件缩略图/pending-sanitize 三界面统一）
 * @param {string} sessionId
 * @param {HTMLElement} msgArea - 消息容器
 * @param {Object} [options] - 发送面板消息 的参数集（编辑重生成的适配器组装用）
 */
export async function 加载会话消息(sessionId, msgArea, options = {}) {
    msgArea.innerHTML = "";
    msgArea._nca消息列表 = [];
    // 服务端强制分页后，首条消息在全量历史中的绝对索引（truncate_at 换算用）
    msgArea._nca起始偏移 = 0;
    // 缓存最新发送参数：历史气泡的编辑按钮回调组装适配器时取用
    msgArea._nca发送参数 = options;
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
            const bubble = _核心创建消息DOM(msg, index, {
                编辑点击: (host, idx) => {
                    const opts = msgArea._nca发送参数 || {};
                    const realIdx = typeof host._消息索引 === "number" ? host._消息索引 : idx;
                    进入编辑模式(_面板适配器构建(opts), host, host._原始内容 ?? "", realIdx);
                },
            });
            绑定代码块复制按钮(bubble);
            msgArea.appendChild(bubble);
        });
        // 进入会话一次性到底：同步定位 + rAF 二次校正（长消息折叠/图片等异步撑高后仍贴底）
        msgArea.scrollTop = msgArea.scrollHeight;
        requestAnimationFrame(() => { msgArea.scrollTop = msgArea.scrollHeight; });
    } catch (e) {
        console.warn("[节点梦工厂] 加载会话消息失败:", e);
    }
}

/**
 * 3. 发送面板消息 — 薄壳 shim：组装面板适配器后委托会话核心的统一发送管线
 * @param {Object} options
 * @param {HTMLTextAreaElement} options.input - 输入框
 * @param {HTMLButtonElement} options.sendBtn - 发送按钮
 * @param {HTMLElement} options.msgArea - 消息容器
 * @param {Object|Array} options.附件 - 附件组件实例（或重发场景附件数组）
 * @param {string} options.activeTab - 'optimize' | 'visualize'
 * @param {Function} options.getSessionId - () => sessionId
 * @param {Function} options.getPluginFolder - () => folderPath
 * @param {Function} [options.onSessionReady] - (session, isNew) => void
 */
export async function 发送面板消息(options) {
    if (_面板正在发送) return;
    _面板正在发送 = true;
    try {
        // 缓存最新发送参数：历史气泡编辑回调组装适配器时取用
        if (options.msgArea) options.msgArea._nca发送参数 = options;
        return await 发送会话消息(_面板适配器构建(options), options);
    } finally {
        _面板正在发送 = false;
    }
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
