// ═══════════════════════════════════════════════════════════════
// 删除确认对话框.js — 会话删除确认（含二次确认）
// NodeCraft AI — Luxury Terminal / Neo-Noir Hacker
// ═══════════════════════════════════════════════════════════════

import { el, Toast } from "./工具函数.js";
import { t } from "./i18n.js";

// ═══════════════════════════════════════════════════════════════
// 删除确认对话框
// ═══════════════════════════════════════════════════════════════

export function 显示删除确认(rootContainer, session, 删除会话Fn) {
    const overlay = el("div", { class: "nca-overlay center-modal" });
    const modal = el("div", { class: "nca-modal" });

    const closeBtn = el("button", { class: "nca-icon-btn", text: "✕" });
    closeBtn.addEventListener("click", () => overlay.remove());

    modal.appendChild(el("div", { class: "nca-modal-header" }, [
        el("h3", { text: `⚠ ${t("common.confirm")}` }),
        closeBtn,
    ]));

    const body = el("div", { class: "nca-modal-body" });
    modal.appendChild(body);
    overlay.appendChild(modal);
    overlay.addEventListener("click", (e) => { if (e.target === overlay) overlay.remove(); });
    rootContainer.appendChild(overlay);

    const hasFolder = !!(session && session.plugin_folder);

    // 主视图：根据是否有 plugin_folder 提供两或三个选项
    const 渲染主视图 = () => {
        body.innerHTML = "";
        body.appendChild(el("p", {
            text: t("session.confirm_delete", { title: session.title || t("session.untitled") }),
            style: { margin: "0", fontSize: "12px", color: "var(--nca-fg-dim)", lineHeight: "1.6" },
        }));

        const actions = el("div", { class: "nca-confirm-actions" });

        const cancelBtn = el("button", { class: "nca-btn nca-btn-sm", text: t("common.cancel") });
        cancelBtn.addEventListener("click", () => overlay.remove());

        const deleteOnlyBtn = el("button", { class: "nca-btn nca-btn-sm", text: t("session.delete_only") });
        deleteOnlyBtn.addEventListener("click", async () => {
            deleteOnlyBtn.textContent = "...";
            deleteOnlyBtn.disabled = true;
            await 删除会话Fn(session.id, false);
            overlay.remove();
            Toast.success(t("session.deleted"));
        });

        actions.appendChild(cancelBtn);
        actions.appendChild(deleteOnlyBtn);

        if (hasFolder) {
            const deleteWithFolderBtn = el("button", { class: "nca-btn nca-btn-sm nca-btn-danger", text: t("session.delete_with_folder") });
            deleteWithFolderBtn.addEventListener("click", () => 渲染二次确认());
            actions.appendChild(deleteWithFolderBtn);
        }

        body.appendChild(actions);
    };

    // 二次确认视图：同时删除文件夹的危险提示
    const 渲染二次确认 = () => {
        body.innerHTML = "";
        body.appendChild(el("p", {
            text: t("session.confirm_delete_folder", { folder: session.plugin_folder }),
            style: { margin: "0", fontSize: "12px", color: "var(--nca-danger, #ff5b5b)", lineHeight: "1.6", fontWeight: "600" },
        }));

        const actions = el("div", { class: "nca-confirm-actions" });

        const backBtn = el("button", { class: "nca-btn nca-btn-sm", text: t("session.delete_back") });
        backBtn.addEventListener("click", () => 渲染主视图());

        const confirmBtn = el("button", { class: "nca-btn nca-btn-sm nca-btn-danger", text: t("session.delete_confirm_btn") });
        confirmBtn.addEventListener("click", async () => {
            confirmBtn.textContent = "...";
            confirmBtn.disabled = true;
            backBtn.disabled = true;
            await 删除会话Fn(session.id, true);
            overlay.remove();
            Toast.success(t("session.deleted_with_folder"));
        });

        actions.appendChild(backBtn);
        actions.appendChild(confirmBtn);
        body.appendChild(actions);
    };

    渲染主视图();
}
