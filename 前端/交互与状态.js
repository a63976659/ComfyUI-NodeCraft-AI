// ═══════════════════════════════════════════════════════════════
// 交互与状态.js — 事件总线 + 全局状态 + HTTP 客户端
// NodeCraft AI — Luxury Terminal Edition
// ═══════════════════════════════════════════════════════════════

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

async function 请求(method, path, body = null) {
    const options = {
        method,
        headers: { "Content-Type": "application/json" },
    };
    if (body) options.body = JSON.stringify(body);

    try {
        const response = await fetch(`${API_BASE}${path}`, options);
        if (!response.ok) {
            throw new Error(`HTTP ${response.status}: ${response.statusText}`);
        }
        状态.连接状态 = "online";
        事件总线.emit(事件.连接状态变更, "online");
        return response.json();
    } catch (e) {
        if (e.message.includes("Failed to fetch") || e.message.includes("NetworkError")) {
            状态.连接状态 = "error";
            事件总线.emit(事件.连接状态变更, "error");
        }
        throw e;
    }
}

// ─── 会话管理 API ─────────────────────────────────────────────
export async function 获取会话列表() {
    try {
        const data = await 请求("GET", "/sessions");
        状态.会话列表 = data.sessions || [];
        事件总线.emit(事件.会话列表更新, 状态.会话列表);
        return 状态.会话列表;
    } catch (e) {
        console.error("[NodeCraft] 获取会话列表失败:", e);
        return [];
    }
}

export async function 创建会话(title = "新会话", plugin_folder = "") {
    try {
        const data = await 请求("POST", "/sessions", { title, plugin_folder });
        await 获取会话列表();
        if (data.session?.id) {
            await 切换会话(data.session.id);
        }
        return data.session;
    } catch (e) {
        console.error("[NodeCraft] 创建会话失败:", e);
        return null;
    }
}

export async function 删除会话(id) {
    try {
        await 请求("DELETE", `/sessions/${id}`);
        if (状态.当前会话ID === id) {
            状态.当前会话ID = null;
            状态.当前消息列表 = [];
            事件总线.emit(事件.消息列表更新, []);
        }
        await 获取会话列表();
        return true;
    } catch (e) {
        console.error("[NodeCraft] 删除会话失败:", e);
        return false;
    }
}

export async function 切换会话(id) {
    状态.当前会话ID = id;
    事件总线.emit(事件.会话切换, id);
    await 加载会话消息(id);
}

export async function 加载会话消息(sessionId) {
    try {
        const data = await 请求("GET", `/sessions/${sessionId}/messages`);
        状态.当前消息列表 = data.messages || [];
        事件总线.emit(事件.消息列表更新, 状态.当前消息列表);
    } catch (e) {
        console.error("[NodeCraft] 加载消息失败:", e);
        状态.当前消息列表 = [];
        事件总线.emit(事件.消息列表更新, []);
    }
}

// ─── 聊天 API ─────────────────────────────────────────────────
export async function 发送消息(content, attachments = []) {
    if (状态.正在发送) return null;
    状态.正在发送 = true;
    状态.连接状态 = "busy";
    事件总线.emit(事件.发送状态变更, true);
    事件总线.emit(事件.连接状态变更, "busy");
    事件总线.emit(事件.状态栏更新, "思考中...");

    // 先追加用户消息到本地
    const 用户消息 = { role: "user", content, timestamp: Date.now(), attachments: attachments.length > 0 ? attachments : undefined };
    状态.当前消息列表.push(用户消息);
    事件总线.emit(事件.新消息追加, 用户消息);

    try {
        const requestBody = {
            message: content,
            session_id: 状态.当前会话ID,
        };
        if (attachments.length > 0) {
            requestBody.attachments = attachments;
        }
        const data = await 请求("POST", "/chat", requestBody);

        const AI回复 = {
            role: "assistant",
            content: data.reply || "（无回复）",
            timestamp: Date.now(),
        };
        状态.当前消息列表.push(AI回复);
        事件总线.emit(事件.新消息追加, AI回复);
        事件总线.emit(事件.状态栏更新, "就绪");
        return AI回复;
    } catch (e) {
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
        事件总线.emit(事件.设置已加载, 状态.设置);
        return 状态.设置;
    } catch (e) {
        console.error("[NodeCraft] 加载设置失败:", e);
        return 状态.设置;
    }
}

export async function 保存设置(settings) {
    try {
        await 请求("POST", "/settings", settings);
        Object.assign(状态.设置, settings);
        事件总线.emit(事件.设置已保存, 状态.设置);
        return true;
    } catch (e) {
        console.error("[NodeCraft] 保存设置失败:", e);
        return false;
    }
}

// ─── 文件操作 API ─────────────────────────────────────────────
export async function 创建插件文件夹(pluginName) {
    try {
        const data = await 请求("POST", "/create-folder", { plugin_name: pluginName });
        return data;
    } catch (e) {
        console.error("[NodeCraft] 创建文件夹失败:", e);
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
        console.error("[NodeCraft] 获取本地模型列表失败:", e);
        return [];
    }
}

// ─── 工具函数 ─────────────────────────────────────────────────
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
