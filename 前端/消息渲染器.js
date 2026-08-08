// ═══════════════════════════════════════════════════════════════
// 消息渲染器.js — 消息气泡渲染、输入区构建、流式响应（模块入口）
// NodeCraft AI — Luxury Terminal / Neo-Noir Hacker
//
// 已按职责拆分为子模块（对外导出符号保持不变，由本入口 re-export）：
//   消息渲染/虚拟滚动管理器.js — 消息列表虚拟滚动
//   消息渲染/代码复制.js       — 代码块复制委托 + Prism 高亮触发
//   消息渲染/欢迎页.js         — 欢迎页与快捷操作
//   消息渲染/附件管理.js       — 文件附件选择、预览、移除
//
// 会话管线（发送/SSE处理/完成分支/编辑重生成）已统一至 会话核心.js，
// 本入口通过 _主适配器 声明主聊天独有能力后委托 发送会话消息。
// ═══════════════════════════════════════════════════════════════

import {
    el, NCA_STORAGE_KEYS, Toast, 移除视觉能力警告, 简易Markdown渲染,
} from "./工具函数.js";
import {
    状态, 加载更早消息,
} from "./交互与状态.js";
import { t } from "./i18n.js";
import {
    创建消息DOM as _核心创建消息DOM, 进入编辑模式, 发送会话消息,
} from "./会话核心.js";
import { 虚拟滚动配置, 虚拟滚动管理器 } from "./消息渲染/虚拟滚动管理器.js";
import { 绑定代码块复制按钮 } from "./消息渲染/代码复制.js";
import { 渲染欢迎页 } from "./消息渲染/欢迎页.js";
import { 处理文件选择, 渲染附件预览, 更新发送按钮状态 } from "./消息渲染/附件管理.js";

// ─── Re-export 拆分子模块的原有导出，外部导入路径与符号保持完全不变 ──
export { 绑定代码块复制按钮 } from "./消息渲染/代码复制.js";
export { 渲染欢迎页 } from "./消息渲染/欢迎页.js";

// ─── 模块级 refs/根容器引用 ────────────────────────────────────
// 消息内编辑功能（hover 用户消息→编辑→重新生成）需要在按钮点击回调里访问
// 当前活动 refs，但创建 DOM 时调用方未必传入；用模块级变量在
// 渲染入口（渲染所有消息 / 渲染输入区域）处缓存，避免给每个消息节点再绑一份引用。
let _当前refs = null;
let _当前rootContainer = null;

// DOMPurify 待消毒重渲染已迁入 会话核心.js（document 级扫描，三界面统一生效）

// ─── 主聊天视图适配器 ────────────────────────────────────────
// 会话管线共用 会话核心.js；本模块仅声明主聊天独有能力：
// 虚拟滚动 / 会话守卫 / 本地模型与会话前置校验 / 流式恢复相关的全局流式状态
function _主适配器(refs) {
    return {
        消息区域: refs.消息区域,
        输入框: refs.输入框,
        发送按钮: refs.发送按钮,
        activeTab: localStorage.getItem(NCA_STORAGE_KEYS.activeTab) || "develop",
        附件获取: () => [...状态.待发送附件],
        发送前校验: () => {
            // 本地模式下验证模型已选择
            if (状态.模型来源 === "local" && !状态.选中本地模型) {
                Toast.warning("请先选择一个本地模型");
                return false;
            }
            // 必须存在会话ID，否则禁止发送
            if (!状态.当前会话ID) {
                Toast.warning("请先创建或选择一个会话");
                return false;
            }
            return true;
        },
        获取会话id: () => 状态.当前会话ID,
        获取插件目录: () => localStorage.getItem(NCA_STORAGE_KEYS.plugin),
        发送后清理: () => {
            refs.输入框.value = "";
            refs.输入框.style.height = "auto";
            refs.发送按钮.classList.remove("active");
            状态.待发送附件 = [];
            渲染附件预览(refs);
            // 发送后移除视觉警告
            const _inputArea = refs.输入框?.closest('.nca-input-area');
            if (_inputArea) 移除视觉能力警告(_inputArea);
        },
        消息列表获取: () => 状态.当前消息列表,
        起始偏移获取: () => 状态.消息起始偏移 || 0,
        // 会话守卫：流式期间用户已切换会话，迟到的完成回调不得渲染进新会话
        会话校验: (sid) => sid === 状态.当前会话ID,
        用户气泡追加: (气泡) => {
            // 已启用虚拟滚动：数据已 push 到列表，交由滚动器渲染
            if (refs._虚拟滚动) {
                refs._虚拟滚动.追加新消息();
            } else {
                refs.消息区域.appendChild(气泡);
                绑定代码块复制按钮(气泡);
                // 累计消息达阈值时升级为虚拟滚动（用户无感知）
                _尝试启用虚拟滚动(refs);
            }
            滚动到底部(refs);
        },
        截断渲染: (index) => {
            // 编辑重生成：虚拟滚动就地裁剪后续消息
            if (refs._虚拟滚动 && typeof refs._虚拟滚动.截断消息 === "function") {
                refs._虚拟滚动.截断消息(index);
                return true;
            }
            return false;
        },
        完成后处理: ({ aiBubble }) => {
            // 虚拟滚动启用时：移除流式临时气泡，让滚动器接管渲染
            // 注意：界面重建后 refs 可能指向旧实例，此时跳过 DOM 操作
            if (refs._虚拟滚动 && refs._虚拟滚动.容器 === refs.消息区域) {
                if (aiBubble && aiBubble.parentNode) aiBubble.parentNode.removeChild(aiBubble);
                refs._虚拟滚动.追加新消息();
            } else if (refs._虚拟滚动) {
                // refs 已过时但虚拟滚动存在：仅追加消息数据，不操作旧 DOM
                refs._虚拟滚动.追加新消息();
            } else {
                _尝试启用虚拟滚动(refs);
            }
            滚动到底部(refs);
        },
    };
}

function _创建消息DOM(msg, index) {
    // 委托会话核心的统一气泡工厂；主聊天特有的索引解析（虚拟滚动索引优先）在编辑回调内处理
    return _核心创建消息DOM(msg, index, {
        编辑点击: (host, idx) => {
            // 优先用 DOM 上缓存的索引（虚拟滚动场景下索引会变化）
            const realIdx = (typeof host._虚拟索引 === "number")
                ? host._虚拟索引
                : (typeof host._消息索引 === "number" ? host._消息索引 : idx);
            _进入编辑模式(host, msg, realIdx);
        },
    });
}

// ─── 消息内编辑模式：委托会话核心（行内 textarea + 截断 + 重发）─────
function _进入编辑模式(msgEl, msg, index) {
    const refs = _当前refs;
    if (!refs) return;
    进入编辑模式(_主适配器(refs), msgEl, msg.content, index);
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

export function 渲染所有消息(refs, messages, options = {}) {
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
    // 强制到底仅限「切换/进入会话」一次性场景（options.强制到底）：
    // 此时容器刚被清空 scrollTop=0，非强制的"阅读保护"守卫会误判为用户在翻历史；
    // 前插更早消息等会话内重渲染不传该标记，用户停在顶部翻看时不会被拽回底部。
    const 到底 = !!options.强制到底;
    // 大消息列表：启用虚拟滚动
    if (messages.length >= 虚拟滚动配置.启用阈值) {
        refs._虚拟滚动 = new 虚拟滚动管理器(
            refs.消息区域, messages, _创建消息DOM, 绑定代码块复制按钮,
        );
        _挂载加载更早提示条(refs);
        滚动到底部(refs, 到底);
        return;
    }
    // 小消息列表：保持原有渲染逻辑（传入索引以支持编辑）
    messages.forEach((msg, index) => 追加消息DOM(refs, msg, index));
    _挂载加载更早提示条(refs);
    滚动到底部(refs, 到底);
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
// 流式消息发送 — 薄壳 shim：委托会话核心的统一发送管线
// ═══════════════════════════════════════════════════════════════

export async function 发送消息流式(refs, rootContainer, options = {}) {
    // 缓存最新 refs：编辑重生成/规划面板回调经由模块级引用组装适配器
    _当前refs = refs;
    _当前rootContainer = rootContainer;
    return 发送会话消息(_主适配器(refs), options);
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
