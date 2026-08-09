// ═══════════════════════════════════════════════════════════════
// 项目创建对话框.js — 创建新插件项目对话框
// NodeCraft AI — Luxury Terminal / Neo-Noir Hacker
// ═══════════════════════════════════════════════════════════════

import { el, Toast, NCA_STORAGE_KEYS } from "./工具函数.js";
import { 存储 } from "./存储引擎.js";
import { 创建插件文件夹, 创建会话 } from "./交互与状态.js";
import { t, 获取当前语言 } from "./i18n.js";

// ═══════════════════════════════════════════════════════════════
// 创建项目对话框
// ═══════════════════════════════════════════════════════════════

export function 显示创建项目对话框(rootContainer, refs) {
    const overlay = el("div", { class: "nca-overlay center-modal" });
    const modal = el("div", { class: "nca-modal" });

    const closeBtn = el("button", { class: "nca-icon-btn", text: "✕" });
    closeBtn.addEventListener("click", () => overlay.remove());

    modal.appendChild(el("div", { class: "nca-modal-header" }, [
        el("h3", { text: t("project.new") }),
        closeBtn,
    ]));

    const body = el("div", { class: "nca-modal-body" });
    const nameInput = el("input", { type: "text", placeholder: t("project.name_placeholder") });
    const preview = el("div", { class: "nca-create-preview", text: "ComfyUI/custom_nodes/..." });
    const errorMsg = el("div", { class: "nca-error-text", style: { display: "none" } });

    const validPattern = /^[a-zA-Z0-9_-]+$/;

    nameInput.addEventListener("input", () => {
        const val = nameInput.value;
        if (!val) {
            preview.textContent = "ComfyUI/custom_nodes/...";
            preview.classList.remove("valid");
            errorMsg.style.display = "none";
            nameInput.classList.remove("nca-input-error", "nca-input-valid");
        } else if (!validPattern.test(val)) {
            nameInput.classList.add("nca-input-error");
            nameInput.classList.remove("nca-input-valid");
            errorMsg.textContent = t("project.name_invalid");
            errorMsg.style.display = "block";
            preview.textContent = "";
            preview.classList.remove("valid");
        } else {
            nameInput.classList.remove("nca-input-error");
            nameInput.classList.add("nca-input-valid");
            errorMsg.style.display = "none";
            preview.textContent = `ComfyUI/custom_nodes/${val}/`;
            preview.classList.add("valid");
        }
    });

    body.appendChild(el("div", { class: "nca-field" }, [
        el("label", { text: t("project.name_label") }),
        nameInput,
        errorMsg,
    ]));
    body.appendChild(preview);

    const createBtn = el("button", { class: "nca-btn nca-btn-primary", text: t("common.create") });
    createBtn.addEventListener("click", async () => {
        const name = nameInput.value.trim();
        if (!name || !validPattern.test(name)) {
            nameInput.classList.add("nca-input-error");
            errorMsg.textContent = t("project.name_required");
            errorMsg.style.display = "block";
            return;
        }
        createBtn.textContent = t("common.creating");
        createBtn.disabled = true;

        const result = await 创建插件文件夹(name, 获取当前语言());
        if (result.success) {
            Toast.success(t("project.created").replace(/^✓\s*/, ''));
            overlay.remove();
            // 异步写入 IndexedDB（存储引擎内部同步刷新 localStorage 影子缓存）
            存储.写入(NCA_STORAGE_KEYS.plugin, name).catch(() => {});
            await 创建会话(name, name);
        } else {
            createBtn.textContent = t("common.create");
            createBtn.disabled = false;
            errorMsg.textContent = result.message || t("project.create_failed");
            errorMsg.style.display = "block";
        }
    });
    body.appendChild(createBtn);

    modal.appendChild(body);
    overlay.appendChild(modal);
    overlay.addEventListener("click", (e) => { if (e.target === overlay) overlay.remove(); });
    rootContainer.appendChild(overlay);

    nameInput.focus();
}
