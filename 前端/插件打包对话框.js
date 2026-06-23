// ═══════════════════════════════════════════════════════════════
// 插件打包对话框.js — 打包当前插件为 zip
// NodeCraft AI — Luxury Terminal / Neo-Noir Hacker
// ═══════════════════════════════════════════════════════════════

import { NCA_STORAGE_KEYS, Toast } from "./工具函数.js";

/**
 * 显示“插件打包导出”对话框
 * @param {HTMLElement} rootContainer - 根容器引用（保留参数以兼容调用方）
 */
export async function 显示打包对话框(rootContainer) {
    const selectedPlugin = localStorage.getItem(NCA_STORAGE_KEYS.plugin);
    if (!selectedPlugin) {
        Toast.warning("请先选择要打包的项目");
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
    header.innerHTML = `<span>📦 插件打包导出</span>`;
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
    projectInfo.innerHTML = `<label>当前项目</label><span class="nc-sync-value">📁 ${selectedPlugin}</span>`;
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
    readmeLabel.textContent = " 生成 README.md（仅在不存在时临时生成）";
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
    pyprojectLabel.textContent = " 生成 pyproject.toml（ComfyUI 注册表格式）";
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
    cancelBtn.textContent = "取消";
    cancelBtn.addEventListener("click", () => overlay.remove());

    const confirmBtn = document.createElement("button");
    confirmBtn.className = "nc-sync-btn-confirm";
    confirmBtn.textContent = "开始打包";

    btnGroup.appendChild(cancelBtn);
    btnGroup.appendChild(confirmBtn);
    body.appendChild(btnGroup);

    modal.appendChild(body);
    overlay.appendChild(modal);

    overlay.addEventListener("click", (e) => {
        if (e.target === overlay) overlay.remove();
    });

    document.body.appendChild(overlay);

    // === 确认按钮：执行打包 ===
    confirmBtn.addEventListener("click", async () => {
        confirmBtn.disabled = true;
        cancelBtn.disabled = true;
        confirmBtn.textContent = "打包中...";
        progressArea.style.display = "block";
        progressArea.textContent = "正在打包插件...";
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
                progressArea.innerHTML = `✓ ${info.message || "打包完成"}<br>文件大小: ${info.size_mb} MB<br><a href="${downloadUrl}" download style="color: #00d4ff; text-decoration: underline; display: inline-block; margin-top: 6px;">⬇ 下载 ${info.filename}</a>`;
                progressArea.style.color = "#00ffc8";
                confirmBtn.textContent = "✓ 完成";
                const autoCloseTimer = setTimeout(() => overlay.remove(), 3000);
                overlay.addEventListener("click", () => clearTimeout(autoCloseTimer), { once: true });
            } else {
                progressArea.textContent = "✗ " + (data.message || data.error || "打包失败");
                progressArea.style.color = "#ff4444";
                confirmBtn.disabled = false;
                cancelBtn.disabled = false;
                confirmBtn.textContent = "重试";
            }
        } catch (e) {
            progressArea.textContent = "✗ 网络错误: " + e.message;
            progressArea.style.color = "#ff4444";
            confirmBtn.disabled = false;
            cancelBtn.disabled = false;
            confirmBtn.textContent = "重试";
        }
    });
}
