// ═══════════════════════════════════════════════════════════════
// 知识库通知条.js — 云端知识库检索失败的侧边栏常驻通知条
// NodeCraft AI — Luxury Terminal / Neo-Noir Hacker
//
// 场景：当云端知识库检索失败（超时/不可用/错误）时，在侧边栏顶部
//       显示一条常驻警告条，让用户持续可见以便联系维护人员。
// 特性：
//   - 常驻：出现后一直保留，直到用户点 × 关闭，或新建/切换会话时清除
//   - 去重：已显示时不重复叠加，仅更新文案
//   - 文案由后端下发，前端只负责展示，绝不硬编码提醒内容
// ═══════════════════════════════════════════════════════════════

import { el } from "./工具函数.js";
import { 事件总线, 事件 } from "./交互与状态.js";

const 通知条ID = "nca-kb-notice-banner";

// 锚点：本插件自有根容器（承载主题变量，适配深浅色）
function 获取锚点() {
    return document.querySelector(".nca-sidebar-root");
}

/**
 * 显示（或更新）知识库失败常驻通知条
 * @param {string} message - 后端下发的中文提醒文案（前端直接展示，不做硬编码）
 */
export function 显示知识库通知(message) {
    if (!message) return;
    const 锚点 = 获取锚点();
    if (!锚点) return;

    // 去重：已存在则只更新文案，不重复叠加，只保留一条
    let banner = 锚点.querySelector("#" + 通知条ID);
    if (banner) {
        const textEl = banner.querySelector(".nca-kb-notice-text");
        if (textEl) textEl.textContent = message;
        return;
    }

    banner = el("div", { id: 通知条ID, class: "nca-kb-notice", role: "alert" }, [
        el("span", { class: "nca-kb-notice-icon", text: "⚠️" }),
        el("span", { class: "nca-kb-notice-text", text: message }),
        el("button", { class: "nca-kb-notice-close", title: "关闭", text: "×" }),
    ]);
    banner.querySelector(".nca-kb-notice-close").addEventListener("click", () => {
        清除知识库通知();
    });

    // 落点：标签栏之后、面板内容之上，作为侧边栏顶部的常驻条（跨标签页均可见）
    const tabBar = 锚点.querySelector(".nc-tab-bar");
    if (tabBar) {
        tabBar.insertAdjacentElement("afterend", banner);
    } else {
        锚点.insertBefore(banner, 锚点.firstChild);
    }
}

/** 清除知识库通知条 */
export function 清除知识库通知() {
    const banner = document.getElementById(通知条ID);
    if (banner) banner.remove();
}

// 新建 / 切换会话时自动清除（两种场景均会触发「会话切换」事件）
事件总线.on(事件.会话切换, () => 清除知识库通知());
