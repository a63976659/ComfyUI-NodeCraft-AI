// ═══════════════════════════════════════════════════════════════
// graphql客户端.js — 零依赖 GraphQL 客户端
// NodeCraft AI — Luxury Terminal Edition
//
// 功能概览：
//   - query(queryStr, variables, options) → fetch POST /graphql
//   - mutate(mutationStr, variables, options) → fetch POST /graphql
//   - subscribe(subscriptionStr, variables, callbacks) → WebSocket(graphql-ws)
//   - setToken / clearToken：Bearer Token 注入
//   - 自动重连（指数退避，最多 5 次）+ 心跳 + 多订阅复用单连接
//
// 不引入任何第三方库（Apollo / urql 等）。
// 与后端 schema（中文命名）一致：query/variables/operationName 字段保持英文以
// 兼容 GraphQL 协议本身。
// ═══════════════════════════════════════════════════════════════

// ─── Token 存取键（与 前端/登录面板.js 保持一致）────────────
// RanKing 集成后，统一使用 ComfyCommunity_Token 作为 token 存储键
const KEY_ACCESS_TOKEN = "ComfyCommunity_Token";

// ─── graphql-ws 协议消息类型常量 ───────────────────────────
// 参考：https://github.com/enisdenjo/graphql-ws/blob/master/PROTOCOL.md
const GQL_WS = {
    CONNECTION_INIT: "connection_init",
    CONNECTION_ACK: "connection_ack",
    PING: "ping",
    PONG: "pong",
    SUBSCRIBE: "subscribe",
    NEXT: "next",
    ERROR: "error",
    COMPLETE: "complete",
};

// ─── GraphQL 错误（带 code / extensions 结构化信息）────────
export class GraphQLError extends Error {
    constructor(message, { code = null, extensions = null, errors = null } = {}) {
        super(message);
        this.name = "GraphQLError";
        this.code = code;
        this.extensions = extensions;
        this.errors = errors; // 完整的 errors 数组（GraphQL spec）
    }
}

function _解析错误(errors) {
    // GraphQL errors 数组 → GraphQLError
    const first = (errors && errors[0]) || {};
    const code = first.extensions?.code || null;
    const message = first.message || "GraphQL 请求失败";
    return new GraphQLError(message, {
        code,
        extensions: first.extensions || null,
        errors,
    });
}

// ─── GraphQL 客户端类 ────────────────────────────────────
export class GraphQLClient {
    constructor(endpoint = "/graphql") {
        this.endpoint = endpoint;
        this._token = null;          // 显式注入的 token（优先级高于 localStorage）
        this._tokenOverride = false; // 是否使用了 setToken 覆盖

        // ── WebSocket 状态 ──
        this._ws = null;
        this._wsReady = false;             // connection_ack 收到后置 true
        this._wsConnecting = null;         // Promise，避免并发建连
        this._reconnectAttempts = 0;
        this._maxReconnectAttempts = 5;
        this._reconnectTimer = null;
        this._heartbeatTimer = null;
        this._heartbeatIntervalMs = 25000; // 25s 心跳
        this._closedByUser = false;

        // ── 订阅注册表 ──
        // id → { query, variables, callbacks, started }
        this._subscriptions = new Map();
        this._subIdCounter = 1;
    }

    // ─── Token 管理 ─────────────────────────────────────
    setToken(token) {
        this._token = token || null;
        this._tokenOverride = true;
    }

    clearToken() {
        this._token = null;
        this._tokenOverride = true; // 显式清空，不回退到 localStorage
    }

    _获取Token() {
        if (this._tokenOverride) return this._token;
        // 默认从 localStorage / sessionStorage 读取
        try {
            return (
                localStorage.getItem(KEY_ACCESS_TOKEN) ||
                sessionStorage.getItem(KEY_ACCESS_TOKEN) ||
                null
            );
        } catch (_) {
            return null;
        }
    }

    _构建Headers(extra = {}) {
        const headers = { "Content-Type": "application/json", ...extra };
        const token = this._获取Token();
        if (token) headers["Authorization"] = `Bearer ${token}`;
        return headers;
    }

    // ─── HTTP: query / mutate ───────────────────────────
    async query(queryStr, variables = {}, options = {}) {
        return this._httpRequest(queryStr, variables, options);
    }

    async mutate(mutationStr, variables = {}, options = {}) {
        return this._httpRequest(mutationStr, variables, options);
    }

    async _httpRequest(queryStr, variables, options) {
        const {
            operationName = null,
            timeout = 30000,
            signal: externalSignal = null,
        } = options || {};

        const controller = new AbortController();
        const timeoutId = (timeout && timeout > 0)
            ? setTimeout(() => controller.abort(), timeout)
            : null;
        // 串接外部 signal（若提供）
        if (externalSignal) {
            if (externalSignal.aborted) controller.abort();
            else externalSignal.addEventListener("abort", () => controller.abort(), { once: true });
        }

        const body = JSON.stringify({
            query: queryStr,
            variables: variables || {},
            operationName,
        });

        try {
            const resp = await fetch(this.endpoint, {
                method: "POST",
                headers: this._构建Headers(),
                body,
                signal: controller.signal,
            });

            if (!resp.ok) {
                // 尝试解析 GraphQL errors；若失败则抛 HTTP 错误
                let errPayload = null;
                try { errPayload = await resp.json(); } catch (_) {}
                if (errPayload?.errors?.length) throw _解析错误(errPayload.errors);
                throw new GraphQLError(`HTTP ${resp.status}: ${resp.statusText}`, {
                    code: `HTTP_${resp.status}`,
                });
            }

            const payload = await resp.json();
            if (payload.errors?.length) throw _解析错误(payload.errors);
            return payload.data;
        } catch (e) {
            if (e.name === "AbortError") {
                throw new GraphQLError("GraphQL 请求超时", { code: "TIMEOUT" });
            }
            if (e instanceof GraphQLError) throw e;
            throw new GraphQLError(e.message || String(e), { code: "NETWORK_ERROR" });
        } finally {
            if (timeoutId) clearTimeout(timeoutId);
        }
    }

    // ─── WebSocket: subscribe ───────────────────────────
    /**
     * 订阅 GraphQL Subscription。
     * @param {string} subscriptionStr Subscription 文本
     * @param {object} variables 变量
     * @param {{onData?:Function,onError?:Function,onComplete?:Function}} callbacks
     * @returns {Function} unsubscribe 函数
     */
    subscribe(subscriptionStr, variables = {}, callbacks = {}) {
        const id = String(this._subIdCounter++);
        const entry = {
            query: subscriptionStr,
            variables: variables || {},
            callbacks: callbacks || {},
            started: false,
        };
        this._subscriptions.set(id, entry);

        // 触发建连（若未连接），连接成功后会逐个 _发送订阅 已注册的订阅
        this._ensureWebSocket().then(() => {
            this._发送订阅(id);
        }).catch((err) => {
            try { entry.callbacks.onError?.(err); } catch (_) {}
            this._subscriptions.delete(id);
        });

        // 返回 unsubscribe 函数
        return () => this._取消订阅(id);
    }

    _取消订阅(id) {
        const entry = this._subscriptions.get(id);
        if (!entry) return;
        // 通知服务端结束（仅在已 start 且连接活跃时）
        if (entry.started && this._ws && this._ws.readyState === WebSocket.OPEN) {
            try {
                this._ws.send(JSON.stringify({ id, type: GQL_WS.COMPLETE }));
            } catch (_) {}
        }
        this._subscriptions.delete(id);
        // 若没有任何订阅了，可考虑保留连接以便后续复用（也可立即关闭）
        // 这里采用：保持连接 30s 空闲后由心跳/外部 disconnect 决定释放
    }

    _发送订阅(id) {
        const entry = this._subscriptions.get(id);
        if (!entry) return;
        if (!this._ws || this._ws.readyState !== WebSocket.OPEN || !this._wsReady) return;
        try {
            this._ws.send(JSON.stringify({
                id,
                type: GQL_WS.SUBSCRIBE,
                payload: {
                    query: entry.query,
                    variables: entry.variables,
                    operationName: null,
                },
            }));
            entry.started = true;
        } catch (e) {
            try { entry.callbacks.onError?.(new GraphQLError(`订阅发送失败: ${e.message}`, { code: "WS_SEND_ERROR" })); } catch (_) {}
        }
    }

    // ─── WebSocket 管理 ─────────────────────────────────
    _构建WSUrl() {
        const protocol = location.protocol === "https:" ? "wss:" : "ws:";
        // endpoint 可能是相对路径（/graphql）或绝对路径
        if (/^wss?:\/\//.test(this.endpoint)) return this.endpoint;
        const path = this.endpoint.startsWith("/") ? this.endpoint : `/${this.endpoint}`;
        return `${protocol}//${location.host}${path}`;
    }

    _ensureWebSocket() {
        if (this._ws && this._ws.readyState === WebSocket.OPEN && this._wsReady) {
            return Promise.resolve();
        }
        if (this._wsConnecting) return this._wsConnecting;

        this._closedByUser = false;
        this._wsConnecting = new Promise((resolve, reject) => {
            const url = this._构建WSUrl();
            let ws;
            try {
                // 第二参数指定子协议：必须为 'graphql-ws'（GraphQL over WebSocket Protocol）
                ws = new WebSocket(url, "graphql-ws");
            } catch (e) {
                this._wsConnecting = null;
                reject(new GraphQLError(`WebSocket 创建失败: ${e.message}`, { code: "WS_CREATE_ERROR" }));
                return;
            }
            this._ws = ws;
            this._wsReady = false;

            ws.onopen = () => {
                // 发送 connection_init（含 Authorization payload）
                const token = this._获取Token();
                const initPayload = token ? { Authorization: `Bearer ${token}` } : {};
                try {
                    ws.send(JSON.stringify({ type: GQL_WS.CONNECTION_INIT, payload: initPayload }));
                } catch (e) {
                    reject(new GraphQLError(`WebSocket 初始化失败: ${e.message}`, { code: "WS_INIT_ERROR" }));
                }
            };

            ws.onmessage = (event) => {
                let msg;
                try { msg = JSON.parse(event.data); }
                catch (e) { return; }
                this._处理WS消息(msg, resolve);
            };

            ws.onerror = (_e) => {
                // 错误本身不 reject，等 onclose 决定是否重连
            };

            ws.onclose = (event) => {
                this._wsReady = false;
                this._wsConnecting = null;
                this._清除心跳();
                // 通知所有未完成的订阅（避免回调悬挂）
                const wasUserClose = this._closedByUser;
                if (!wasUserClose) {
                    // 触发指数退避重连
                    this._handleReconnect();
                } else {
                    // 用户主动关闭：通知所有订阅 complete
                    for (const [, entry] of this._subscriptions) {
                        try { entry.callbacks.onComplete?.(); } catch (_) {}
                    }
                    this._subscriptions.clear();
                }
                // 若还在等待初始连接，reject
                try {
                    reject(new GraphQLError(
                        `WebSocket 已关闭 (code=${event.code})`,
                        { code: "WS_CLOSED" }
                    ));
                } catch (_) {}
            };
        });

        return this._wsConnecting;
    }

    _处理WS消息(msg, resolveConnect) {
        const { id, type, payload } = msg || {};
        switch (type) {
            case GQL_WS.CONNECTION_ACK: {
                this._wsReady = true;
                this._reconnectAttempts = 0;
                this._wsConnecting = null;
                this._启动心跳();
                // 重发所有未启动的订阅（断线重连场景）
                for (const [subId, entry] of this._subscriptions) {
                    if (!entry.started) this._发送订阅(subId);
                }
                if (typeof resolveConnect === "function") resolveConnect();
                break;
            }
            case GQL_WS.PING: {
                // 服务端 ping → 客户端 pong
                try {
                    this._ws?.send(JSON.stringify({ type: GQL_WS.PONG, payload: payload || {} }));
                } catch (_) {}
                break;
            }
            case GQL_WS.PONG: {
                // 服务端响应客户端 ping，正常无需处理
                break;
            }
            case GQL_WS.NEXT: {
                const entry = id != null ? this._subscriptions.get(id) : null;
                if (!entry) break;
                if (payload?.errors?.length) {
                    try { entry.callbacks.onError?.(_解析错误(payload.errors)); } catch (_) {}
                } else {
                    try { entry.callbacks.onData?.(payload?.data); } catch (_) {}
                }
                break;
            }
            case GQL_WS.ERROR: {
                const entry = id != null ? this._subscriptions.get(id) : null;
                if (!entry) break;
                const errs = Array.isArray(payload) ? payload : (payload?.errors || [payload]);
                try { entry.callbacks.onError?.(_解析错误(errs)); } catch (_) {}
                // 发生错误后服务端会关闭该订阅
                this._subscriptions.delete(id);
                break;
            }
            case GQL_WS.COMPLETE: {
                const entry = id != null ? this._subscriptions.get(id) : null;
                if (!entry) break;
                try { entry.callbacks.onComplete?.(); } catch (_) {}
                this._subscriptions.delete(id);
                break;
            }
            default:
                break;
        }
    }

    _启动心跳() {
        // 防御性清除：无论当前值如何，确保旧计时器被清除
        if (this._heartbeatTimer !== null) {
            clearInterval(this._heartbeatTimer);
            this._heartbeatTimer = null;
        }
        this._heartbeatTimer = setInterval(() => {
            if (this._ws && this._ws.readyState === WebSocket.OPEN) {
                try {
                    this._ws.send(JSON.stringify({ type: GQL_WS.PING, payload: {} }));
                } catch (_) {}
            }
        }, this._heartbeatIntervalMs);
    }

    _清除心跳() {
        if (this._heartbeatTimer) {
            clearInterval(this._heartbeatTimer);
            this._heartbeatTimer = null;
        }
    }

    _handleReconnect() {
        if (this._closedByUser) return;
        if (this._subscriptions.size === 0) return; // 没有订阅就不需要重连
        if (this._reconnectAttempts >= this._maxReconnectAttempts) {
            // 通知所有订阅放弃
            for (const [, entry] of this._subscriptions) {
                try {
                    entry.callbacks.onError?.(new GraphQLError("WebSocket 重连失败已达上限", { code: "WS_RECONNECT_FAILED" }));
                } catch (_) {}
            }
            this._subscriptions.clear();
            return;
        }
        const attempt = this._reconnectAttempts++;
        // 指数退避：1s, 2s, 4s, 8s, 16s（上限 30s）
        const delay = Math.min(1000 * Math.pow(2, attempt), 30000);
        if (this._reconnectTimer) clearTimeout(this._reconnectTimer);
        this._reconnectTimer = setTimeout(() => {
            // 重置订阅 started 标志，连接成功后会重发
            for (const entry of this._subscriptions.values()) entry.started = false;
            this._ensureWebSocket().catch(() => {/* 失败由 onclose 触发下一轮 */});
        }, delay);
    }

    disconnect() {
        this._closedByUser = true;
        if (this._reconnectTimer) {
            clearTimeout(this._reconnectTimer);
            this._reconnectTimer = null;
        }
        this._清除心跳();
        // 主动 complete 所有订阅
        if (this._ws && this._ws.readyState === WebSocket.OPEN) {
            for (const [id, entry] of this._subscriptions) {
                if (entry.started) {
                    try { this._ws.send(JSON.stringify({ id, type: GQL_WS.COMPLETE })); } catch (_) {}
                }
            }
            try { this._ws.close(1000, "client disconnect"); } catch (_) {}
        }
        this._ws = null;
        this._wsReady = false;
        this._wsConnecting = null;
        this._subscriptions.clear();
        this._reconnectAttempts = 0;
    }
}

// ─── 单例导出 ──────────────────────────────────────────
export const graphqlClient = new GraphQLClient("/graphql");
