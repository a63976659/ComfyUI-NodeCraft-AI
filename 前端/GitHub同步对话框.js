// ═══════════════════════════════════════════════════════════════
// GitHub同步对话框.js — 同步当前插件到 GitHub 仓库
// NodeCraft AI — Luxury Terminal / Neo-Noir Hacker
// ──── 注意：`显示GitHub同步对话框` 当前无 UI 入口调用，调用链未接入 ────
// ═══════════════════════════════════════════════════════════════

import { NCA_STORAGE_KEYS, Toast, 安全存储读 } from "./工具函数.js";

/**
 * 显示“GitHub 同步上传”对话框
 * @param {HTMLElement} rootContainer - 根容器引用（保留参数以兼容调用方）
 */
export async function 显示GitHub同步对话框(rootContainer) {
    const selectedPlugin = 安全存储读(NCA_STORAGE_KEYS.plugin);
    if (!selectedPlugin) {
        Toast.warning("请先选择要同步的项目");
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
    header.innerHTML = `<span>⬆ GitHub 同步上传</span>`;
    const closeBtn = document.createElement("button");
    closeBtn.className = "nc-sync-close";
    closeBtn.textContent = "✕";
    closeBtn.addEventListener("click", () => { clearTimeout(checkTimer); overlay.remove(); });
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

    // 状态提示区
    const statusArea = document.createElement("div");
    statusArea.className = "nc-sync-status";
    statusArea.textContent = "正在检查仓库...";
    body.appendChild(statusArea);

    // 仓库名输入
    const repoField = document.createElement("div");
    repoField.className = "nc-sync-field";
    repoField.innerHTML = `<label>仓库名称</label>`;
    const repoInput = document.createElement("input");
    repoInput.type = "text";
    repoInput.className = "nc-sync-input";
    repoInput.placeholder = "仅允许英文、数字、下划线、点、连字符";
    repoInput.value = selectedPlugin;
    repoInput.addEventListener("input", () => {
        repoInput.value = repoInput.value.replace(/[^a-zA-Z0-9_.\-]/g, '');
    });
    repoField.appendChild(repoInput);
    body.appendChild(repoField);

    // .gitignore 选项
    const ignoreField = document.createElement("div");
    ignoreField.className = "nc-sync-field nc-sync-checkbox-field";
    const ignoreCheckbox = document.createElement("input");
    ignoreCheckbox.type = "checkbox";
    ignoreCheckbox.checked = true;
    ignoreCheckbox.id = "nc-sync-gitignore";
    const ignoreLabel = document.createElement("label");
    ignoreLabel.htmlFor = "nc-sync-gitignore";
    ignoreLabel.textContent = " 遵循 .gitignore（跳过 __pycache__、.git 等）";
    ignoreField.appendChild(ignoreCheckbox);
    ignoreField.appendChild(ignoreLabel);
    body.appendChild(ignoreField);

    // 进度区域
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
    cancelBtn.addEventListener("click", () => { clearTimeout(checkTimer); overlay.remove(); });

    const confirmBtn = document.createElement("button");
    confirmBtn.className = "nc-sync-btn-confirm";
    confirmBtn.textContent = "开始同步";

    btnGroup.appendChild(cancelBtn);
    btnGroup.appendChild(confirmBtn);
    body.appendChild(btnGroup);

    modal.appendChild(body);
    overlay.appendChild(modal);

    overlay.addEventListener("click", (e) => {
        if (e.target === overlay) { clearTimeout(checkTimer); overlay.remove(); }
    });

    let checkTimer = null;
    rootContainer.appendChild(overlay);

    // === 检查仓库是否存在 ===
    try {
        const checkResp = await fetch("/ai-coder/github-check-repo", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ repo_name: repoInput.value })
        });
        const checkData = await checkResp.json();

        if (checkData.status === "error") {
            statusArea.textContent = "⚠ " + checkData.error;
            statusArea.style.color = "#ff4444";
        } else if (checkData.exists) {
            statusArea.textContent = "✓ 检测到同名仓库，同步将更新已有内容";
            statusArea.style.color = "#00ffc8";
            confirmBtn.textContent = "确认同步更新";
        } else {
            statusArea.textContent = "ℹ 未检测到同名仓库，将创建新仓库";
            statusArea.style.color = "#00d4ff";
            confirmBtn.textContent = "创建并同步";
        }
    } catch (e) {
        statusArea.textContent = "⚠ 检查失败：" + e.message;
        statusArea.style.color = "#ff4444";
    }

    // 仓库名变化时重新检查（600ms debounce）
    repoInput.addEventListener("input", () => {
        clearTimeout(checkTimer);
        statusArea.textContent = "等待输入...";
        statusArea.style.color = "#888";
        checkTimer = setTimeout(async () => {
            if (!repoInput.value.trim()) return;
            statusArea.textContent = "正在检查仓库...";
            try {
                const resp = await fetch("/ai-coder/github-check-repo", {
                    method: "POST",
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify({ repo_name: repoInput.value.trim() })
                });
                const data = await resp.json();
                if (data.exists) {
                    statusArea.textContent = "✓ 检测到同名仓库，同步将更新已有内容";
                    statusArea.style.color = "#00ffc8";
                    confirmBtn.textContent = "确认同步更新";
                } else {
                    statusArea.textContent = "ℹ 未检测到同名仓库，将创建新仓库";
                    statusArea.style.color = "#00d4ff";
                    confirmBtn.textContent = "创建并同步";
                }
            } catch (e) {
                statusArea.textContent = "⚠ 检查失败";
                statusArea.style.color = "#ff4444";
            }
        }, 600);
    });

    // === 确认按钮：执行同步 ===
    confirmBtn.addEventListener("click", async () => {
        const repoName = repoInput.value.trim();
        if (!repoName) {
            statusArea.textContent = "⚠ 请输入仓库名称";
            statusArea.style.color = "#ff4444";
            return;
        }

        if (!/^[a-zA-Z0-9_.\-]+$/.test(repoName)) {
            statusArea.textContent = "⚠ 仓库名仅允许英文字母、数字、下划线、点、连字符";
            statusArea.style.color = "#ff4444";
            return;
        }

        confirmBtn.disabled = true;
        cancelBtn.disabled = true;
        confirmBtn.textContent = "同步中...";
        progressArea.style.display = "block";
        progressArea.textContent = "正在上传文件到 GitHub...";
        progressArea.style.color = "#00d4ff";

        try {
            const resp = await fetch("/ai-coder/github-sync", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({
                    plugin_path: selectedPlugin,
                    repo_name: repoName,
                    ignore_gitignore: !ignoreCheckbox.checked
                })
            });
            const data = await resp.json();

            if (data.status === "success") {
                progressArea.innerHTML = `✓ ${data.message}<br><a href="${data.repo_url}" target="_blank" style="color: #00d4ff;">${data.repo_url}</a>`;
                progressArea.style.color = "#00ffc8";
                confirmBtn.textContent = "✓ 完成";
                const autoCloseTimer = setTimeout(() => overlay.remove(), 3000);
                overlay.addEventListener("click", () => clearTimeout(autoCloseTimer), { once: true });
            } else {
                progressArea.textContent = "✗ " + (data.error || "同步失败");
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
