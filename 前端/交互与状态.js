// ═══════════════════════════════════════════════════════════════
// 交互与状态.js — 事件总线 + 全局状态 + HTTP 客户端
// NodeCraft AI — Luxury Terminal Edition
// ═══════════════════════════════════════════════════════════════

import { NCA_STORAGE_KEYS, Toast, 安全存储读 } from "./工具函数.js";
import { 存储 } from "./存储引擎.js";

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
};

// ─── 全局状态 ─────────────────────────────────────────────────
export const 状态 = {
    当前会话ID: null,
    会话列表: [],
    当前消息列表: [],
    正在发送: false,
    连接状态: "online", // online | busy | error
    模型来源: "api",     // "local" 或 "api"
    本地模型列表: [],
    选中本地模型: "",
    待发送附件: [],       // 文件附件列表 [{name, type, size, data}]
    设置: {
        model_source: "api",
        local_path: "",
        local_model_name: "",
        base_url: "",
        model_name: "",
        api_key: "",
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

// ─── CSRF 防护 ───
// 为所有非 GET 请求注入 X-CSRF-Token 头；首次访问时本地生成并保存到 sessionStorage。
function _获取或生成CsrfToken() {
    let csrf = null;
    try {
        csrf = sessionStorage.getItem("_nca_csrf");
        if (!csrf) {
            csrf = (typeof crypto !== "undefined" && crypto.randomUUID)
                ? crypto.randomUUID()
                : Date.now().toString(36) + Math.random().toString(36).slice(2);
            sessionStorage.setItem("_nca_csrf", csrf);
        }
    } catch (_) {
        csrf = Date.now().toString(36) + Math.random().toString(36).slice(2);
    }
    return csrf;
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

export async function 请求(method, path, body = null, timeout = 默认请求超时, extraHeaders = null) {
    const headers = { "Content-Type": "application/json" };

    // 注入 RanKing Token（如果已登录）
    try {
        const token = _获取Token();
        if (token) {
            headers["Authorization"] = `Bearer ${token}`;
        }
    } catch (_) {}

    // CSRF 防护：所有非 GET 请求注入 X-CSRF-Token
    if (method !== "GET") {
        headers["X-CSRF-Token"] = _获取或生成CsrfToken();
    }

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
export async function 删除会话(id, deleteFolder = false, type = null) {
    try {
        await 请求("DELETE", `/sessions/${id}`, { delete_folder: !!deleteFolder });
        if (!type || type === "develop") {
            if (状态.当前会话ID === id) {
                状态.当前会话ID = null;
                状态.当前消息列表 = [];
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

export async function 切换会话(id) {
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
        const data = await 请求("GET", `/sessions/${id}/messages`);
        if (thisAbort.signal.aborted) return; // 已被更新的切换覆盖
        状态.当前消息列表 = data.messages || [];
        事件总线.emit(事件.消息列表更新, 状态.当前消息列表);
    } catch (e) {
        if (thisAbort.signal.aborted) return; // 已被更新的切换覆盖
        console.error("[节点梦工厂] 加载消息失败:", e);
        状态.当前消息列表 = [];
        事件总线.emit(事件.消息列表更新, []);
    }
}

// ─── 插件文件夹联动 ───────────────────────────────────────────
// 统一入口：写入 localStorage 并广播事件，驱动文件夹选择器、项目信息栏等同步刷新
export function 设置插件文件夹(name) {
    const value = (name || "").trim();
    // 迁至 IndexedDB 异步写入（fire-and-forget）；存储引擎内部同步刷新 localStorage 影子缓存，
    // 保证后续 同步读取 调用（例如设置面板、项目信息栏）仍能读到最新值
    if (value) {
        存储.写入(NCA_STORAGE_KEYS.plugin, value).catch(() => {});
    } else {
        存储.删除(NCA_STORAGE_KEYS.plugin).catch(() => {});
    }
    事件总线.emit(事件.插件选择变更, value);
}

export async function 加载会话消息(sessionId) {
    try {
        const data = await 请求("GET", `/sessions/${sessionId}/messages`);
        状态.当前消息列表 = data.messages || [];
        事件总线.emit(事件.消息列表更新, 状态.当前消息列表);
    } catch (e) {
        console.error("[节点梦工厂] 加载消息失败:", e);
        状态.当前消息列表 = [];
        事件总线.emit(事件.消息列表更新, []);
    }
}

// ─── RanKing 计费集成 ───────────────────────────────────────
// 本地余额缓存（单位：token）
let _余额缓存 = null;
let _余额缓存时间 = 0;
const _余额缓存TTL = 30000; // 30 秒

export function 获取余额缓存() {
    if (_余额缓存 === null) return null;
    if (Date.now() - _余额缓存时间 > _余额缓存TTL) return null;
    return _余额缓存;
}

export function 设置余额缓存(balance) {
    if (typeof balance === "number" && !isNaN(balance)) {
        _余额缓存 = balance;
        _余额缓存时间 = Date.now();
        事件总线.emit("nca:balance-updated", balance);
    }
}

/**
 * 发送前检查余额。
 * 返回值：{ balance: number, sufficient: boolean }
 *   - 查询失败时降级放行（sufficient: true, balance: -1）
 *   - 余额 <= 0 时 sufficient: false
 */
export async function 检查余额() {
    try {
        const token = _获取Token();
        if (!token) return { balance: -1, sufficient: true };
        const resp = await fetch("/nca/billing/token-balance", {
            headers: { "Authorization": `Bearer ${token}` },
        });
        if (resp.ok) {
            const data = await resp.json();
            const balance = typeof data.token_balance === "number" ? data.token_balance : 0;
            设置余额缓存(balance);
            return { balance, sufficient: balance > 0 };
        }
    } catch (e) {
        console.warn("[节点梦工厂] 余额查询失败，降级放行", e);
    }
    return { balance: -1, sufficient: true };
}

/**
 * 构建本次消耗信息 DOM。
 * cost: 云端返回的消耗积分（元），需转换为 token 数显示
 * balance: 云端返回的 token 余额
 */
export function 创建消耗信息DOM(cost, balance) {
    const wrap = document.createElement("div");
    wrap.className = "nca-billing-info";
    wrap.style.cssText = "font-size:12px;color:var(--nca-text-secondary,#8a9bb8);margin-top:6px;padding-top:4px;border-top:1px dashed rgba(138,155,184,0.2);display:flex;gap:12px;align-items:center;";
    const TPC = 200000; // 1积分 = 20万token
    // cost 是积分(元)，转换为 token 数
    const costTokens = typeof cost === "number" ? Math.round(cost * TPC) : cost;
    const costNum = typeof costTokens === "number" ? costTokens.toLocaleString() : costTokens;
    const balNum = typeof balance === "number" ? Math.floor(balance).toLocaleString() : balance;
    wrap.innerHTML = `<span>本次消耗 ${costNum} token</span><span>|</span><span>剩余 ${balNum} token</span>`;
    return wrap;
}

// ─── 聊天 API ─────────────────────────────────────────────────
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

    // ── 发送前余额检查（余额不足则阻断；查询失败降级放行）──
    const 余额检查 = await 检查余额();
    if (!余额检查.sufficient && 余额检查.balance >= 0) {
        try { Toast.warning("额度已耗尽，请充值后继续使用"); } catch (_) {}
        状态.正在发送 = false;
        事件总线.emit(事件.发送状态变更, false);
        事件总线.emit(事件.状态栏更新, "额度已耗尽");
        状态.连接状态 = "online";
        事件总线.emit(事件.连接状态变更, "online");
        return null;
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
            plugin_context: 安全存储读(NCA_STORAGE_KEYS.plugin),
            activeTab: 安全存储读(NCA_STORAGE_KEYS.activeTab, "develop"),
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

        // 处理后端返回的计费信息
        if (data.billing) {
            设置余额缓存(data.billing.balance);
        }

        const AI回复 = {
            role: "assistant",
            content: _过滤工具标记(data.reply) || "（无回复）",
            timestamp: Date.now(),
        };
        if (data.billing) {
            AI回复.billing = data.billing;
        }
        状态.当前消息列表.push(AI回复);
        事件总线.emit(事件.新消息追加, AI回复);
        事件总线.emit(事件.状态栏更新, "就绪");
        return AI回复;
    } catch (e) {
        // 403 会员权限不足：弹出登录面板并提示升级会员
        if (e?.status === 403) {
            const 提示 = e?.data?.message || e?.message || "当前会员无法使用 API 模型，请升级会员后使用";
            try { Toast.error(提示); } catch (_) {}
            try {
                import("./登录面板.js").then(m => {
                    if (m && typeof m.显示会员升级提示 === "function") m.显示会员升级提示(提示);
                }).catch(() => {});
            } catch (_) {}
            状态.正在发送 = false;
            事件总线.emit(事件.发送状态变更, false);
            事件总线.emit(事件.状态栏更新, "权限不足");
            状态.连接状态 = "online";
            事件总线.emit(事件.连接状态变更, "online");
            return null;
        }
        // 402 余额不足：后端明确返回 BALANCE_EXHAUSTED
        if (e?.code === "BALANCE_EXHAUSTED" || e?.data?.code === "BALANCE_EXHAUSTED") {
            设置余额缓存(typeof e.data?.balance === "number" ? e.data.balance : 0);
            try { Toast.warning("额度已耗尽，请充值后继续使用"); } catch (_) {}
            状态.正在发送 = false;
            事件总线.emit(事件.发送状态变更, false);
            事件总线.emit(事件.状态栏更新, "额度已耗尽");
            状态.连接状态 = "online";
            事件总线.emit(事件.连接状态变更, "online");
            return null;
        }
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

export async function 保存设置(settings) {
    try {
        // 切换到 API 模式前校验会员权限（无进阶版/高级版会员不可用）
        if (settings.model_source === "api" && 状态.设置.model_source !== "api") {
            try {
                const { 获取登录状态 } = await import("./登录面板.js");
                const tierInfo = 获取登录状态()?.tier_info;
                if (!tierInfo || !tierInfo.api_available) {
                    Toast.warning("您没有使用 API 模型的权限，请购买进阶版或高级版会员");
                    return false;
                }
            } catch (_) { /* 登录状态获取失败时不阻断，交由后端最终拦截 */ }
        }
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
        const resp = await 请求("GET", "/model-capabilities");
        _能力缓存 = resp;
        _能力缓存时间 = now;
        return resp;
    } catch (e) {
        console.warn("[节点梦工厂] 查询模型能力失败，降级为全部启用:", e);
        return { supports_vision: true, supports_file_content: true };
    }
}

// 模型切换时清除能力缓存
事件总线.on(事件.模型选择变更, () => { _能力缓存 = null; });

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
        return d.toLocaleTimeString("zh-CN", { hour: "2-digit", minute: "2-digit" });
    } else if (diff < 172800000) {
        return "昨天";
    } else {
        return `${Math.floor(diff / 86400000)}天前`;
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
