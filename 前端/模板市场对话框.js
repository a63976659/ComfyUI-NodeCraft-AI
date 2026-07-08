// ═══════════════════════════════════════════════════════════════
// 模板市场对话框.js — 按入口类型选择的可视化 ComfyUI 界面选择器
// NodeCraft AI — Luxury Terminal / Neo-Noir Hacker
// ═══════════════════════════════════════════════════════════════

import { NCA_STORAGE_KEYS } from "./工具函数.js";
import { 存储 } from "./存储引擎.js";
import { 创建会话 } from "./交互与状态.js";

// 入口类型 → 模板与默认项目名的映射
const ENTRY_TYPE_MAP = {
    topMenu: { template_id: "ui_extension", label: "顶部菜单栏", defaultName: "my_menu_extension" },
    sidebar: { template_id: "ui_extension", label: "侧边栏 Tab", defaultName: "my_sidebar_plugin" },
    canvas: { template_id: "basic_node", label: "基础节点", defaultName: "my_custom_node" },
    statusBar: { template_id: "ui_extension", label: "底部状态栏", defaultName: "my_statusbar_plugin" },
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
    header.innerHTML = `<span>选择插件入口类型</span>`;
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
    subtitle.textContent = "点击下方界面区域来选择模板类型（单选）";
    body.appendChild(subtitle);

    // === 可视化 ComfyUI 界面布局 ===
    const layout = document.createElement("div");
    layout.className = "nc-tpl-layout";

    // 顶部菜单栏
    const topMenu = document.createElement("div");
    topMenu.className = "nc-tpl-region nc-tpl-topmenu";
    topMenu.dataset.entry = "topMenu";
    topMenu.innerHTML = `<span class="nc-tpl-region-label">☰ 顶部菜单栏</span>`;
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
        <span class="nc-tpl-region-label">侧边栏 Tab</span>
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
        <span class="nc-tpl-node-label">基础节点</span>
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
        <span class="nc-tpl-region-label">● 状态信息</span>
        <span class="nc-tpl-region-label">底部状态栏</span>
        <span class="nc-tpl-region-label">模型: GPT ｜ 余额: ¥12</span>
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
        <span>快捷键与全局事件 <span class="nc-tpl-checkbox-desc">— 注册键盘快捷键和监听全局事件</span></span>
    `;
    options.appendChild(shortcutsLabel);

    const settingsLabel = document.createElement("label");
    settingsLabel.className = "nc-tpl-checkbox";
    settingsLabel.innerHTML = `
        <input type="checkbox" data-option="settings">
        <span>设置面板 <span class="nc-tpl-checkbox-desc">— 持久化用户配置（API 密钥、偏好等）</span></span>
    `;
    options.appendChild(settingsLabel);

    body.appendChild(options);

    // === 项目名输入 ===
    const nameField = document.createElement("div");
    nameField.className = "nc-sync-field";
    nameField.innerHTML = `<label>项目名称</label>`;
    const nameInput = document.createElement("input");
    nameInput.type = "text";
    nameInput.className = "nc-sync-input";
    nameInput.placeholder = "仅允许英文、数字、下划线和连字符";
    nameInput.addEventListener("input", () => {
        nameInput.value = nameInput.value.replace(/[^a-zA-Z0-9_-]/g, '');
        // 一旦用户手动编辑，标记为脏，避免切换入口时被覆盖
        nameInput.dataset.dirty = "1";
    });
    nameField.appendChild(nameInput);
    body.appendChild(nameField);

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
    cancelBtn.textContent = "取消";
    cancelBtn.addEventListener("click", () => overlay.remove());

    const confirmBtn = document.createElement("button");
    confirmBtn.className = "nc-sync-btn-confirm";
    confirmBtn.textContent = "确认创建";
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
        confirmBtn.disabled = false;
    }

    selectableEls.forEach(el => {
        el.addEventListener("click", () => selectEntry(el.dataset.entry, el));
    });

    // === 确认按钮：从模板创建项目 ===
    confirmBtn.addEventListener("click", async () => {
        if (!selectedEntry) {
            progressArea.style.display = "block";
            progressArea.textContent = "⚠ 请先选择一个入口类型";
            progressArea.style.color = "var(--nca-error)";
            return;
        }
        const projectName = nameInput.value.trim();
        if (!projectName) {
            progressArea.style.display = "block";
            progressArea.textContent = "⚠ 请输入项目名称";
            progressArea.style.color = "var(--nca-error)";
            return;
        }

        const meta = ENTRY_TYPE_MAP[selectedEntry];
        const templateId = meta.template_id;
        const selectedOptions = Array.from(
            options.querySelectorAll('input[type="checkbox"]:checked')
        ).map(cb => cb.dataset.option);

        confirmBtn.disabled = true;
        cancelBtn.disabled = true;
        confirmBtn.textContent = "创建中...";
        progressArea.style.display = "block";
        progressArea.textContent = "正在从模板创建项目...";
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
                })
            });
            const data = await resp.json();

            if (data.success) {
                progressArea.innerHTML = `✓ ${data.message}<br><span style="font-size:11px;color:var(--nca-fg-dim);">路径: ${data.path}</span>`;
                progressArea.style.color = "var(--nca-green)";
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
                progressArea.style.color = "var(--nca-error)";
                confirmBtn.disabled = false;
                cancelBtn.disabled = false;
                confirmBtn.textContent = "重试";
            }
        } catch (e) {
            progressArea.textContent = "✗ 网络错误: " + e.message;
            progressArea.style.color = "var(--nca-error)";
            confirmBtn.disabled = false;
            cancelBtn.disabled = false;
            confirmBtn.textContent = "重试";
        }
    });
}
