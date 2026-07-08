// ═══════════════════════════════════════════════════════════════
// 消息渲染器.js — 消息气泡渲染、输入区构建、流式响应
// NodeCraft AI — Luxury Terminal / Neo-Noir Hacker
// ═══════════════════════════════════════════════════════════════

import {
    el, 简易Markdown渲染, 应用Prism高亮, LOGO_SVG, NCA_STORAGE_KEYS, registerCleanup, Toast,
    显示视觉能力警告, 移除视觉能力警告, 安全存储读, 工具名显示, 追加附件缩略图,
} from "./工具函数.js";
import {
    事件总线, 事件, 状态,
    发送消息, 创建会话, 获取当前模型名称, 格式化时间,
    创建消耗信息DOM, 检查余额,
} from "./交互与状态.js";
import { 创建流式聊天 } from "./流式聊天管理器.js";
import { t } from "./i18n.js";
import { 显示知识库通知 } from "./知识库通知条.js";

// ─── 流式响应状态 ────────────────────────────────────────────
let _abortController = null;
let _isStreaming = false;

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

// ═══════════════════════════════════════════════════════════════
// 虚拟滚动（消息列表性能优化）
// 仅在消息数量 >= 启用阈值 时启用，向后兼容原渲染逻辑
// ═══════════════════════════════════════════════════════════════
const 虚拟滚动配置 = {
    启用阈值: 100,      // 超过该数量时启用虚拟滚动
    缓冲区大小: 10,     // 可视区上下各多渲染的消息条数
    预估消息高度: 120,  // 未测量消息的默认高度（px）
};

class 虚拟滚动管理器 {
    constructor(容器元素, 消息列表, 渲染单条, 绑定后处理) {
        this.容器 = 容器元素;
        this.消息列表 = 消息列表;
        this.渲染单条 = 渲染单条;
        this.绑定后处理 = 绑定后处理;
        this.已测量高度 = new Map(); // 消息索引 → 实际高度
        this.startIndex = 0;
        this.endIndex = 0;
        this._rafId = null;
        this._rafResizeId = null;
        this.滚动监听 = this._节流滚动处理.bind(this);
        // ResizeObserver：监听消息气泡尺寸变化，解决流式内容动态增长导致的高度抖动
        this._resizeObserver = (typeof ResizeObserver !== "undefined") ? new ResizeObserver(entries => {
            if (this._rafResizeId) return; // RAF 节流，避免频繁重计算
            this._rafResizeId = requestAnimationFrame(() => {
                this._rafResizeId = null;
                let 需要更新 = false;
                for (const entry of entries) {
                    const index = entry.target._虚拟索引;
                    if (index !== undefined) {
                        const 新高度 = entry.target.offsetHeight;
                        if (新高度 > 0 && this.已测量高度.get(index) !== 新高度) {
                            this.已测量高度.set(index, 新高度);
                            需要更新 = true;
                        }
                    }
                }
                if (需要更新) this._渲染可视消息();
            });
        }) : null;
        this.顶部占位 = document.createElement("div");
        this.顶部占位.className = "nca-vscroll-spacer-top";
        this.顶部占位.style.cssText = "width:100%;flex-shrink:0;pointer-events:none;";
        this.底部占位 = document.createElement("div");
        this.底部占位.className = "nca-vscroll-spacer-bottom";
        this.底部占位.style.cssText = "width:100%;flex-shrink:0;pointer-events:none;";
        this._挂载();
    }

    _挂载() {
        this.容器.innerHTML = "";
        this.容器.appendChild(this.顶部占位);
        this.容器.appendChild(this.底部占位);
        this.容器.addEventListener("scroll", this.滚动监听, { passive: true });
        this._渲染可视消息();
    }

    销毁() {
        this.容器.removeEventListener("scroll", this.滚动监听);
        if (this._rafId) cancelAnimationFrame(this._rafId);
        this._rafId = null;
        if (this._resizeObserver) {
            this._resizeObserver.disconnect();
            this._resizeObserver = null;
        }
        if (this._rafResizeId) {
            cancelAnimationFrame(this._rafResizeId);
            this._rafResizeId = null;
        }
    }

    _获取高度(i) {
        const cached = this.已测量高度.get(i);
        if (cached) return cached;
        // 根据消息内容长度动态预估高度
        const msg = this.消息列表[i];
        if (msg && msg.content && msg.content.length > 500) {
            return Math.min(虚拟滚动配置.预估消息高度 * Math.ceil(msg.content.length / 300), 600);
        }
        return 虚拟滚动配置.预估消息高度;
    }

    _计算可视范围() {
        const scrollTop = this.容器.scrollTop;
        const viewportHeight = this.容器.clientHeight;
        const total = this.消息列表.length;
        let acc = 0;
        let startIndex = 0;
        for (let i = 0; i < total; i++) {
            const h = this._获取高度(i);
            if (acc + h > scrollTop) { startIndex = i; break; }
            acc += h;
            startIndex = i + 1;
        }
        let endIndex = startIndex;
        let cumul = acc;
        for (let i = startIndex; i < total; i++) {
            cumul += this._获取高度(i);
            endIndex = i;
            if (cumul >= scrollTop + viewportHeight) break;
        }
        const buf = 虚拟滚动配置.缓冲区大小;
        startIndex = Math.max(0, startIndex - buf);
        endIndex = Math.min(total - 1, endIndex + buf);
        let topPad = 0;
        for (let i = 0; i < startIndex; i++) topPad += this._获取高度(i);
        let bottomPad = 0;
        for (let i = endIndex + 1; i < total; i++) bottomPad += this._获取高度(i);
        return { startIndex, endIndex, topPad, bottomPad };
    }

    _渲染可视消息() {
        if (this.消息列表.length === 0) {
            this.顶部占位.style.height = "0px";
            this.底部占位.style.height = "0px";
            return;
        }
        const { startIndex, endIndex, topPad, bottomPad } = this._计算可视范围();
        this.startIndex = startIndex;
        this.endIndex = endIndex;
        // 回收占位之间的旧 DOM 节点
        let node = this.顶部占位.nextSibling;
        while (node && node !== this.底部占位) {
            const next = node.nextSibling;
            if (this._resizeObserver) this._resizeObserver.unobserve(node);
            this.容器.removeChild(node);
            node = next;
        }
        this.顶部占位.style.height = topPad + "px";
        this.底部占位.style.height = bottomPad + "px";
        // 渲染可视范围 + 缓冲区
        const 新增节点 = [];
        for (let i = startIndex; i <= endIndex; i++) {
            const msg = this.消息列表[i];
            if (!msg) continue;
            // 传递索引：消息编辑功能需要知道当前消息在列表中的位置
            const dom = this.渲染单条(msg, i);
            dom.dataset.vscrollIndex = String(i);
            dom._虚拟索引 = i;
            this.容器.insertBefore(dom, this.底部占位);
            if (this.绑定后处理) this.绑定后处理(dom);
            // 观察该消息气泡尺寸，流式内容增长时自动更新已测量高度
            if (this._resizeObserver) this._resizeObserver.observe(dom);
            新增节点.push({ index: i, dom });
        }
        // 下一帧测量实际高度并缓存
        requestAnimationFrame(() => {
            for (const { index, dom } of 新增节点) {
                const h = dom.offsetHeight;
                if (h > 0) this.已测量高度.set(index, h);
            }
        });
    }

    _节流滚动处理() {
        if (this._rafId) return;
        this._rafId = requestAnimationFrame(() => {
            this._rafId = null;
            this._渲染可视消息();
        });
    }

    重新渲染() {
        this._渲染可视消息();
    }

    追加新消息() {
        // 调用方已将新消息 push 到共享 消息列表
        this._渲染可视消息();
    }

    跳转到消息(index) {
        let offset = 0;
        for (let i = 0; i < index; i++) offset += this._获取高度(i);
        this.容器.scrollTop = offset;
    }

    // 编辑重生成后调用：消息列表已被外部截断到 newLength，
    // 清理 >= newLength 的高度缓存并重新渲染可视区。
    截断消息(newLength) {
        for (const key of Array.from(this.已测量高度.keys())) {
            if (key >= newLength) this.已测量高度.delete(key);
        }
        this._渲染可视消息();
    }
}

function _创建消息DOM(msg, index) {
    const timeStr = 格式化时间(msg.timestamp);
    const roleLabel = msg.role === "user" ? "用户" : msg.role === "assistant" ? "AI" : "系统";

    const headerChildren = [
        el("span", { class: "msg-role", text: `─ ${roleLabel}` }),
        timeStr ? el("span", { class: "msg-time", text: timeStr }) : null,
    ];

    // 用户消息追加 hover-显示的编辑按钮：进入行内编辑→截断后续历史→重新生成
    if (msg.role === "user") {
        const editBtn = el("button", { class: "nca-msg-edit-btn", text: "✎", title: "编辑并重新生成" });
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
    if (msg.role !== "user" && msg.billing) {
        const billingDOM = 创建消耗信息DOM(msg.billing.cost, msg.billing.balance);
        if (billingDOM) children.push(billingDOM);
    }
    const msgEl = el("div", { class: `nca-msg ${msg.role}` }, children);
    // 缓存索引：编辑回调与虚拟滚动测量都会读取
    msgEl._消息索引 = index;
    return msgEl;
}

// ─── 消息内编辑模式：行内 textarea + 确认/取消 ───────────────
function _进入编辑模式(msgEl, msg, index) {
    if (_isStreaming) return; // 流式生成中禁止编辑
    if (msgEl.classList.contains("nca-msg-editing")) return;

    msgEl.classList.add("nca-msg-editing");
    const body = msgEl.querySelector(".nca-msg-body");
    if (!body) return;
    const originalHTML = body.innerHTML;

    const textarea = el("textarea", { class: "nca-edit-textarea", spellcheck: "false" });
    textarea.value = msg.content || "";

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
        // 光标置于末尾
        try { textarea.setSelectionRange(textarea.value.length, textarea.value.length); } catch (_) {}
        自适应高度();
    });
    textarea.addEventListener("input", 自适应高度);

    // ESC 取消、Ctrl/Cmd+Enter 确认
    textarea.addEventListener("keydown", (e) => {
        if (e.key === "Escape") { e.preventDefault(); cancelBtn.click(); }
        else if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) { e.preventDefault(); confirmBtn.click(); }
    });

    cancelBtn.addEventListener("click", () => {
        msgEl.classList.remove("nca-msg-editing");
        body.innerHTML = originalHTML;
        绑定代码块复制按钮(msgEl);
    });

    confirmBtn.addEventListener("click", () => {
        const newContent = textarea.value.trim();
        if (!newContent) {
            try { Toast.warning("消息内容不能为空"); } catch (_) {}
            return;
        }
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
    });
}

export function 绑定代码块复制按钮(containerEl) {
    containerEl.querySelectorAll(".nca-code-copy").forEach(btn => {
        // 防重复绑定：同一按钮只注册一次 click
        if (btn.dataset.bound === "1") return;
        btn.dataset.bound = "1";
        btn.addEventListener("click", () => {
            const pre = btn.closest("pre");
            const code = pre?.querySelector("code")?.textContent || "";
            navigator.clipboard.writeText(code).then(() => {
                btn.classList.add("copied");
                btn.textContent = t("common.copied");
                setTimeout(() => {
                    btn.classList.remove("copied");
                    btn.textContent = t("common.copy");
                }, 1500);
            }).catch(() => {
                // clipboard API 不可用时降级 execCommand
                const ta = document.createElement("textarea");
                ta.value = code;
                ta.style.position = "fixed";
                ta.style.opacity = "0";
                document.body.appendChild(ta);
                ta.select();
                try { document.execCommand("copy"); } catch(_) {}
                ta.remove();
                btn.classList.add("copied");
                btn.textContent = t("common.copied");
                setTimeout(() => {
                    btn.classList.remove("copied");
                    btn.textContent = t("common.copy");
                }, 1500);
            });
        });
    });
    // 应用 Prism 语法高亮（异步，不阻塞渲染）
    应用Prism高亮(containerEl);
}

function _尝试启用虚拟滚动(refs) {
    if (refs._虚拟滚动) return;
    const list = 状态.当前消息列表;
    if (!list || list.length < 虚拟滚动配置.启用阈值) return;
    refs._虚拟滚动 = new 虚拟滚动管理器(
        refs.消息区域, list, _创建消息DOM, 绑定代码块复制按钮,
    );
    refs.消息区域.scrollTop = refs.消息区域.scrollHeight;
}

// ═══════════════════════════════════════════════════════════════
// 消息区域渲染
// ═══════════════════════════════════════════════════════════════

export function 渲染欢迎页(container, refs) {
    container.innerHTML = "";
    const welcome = el("div", { class: "nca-welcome" }, [
        el("div", { class: "nca-welcome-icon", html: LOGO_SVG }),
        el("h3", { text: t("brand.name") }),
        el("p", { text: t("chat.welcome_subtitle") }),
        el("div", { class: "nca-quick-actions" }, [
            创建快捷操作("⚡", "新建插件项目", () => {
                if (refs.显示创建项目对话框) refs.显示创建项目对话框();
            }),
            创建快捷操作("◇", "帮我写一个图像缩放节点", () => 快捷输入(refs, "帮我写一个图像缩放节点")),
            创建快捷操作("▸", "如何开发 ComfyUI 节点？", () => 快捷输入(refs, "如何开发 ComfyUI 自定义节点？请给我一个完整的教程。")),
        ]),
    ]);
    container.appendChild(welcome);
}

function 创建快捷操作(icon, label, onClick) {
    const btn = el("button", { class: "nca-quick-action" }, [
        el("span", { class: "qa-icon", text: icon }),
        el("span", { text: label }),
    ]);
    btn.addEventListener("click", onClick);
    return btn;
}

function 快捷输入(refs, text) {
    refs.输入框.value = text;
    refs.输入框.dispatchEvent(new Event("input"));
    refs.输入框.focus();
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
        滚动到底部(refs);
        return;
    }
    // 小消息列表：保持原有渲染逻辑（传入索引以支持编辑）
    messages.forEach((msg, index) => 追加消息DOM(refs, msg, index));
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

export function 滚动到底部(refs) {
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

    // 发送前余额检查（余额不足则阻断；查询失败降级放行）
    const 余额检查 = await 检查余额();
    if (!余额检查.sufficient && 余额检查.balance >= 0) {
        try { Toast.warning("额度已耗尽，请充值后继续使用"); } catch (_) {}
        return;
    }

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
    状态.正在发送 = true;
    refs.输入框.disabled = true;
    refs.发送按钮.disabled = true;
    事件总线.emit(事件.发送状态变更, true);
    事件总线.emit(事件.连接状态变更, "busy");
    事件总线.emit(事件.状态栏更新, "生成中...");

    // 显示停止按钮
    const stopBtn = el("button", { class: "nca-stop-btn", text: "■ 停止生成" });
    stopBtn.addEventListener("click", () => {
        if (_abortController) _abortController.abort();
    });
    refs.停止按钮容器 = stopBtn;
    // 插入到输入区域前面
    const inputArea = refs.输入框.closest(".nca-input-area");
    if (inputArea) inputArea.insertBefore(stopBtn, inputArea.firstChild);

    // 4. 开始流式请求
    _abortController = new AbortController();
    let hasError = false;
    let _toolExecutingEl = null; // 工具执行状态指示器元素（闭包共享）

    // 构建请求体（提升为变量，供完成回调中规划面板作为“原始请求体”引用）
    const 请求体对象 = (() => {
        const requestBody = {
            message: text,
            session_id: 状态.当前会话ID,
            model_source: 状态.模型来源,
            local_model_name: 状态.选中本地模型 || "",
            plugin_context: 安全存储读(NCA_STORAGE_KEYS.plugin),
            activeTab: 安全存储读(NCA_STORAGE_KEYS.activeTab, "develop"),
        };
        if (attachments.length > 0) requestBody.attachments = attachments;
        // 编辑重生成：负载 truncate_at，服务端据此裁剪历史后以 message 作为新的末位输入
        if (truncate_at !== undefined && truncate_at !== null) {
            requestBody.truncate_at = truncate_at;
        }
        // 规划重发：跳过服务端规划环节，并携带用户在规划面板的决策
        if (skip_planning) requestBody.skip_planning = true;
        if (user_choices) requestBody.user_choices = user_choices;
        return requestBody;
    })();

    await 创建流式聊天({
        消息容器: aiBubble,
        消息体: aiBody,
        滚动容器: refs.消息区域,
        请求体: 请求体对象,
        中止信号: _abortController.signal,
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
                事件总线.emit(事件.状态栏更新, `执行工具: ${toolName}...`);
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
                // 过滤内容中的 [正在执行: xxx...] 格式文本
                data.content = data.content.replace(/\n?\[正在执行:\s*.+?\.{3}\]\n?/g, '');
                事件总线.emit(事件.状态栏更新, "生成中...");
            }
        },
        完成回调: (结果) => {
            // ── 规划面板分支：需用户确认执行计划 ──
            // 流式管理器在收到 planning_done 且 needs_confirmation 时，会提前结束并回传
            // { type: "planning", planResult }；此时渲染规划面板而非普通 Markdown 消息
            if (结果.type === "planning" && 结果.planResult) {
                aiBody.innerHTML = ""; // 清除流式光标
                import("./任务规划面板.js").then(({ 渲染规划面板 }) => {
                    渲染规划面板(aiBody, 结果.planResult, {
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
                    aiBody.innerHTML = 简易Markdown渲染(结果.fullContent || "（规划面板加载失败）");
                    绑定代码块复制按钮(aiBubble);
                });
                滚动到底部(refs);
                return; // 不执行后续的普通消息渲染
            }

            const { fullContent, hasError: err, isAbort, billing } = 结果;
            hasError = err;
            const 内容 = fullContent || "（无回复）";
            aiBody.innerHTML = 简易Markdown渲染(内容);
            if (!window.DOMPurify) {
                aiBody.dataset.pendingSanitize = "true";
                aiBubble._pendingContent = 内容;
            }
            绑定代码块复制按钮(aiBubble);
            // 显示本次消耗
            if (billing) {
                const billingDOM = 创建消耗信息DOM(billing.cost, billing.balance);
                if (billingDOM) aiBubble.appendChild(billingDOM);
            }
            // 记录到消息列表
            状态.当前消息列表.push({ role: "assistant", content: 内容, timestamp: Date.now(), billing });
            // 虚拟滚动启用时：移除流式临时气泡，让滚动器接管渲染
            if (refs._虚拟滚动) {
                if (aiBubble.parentNode) aiBubble.parentNode.removeChild(aiBubble);
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
    refs.输入框.disabled = false;
    refs.发送按钮.disabled = false;
    事件总线.emit(事件.发送状态变更, false);
    事件总线.emit(事件.连接状态变更, hasError ? "error" : "online");
    事件总线.emit(事件.状态栏更新, hasError ? "错误" : "就绪");

    // 移除停止按钮
    if (stopBtn.parentNode) stopBtn.remove();

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
        accept: "image/png,image/jpeg,image/gif,image/webp,.txt,.py,.js,.json,.md,.css,.html,.yaml,.yml,.toml,.cfg,.ini,.sh,.bat",
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

// ─── 文件附件处理 ────────────────────────────────────────────
const 允许的图片类型 = ["image/png", "image/jpeg", "image/gif", "image/webp"];
const 最大附件数 = 6;

function 处理文件选择(e, refs) {
    const files = Array.from(e.target.files || []);
    if (!files.length) return;

    const 当前数量 = 状态.待发送附件.length;
    if (当前数量 + files.length > 最大附件数) {
        Toast.warning(t("chat.attach_max", { max: 最大附件数 }));
        refs.文件输入.value = "";
        return;
    }

    // 检测是否包含图片文件，触发视觉能力警告
    const 含图片 = files.some(f => 允许的图片类型.includes(f.type));
    if (含图片) {
        const inputArea = refs.输入框?.closest('.nca-input-area');
        if (inputArea) 显示视觉能力警告(inputArea);
    }

    // 中止之前的文件读取操作（防止切换页面/重复选择导致泄漏）
    if (refs._fileReaderAbort) {
        try { refs._fileReaderAbort.abort(); } catch (_) {}
    }
    const abortController = new AbortController();
    refs._fileReaderAbort = abortController;

    let 已处理 = 0;
    files.forEach(file => {
        const reader = new FileReader();
        reader.onload = (ev) => {
            if (abortController.signal.aborted) return;
            状态.待发送附件.push({
                name: file.name,
                type: file.type || "application/octet-stream",
                size: file.size,
                data: ev.target.result,
            });
            已处理++;
            if (已处理 === files.length && !abortController.signal.aborted) {
                渲染附件预览(refs);
                更新发送按钮状态(refs);
            }
        };
        reader.onerror = () => {
            if (abortController.signal.aborted) return;
            已处理++;
            console.warn("[节点梦工厂] 文件读取失败:", file.name);
        };
        reader.readAsDataURL(file);
    });

    refs.文件输入.value = "";
}

function 移除附件(index, refs) {
    状态.待发送附件.splice(index, 1);
    渲染附件预览(refs);
    更新发送按钮状态(refs);
    // 如果没有图片附件了，移除警告
    const 还有图片 = 状态.待发送附件.some(f => 允许的图片类型.includes(f.type));
    if (!还有图片) {
        const inputArea = refs.输入框?.closest('.nca-input-area');
        if (inputArea) 移除视觉能力警告(inputArea);
    }
}

function 渲染附件预览(refs) {
    const container = refs.附件预览区;
    if (!container) return;
    container.innerHTML = "";

    if (状态.待发送附件.length === 0) {
        container.style.display = "none";
        return;
    }

    container.style.display = "flex";

    状态.待发送附件.forEach((file, idx) => {
        const 是图片 = 允许的图片类型.includes(file.type);
        const card = el("div", { class: "nca-attach-card" });

        if (是图片) {
            const thumb = el("img", { class: "nca-attach-thumb", src: file.data });
            card.appendChild(thumb);
        } else {
            const ext = file.name.split(".").pop() || "file";
            const icon = el("div", { class: "nca-attach-icon", text: ext.toUpperCase() });
            card.appendChild(icon);
        }

        const name = el("span", { class: "nca-attach-name", text: file.name, title: file.name });
        card.appendChild(name);

        const removeBtn = el("button", { class: "nca-attach-remove", text: "×" });
        removeBtn.addEventListener("click", () => 移除附件(idx, refs));
        card.appendChild(removeBtn);

        container.appendChild(card);
    });
}

function 更新发送按钮状态(refs) {
    const 有内容 = refs.输入框.value.trim().length > 0 || 状态.待发送附件.length > 0;
    refs.发送按钮.classList.toggle("active", 有内容);
}
