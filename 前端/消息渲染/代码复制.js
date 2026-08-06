// ═══════════════════════════════════════════════════════════════
// 消息渲染/代码复制.js — 代码块复制按钮（document 级事件委托）+ Prism 高亮触发
// NodeCraft AI — Luxury Terminal / Neo-Noir Hacker
// 从 消息渲染器.js 拆分：与消息状态无耦合的独立职责
// ═══════════════════════════════════════════════════════════════

import { 应用Prism高亮 } from "../工具函数.js";
import { t } from "../i18n.js";

// ── 代码块复制：document 级事件委托 ──
// 历史实现为每个复制按钮各挂一个 click 监听，消息重渲染/面板重建时反复累积；
// 改为 document 上单一委托监听（只安装一次，无需随 DOM 重建清理）。
let _复制委托已安装 = false;
function _安装代码复制委托() {
    if (_复制委托已安装) return;
    _复制委托已安装 = true;
    document.addEventListener("click", (e) => {
        const btn = e.target && e.target.closest ? e.target.closest(".nca-code-copy") : null;
        if (!btn) return;
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
}

export function 绑定代码块复制按钮(containerEl) {
    if (!containerEl) return;
    // 复制点击由 document 级委托统一处理，不再逐个按钮绑定
    _安装代码复制委托();
    // 应用 Prism 语法高亮（异步，不阻塞渲染）
    // 流式进行中跳过高亮（消息体带 nca-streaming-cursor 光标），
    // 流式结束后完成回调重新调用本函数（光标已移除）一次性高亮；
    // 非流式的历史消息无光标，高亮行为不变
    if (containerEl.querySelector(".nca-streaming-cursor")) return;
    应用Prism高亮(containerEl);
}
