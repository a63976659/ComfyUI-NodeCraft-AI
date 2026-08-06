// ═══════════════════════════════════════════════════════════════
// 消息渲染器.js — 消息气泡渲染、输入区构建、流式响应（模块入口）
// NodeCraft AI — Luxury Terminal / Neo-Noir Hacker
//
// 已按职责拆分为子模块（对外导出符号保持不变，由本入口 re-export）：
//   消息渲染/虚拟滚动管理器.js — 消息列表虚拟滚动
//   消息渲染/代码复制.js       — 代码块复制委托 + Prism 高亮触发
//   消息渲染/欢迎页.js         — 欢迎页与快捷操作
//   消息渲染/附件管理.js       — 文件附件选择、预览、移除
// ═══════════════════════════════════════════════════════════════

import {
    el, 简易Markdown渲染, NCA_STORAGE_KEYS, Toast,
    移除视觉能力警告, 工具名显示, 追加附件缩略图, 创建行内编辑器,
} from "./工具函数.js";
import {
    事件总线, 事件, 状态,
    发送消息, 创建会话, 获取当前模型名称, 格式化时间, 加载更早消息,
} from "./交互与状态.js";
import { 创建流式聊天 } from "./流式聊天管理器.js";
import { t, 获取当前语言 } from "./i18n.js";
import { 显示知识库通知 } from "./知识库通知条.js";
import { 虚拟滚动配置, 虚拟滚动管理器 } from "./消息渲染/虚拟滚动管理器.js";
import { 绑定代码块复制按钮 } from "./消息渲染/代码复制.js";
import { 渲染欢迎页 } from "./消息渲染/欢迎页.js";
import { 处理文件选择, 渲染附件预览, 更新发送按钮状态 } from "./消息渲染/附件管理.js";

// ─── Re-export 拆分子模块的原有导出，外部导入路径与符号保持完全不变 ──
export { 绑定代码块复制按钮 } from "./消息渲染/代码复制.js";
export { 渲染欢迎页 } from "./消息渲染/欢迎页.js";

// ─── 流式响应状态 ────────────────────────────────────────────
let _abortController = null;
let _isStreaming = false;

// ─── 工具循环噪音折叠 ─────────────────────────────────────────
// 模型在工具调用之间产生的"内心独白"（如"让我先了解..."）会自动折叠为紧凑指示器
// 扩展覆盖更多中文高频噪音模式：分段读取、继续查看、用XX方式等
const _规划噪音模式 = /^(让我|我先|好的[，,]让我|现在(创建|更新|修复|实现|读取|写入|设计|安装|配置|检查|验证)|我来(设计|实现|创建|编写|修改)|文件.*截断|Let me |I'll |Now let|First let|让我用|让我查看|让我搜索|让我先看|让我重新|让我完整|让我精确|让我分段|让我全面|我来看看|我先看看|好的[，,]让我先|(读取|继续读取|先查看|先了解|先检查|分段读取|完整读取|获取|用分段|用更|用更精|先读取|先完整|先全面|先获取|查看当前|查看完整|了解当前|了解完整|检查当前|检查完整).*)/;
let _工具已执行 = false;    // 是否已触发至少一次工具执行
let _工具噪音计数 = 0;      // 被折叠的噪音行数
let _工具噪音累积 = '';     // 被折叠的原始文本（调试用）

// ─── 模块级 refs/根容器引用 ────────────────────────────────────
// 消息内编辑功能（hover 用户消息→编辑→重新生成）需要在按钮点击回调里访问
// 当前活动 refs 与根容器，但创建 DOM 时调用方未必传入；用模块级变量在
// 渲染入口（渲染所有消息 / 渲染输入区域）处缓存，避免给每个消息节点再绑一份引用。
let _当前refs = null;
let _当前rootContainer = null;

// DOMPurify 加载完成/失败后重新渲染标记为待消毒的消息（安全纯文本降级→完整 Markdown 渲染）
// 仅处理已完成的静态消息；流式渲染中的消息不带 data-pending-sanitize 标记，不受影响
// ready 事件：DOMPurify 加载成功，简易Markdown渲染 走 DOMPurify 消毒路径
// failed 事件：DOMPurify 加载失败，简易Markdown渲染 走 _本地消毒 路径（DOMParser 白名单）
function _重渲染待处理消息() {
    if (!_当前refs || !_当前refs.消息区域) return;
    const 待处理 = _当前refs.消息区域.querySelectorAll('[data-pending-sanitize="true"]');
    if (待处理.length === 0) return;
    待处理.forEach(body => {
        const msgEl = body.closest('.nca-msg');
        if (!msgEl) return;
        // 优先使用元素上缓存的原始内容（流式完成消息），其次通过消息索引从列表获取
        let rawContent = msgEl._pendingContent;
        if (rawContent === undefined && typeof msgEl._消息索引 === 'number') {
            const msg = 状态.当前消息列表[msgEl._消息索引];
            if (msg) rawContent = msg.content;
        }
        if (rawContent !== undefined) {
            body.innerHTML = 简易Markdown渲染(rawContent);
            body.removeAttribute('data-pending-sanitize');
            delete msgEl._pendingContent;
            绑定代码块复制按钮(msgEl);
        }
    });
}

window.addEventListener('nca-dompurify-ready', _重渲染待处理消息);
window.addEventListener('nca-dompurify-failed', _重渲染待处理消息);

function _创建消息DOM(msg, index) {
    const timeStr = 格式化时间(msg.timestamp);
    const roleLabel = msg.role === "user" ? t("chat.role_user") : msg.role === "assistant" ? "AI" : "系统";

    const headerChildren = [
        el("span", { class: "msg-role", text: `─ ${roleLabel}` }),
        timeStr ? el("span", { class: "msg-time", text: timeStr }) : null,
    ];

    // 用户消息追加 hover-显示的编辑按钮：进入行内编辑→截断后续历史→重新生成
    if (msg.role === "user") {
        const editBtn = el("button", { class: "nca-msg-edit-btn", text: "✎", title: t("chat.edit_regen") });
        editBtn.addEventListener("click", (e) => {
            e.stopPropagation();
            const host = editBtn.closest(".nca-msg");
            if (!host) return;
            // 优先用 DOM 上缓存的索引（虚拟滚动场景下索引会变化）
            const idx = (typeof host._虚拟索引 === "number")
                ? host._虚拟索引
                : (typeof host._消息索引 === "number" ? host._消息索引 : index);
            _进入编辑模式(host, msg, idx);
        });
        headerChildren.push(editBtn);
    }

    const msgBody = el("div", { class: "nca-msg-body", html: 简易Markdown渲染(msg.content) });
    // 长消息折叠：超过2000字符默认折叠
    if (msg.content && msg.content.length > 2000) {
        const preview = msg.content.substring(0, 200);
        msgBody.innerHTML = '';
        const details = document.createElement('details');
        details.className = 'nca-msg-collapsible';
        const summary = document.createElement('summary');
        summary.textContent = `📄 长消息 (${msg.content.length}字) — 点击展开`;
        details.appendChild(summary);
        const fullDiv = document.createElement('div');
        fullDiv.innerHTML = 简易Markdown渲染(msg.content);
        details.appendChild(fullDiv);
        msgBody.appendChild(details);
    }
    // DOMPurify 未就绪时标记，待加载完成后由事件监听器重新渲染
    if (!window.DOMPurify) {
        msgBody.dataset.pendingSanitize = "true";
    }
    // 用户消息附带附件图片缩略图
    if (msg.role === "user" && msg.attachments && msg.attachments.length > 0) {
        追加附件缩略图(msgBody, msg.attachments);
    }
    const children = [
        el("div", { class: "nca-msg-header" }, headerChildren),
        msgBody,
    ];
    // 本地 API Key 直连后不再显示 billing 消耗信息
    const msgEl = el("div", { class: `nca-msg ${msg.role}` }, children);
    // 缓存索引：编辑回调与虚拟滚动测量都会读取
    msgEl._消息索引 = index;
    return msgEl;
}

// ─── 消息内编辑模式：行内 textarea + 确认/取消 ───────────────
function _进入编辑模式(msgEl, msg, index) {
    if (_isStreaming) return; // 流式生成中禁止编辑

    创建行内编辑器(msgEl, msg.content, {
        onCancel: () => 绑定代码块复制按钮(msgEl),
        onConfirm: (newContent, { body, originalHTML }) => {
            if (_isStreaming || 状态.正在发送) return;
            msgEl.classList.remove("nca-msg-editing");

            const refs = _当前refs;
            const root = _当前rootContainer;
            if (!refs) {
                // 异常兜底：还原内容，避免气泡留空
                body.innerHTML = originalHTML;
                绑定代码块复制按钮(msgEl);
                return;
            }

            // 截断本地消息列表：移除被编辑消息及之后所有内容
            状态.当前消息列表.length = index;
            // 虚拟滚动场景：就地裁剪高度缓存并重渲染可视区，避免整个销毁重建
            if (refs._虚拟滚动 && typeof refs._虚拟滚动.截断消息 === "function") {
                refs._虚拟滚动.截断消息(index);
            } else {
                // 非虚拟滚动路径：整体重渲染（清空旧 DOM 包含被编辑气泡）
                渲染所有消息(refs, 状态.当前消息列表);
            }
            // 不 emit 消息列表更新：以避免订阅者重复调用 渲染所有消息。
            // 虚拟滚动下重复渲染会销毁列表重建，干扰就地裁剪优化。

            // 触发流式重新生成：携带 truncate_at，由后端裁剪服务端历史
            发送消息流式(refs, root, { content: newContent, truncate_at: index });
        },
    });
}

function _尝试启用虚拟滚动(refs) {
    if (refs._虚拟滚动) return;
    const list = 状态.当前消息列表;
    if (!list || list.length < 虚拟滚动配置.启用阈值) return;
    refs._虚拟滚动 = new 虚拟滚动管理器(
        refs.消息区域, list, _创建消息DOM, 绑定代码块复制按钮,
    );
    // 升级为虚拟滚动时容器被清空，需重新挂载加载更早提示条
    _挂载加载更早提示条(refs);
    refs.消息区域.scrollTop = refs.消息区域.scrollHeight;
}

// ─── 加载更早消息提示条 ────────────────────────────────────
// 首屏窗口模式（?recent=1）下 消息起始偏移 > 0 时，在消息区域顶部挂载提示条；
// 点击后由 加载更早消息 显式分页向前补取并 emit 消息列表更新，触发整体重渲染，
// 提示条随之重建；偏移归零后不再挂载（自然隐藏）。
// 虚拟滚动场景：提示条插在顶部占位之前，不在占位区间内，不会被回收逻辑移除。
function _挂载加载更早提示条(refs) {
    const 偏移 = 状态.消息起始偏移 || 0;
    if (偏移 <= 0) return;
    const 按钮文案 = `▲ 加载更早消息（还有 ${偏移} 条）`;
    const btn = el("button", { class: "nca-load-earlier-btn", text: 按钮文案 });
    const bar = el("div", { class: "nca-load-earlier" }, [btn]);
    btn.addEventListener("click", async () => {
        if (btn.disabled) return;
        btn.disabled = true;
        btn.textContent = "加载中...";
        const ok = await 加载更早消息();
        if (!ok) {
            // 未触发重渲染（失败/竞态）时恢复按钮可点
            btn.disabled = false;
            btn.textContent = 按钮文案;
        }
    });
    refs.消息区域.insertBefore(bar, refs.消息区域.firstChild);
}

// ═══════════════════════════════════════════════════════════════
// 消息 DOM 操作
// ═══════════════════════════════════════════════════════════════

export function 渲染所有消息(refs, messages) {
    // 缓存最新 refs，编辑模式按钮回调通过模块级变量取用
    _当前refs = refs;
    // 切换会话/重渲染时销毁旧滚动器
    if (refs._虚拟滚动) {
        refs._虚拟滚动.销毁();
        refs._虚拟滚动 = null;
    }
    refs.消息区域.innerHTML = "";
    if (!messages || messages.length === 0) {
        渲染欢迎页(refs.消息区域, refs);
        return;
    }
    // 大消息列表：启用虚拟滚动
    if (messages.length >= 虚拟滚动配置.启用阈值) {
        refs._虚拟滚动 = new 虚拟滚动管理器(
            refs.消息区域, messages, _创建消息DOM, 绑定代码块复制按钮,
        );
        _挂载加载更早提示条(refs);
        滚动到底部(refs);
        return;
    }
    // 小消息列表：保持原有渲染逻辑（传入索引以支持编辑）
    messages.forEach((msg, index) => 追加消息DOM(refs, msg, index));
    _挂载加载更早提示条(refs);
    滚动到底部(refs);
}

export function 追加消息DOM(refs, msg, index) {
    // 清除欢迎页
    if (refs.消息区域.querySelector(".nca-welcome")) {
        refs.消息区域.innerHTML = "";
    }

    // 已启用虚拟滚动：调用方应已将 msg push 到 状态.当前消息列表
    if (refs._虚拟滚动) {
        refs._虚拟滚动.追加新消息();
        滚动到底部(refs);
        return;
    }

    // index 缺省时取列表末位（追加场景），与虚拟滚动开启前的索引一致
    const 索引 = (index !== undefined && index !== null)
        ? index
        : Math.max(0, 状态.当前消息列表.length - 1);
    const msgEl = _创建消息DOM(msg, 索引);
    refs.消息区域.appendChild(msgEl);
    绑定代码块复制按钮(msgEl);

    滚动到底部(refs);

    // 累计消息达阈值时升级为虚拟滚动（用户无感知）
    _尝试启用虚拟滚动(refs);
}

export function 显示加载动画(refs) {
    const loader = el("div", { class: "nca-msg assistant", id: "nca-loader" }, [
        el("div", { class: "nca-msg-header" }, [
            el("span", { class: "msg-role", text: "─ AI" }),
        ]),
        el("div", { class: "nca-loading" }, [
            el("span", { class: "nca-loading-dot" }),
            el("span", { class: "nca-loading-dot" }),
            el("span", { class: "nca-loading-dot" }),
        ]),
    ]);
    refs.消息区域.appendChild(loader);
    滚动到底部(refs);
}

export function 移除加载动画(refs) {
    const loader = refs.消息区域.querySelector("#nca-loader");
    if (loader) loader.remove();
}

export function 滚动到底部(refs, force = false) {
    if (!force) {
        const container = refs.消息区域;
        const distToBottom = container.scrollHeight - container.scrollTop - container.clientHeight;
        if (distToBottom > 150) return; // 用户正在阅读，不打断
    }
    requestAnimationFrame(() => {
        refs.消息区域.scrollTop = refs.消息区域.scrollHeight;
    });
}

// ═══════════════════════════════════════════════════════════════
// 流式消息发送
// ═══════════════════════════════════════════════════════════════

export async function 发送消息流式(refs, rootContainer, options = {}) {
    // options.content 存在 = 来自编辑或规划重发；此时不读取输入框与附件，也不清空
    const 来自编辑 = typeof options.content === "string" && options.content.length > 0;
    const text = 来自编辑 ? options.content : refs.输入框.value.trim();
    const attachments = 来自编辑 ? (Array.isArray(options.附件) ? options.附件 : []) : [...状态.待发送附件];
    const truncate_at = options.truncate_at;
    // 规划重发：跳过用户消息气泡（用户消息已在首轮显示），并携带 skip_planning / user_choices
    const 跳过用户消息 = options.跳过用户消息 === true;
    const skip_planning = options.skip_planning === true;
    const user_choices = options.user_choices;
    if (!text && attachments.length === 0) return;
    if (状态.正在发送 || _isStreaming) return;

    // 本地 API Key 直连后不再做前置余额检查

    // 本地模式下验证模型已选择
    if (状态.模型来源 === "local" && !状态.选中本地模型) {
        Toast.warning("请先选择一个本地模型");
        return;
    }

    // 必须存在会话ID，否则禁止发送
    if (!状态.当前会话ID) {
        Toast.warning("请先创建或选择一个会话");
        return;
    }

    // 编辑重生成不从输入框取值，跳过输入框/附件清空
    if (!来自编辑) {
        // 清空输入
        refs.输入框.value = "";
        refs.输入框.style.height = "auto";
        refs.发送按钮.classList.remove("active");

        // 清空附件
        状态.待发送附件 = [];
        渲染附件预览(refs);
        // 发送后移除视觉警告
        const _inputArea = refs.输入框?.closest('.nca-input-area');
        if (_inputArea) 移除视觉能力警告(_inputArea);
    }

    // 1. 显示用户消息气泡（规划重发时跳过，避免重复显示同一条用户消息）
    if (!跳过用户消息) {
        const 用户消息 = { role: "user", content: text, timestamp: Date.now(), attachments: attachments.length > 0 ? attachments : undefined };
        状态.当前消息列表.push(用户消息);
        追加消息DOM(refs, 用户消息);
    }

    // 2. 创建空的 AI 回复气泡（带光标闪烁动画）
    if (refs.消息区域.querySelector(".nca-welcome")) {
        refs.消息区域.innerHTML = "";
    }
    const aiBubble = el("div", { class: "nca-msg assistant" }, [
        el("div", { class: "nca-msg-header" }, [
            el("span", { class: "msg-role", text: "─ AI" }),
        ]),
        el("div", { class: "nca-msg-body" }, [
            el("span", { class: "nca-streaming-cursor" }),
        ]),
    ]);
    refs.消息区域.appendChild(aiBubble);
    const aiBody = aiBubble.querySelector(".nca-msg-body");

    // 3. 禁用发送按钮，显示"停止生成"按钮
    _isStreaming = true;
    _工具已执行 = false;     // 重置工具循环标记
    _工具噪音计数 = 0;       // 重置噪音计数器
    _工具噪音累积 = '';      // 重置噪音累积
    状态.正在发送 = true;
    状态.流式状态.活跃 = true;
    状态.流式状态.累积内容 = "";
    状态.流式状态.消息体引用 = aiBody;
    状态.流式状态.消息容器引用 = aiBubble;
    状态.流式状态.滚动容器引用 = refs.消息区域;
    状态.流式状态.工具指示器引用 = null;
    事件总线.emit(事件.流式状态变更, true);
    refs.输入框.disabled = true;
    refs.发送按钮.disabled = true;
    事件总线.emit(事件.发送状态变更, true);
    事件总线.emit(事件.连接状态变更, "busy");
    事件总线.emit(事件.状态栏更新, t("chat.generating"));

    // 显示停止按钮
    const stopBtn = el("button", { class: "nca-stop-btn", text: "■ " + t("chat.stop") });
    stopBtn.addEventListener("click", () => {
        if (_abortController) _abortController.abort();
    });
    refs.停止按钮容器 = stopBtn;
    状态.流式状态.停止按钮引用 = stopBtn;
    // 插入到输入区域前面
    const inputArea = refs.输入框.closest(".nca-input-area");
    if (inputArea) inputArea.insertBefore(stopBtn, inputArea.firstChild);

    // 4. 开始流式请求
    _abortController = new AbortController();
    状态.流式状态.中止控制器 = _abortController;
    let hasError = false;
    let _toolExecutingEl = null; // 工具执行状态指示器元素（闭包共享）

    // 构建请求体（提升为变量，供完成回调中规划面板作为“原始请求体”引用）
    const 请求体对象 = (() => {
        const requestBody = {
            message: text,
            session_id: 状态.当前会话ID,
            model_source: 状态.模型来源,
            local_model_name: 状态.选中本地模型 || "",
            plugin_context: localStorage.getItem(NCA_STORAGE_KEYS.plugin),
            activeTab: localStorage.getItem(NCA_STORAGE_KEYS.activeTab) || "develop",
            language: 获取当前语言(),
        };
        if (attachments.length > 0) requestBody.attachments = attachments;
        // 编辑重生成：负载 truncate_at，服务端据此裁剪历史后以 message 作为新的末位输入
        // 本地索引需叠加 消息起始偏移 换算为服务端绝对索引（首屏仅加载最近50条）
        if (truncate_at !== undefined && truncate_at !== null) {
            requestBody.truncate_at = truncate_at + (状态.消息起始偏移 || 0);
        }
        // 规划重发：跳过服务端规划环节，并携带用户在规划面板的决策
        if (skip_planning) requestBody.skip_planning = true;
        if (user_choices) requestBody.user_choices = user_choices;
        return requestBody;
    })();

    // 保存请求体到流式状态，供界面重建时引用
    状态.流式状态.请求体 = 请求体对象;

    await 创建流式聊天({
        消息容器: aiBubble,
        消息体: aiBody,
        滚动容器: refs.消息区域,
        获取消息容器: () => 状态.流式状态.消息容器引用,
        获取消息体: () => 状态.流式状态.消息体引用,
        获取滚动容器: () => 状态.流式状态.滚动容器引用,
        请求体: 请求体对象,
        中止信号: _abortController.signal,
        内容更新回调: (内容) => { 状态.流式状态.累积内容 = 内容; },
        数据处理: (data, 状态引用) => {
            // ── 上下文健康度指标 - 不渲染为聊天消息 ──
            if (data.type === 'context_health') {
                事件总线.emit('context-health-updated', data);
                if (data.warning) {
                    if (data.usage_percent >= 95) {
                        Toast.error(`上下文已满 (${data.usage_percent}%)，建议新建会话以获得最佳体验`);
                    } else if (data.usage_percent >= 85) {
                        Toast.warning(`上下文使用率较高 (${data.usage_percent}%)，复杂任务建议新建会话`);
                    }
                }
                return 'skip';
            }
            // ── 知识库检索状态 - 不渲染为聊天消息，改为侧边栏常驻通知条 ──
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
                const body = 状态.流式状态.消息体引用;
                if (!状态引用.el && body) {
                    状态引用.el = document.createElement('div');
                    状态引用.el.className = 'nca-tool-executing';
                    body.appendChild(状态引用.el);
                    状态.流式状态.工具指示器引用 = 状态引用.el;
                } else if (状态引用.el && body && 状态引用.el.parentNode !== body) {
                    // DOM 已被替换，重新挂载指示器元素
                    状态引用.el.remove();
                    状态引用.el = null;
                }
                if (状态引用.el) {
                    状态引用.el.innerHTML = `<span class="tool-exec-icon">🤔</span> ${t('tool.thinking')} <code></code><span class="tool-exec-dots"></span>`;
                    状态引用.el.querySelector('code').textContent = t('tool.thinking_chars', { n: charCount });
                }
                事件总线.emit(事件.状态栏更新, t('tool.thinking'));
                滚动到底部(refs);
                return 'skip';
            }
            // ── 工具执行状态消息 ──
            if (data.type === 'tool_executing') {
                _工具已执行 = true;  // 标记进入工具循环，后续文本将被噪音过滤
                const toolMatch = (data.content || '').match(/\[正在执行:\s*(.+?)\.{3}\]/);
                const toolName = 工具名显示(toolMatch ? toolMatch[1] : t('tool.generic'));
                const body = 状态.流式状态.消息体引用;
                if (!状态引用.el && body) {
                    状态引用.el = document.createElement('div');
                    状态引用.el.className = 'nca-tool-executing';
                    body.appendChild(状态引用.el);
                    状态.流式状态.工具指示器引用 = 状态引用.el;
                } else if (状态引用.el && body && 状态引用.el.parentNode !== body) {
                    // DOM 已被替换，重新挂载工具指示器元素
                    状态引用.el.remove();
                    状态引用.el = null;
                }
                if (状态引用.el) {
                    状态引用.el.innerHTML = `<span class="tool-exec-icon">⚙️</span> ${t('tool.executing')} <code></code><span class="tool-exec-dots"></span>`;
                    状态引用.el.querySelector('code').textContent = toolName;
                }
                事件总线.emit(事件.状态栏更新, t('tool.status_bar', { name: toolName }));
                滚动到底部(refs);
                return 'skip';
            }
            // 收到正常文本时，移除工具执行指示器
            if (data.content) {
                if (状态引用.el) {
                    状态引用.el.remove();
                    状态引用.el = null;
                }
                _toolExecutingEl = 状态引用.el; // 同步到外部
                // 过滤内容中的 [正在执行: xxx...] 、[思考中: xxx...] 和 [自动继续执行...] 格式文本
                data.content = data.content.replace(/\n?\[正在执行:\s*.+?\.{3}\]\n?/g, '');
                data.content = data.content.replace(/\n?\[思考中:\s*[^\]]*\.{3}\]\n?/g, '');
                data.content = data.content.replace(/\n?\[自动继续执行\.{3}\]\n?/g, '');
                // ── 工具循环噪音过滤：折叠模型在工具调用间的"内心独白" ──
                if (_工具已执行) {
                    const lines = data.content.split('\n');
                    const 保留行 = [];
                    for (const line of lines) {
                        const trimmed = line.trim();
                        if (!trimmed) { 保留行.push(line); continue; }
                        // 代码块标记不过滤
                        if (/^```/.test(trimmed)) { 保留行.push(line); continue; }
                        // 列表项不过滤
                        if (/^[-*+]\s|^\d+[.)]\s/.test(trimmed)) { 保留行.push(line); continue; }
                        // 标题不过滤
                        if (/^#{1,6}\s/.test(trimmed)) { 保留行.push(line); continue; }
                        // 匹配规划噪音模式 → 折叠为紧凑指示器（任何长度都检测）
                        if (_规划噪音模式.test(trimmed) || (trimmed.length > 80 && _规划噪音模式.test(trimmed.slice(0, 40)))) {
                            _工具噪音计数++;
                            _工具噪音累积 += (trimmed + '\n');
                            // 每折叠 5 条噪音输出一次指示器（用粗体+换行独立成段）
                            if (_工具噪音计数 % 5 === 1) {
                                保留行.push(`\n\n**🔧 已自动执行 ${_工具噪音计数} 步操作**\n`);
                            }
                            continue;
                        }
                        // 长文本（>80字符）若不以噪音模式开头则保留
                        if (trimmed.length > 80) { 保留行.push(line); continue; }
                        保留行.push(line);
                    }
                    data.content = 保留行.join('\n');
                }
                事件总线.emit(事件.状态栏更新, t("chat.generating"));
            }
        },
        完成回调: (结果) => {
            // 会话守卫：流式期间用户已切换会话（中止当前流式 已清理残留气泡），
            // 迟到的完成回调不得再渲染/将内容 push 进新会话的消息列表
            if (请求体对象.session_id !== 状态.当前会话ID) return;
            const cbBody = 状态.流式状态.消息体引用;
            const cbContainer = 状态.流式状态.消息容器引用;
            // ── 规划面板分支：需用户确认执行计划 ──
            // 流式管理器在收到 planning_done 且 needs_confirmation 时，会提前结束并回传
            // { type: "planning", planResult }；此时渲染规划面板而非普通 Markdown 消息
            if (结果.type === "planning" && 结果.planResult) {
                if (cbBody) cbBody.innerHTML = ""; // 清除流式光标
                import("./任务规划面板.js").then(({ 渲染规划面板 }) => {
                    渲染规划面板(cbBody, 结果.planResult, {
                        会话id: 状态.当前会话ID,
                        原始请求体: 请求体对象,
                        // 确认：携带 skip_planning + user_choices 重新发起流式（新建 AI 气泡）
                        确认后回调: (userChoices) => {
                            发送消息流式(refs, rootContainer, {
                                content: text,
                                附件: 请求体对象.attachments,
                                跳过用户消息: true,
                                skip_planning: true,
                                user_choices: userChoices,
                            });
                        },
                        // 跳过：仅携带 skip_planning（无 user_choices）直接执行
                        跳过回调: () => {
                            发送消息流式(refs, rootContainer, {
                                content: text,
                                附件: 请求体对象.attachments,
                                跳过用户消息: true,
                                skip_planning: true,
                            });
                        },
                    });
                }).catch((e) => {
                    console.warn("[节点梦工厂] 规划面板加载失败:", e);
                    if (cbBody) cbBody.innerHTML = 简易Markdown渲染(结果.fullContent || "（规划面板加载失败）");
                    绑定代码块复制按钮(cbContainer);
                });
                // 使用当前流式状态的滚动容器（界面重建后可能已更换）
                const currentScroll = 状态.流式状态.滚动容器引用;
                if (currentScroll) {
                    requestAnimationFrame(() => {
                        currentScroll.scrollTop = currentScroll.scrollHeight;
                    });
                }
                return; // 不执行后续的普通消息渲染
            }

            const { fullContent, hasError: err, isAbort, planResult } = 结果;
            hasError = err;
            const 内容 = fullContent || "（无回复）";
            if (cbBody) cbBody.innerHTML = 简易Markdown渲染(内容);
            if (!window.DOMPurify && cbBody) {
                cbBody.dataset.pendingSanitize = "true";
                if (cbContainer) cbContainer._pendingContent = 内容;
            }
            绑定代码块复制按钮(cbContainer);
            // ── 无 questions 的规划摘要（后端已静默注入上下文，前端仅展示）──
            if (planResult && cbBody && Array.isArray(planResult.plan_steps) && planResult.plan_steps.length > 0) {
                const 规划 = planResult;
                const 复杂度文字 = { simple: "简单", moderate: "中等", complex: "复杂" };
                const 摘要面板 = el("details", { class: "nca-planning-summary" });
                const 摘要标题 = el("summary", {}, [
                    el("span", { class: "nca-planning-summary-icon", text: "📋" }),
                    el("span", { text: `执行计划（${复杂度文字[规划.complexity] || 规划.complexity || "中等"}）` }),
                ]);
                摘要面板.appendChild(摘要标题);
                const 步骤列表 = el("ol", { class: "nca-planning-summary-steps" });
                规划.plan_steps.forEach((s) => {
                    步骤列表.appendChild(el("li", { text: String(s) }));
                });
                摘要面板.appendChild(步骤列表);
                if (Array.isArray(规划.risk_notes) && 规划.risk_notes.length > 0) {
                    const 风险区 = el("div", { class: "nca-planning-summary-risks" });
                    规划.risk_notes.forEach((r) => {
                        风险区.appendChild(el("div", { text: `⚠ ${String(r)}` }));
                    });
                    摘要面板.appendChild(风险区);
                }
                cbBody.insertBefore(摘要面板, cbBody.firstChild);
            }
            // 本地 API Key 直连后不再显示本次消耗
            // 记录到消息列表
            状态.当前消息列表.push({ role: "assistant", content: 内容, timestamp: Date.now() });
            // 虚拟滚动启用时：移除流式临时气泡，让滚动器接管渲染
            // 注意：界面重建后 refs 可能指向旧实例，此时跳过虚拟滚动处理
            if (refs._虚拟滚动 && refs._虚拟滚动.容器 === refs.消息区域) {
                const oldBubble = 状态.流式状态.消息容器引用;
                if (oldBubble && oldBubble.parentNode) oldBubble.parentNode.removeChild(oldBubble);
                refs._虚拟滚动.追加新消息();
            } else if (refs._虚拟滚动) {
                // refs 已过时但虚拟滚动存在：仅追加消息数据，不操作旧 DOM
                refs._虚拟滚动.追加新消息();
            } else {
                _尝试启用虚拟滚动(refs);
            }
        },
    });

    // 5. 恢复状态
    _isStreaming = false;
    _abortController = null;
    状态.正在发送 = false;
    状态.流式状态.活跃 = false;
    状态.流式状态.累积内容 = "";
    状态.流式状态.中止控制器 = null;
    状态.流式状态.消息体引用 = null;
    状态.流式状态.消息容器引用 = null;
    状态.流式状态.滚动容器引用 = null;
    状态.流式状态.工具指示器引用 = null;
    _工具已执行 = false;
    _工具噪音计数 = 0;
    _工具噪音累积 = '';
    事件总线.emit(事件.流式状态变更, false);
    refs.输入框.disabled = false;
    refs.发送按钮.disabled = false;
    事件总线.emit(事件.发送状态变更, false);
    事件总线.emit(事件.连接状态变更, hasError ? "error" : "online");
    事件总线.emit(事件.状态栏更新, hasError ? "错误" : "就绪");

    // 移除停止按钮：界面重建后恢复流程会创建新按钮并禁用新输入区，
    // 闭包中的 stopBtn/refs 已随旧 DOM 失效，须通过全局流式状态引用一并清理
    if (stopBtn.parentNode) stopBtn.remove();
    const 当前停止按钮 = 状态.流式状态.停止按钮引用;
    if (当前停止按钮 && 当前停止按钮 !== stopBtn) {
        const 当前输入区 = 当前停止按钮.closest(".nca-input-area");
        if (当前输入区) {
            const 输入框 = 当前输入区.querySelector("textarea");
            if (输入框) 输入框.disabled = false;
            const 发送按钮 = 当前输入区.querySelector(".nca-send-btn");
            if (发送按钮) 发送按钮.disabled = false;
        }
        当前停止按钮.remove();
    }
    状态.流式状态.停止按钮引用 = null;

    滚动到底部(refs);
}

// ═══════════════════════════════════════════════════════════════
// 输入区域
// ═══════════════════════════════════════════════════════════════

export function 渲染输入区域(refs, rootContainer) {
    // 缓存 refs/rootContainer：编辑模式确认后调用 发送消息流式 需两者
    _当前refs = refs;
    _当前rootContainer = rootContainer;
    const area = el("div", { class: "nca-input-area" });

    // 文件预览区
    refs.附件预览区 = el("div", { class: "nca-attachments-preview" });
    refs.附件预览区.style.display = "none";
    area.appendChild(refs.附件预览区);

    const wrapper = el("div", { class: "nca-input-wrapper" });

    // "+"按钮——文件选择
    refs.文件按钮 = el("button", { class: "nca-attach-btn", html: "+", title: t("chat.attach") });
    refs.文件输入 = el("input", {
        type: "file",
        multiple: "true",
        accept: "image/png,image/jpeg,image/gif,image/webp,.txt,.py,.js,.json,.md,.css,.html,.yaml,.yml,.toml,.cfg,.ini,.sh,.bat,.wav,.mp3,.m4a,.ogg,.flac",
        style: { display: "none" },
    });
    refs.文件按钮.addEventListener("click", () => refs.文件输入.click());
    refs.文件输入.addEventListener("change", (e) => 处理文件选择(e, refs));

    refs.输入框 = el("textarea", {
        rows: "1",
        placeholder: t("chat.placeholder"),
    });

    refs.发送按钮 = el("button", { class: "nca-send-btn", html: "▶" });

    // 自适应高度
    refs.输入框.addEventListener("input", () => {
        refs.输入框.style.height = "auto";
        refs.输入框.style.height = Math.min(refs.输入框.scrollHeight, 100) + "px";
        更新发送按钮状态(refs);
    });

    // 键盘事件：Enter 发送（Shift+Enter 换行）
    refs.输入框.addEventListener("keydown", (e) => {
        if (e.key === "Enter" && !e.shiftKey) {
            e.preventDefault();
            发送消息流式(refs, rootContainer);
        }
    });

    refs.发送按钮.addEventListener("click", () => 发送消息流式(refs, rootContainer));

    wrapper.appendChild(refs.文件按钮);
    wrapper.appendChild(refs.文件输入);
    wrapper.appendChild(refs.输入框);
    wrapper.appendChild(refs.发送按钮);
    area.appendChild(wrapper);
    return area;
}

// ═══════════════════════════════════════════════════════════════
// 流式恢复 — 界面重建（侧边栏折叠/切换）时恢复进行中的流式输出
// ═══════════════════════════════════════════════════════════════

/**
 * 检查当前是否有进行中的流式输出
 */
export function 是否流式中() {
    return 状态.流式状态.活跃;
}

/**
 * 在界面重建时创建流式恢复气泡
 * 将新的 DOM 元素引用写入全局流式状态，使后台 SSE 流继续渲染到新 DOM
 *
 * @param {HTMLElement} 消息区域 - 消息列表容器
 * @param {HTMLElement} 滚动容器 - 滚动目标元素
 * @returns {{ aiBody: HTMLElement, aiBubble: HTMLElement }|null}
 */
export function 创建流式恢复气泡(消息区域, 滚动容器) {
    if (!状态.流式状态.活跃) return null;

    const 累积内容 = 状态.流式状态.累积内容 || "";

    // 创建 AI 回复气泡，显示已累积的内容和流式光标
    const aiBubble = el("div", { class: "nca-msg assistant" }, [
        el("div", { class: "nca-msg-header" }, [
            el("span", { class: "msg-role", text: "─ AI" }),
        ]),
        el("div", { class: "nca-msg-body" }),
    ]);
    const aiBody = aiBubble.querySelector(".nca-msg-body");
    if (累积内容) {
        aiBody.innerHTML = 简易Markdown渲染(累积内容) + '<span class="nca-streaming-cursor"></span>';
    } else {
        aiBody.innerHTML = '<span class="nca-streaming-cursor"></span>';
    }

    // 清空消息区域并追加恢复气泡
    if (消息区域.querySelector(".nca-welcome")) {
        消息区域.innerHTML = "";
    }
    消息区域.appendChild(aiBubble);

    // 更新全局流式状态中的 DOM 引用，使后台 SSE 流写入新 DOM
    状态.流式状态.消息体引用 = aiBody;
    状态.流式状态.消息容器引用 = aiBubble;
    状态.流式状态.滚动容器引用 = 滚动容器 || 消息区域;

    // 重置工具指示器引用（旧 DOM 已销毁）
    状态.流式状态.工具指示器引用 = null;

    // 滚动到底部
    const scrollTarget = 滚动容器 || 消息区域;
    if (scrollTarget) {
        requestAnimationFrame(() => {
            scrollTarget.scrollTop = scrollTarget.scrollHeight;
        });
    }

    return { aiBody, aiBubble };
}
