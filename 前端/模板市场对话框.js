// ═══════════════════════════════════════════════════════════════
// 模板市场对话框.js — 按入口类型选择的可视化 ComfyUI 界面选择器
// NodeCraft AI — Luxury Terminal / Neo-Noir Hacker
// ═══════════════════════════════════════════════════════════════

import { NCA_STORAGE_KEYS } from "./工具函数.js";
import { 存储 } from "./存储引擎.js";
import { 创建会话 } from "./交互与状态.js";
import { t } from "./i18n.js";

// 入口类型 → 模板与默认项目名的映射（文案字段存 i18n key，使用处调 t() 以响应语言切换）
const ENTRY_TYPE_MAP = {
    topMenu: { template_id: "ui_extension", label: "template.entry_top_menu", defaultName: "my_menu_extension",
        customLabel: "template.menu_label", customPlaceholder: "template.menu_label_ph", customKey: "menu_label", customDefault: "template.menu_label_default" },
    sidebar: { template_id: "ui_extension", label: "template.entry_sidebar", defaultName: "my_sidebar_plugin",
        customLabel: "template.sidebar_title", customPlaceholder: "template.sidebar_title_ph", customKey: "sidebar_title", customDefault: "template.sidebar_title_default" },
    canvas: { template_id: "basic_node", label: "template.entry_basic_node", defaultName: "my_custom_node",
        customLabel: "template.node_category", customPlaceholder: "template.node_category_ph", customKey: "category", customDefault: "template.node_category_default" },
    statusBar: { template_id: "ui_extension", label: "template.entry_status_bar", defaultName: "my_statusbar_plugin",
        customLabel: "template.status_label", customPlaceholder: "template.status_label_ph", customKey: "status_label", customDefault: "template.status_label_default" },
};

/**
 * 显示"选择插件入口类型"对话框
 * @param {HTMLElement} rootContainer - 根容器引用（保留参数以兼容调用约定）
 */
export async function 显示模板市场对话框(rootContainer) {
    // === Overlay 遮罩 ===
    const overlay = document.createElement("div");
    overlay.className = "nc-sync-overlay";

    // === Modal 容器 ===
    const modal = document.createElement("div");
    modal.className = "nc-sync-modal";
    modal.style.width = "580px";

    // === Header ===
    const header = document.createElement("div");
    header.className = "nc-sync-header";
    header.innerHTML = `<span>${t("template.dialog_title")}</span>`;
    const closeBtn = document.createElement("button");
    closeBtn.className = "nc-sync-close";
    closeBtn.textContent = "✕";
    closeBtn.addEventListener("click", () => overlay.remove());
    header.appendChild(closeBtn);
    modal.appendChild(header);

    // === Body ===
    const body = document.createElement("div");
    body.className = "nc-sync-body";

    // 副标题
    const subtitle = document.createElement("div");
    subtitle.className = "nc-tpl-subtitle";
    subtitle.textContent = t("template.subtitle");
    body.appendChild(subtitle);

    // === 可视化 ComfyUI 界面布局 ===
    const layout = document.createElement("div");
    layout.className = "nc-tpl-layout";

    // 顶部菜单栏
    const topMenu = document.createElement("div");
    topMenu.className = "nc-tpl-region nc-tpl-topmenu";
    topMenu.dataset.entry = "topMenu";
    topMenu.innerHTML = `<span class="nc-tpl-region-label">☰ ${t("template.entry_top_menu")}</span>`;
    layout.appendChild(topMenu);

    // 侧边栏
    const sidebar = document.createElement("div");
    sidebar.className = "nc-tpl-region nc-tpl-sidebar";
    sidebar.dataset.entry = "sidebar";
    sidebar.innerHTML = `
        <div class="nc-tpl-sidebar-icons">
            <div class="nc-tpl-sidebar-icon">⚡</div>
            <div class="nc-tpl-sidebar-icon">📁</div>
            <div class="nc-tpl-sidebar-icon">🔍</div>
        </div>
        <span class="nc-tpl-region-label">${t("template.entry_sidebar")}</span>
    `;
    layout.appendChild(sidebar);

    // 画布中央区域（装饰性背景 + 可点击节点）
    const canvas = document.createElement("div");
    canvas.className = "nc-tpl-canvas";
    const dots = document.createElement("div");
    dots.className = "nc-tpl-canvas-dots";
    canvas.appendChild(dots);

    const node = document.createElement("div");
    node.className = "nc-tpl-node";
    node.dataset.entry = "canvas";
    node.innerHTML = `
        <span class="nc-tpl-node-label">${t("template.entry_basic_node")}</span>
        <div class="nc-tpl-node-dots">
            <div class="nc-tpl-dot" style="background:#10b981;"></div>
            <div class="nc-tpl-dot" style="background:#00d4ff;"></div>
            <div class="nc-tpl-dot" style="background:#f59e0b;"></div>
        </div>
    `;
    canvas.appendChild(node);
    layout.appendChild(canvas);

    // 底部状态栏
    const statusBar = document.createElement("div");
    statusBar.className = "nc-tpl-region nc-tpl-statusbar";
    statusBar.dataset.entry = "statusBar";
    statusBar.innerHTML = `
        <span class="nc-tpl-region-label">${t("template.status_info")}</span>
        <span class="nc-tpl-region-label">${t("template.entry_status_bar")}</span>
        <span class="nc-tpl-region-label">${t("template.status_demo")}</span>
    `;
    layout.appendChild(statusBar);

    body.appendChild(layout);

    // === 附加选项（checkbox 多选）===
    const options = document.createElement("div");
    options.className = "nc-tpl-options";

    const shortcutsLabel = document.createElement("label");
    shortcutsLabel.className = "nc-tpl-checkbox";
    shortcutsLabel.innerHTML = `
        <input type="checkbox" data-option="shortcuts">
        <span>${t("template.opt_shortcuts")} <span class="nc-tpl-checkbox-desc">${t("template.opt_shortcuts_desc")}</span></span>
    `;
    options.appendChild(shortcutsLabel);

    const settingsLabel = document.createElement("label");
    settingsLabel.className = "nc-tpl-checkbox";
    settingsLabel.innerHTML = `
        <input type="checkbox" data-option="settings">
        <span>${t("template.opt_settings")} <span class="nc-tpl-checkbox-desc">${t("template.opt_settings_desc")}</span></span>
    `;
    options.appendChild(settingsLabel);

    body.appendChild(options);

    // === 项目名输入 ===
    const nameField = document.createElement("div");
    nameField.className = "nc-sync-field";
    nameField.innerHTML = `<label>${t("template.project_name")}</label>`;
    const nameInput = document.createElement("input");
    nameInput.type = "text";
    nameInput.className = "nc-sync-input";
    nameInput.placeholder = t("template.project_name_ph");
    nameInput.addEventListener("input", () => {
        nameInput.value = nameInput.value.replace(/[^a-zA-Z0-9_-]/g, '');
        // 一旦用户手动编辑，标记为脏，避免切换入口时被覆盖
        nameInput.dataset.dirty = "1";
    });
    nameField.appendChild(nameInput);
    body.appendChild(nameField);

    // === 自定义名称输入（根据入口类型动态显示） ===
    const customNameField = document.createElement("div");
    customNameField.className = "nc-sync-field";
    customNameField.style.display = "none";
    const customNameLabel = document.createElement("label");
    customNameField.appendChild(customNameLabel);
    const customNameInput = document.createElement("input");
    customNameInput.type = "text";
    customNameInput.className = "nc-sync-input";
    customNameField.appendChild(customNameInput);
    body.appendChild(customNameField);

    // === 进度/结果区域 ===
    const progressArea = document.createElement("div");
    progressArea.className = "nc-sync-progress";
    progressArea.style.display = "none";
    body.appendChild(progressArea);

    // === 操作按钮 ===
    const btnGroup = document.createElement("div");
    btnGroup.className = "nc-sync-actions";

    const cancelBtn = document.createElement("button");
    cancelBtn.className = "nc-sync-btn-cancel";
    cancelBtn.textContent = t("common.cancel");
    cancelBtn.addEventListener("click", () => overlay.remove());

    const confirmBtn = document.createElement("button");
    confirmBtn.className = "nc-sync-btn-confirm";
    confirmBtn.textContent = t("template.confirm_create");
    confirmBtn.disabled = true;

    btnGroup.appendChild(cancelBtn);
    btnGroup.appendChild(confirmBtn);
    body.appendChild(btnGroup);

    modal.appendChild(body);
    overlay.appendChild(modal);
    overlay.addEventListener("click", (e) => {
        if (e.target === overlay) overlay.remove();
    });
    rootContainer.appendChild(overlay);

    // === 状态管理 ===
    let selectedEntry = null;
    const selectableEls = [topMenu, sidebar, node, statusBar];

    function selectEntry(entryType, el) {
        // 单选：清除所有选中态
        selectableEls.forEach(e => e.classList.remove("selected"));
        el.classList.add("selected");
        selectedEntry = entryType;

        // 自动填充默认项目名（用户未手动编辑时）
        const meta = ENTRY_TYPE_MAP[entryType];
        if (meta && nameInput.dataset.dirty !== "1") {
            nameInput.value = meta.defaultName;
        }

        // 显示/填充自定义名称输入框
        if (meta && meta.customKey) {
            customNameLabel.textContent = t(meta.customLabel);
            customNameInput.placeholder = t(meta.customPlaceholder);
            customNameInput.value = t(meta.customDefault);
            customNameField.style.display = "";
        } else {
            customNameField.style.display = "none";
        }

        confirmBtn.disabled = false;
    }

    selectableEls.forEach(el => {
        el.addEventListener("click", () => selectEntry(el.dataset.entry, el));
    });

    // === 确认按钮：从模板创建项目 ===
    confirmBtn.addEventListener("click", async () => {
        if (!selectedEntry) {
            progressArea.style.display = "block";
            progressArea.textContent = t("template.select_entry_first");
            progressArea.style.color = "var(--nca-error)";
            return;
        }
        const projectName = nameInput.value.trim();
        if (!projectName) {
            progressArea.style.display = "block";
            progressArea.textContent = t("template.project_name_required");
            progressArea.style.color = "var(--nca-error)";
            return;
        }

        const meta = ENTRY_TYPE_MAP[selectedEntry];
        const templateId = meta.template_id;
        const selectedOptions = Array.from(
            options.querySelectorAll('input[type="checkbox"]:checked')
        ).map(cb => cb.dataset.option);

        // 收集自定义名称
        const customNames = {};
        if (meta && meta.customKey) {
            const customValue = customNameInput.value.trim();
            if (customValue) {
                customNames[meta.customKey] = customValue;
            }
        }

        confirmBtn.disabled = true;
        cancelBtn.disabled = true;
        confirmBtn.textContent = t("common.creating");
        progressArea.style.display = "block";
        progressArea.textContent = t("template.creating_from");
        progressArea.style.color = "var(--nca-accent)";

        try {
            const resp = await fetch("/ai-coder/templates/create", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({
                    template_id: templateId,
                    project_name: projectName,
                    entry_type: selectedEntry,
                    options: selectedOptions,
                    custom_names: customNames,
                })
            });
            const data = await resp.json();

            if (data.success) {
                progressArea.innerHTML = `✓ ${data.message}<br><span style="font-size:11px;color:var(--nca-fg-dim);">${t("template.path", { path: data.path })}</span>`;
                progressArea.style.color = "var(--nca-green)";
                confirmBtn.textContent = t("common.done");
                // 更新当前选中的项目——异步写入 IndexedDB，并同步刷新 localStorage 影子缓存
                存储.写入(NCA_STORAGE_KEYS.plugin, projectName).catch(() => {});
                // 自动创建对应会话并切换过去（与 "+" 新建项目体验一致）
                await 创建会话(projectName, projectName);
                // 自动关闭
                const autoCloseTimer = setTimeout(() => overlay.remove(), 3000);
                overlay.addEventListener("click", () => clearTimeout(autoCloseTimer), { once: true });
            } else {
                progressArea.textContent = "✗ " + (data.message || data.error || t("project.create_failed"));
                progressArea.style.color = "var(--nca-error)";
                confirmBtn.disabled = false;
                cancelBtn.disabled = false;
                confirmBtn.textContent = t("common.retry");
            }
        } catch (e) {
            progressArea.textContent = t("common.network_error_msg", { error: e.message });
            progressArea.style.color = "var(--nca-error)";
            confirmBtn.disabled = false;
            cancelBtn.disabled = false;
            confirmBtn.textContent = t("common.retry");
        }
    });
}
