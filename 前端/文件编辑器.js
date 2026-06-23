// ═══════════════════════════════════════════════════════════════
// 文件编辑器.js — 插件文件编辑器（文件树 + 代码编辑区）
// NodeCraft AI — Luxury Terminal / Neo-Noir Hacker
// ═══════════════════════════════════════════════════════════════

import { el, NCA_STORAGE_KEYS, Toast } from "./工具函数.js";
import { 事件总线, 事件 } from "./交互与状态.js";

const API_BASE = "/ai-coder";

// ─── 内部请求封装（复用项目 Token / CSRF 逻辑）─────────────────
async function 请求文件(path, body = null) {
    const headers = { "Content-Type": "application/json" };
    try {
        const token = sessionStorage.getItem("ComfyCommunity_Token")
            || localStorage.getItem("ComfyCommunity_Token");
        if (token) headers["Authorization"] = `Bearer ${token}`;
    } catch (_) {}
    try {
        let csrf = sessionStorage.getItem("_nca_csrf");
        if (csrf) headers["X-CSRF-Token"] = csrf;
    } catch (_) {}

    const options = { method: "POST", headers };
    if (body) options.body = JSON.stringify(body);

    const response = await fetch(`${API_BASE}${path}`, options);
    if (!response.ok) {
        let errorMsg = `HTTP ${response.status}`;
        try { const d = await response.json(); if (d.error) errorMsg = d.error; } catch (_) {}
        throw new Error(errorMsg);
    }
    return response.json();
}

// ─── 递归渲染文件树 ──────────────────────────────────────────
function 渲染文件树(树结构, 点击回调, 深度 = 0) {
    const fragment = document.createDocumentFragment();

    for (const item of 树结构) {
        if (item.type === "directory" || item.children) {
            // 目录节点
            const details = document.createElement("details");
            const summary = document.createElement("summary");
            summary.textContent = `📁 ${item.name}`;
            summary.title = item.path || item.name;
            details.appendChild(summary);

            if (item.children && item.children.length > 0) {
                const children = 渲染文件树(item.children, 点击回调, 深度 + 1);
                details.appendChild(children);
            }
            fragment.appendChild(details);
        } else {
            // 文件节点
            const fileEl = document.createElement("div");
            fileEl.className = "nca-file-editor-tree-file";
            fileEl.textContent = `📄 ${item.name}`;
            fileEl.title = item.path || item.name;
            fileEl.dataset.path = item.path || "";
            fileEl.addEventListener("click", () => 点击回调(item));
            fragment.appendChild(fileEl);
        }
    }

    return fragment;
}

/**
 * 创建文件编辑器面板
 * @param {Object} options - 配置项
 * @returns {{ container: HTMLElement, refresh: Function, destroy: Function }}
 */
export function 创建文件编辑器面板(options = {}) {
    // ─── 状态 ─────────────────────────────────────────────────
    let 当前文件路径 = "";
    let 原始内容 = "";
    let 是否dirty = false;
    let 当前文件夹 = localStorage.getItem(NCA_STORAGE_KEYS.plugin) || "";
    let 文件树数据 = [];

    // ─── DOM 结构 ──────────────────────────────────────────────
    const container = el("div", {
        class: "nca-file-editor-container",
        style: { flex: "1", display: "flex", flexDirection: "row", minHeight: "0", overflow: "hidden" },
    });

    // 左侧文件树
    const treePanel = el("div", { class: "nca-file-editor-tree" });
    container.appendChild(treePanel);

    // 右侧编辑区
    const contentPanel = el("div", { class: "nca-file-editor-content" });
    container.appendChild(contentPanel);

    // 工具栏
    const toolbar = el("div", { class: "nca-file-editor-toolbar" });
    const filenameLabel = el("span", { class: "nca-file-editor-filename", text: "未打开文件" });
    const saveBtn = el("button", { class: "nca-file-editor-toolbar-btn save-btn", text: "💾 保存", disabled: "true" });
    const discardBtn = el("button", { class: "nca-file-editor-toolbar-btn", text: "↩ 放弃修改", disabled: "true" });
    toolbar.appendChild(filenameLabel);
    toolbar.appendChild(saveBtn);
    toolbar.appendChild(discardBtn);
    contentPanel.appendChild(toolbar);

    // textarea 编辑区
    const textarea = el("textarea", {
        class: "nca-file-editor-textarea",
        placeholder: "选择左侧文件开始编辑...",
        spellcheck: "false",
    });
    contentPanel.appendChild(textarea);

    // 空状态提示（无文件夹时显示）
    const emptyState = el("div", { class: "nca-file-editor-empty" });
    emptyState.innerHTML = '<div><div class="nca-file-editor-empty-icon">📂</div><div>请先选择插件文件夹</div></div>';

    // ─── 功能方法 ──────────────────────────────────────────────

    function 更新Dirty状态() {
        是否dirty = textarea.value !== 原始内容;
        saveBtn.disabled = !是否dirty;
        discardBtn.disabled = !是否dirty;
        // 文件名旁显示 dirty 标记
        if (当前文件路径) {
            const name = 当前文件路径.split(/[/\\]/).pop();
            filenameLabel.innerHTML = 是否dirty
                ? `${name}<span class="dirty-mark">*</span>`
                : name;
        }
    }

    textarea.addEventListener("input", 更新Dirty状态);

    // Tab 键支持（插入缩进而非跳出）
    textarea.addEventListener("keydown", (e) => {
        if (e.key === "Tab") {
            e.preventDefault();
            const start = textarea.selectionStart;
            const end = textarea.selectionEnd;
            textarea.value = textarea.value.substring(0, start) + "    " + textarea.value.substring(end);
            textarea.selectionStart = textarea.selectionEnd = start + 4;
            更新Dirty状态();
        }
        // Ctrl+S 保存
        if ((e.ctrlKey || e.metaKey) && e.key === "s") {
            e.preventDefault();
            if (是否dirty) 保存文件();
        }
    });

    async function 加载文件树() {
        if (!当前文件夹) {
            treePanel.innerHTML = "";
            contentPanel.style.display = "none";
            container.innerHTML = "";
            container.appendChild(emptyState);
            return;
        }

        // 确保正常结构
        if (!container.contains(treePanel)) {
            container.innerHTML = "";
            container.appendChild(treePanel);
            container.appendChild(contentPanel);
            contentPanel.style.display = "";
        }

        treePanel.innerHTML = '<div class="nca-file-editor-loading">加载文件树...</div>';
        try {
            const data = await 请求文件("/plugin-files", { folder_path: 当前文件夹 });
            文件树数据 = data.files || data.tree || [];
            渲染树();
        } catch (e) {
            treePanel.innerHTML = `<div class="nca-file-editor-empty"><div>加载失败: ${e.message}</div></div>`;
        }
    }

    function 渲染树() {
        treePanel.innerHTML = "";
        if (文件树数据.length === 0) {
            treePanel.innerHTML = '<div class="nca-file-editor-empty"><div>空目录</div></div>';
            return;
        }
        const tree = 渲染文件树(文件树数据, 点击文件);
        treePanel.appendChild(tree);
    }

    async function 点击文件(item) {
        // dirty 检查
        if (是否dirty) {
            const confirm = window.confirm(`文件 "${当前文件路径.split(/[/\\]/).pop()}" 有未保存的修改，确定切换？`);
            if (!confirm) return;
        }

        // 高亮当前文件
        treePanel.querySelectorAll(".nca-file-editor-tree-file.active").forEach(el => el.classList.remove("active"));
        const target = treePanel.querySelector(`[data-path="${CSS.escape(item.path)}"]`);
        if (target) target.classList.add("active");

        当前文件路径 = item.path;
        filenameLabel.textContent = item.name || item.path.split(/[/\\]/).pop();
        textarea.value = "";
        textarea.placeholder = "加载中...";
        saveBtn.disabled = true;
        discardBtn.disabled = true;

        try {
            const data = await 请求文件("/read-file", { file_path: item.path });
            原始内容 = data.content || "";
            textarea.value = 原始内容;
            textarea.placeholder = "选择左侧文件开始编辑...";
            是否dirty = false;
            更新Dirty状态();
        } catch (e) {
            textarea.value = "";
            textarea.placeholder = `加载失败: ${e.message}`;
            Toast.error(`读取文件失败: ${e.message}`);
        }
    }

    async function 保存文件() {
        if (!当前文件路径 || !是否dirty) return;

        saveBtn.disabled = true;
        saveBtn.textContent = "保存中...";
        try {
            await 请求文件("/write-file", { file_path: 当前文件路径, content: textarea.value });
            原始内容 = textarea.value;
            是否dirty = false;
            更新Dirty状态();
            Toast.success("文件已保存");
        } catch (e) {
            Toast.error(`保存失败: ${e.message}`);
        } finally {
            saveBtn.textContent = "💾 保存";
            更新Dirty状态();
        }
    }

    function 放弃修改() {
        if (!是否dirty) return;
        const confirm = window.confirm("确定放弃所有未保存的修改？");
        if (!confirm) return;
        textarea.value = 原始内容;
        是否dirty = false;
        更新Dirty状态();
    }

    // 按钮绑定
    saveBtn.addEventListener("click", 保存文件);
    discardBtn.addEventListener("click", 放弃修改);

    // ─── 事件监听 ──────────────────────────────────────────────
    function 处理插件选择变更(pluginName) {
        当前文件夹 = pluginName || "";
        // 重置编辑状态
        当前文件路径 = "";
        原始内容 = "";
        是否dirty = false;
        textarea.value = "";
        filenameLabel.textContent = "未打开文件";
        saveBtn.disabled = true;
        discardBtn.disabled = true;
        加载文件树();
    }

    事件总线.on(事件.插件选择变更, 处理插件选择变更);

    // 初始加载
    加载文件树();

    // ─── 返回公开接口 ──────────────────────────────────────────
    return {
        container,
        refresh() {
            当前文件夹 = localStorage.getItem(NCA_STORAGE_KEYS.plugin) || "";
            加载文件树();
        },
        destroy() {
            事件总线.off(事件.插件选择变更, 处理插件选择变更);
        },
    };
}
