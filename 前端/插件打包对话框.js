// ═══════════════════════════════════════════════════════════════
// 插件打包对话框.js — 打包当前插件为 zip
// NodeCraft AI — Luxury Terminal / Neo-Noir Hacker
// ═══════════════════════════════════════════════════════════════

import { NCA_STORAGE_KEYS, Toast } from "./工具函数.js";
import { t } from "./i18n.js";

/**
 * 显示“插件打包导出”对话框
 * @param {HTMLElement} rootContainer - 根容器引用（保留参数以兼容调用方）
 */
export async function 显示打包对话框(rootContainer) {
    const selectedPlugin = localStorage.getItem(NCA_STORAGE_KEYS.plugin);
    if (!selectedPlugin) {
        Toast.warning(t("package.select_project_first"));
        return;
    }

    // 创建 overlay 遮罩
    const overlay = document.createElement("div");
    overlay.className = "nc-sync-overlay";

    // 创建 modal 容器
    const modal = document.createElement("div");
    modal.className = "nc-sync-modal";

    // === Header ===
    const header = document.createElement("div");
    header.className = "nc-sync-header";
    header.innerHTML = `<span>${t("package.title")}</span>`;
    const closeBtn = document.createElement("button");
    closeBtn.className = "nc-sync-close";
    closeBtn.textContent = "✕";
    closeBtn.addEventListener("click", () => overlay.remove());
    header.appendChild(closeBtn);
    modal.appendChild(header);

    // === Body ===
    const body = document.createElement("div");
    body.className = "nc-sync-body";

    // 项目名显示
    const projectInfo = document.createElement("div");
    projectInfo.className = "nc-sync-field";
    projectInfo.innerHTML = `<label>${t("package.current_project")}</label><span class="nc-sync-value">📁 ${selectedPlugin}</span>`;
    body.appendChild(projectInfo);

    // 选项区域
    const optionsArea = document.createElement("div");
    optionsArea.style.cssText = "padding: 12px 0; display: flex; flex-direction: column; gap: 8px;";

    // 生成 README 复选框
    const readmeField = document.createElement("div");
    readmeField.className = "nc-sync-field nc-sync-checkbox-field";
    const readmeCheckbox = document.createElement("input");
    readmeCheckbox.type = "checkbox";
    readmeCheckbox.checked = true;
    readmeCheckbox.id = "nc-pkg-readme";
    const readmeLabel = document.createElement("label");
    readmeLabel.htmlFor = "nc-pkg-readme";
    readmeLabel.textContent = " " + t("package.gen_readme");
    readmeField.appendChild(readmeCheckbox);
    readmeField.appendChild(readmeLabel);
    optionsArea.appendChild(readmeField);

    // 生成 pyproject.toml 复选框
    const pyprojectField = document.createElement("div");
    pyprojectField.className = "nc-sync-field nc-sync-checkbox-field";
    const pyprojectCheckbox = document.createElement("input");
    pyprojectCheckbox.type = "checkbox";
    pyprojectCheckbox.checked = true;
    pyprojectCheckbox.id = "nc-pkg-pyproject";
    const pyprojectLabel = document.createElement("label");
    pyprojectLabel.htmlFor = "nc-pkg-pyproject";
    pyprojectLabel.textContent = " " + t("package.gen_pyproject");
    pyprojectField.appendChild(pyprojectCheckbox);
    pyprojectField.appendChild(pyprojectLabel);
    optionsArea.appendChild(pyprojectField);

    body.appendChild(optionsArea);

    // 进度/结果区域
    const progressArea = document.createElement("div");
    progressArea.className = "nc-sync-progress";
    progressArea.style.display = "none";
    body.appendChild(progressArea);

    // 操作按钮
    const btnGroup = document.createElement("div");
    btnGroup.className = "nc-sync-actions";

    const cancelBtn = document.createElement("button");
    cancelBtn.className = "nc-sync-btn-cancel";
    cancelBtn.textContent = t("common.cancel");
    cancelBtn.addEventListener("click", () => overlay.remove());

    const confirmBtn = document.createElement("button");
    confirmBtn.className = "nc-sync-btn-confirm";
    confirmBtn.textContent = t("package.start");

    btnGroup.appendChild(cancelBtn);
    btnGroup.appendChild(confirmBtn);
    body.appendChild(btnGroup);

    modal.appendChild(body);
    overlay.appendChild(modal);

    overlay.addEventListener("click", (e) => {
        if (e.target === overlay) overlay.remove();
    });

    rootContainer.appendChild(overlay);

    // === 确认按钮：执行打包 ===
    confirmBtn.addEventListener("click", async () => {
        confirmBtn.disabled = true;
        cancelBtn.disabled = true;
        confirmBtn.textContent = t("package.packaging");
        progressArea.style.display = "block";
        progressArea.textContent = t("package.packaging_progress");
        progressArea.style.color = "#00d4ff";

        try {
            const resp = await fetch("/ai-coder/package-plugin", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({
                    plugin_name: selectedPlugin,
                    options: {
                        generate_readme: readmeCheckbox.checked,
                        generate_pyproject: pyprojectCheckbox.checked
                    }
                })
            });
            const data = await resp.json();

            if (data.success) {
                const info = data.data || data;
                const downloadUrl = `/ai-coder/packages/download/${info.filename}`;
                progressArea.innerHTML = `✓ ${info.message || t("package.done")}<br>${t("package.file_size", { size: info.size_mb })}<br><a href="${downloadUrl}" download style="color: #00d4ff; text-decoration: underline; display: inline-block; margin-top: 6px;">${t("package.download", { filename: info.filename })}</a>`;
                progressArea.style.color = "#00ffc8";
                confirmBtn.textContent = t("package.completed");
                const autoCloseTimer = setTimeout(() => overlay.remove(), 3000);
                overlay.addEventListener("click", () => clearTimeout(autoCloseTimer), { once: true });
            } else {
                progressArea.textContent = "✗ " + (data.message || data.error || t("package.failed"));
                progressArea.style.color = "#ff4444";
                confirmBtn.disabled = false;
                cancelBtn.disabled = false;
                confirmBtn.textContent = t("common.retry");
            }
        } catch (e) {
            progressArea.textContent = t("package.network_error", { message: e.message });
            progressArea.style.color = "#ff4444";
            confirmBtn.disabled = false;
            cancelBtn.disabled = false;
            confirmBtn.textContent = t("common.retry");
        }
    });
}
