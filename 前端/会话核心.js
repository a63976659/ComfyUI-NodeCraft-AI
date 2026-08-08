// ═══════════════════════════════════════════════════════════════
// 会话核心.js — 三界面（开发主聊天 / 优化 / 可视化）共享的会话管线
// NodeCraft AI — Luxury Terminal / Neo-Noir Hacker
//
// 【长期约定】三界面非特别注明的会话机制一律在此实现一次，各界面自动继承：
//   1. 新增 SSE 事件类型、新增完成回调分支（ask_user/planning 类交互）→ 只改本文件
//   2. 界面独有行为必须通过视图适配器的钩子声明，禁止在界面文件复制管线逻辑
//   3. 视图适配器由各界面 shim 组装：
//      - 消息渲染器.js    发送消息流式（主聊天：虚拟滚动/会话守卫/流式恢复）
//      - 面板会话公共.js  发送面板消息（优化/可视化：会话自动创建/插件目录校验）
//
// 依赖方向：仅依赖底层模块，不导入 消息渲染器.js 与 面板会话公共.js，
// 避免新增循环依赖（与 流式聊天管理器.js 的环为既有延迟解析模式）。
// ═══════════════════════════════════════════════════════════════

import {
    el, 简易Markdown渲染, Toast, 工具名显示, 追加附件缩略图, 创建提问面板, 创建行内编辑器, 移除视觉能力警告,
} from "./工具函数.js";
import { 事件总线, 事件, 状态, 格式化时间 } from "./交互与状态.js";
import { 创建流式聊天 } from "./流式聊天管理器.js";
import { t, 获取当前语言 } from "./i18n.js";
import { 显示知识库通知 } from "./知识库通知条.js";
import { 绑定代码块复制按钮 } from "./消息渲染/代码复制.js";

// ─── 全局并发保护：三界面同一时刻仅允许一条流式（界面互斥，单一标志即可）───
let _发送锁 = false;

// ─── 工具循环噪音折叠模式（三界面共用一份正则）───
// 模型在工具调用之间产生的"内心独白"（如"让我先了解..."）自动折叠为紧凑指示器
const _噪音模式 = /^(让我|我先|好的[，,]让我|现在(创建|更新|修复|实现|读取|写入|设计|安装|配置|检查|验证)|我来(设计|实现|创建|编写|修改)|文件.*截断|Let me |I'll |Now let|First let|让我用|让我查看|让我搜索|让我先看|让我重新|让我完整|让我精确|让我分段|让我全面|我来看看|我先看看|好的[，,]让我先|(读取|继续读取|先查看|先了解|先检查|分段读取|完整读取|获取|用分段|用更|用更精|先读取|先完整|先全面|先获取|查看当前|查看完整|了解当前|了解完整|检查当前|检查完整).*)/;

// ═══════════════════════════════════════════════════════════════
// 消息气泡工厂（历史渲染与流式用户气泡共用）
// ═══════════════════════════════════════════════════════════════

/**
 * 创建消息气泡 DOM（统一实现：header/编辑按钮/长消息折叠/附件缩略图/pending-sanitize）
 * @param {Object} msg - { role, content, timestamp?, attachments? }
 * @param {number} index - 消息在列表中的索引（编辑重生成 truncate_at 用）
 * @param {Object} [opts]
 * @param {Function} [opts.编辑点击] - (msgEl, index) => void，仅用户消息绑定编辑按钮
 * @returns {HTMLElement}
 */
export function 创建消息DOM(msg, index, opts = {}) {
    const timeStr = msg.timestamp ? 格式化时间(msg.timestamp) : "";
    const roleLabel = msg.role === "user" ? t("chat.role_user") : msg.role === "assistant" ? "AI" : "系统";

    const headerChildren = [
        el("span", { class: "msg-role", text: `─ ${roleLabel}` }),
        timeStr ? el("span", { class: "msg-time", text: timeStr }) : null,
    ];

    // 用户消息追加 hover-显示的编辑按钮：进入行内编辑→截断后续历史→重新生成
    if (msg.role === "user" && typeof opts.编辑点击 === "function") {
        const editBtn = el("button", { class: "nca-msg-edit-btn", text: "✎", title: t("chat.edit_regen") });
        editBtn.addEventListener("click", (e) => {
            e.stopPropagation();
            const host = editBtn.closest(".nca-msg");
            if (!host) return;
            opts.编辑点击(host, index);
        });
        headerChildren.push(editBtn);
    }

    const 内容 = msg.content || "";
    const msgBody = el("div", { class: "nca-msg-body", html: 简易Markdown渲染(内容) });
    // 长消息折叠：超过2000字符默认折叠（三界面统一）
    if (内容.length > 2000) {
        const preview = 内容.substring(0, 200);
        msgBody.innerHTML = '';
        const details = document.createElement('details');
        details.className = 'nca-msg-collapsible';
        const summary = document.createElement('summary');
        summary.textContent = `📄 长消息 (${内容.length}字) — 点击展开`;
        details.appendChild(summary);
        const fullDiv = document.createElement('div');
        fullDiv.innerHTML = 简易Markdown渲染(内容);
        details.appendChild(fullDiv);
        msgBody.appendChild(details);
    }
    // DOMPurify 未就绪时标记待消毒，原始内容缓存到元素上供重渲染自取（三界面统一）
    if (!window.DOMPurify) {
        msgBody.dataset.pendingSanitize = "true";
    }
    // 用户消息附带附件图片缩略图
    if (msg.role === "user" && msg.attachments && msg.attachments.length > 0) {
        追加附件缩略图(msgBody, msg.attachments);
    }
    const msgEl = el("div", { class: `nca-msg ${msg.role}` }, [
        el("div", { class: "nca-msg-header" }, headerChildren),
        msgBody,
    ]);
    // 缓存索引与原始内容：编辑回调与待消毒重渲染都会读取
    msgEl._消息索引 = index;
    msgEl._原始内容 = 内容;
    msgEl._pendingContent = 内容;
    return msgEl;
}

// ─── DOMPurify 就绪/失败后重渲染待消毒消息（document 级扫描，覆盖三界面）──
// ready 事件：DOMPurify 加载成功，简易Markdown渲染 走 DOMPurify 消毒路径
// failed 事件：DOMPurify 加载失败，简易Markdown渲染 走 _本地消毒 路径（DOMParser 白名单）
function _重渲染待处理消息() {
    const 待处理 = document.querySelectorAll('.nca-msg-body[data-pending-sanitize="true"]');
    if (待处理.length === 0) return;
    待处理.forEach(body => {
        const msgEl = body.closest('.nca-msg');
        if (!msgEl) return;
        // 原始内容统一缓存在元素上（创建消息DOM / 流式完成回调均写入）
        const rawContent = msgEl._pendingContent ?? msgEl._原始内容;
        if (rawContent !== undefined) {
            body.innerHTML = 简易Markdown渲染(rawContent);
            body.removeAttribute('data-pending-sanitize');
            绑定代码块复制按钮(msgEl);
        }
    });
}
window.addEventListener('nca-dompurify-ready', _重渲染待处理消息);
window.addEventListener('nca-dompurify-failed', _重渲染待处理消息);

// ═══════════════════════════════════════════════════════════════
// SSE 事件处理器（三界面共用：context_health/kb_status/thinking/tool_executing/噪音过滤）
// ═══════════════════════════════════════════════════════════════

/**
 * 创建统一的 SSE 数据处理函数
 * @param {Object} ctx
 * @param {Function} ctx.消息体获取 - () => HTMLElement|null，当前流式消息体（支持界面重建后换新 DOM）
 * @param {Function} ctx.滚动 - () => void，指示器更新后的滚动回调
 * @returns {Function} (data, 状态引用) => 'skip'|undefined（签名对齐 创建流式聊天.数据处理）
 */
export function 创建SSE事件处理器({ 消息体获取, 滚动 }) {
    const 噪音状态 = { 工具已执行: false, 噪音计数: 0 };

    // 指示器元素懒创建；界面重建导致 DOM 被替换时自动重新挂载
    const 指示器就位 = (状态引用) => {
        const body = 消息体获取();
        if (!状态引用.el && body) {
            状态引用.el = document.createElement('div');
            状态引用.el.className = 'nca-tool-executing';
            body.appendChild(状态引用.el);
        } else if (状态引用.el && body && 状态引用.el.parentNode !== body) {
            状态引用.el.remove();
            状态引用.el = null;
        }
        return 状态引用.el;
    };

    return function 处理(data, 状态引用) {
        // ── 上下文健康度指标：i18n Toast + 统一广播（三界面一致）──
        if (data.type === 'context_health') {
            try { 事件总线.emit('context-health-updated', data); } catch (_) {}
            if (data.warning) {
                if (data.usage_percent >= 95) {
                    Toast.error(t('chat.context_full', { percent: data.usage_percent }));
                } else if (data.usage_percent >= 85) {
                    Toast.warning(t('chat.context_high', { percent: data.usage_percent }));
                }
            }
            return 'skip';
        }
        // ── 知识库检索状态：侧边栏常驻通知条，不渲染为聊天消息 ──
        if (data.type === 'kb_status') {
            if (data.status === 'degraded' && data.message) {
                显示知识库通知(data.message);
            }
            return 'skip';
        }
        // ── 思考状态消息（深度思考模型的 reasoning 阶段进度，不渲染为正文）──
        if (data.type === 'thinking') {
            const charMatch = (data.content || '').match(/\[思考中:\s*(\d+)/);
            const charCount = charMatch ? charMatch[1] : '0';
            const el指示 = 指示器就位(状态引用);
            if (el指示) {
                el指示.innerHTML = `<span class="tool-exec-icon">🤔</span> ${t('tool.thinking')} <code></code><span class="tool-exec-dots"></span>`;
                el指示.querySelector('code').textContent = t('tool.thinking_chars', { n: charCount });
            }
            事件总线.emit(事件.状态栏更新, t('tool.thinking'));
            if (typeof 滚动 === "function") 滚动();
            return 'skip';
        }
        // ── 工具执行状态消息 ──
        if (data.type === 'tool_executing') {
            噪音状态.工具已执行 = true;  // 标记进入工具循环，后续文本将被噪音过滤
            const toolMatch = (data.content || '').match(/\[正在执行:\s*(.+?)\.{3}\]/);
            const toolName = 工具名显示(toolMatch ? toolMatch[1] : t('tool.generic'));
            const el指示 = 指示器就位(状态引用);
            if (el指示) {
                el指示.innerHTML = `<span class="tool-exec-icon">⚙️</span> ${t('tool.executing')} <code></code><span class="tool-exec-dots"></span>`;
                el指示.querySelector('code').textContent = toolName;
            }
            事件总线.emit(事件.状态栏更新, t('tool.status_bar', { name: toolName }));
            if (typeof 滚动 === "function") 滚动();
            return 'skip';
        }
        // ── 收到正常文本：移除指示器、清洗标记、噪音折叠 ──
        if (data.content) {
            if (状态引用.el) {
                状态引用.el.remove();
                状态引用.el = null;
            }
            // 过滤混入正文的 [正在执行: xxx...] / [思考中: xxx...] / [自动继续执行...] 标记
            data.content = data.content.replace(/\n?\[正在执行:\s*.+?\.{3}\]\n?/g, '');
            data.content = data.content.replace(/\n?\[思考中:\s*[^\]]*\.{3}\]\n?/g, '');
            data.content = data.content.replace(/\n?\[自动继续执行[^\]]*\]\n?/g, '');
            // ── 工具循环噪音过滤：折叠模型在工具调用间的"内心独白" ──
            if (噪音状态.工具已执行) {
                const lines = data.content.split('\n');
                const 保留行 = [];
                for (const line of lines) {
                    const trimmed = line.trim();
                    if (!trimmed) { 保留行.push(line); continue; }
                    // 代码块标记 / 列表项 / 标题不过滤
                    if (/^```/.test(trimmed)) { 保留行.push(line); continue; }
                    if (/^[-*+]\s|^\d+[.)]\s/.test(trimmed)) { 保留行.push(line); continue; }
                    if (/^#{1,6}\s/.test(trimmed)) { 保留行.push(line); continue; }
                    // 匹配噪音模式 → 折叠（任何长度都检测，长行检测前40字符）
                    if (_噪音模式.test(trimmed) || (trimmed.length > 80 && _噪音模式.test(trimmed.slice(0, 40)))) {
                        噪音状态.噪音计数++;
                        // 每折叠 5 条噪音输出一次指示器（i18n 文案，双语生效）
                        if (噪音状态.噪音计数 % 5 === 1) {
                            保留行.push(`\n\n**${t('chat.auto_steps', { n: 噪音状态.噪音计数 })}**\n`);
                        }
                        continue;
                    }
                    保留行.push(line);
                }
                data.content = 保留行.join('\n');
            }
            事件总线.emit(事件.状态栏更新, t("chat.generating"));
        }
    };
}

// ═══════════════════════════════════════════════════════════════
// 无确认规划摘要（后端已静默注入上下文，前端仅展示；三界面统一）
// ═══════════════════════════════════════════════════════════════

export function 渲染规划摘要(消息体, planResult) {
    if (!planResult || !消息体 || !Array.isArray(planResult.plan_steps) || planResult.plan_steps.length === 0) return;
    const 复杂度文字 = { simple: "简单", moderate: "中等", complex: "复杂" };
    const 摘要面板 = el("details", { class: "nca-planning-summary" });
    摘要面板.appendChild(el("summary", {}, [
        el("span", { class: "nca-planning-summary-icon", text: "📋" }),
        el("span", { text: `执行计划（${复杂度文字[planResult.complexity] || planResult.complexity || "中等"}）` }),
    ]));
    const 步骤列表 = el("ol", { class: "nca-planning-summary-steps" });
    planResult.plan_steps.forEach((s) => 步骤列表.appendChild(el("li", { text: String(s) })));
    摘要面板.appendChild(步骤列表);
    if (Array.isArray(planResult.risk_notes) && planResult.risk_notes.length > 0) {
        const 风险区 = el("div", { class: "nca-planning-summary-risks" });
        planResult.risk_notes.forEach((r) => 风险区.appendChild(el("div", { text: `⚠ ${String(r)}` })));
        摘要面板.appendChild(风险区);
    }
    消息体.insertBefore(摘要面板, 消息体.firstChild);
}

// ═══════════════════════════════════════════════════════════════
// 编辑重生成（三界面共用：行内编辑 → 截断本地历史 → 携 truncate_at 重发）
// ═══════════════════════════════════════════════════════════════

/**
 * @param {Object} 适配器 - 视图适配器（同 发送会话消息）
 * @param {HTMLElement} msgEl - 被编辑的用户消息气泡
 * @param {string} 原始内容
 * @param {number} index - 消息索引（本地索引，服务端绝对索引在发送管线内换算）
 */
export function 进入编辑模式(适配器, msgEl, 原始内容, index) {
    if (_发送锁 || 状态.正在发送) return; // 流式生成中禁止编辑

    创建行内编辑器(msgEl, 原始内容, {
        onCancel: () => 绑定代码块复制按钮(msgEl),
        onConfirm: async (newContent) => {
            if (_发送锁 || 状态.正在发送) return;
            msgEl.classList.remove("nca-msg-editing");

            // 截断本地消息列表：移除被编辑消息及之后所有内容
            const 列表 = 适配器.消息列表获取?.();
            if (Array.isArray(列表)) 列表.length = index;

            // DOM 截断：虚拟滚动就地裁剪由适配器钩子处理，否则就地移除后续气泡
            if (typeof 适配器.截断渲染 === "function" && 适配器.截断渲染(index, msgEl) === true) {
                // 适配器已处理
            } else {
                const allMsgs = Array.from(适配器.消息区域.querySelectorAll('.nca-msg'));
                const currentIdx = allMsgs.indexOf(msgEl);
                if (currentIdx >= 0) {
                    for (let i = allMsgs.length - 1; i >= currentIdx; i--) {
                        allMsgs[i].remove();
                    }
                }
            }

            // 触发流式重新生成：携带 truncate_at，由后端裁剪服务端历史
            await 发送会话消息(适配器, { content: newContent, truncate_at: index });
        },
    });
}

// ═══════════════════════════════════════════════════════════════
// 统一发送管线（唯一入口；三界面 shim 组装视图适配器后委托本函数）
// ═══════════════════════════════════════════════════════════════

/**
 * @param {Object} 适配器 - 视图适配器
 * @param {HTMLElement} 适配器.消息区域 - 消息列表容器（兼滚动容器）
 * @param {HTMLTextAreaElement} 适配器.输入框
 * @param {HTMLButtonElement} 适配器.发送按钮
 * @param {string} 适配器.activeTab - 'develop' | 'optimize' | 'visualize'
 * @param {Function} 适配器.附件获取 - () => Array（从界面附件来源取待发送附件）
 * @param {Function} [适配器.发送前校验] - async (text, attachments) => bool，false 拦截（含 Toast 提示）
 * @param {Function} 适配器.获取会话id - async () => string|null（面板 shim 内含自动匹配/创建）
 * @param {Function} [适配器.获取插件目录] - () => string
 * @param {Function} [适配器.发送后清理] - 清空输入框/附件（仅非"来自编辑"时调用）
 * @param {Function} [适配器.消息列表获取] - () => Array，本地消息列表引用
 * @param {Function} [适配器.起始偏移获取] - () => number，服务端分页起始偏移（truncate_at 换算）
 * @param {Function} [适配器.用户气泡追加] - (bubble, msg) => void（默认 appendChild；虚拟滚动界面覆写）
 * @param {Function} [适配器.会话校验] - (sessionId) => bool（完成回调守卫：流式期间会话已切换则丢弃）
 * @param {Function} [适配器.完成后处理] - ({ aiBubble, 结果, 内容 }) => void（虚拟滚动接管等界面专属收尾）
 * @param {Function} [适配器.截断渲染] - (index, msgEl) => bool（编辑重生成的虚拟滚动就地裁剪）
 * @param {Object} [options]
 * @param {string} [options.content] - 存在即"来自编辑/重发"：不读输入框、不清空、用户气泡可选跳过
 * @param {Array} [options.附件] - 重发场景附件透传（规划确认重发时输入框已清空）
 * @param {number} [options.truncate_at] - 编辑重生成的本地截断索引
 * @param {boolean} [options.跳过用户消息] - 规划确认重发：不重复渲染用户气泡
 * @param {boolean} [options.skip_planning] - 规划确认重发：跳过服务端规划环节
 * @param {Object} [options.user_choices] - 用户在规划面板的决策
 */
export async function 发送会话消息(适配器, options = {}) {
    // 并发保护：三界面共用单一发送锁
    if (_发送锁 || 状态.正在发送) return;

    const 来自编辑 = typeof options.content === "string" && options.content.length > 0;
    const text = 来自编辑 ? options.content : 适配器.输入框.value.trim();
    const attachments = 来自编辑
        ? (Array.isArray(options.附件) ? options.附件 : [])
        : (适配器.附件获取 ? 适配器.附件获取() : []);
    const truncate_at = options.truncate_at;
    const 跳过用户消息 = options.跳过用户消息 === true;
    const skip_planning = options.skip_planning === true;
    const user_choices = options.user_choices;
    if (!text && attachments.length === 0) return;

    // 界面前置校验（主聊天：本地模型/会话必选；面板：插件目录必选）
    if (typeof 适配器.发送前校验 === "function") {
        const 通过 = await 适配器.发送前校验(text, attachments);
        if (通过 === false) return;
    }

    // 会话解析（面板 shim 在此自动匹配/创建会话）
    const sessionId = await 适配器.获取会话id();
    if (!sessionId) return;

    // 清空输入框与附件（编辑/重发路径跳过）
    if (!来自编辑 && typeof 适配器.发送后清理 === "function") 适配器.发送后清理();

    // 清除欢迎页残留：全程用 appendChild 追加，不清掉会残留在会话消息上方
    if (适配器.消息区域.querySelector(".nca-welcome")) {
        适配器.消息区域.innerHTML = "";
    }

    // 1. 渲染用户消息气泡（规划确认重发时跳过：用户消息已在首轮显示）
    if (!跳过用户消息) {
        const 用户消息 = { role: "user", content: text, timestamp: Date.now(), attachments: attachments.length > 0 ? attachments : undefined };
        const 列表 = 适配器.消息列表获取?.();
        if (Array.isArray(列表)) 列表.push(用户消息);
        const 索引 = Array.isArray(列表) ? 列表.length - 1 : 适配器.消息区域.querySelectorAll('.nca-msg').length;
        const userBubble = 创建消息DOM(用户消息, 索引, {
            编辑点击: (host, idx) => 进入编辑模式(适配器, host, host._原始内容 ?? text, idx),
        });
        // 编辑重生成气泡：索引用 truncate_at（服务端绝对索引换算基准）
        if (来自编辑 && typeof truncate_at === "number") userBubble._消息索引 = truncate_at;
        if (typeof 适配器.用户气泡追加 === "function") 适配器.用户气泡追加(userBubble, 用户消息);
        else 适配器.消息区域.appendChild(userBubble);
    }

    // 2. 创建空的 AI 回复气泡（带光标闪烁动画）
    const aiBubble = el("div", { class: "nca-msg assistant" }, [
        el("div", { class: "nca-msg-header" }, [
            el("span", { class: "msg-role", text: "─ AI" }),
        ]),
        el("div", { class: "nca-msg-body" }, [
            el("span", { class: "nca-streaming-cursor" }),
        ]),
    ]);
    适配器.消息区域.appendChild(aiBubble);
    const aiBody = aiBubble.querySelector(".nca-msg-body");
    适配器.消息区域.scrollTop = 适配器.消息区域.scrollHeight;

    // 3. 全局流式状态 + 事件广播（供流式恢复/状态栏/会话切换中止等机制读取）
    _发送锁 = true;
    状态.正在发送 = true;
    状态.流式状态.活跃 = true;
    状态.流式状态.累积内容 = "";
    状态.流式状态.消息体引用 = aiBody;
    状态.流式状态.消息容器引用 = aiBubble;
    状态.流式状态.滚动容器引用 = 适配器.消息区域;
    状态.流式状态.工具指示器引用 = null;
    事件总线.emit(事件.流式状态变更, true);
    事件总线.emit(事件.发送状态变更, true);
    事件总线.emit(事件.连接状态变更, "busy");
    事件总线.emit(事件.状态栏更新, t("chat.generating"));
    适配器.输入框.disabled = true;
    适配器.发送按钮.disabled = true;

    // 4. 停止按钮（插入到输入区域前面）
    const abortCtrl = new AbortController();
    状态.流式状态.中止控制器 = abortCtrl;
    let _活跃检查定时器 = null;
    const stopBtn = el("button", { class: "nca-stop-btn", text: "■ " + t("chat.stop") });
    stopBtn.addEventListener("click", () => {
        // 手动停止：顺便清理活跃定时器，避免 abort 后定时器空转重复 abort
        if (_活跃检查定时器) { clearInterval(_活跃检查定时器); _活跃检查定时器 = null; }
        abortCtrl.abort();
    });
    状态.流式状态.停止按钮引用 = stopBtn;
    const inputArea = 适配器.输入框.closest(".nca-input-area");
    if (inputArea) inputArea.insertBefore(stopBtn, inputArea.firstChild);

    // 5. 滑动活跃超时看门狗（三界面统一）：任何 SSE 事件刷新活跃时间，
    //    仅当持续无数据超过 后端超时+5s 才自动中止，防止长工具循环被误杀
    let _最后活跃时间 = Date.now();
    let _基础超时 = Number(状态.设置?.chat_timeout ?? 120000);
    if (!Number.isFinite(_基础超时) || _基础超时 <= 0) _基础超时 = 120000;
    const _活跃超时阈值 = _基础超时 + 5000;
    _活跃检查定时器 = setInterval(() => {
        if (Date.now() - _最后活跃时间 > _活跃超时阈值) {
            clearInterval(_活跃检查定时器);
            _活跃检查定时器 = null;
            abortCtrl.abort();
        }
    }, 5000);

    // 6. 构建请求体（三界面字段完全一致）
    // model_source 多源读取：全局状态 → 设置对象 → 默认值 'api'
    const _modelSource = 状态.模型来源 || 状态.设置?.model_source || 'api';
    const 请求体对象 = {
        message: text,
        session_id: sessionId,
        plugin_context: 适配器.获取插件目录 ? 适配器.获取插件目录() : "",
        model_source: _modelSource,
        local_model_name: 状态.选中本地模型 || 状态.设置?.local_model_name || "",
        activeTab: 适配器.activeTab,
        language: 获取当前语言(),
    };
    if (attachments.length > 0) 请求体对象.attachments = attachments;
    // 编辑重生成：本地索引叠加起始偏移换算为服务端绝对索引（首屏仅加载最近一窗消息）
    if (truncate_at !== undefined && truncate_at !== null) {
        请求体对象.truncate_at = truncate_at + (适配器.起始偏移获取 ? 适配器.起始偏移获取() : 0);
    }
    // 规划确认重发：跳过服务端规划环节，并携带用户在规划面板的决策
    if (skip_planning) 请求体对象.skip_planning = true;
    if (user_choices) 请求体对象.user_choices = user_choices;
    // 保存请求体到流式状态，供界面重建时引用
    状态.流式状态.请求体 = 请求体对象;

    // 7. 统一 SSE 处理与完成分支路由
    const SSE处理 = 创建SSE事件处理器({
        消息体获取: () => 状态.流式状态.消息体引用,
        滚动: () => {
            requestAnimationFrame(() => {
                适配器.消息区域.scrollTop = 适配器.消息区域.scrollHeight;
            });
        },
    });
    let hasError = false;

    try {
        await 创建流式聊天({
            消息容器: aiBubble,
            消息体: aiBody,
            滚动容器: 适配器.消息区域,
            // 动态引用获取：界面重建后由 创建流式恢复气泡 更新全局流式状态，SSE 续写新 DOM
            获取消息容器: () => 状态.流式状态.消息容器引用,
            获取消息体: () => 状态.流式状态.消息体引用,
            获取滚动容器: () => 状态.流式状态.滚动容器引用,
            请求体: 请求体对象,
            中止信号: abortCtrl.signal,
            内容更新回调: (内容) => { 状态.流式状态.累积内容 = 内容; },
            数据处理: (data, 状态引用) => {
                _最后活跃时间 = Date.now(); // 滑动活跃超时：任何事件到达都刷新
                return SSE处理(data, 状态引用);
            },
            完成回调: (结果) => {
                // 会话守卫：流式期间用户已切换会话，迟到的完成回调不得渲染进新会话
                if (typeof 适配器.会话校验 === "function" && !适配器.会话校验(sessionId)) return;
                const cbBody = 状态.流式状态.消息体引用;
                const cbContainer = 状态.流式状态.消息容器引用;
                // 重发闭包：ask_user 作答与规划确认均通过它发起新一轮（同一适配器）
                const 再发送 = (opts) => 发送会话消息(适配器, opts);
                const 滚动到底 = () => requestAnimationFrame(() => {
                    const sc = 状态.流式状态.滚动容器引用 || 适配器.消息区域;
                    if (sc) sc.scrollTop = sc.scrollHeight;
                });

                // ── ask_user 面板分支：模型向用户提问，停下等待用户真实回复 ──
                // 后端在工具循环中拦截 ask_user 并推送事件结束流；用户点击选项或
                // 在输入框回答后，回复作为普通新消息进入下一轮
                if (结果.type === "ask_user" && 结果.question) {
                    if (cbBody) {
                        // 保留提问前已产出的内容（如有），末尾追加提问面板（共享实现）
                        cbBody.innerHTML = 结果.fullContent ? 简易Markdown渲染(结果.fullContent) : "";
                        cbBody.appendChild(创建提问面板(结果, (opt) => {
                            // 点击选项 = 以该选项为用户回复直接发起新一轮
                            再发送({ content: opt });
                        }));
                        绑定代码块复制按钮(cbContainer);
                    }
                    滚动到底();
                    return; // 不执行后续的普通消息渲染
                }

                // ── 规划面板分支：需用户确认执行计划 ──
                // 流式管理器在收到 planning_done 且 needs_confirmation 时提前结束并回传
                // { type: "planning", planResult }；此时渲染规划面板而非普通 Markdown 消息
                if (结果.type === "planning" && 结果.planResult) {
                    if (cbBody) cbBody.innerHTML = ""; // 清除流式光标
                    import("./任务规划面板.js").then(({ 渲染规划面板 }) => {
                        渲染规划面板(cbBody, 结果.planResult, {
                            会话id: sessionId,
                            原始请求体: 请求体对象,
                            // 确认：携带 skip_planning + user_choices 重新发起流式（新建 AI 气泡）
                            确认后回调: (userChoices) => {
                                再发送({
                                    content: text,
                                    附件: 请求体对象.attachments,
                                    跳过用户消息: true,
                                    skip_planning: true,
                                    user_choices: userChoices,
                                });
                            },
                            // 跳过：仅携带 skip_planning（无 user_choices）直接执行
                            跳过回调: () => {
                                再发送({
                                    content: text,
                                    附件: 请求体对象.attachments,
                                    跳过用户消息: true,
                                    skip_planning: true,
                                });
                            },
                        });
                        滚动到底();
                    }).catch((e) => {
                        console.warn("[节点梦工厂] 规划面板加载失败:", e);
                        if (cbBody) cbBody.innerHTML = 简易Markdown渲染(结果.fullContent || "（规划面板加载失败）");
                        if (cbContainer) 绑定代码块复制按钮(cbContainer);
                    });
                    滚动到底();
                    return; // 不执行后续的普通消息渲染
                }

                // ── 普通完成：最终渲染 ──
                const { fullContent, hasError: err, planResult } = 结果;
                hasError = err;
                if (err && !fullContent) {
                    // 网络错误且无内容：错误气泡（不记录消息列表，避免历史残留"无回复"）
                    if (cbBody) cbBody.textContent = `❌ ${t('common.network_error')}`;
                    if (cbContainer) {
                        cbContainer.classList.remove('assistant');
                        cbContainer.classList.add('system');
                    }
                    return;
                }
                const 内容 = fullContent || t('chat.no_reply');
                if (cbBody) cbBody.innerHTML = 简易Markdown渲染(内容);
                // DOMPurify 未就绪：标记待消毒并缓存原文，就绪后由统一监听器重渲染
                if (!window.DOMPurify && cbBody && cbContainer) {
                    cbBody.dataset.pendingSanitize = "true";
                    cbContainer._pendingContent = 内容;
                }
                if (cbContainer) 绑定代码块复制按钮(cbContainer);
                // 无 questions 的规划摘要（后端已静默注入上下文，前端仅展示）
                渲染规划摘要(cbBody, planResult);
                // 记录到本地消息列表（三界面统一维护，编辑索引因此保持一致）
                const 列表 = 适配器.消息列表获取?.();
                if (Array.isArray(列表)) 列表.push({ role: "assistant", content: 内容, timestamp: Date.now() });
                // 界面专属收尾（主聊天：虚拟滚动接管；面板：无）
                if (typeof 适配器.完成后处理 === "function") 适配器.完成后处理({ aiBubble, 结果, 内容 });
            },
        });
    } finally {
        // 8. 状态恢复与清理（正常结束/异常/手动停止均在此，避免定时器与锁泄漏）
        if (_活跃检查定时器) { clearInterval(_活跃检查定时器); _活跃检查定时器 = null; }
        _发送锁 = false;
        状态.正在发送 = false;
        状态.流式状态.活跃 = false;
        状态.流式状态.累积内容 = "";
        状态.流式状态.中止控制器 = null;
        状态.流式状态.消息体引用 = null;
        状态.流式状态.消息容器引用 = null;
        状态.流式状态.滚动容器引用 = null;
        状态.流式状态.工具指示器引用 = null;
        事件总线.emit(事件.流式状态变更, false);
        适配器.输入框.disabled = false;
        适配器.发送按钮.disabled = false;
        事件总线.emit(事件.发送状态变更, false);
        事件总线.emit(事件.连接状态变更, hasError ? "error" : "online");
        事件总线.emit(事件.状态栏更新, hasError ? t('common.error') : t('common.ready'));

        // 移除停止按钮：界面重建后恢复流程会创建新按钮并禁用新输入区，
        // 闭包中的 stopBtn 已随旧 DOM 失效，须通过全局流式状态引用一并清理
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

        requestAnimationFrame(() => {
            适配器.消息区域.scrollTop = 适配器.消息区域.scrollHeight;
        });
    }
}
