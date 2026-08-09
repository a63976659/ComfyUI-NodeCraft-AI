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
import { 状态, 获取会话列表, 创建会话, 事件总线 } from "./交互与状态.js";
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
 * 2a. 短时窗持续贴底 — 覆盖进入会话后异步撑高场景
 *     图片解码/压缩缩略图替换/DOMPurify pending-sanitize 重渲染都在此窗口内完成；
 *     窗口结束自动断开，用户主动滚动（wheel/touch）立即终止，
 *     保证「会话中不持续强制，不干扰上翻历史」的既有约束
 * @param {HTMLElement} msgArea - 消息容器
 */
function _短时窗持续贴底(msgArea) {
    if (typeof msgArea._nca贴底清理 === "function") msgArea._nca贴底清理();
    const 贴底 = () => { msgArea.scrollTop = msgArea.scrollHeight; };
    // DOM 变更（重渲染/缩略图替换/新增节点）→ 重新贴底
    const mo = new MutationObserver(贴底);
    mo.observe(msgArea, { subtree: true, childList: true, attributes: true, characterData: true });
    // load 事件不冒泡：捕获阶段监听图片解码完成（初始 src 解码无 DOM 变更，MutationObserver 感知不到）
    const 媒体加载 = () => 贴底();
    msgArea.addEventListener("load", 媒体加载, true);
    let 已停止 = false;
    const 停止 = () => {
        if (已停止) return;
        已停止 = true;
        mo.disconnect();
        msgArea.removeEventListener("load", 媒体加载, true);
        msgArea.removeEventListener("wheel", 停止, true);
        msgArea.removeEventListener("touchmove", 停止, true);
        msgArea._nca贴底清理 = null;
    };
    // 用户主动滚动 → 立即终止贴底（对标 ChatGPT/Claude：用户上翻即停止跟随）
    msgArea.addEventListener("wheel", 停止, { capture: true, passive: true });
    msgArea.addEventListener("touchmove", 停止, { capture: true, passive: true });
    msgArea._nca贴底清理 = 停止;
    setTimeout(停止, 1500);
}

/**
 * 2. 加载会话消息 — 从 API 加载消息历史并渲染到容器
 *    气泡工厂复用会话核心（长消息折叠/附件缩略图/pending-sanitize 三界面统一）
 * @param {string} sessionId
 * @param {HTMLElement} msgArea - 消息容器
 * @param {Object} [options] - 发送面板消息 的参数集（编辑重生成的适配器组装用）
 */
export async function 加载会话消息(sessionId, msgArea, options = {}) {
    // 竞态防护：单调递增版本号，快速连点会话时晚到的旧响应直接丢弃
    msgArea._nca加载版本 = (msgArea._nca加载版本 || 0) + 1;
    const 本次版本 = msgArea._nca加载版本;
    // 终止上次加载遗留的短时窗贴底观察器
    if (typeof msgArea._nca贴底清理 === "function") msgArea._nca贴底清理();
    msgArea.innerHTML = "";
    msgArea._nca消息列表 = [];
    // 服务端强制分页后，首条消息在全量历史中的绝对索引（truncate_at 换算用）
    msgArea._nca起始偏移 = 0;
    // 缓存最新发送参数：历史气泡的编辑按钮回调组装适配器时取用
    msgArea._nca发送参数 = options;
    if (!sessionId) {
        msgArea.appendChild(el("div", { class: "nca-empty-state", text: t("chat.select_or_create") }));
        // 无会话：状态栏上下文指示器回到空闲占位态（与 develop 面板删除会话行为一致）
        try { 事件总线.emit('context-health-updated', { idle: true }); } catch (_) {}
        return;
    }
    try {
        // 显式窗口模式：仅加载最近一窗消息（首屏性能优化），start_index 写入 _nca起始偏移
        const res = await fetch(`${NCA_API_BASE}/sessions/${sessionId}/messages?recent=1`);
        if (!res.ok) return;
        const data = await res.json();
        if (本次版本 !== msgArea._nca加载版本) return; // 已被更新的加载取代，丢弃旧响应
        const messages = data.messages || [];
        msgArea._nca消息列表 = messages;
        msgArea._nca起始偏移 = data.start_index || 0;
        // 初始化状态栏上下文指示器（后端基于全量历史的离线估算，三面板共享状态栏）
        if (data.context_health) {
            try { 事件总线.emit('context-health-updated', data.context_health); } catch (_) {}
        }
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
        // 进入会话一次性到底：同步定位 + rAF 二次校正 + 短时窗持续贴底
        // （覆盖图片解码/压缩缩略图替换/DOMPurify 重渲染等异步撑高）
        msgArea.scrollTop = msgArea.scrollHeight;
        requestAnimationFrame(() => { msgArea.scrollTop = msgArea.scrollHeight; });
        _短时窗持续贴底(msgArea);
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
