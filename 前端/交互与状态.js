/**
 * @module 交互与状态
 * @description 全局状态管理与 API 交互模块
 *
 * 本模块是 NodeCraft AI 前端的核心中枢，承担三大职责：
 * 1. **事件总线** — 发布/订阅模式的跨组件通信（`事件总线`）
 * 2. **全局状态** — 单例状态对象（`状态`），存储会话、消息、模型选择等运行时数据
 * 3. **HTTP 客户端** — 统一封装 fetch 请求（`请求()`），内置超时控制与错误状态广播
 *
 * 数据流向：
 *   UI 组件 → API 函数 → 修改全局状态 → 事件总线广播 → UI 组件响应更新
 *
 * @example
 * import { 状态, 事件总线, 事件, 发送消息 } from './交互与状态.js';
 * 事件总线.on(事件.新消息追加, (msg) => console.log(msg));
 */

/**
 * @typedef {Object} 流式状态结构
 * @property {boolean} 活跃 - 是否有进行中的 SSE 流
 * @property {string} 累积内容 - 已接收的完整内容
 * @property {Object|null} 请求体 - 当前流的原始请求体
 * @property {AbortController|null} 中止控制器 - 当前流的 AbortController
 * @property {HTMLElement|null} 消息体引用 - 当前流写入的 DOM 元素
 * @property {HTMLElement|null} 消息容器引用 - 当前流的消息容器 DOM 引用
 * @property {HTMLElement|null} 滚动容器引用 - 当前流的滚动容器 DOM 引用
 * @property {Function|null} 完成回调引用 - 流结束时的完成回调
 * @property {HTMLElement|null} 工具指示器引用 - 工具执行状态指示器 DOM 元素
 */

/**
 * @typedef {Object} 全局状态结构
 * @property {string|null} 当前会话ID - 当前活跃的会话 UUID
 * @property {Array<Object>} 会话列表 - 已加载的会话列表
 * @property {Array<Object>} 当前消息列表 - 当前会话的消息数组
 * @property {boolean} 正在发送 - 是否有消息正在发送中
 * @property {'online'|'busy'|'error'} 连接状态 - 后端连接健康状态
 * @property {'local'|'api'} 模型来源 - 当前使用的模型类型
 * @property {Array<Object>} 本地模型列表 - 可用本地模型
 * @property {string} 选中本地模型 - 当前选中的本地模型名
 * @property {Array<Object>} 待发送附件 - 待发送的文件附件列表
 * @property {流式状态结构} 流式状态 - SSE 流式输出追踪状态
 * @property {Object} 设置 - 用户持久化设置
 */

// ═══════════════════════════════════════════════════════════════
// 交互与状态.js — 事件总线 + 全局状态 + HTTP 客户端
// NodeCraft AI — Luxury Terminal Edition
// ═══════════════════════════════════════════════════════════════

import { NCA_STORAGE_KEYS, 插件存储键, Toast, 设置当前模型上下文 } from "./工具函数.js";
import { 存储 } from "./存储引擎.js";
import { t, 获取当前语言 } from "./i18n.js";

// ─── 事件总线 ───────────────────────────────────────────────────
class 事件总线类 {
    constructor() {
        this._listeners = {};
    }
    on(event, fn) {
        (this._listeners[event] ||= []).push(fn);
    }
    off(event, fn) {
        if (!this._listeners[event]) return;
        this._listeners[event] = this._listeners[event].filter(f => f !== fn);
    }
    emit(event, ...args) {
        (this._listeners[event] || []).forEach(fn => fn(...args));
    }
}

export const 事件总线 = new 事件总线类();

// ─── 事件常量 ─────────────────────────────────────────────────
export const 事件 = {
    会话列表更新: "sessions-updated",
    会话切换: "session-switched",
    消息列表更新: "messages-updated",
    新消息追加: "message-appended",
    设置已加载: "settings-loaded",
    设置已保存: "settings-saved",
    发送状态变更: "sending-state-changed",
    状态栏更新: "status-updated",
    连接状态变更: "connection-state-changed",
    模型选择变更: "model-selection-changed",
    插件选择变更: "plugin-folder-changed",
    上下文健康更新: "context-health-updated",
    // 代码补全相关
    代码补全请求: "nca:code-completion-request",
    代码补全结果: "nca:code-completion-result",
    代码补全状态: "nca:code-completion-status",
    // 流式输出状态追踪
    流式状态变更: "streaming-state-changed",
};

// ─── 全局状态 ─────────────────────────────────────────────────
export const 状态 = {
    当前会话ID: null,
    会话列表: [],
    当前消息列表: [],
    // 服务端强制分页后，当前消息列表首条在全量历史中的绝对索引
    // （编辑重生成的 truncate_at 需叠加此偏移换算为服务端索引）
    消息起始偏移: 0,
    正在发送: false,
    连接状态: "online", // online | busy | error
    模型来源: "api",     // "local" 或 "api"
    本地模型列表: [],
    选中本地模型: "",
    待发送附件: [],       // 文件附件列表 [{name, type, size, data}]
    // ── 流式输出追踪（跨界面重建保持输出连续性）────────────────
    流式状态: {
        活跃: false,              // 是否有进行中的 SSE 流
        累积内容: "",             // 已接收的完整内容
        请求体: null,             // 当前流的原始请求体
        中止控制器: null,         // 当前流的 AbortController
        消息体引用: null,         // 当前流写入的 DOM 元素（可在重建时更换）
        消息容器引用: null,       // 当前流的消息容器 DOM 引用
        滚动容器引用: null,       // 当前流的滚动容器 DOM 引用
        完成回调引用: null,       // 流结束时的完成回调（重建时更换）
        工具指示器引用: null,     // 工具执行状态指示器 DOM 元素
        停止按钮引用: null,       // 当前显示的停止按钮 DOM（重建时更换，流结束统一移除）
    },
    设置: {
        model_source: "api",
        local_path: "",
        local_model_name: "",
        base_url: "",
        model_name: "",
        api_key: "",
        api_profiles: [],
        active_api_profile_id: "",
        temperature: 0.2,
        max_tokens: 4096,
    },
};

// ─── HTTP 客户端封装 ──────────────────────────────────────────
const API_BASE = "/ai-coder";
// 默认 30 秒全局超时；流式请求可传 0 或更大值以放宽限制
// 从设置中读取，设置未加载时用默认值 30000
let 默认请求超时 = 30000;

// ─── Token 安全存储 ───
// Token key 名称保持 "ComfyCommunity_Token"（与 RanKing 平台一致）。
// NCA 自己的会话副本写入 sessionStorage；同时从 localStorage 读取作为 fallback，
// 但绝不删除 localStorage 中的值——该键由 RanKing 插件管理（用户勾选"记住我"时写入），
// 删除会导致 RanKing 凭证在每次页面加载时丢失。
let _tokenCache = null;
// 会话切换防竞态：快速连续切换时，旧请求结果应被丢弃
let _currentSessionAbort = null;

export function _获取Token() {
    if (_tokenCache) return _tokenCache;
    let token = null;
    try {
        token = sessionStorage.getItem("ComfyCommunity_Token");
        if (!token) {
            // fallback：从 localStorage 读取 RanKing 插件保存的凭证（用户勾选"记住我"时）
            // 复制到 sessionStorage 作为本会话副本，但保留 localStorage 原值——
            // 该键由 RanKing 管理，删除会导致用户每次刷新都需重新登录。
            token = localStorage.getItem("ComfyCommunity_Token");
            if (token) {
                sessionStorage.setItem("ComfyCommunity_Token", token);
                // 注意：不调用 localStorage.removeItem("ComfyCommunity_Token")
            }
        }
    } catch (e) {
        if (e && e.name === 'QuotaExceededError') {
            console.warn("[节点梦工厂] 存储空间已满，无法迁移Token");
        } else {
            console.warn("[节点梦工厂] 存储访问异常:", (e && e.message) || e);
        }
    }
    _tokenCache = token;
    return token;
}

// 生成幂等键：用于防止重复提交（M8 幂等性保证）
function _生成幂等键() {
    if (typeof crypto !== "undefined" && crypto.getRandomValues) {
        const arr = new Uint8Array(16);
        crypto.getRandomValues(arr);
        return Array.from(arr, b => b.toString(16).padStart(2, "0")).join("");
    }
    // 降级方案
    return `${Date.now()}_${Math.random().toString(36).slice(2, 10)}`;
}

/**
 * 统一 HTTP 请求封装
 * 支持超时控制和错误状态广播。
 * @param {'GET'|'POST'|'PUT'|'DELETE'|'PATCH'} method - HTTP 方法
 * @param {string} path - API 路径（相对于 /ai-coder）
 * @param {Object|null} [body=null] - 请求体（自动 JSON 序列化）
 * @param {number} [timeout=默认请求超时] - 超时毫秒数，0 表示不限时
 * @param {Object|null} [extraHeaders=null] - 额外请求头（如幂等键）
 * @returns {Promise<Object>} 解析后的 JSON 响应
 * @throws {Error} HTTP 错误（含 status/code/data 属性）或超时/网络错误
 */
export async function 请求(method, path, body = null, timeout = 默认请求超时, extraHeaders = null) {
    const headers = { "Content-Type": "application/json" };

    // 调用方传入的额外请求头（例如幂等键）
    if (extraHeaders && typeof extraHeaders === "object") {
        Object.assign(headers, extraHeaders);
    }

    const options = {
        method,
        headers,
    };
    if (body) options.body = JSON.stringify(body);

    // 使用 AbortController 实现超时控制；timeout<=0 表示不设超时（用于流式/长连接）
    const controller = new AbortController();
    options.signal = controller.signal;
    const timeoutId = (timeout && timeout > 0)
        ? setTimeout(() => controller.abort(), timeout)
        : null;

    try {
        const response = await fetch(`${API_BASE}${path}`, options);
        if (!response.ok) {
            let errorMsg = `HTTP ${response.status}: ${response.statusText}`;
            let errData = {};
            try {
                errData = await response.json();
                // errData.error 可能是布尔值 true（非字符串），须优先取 message
                if (typeof errData.error === "string") errorMsg = errData.error;
                else if (errData.message) errorMsg = errData.message;
            } catch (_) {}
            const err = new Error(errorMsg);
            err.status = response.status;
            err.code = errData.code || null;
            err.data = errData;
            throw err;
        }
        状态.连接状态 = "online";
        事件总线.emit(事件.连接状态变更, "online");
        return response.json();
    } catch (e) {
        // 超时（AbortError）→ 友好中文提示，并标记连接异常
        if (e.name === "AbortError") {
            状态.连接状态 = "error";
            事件总线.emit(事件.连接状态变更, "error");
            throw new Error("请求超时，请检查网络连接");
        }
        if (e.message && (e.message.includes("Failed to fetch") || e.message.includes("NetworkError"))) {
            状态.连接状态 = "error";
            事件总线.emit(事件.连接状态变更, "error");
            try { Toast.error("网络连接失败，请检查网络"); } catch (_) {}
        }
        throw e;
    } finally {
        if (timeoutId) clearTimeout(timeoutId);
    }
}

// ─── 会话管理 API ─────────────────────────────────────────────
// 获取会话列表：
//   - type 可选；不传或传 'develop' 时同步刷新全局 状态.会话列表 并广播事件（保持开发插件流向后兼容）；
//   - 传入 'optimize'/'visualize' 时仅返回该类型会话列表，不污染全局状态，供独立组件使用。
/**
 * 获取会话列表
 * @param {string} [type] - 会话类型过滤：'develop'|'optimize'|'visualize'；不传返回开发类型
 * @returns {Promise<Array<Object>>} 会话对象数组
 */
export async function 获取会话列表(type) {
    try {
        const path = type ? `/sessions?type=${encodeURIComponent(type)}` : "/sessions";
        const data = await 请求("GET", path);
        const sessions = data.sessions || [];
        if (!type || type === "develop") {
            状态.会话列表 = sessions;
            事件总线.emit(事件.会话列表更新, 状态.会话列表);
        }
        return sessions;
    } catch (e) {
        console.error("[节点梦工厂] 获取会话列表失败:", e);
        return [];
    }
}

// 创建会话：
//   - type 可选，默认 'develop'，与会话视图管理器中开发插件流程保持一致；
//   - 仅当 type==='develop' 时刷新全局会话列表并自动切换为新会话（保持向后兼容）；
//   - optimize/visualize 创建后由调用方自行处理（通常通过 创建会话列表面板 控制对象 refresh()）。
/**
 * 创建新会话
 * @param {string} [title='新会话'] - 会话标题
 * @param {string} [plugin_folder=''] - 关联的插件文件夹名
 * @param {string} [type='develop'] - 会话类型
 * @returns {Promise<Object|null>} 创建的会话对象，失败返回 null
 */
export async function 创建会话(title = "新会话", plugin_folder = "", type = "develop") {
    try {
        const data = await 请求("POST", "/sessions", { title, plugin_folder, type });
        if (type === "develop") {
            await 获取会话列表("develop");
            if (data.session?.id) {
                await 切换会话(data.session.id);
            }
        }
        return data.session;
    } catch (e) {
        console.error("[节点梦工厂] 创建会话失败:", e);
        Toast.error(`创建会话失败: ${e.message || e}`);
        return null;
    }
}

export async function 更新会话标题(id, title) {
    const 新标题 = (title || "").trim();
    if (!新标题) return false;
    try {
        await 请求("PUT", `/sessions/${id}/title`, { title: 新标题 });
        // 同步本地会话列表内容
        const sess = 状态.会话列表.find(s => s.id === id);
        if (sess) sess.title = 新标题;
        事件总线.emit(事件.会话列表更新, 状态.会话列表);
        return true;
    } catch (e) {
        console.error("[节点梦工厂] 更新会话标题失败:", e);
        Toast.error(`更新会话标题失败: ${e.message || e}`);
        return false;
    }
}

// 删除会话：
//   - type 可选；不传或传 'develop' 时按旧逻辑刷新全局状态、清空当前会话；
//   - 'optimize'/'visualize' 类型仅完成后端删除，由组件自行刷新本地列表，避免污染开发插件全局状态。
/**
 * 删除会话
 * @param {string} id - 会话 UUID
 * @param {boolean} [deleteFolder=false] - 是否同时删除关联的插件文件夹
 * @param {string|null} [type=null] - 会话类型；develop 或空时刷新全局状态
 * @returns {Promise<boolean>} 是否删除成功
 */
export async function 删除会话(id, deleteFolder = false, type = null) {
    try {
        await 请求("DELETE", `/sessions/${id}`, { delete_folder: !!deleteFolder });
        if (!type || type === "develop") {
            if (状态.当前会话ID === id) {
                状态.当前会话ID = null;
                状态.当前消息列表 = [];
                状态.消息起始偏移 = 0;
                事件总线.emit(事件.消息列表更新, []);
            }
            await 获取会话列表("develop");
        }
        return true;
    } catch (e) {
        console.error("[节点梦工厂] 删除会话失败:", e);
        Toast.error(`删除会话失败: ${e.message || e}`);
        return false;
    }
}

/**
 * 中止当前进行中的流式输出（切换会话/关闭面板前的残留清理）
 *
 * 处理顺序关键：先在气泡上标记「已中断」并移除光标（保留已生成内容，不删除气泡），
 * 再解除 DOM 引用（使迟到的流式渲染通过 getter 拿到 null 成为 no-op），最后 abort
 * fetch 并复位全局流式状态。
 * @returns {boolean} 是否实际中止了一个进行中的流
 */
export function 中止当前流式() {
    const 流式 = 状态.流式状态;
    if (!流式.活跃) return false;
    const body = 流式.消息体引用;
    if (body) {
        try {
            body.querySelectorAll(".nca-streaming-cursor").forEach(c => c.remove());
            const 提示 = document.createElement("div");
            提示.className = "nca-stream-interrupted";
            提示.textContent = "⚠ 生成已中断";
            提示.style.cssText = "margin-top:6px;font-size:11px;color:var(--nca-fg-dim,#9ca3af);opacity:.8;";
            body.appendChild(提示);
        } catch (_) {}
    }
    if (流式.工具指示器引用) { try { 流式.工具指示器引用.remove(); } catch (_) {} }
    流式.消息体引用 = null;
    流式.消息容器引用 = null;
    流式.滚动容器引用 = null;
    流式.工具指示器引用 = null;
    流式.完成回调引用 = null;
    try { 流式.中止控制器 && 流式.中止控制器.abort(); } catch (_) {}
    流式.活跃 = false;
    流式.累积内容 = "";
    流式.请求体 = null;
    流式.中止控制器 = null;
    事件总线.emit(事件.流式状态变更, false);
    return true;
}

/**
 * 切换当前活跃会话（含防竞态处理）
 * @param {string} id - 目标会话 UUID
 * @returns {Promise<void>}
 */
export async function 切换会话(id) {
    // 有进行中的流式输出时先中止并清理残留，避免旧流写入新会话视图
    中止当前流式();
    if (_currentSessionAbort) _currentSessionAbort.abort();
    _currentSessionAbort = new AbortController();
    const thisAbort = _currentSessionAbort;

    状态.当前会话ID = id;
    事件总线.emit(事件.会话切换, id);
    事件总线.emit(事件.状态栏更新, "加载会话...");
    // 同步会话关联的插件文件夹：有则恢复，无则清空，确保"已选择"标签与会话状态严格一致
    const sess = (状态.会话列表 || []).find(s => s.id === id);
    设置插件文件夹(sess && sess.plugin_folder ? sess.plugin_folder : "");

    try {
        // 显式窗口模式：仅加载最近一窗消息（首屏性能优化），更早历史由 加载更早消息 补取
        const data = await 请求("GET", `/sessions/${id}/messages?recent=1`);
        if (thisAbort.signal.aborted) return; // 已被更新的切换覆盖
        状态.当前消息列表 = data.messages || [];
        状态.消息起始偏移 = data.start_index || 0;
        事件总线.emit(事件.消息列表更新, 状态.当前消息列表);
    } catch (e) {
        if (thisAbort.signal.aborted) return; // 已被更新的切换覆盖
        console.error("[节点梦工厂] 加载消息失败:", e);
        状态.当前消息列表 = [];
        状态.消息起始偏移 = 0;
        事件总线.emit(事件.消息列表更新, []);
    }
}

// ─── 插件文件夹联动 ───────────────────────────────────────────
// 统一入口：写入 localStorage 并广播事件，驱动文件夹选择器、项目信息栏等同步刷新
// scope 区分三个工作界面（'develop' | 'optimize' | 'visualize'），各自写入独立键、
// 事件 payload 携带 scope，订阅方按 scope 过滤，实现三界面文件夹选择完全隔离。
export function 设置插件文件夹(name, scope = 'develop') {
    const value = (name || "").trim();
    const key = 插件存储键(scope);
    // 迁至 IndexedDB 异步写入（fire-and-forget）；存储引擎内部同步刷新 localStorage 影子缓存，
    // 保证后续 同步读取 调用（例如设置面板、项目信息栏）仍能读到最新值
    if (value) {
        存储.写入(key, value).catch(() => {});
    } else {
        存储.删除(key).catch(() => {});
    }
    事件总线.emit(事件.插件选择变更, { scope, value });
}

export async function 加载会话消息(sessionId) {
    try {
        // 显式窗口模式：仅加载最近一窗消息（首屏性能优化）
        const data = await 请求("GET", `/sessions/${sessionId}/messages?recent=1`);
        状态.当前消息列表 = data.messages || [];
        状态.消息起始偏移 = data.start_index || 0;
        事件总线.emit(事件.消息列表更新, 状态.当前消息列表);
    } catch (e) {
        console.error("[节点梦工厂] 加载消息失败:", e);
        状态.当前消息列表 = [];
        状态.消息起始偏移 = 0;
        事件总线.emit(事件.消息列表更新, []);
    }
}

// ─── 加载更早消息（显式分页向前补取）────────────────────────
// 首屏窗口模式（?recent=1）下 消息起始偏移 > 0 表示前面还有历史消息；
// 通过 ?page=X&page_size=50 取回包含索引 偏移-1 的那一页，页边界固定，
// 与当前窗口重叠部分通过切片去重后前插到 状态.当前消息列表。
// 前插与偏移同步更新，编辑重生成的 truncate_at + 消息起始偏移 换算天然保持正确。
const _更早消息页大小 = 50;
let _正在加载更早消息 = false;

/**
 * 向前补取上一段历史消息并前插到当前消息列表
 * @returns {Promise<boolean>} 是否成功前插并触发了重新渲染
 */
export async function 加载更早消息() {
    const 偏移 = 状态.消息起始偏移 || 0;
    const sessionId = 状态.当前会话ID;
    if (偏移 <= 0 || !sessionId) return false;
    if (_正在加载更早消息 || 状态.正在发送) return false;
    _正在加载更早消息 = true;
    try {
        const page = Math.ceil(偏移 / _更早消息页大小);
        const 页起始 = (page - 1) * _更早消息页大小;
        const data = await 请求("GET", `/sessions/${sessionId}/messages?page=${page}&page_size=${_更早消息页大小}`);
        if (状态.当前会话ID !== sessionId) return false; // 已切换会话，丢弃结果
        // 仅取当前窗口之前的部分（页尾可能与已加载窗口重叠）
        const 片段 = (data.messages || []).slice(0, 偏移 - 页起始);
        if (片段.length === 0) return false;
        状态.当前消息列表.unshift(...片段);
        状态.消息起始偏移 = 页起始;
        事件总线.emit(事件.消息列表更新, 状态.当前消息列表);
        return true;
    } catch (e) {
        console.error("[节点梦工厂] 加载更早消息失败:", e);
        return false;
    } finally {
        _正在加载更早消息 = false;
    }
}


// ─── 聊天 API ─────────────────────────────────────────────────
/**
 * 发送用户消息并获取 AI 回复（非流式模式）
 * 自动处理：无会话时创建、本地追加用户消息、发送状态广播、幂等键生成。
 * @param {string} content - 用户消息文本
 * @param {Array<Object>} [attachments=[]] - 附件列表 [{name, type, size, data}]
 * @returns {Promise<Object|null>} AI 回复消息对象，失败返回 null
 */
export async function 发送消息(content, attachments = []) {
    if (状态.正在发送) return null;
    状态.正在发送 = true;
    状态.连接状态 = "busy";
    事件总线.emit(事件.发送状态变更, true);
    事件总线.emit(事件.连接状态变更, "busy");
    事件总线.emit(事件.状态栏更新, "思考中...");

    // 如果没有当前会话，自动创建一个
    if (!状态.当前会话ID) {
        const session = await 创建会话(content.substring(0, 20) || "新对话");
        if (!session) {
            状态.正在发送 = false;
            事件总线.emit(事件.发送状态变更, false);
            事件总线.emit(事件.状态栏更新, "创建会话失败");
            return null;
        }
    }

    // 先追加用户消息到本地
    const 用户消息 = { role: "user", content, timestamp: Date.now(), attachments: attachments.length > 0 ? attachments : undefined };
    状态.当前消息列表.push(用户消息);
    事件总线.emit(事件.新消息追加, 用户消息);

    try {
        const requestBody = {
            message: content,
            session_id: 状态.当前会话ID,
            model_source: 状态.模型来源,
            local_model_name: 状态.选中本地模型 || "",
            plugin_context: localStorage.getItem(NCA_STORAGE_KEYS.plugin),
            activeTab: localStorage.getItem(NCA_STORAGE_KEYS.activeTab) || "develop",
            language: 获取当前语言(),
        };
        if (attachments.length > 0) {
            requestBody.attachments = attachments;
        }
        // 聊天请求使用 chat_timeout（默认 120 秒），避免长回复被 30 秒默认超时截断
        const chatTimeout = 状态.设置?.chat_timeout || 默认请求超时;
        // M8: 幂等性保证 — 为聊天请求附加幂等键，避免网络重试或快速点击导致的重复提交
        const data = await 请求("POST", "/chat", requestBody, chatTimeout, {
            "X-Idempotency-Key": _生成幂等键(),
        });

        // 后端直连 API 模式下不再返回 billing 字段

        const AI回复 = {
            role: "assistant",
            content: _过滤工具标记(data.reply) || "（无回复）",
            timestamp: Date.now(),
        };
        状态.当前消息列表.push(AI回复);
        事件总线.emit(事件.新消息追加, AI回复);
        事件总线.emit(事件.状态栏更新, "就绪");
        return AI回复;
    } catch (e) {
        // 本地 API Key 直连模式下不再区分 402/403会员/余额，统一归为通用错误提示
        const 错误消息 = {
            role: "assistant",
            content: `⚠ 请求失败: ${e.message}`,
            timestamp: Date.now(),
        };
        状态.当前消息列表.push(错误消息);
        事件总线.emit(事件.新消息追加, 错误消息);
        事件总线.emit(事件.状态栏更新, "错误");
        状态.连接状态 = "error";
        事件总线.emit(事件.连接状态变更, "error");
        return null;
    } finally {
        状态.正在发送 = false;
        事件总线.emit(事件.发送状态变更, false);
    }
}

// ─── 设置 API ─────────────────────────────────────────────────
/**
 * 从后端加载用户设置并同步到全局状态
 * @returns {Promise<Object>} 设置对象
 */
export async function 加载设置() {
    try {
        const data = await 请求("GET", "/settings");
        Object.assign(状态.设置, data);
        // 从设置同步超时配置（设置未加载时用默认值）
        默认请求超时 = 状态.设置.request_timeout || 30000;
        // 从持久化的设置同步模型选择到全局状态字段（重启后恢复用户上次选择）
        状态.模型来源 = 状态.设置.model_source || "api";
        状态.选中本地模型 = 状态.设置.local_model_name || "";
        // 若上次为本地模式，预先拉取本地模型列表，避免各面板首次渲染时空白
        if (状态.模型来源 === "local") {
            try {
                const m = await 请求("GET", "/local-models");
                状态.本地模型列表 = m.models || [];
            } catch (_) { /* 忽略，UI 后续可重试 */ }
        }
        事件总线.emit(事件.设置已加载, 状态.设置);
        // 通知所有面板的模型切换栏同步刷新
        事件总线.emit(事件.模型选择变更, {
            模型来源: 状态.模型来源,
            选中本地模型: 状态.选中本地模型,
        });
        return 状态.设置;
    } catch (e) {
        console.error("[节点梦工厂] 加载设置失败:", e);
        return 状态.设置;
    }
}

/**
 * 保存设置到后端并同步全局状态
 * @param {Object} settings - 要保存的设置字段（增量合并）
 * @returns {Promise<boolean>} 是否保存成功
 */
export async function 保存设置(settings) {
    try {
        // 本地 API Key 直连后不再校验会员权限，直接提交保存
        await 请求("POST", "/settings", settings);
        Object.assign(状态.设置, settings);
        // 同步超时配置（若保存的设置中包含超时字段）
        if (settings.request_timeout !== undefined) 默认请求超时 = settings.request_timeout || 30000;
        // 当保存包含模型字段时，同步全局状态，避免设置面板等其他入口造成状态不一致
        if (settings.model_source !== undefined) 状态.模型来源 = settings.model_source;
        if (settings.local_model_name !== undefined) 状态.选中本地模型 = settings.local_model_name;
        事件总线.emit(事件.设置已保存, 状态.设置);
        // 若涉及模型字段，额外触发模型选择变更事件，驱动三个面板同步 UI
        if (settings.model_source !== undefined || settings.local_model_name !== undefined) {
            事件总线.emit(事件.模型选择变更, {
                模型来源: 状态.模型来源,
                选中本地模型: 状态.选中本地模型,
            });
        }
        return true;
    } catch (e) {
        console.error("[节点梦工厂] 保存设置失败:", e);
        Toast.error(`保存设置失败: ${e.message || e}`);
        return false;
    }
}

// ─── 模型选择统一变更入口 ──────────────────────────────────────
// 三个面板（开发插件 / 优化插件 / 功能可视化）的模型切换栏统一调用此函数：
// 1) 更新全局状态  2) 通知所有面板刷新 UI  3) 持久化到后端
/**
 * 统一模型选择变更入口（三面板共享）
 * 更新全局状态 → 广播事件 → 持久化到后端。
 * @param {Object} options
 * @param {'local'|'api'} [options.模型来源] - 模型来源类型
 * @param {string} [options.选中本地模型] - 本地模型名称
 * @param {string} [options.API模型名] - API 模型名称
 * @returns {Promise<void>}
 */
export async function 更新模型选择({ 模型来源, 选中本地模型, API模型名 } = {}) {
    if (模型来源 !== undefined) 状态.模型来源 = 模型来源;
    if (选中本地模型 !== undefined) 状态.选中本地模型 = 选中本地模型;
    // API 模型名直接写入设置（model_name），供底部栏/设置面板共享持久化
    if (API模型名 !== undefined) 状态.设置.model_name = API模型名;

    // 切到本地模式时，确保本地模型列表已加载，并自动选第一个
    if (状态.模型来源 === "local") {
        if (状态.本地模型列表.length === 0) {
            try {
                const m = await 请求("GET", "/local-models");
                状态.本地模型列表 = m.models || [];
            } catch (_) {}
        }
        if (状态.本地模型列表.length > 0 && !状态.选中本地模型) {
            状态.选中本地模型 = 状态.本地模型列表[0].name;
        }
    }

    // 通知所有面板刷新（同源面板也会刷新，但 select 是程序化重建，不会回环触发 change）
    事件总线.emit(事件.模型选择变更, {
        模型来源: 状态.模型来源,
        选中本地模型: 状态.选中本地模型,
    });

    // 持久化（使用底层请求避免再次触发 设置已保存 + 模型选择变更 造成重复刷新）
    try {
        const newSettings = {
            ...状态.设置,
            model_source: 状态.模型来源,
            local_model_name: 状态.选中本地模型,
        };
        await 请求("POST", "/settings", newSettings);
        Object.assign(状态.设置, newSettings);
        事件总线.emit(事件.设置已保存, 状态.设置);
    } catch (e) {
        console.error("[节点梦工厂] 持久化模型选择失败:", e);
    }
}

// 各模型推荐的默认最大输出 tokens（用户未填写时采用；按模型名模糊匹配，匹配不到回退 4096）
const 模型默认MaxTokens表 = [
    [/glm/i, 16384],
    [/kimi/i, 10000],
    [/deepseek/i, 8192],
];
export function 模型默认MaxTokens(模型名) {
    const 名 = String(模型名 || "");
    const 命中 = 模型默认MaxTokens表.find(([re]) => re.test(名));
    return 命中 ? 命中[1] : 4096;
}

// 各模型 API 允许的最大输出 tokens 上限（据官方文档；匹配不到回退 32768 保守值）
const 模型MaxTokens上限表 = [
    [/kimi/i, 1048576],    // Kimi K3：max_completion_tokens 默认 131072，最大 1M（1024*1024）
    [/deepseek/i, 393216], // DeepSeek V4 系列：输出长度最大 384K
    [/glm/i, 131072],      // GLM-5.2：最大输出 128K
];
export function 模型MaxTokens上限(模型名) {
    const 名 = String(模型名 || "");
    const 命中 = 模型MaxTokens上限表.find(([re]) => re.test(名));
    return 命中 ? 命中[1] : 32768;
}

// ─── 思考模式（开关 + 深度，模型切换栏按钮共享）───────────────

// 档位循环顺序（空串 = 不传参数，服务端取默认值）
const 思考深度档位 = ["", "low", "high", "max"];
const 思考模式档位 = ["", "on", "off"];

// 思考能力表：与后端 API模型客户端._思考能力表 一一对应（按模型名前缀匹配，顺序敏感）
// 开关：能否通过参数开/关思考；深度：能否传 reasoning_effort 调推理强度
const 思考能力表 = [
    ["kimi-k3", { 开关: false, 深度: true }],   // 始终思考，仅可调深度
    ["kimi-k2.7", { 开关: false, 深度: false }],
    ["kimi-k2.6", { 开关: true, 深度: false }],
    ["kimi-k2.5", { 开关: true, 深度: false }],
    ["kimi-", { 开关: false, 深度: false }],
    ["deepseek-v4", { 开关: true, 深度: true }],  // thinking.type + reasoning_effort 均支持
    ["deepseek-reasoner", { 开关: false, 深度: false }],
    ["qwen3", { 开关: true, 深度: false }],       // 混合思考，enable_thinking 开关
    ["qwen-plus", { 开关: true, 深度: false }],
    ["qwen-turbo", { 开关: true, 深度: false }],
    ["qwen-flash", { 开关: true, 深度: false }],
];
const 无思考能力 = { 开关: false, 深度: false };

function 思考能力(模型名) {
    const 名 = String(模型名 || "").toLowerCase();
    const 命中 = 思考能力表.find(([前缀]) => 名.startsWith(前缀));
    return 命中 ? 命中[1] : 无思考能力;
}

// 是否支持 reasoning_effort 档位（K3 / DeepSeek V4）
export function 支持思考深度(模型名) {
    return 思考能力(模型名).深度;
}

// 是否支持思考开关（DeepSeek V4 / Kimi K2.6・K2.5 / 千问 Qwen3；K3始终思考无开关）
export function 支持思考开关(模型名) {
    return 思考能力(模型名).开关;
}

// 读取当前激活 API 配置的某个思考字段（无激活配置时回退顶层设置）
function 读思考字段(字段名) {
    const 列表 = Array.isArray(状态.设置.api_profiles) ? 状态.设置.api_profiles : [];
    const 配置 = 列表.find((p) => p && p.id === 状态.设置.active_api_profile_id);
    return (配置 ? 配置[字段名] : 状态.设置[字段名]) || "";
}

/**
 * 循环切换激活 API 配置的某个思考字段（写入配置 + 顶层设置并持久化）
 * @param {string} 字段名 - reasoning_effort 或 thinking_mode
 * @param {string[]} 档位表 - 循环顺序
 * @returns {Promise<string>} 切换后的档位
 */
async function 切换思考字段(字段名, 档位表) {
    const 列表 = Array.isArray(状态.设置.api_profiles) ? 状态.设置.api_profiles : [];
    const 配置 = 列表.find((p) => p && p.id === 状态.设置.active_api_profile_id);
    const 当前 = (配置 ? 配置[字段名] : 状态.设置[字段名]) || "";
    const 下一个 = 档位表[(档位表.indexOf(当前) + 1) % 档位表.length];
    if (配置) 配置[字段名] = 下一个 || null;
    状态.设置[字段名] = 下一个;
    try {
        await 请求("POST", "/settings", { [字段名]: 下一个, api_profiles: 状态.设置.api_profiles });
        事件总线.emit(事件.设置已保存, 状态.设置);
    } catch (e) {
        console.error(`[节点梦工厂] 切换 ${字段名} 失败:`, e);
        Toast.error(`切换失败: ${e.message || e}`);
    }
    return 下一个;
}

// 读取当前激活 API 配置的思考深度（无激活配置时回退顶层设置）
export function 当前思考深度() {
    return 读思考字段("reasoning_effort");
}

// 读取当前激活 API 配置的思考开关状态（"" | "on" | "off"）
export function 当前思考模式() {
    return 读思考字段("thinking_mode");
}

/**
 * 循环切换激活 API 配置的思考深度（默认→low→high→max→默认）
 * 同步顶层 reasoning_effort 并持久化；模型切换栏的思考深度按钮共用。
 * @returns {Promise<string>} 切换后的档位（空串 = 默认不传）
 */
export async function 切换思考深度() {
    return 切换思考字段("reasoning_effort", 思考深度档位);
}

/**
 * 循环切换激活 API 配置的思考开关（默认→开→关→默认）
 * 后端按供应商转成 thinking.type（DeepSeek/Kimi K2.x）或 enable_thinking（千问）。
 * @returns {Promise<string>} 切换后的状态（"" = 不传参数，跟随服务端默认）
 */
export async function 切换思考模式() {
    return 切换思考字段("thinking_mode", 思考模式档位);
}

/**
 * 切换当前激活的 API 配置方案（三面板 API 下拉共享）
 * 将所选配置的连接信息（供应商/Base URL/Key/模型名）同步到顶层设置字段并持久化，
 * 后端聊天/计费/能力检测继续读顶层字段，无需感知多配置。
 * @param {string} 配置ID - api_profiles 中的配置 id
 * @returns {Promise<boolean>} 是否切换成功
 */
export async function 切换API配置(配置ID) {
    const 列表 = Array.isArray(状态.设置.api_profiles) ? 状态.设置.api_profiles : [];
    const 配置 = 列表.find((p) => p && p.id === 配置ID);
    if (!配置) return false;
    状态.设置.active_api_profile_id = 配置.id;
    状态.设置.api_provider = 配置.api_provider || "custom";
    状态.设置.base_url = 配置.base_url || "";
    状态.设置.api_key = 配置.api_key || "";
    状态.设置.model_name = 配置.model_name || "";
    // 最大输出 tokens 随配置方案切换（用户未填写的方案自动采用模型推荐默认值）
    状态.设置.max_tokens = 配置.max_tokens || 模型默认MaxTokens(配置.model_name);
    // 思考深度/开关随配置方案切换（空 = 不传参数，后端仅对支持的模型透传）
    状态.设置.reasoning_effort = 配置.reasoning_effort || "";
    状态.设置.thinking_mode = 配置.thinking_mode || "";

    // 通知所有面板刷新（select 程序化重建，不会回环触发 change），并清除模型能力缓存
    事件总线.emit(事件.模型选择变更, {
        模型来源: 状态.模型来源,
        选中本地模型: 状态.选中本地模型,
    });

    // 持久化（与 更新模型选择 同样使用底层请求，避免重复刷新）
    try {
        await 请求("POST", "/settings", { ...状态.设置 });
        事件总线.emit(事件.设置已保存, 状态.设置);
        return true;
    } catch (e) {
        console.error("[节点梦工厂] 切换 API 配置失败:", e);
        Toast.error(`切换 API 配置失败: ${e.message || e}`);
        return false;
    }
}

// ─── 模型能力查询 ─────────────────────────────────────────────
let _能力缓存 = null;
let _能力缓存时间 = 0;
const _能力缓存TTL = 60000; // 60秒

/**
 * 查询当前模型的能力（是否支持 vision / 文件内容）。
 * 带 60 秒缓存；模型切换时自动清除。
 * 降级策略：查询失败默认启用（宽容策略）。
 */
export async function 查询模型能力() {
    const now = Date.now();
    if (_能力缓存 && (now - _能力缓存时间) < _能力缓存TTL) {
        return _能力缓存;
    }
    try {
        // 传入当前模型信息，避免刚切换模型但设置未落盘时读到旧值
        const params = new URLSearchParams();
        params.set("model_source", 状态.模型来源 || "api");
        if (状态.模型来源 === "local" && 状态.选中本地模型) {
            params.set("model_name", 状态.选中本地模型);
        } else if (状态.设置?.model_name) {
            params.set("model_name", 状态.设置.model_name);
        }
        const resp = await 请求("GET", `/model-capabilities?${params}`);
        _能力缓存 = resp;
        _能力缓存时间 = now;
        return resp;
    } catch (e) {
        console.warn("[节点梦工厂] 查询模型能力失败，降级为全部启用:", e);
        return { supports_vision: true, supports_file_content: true };
    }
}

// 模型切换时清除能力缓存并更新工具函数中的模型上下文
事件总线.on(事件.模型选择变更, ({ 模型来源, 选中本地模型 }) => {
    _能力缓存 = null;
    设置当前模型上下文({
        model_source: 模型来源 || '',
        model_name: 模型来源 === 'local' ? (选中本地模型 || '') : '',
    });
});

// ─── 文件操作 API ─────────────────────────────────────────────
export async function 创建插件文件夹(pluginName) {
    try {
        const data = await 请求("POST", "/create-folder", { plugin_name: pluginName });
        return data;
    } catch (e) {
        console.error("[节点梦工厂] 创建文件夹失败:", e);
        return { success: false, message: `请求失败: ${e.message}` };
    }
}

// ─── 本地模型 API ─────────────────────────────────────────────
export async function 获取本地模型列表() {
    try {
        const data = await 请求("GET", "/local-models");
        状态.本地模型列表 = data.models || [];
        return 状态.本地模型列表;
    } catch (e) {
        console.error("[节点梦工厂][诊断] 获取本地模型列表失败:", e);
        return [];
    }
}

// ─── 插件目录管理 ──────────────────────────────────────────────
export async function 获取本地插件列表() {
    try {
        const data = await 请求("GET", "/local-plugins");
        return data.plugins || [];
    } catch (e) {
        console.error("[节点梦工厂] 获取插件列表失败:", e);
        return [];
    }
}

async function 浏览文件夹(initialDir = "") {
    try {
        const data = await 请求("POST", "/browse-folder", { initial_dir: initialDir });
        return data.folders || [];
    } catch (e) {
        console.error("[节点梦工厂] 浏览文件夹失败:", e);
        return [];
    }
}

// ─── 工具函数 ─────────────────────────────────────────────────

/**
 * 过滤消息中的 tool_call 标记
 * 移除 <tool_call>...</tool_call> 标记，保留进度提示
 */
function _过滤工具标记(text) {
    if (!text) return text;
    // 移除完整的 tool_call 标记对
    let cleaned = text.replace(/<tool_call>[\s\S]*?<\/tool_call>/g, '');
    // 移除可能残留的不完整开始标记（流式场景）
    cleaned = cleaned.replace(/<tool_call>[^<]*$/g, '');
    // 清理多余空行（3个以上连续换行压缩为2个）
    cleaned = cleaned.replace(/\n{3,}/g, '\n\n');
    return cleaned.trim();
}

export function 获取当前模型名称() {
    if (状态.模型来源 === "local" || 状态.设置.model_source === "local") {
        return 状态.选中本地模型 || 状态.设置.local_model_name || "local";
    }
    return 状态.设置.model_name || "未配置";
}

export function 推导平台名称(baseUrl) {
    if (!baseUrl) return "未配置";
    try {
        const host = new URL(baseUrl).hostname;
        const platformMap = {
            "api.deepseek.com": "DeepSeek",
            "api.siliconflow.cn": "SiliconFlow",
            "dashscope.aliyuncs.com": "阿里通义",
            "open.bigmodel.cn": "智谱GLM",
            "ark.cn-beijing.volces.com": "火山引擎",
            "api.openai.com": "OpenAI",
        };
        if (host === "127.0.0.1" || host === "localhost") return "本地服务";
        return platformMap[host] || host;
    } catch {
        return baseUrl;
    }
}

export function 获取会话序号() {
    if (!状态.当前会话ID) return null;
    const idx = 状态.会话列表.findIndex(s => s.id === 状态.当前会话ID);
    return idx >= 0 ? idx + 1 : null;
}

export function 格式化时间(timestamp) {
    if (!timestamp) return "";
    const d = new Date(timestamp);
    const now = new Date();
    const diff = now - d;

    if (diff < 86400000) {
        const locale = 获取当前语言() === "en" ? "en-US" : "zh-CN";
        return d.toLocaleTimeString(locale, { hour: "2-digit", minute: "2-digit" });
    } else if (diff < 172800000) {
        return t("time.yesterday");
    } else {
        return t("time.days_ago", { n: Math.floor(diff / 86400000) });
    }
}

/**
 * 判断一个 fetch 流式异常是否属于“可重连的网络错误”。
 * - AbortError：用户主动取消，不重连
 * - TypeError / NetworkError / Failed to fetch：判为网络问题，可重连
 */
export function 是可重连错误(error) {
    if (!error) return false;
    const msg = String(error.message || error);
    // 用户主动取消不重试
    if (error.name === 'AbortError') return false;
    // 网络级错误
    if (error instanceof TypeError) return true;
    // 网络错误关键词
    if (msg.includes('Failed to fetch') || 
        msg.includes('NetworkError') || 
        msg.includes('ECONNRESET') || 
        msg.includes('Load failed')) return true;
    // HTTP 5xx 服务器临时错误
    if (/\b5\d{2}\b/.test(msg)) return true;
    return false;
}

// ===== 上下文健康度指示器 =====
function _创建上下文指示器() {
    // 找到状态栏右侧区域
    const statusRight = document.querySelector('.status-right');
    if (!statusRight) return;

    // 避免重复创建
    if (document.querySelector('.nca-context-indicator')) return;

    // 创建分隔符
    const separator = document.createElement('span');
    separator.className = 'status-separator';
    separator.textContent = '|';

    // 创建指示器容器
    const indicator = document.createElement('span');
    indicator.className = 'nca-context-indicator';
    indicator.title = '上下文使用率';
    indicator.innerHTML = `
        <span class="context-bar-bg">
            <span class="context-bar-fill"></span>
        </span>
        <span class="context-text">0%</span>
    `;

    statusRight.appendChild(separator);
    statusRight.appendChild(indicator);
}

// DOM 就绪后创建指示器
if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', _创建上下文指示器);
} else {
    // 延迟确保状态栏已渲染
    setTimeout(_创建上下文指示器, 500);
}

// 监听上下文健康度更新
事件总线.on('context-health-updated', (data) => {
    // 确保指示器存在
    _创建上下文指示器();

    const fill = document.querySelector('.context-bar-fill');
    const text = document.querySelector('.context-text');
    if (!fill || !text) return;

    const percent = Math.min(data.usage_percent || 0, 100);
    fill.style.width = `${percent}%`;
    text.textContent = `${Math.round(percent)}%`;

    // 设置级别颜色
    fill.className = 'context-bar-fill';
    if (percent >= 95) {
        fill.classList.add('level-critical');
    } else if (percent >= 85) {
        fill.classList.add('level-warning');
    } else if (percent >= 70) {
        fill.classList.add('level-info');
    }
});
