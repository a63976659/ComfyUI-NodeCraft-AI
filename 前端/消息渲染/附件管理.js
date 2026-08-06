// ═══════════════════════════════════════════════════════════════
// 消息渲染/附件管理.js — 文件附件选择、预览、移除
// NodeCraft AI — Luxury Terminal / Neo-Noir Hacker
// 从 消息渲染器.js 拆分：输入区附件处理独立职责
// ═══════════════════════════════════════════════════════════════

import { el, Toast, 显示视觉能力警告, 移除视觉能力警告 } from "../工具函数.js";
import { 状态 } from "../交互与状态.js";
import { t } from "../i18n.js";

// ─── 文件附件处理 ────────────────────────────────────────────
const 允许的图片类型 = ["image/png", "image/jpeg", "image/gif", "image/webp"];
const 最大附件数 = 6;

export function 处理文件选择(e, refs) {
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

export function 渲染附件预览(refs) {
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

export function 更新发送按钮状态(refs) {
    const 有内容 = refs.输入框.value.trim().length > 0 || 状态.待发送附件.length > 0;
    refs.发送按钮.classList.toggle("active", 有内容);
}
