// ═══════════════════════════════════════════════════════════════
// 模板市场对话框.js — 从模板创建项目
// NodeCraft AI — Luxury Terminal / Neo-Noir Hacker
// ═══════════════════════════════════════════════════════════════

import { NCA_STORAGE_KEYS } from "./工具函数.js";
import { 存储 } from "./存储引擎.js";
import { 创建会话 } from "./交互与状态.js";

/**
 * 显示"从模板创建项目"对话框
 * @param {HTMLElement} rootContainer - 根容器引用（保留参数以兼容调用约定）
 */
export async function 显示模板市场对话框(rootContainer) {
    // 创建 overlay 遮罩
    const overlay = document.createElement("div");
    overlay.className = "nc-sync-overlay";

    // 创建 modal 容器
    const modal = document.createElement("div");
    modal.className = "nc-sync-modal";
    modal.style.width = "520px";

    // === Header ===
    const header = document.createElement("div");
    header.className = "nc-sync-header";
    header.innerHTML = `<span>📋 从模板创建项目</span>`;
    const closeBtn = document.createElement("button");
    closeBtn.className = "nc-sync-close";
    closeBtn.textContent = "✕";
    closeBtn.addEventListener("click", () => overlay.remove());
    header.appendChild(closeBtn);
    modal.appendChild(header);

    // === Body ===
    const body = document.createElement("div");
    body.className = "nc-sync-body";

    // 模板列表区域
    const templateListArea = document.createElement("div");
    templateListArea.style.cssText = "display: flex; flex-direction: column; gap: 8px; max-height: 300px; overflow-y: auto; padding: 4px 0;";
    templateListArea.innerHTML = '<div style="color: #6b7280; text-align: center; padding: 20px;">加载模板中...</div>';
    body.appendChild(templateListArea);

    // 项目名输入
    const nameField = document.createElement("div");
    nameField.className = "nc-sync-field";
    nameField.innerHTML = `<label>项目名称</label>`;
    const nameInput = document.createElement("input");
    nameInput.type = "text";
    nameInput.className = "nc-sync-input";
    nameInput.placeholder = "仅允许英文、数字、下划线和连字符";
    nameInput.addEventListener("input", () => {
        nameInput.value = nameInput.value.replace(/[^a-zA-Z0-9_-]/g, '');
    });
    nameField.appendChild(nameInput);
    body.appendChild(nameField);

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
    confirmBtn.textContent = "创建项目";
    confirmBtn.disabled = true;

    btnGroup.appendChild(cancelBtn);
    btnGroup.appendChild(confirmBtn);
    body.appendChild(btnGroup);

    modal.appendChild(body);
    overlay.appendChild(modal);
    overlay.addEventListener("click", (e) => {
        if (e.target === overlay) overlay.remove();
    });
    document.body.appendChild(overlay);

    // === 状态管理 ===
    let selectedTemplateId = null;

    // === 加载模板列表 ===
    try {
        const resp = await fetch("/ai-coder/templates");
        const data = await resp.json();
        if (!data.success) {
            templateListArea.innerHTML = `<div style="color: #ff4444; text-align: center; padding: 20px;">加载失败: ${data.error || '未知错误'}</div>`;
            return;
        }
        const templates = data.templates || [];
        if (templates.length === 0) {
            templateListArea.innerHTML = '<div style="color: #6b7280; text-align: center; padding: 20px;">暂无可用模板</div>';
            return;
        }

        templateListArea.innerHTML = '';
        templates.forEach(tpl => {
            const card = document.createElement("div");
            card.className = "nc-template-card";
            card.style.cssText = `
                padding: 10px 14px;
                border: 1px solid rgba(255,255,255,0.08);
                border-radius: 6px;
                cursor: pointer;
                transition: all 0.15s ease;
                background: rgba(255,255,255,0.02);
            `;

            const titleRow = document.createElement("div");
            titleRow.style.cssText = "display: flex; align-items: center; justify-content: space-between;";
            const title = document.createElement("span");
            title.style.cssText = "font-size: 13px; font-weight: 600; color: #e0e0e0;";
            title.textContent = tpl.name;
            const badge = document.createElement("span");
            badge.style.cssText = "font-size: 10px; padding: 2px 8px; border-radius: 10px; background: rgba(0,212,255,0.15); color: #00d4ff;";
            badge.textContent = tpl.category;
            titleRow.appendChild(title);
            titleRow.appendChild(badge);
            card.appendChild(titleRow);

            const desc = document.createElement("div");
            desc.style.cssText = "font-size: 11px; color: #888; margin-top: 4px;";
            desc.textContent = tpl.description;
            card.appendChild(desc);

            card.addEventListener("mouseenter", () => {
                if (selectedTemplateId !== tpl.id) card.style.borderColor = "rgba(0,212,255,0.3)";
            });
            card.addEventListener("mouseleave", () => {
                if (selectedTemplateId !== tpl.id) card.style.borderColor = "rgba(255,255,255,0.08)";
            });
            card.addEventListener("click", () => {
                // 取消之前的选中
                templateListArea.querySelectorAll(".nc-template-card").forEach(c => {
                    c.style.borderColor = "rgba(255,255,255,0.08)";
                    c.style.background = "rgba(255,255,255,0.02)";
                });
                card.style.borderColor = "rgba(0,212,255,0.5)";
                card.style.background = "rgba(0,212,255,0.06)";
                selectedTemplateId = tpl.id;
                // 自动填充项目名（用模板id作为默认值）
                if (!nameInput.value) nameInput.value = tpl.id;
                confirmBtn.disabled = false;
            });

            templateListArea.appendChild(card);
        });
    } catch (e) {
        templateListArea.innerHTML = `<div style="color: #ff4444; text-align: center; padding: 20px;">网络错误: ${e.message}</div>`;
    }

    // === 确认按钮：从模板创建项目 ===
    confirmBtn.addEventListener("click", async () => {
        const projectName = nameInput.value.trim();
        if (!selectedTemplateId) {
            progressArea.style.display = "block";
            progressArea.textContent = "⚠ 请先选择一个模板";
            progressArea.style.color = "#ff4444";
            return;
        }
        if (!projectName) {
            progressArea.style.display = "block";
            progressArea.textContent = "⚠ 请输入项目名称";
            progressArea.style.color = "#ff4444";
            return;
        }

        confirmBtn.disabled = true;
        cancelBtn.disabled = true;
        confirmBtn.textContent = "创建中...";
        progressArea.style.display = "block";
        progressArea.textContent = "正在从模板创建项目...";
        progressArea.style.color = "#00d4ff";

        try {
            const resp = await fetch("/ai-coder/templates/create", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ template_id: selectedTemplateId, project_name: projectName })
            });
            const data = await resp.json();

            if (data.success) {
                progressArea.innerHTML = `✓ ${data.message}<br><span style="font-size:11px;color:#888;">路径: ${data.path}</span>`;
                progressArea.style.color = "#00ffc8";
                confirmBtn.textContent = "✓ 完成";
                // 更新当前选中的项目——异步写入 IndexedDB，并同步刷新 localStorage 影子缓存
                存储.写入(NCA_STORAGE_KEYS.plugin, projectName).catch(() => {});
                // 自动创建对应会话并切换过去（与 "+" 新建项目体验一致）
                await 创建会话(projectName, projectName);
                // 自动关闭
                const autoCloseTimer = setTimeout(() => overlay.remove(), 3000);
                overlay.addEventListener("click", () => clearTimeout(autoCloseTimer), { once: true });
            } else {
                progressArea.textContent = "✗ " + (data.message || data.error || "创建失败");
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
