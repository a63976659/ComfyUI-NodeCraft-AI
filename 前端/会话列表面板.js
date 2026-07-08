// ═══════════════════════════════════════════════════════════════
// 会话列表面板.js — 可复用的侧边栏会话列表组件
// NodeCraft AI — Luxury Terminal / Neo-Noir Hacker
//
// 三个界面（开发 / 优化 / 可视化）共用此组件：
//   - optimize / visualize：使用面板内部状态，自治拉取与刷新；
//   - develop：通过 useGlobalState=true 接入全局 状态.会话列表 / 状态.当前会话ID
//     与全局事件（会话列表更新 / 会话切换），保留旧有跨面板联动；
//   - 仅 type==='develop' 显示"删除会话和文件夹"双选项，其他类型仅普通删除。
// ═══════════════════════════════════════════════════════════════

import { el, Toast, NCA_STORAGE_KEYS, 安全存储读 } from "./工具函数.js";
import { t } from "./i18n.js";
import {
    获取会话列表, 创建会话, 删除会话, 更新会话标题, 格式化时间,
    切换会话 as 切换全局会话, 状态, 事件总线, 事件,
} from "./交互与状态.js";

/**
 * 创建一个独立的会话列表面板。
 * @param {Object} options
 * @param {'develop'|'optimize'|'visualize'} options.type  会话类型
 * @param {HTMLElement} options.container  挂载容器
 * @param {(session:Object)=>void} [options.onSessionSwitch]  切换会话回调（侧效）
 * @param {(session:Object)=>void} [options.onSessionCreate]  新建会话回调（保留扩展位）
 * @param {(sessionId:string)=>void} [options.onSessionDelete] 删除会话回调
 * @param {(session:Object)=>void} [options.onPackageClick]  打包按钮点击回调（仅当传入时才显示打包按钮）
 * @param {(collapsed:boolean)=>void} [options.onToggleCollapse]  会话面板折叠状态变化回调
 * @param {boolean} [options.defaultCollapsed=false]  初始是否折叠
 * @param {boolean} [options.showSearch=true]  是否显示搜索框
 * @param {boolean} [options.showCancelButton=false]  是否在标题栏显示"取消当前会话"按钮（✕）
 * @param {()=>void} [options.onCancelClick]  取消按钮点击回调
 * @param {boolean} [options.showCurrentSessionLabel=false]  是否在标题栏显示当前会话标签（折叠时可见）
 * @param {(newBtn:HTMLElement)=>void} [options.onNewClick]  自定义"+"按钮点击行为（提供时覆盖默认创建逻辑）
 * @param {boolean} [options.useGlobalState=false]  是否接入全局会话状态/事件（仅 type==='develop' 推荐）
 * @returns {{refresh:()=>Promise<void>, getCurrentSessionId:()=>(string|null), getCurrentSession:()=>(Object|null), isCollapsed:()=>boolean, destroy:()=>void}}
 */
export function 创建会话列表面板(options = {}) {
    const type = options.type || "develop";
    const container = options.container;
    if (!container) throw new Error("创建会话列表面板: container 必填");

    const onSwitch = typeof options.onSessionSwitch === "function" ? options.onSessionSwitch : null;
    const onCreate = typeof options.onSessionCreate === "function" ? options.onSessionCreate : null;
    const onDelete = typeof options.onSessionDelete === "function" ? options.onSessionDelete : null;
    const onPackage = typeof options.onPackageClick === "function" ? options.onPackageClick : null;
    const onCollapseChange = typeof options.onToggleCollapse === "function" ? options.onToggleCollapse : null;

    // ── 新增配置（向后兼容默认值；showCurrentSessionLabel 默认开启，三界面保持一致） ──
    const defaultCollapsed = options.defaultCollapsed === true;
    const showSearch = options.showSearch !== false;
    const showCancelButton = options.showCancelButton === true;
    const onCancelClick = typeof options.onCancelClick === "function" ? options.onCancelClick : null;
    const showCurrentSessionLabel = options.showCurrentSessionLabel !== false;
    const onNewClick = typeof options.onNewClick === "function" ? options.onNewClick : null;
    const useGlobalState = options.useGlobalState === true;

    // ─── 内部状态（与全局状态隔离；useGlobalState 时仅作为搜索关键词等本地态容器） ───
    const 内部 = {
        会话列表: [],
        当前会话ID: null,
        关键词: "",
        已销毁: false,
    };

    // 全局事件订阅取消句柄（仅 useGlobalState 时使用）
    const 全局事件取消句柄 = [];

    // ─── 数据访问层：根据是否启用全局状态切换数据源 ───────────
    function _获取列表() {
        return useGlobalState ? (状态.会话列表 || []) : 内部.会话列表;
    }
    function _获取当前ID() {
        return useGlobalState ? 状态.当前会话ID : 内部.当前会话ID;
    }

    // ─── DOM 结构 ────────────────────────────────────────────
    const wrap = el("div", { class: defaultCollapsed ? "nca-sessions collapsed" : "nca-sessions" });

    const toggle = el("div", { class: "nca-sessions-toggle" }, [
        el("span", { class: "arrow", text: "▼" }),
        el("span", { class: "sessions-label", text: t("session.label") || "会话" }),
    ]);

    const 计数 = el("span", { class: "session-count", text: "0" });
    toggle.appendChild(计数);

    // 当前会话标签（折叠时显示，由 CSS 控制可见性）
    let 当前会话标签 = null;
    if (showCurrentSessionLabel) {
        当前会话标签 = el("span", { class: "nca-current-session-label" });
        toggle.appendChild(当前会话标签);
    }

    // 弹性占位，确保标题栏右侧按钮（取消 / 新建）始终靠右对齐
    const 标题栏占位 = el("span", { class: "nca-session-header-spacer" });
    toggle.appendChild(标题栏占位);

    // 取消当前会话按钮（仅开发面板显示）
    let 取消按钮 = null;
    if (showCancelButton) {
        取消按钮 = el("button", {
            class: "nca-session-header-btn cancel-btn disabled",
            title: t("session.cancel") || "取消当前会话",
            html: "✕",
        });
        取消按钮.addEventListener("click", (e) => {
            e.stopPropagation();
            if (取消按钮.classList.contains("disabled")) return;
            try { onCancelClick && onCancelClick(); } catch (_) {}
        });
        toggle.appendChild(取消按钮);
    }

    // 新建按钮
    const 新建按钮 = el("button", {
        class: "nca-session-header-btn new-btn",
        title: onNewClick ? (t("project.new_or_template") || "新建项目 / 从模板创建") : (t("session.new") || "新建会话"),
        html: "＋",
    });
    新建按钮.addEventListener("click", async (e) => {
        e.stopPropagation();
        if (wrap.classList.contains("collapsed")) {
            wrap.classList.remove("collapsed");
            try { onCollapseChange && onCollapseChange(false); } catch (_) {}
        }
        if (onNewClick) {
            try { onNewClick(新建按钮); } catch (_) {}
        } else {
            await _新建会话();
        }
    });
    toggle.appendChild(新建按钮);

    toggle.addEventListener("click", () => {
        wrap.classList.toggle("collapsed");
        try { onCollapseChange && onCollapseChange(wrap.classList.contains("collapsed")); } catch (_) {}
    });
    wrap.appendChild(toggle);

    // 搜索框（可选）
    if (showSearch) {
        const 搜索区 = el("div", { class: "nca-session-search", style: { padding: "6px 10px" } });
        const 搜索输入 = el("input", {
            type: "text",
            class: "nca-session-search-input",
            placeholder: t("session.search_placeholder") || "搜索会话...",
            style: {
                width: "100%", boxSizing: "border-box", padding: "4px 8px",
                background: "var(--nca-input-bg, rgba(255,255,255,0.04))",
                border: "1px solid var(--nca-border, rgba(255,255,255,0.08))",
                borderRadius: "3px", color: "var(--nca-fg, #e5e7eb)",
                fontSize: "11px", outline: "none",
            },
        });
        搜索输入.addEventListener("input", () => {
            内部.关键词 = (搜索输入.value || "").trim().toLowerCase();
            渲染();
        });
        搜索输入.addEventListener("click", (e) => e.stopPropagation());
        搜索区.appendChild(搜索输入);
        wrap.appendChild(搜索区);
    }

    // 列表容器
    const 列表容器 = el("div", { class: "nca-session-list" });
    wrap.appendChild(列表容器);

    container.appendChild(wrap);

    // ─── 渲染 ────────────────────────────────────────────────
    function 过滤会话(列表) {
        if (!内部.关键词) return 列表;
        return 列表.filter(s => {
            const t1 = (s.title || "").toLowerCase();
            const t2 = (s.plugin_folder || "").toLowerCase();
            return t1.includes(内部.关键词) || t2.includes(内部.关键词);
        });
    }

    function 渲染() {
        if (内部.已销毁) return;
        const 列表 = _获取列表();
        列表容器.innerHTML = "";
        计数.textContent = 列表.length || 0;
        if (取消按钮) {
            取消按钮.classList.toggle("disabled", !_获取当前ID());
        }
        _更新当前会话标签();
        const 显示列表 = 过滤会话(列表);
        if (显示列表.length === 0) {
            const 提示文案 = 内部.关键词
                ? (t("session.no_match") || "— 无匹配 —")
                : (t("session.empty") || "暂无会话，点击 ＋ 新建开始对话");
            列表容器.appendChild(el("div", { class: "nca-session-empty nca-empty-state", text: 提示文案 }));
            return;
        }
        显示列表.forEach(session => {
            列表容器.appendChild(_构建会话项(session));
        });
    }

    function _更新当前会话标签() {
        if (!当前会话标签) return;
        const id = _获取当前ID();
        if (!id) {
            当前会话标签.textContent = "";
            当前会话标签.removeAttribute("title");
            return;
        }
        const sess = (_获取列表() || []).find(s => s.id === id);
        const title = (sess?.title || "").trim();
        const folder = (sess?.plugin_folder || "").trim();
        const folderName = folder ? folder.split(/[\\/]/).pop() : "";
        const parts = [];
        if (title && title !== folderName) parts.push(title);
        if (folderName) parts.push(`📁${folderName}`);
        const label = parts.length ? `· ${parts.join(" · ")}` : "";
        当前会话标签.textContent = label;
        const fullTitle = [title, folder].filter(Boolean).join(" / ");
        if (fullTitle) 当前会话标签.title = fullTitle;
        else 当前会话标签.removeAttribute("title");
    }

    function _构建会话项(session) {
        const isActive = session.id === _获取当前ID();
        const titleSpan = el("span", {
            class: "session-title",
            text: session.title || (t("session.untitled") || "未命名"),
            title: session.title,
        });
        // 当前会话若关联插件文件夹，则在会话项上展示文件夹标签（显示为文件夹名，hover 显示完整路径）
        const folderName = session.plugin_folder ? session.plugin_folder.split(/[\\/]/).pop() : "";
        const folderTag = folderName
            ? el("span", { class: "session-folder", text: `📁${folderName}`, title: session.plugin_folder })
            : null;
        const timeSpan = el("span", {
            class: "session-time",
            text: 格式化时间(session.updated_at || session.created_at),
        });
        const renameBtn = el("button", {
            class: "session-rename",
            text: "✎",
            title: t("session.rename") || "修改标题",
        });
        // 仅在外部传入 onPackageClick 且会话关联 plugin_folder 时展示打包按钮（hover 显示，由 CSS 控制）
        const packageBtn = (onPackage && session.plugin_folder)
            ? el("button", {
                class: "session-package",
                text: "⇧",
                title: t("session.package") || "打包插件",
            })
            : null;
        if (packageBtn) {
            packageBtn.addEventListener("click", (e) => {
                e.stopPropagation();
                try { onPackage(session); } catch (_) {}
            });
        }
        const deleteBtn = el("button", {
            class: "session-delete",
            text: "⊘",
            title: t("common.delete") || "删除",
        });
        const children = [titleSpan, folderTag, timeSpan, renameBtn, packageBtn, deleteBtn].filter(Boolean);
        const item = el("div", {
            class: `nca-session-item ${isActive ? "active" : ""}`,
        }, children);

        deleteBtn.addEventListener("click", (e) => {
            e.stopPropagation();
            _显示删除确认(session);
        });
        renameBtn.addEventListener("click", (e) => {
            e.stopPropagation();
            _进入编辑标题(item, session);
        });
        item.addEventListener("click", () => {
            if (item.classList.contains("editing")) return;
            _切换会话(session);
        });
        return item;
    }

    // ─── 操作：新建 / 切换 / 删除 / 重命名 ────────────────────
    async function _新建会话() {
        // optimize / visualize 类型：必须先选择插件文件夹才能新建
        if (type === "optimize" || type === "visualize") {
            const 选中文件夹 = 安全存储读(NCA_STORAGE_KEYS.plugin).trim();
            if (!选中文件夹) {
                Toast.warning("请选择文件夹");
                return;
            }
            // 默认名称 = 文件夹名（不是 i18n key）
            const 文件夹名 = 选中文件夹.split(/[\\/]/).pop() || 选中文件夹;
            const session = await 创建会话(文件夹名, 选中文件夹, type);
            if (!session) return;
            内部.会话列表 = 内部.会话列表 || [];
            await refresh();
            内部.当前会话ID = session.id;
            渲染();
            try { onCreate && onCreate(session); } catch (_) {}
            try { onSwitch && onSwitch(session); } catch (_) {}
            return;
        }
        const 默认标题 = t("session.default_develop") || "新会话";
        const session = await 创建会话(默认标题, "", type);
        if (!session) return;
        // develop 创建时已自动刷新全局并切换；其余类型需手动同步
        if (!useGlobalState) {
            await refresh();
            内部.当前会话ID = session.id;
        }
        渲染();
        try { onCreate && onCreate(session); } catch (_) {}
        try { onSwitch && onSwitch(session); } catch (_) {}
    }

    function _切换会话(session) {
        if (useGlobalState) {
            // 委托全局 切换会话：写入 状态.当前会话ID、广播 会话切换 事件、加载消息、同步插件文件夹
            try { 切换全局会话(session.id); } catch (_) {}
            try { onSwitch && onSwitch(session); } catch (_) {}
            return;
        }
        内部.当前会话ID = session.id;
        渲染();
        try { onSwitch && onSwitch(session); } catch (_) {}
    }

    function _显示删除确认(session) {
        // develop 类型保留双选项（含"删除会话和文件夹"），其余类型仅普通删除确认
        if (type === "develop") {
            // 复用现有设置面板中的删除确认对话框，保持开发插件流程一致
            // 动态导入避免循环依赖
            import("./设置面板.js").then(({ 显示删除确认 }) => {
                显示删除确认(container, session, async (id, deleteFolder) => {
                    const ok = await 删除会话(id, deleteFolder, "develop");
                    if (!ok) return;
                    if (!useGlobalState && 内部.当前会话ID === id) 内部.当前会话ID = null;
                    if (!useGlobalState) {
                        await refresh();
                        渲染();
                    }
                    // useGlobalState 下，删除会话内部已 emit 会话列表更新 事件，由订阅器触发渲染
                    try { onDelete && onDelete(id); } catch (_) {}
                });
            });
            return;
        }
        // optimize / visualize：单一普通删除确认
        const overlay = el("div", { class: "nca-overlay center-modal" });
        const modal = el("div", { class: "nca-modal" });
        const closeBtn = el("button", { class: "nca-icon-btn", text: "✕" });
        closeBtn.addEventListener("click", () => overlay.remove());
        modal.appendChild(el("div", { class: "nca-modal-header" }, [
            el("h3", { text: `⚠ ${t("common.confirm") || "确认"}` }),
            closeBtn,
        ]));
        const body = el("div", { class: "nca-modal-body" });
        body.appendChild(el("p", {
            text: t("session.confirm_delete", { title: session.title || (t("session.untitled") || "未命名") })
                || `确认删除会话 "${session.title || "未命名"}" 吗？`,
            style: { margin: "0", fontSize: "12px", color: "var(--nca-fg-dim)", lineHeight: "1.6" },
        }));
        const actions = el("div", { class: "nca-confirm-actions" });
        const cancelBtn = el("button", { class: "nca-btn nca-btn-sm", text: t("common.cancel") || "取消" });
        cancelBtn.addEventListener("click", () => overlay.remove());
        const okBtn = el("button", {
            class: "nca-btn nca-btn-sm nca-btn-danger",
            text: t("session.delete_only") || "删除",
        });
        okBtn.addEventListener("click", async () => {
            okBtn.disabled = true;
            okBtn.textContent = "...";
            const ok = await 删除会话(session.id, false, type);
            overlay.remove();
            if (!ok) return;
            if (内部.当前会话ID === session.id) 内部.当前会话ID = null;
            await refresh();
            渲染();
            try { onDelete && onDelete(session.id); } catch (_) {}
            try { Toast.success(t("session.deleted") || "已删除"); } catch (_) {}
        });
        actions.appendChild(cancelBtn);
        actions.appendChild(okBtn);
        body.appendChild(actions);
        modal.appendChild(body);
        overlay.appendChild(modal);
        overlay.addEventListener("click", (e) => { if (e.target === overlay) overlay.remove(); });
        container.appendChild(overlay);
    }

    function _进入编辑标题(item, session) {
        if (item.classList.contains("editing")) return;
        const titleEl = item.querySelector(".session-title");
        if (!titleEl) return;
        const 原标题 = session.title || "";
        const input = el("input", {
            class: "session-title-input",
            type: "text",
            value: 原标题,
            maxlength: 500,
        });
        item.classList.add("editing");
        titleEl.replaceWith(input);
        requestAnimationFrame(() => { input.focus(); input.select(); });

        let 已结束 = false;
        const 结束 = async (保存) => {
            if (已结束) return;
            已结束 = true;
            const 新值 = input.value.trim();
            if (保存 && 新值 && 新值 !== 原标题) {
                const ok = await 更新会话标题(session.id, 新值);
                if (!ok) {
                    _复原标题(input, 原标题, item);
                    return;
                }
                // develop 类型 更新会话标题 已经更新了全局列表并广播事件；
                // 其余类型直接刷新本组件即可
                if (!useGlobalState) await refresh();
                渲染();
            } else {
                _复原标题(input, 原标题, item);
            }
        };

        input.addEventListener("click", (e) => e.stopPropagation());
        input.addEventListener("mousedown", (e) => e.stopPropagation());
        input.addEventListener("keydown", (e) => {
            e.stopPropagation();
            if (e.key === "Enter") { e.preventDefault(); 结束(true); }
            else if (e.key === "Escape") { e.preventDefault(); 结束(false); }
        });
        input.addEventListener("blur", () => {
            const 新值 = input.value.trim();
            结束(新值 !== 原标题);
        });
    }

    function _复原标题(input, 原标题, item) {
        const titleSpan = el("span", {
            class: "session-title",
            text: 原标题 || (t("session.untitled") || "未命名"),
            title: 原标题,
        });
        if (input.parentNode) input.replaceWith(titleSpan);
        item.classList.remove("editing");
    }

    // ─── 公开控制对象 ────────────────────────────────────────
    async function refresh() {
        if (内部.已销毁) return;
        const list = await 获取会话列表(type);
        // useGlobalState 下，获取会话列表 已写入全局并 emit 事件，订阅器会触发渲染；
        // 仍写入内部缓存以兼容 getCurrentSession 在禁用全局态时的访问
        内部.会话列表 = list || [];
        // 当前会话不存在则清空指针（仅本地态）
        if (!useGlobalState && 内部.当前会话ID && !内部.会话列表.find(s => s.id === 内部.当前会话ID)) {
            内部.当前会话ID = null;
        }
        渲染();
    }

    function getCurrentSessionId() {
        return _获取当前ID();
    }

    function getCurrentSession() {
        const id = _获取当前ID();
        if (!id) return null;
        return (_获取列表() || []).find(s => s.id === id) || null;
    }

    // 外部设置当前会话 ID（不触发 onSessionSwitch，仅同步 UI 高亮状态）
    function setCurrentSessionId(sessionId) {
        if (useGlobalState) {
            const session = (_获取列表() || []).find(s => s.id === sessionId);
            if (session) {
                try { 切换全局会话(sessionId); } catch (_) {}
            }
        } else {
            内部.当前会话ID = sessionId || null;
        }
        渲染();
    }

    function clearSelection() {
        if (useGlobalState) return; // 全局状态由调用方通过 onCancelClick 自行处理
        内部.当前会话ID = null;
        渲染();
    }

    function isCollapsed() {
        return wrap.classList.contains("collapsed");
    }

    function destroy() {
        内部.已销毁 = true;
        // 取消全局事件订阅
        全局事件取消句柄.forEach(fn => { try { fn(); } catch (_) {} });
        全局事件取消句柄.length = 0;
        try { wrap.remove(); } catch (_) {}
    }

    // ─── 全局事件订阅（仅 useGlobalState） ────────────────
    if (useGlobalState) {
        const onListUpdate = () => 渲染();
        const onSessionSwitched = () => 渲染();
        事件总线.on(事件.会话列表更新, onListUpdate);
        事件总线.on(事件.会话切换, onSessionSwitched);
        全局事件取消句柄.push(() => 事件总线.off(事件.会话列表更新, onListUpdate));
        全局事件取消句柄.push(() => 事件总线.off(事件.会话切换, onSessionSwitched));
        // 首屏：渲染当前全局态（外部 初始化 流程会触发 获取会话列表 并经事件再次渲染）
        渲染();
    } else {
        // 首次拉取
        refresh();
    }

    return { refresh, getCurrentSessionId, getCurrentSession, setCurrentSessionId, clearSelection, isCollapsed, destroy };
}
